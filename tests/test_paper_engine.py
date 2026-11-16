"""모의 투자 엔진 통합 테스트 — 가짜 시세로 실행 루프·저장·요약까지 한 번에 확인한다 (네트워크 없음)."""

from __future__ import annotations

import threading
import time

import pytest

from autotrade.paper import run as run_mod
from autotrade.paper import store as store_mod
from autotrade.paper import view as view_mod
from autotrade.paper.book import Book
from autotrade.paper.run import Engine
from autotrade.paper.store import Store


def book(bid, ask, tick):
    return Book.make([(ask + i * tick, 1e9 / ask) for i in range(5)], [(bid - i * tick, 1e9 / bid) for i in range(5)])


class FakeFeeds:
    """해외 가격이 주기적으로 +0.5% 뛰고, 업비트는 1초 늦게 따라오는 시장."""

    coins = ("BTC", "XRP")
    source = None

    def __init__(self):
        from concurrent.futures import ThreadPoolExecutor

        self.pool = ThreadPoolExecutor(4)
        self.t0 = time.time()
        self.s = None

    def level(self, lag=0.0):
        steps = int((time.time() - self.t0 - lag) / 1.5)  # 1.5초마다 한 계단
        return 1.005 ** max(steps, 0)

    def pick_source(self):
        self.source = "okx_swap"
        return self.source

    def global_mids(self):
        return {"BTC": 60_000 * self.level(), "XRP": 1.0 * self.level()}

    def upbit_books(self):
        m = self.level(lag=0.6)
        btc = round(90_000_000 * m, -3)
        xrp = round(1340 * m)
        return {"BTC": book(btc - 1000, btc, 1000), "XRP": book(xrp - 1, xrp, 1)}

    def coinone_books(self, coins):
        return {c: b for c, b in self.upbit_books().items() if c in coins}


@pytest.fixture()
def paper_dir(tmp_path, monkeypatch):
    for mod in (store_mod, run_mod, view_mod):
        for name in ("LIVE_PATH", "SLOW_PATH"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, tmp_path / getattr(store_mod, name).name)
    monkeypatch.setattr(run_mod, "compute_slow", lambda feeds, store=None: {"updated": time.time(), "items": {}, "next": {}})
    monkeypatch.setattr(run_mod, "Feeds", lambda coins=None: None)
    monkeypatch.setattr(run_mod, "UPBIT_GAP", 0.0)
    return tmp_path


def test_engine_records_trades_radar_costs_and_summary(paper_dir, monkeypatch):
    from autotrade.paper import scalp

    fast = scalp.ScalpParams("s18", move=0.0010, lag=0.0005, amount=1_000_000, max_hold=0.3, latency=0.05)
    formal = scalp.ScalpParams("s17", move=0.0035, lag=0.0020, amount=5_000_000, max_hold=0.5, need_tight_spread=True,
                               min_depth3=5_000_000, take=0.0025, stop=0.0030, origin_stop=True, catchup=True,
                               latency=0.05)
    monkeypatch.setattr(run_mod, "FORMAL", formal)
    monkeypatch.setattr(run_mod, "ALWAYS", fast)
    real = scalp.Scalper
    monkeypatch.setattr(run_mod, "Scalper", lambda **kw: real(lookback=0.3, max_age=1.0, radar_cooldown=0.5, **kw))
    monkeypatch.setattr(scalp, "RADAR_OFFSETS", (0.05, 0.1, 0.2))

    store = Store(paper_dir / "paper.db")
    engine = Engine(store=store, feeds=FakeFeeds(), poll=0.05, cost_every=0.5)
    th = threading.Thread(target=engine.run, daemon=True)
    th.start()
    time.sleep(6)
    engine.stop.set()
    th.join(5)

    s18 = store.scalp_summary("s18", "okx_swap")
    s17 = store.scalp_summary("s17", "okx_swap")
    assert s18["n"] >= 2 and s17["n"] >= 1
    assert s18["value"] == pytest.approx(10_000_000 + s18["net_krw"])
    one = store.trade(store.trades("s18", limit=1)[0]["id"])
    assert one["entry_t"] >= one["signal_t"] + 0.05  # 신호 호가가 아니라 지연 뒤 호가로 체결
    assert one["exit_t"] >= one["entry_t"] + 0.3
    assert one["detail"]["entry_book"]["asks"] and one["alt_net_pct"] is not None
    # 수수료만큼은 반드시 잃는다: 순수익 = 총수익 − 약 0.1%
    assert one["net_pct"] == pytest.approx(one["gross_pct"] - 0.0005 * (2 + one["gross_pct"]), abs=1e-9)

    radar = store.radar_summary("okx_swap")
    assert radar["n"] >= 1 and radar["curve"][0]["offset"] == 0.0
    assert store.cost_summary() and store.uptime()["seconds"] > 3
    summary = view_mod.build_summary(store)
    assert summary["scalp"]["s18"]["n"] == s18["n"] and summary["engine"]["official"]
    assert summary["engine"]["source_name"] == "OKX 선물"

    # 다시 켜면 남은 돈을 기록에서 되살린다
    assert store.realized("s18") == pytest.approx(s18["net_krw"])


def test_second_engine_is_refused_while_one_is_running(paper_dir):
    store = Store(paper_dir / "paper.db")
    store.heartbeat(time.time(), "okx_swap")
    with pytest.raises(RuntimeError):
        Engine(store=store, feeds=FakeFeeds())
    Engine(store=store, feeds=FakeFeeds(), force=True)


def test_uptime_segments_join_only_when_close(paper_dir):
    store = Store(paper_dir / "paper.db")
    store.heartbeat(1000, "a"); store.heartbeat(1010, "a"); store.heartbeat(2000, "a"); store.heartbeat(2030, "a")
    up = store.uptime("a")
    assert up["segments"] == 2 and up["seconds"] == pytest.approx(40)


# ── 코인이 많을 때의 시세 받기 ──
class _Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        for key, fn in self.routes.items():
            if key in url:
                return fn(params)
        return _Resp({}, 404)


def test_feeds_bulk_okx_and_universe_filtering():
    from autotrade.paper.feeds import Feeds, load_universe

    uni = load_universe()
    assert len(uni) > 100 and uni[:4] == ("BTC", "ETH", "XRP", "SOL") and len(set(uni)) == len(uni)
    assert "USDC" not in uni
    coins = ("BTC", "ETH", "XRP", "SOL") + tuple(f"C{i}" for i in range(10))
    tickers = [{"instId": f"{c}-USDT-SWAP", "bidPx": "1", "askPx": "3"} for c in coins[:-1]]  # 마지막 코인은 선물 없음
    tickers.append({"instId": "C5-USDT-SWAP", "bidPx": "", "askPx": ""})
    tickers.append({"instId": "BTC-USD-SWAP", "bidPx": "9", "askPx": "9"})
    sess = _Session({"okx.com/api/v5/market/tickers": lambda p: _Resp({"data": tickers})})
    f = Feeds(coins=coins, session=sess)
    mids = f.global_mids_for("okx_swap") if hasattr(f, "global_mids_for") else f._global("okx_swap")
    assert len([c for c in sess.calls if "tickers" in c[0]]) == 1  # 코인이 많아도 한 번에
    assert mids["BTC"] == 2.0 and "C9" not in mids and len(mids) == len(coins) - 1


def test_feeds_upbit_falls_back_to_base_coins_when_a_market_disappears():
    from autotrade.paper.feeds import Feeds

    coins = ("BTC", "ETH", "XRP", "SOL", "GONE")

    def ob(p):
        if "KRW-GONE" in p["markets"]:
            return _Resp({}, 404)
        return _Resp([{"market": m, "timestamp": 0,
                       "orderbook_units": [{"ask_price": 101, "ask_size": 1, "bid_price": 100, "bid_size": 1}]}
                      for m in p["markets"].split(",")])

    sess = _Session({"orderbook": ob, "market/all": lambda p: _Resp([{"market": f"KRW-{c}", "market_event": {"warning": False}}
                                                                  for c in coins[:4]]),
                     "okx.com": lambda p: _Resp({"data": [{"instId": f"{c}-USDT-SWAP", "bidPx": "1", "askPx": "2"}
                                                         for c in coins]})})
    f = Feeds(coins=coins, session=sess)
    f.UPBIT_SPACING = 0.0
    books = f.upbit_books()
    assert set(books) == {"BTC", "ETH", "XRP", "SOL"} and "GONE" not in f.active
