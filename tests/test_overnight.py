from __future__ import annotations

import pandas as pd
import pytest

from autotrade import CostModel
from autotrade.overnight import breakeven_price, night_day_returns, simulate

COSTS = CostModel(commission=0.001, sell_tax=0.0, slippage=0.0)  # 계산 확인용으로 크게


def ohlc(rows):
    """rows: [(open, high, close), ...]"""
    idx = pd.bdate_range("2020-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Close"], index=idx)


def test_breakeven_has_zero_net_return():
    t = breakeven_price(100.0, COSTS)
    assert t * (1 - COSTS.sell_rate) / (100.0 * (1 + COSTS.buy_rate)) == pytest.approx(1.0)


def test_pure_buys_at_close_and_sells_next_open():
    data = ohlc([(99, 100, 100), (102, 103, 101), (100, 102, 100)])
    eq, tr = simulate(data, COSTS, mode="pure")
    first = tr.iloc[0]
    assert (first.buy_px, first.sell_px, first.kind) == (100, 102, "아침 매도")
    assert first.ret == pytest.approx(102 * 0.999 / (100 * 1.001) - 1)
    assert tr.iloc[1].buy_px == 101  # 판 날 종가에 다시 삼


def test_wait_sells_at_breakeven_during_the_day():
    data = ohlc([(100, 100, 100), (98, 101, 99), (99, 99, 99)])
    eq, tr = simulate(data, COSTS, mode="wait")
    first = tr.iloc[0]
    assert first.kind == "낮에 본전 매도"
    assert first.sell_px == pytest.approx(breakeven_price(100, COSTS))
    assert first.ret == pytest.approx(0.0)
    # 낮에 판 날 종가(99)에 다시 삼 → 그날 종가 평가 = 본전 1.0 에서 매수 비용만 빠짐
    assert eq.iloc[1] == pytest.approx(1 / 1.001)


def test_wait_forces_sale_on_second_morning():
    data = ohlc([(100, 100, 100), (98, 99, 97), (95, 96, 96), (96, 96, 96)])
    eq, tr = simulate(data, COSTS, mode="wait")
    first = tr.iloc[0]
    assert (first.sell_date, first.sell_px, first.kind) == (data.index[2], 95, "다음날 강제 매도")
    # 기다린 하루 동안은 종가로 평가되고, 그 사이 추가 매수는 없음
    assert eq.iloc[1] == pytest.approx(97 / (100 * 1.001))
    assert len(tr) == 1  # 판 날 종가(96)에 다시 샀고, 마지막 날까지 아직 안 팔림


def test_high_must_clear_breakeven_with_margin():
    t = breakeven_price(100, COSTS)
    data = ohlc([(100, 100, 100), (98, t * 1.0001, 99), (99, 99, 99)])
    _, tr = simulate(data, COSTS, mode="wait", fill_margin=0.0002)
    assert tr.iloc[0].kind == "다음날 강제 매도"


def test_night_day_split_multiplies_back_to_close_to_close():
    data = ohlc([(100, 100, 100), (103, 104, 101), (99, 102, 102)])
    nd = night_day_returns(data)
    total = (1 + nd["night"]) * (1 + nd["day"]) - 1
    expected = data["Close"].pct_change().iloc[1:]
    pd.testing.assert_series_equal(total, expected, check_names=False)
