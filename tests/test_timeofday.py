from __future__ import annotations

import pandas as pd
import pytest

from autotrade.timeofday import after_cost, breakeven_side_cost, daily_equity, fill_forward_opens, window_returns


def minutes(day: str, values: dict[str, float]) -> pd.Series:
    """'HH:MM' → 시가."""
    idx = pd.to_datetime([f"{day} {k}" for k in values])
    return pd.Series(list(values.values()), index=idx, dtype=float)


def test_buy_and_sell_at_exact_minutes():
    s = minutes("2023-01-02", {"00:00": 1, "21:00": 100, "22:00": 105, "23:00": 110, "23:59": 1})
    days = pd.DatetimeIndex(["2023-01-02"])
    out = window_returns(fill_forward_opens(s), 21, 2, days)
    assert out.loc["2023-01-02", "buy"] == 100 and out.loc["2023-01-02", "sell"] == 110
    assert out.loc["2023-01-02", "ret"] == pytest.approx(0.10)


def test_missing_minute_uses_next_candle_within_5_minutes():
    s = minutes("2023-01-02", {"00:00": 1, "21:04": 100, "23:00": 99, "23:59": 1})
    out = window_returns(fill_forward_opens(s), 21, 2, pd.DatetimeIndex(["2023-01-02"]))
    assert out.iloc[0]["buy"] == 100


def test_day_skipped_when_no_candle_within_5_minutes():
    s = minutes("2023-01-02", {"00:00": 1, "21:06": 100, "23:00": 99, "23:59": 1})
    out = window_returns(fill_forward_opens(s), 21, 2, pd.DatetimeIndex(["2023-01-02"]))
    assert out.empty


def test_window_crossing_midnight_sells_next_day():
    s = pd.concat([minutes("2023-01-02", {"00:00": 1, "23:00": 100}), minutes("2023-01-03", {"01:00": 90, "23:59": 1})])
    out = window_returns(fill_forward_opens(s), 23, 2, pd.DatetimeIndex(["2023-01-02"]))
    assert out.iloc[0]["sell"] == 90


def test_costs_and_breakeven():
    gross = pd.Series([0.01, -0.005, 0.002])
    c = breakeven_side_cost(gross)
    assert float((1 + after_cost(gross, c)).prod()) == pytest.approx(1.0)
    assert after_cost(pd.Series([0.0]), 0.001).iloc[0] == pytest.approx(0.999 / 1.001 - 1)


def test_rest_days_count_as_zero():
    days = pd.date_range("2023-01-01", periods=4, freq="D")
    eq = daily_equity(pd.Series([0.1, -0.1], index=days[[1, 3]]), days)
    assert list(eq.round(4)) == [1.0, 1.1, 1.1, 0.99]
