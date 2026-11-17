"""국내 거래소 차익 관찰 테스트: 메시지 읽기, 틈 계산(잔량 반영), 에피소드 열고 닫기, 낡은 호가, 수수료 가정."""

import pytest

from autotrade.arb import (ArbStore, Episode, Tracker, gap, net_after_fees, parse_coinone, parse_korbit,
                           parse_upbit_like, report)
from autotrade.paper.book import Book


def book(ask, bid, ask_krw=10_000_000, bid_krw=10_000_000, levels=3, tick=1.0):
    return Book.make([(ask + i * tick, ask_krw / ask) for i in range(levels)],
                     [(bid - i * tick, bid_krw / bid) for i in range(levels)])


def test_parse_upbit_bithumb_coinone_korbit():
    up = {"type": "orderbook", "code": "KRW-BTC", "timestamp": 1_700_000_000_123,
          "orderbook_units": [{"ask_price": 101, "bid_price": 100, "ask_size": 1, "bid_size": 2}]}
    coin, b, ts = parse_upbit_like(up)
    assert coin == "BTC" and b.ask == 101 and b.bid == 100 and ts == pytest.approx(1_700_000_000.123)
    bt = dict(up, timestamp=1_700_000_000_123_456)  # 빗썸은 마이크로초
    assert parse_upbit_like(bt)[2] == pytest.approx(1_700_000_000.123456)
    assert parse_upbit_like({"type": "orderbook", "code": "BTC-ETH", "orderbook_units": []}) is None
    co = {"response_type": "DATA", "channel": "ORDERBOOK", "data": {
        "quote_currency": "KRW", "target_currency": "eth", "timestamp": 1_700_000_000_000,
        "asks": [{"price": "105", "qty": "1"}, {"price": "104", "qty": "2"}], "bids": [{"price": "100", "qty": "3"}]}}
    coin, b, _ = parse_coinone(co)
    assert coin == "ETH" and b.ask == 104  # 순서가 뒤섞여 와도 싼 매도부터
    assert parse_coinone({"response_type": "SUBSCRIBED"}) is None
    ko = {"symbol": "btc_krw", "type": "orderbook", "timestamp": 1_700_000_000_000,
          "data": {"timestamp": 1_700_000_000_000, "asks": [{"price": "102", "qty": "1"}], "bids": [{"price": "99", "qty": "1"}]}}
    assert parse_korbit(ko)[0] == "BTC"


def test_gap_uses_real_depth_and_returns_none_when_book_too_thin():
    cheap = book(ask=100, bid=99)   # 여기서 산다
    dear = book(ask=103, bid=102)   # 여기서 판다
    g = gap(cheap, dear, 1_000_000, "a", "b")
    assert g.gross == pytest.approx(102 / 100 - 1, abs=0.002)  # 1호가만 걸리는 작은 주문
    thin = Book.make([(100, 1)], [(99, 1)])  # 100원어치뿐
    assert gap(thin, dear, 1_000_000) is None  # 잔량이 모자라면 틈으로 치지 않음
    big = gap(book(ask=100, bid=99, ask_krw=1_000_000, levels=5), dear, 3_000_000)
    assert big is None or big.gross < g.gross  # 큰 주문은 호가를 더 걸어 내려가 틈이 줄어듦


def test_net_after_fees():
    assert net_after_fees(0.01, 0.0005, 0.0005) == pytest.approx(1.01 * 0.9995 / 1.0005 - 1)
    assert net_after_fees(0.001, 0.0025, 0.002) < 0  # 일반 수수료면 0.1% 틈은 못 넘음


def test_episode_opens_on_gap_and_closes_when_edge_disappears():
    t = Tracker(notionals=(1_000_000,))
    a_low, b_high = book(100, 99), book(103, 102)
    t.update("X", "a", a_low, now=10.0)
    t.update("X", "b", b_high, now=10.1)  # 틈이 열림: a 에서 사서 b 에 팔기
    assert len(t.open) == 1 and t.drain() == []
    t.update("X", "b", book(103, 102), now=10.4)  # 계속 열려 있음
    t.update("X", "b", book(100, 99), now=10.6)  # b 호가가 내려와 틈이 닫힘
    eps = t.drain()
    assert len(eps) == 1 and eps[0].closed_by == "edge"
    assert eps[0].buy_ex == "a" and eps[0].sell_ex == "b" and eps[0].duration_ms == pytest.approx(500, abs=1)
    assert eps[0].max_gross > 0.01 and t.open == {}


def test_stale_book_is_not_compared_and_closes_episode_as_stale():
    t = Tracker(notionals=(1_000_000,), stale=3.0)
    t.update("X", "a", book(100, 99), now=0.0)
    t.update("X", "b", book(103, 102), now=0.1)
    assert len(t.open) == 1
    t.update("X", "a", book(100, 99), now=5.0)  # b 는 4.9초 동안 조용 → 낡음, 비교에서 뺌
    eps = t.drain()
    assert len(eps) == 1 and eps[0].closed_by == "stale" and t.open == {}
    # 낡은 호가끼리는 새 에피소드를 만들지 않는다
    t.update("X", "a", book(100, 99), now=6.0)
    assert t.open == {}


def test_gap_below_minimum_is_not_recorded():
    t = Tracker(notionals=(1_000_000,))
    t.update("X", "a", book(100, 99), now=0.0)
    t.update("X", "b", book(100.5, 100.05), now=0.1)  # 수수료 전 0.05% 뿐
    assert t.open == {} and t.drain() == []


def test_report_separates_fast_gaps_from_persistent_differences_and_applies_fees():
    rows = [
        {"coin": "A", "buy_ex": "upbit", "sell_ex": "bithumb", "max_gross": 0.0030, "dur_ms": 300.0, "closed_by": "edge"},
        {"coin": "B", "buy_ex": "bithumb", "sell_ex": "upbit", "max_gross": 0.0200, "dur_ms": 9000.0, "closed_by": "stale"},
        {"coin": "C", "buy_ex": "upbit", "sell_ex": "korbit", "max_gross": 0.0015, "dur_ms": 400.0, "closed_by": "edge"},
    ]
    rep = report(rows, 86400.0)
    s = rep["scenarios"]
    assert s["수수료 할인(양쪽 0.05%) · 빠른 틈"]["n"] == 2  # A(0.30%), C(0.15%) 모두 0.10% 수수료를 넘음
    assert s["일반 수수료(가정) · 빠른 틈"]["n"] == 0  # 일반 수수료(0.25~0.45%)면 0.30% 이하는 못 넘음
    assert s["일반 수수료(가정) · 오래 가는 차이"]["n"] == 1  # B 만, 2%는 넘음
    assert s["수수료 할인(양쪽 0.05%) · 빠른 틈"]["per_day"] == pytest.approx(2.0)


def test_store_roundtrip():
    st = ArbStore(":memory:")
    e = Episode("X", "a", "b", 1e6, start=1.0, end=1.5, max_gross=0.01, updates=3, sum_gross=0.027, closed_by="edge")
    st.add_episodes([e])
    r = dict(st.episodes()[0])
    assert r["coin"] == "X" and r["dur_ms"] == pytest.approx(500) and r["closed_by"] == "edge" and r["mean_gross"] == pytest.approx(0.009)


# ── 모의 체결 ──
from autotrade.arb import PaperArb, sim_report  # noqa: E402

FEE0 = {"none": {"a": 0.0, "b": 0.0}}


def sim(lat=0.5, thr=0.001):
    return PaperArb(notional=1_000_000, thresholds={"S": thr}, fees=FEE0, latencies=(lat,))


def test_paper_fill_uses_books_after_latency_not_signal_books():
    s = sim()
    s.update("X", "a", book(100, 99), now=0.0)
    s.update("X", "b", book(103, 102), now=0.1)  # 신호: 약 +2%
    assert s.drain() == [] and len(s.pending["X"]) == 1
    s.update("X", "b", book(103, 102), now=0.3)  # 지연(0.5초) 전 → 아직 체결 안 함
    assert s.drain() == []
    s.update("X", "b", book(100.5, 99.5), now=0.7)  # 지연 뒤: 틈이 사라짐 → 손해로 기록
    rows = s.drain()
    assert len(rows) == 1 and rows[0]["net_signal"] > 0.015 and rows[0]["net"] < 0


def test_paper_inventory_blocks_same_direction_until_reverse():
    s = sim(lat=0.0)
    s.update("X", "a", book(100, 99), now=0.0)
    s.update("X", "b", book(103, 102), now=0.1)   # 신호 (a 에서 사서 b 에 팖)
    s.update("X", "b", book(103, 102), now=0.2)   # 체결
    assert len(s.drain()) == 1
    s.update("X", "b", book(103, 102), now=0.3)   # 같은 방향: 재고 없음 → 신호 없음
    s.update("X", "b", book(103, 102), now=0.4)
    assert s.drain() == [] and not s.pending["X"]
    s.update("X", "b", book(97, 96), now=1.0)     # 반대 방향 틈 (b 에서 사서 a 에 팖)
    s.update("X", "b", book(97, 96), now=1.1)
    assert [r["buy_ex"] for r in s.drain()] == ["b"]
    s.update("X", "b", book(103, 102), now=2.0)   # 이제 처음 방향이 다시 가능
    s.update("X", "b", book(103, 102), now=2.1)
    assert [r["buy_ex"] for r in s.drain()] == ["a"]


def test_paper_skips_odd_gaps_and_stale_and_thin_books():
    s = sim(lat=0.0)
    s.update("X", "a", book(100, 99), now=0.0)
    s.update("X", "b", book(120, 110), now=0.1)   # 10% 차이 → 이상한 값
    assert s.odd >= 1 and not s.pending.get("X")
    s2 = sim(lat=0.5)
    s2.update("X", "a", book(100, 99), now=0.0)
    s2.update("X", "b", book(103, 102), now=0.1)
    s2.update("X", "b", book(103, 102), now=5.0)  # a 호가가 4.9초 낡음
    assert s2.drain()[0]["why"] == "호가 낡음"
    s3 = sim(lat=0.5)
    s3.update("X", "a", book(100, 99), now=0.0)
    s3.update("X", "b", book(103, 102), now=0.1)
    s3.update("X", "b", Book.make([(103, 1)], [(102, 1)]), now=0.7)  # 잔량 102원뿐
    assert s3.drain()[0]["why"] == "잔량 부족"


def test_sim_report():
    rows = [{"key": "S", "coin": "X", "net": 0.01, "net_signal": 0.012}, {"key": "S", "coin": "Y", "net": -0.002, "net_signal": 0.001},
            {"key": "S", "coin": "Y", "net": None, "net_signal": 0.003}]
    r = sim_report(rows, 3600.0, 1_000_000)[0]
    assert r["filled"] == 2 and r["failed"] == 1 and r["win"] == 0.5 and r["total_krw"] == pytest.approx(8000)
