from __future__ import annotations

import pandas as pd
import pytest

from autotrade.metrics import compare, summarize


def test_max_drawdown_and_total_return():
    equity = pd.Series([100, 120, 90, 110, 130], index=pd.bdate_range("2020-01-01", periods=5), dtype=float)
    s = summarize(equity)
    assert s["total_return"] == pytest.approx(0.30)
    assert s["max_drawdown"] == pytest.approx(90 / 120 - 1)


def test_cagr_for_one_year_of_steady_growth():
    dates = pd.bdate_range("2020-01-01", periods=253)
    equity = pd.Series(100 * 1.10 ** (pd.RangeIndex(253) / 252), index=dates)
    assert summarize(equity)["cagr"] == pytest.approx(0.10)


def test_compare_aligns_periods():
    a = pd.Series([1.0, 1.1, 1.2], index=pd.bdate_range("2020-01-01", periods=3))
    b = pd.Series([1.0, 1.0, 1.0, 1.0], index=pd.bdate_range("2019-12-31", periods=4))
    table = compare({"a": a, "b": b})
    assert list(table.index) == ["a", "b"]
    assert table.loc["b", "total_return"] == 0
