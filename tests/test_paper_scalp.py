"""모의 단타 로직 테스트 — 네트워크 없음. 체결 시점(지연), 조건, 비용 계산을 확인한다."""

from __future__ import annotations

import pytest

from autotrade.paper.book import Book, market_buy, market_sell, round_trip_cost
from autotrade.paper.scalp import ALWAYS, FORMAL, Scalper


def book(bid: float, ask: float, size_krw: float = 50_000_000, tick: float = 1000.0, levels: int = 5) -> Book:
    asks = [(ask + i * tick, size_krw / ask) for i in range(levels)]
    bids = [(bid - i * tick, size_krw / bid) for i in range(levels)]
    return Book.make(asks, bids)


# ── 호가창 체결 ──
def test_market_buy_walks_levels_and_reports_average():
    b = Book.make([(100, 1), (101, 1), (102, 10)], [(99, 5)])
    f = market_buy(b, 100 + 101 + 51)  # 1개 + 1개 + 0.5개
    assert f.complete and f.levels == 3
    assert f.qty == pytest.approx(2.5)
    assert f.price == pytest.approx(252 / 2.5)


def test_market_orders_flag_insufficient_depth():
    b = Book.make([(100, 1)], [(99, 1)])
    assert not market_buy(b, 1000).complete
    assert not market_sell(b, 5).complete
    assert round_trip_cost(b, 1000) is None


def test_round_trip_cost_is_spread_for_small_order():
    b = book(99_000, 100_000)
    assert round_trip_cost(b, 1_000_000) == pytest.approx(1 - 99_000 / 100_000)


def test_one_tick_spread_detected_from_level_gaps():
    assert book(1350, 1351, tick=1).one_tick_spread()          # 0.074% 지만 딱 한 칸
    assert not book(1350, 1352, tick=1).one_tick_spread()


# ── 신호와 체결 시점 ──
def run_up(s: Scalper, coin="BTC", g0=100.0, g1=100.2, bid=99_990_000.0, ask=100_000_000.0, t0=0.0):
    """t0~t0+3 초: 해외는 g0, t0+3.5 에 g1 로 뛰고 업비트는 그대로."""
    for i in range(7):
        s.on_tick(t0 + i * 0.5, coin, g0, book(bid, ask))
    s.on_tick(t0 + 3.5, coin, g1, book(bid, ask))


def test_always_scalper_signals_but_does_not_fill_on_the_signal_book():
    s = Scalper(params=(ALWAYS,))
    run_up(s)  # 해외 +0.2%, 업비트 0% → 신호
    assert len(s.entries) == 1 and not s.pos
    assert s.next_due() == pytest.approx(3.7)


def test_fill_needs_a_book_requested_after_the_latency():
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    s.on_book(3.6, "BTC", book(99_990_000, 100_000_000))  # 0.1초 뒤 호가 → 아직 안 됨
    assert not s.pos
    later = book(100_090_000, 100_100_000)  # 0.2초 뒤에는 이미 올라 있음 → 그 비싼 값에 산다
    s.on_book(3.7, "BTC", later)
    pos = s.pos[("s18", "BTC")]
    assert pos.price == pytest.approx(100_100_000)
    assert pos.fill_t == 3.7 and pos.signal_t == 3.5
    assert s.cash["s18"] == pytest.approx(10_000_000 - 1_000_000 * 1.0005)


def test_no_signal_when_reference_sample_is_too_old():
    s = Scalper(params=(ALWAYS,))
    s.on_tick(0.0, "BTC", 100.0, book(99_990_000, 100_000_000))
    s.on_tick(5.0, "BTC", 100.5, book(99_990_000, 100_000_000))  # 3초 전 기록이 4.5초보다 오래됨
    assert not s.entries and s.live["BTC"]["move"] is None


def test_no_signal_when_upbit_already_moved():
    s = Scalper(params=(ALWAYS,))
    for i in range(7):
        s.on_tick(i * 0.5, "BTC", 100.0, book(99_990_000, 100_000_000))
    s.on_tick(3.5, "BTC", 100.2, book(100_180_000, 100_190_000))  # 업비트도 +0.19% → 뒤처짐 0.01%p
    assert not s.entries


def test_timed_exit_sells_into_bids_and_records_costs():
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
    s.on_tick(30.0, "BTC", 100.2, book(100_090_000, 100_100_000))
    assert s.pos  # 30초 + 0.2초 전에는 안 판다 (따라잡지도, 출발점으로 돌아오지도 않음)
    s.on_tick(34.0, "BTC", 100.2, book(100_090_000, 100_100_000))  # 3.7 + 30 + 0.2 지난 뒤
    assert not s.pos
    (tr,) = [e for e in s.drain() if e["type"] == "trade"]
    assert tr["reason"] == "시간" and tr["exit_t"] == 34.0
    gross = 100_090_000 / 100_000_000
    assert tr["gross_pct"] == pytest.approx(gross - 1)
    assert tr["net_krw"] == pytest.approx(1_000_000 * gross * 0.9995 - 1_000_000 * 1.0005)
    assert s.cash["s18"] == pytest.approx(10_000_000 + tr["net_krw"])


def test_no_second_entry_while_holding_and_s17_stops_when_cash_runs_out():
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
    s.on_tick(4.0, "BTC", 100.5, book(99_990_000, 100_000_000))  # 또 신호지만 들고 있음
    assert len(s.pos) == 1 and not s.entries
    f = Scalper(params=(FORMAL,), cash={"s17": 3_000_000})
    formal_signal(f, book(1350, 1351, tick=1))  # 남은 돈 300만 원 < 500만 원 → 못 삼
    assert not f.entries
    assert any(e["type"] == "skip" and e["why"] == "남은 돈 부족" for e in f.drain())


def test_s18_refills_cash_and_records_it():
    s = Scalper(params=(ALWAYS,), cash={"s18": 500_000})
    run_up(s)
    assert len(s.entries) == 1  # 돈이 모자라도 신호는 받는다
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
    assert ("s18", "BTC") in s.pos
    assert s.cash["s18"] == pytest.approx(500_000 + 10_000_000 - 1_000_000 * 1.0005)
    (ev,) = [e for e in s.drain() if e["type"] == "refill"]
    assert ev["amount"] == 10_000_000 and ev["key"] == "s18"


def test_s18_needs_lag_above_cost_at_that_moment():
    # 해외 +0.2%, 업비트 그대로 → 뒤처짐 0.2%p. 호가 차이 0.01% 면 비용 0.16% → 신호
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    assert len(s.entries) == 1
    # 호가 차이가 0.1% 면 비용 0.1% + 0.1% + 0.05% = 0.25% > 0.2%p → 신호 없음
    s2 = Scalper(params=(ALWAYS,))
    run_up(s2, bid=99_900_000, ask=100_000_000)
    assert not s2.entries
    # 해외 +0.12% 는 뒤처짐이 충분해도 기준(+0.15%) 미달
    s3 = Scalper(params=(ALWAYS,))
    run_up(s3, g1=100.12)
    assert not s3.entries


def test_s18_sells_when_upbit_catches_up_or_origin_collapses():
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
    s.on_tick(5.0, "BTC", 100.2, book(100_200_000, 100_210_000))  # 매수 1호가 ≥ 신호 3초 전 매도 1호가 × 1.002
    assert s.pos[("s18", "BTC")].exit_reason == "따라잡음"
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
    s.on_tick(5.0, "BTC", 100.0, book(99_990_000, 100_000_000))
    assert s.pos[("s18", "BTC")].exit_reason == "출발점 붕괴"


# ── 정식(#17) 조건 ──
def formal_signal(s: Scalper, b: Book, coin="XRP"):
    for i in range(7):
        s.on_tick(i * 0.5, coin, 1.0, b)
    s.on_tick(3.5, coin, 1.004, b)  # 해외 +0.4%


def test_formal_accepts_one_tick_spread_even_above_005pct():
    s = Scalper(params=(FORMAL,))
    formal_signal(s, book(1350, 1351, tick=1))  # 0.074% 지만 한 칸
    assert len(s.entries) == 1


def test_formal_rejects_wide_spread_and_thin_depth():
    s = Scalper(params=(FORMAL,))
    formal_signal(s, book(1350, 1352, tick=1))  # 두 칸, 0.15%
    assert not s.entries
    s2 = Scalper(params=(FORMAL,))
    formal_signal(s2, book(1350, 1351, tick=1, size_krw=1_000_000))  # 1~3호가 합 300만 원
    assert not s2.entries
    whys = {e["why"] for e in s.drain() + s2.drain() if e["type"] == "skip"}
    assert whys == {"호가 차이가 넓음", "매도 1~3호가 잔량 부족"}


def test_formal_exit_reasons_and_latency():
    def held():
        s = Scalper(params=(FORMAL,))
        formal_signal(s, book(99_990_000, 100_000_000), coin="BTC")
        s.on_book(3.7, "BTC", book(99_990_000, 100_000_000))
        return s

    s = held()  # 손절이 다른 조건보다 먼저
    s.on_tick(5.0, "BTC", 0.99, book(99_600_000, 99_610_000))
    assert s.pos[("s17", "BTC")].exit_reason == "손절"
    assert s.pos[("s17", "BTC")].exit_due == pytest.approx(5.2)
    s.on_book(5.1, "BTC", book(99_600_000, 99_610_000))
    assert s.pos  # 0.2초가 안 지났다
    s.on_book(5.2, "BTC", book(99_500_000, 99_510_000))
    (tr,) = [e for e in s.drain() if e["type"] == "trade"]
    assert tr["exit_price"] == pytest.approx(99_500_000) and tr["detail"]["latency_out"] == pytest.approx(0.2)

    s = held()  # 해외가 출발점으로 돌아옴
    s.on_tick(5.0, "BTC", 1.0, book(99_990_000, 100_000_000))
    assert s.pos[("s17", "BTC")].exit_reason == "출발점 붕괴"

    s = held()  # +0.25% 익절
    s.on_tick(5.0, "BTC", 1.004, book(100_260_000, 100_270_000))
    assert s.pos[("s17", "BTC")].exit_reason == "익절"

    s = held()  # 60초
    s.on_tick(64.0, "BTC", 1.004, book(100_100_000, 100_110_000))
    (tr,) = [e for e in s.drain() if e["type"] == "trade"]
    assert tr["reason"] == "시간"


def test_coinone_shadow_fill_uses_zero_fee():
    s = Scalper(params=(ALWAYS,))
    run_up(s)
    alt_in, alt_out = book(99_950_000, 100_050_000), book(100_150_000, 100_250_000)
    s.on_book(3.7, "BTC", book(99_990_000, 100_000_000), alt_in)
    s.on_book(34.0, "BTC", book(100_090_000, 100_100_000), alt_out)
    (tr,) = [e for e in s.drain() if e["type"] == "trade"]
    assert tr["alt_net_pct"] == pytest.approx(100_150_000 / 100_050_000 - 1)


# ── 지연 관측 ──
def test_radar_records_actual_offsets_and_cooldown():
    s = Scalper(params=())
    run_up(s)  # +0.2% ≥ 0.15% → 관측 시작
    s.on_book(3.75, "BTC", book(100_000_000, 100_010_000))  # 0.2초 지점을 0.25초에 관측
    s.on_tick(4.0, "BTC", 100.4, book(99_990_000, 100_000_000))  # 5초 안의 두 번째는 세지 않음
    assert len(s.radars) == 1
    for t in (4.0, 4.5, 5.5, 6.5, 8.5, 13.5, 23.5):
        s.on_book(t, "BTC", book(100_100_000, 100_110_000))
    (r,) = [e for e in s.drain() if e["type"] == "radar"]
    assert r["points"][0]["at"] == pytest.approx(0.25) and r["points"][0]["ask"] == 100_010_000
    assert len(r["points"]) == 8 and r["ref_ask"] == 100_000_000


# ── 채점: 사건 단위 ──
def test_trades_with_signals_close_together_count_as_one_event(tmp_path):
    from autotrade.paper.store import Store

    store = Store(tmp_path / "p.db")
    base = {"type": "trade", "key": "s18", "source": "okx_swap", "amount": 1_000_000, "entry_price": 1,
            "exit_price": 1, "reason": "시간", "gross_pct": 0, "alt_net_pct": None, "move": 0.002, "lag": 0.002,
            "detail": {}}
    # 4개 코인이 같은 순간 신호(한 사건), 10분 뒤 하나 더
    for i, (coin, sig, net) in enumerate([("BTC", 1000, 0.01), ("ETH", 1000.5, 0.01), ("XRP", 1001, 0.01),
                                          ("SOL", 1001, 0.01), ("BTC", 1600, -0.002)]):
        store.add_event({**base, "coin": coin, "signal_t": sig, "entry_t": sig + 0.2, "exit_signal_t": sig + 30,
                         "exit_t": sig + 30.2, "net_pct": net, "net_krw": net * 1_000_000})
    store.add_event({"type": "refill", "key": "s18", "t": 5, "amount": 10_000_000, "source": "okx_swap"})
    s = store.scalp_summary("s18", "okx_swap")
    assert s["n"] == 5 and s["n_events"] == 2 and s["refills"] == 1
    assert s["mean_event"] == pytest.approx((0.01 - 0.002) / 2)
    assert store.realized("s18") == pytest.approx(0.04 * 1_000_000 - 2_000 + 10_000_000)
