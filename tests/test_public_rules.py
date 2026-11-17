"""#24~#27 규칙: 미래 참조 없음, 체결가·비용, 신호 다음 날 시가 체결."""
import numpy as np
import pandas as pd
import pytest

from autotrade.public_rules import _hold_signal_returns, breakout_returns


def frame(o, h, l, c):
    idx = pd.date_range("2024-01-01", periods=len(o), freq="D")
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)


def test_breakout_buys_at_target_and_sells_next_open():
    d = frame([100, 100, 110, 120], [110, 108, 115, 125], [90, 95, 105, 115], [105, 106, 112, 122])
    r = breakout_returns(d, 0.5, None, 0.0)
    # 둘째 날: 목표 100 + 0.5*(110-90) = 110, 고가 108 → 안 삼
    assert r.loc["2024-01-02", "r"] == 0 and not r.loc["2024-01-02", "traded"]
    # 셋째 날: 목표 110 + 0.5*(108-95) = 116.5 → 고가 115 안 닿음
    assert not r.loc["2024-01-03", "traded"]
    d2 = frame([100, 100, 110], [110, 120, 115], [90, 95, 105], [105, 115, 112])
    r2 = breakout_returns(d2, 0.5, None, 0.001)
    assert r2.loc["2024-01-02", "r"] == pytest.approx(110 * 0.999 / (110 * 1.001) - 1)  # 목표 110 사서 다음 시가 110


def test_breakout_ignores_future_rows():
    rng = np.random.default_rng(0)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.02, 60))
    d = frame(c, c * 1.02, c * 0.98, c * 1.005)
    full = breakout_returns(d, 0.5, 15, 0.001)
    cut = breakout_returns(d.iloc[:40], 0.5, 15, 0.001)
    pd.testing.assert_series_equal(full["r"].iloc[:38], cut["r"], check_names=False)


def test_signal_holds_from_next_open_with_costs():
    d = frame([100, 100, 110, 121, 121], [1] * 5, [1] * 5, [100, 100, 100, 100, 100])
    enter = pd.Series([False, True, False, False, False], index=d.index)
    exit_ = pd.Series([False, False, True, False, False], index=d.index)
    r = _hold_signal_returns(d, enter, exit_, 0.01)
    # 둘째 날 종가 신호 → 셋째 날 시가 110에 사서, 셋째 날 종가 매도 신호 → 넷째 날 시가 121에 판다
    assert r.loc["2024-01-02", "r"] == 0
    assert r.loc["2024-01-03", "r"] == pytest.approx(121 / 110 * 0.99 / 1.01 - 1)
    assert r.loc["2024-01-04", "r"] == 0
