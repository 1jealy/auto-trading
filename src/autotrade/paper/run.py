"""모의 투자 엔진 실행 루프. 공개 시세만 읽고, 주문은 내지 않는다(가짜 체결을 기록할 뿐).

    python scripts/paper_engine.py            # 엔진만
    python crypto_live/server.py --engine     # 화면 서버와 함께 (코인시세.bat 이 이렇게 실행)

- 단타(#17·#18)와 지연 관측: 약 0.5초마다 해외 가격과 업비트 호가창을 받아 scalp.Scalper 에 넘긴다.
- 호가 비용 기록: 1분마다 업비트·코인원 호가창에서 100만·500만·1,000만 원을 사자마자 팔 때 잃는 비율.
- 느린 전략(#12·#13·#14)과 대조군: 10분마다 시작일 이후 시세로 다시 계산해 slow.json 에 쓴다.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd

from autotrade.data import Prices, load_prices
from autotrade.paper import slow
from autotrade.paper.book import round_trip_cost
from autotrade.paper.feeds import COINS, SOURCE_NAMES, Feeds
from autotrade.paper.scalp import ALWAYS, FORMAL, Scalper
from autotrade.paper.store import LIVE_PATH, OFFICIAL_SOURCE, SLOW_PATH, Store, write_json
from autotrade.upbit import days_between

KST = timezone(timedelta(hours=9))
UPBIT_GAP = 0.11  # 업비트 요청 사이 최소 간격(초당 10회 한도)


def log(msg: str) -> None:
    line = f"[모의 엔진 {datetime.now(KST):%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except UnicodeEncodeError:  # 윈도우 창이 못 찍는 글자가 있어도 엔진은 멈추지 않게
        print(line.encode("ascii", "replace").decode(), file=sys.stderr, flush=True)


# ── 느린 전략 자료 ──
def crypto_prices(markets=slow.COINS4, days: int = 200, session=None) -> Prices:
    """업비트 일봉(UTC). 마지막 줄은 아직 진행 중인 오늘 캔들."""
    end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    start = end - timedelta(days=days)
    frames = {m: days_between(m, start, end, session) for m in markets}
    open_ = pd.DataFrame({m: f["Open"] for m, f in frames.items()}).sort_index()
    close = pd.DataFrame({m: f["Close"] for m, f in frames.items()}).sort_index()
    return Prices(open_, close)


def etf_prices(days: int = 420) -> Prices:
    start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    return load_prices(slow.ETF5 + [slow.ETF_SAFE], start, None, workers=3)


def completed(prices: Prices, market: str, now: datetime | None = None) -> Prices:
    """끝난 봉까지만 남긴다 (판단 근거 표시용)."""
    now = now or datetime.now(timezone.utc)
    last = prices.dates[-1]
    if market == "coin":
        open_bar = last.date() >= now.date()  # UTC 오늘 캔들은 진행 중
    else:
        k = now.astimezone(KST)
        open_bar = last.date() == k.date() and (k.hour, k.minute) < (15, 40)
    return prices.until(prices.dates[-2]) if open_bar and len(prices.dates) > 1 else prices


def next_times(now: datetime | None = None) -> dict[str, float]:
    now = now or datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    monday = day + timedelta(days=(7 - day.weekday()) % 7)
    k = now.astimezone(KST)
    friday = (k + timedelta(days=(4 - k.weekday()) % 7)).replace(hour=15, minute=30, second=0, microsecond=0)
    if friday <= k:
        friday += timedelta(days=7)
    return {"s13": day.timestamp(), "s14": monday.timestamp(), "s12": friday.timestamp()}


def compute_slow(feeds: Feeds) -> dict:
    """느린 전략·대조군을 시작일 이후 시세로 다시 계산한다. 자료를 못 받은 것은 error 로 표시."""
    out: dict = {"updated": time.time(), "items": {}, "next": next_times()}

    def guard(key: str, fn) -> None:
        try:
            out["items"][key] = fn()
        except Exception as e:  # noqa: BLE001
            out["items"][key] = {"key": key, "name": slow.SPECS[key].name, "kind": slow.SPECS[key].kind,
                                 "control": slow.SPECS[key].control, "error": f"{type(e).__name__}: {str(e)[:140]}"}

    coins: Prices | None = None
    try:
        coins = crypto_prices(session=feeds.s)
    except Exception as e:  # noqa: BLE001
        coin_err = e

    def need_coins() -> Prices:
        if coins is None:
            raise RuntimeError(f"업비트 일봉을 받지 못함: {coin_err}")
        return coins

    def s14() -> dict:
        p = need_coins()
        d = slow.summarize("s14", slow.replay("s14", p), p)
        d["explain"] = {"as_of": completed(p, "coin").dates[-1].strftime("%Y-%m-%d"),
                        "rows": slow.WeeklySharpeRotation().table(completed(p, "coin"))}
        return d

    def c_btc() -> dict:
        p = need_coins().select([slow.BTC])
        return slow.summarize("c_btc", slow.replay("c_btc", p), p)

    def s13() -> dict:
        p = slow.with_funding_kimchi(need_coins().select([slow.BTC]), feeds.funding(), feeds.usdt_daily(),
                                     feeds.usdkrw_daily())
        d = slow.summarize("s13", slow.replay("s13", p), p)
        _, last = slow.FundingKimchi(slow.START_COIN).walk(completed(p, "coin"))
        if last:
            last["date"] = last["date"].strftime("%Y-%m-%d")
        d["explain"] = last
        return d

    etfs: Prices | None = None
    try:
        etfs = etf_prices()
    except Exception as e:  # noqa: BLE001
        etf_err = e

    def need_etfs() -> Prices:
        if etfs is None:
            raise RuntimeError(f"ETF 시세를 받지 못함: {etf_err}")
        return etfs

    def s12() -> dict:
        p = need_etfs()
        d = slow.summarize("s12", slow.replay("s12", p), p, slow.ETF_NAMES)
        done = completed(p, "etf")
        rows = slow.InverseVolTrend().table(done)
        for r in rows or []:
            r["name"] = slow.ETF_NAMES[r["ticker"]]
        d["explain"] = {"as_of": done.dates[-1].strftime("%Y-%m-%d"), "rows": rows}
        return d

    def c_etf4() -> dict:
        p = need_etfs().select(slow.ETF4)
        return slow.summarize("c_etf4", slow.replay("c_etf4", p), p, slow.ETF_NAMES)

    def c_kodex() -> dict:
        p = need_etfs().select(["069500"])
        return slow.summarize("c_kodex", slow.replay("c_kodex", p), p, slow.ETF_NAMES)

    for key, fn in (("s14", s14), ("c_btc", c_btc), ("s13", s13), ("s12", s12), ("c_etf4", c_etf4), ("c_kodex", c_kodex)):
        guard(key, fn)
    return out


# ── 엔진 ──
class Engine:
    def __init__(self, store: Store | None = None, feeds: Feeds | None = None, poll: float = 0.5,
                 slow_every: float = 600.0, cost_every: float = 60.0, force: bool = False) -> None:
        self.store = store or Store()
        self.feeds = feeds or Feeds()
        self.poll, self.slow_every, self.cost_every = poll, slow_every, cost_every
        self.stop = threading.Event()
        self.scalper: Scalper | None = None
        self.started = time.time()
        self.errors: dict[str, str] = {}
        self.loop_ms: list[float] = []
        up = self.store.uptime()
        if not force and up["last"] and time.time() - up["last"] < 20:
            raise RuntimeError("다른 엔진이 이미 돌고 있는 것 같습니다(20초 안에 기록이 있음). 하나만 실행하세요.")

    # 느린 전략: 별도 스레드
    def slow_loop(self) -> None:
        while not self.stop.is_set():
            try:
                write_json(SLOW_PATH, compute_slow(Feeds(self.feeds.coins)))
            except Exception:  # noqa: BLE001
                log("느린 전략 계산 실패:\n" + traceback.format_exc(limit=3))
            self.stop.wait(self.slow_every)

    def run(self) -> None:
        source = self.feeds.pick_source()
        log(f"해외 가격 출처: {SOURCE_NAMES[source]}" + ("" if source == OFFICIAL_SOURCE else
                                                    " — 바이낸스 선물에 연결되지 않아 대신 씀(정식 판정에서 제외되는 기록)"))
        cash = {p.key: p.capital + self.store.realized(p.key) for p in (FORMAL, ALWAYS)}
        self.scalper = Scalper(params=(FORMAL, ALWAYS), source=source, cash=cash)
        threading.Thread(target=self.slow_loop, daemon=True, name="paper-slow").start()

        next_poll = time.time()
        last_upbit = last_beat = last_live = last_cost = 0.0
        fails = 0
        while not self.stop.is_set():
            now = time.time()
            due = self.scalper.next_due()
            wake = max(min(next_poll, due) if due is not None else next_poll, last_upbit + UPBIT_GAP)
            if wake > now:
                self.stop.wait(min(wake - now, 0.25))
                continue
            is_poll = now >= next_poll
            t_req = last_upbit = time.time()
            want_alt = self.scalper.alt_wanted(t_req)
            try:
                f_books = self.feeds.pool.submit(self.feeds.upbit_books)
                f_g = self.feeds.pool.submit(self.feeds.global_mids) if is_poll else None
                f_alt = self.feeds.pool.submit(self.feeds.coinone_books, want_alt) if want_alt else None
                books = f_books.result()
                g = f_g.result() if f_g else None
                alt = f_alt.result() if f_alt else {}
                fails = 0
                self.errors.pop("feed", None)
            except Exception as e:  # noqa: BLE001
                fails += 1
                self.errors["feed"] = f"{type(e).__name__}: {str(e)[:120]}"
                if fails in (1, 20) or fails % 200 == 0:
                    log(f"시세 받기 실패({fails}번째): {self.errors['feed']}")
                if is_poll:
                    next_poll = time.time() + min(5.0, 0.5 * fails)
                else:
                    self.stop.wait(0.3)
                self._write_live(time.time())
                continue
            self.loop_ms = (self.loop_ms + [(time.time() - t_req) * 1000])[-40:]
            for coin in self.feeds.coins:
                if coin not in books:
                    continue
                if g is not None and coin in g:
                    self.scalper.on_tick(t_req, coin, g[coin], books[coin], alt.get(coin))
                else:
                    self.scalper.on_book(t_req, coin, books[coin], alt.get(coin))
            if is_poll:
                next_poll = max(next_poll + self.poll, time.time())
            for ev in self.scalper.drain():
                self.store.add_event(ev)

            now = time.time()
            if now - last_beat >= 10:
                last_beat = now
                self.store.heartbeat(now, source)
            if now - last_cost >= self.cost_every:
                last_cost = now
                self._log_costs(now, books)
            if now - last_live >= 1.0:
                last_live = now
                self._write_live(now)
        self.store.heartbeat(time.time(), source)  # 멈춘 시각까지 켜져 있던 것으로 기록
        self._write_live(time.time(), stopped=True)

    def _log_costs(self, t: float, books: dict) -> None:
        def record(exchange: str, bs: dict) -> None:
            for coin, b in bs.items():
                if b is not None and b.ok:
                    self.store.add_cost(t, coin, exchange, b.spread, round_trip_cost(b, 1_000_000),
                                        round_trip_cost(b, 5_000_000), round_trip_cost(b, 10_000_000))
        record("upbit", books)
        try:
            record("coinone", self.feeds.coinone_books(self.feeds.coins))
        except Exception as e:  # noqa: BLE001
            self.errors["coinone"] = str(e)[:100]

    def _write_live(self, t: float, stopped: bool = False) -> None:
        snap = self.scalper.snapshot() if self.scalper else {}
        source = snap.get("source")
        write_json(LIVE_PATH, {
            "t": t, "started": self.started, "stopped": stopped, "source": source,
            "source_name": SOURCE_NAMES.get(source, source), "official": source == OFFICIAL_SOURCE,
            "loop_ms": sum(self.loop_ms) / len(self.loop_ms) if self.loop_ms else None,
            "errors": self.errors, "scalp": snap,
        })


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    engine = Engine(force="--force" in argv)
    log("시작 — 실제 주문은 내지 않습니다. 끄려면 Ctrl+C")
    try:
        engine.run()
    except KeyboardInterrupt:
        engine.stop.set()
        log("멈춤")


def start_in_thread() -> Engine:
    """화면 서버 안에서 함께 돌릴 때. 엔진이 죽으면 10초 뒤 다시 시작한다."""
    engine = Engine()

    def forever() -> None:
        while not engine.stop.is_set():
            try:
                engine.run()
            except Exception:  # noqa: BLE001
                log("엔진 오류 — 10초 뒤 다시 시작:\n" + traceback.format_exc(limit=4))
                engine.stop.wait(10)

    threading.Thread(target=forever, daemon=True, name="paper-engine").start()
    return engine


if __name__ == "__main__":
    main()
