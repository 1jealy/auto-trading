"""관찰(실험 아님): 주식 차익 틈 — ① 같은 종목 한국거래소(KRX) vs 넥스트레이드(NXT) ② 같은 지수를 따라가는 ETF 두 개.

한국투자증권 실시간 호가(웹소켓)를 받아 '비용을 빼고도 남는 틈'이 얼마나 자주, 얼마나 크게 나오는지 센다. 주문은 내지 않는다.

- ① KRX vs NXT: 한 계좌라 주식을 옮길 필요가 없다. 한쪽에서 사고 다른 쪽에서 판다(들고 있는 주식을 판다고 본다).
  비용 = 수수료 × 2 + 증권거래세(config.stock_sell_tax, 2026년 0.20%).
- ② ETF 짝: 가격 단위가 달라 '두 ETF 가격 비율'이 평소(최근 30분 가운데 값)보다 벌어진 만큼을 틈으로 본다.
  싼 쪽 사고 비싼 쪽 팔고, 나중에 되돌린다고 보면 수수료 4번(국내 주식형 ETF는 거래세 없음). 되돌릴 때 비율이 평소로 돌아온다는 가정이라
  ①보다 확실하지 않다.
- 호가 메시지 형식은 공식 예제 기준(H0STASP0 KRX, H0NXASP0 NXT). 앞의 43칸(종목·시각·구분·매도 10·매수 10·잔량 20)만 쓴다.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from autotrade.config import stock_sell_tax
from autotrade.paper.book import Book, market_buy, market_sell
from autotrade.paper.store import DATA_DIR

TR_KRX, TR_NXT = "H0STASP0", "H0NXASP0"
STOCKS = {  # 넥스트레이드에서도 거래되는 큰 종목
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션", "207940": "삼성바이오로직스",
    "005380": "현대차", "000270": "기아", "068270": "셀트리온", "105560": "KB금융", "035420": "NAVER", "035720": "카카오",
}
ETF_PAIRS = [  # 같은 지수(국내 주식형 → 거래세 없음)
    ("069500", "102110", "코스피200 (KODEX 200 · TIGER 200)"),
    ("229200", "232080", "코스닥150 (KODEX · TIGER)"),
    ("122630", "123320", "코스피200 레버리지 (KODEX · TIGER)"),
    ("252670", "252710", "코스피200 곱버스 (KODEX · TIGER)"),
]
FEE = 0.00015          # 증권사 수수료 가정(한쪽, 온라인). 이벤트로 더 싼 곳도 있다.
NOTIONAL = 1_000_000   # 한 번에 100만 원어치로 틈을 잰다(호가 잔량 반영)
STALE = 3.0            # 3초 넘게 안 바뀐 호가는 비교에서 뺀다
RATIO_WINDOW = 1800    # ETF 비율의 '평소' = 최근 30분 가운데 값
GAP_MIN = 0.0          # 비용 뺀 뒤 0 넘는 틈만 기록
STOCK_DB = DATA_DIR / "stock_arb.db"


def parse_kis(raw: str) -> list[tuple[str, str, Book]]:
    """'0|H0STASP0|001|칸^칸^...' → [(tr_id, 종목, 호가)]. 암호화(1|…)·응답(JSON)은 빈 목록."""
    if not raw or raw[0] != "0":
        return []
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return []
    tr_id, n = parts[1], int(parts[2] or 1)
    f = parts[3].split("^")
    width = len(f) // n if n else 0
    out = []
    for i in range(n):
        x = f[i * width:(i + 1) * width]
        if len(x) < 43:
            continue
        try:
            asks = [(x[3 + j], x[23 + j]) for j in range(10)]
            bids = [(x[13 + j], x[33 + j]) for j in range(10)]
            out.append((tr_id, x[0], Book.make(asks, bids)))
        except ValueError:
            continue
    return out


def cross_net(buy: Book, sell: Book, notional: float = NOTIONAL, fee: float = FEE, tax: float | None = None) -> float | None:
    """buy 시장에서 사서 sell 시장에서 바로 팔 때 비용 뺀 순수익(산 돈 대비). 주식은 1주 단위로 자른다."""
    if not (buy.ok and sell.ok):
        return None
    shares = int(notional // buy.ask)
    if shares < 1:
        return None
    b = market_buy(buy, shares * buy.ask * 1.0000001)
    if not b.complete:
        return None
    s = market_sell(sell, b.qty)
    if not s.complete:
        return None
    tax = stock_sell_tax(time.strftime("%Y-%m-%d")) if tax is None else tax
    return (s.krw * (1 - fee - tax)) / (b.krw * (1 + fee)) - 1


@dataclass
class PairState:
    ratios: deque = field(default_factory=lambda: deque())  # (t, 가운데 값 비율)


def pair_dev(a: Book, b: Book, hist: deque, now: float, fee: float = FEE) -> tuple[float, str] | None:
    """ETF 짝: 지금 '싼 쪽 사고 비싼 쪽 팔기' 비율이 평소에서 벗어난 정도 − 수수료 4번. (순수익, '싼 쪽') 또는 None."""
    if not (a.ok and b.ok):
        return None
    mid = ((a.ask + a.bid) / 2) / ((b.ask + b.bid) / 2)
    hist.append((now, mid))
    while hist and now - hist[0][0] > RATIO_WINDOW:
        hist.popleft()
    if len(hist) < 30 or now - hist[0][0] < 300:  # 평소를 알려면 5분 이상
        return None
    normal = statistics.median(r for _, r in hist)
    cost = 4 * fee
    a_cheap = normal / (a.ask / b.bid) - 1 - cost   # a 사고 b 팔기
    b_cheap = (a.bid / b.ask) / normal - 1 - cost   # b 사고 a 팔기
    return (a_cheap, "a") if a_cheap >= b_cheap else (b_cheap, "b")


SCHEMA = """
CREATE TABLE IF NOT EXISTS gaps (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, dir TEXT, start REAL, dur REAL, max_net REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, start REAL, end REAL, msgs TEXT);
"""


class StockGapWatch:
    """호가를 받아 틈이 열리면 기록하고, 닫히면(비용 뺀 순수익 ≤ 0) 지속 시간과 최대값을 저장한다."""

    def __init__(self, db_path: Path = STOCK_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)
        self.books: dict[tuple[str, str], tuple[Book, float]] = {}  # (시장, 종목) → 호가, 받은 시각
        self.hist: dict[str, deque] = {p[2]: deque() for p in ETF_PAIRS}
        self.open: dict[tuple, dict] = {}
        self.msgs = {"KRX": 0, "NXT": 0}
        self.tax = stock_sell_tax(time.strftime("%Y-%m-%d"))

    def on(self, tr_id: str, code: str, book: Book, now: float) -> None:
        mkt = "NXT" if tr_id == TR_NXT else "KRX"
        self.msgs[mkt] += 1
        self.books[(mkt, code)] = (book, now)
        if code in STOCKS:
            k, n = self._fresh("KRX", code, now), self._fresh("NXT", code, now)
            if k and n:
                for buy, sell, d in ((k, n, "KRX→NXT"), (n, k, "NXT→KRX")):
                    self._mark("교차", STOCKS[code], d, cross_net(buy, sell, tax=self.tax), now)
        for a, b, name in ETF_PAIRS:
            if code in (a, b):
                ba, bb = self._fresh("KRX", a, now), self._fresh("KRX", b, now)
                if ba and bb:
                    r = pair_dev(ba, bb, self.hist[name], now)
                    if r:
                        self._mark("ETF짝", name, "앞 사고 뒤 팔기" if r[1] == "a" else "뒤 사고 앞 팔기", r[0], now)

    def _fresh(self, mkt: str, code: str, now: float) -> Book | None:
        x = self.books.get((mkt, code))
        return x[0] if x and now - x[1] <= STALE else None

    def _mark(self, kind: str, name: str, d: str, net: float | None, now: float) -> None:
        key = (kind, name, d)
        if net is not None and net > GAP_MIN:
            o = self.open.setdefault(key, {"start": now, "max": net, "n": 0})
            o["max"], o["n"], o["last"] = max(o["max"], net), o["n"] + 1, now
        elif key in self.open:
            o = self.open.pop(key)
            self.db.execute("INSERT INTO gaps (kind,name,dir,start,dur,max_net,n) VALUES (?,?,?,?,?,?,?)",
                            (kind, name, d, o["start"], now - o["start"], o["max"], o["n"]))
            self.db.commit()

    def close_all(self, now: float) -> None:
        for key in list(self.open):
            self._mark(*key, None, now)


def summary(db_path: Path = STOCK_DB, since: float = 0.0) -> list[dict]:
    if not db_path.exists():
        return []
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT kind, name, dir, COUNT(*), AVG(max_net), MAX(max_net), AVG(dur), SUM(dur>1) FROM gaps "
                      "WHERE start >= ? GROUP BY kind, name, dir ORDER BY COUNT(*) DESC", (since,)).fetchall()
    return [{"kind": r[0], "name": r[1], "dir": r[2], "n": r[3], "avg": r[4], "max": r[5], "dur": r[6], "over1s": r[7]} for r in rows]
