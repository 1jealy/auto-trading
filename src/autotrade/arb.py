"""국내 거래소 사이 차익거래 관찰 (업비트·빗썸·코인원·코빗). 실험이 아니라 관찰 — 주문은 내지 않는다.

같은 코인이 한 거래소에서 더 싸고 다른 거래소에서 더 비쌀 때, **실제 호가 잔량**을 걸어 내려가며(시장가 체결 계산)
사자마자 팔면 수수료 전에 몇 %가 남는지(gross)와, 그 틈이 **몇 ms 동안 열려 있었는지**를 기록한다.

- 호가는 거래소 웹소켓으로 받는다(받는 쪽은 scripts/obs_arbitrage.py). 이 파일은 네트워크를 쓰지 않는다.
- 호가가 낡았으면(마지막 수신이 STALE 초 전) 그 거래소는 비교에서 뺀다 — 낡은 값끼리 비교하면 가짜 틈이 생긴다.
- 수수료는 틈을 찾는 기준이 아니라 **나중에 판단하는 기준**이다: 기록은 gross ≥ GROSS_MIN 인 틈 전부, 수수료 가정은 보고서에서 적용한다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from autotrade.paper.book import Book, market_buy, market_sell
from autotrade.paper.store import DATA_DIR

EXCHANGES = ("upbit", "bithumb", "coinone", "korbit")
NOTIONALS = (500_000, 2_000_000)  # 한 번에 사고파는 금액(원)
GROSS_MIN = 0.0010  # 수수료 전 0.10% 이상 남는 틈만 기록(어떤 수수료 가정으로도 이보다 작으면 의미 없음)
STALE = 3.0  # 호가를 이 초 넘게 못 받았으면 낡은 것으로 보고 비교에서 뺀다
# 수수료 가정(한쪽, 모두 가정치 — 거래소·등급·이벤트마다 다르니 실제 값을 확인해 고칠 것)
FEES_STANDARD = {"upbit": 0.0005, "bithumb": 0.0025, "coinone": 0.0020, "korbit": 0.0020}
FEE_DISCOUNT = 0.0005
ARB_DB = DATA_DIR / "arb.db"


# ---------------------------------------------------------------- 메시지 읽기


def _ts(t: float) -> float:
    """거래소 시각 → 초 (밀리초·마이크로초 모두 받음)."""
    return t / 1e6 if t > 1e14 else t / 1e3


def parse_upbit_like(msg: dict) -> tuple[str, Book, float] | None:
    """업비트·빗썸 (같은 형식): code='KRW-BTC', orderbook_units."""
    if msg.get("type") != "orderbook" or "orderbook_units" not in msg:
        return None
    code = str(msg.get("code", ""))
    if not code.startswith("KRW-"):
        return None
    u = msg["orderbook_units"]
    book = Book.make([(x["ask_price"], x["ask_size"]) for x in u], [(x["bid_price"], x["bid_size"]) for x in u],
                     _ts(float(msg.get("timestamp", 0))))
    return code[4:], book, book.ts


def parse_coinone(msg: dict) -> tuple[str, Book, float] | None:
    if msg.get("response_type") != "DATA" or msg.get("channel") != "ORDERBOOK":
        return None
    d = msg["data"]
    if d.get("quote_currency") != "KRW":
        return None
    book = Book.make([(x["price"], x["qty"]) for x in d["asks"]], [(x["price"], x["qty"]) for x in d["bids"]],
                     _ts(float(d.get("timestamp", 0))))
    return str(d["target_currency"]).upper(), book, book.ts


def parse_korbit(msg: dict) -> tuple[str, Book, float] | None:
    if msg.get("type") != "orderbook" or "data" not in msg:
        return None
    sym = str(msg.get("symbol", ""))
    if not sym.endswith("_krw"):
        return None
    d = msg["data"]
    book = Book.make([(x["price"], x["qty"]) for x in d["asks"]], [(x["price"], x["qty"]) for x in d["bids"]],
                     _ts(float(d.get("timestamp", msg.get("timestamp", 0)))))
    return sym[:-4].upper(), book, book.ts


PARSERS = {"upbit": parse_upbit_like, "bithumb": parse_upbit_like, "coinone": parse_coinone, "korbit": parse_korbit}


# ---------------------------------------------------------------- 틈 계산


@dataclass(frozen=True)
class Gap:
    buy_ex: str
    sell_ex: str
    notional: float
    gross: float  # 수수료 전, 산 돈 대비
    buy_krw: float
    qty: float


def gap(buy_book: Book, sell_book: Book, notional: float, buy_ex: str = "", sell_ex: str = "") -> Gap | None:
    """buy_book 에서 notional 원어치를 사서 sell_book 에 바로 팔면 수수료 전에 얼마가 남나. 잔량이 모자라면 None."""
    if not (buy_book.ok and sell_book.ok):
        return None
    b = market_buy(buy_book, notional)
    if not b.complete or b.qty <= 0:
        return None
    s = market_sell(sell_book, b.qty)
    if not s.complete:
        return None
    return Gap(buy_ex, sell_ex, notional, s.krw / b.krw - 1, b.krw, b.qty)


def net_after_fees(g: float, fee_buy: float, fee_sell: float) -> float:
    """gross(g) 에서 양쪽 수수료를 뺀 순수익(산 돈 대비)."""
    return (1 + g) * (1 - fee_sell) / (1 + fee_buy) - 1


@dataclass
class Episode:
    coin: str
    buy_ex: str
    sell_ex: str
    notional: float
    start: float
    end: float = 0.0
    max_gross: float = 0.0
    updates: int = 0
    sum_gross: float = 0.0
    closed_by: str = ""  # edge: 호가가 바뀌어 틈이 닫힘(지속 시간을 믿을 수 있음) · stale: 한쪽 호가가 조용해져 닫음(지속 시간은 '최소') · end: 관찰 끝

    @property
    def duration_ms(self) -> float:
        return (self.end - self.start) * 1000


class Tracker:
    """코인 하나의 모든 거래소 호가를 받아 틈이 열리고 닫히는 것을 에피소드로 묶는다."""

    def __init__(self, notionals=NOTIONALS, gross_min: float = GROSS_MIN, stale: float = STALE) -> None:
        self.notionals, self.gross_min, self.stale = notionals, gross_min, stale
        self.books: dict[str, dict[str, tuple[Book, float]]] = {}  # coin → exchange → (book, 받은 시각)
        self.open: dict[tuple, Episode] = {}
        self.done: list[Episode] = []

    def update(self, coin: str, ex: str, book: Book, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.books.setdefault(coin, {})[ex] = (book, now)
        fresh = {e: b for e, (b, t) in self.books[coin].items() if now - t <= self.stale}
        for buy_ex, bb in fresh.items():
            for sell_ex, sb in fresh.items():
                if buy_ex == sell_ex or ex not in (buy_ex, sell_ex):
                    continue
                for n in self.notionals:
                    g = gap(bb, sb, n, buy_ex, sell_ex)
                    self._track((coin, buy_ex, sell_ex, n), g, now)
        # 낡아진 거래소가 낀 열린 에피소드는 닫는다(그 거래소 호가를 더는 믿을 수 없음)
        for key in [k for k in self.open if k[0] == coin and (k[1] not in fresh or k[2] not in fresh)]:
            self._close(key, now, "stale")

    def _track(self, key: tuple, g: Gap | None, now: float) -> None:
        ep = self.open.get(key)
        if g is not None and g.gross >= self.gross_min:
            if ep is None:
                ep = self.open[key] = Episode(key[0], key[1], key[2], key[3], start=now)
            ep.end = now
            ep.updates += 1
            ep.sum_gross += g.gross
            ep.max_gross = max(ep.max_gross, g.gross)
        elif ep is not None:
            self._close(key, now, "edge")

    def _close(self, key: tuple, now: float, why: str = "end") -> None:
        ep = self.open.pop(key, None)
        if ep is None:
            return
        ep.closed_by = why
        # 틈이 닫힌 것을 본 시각(edge)이 끝. stale/end 는 마지막으로 틈을 본 시각(= 최소 지속 시간)
        ep.end = now if why == "edge" else ep.end
        self.done.append(ep)

    def drain(self) -> list[Episode]:
        out, self.done = self.done, []
        return out


# ---------------------------------------------------------------- 저장·보고서

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, coin TEXT, buy_ex TEXT, sell_ex TEXT, notional REAL,
  start REAL, dur_ms REAL, max_gross REAL, mean_gross REAL, updates INTEGER, closed_by TEXT);
CREATE INDEX IF NOT EXISTS ep_start ON episodes(start);
CREATE TABLE IF NOT EXISTS arb_runs (id INTEGER PRIMARY KEY, start REAL, end REAL, host TEXT, note TEXT, stats TEXT);
"""


class ArbStore:
    def __init__(self, path: Path | str = ARB_DB) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def add_episodes(self, eps: list[Episode]) -> None:
        if not eps:
            return
        self.db.executemany(
            "INSERT INTO episodes (coin, buy_ex, sell_ex, notional, start, dur_ms, max_gross, mean_gross, updates, closed_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(e.coin, e.buy_ex, e.sell_ex, e.notional, e.start, e.duration_ms, e.max_gross,
              e.sum_gross / max(e.updates, 1), e.updates, e.closed_by) for e in eps])
        self.db.commit()

    def add_run(self, start: float, end: float, host: str, note: str, stats: dict) -> None:
        self.db.execute("INSERT INTO arb_runs (start, end, host, note, stats) VALUES (?,?,?,?,?)",
                        (start, end, host, note, json.dumps(stats, ensure_ascii=False)))
        self.db.commit()

    def episodes(self, since: float = 0.0) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM episodes WHERE start >= ? ORDER BY start", (since,)).fetchall()

    def runs(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM arb_runs ORDER BY id").fetchall()


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


FAST_MS = 2000  # 이보다 짧게 열렸다 호가 변화로 닫힌 틈만 '빠른 틈'(속도 싸움). 나머지는 '오래 가는 차이'


def _kind(r) -> str:
    return "fast" if r["closed_by"] == "edge" and r["dur_ms"] < FAST_MS else "persistent"


def report(rows, seconds: float, fees: dict[str, float] | None = None) -> dict:
    """에피소드 → 수수료 가정별 '실제로 남는 틈' 요약. seconds = 관찰한 시간(초). rows 는 dict 비슷한 것.

    빠른 틈(fast): 호가가 바뀌며 2초 안에 닫힘 → 먼저 도착한 쪽이 가져가는 속도 싸움.
    오래 가는 차이(persistent): 2초 넘게 이어지거나 한쪽 호가가 조용해서 안 닫힌 것 → 대개 입출금 중단·시장 상황이 달라서 생기고,
      한 번 거래하면 코인이 한쪽에 쏠려 송금이 필요하다(양쪽 동시 거래를 계속 반복할 수 없음).
    """
    fees = fees or FEES_STANDARD
    days = max(seconds / 86400, 1e-9)
    scenarios = {
        "수수료 할인(양쪽 0.05%)": lambda r: net_after_fees(r["max_gross"], FEE_DISCOUNT, FEE_DISCOUNT),
        "일반 수수료(가정)": lambda r: net_after_fees(r["max_gross"], fees[r["buy_ex"]], fees[r["sell_ex"]]),
    }
    out: dict = {"episodes": len(rows), "days": days, "scenarios": {}}
    for name, f in scenarios.items():
        for kind in ("fast", "persistent"):
            hit = [r for r in rows if _kind(r) == kind and f(r) > 0]
            by_pair: dict[str, int] = {}
            by_coin: dict[str, int] = {}
            for r in hit:
                by_pair[f"{r['buy_ex']}→{r['sell_ex']}"] = by_pair.get(f"{r['buy_ex']}→{r['sell_ex']}", 0) + 1
                by_coin[r["coin"]] = by_coin.get(r["coin"], 0) + 1
            durs = [r["dur_ms"] for r in hit]
            out["scenarios"][f"{name} · {'빠른 틈' if kind == 'fast' else '오래 가는 차이'}"] = {
                "per_day": len(hit) / days, "n": len(hit),
                "dur_ms_median": _pct(durs, 0.5), "dur_ms_p90": _pct(durs, 0.9),
                "net_median": _pct([f(r) for r in hit], 0.5), "net_max": max((f(r) for r in hit), default=None),
                "coins": len(by_coin),
                "top_pairs": sorted(by_pair.items(), key=lambda x: -x[1])[:5],
                "top_coins": sorted(by_coin.items(), key=lambda x: -x[1])[:8],
            }
    return out
