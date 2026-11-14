from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade.takeprofit import ExitRule, crash_signals, minute_grid, run_signals, simulate_trade

RULE = ExitRule(take_profit=0.005, stop_loss=0.02, max_minutes=60, tp_margin=0.0, stop_slip=0.001)
C = 0.001  # 한쪽 비용 (계산 확인용으로 크게)
K = (1 + C) / (1 - C)


def arrays(rows):
    """rows: [(open, high, low), ...] 또는 None(캔들 없음)"""
    o = np.array([r[0] if r else np.nan for r in rows], dtype=float)
    h = np.array([r[1] if r else np.nan for r in rows], dtype=float)
    l = np.array([r[2] if r else np.nan for r in rows], dtype=float)
    return o, h, l


def test_take_profit_net_is_exactly_target():
    tp = 100 * 1.005 * K
    o, h, l = arrays([(100, 100, 100), (100, 100.2, 99.9), (100.3, tp + 0.01, 100.2)])
    e, x, why, net = simulate_trade(o, h, l, 0, C, RULE)
    assert (e, x, why) == (0, 2, "목표 도달") and net == pytest.approx(0.005)


def test_stop_loss_with_extra_slippage():
    sl = 100 * 0.98 * K
    o, h, l = arrays([(100, 100, 100), (99, 99.5, sl - 0.5)])
    _, x, why, net = simulate_trade(o, h, l, 0, C, RULE)
    assert why == "손절" and x == 1
    assert net == pytest.approx(sl * 0.999 * (1 - C) / (100 * (1 + C)) - 1)


def test_same_minute_both_hit_counts_as_stop():
    tp, sl = 100 * 1.005 * K, 100 * 0.98 * K
    o, h, l = arrays([(100, 100, 100), (100, tp + 1, sl - 1)])
    assert simulate_trade(o, h, l, 0, C, RULE)[2] == "손절"


def test_timeout_sells_at_open_after_limit():
    rows = [(100, 100.1, 99.9)] * 60 + [(100.2, 100.3, 100.1)] * 3
    o, h, l = arrays(rows)
    _, x, why, net = simulate_trade(o, h, l, 0, C, RULE)
    assert (x, why) == (60, "24시간 초과")
    assert net == pytest.approx(100.2 * (1 - C) / (100 * (1 + C)) - 1)


def test_entry_waits_for_next_candle_up_to_5_minutes():
    o, h, l = arrays([None, None, (100, 100, 100)] + [(100, 100, 100)] * 70)
    assert simulate_trade(o, h, l, 0, C, RULE)[0] == 2
    o2, h2, l2 = arrays([None] * 6 + [(100, 100, 100)] * 70)
    assert simulate_trade(o2, h2, l2, 0, C, RULE) is None


def test_crash_signal_uses_only_past_hours_and_fires_on_big_drop():
    idx = pd.date_range("2023-01-01", periods=60 * 24 * 32, freq="1min")
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0003, len(idx))))
    crash_at = len(idx) - 200
    close[crash_at:] *= 0.95  # 한 번에 -5% (평소 1시간 흔들림 약 0.23%의 20배)
    m = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close}, index=idx)
    sig = crash_signals(minute_grid(m), k=3.0)
    assert sig.iloc[crash_at] and not sig.iloc[: crash_at].any()


def test_signals_ignored_while_holding():
    idx = pd.date_range("2023-01-01", periods=200, freq="1min")
    px = np.full(200, 100.0)
    g = minute_grid(pd.DataFrame({"Open": px, "High": px, "Low": px, "Close": px}, index=idx))
    sig = pd.Series(False, index=g.index)
    sig.iloc[[10, 20, 100]] = True  # 20분 신호는 첫 거래(최대 60분) 중이라 무시
    tr = run_signals(g, sig, C, RULE, "2023-01-01", "2023-01-01")
    assert list(tr["signal"]) == [idx[10], idx[100]]
    assert (tr["why"] == "24시간 초과").all()
