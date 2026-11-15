from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade.crashbounce import crash_signals, fixed_signals, simulate, t_value
from autotrade.takeprofit import minute_grid


def candles(closes, start="2020-01-01", drop_minutes=()):
    idx = pd.date_range(start, periods=len(closes), freq="1min")
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])  # 시가 = 직전 종가
    m = pd.DataFrame({"Open": o, "High": np.maximum(o, c), "Low": np.minimum(o, c), "Close": c})
    return m.drop(index=[idx[i] for i in drop_minutes])


def wiggle(n, seed=0):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(0, 0.001, n))))


def test_signal_needs_drop_beyond_k_sigma_and_full_window():
    closes = wiggle(60)
    closes[50] = closes[49] * 0.98  # −2% ≫ 6 × 0.1%
    g = minute_grid(candles(closes))
    sig = crash_signals(g, k=6, window=30)
    assert sig.sum() == 1 and sig.iloc[50]
    assert not crash_signals(g, k=6, window=55).any()  # 24시간(여기선 55분)치가 없으면 신호 없음


def test_crash_minute_itself_not_in_sigma():
    closes = wiggle(60)
    closes[50] = closes[49] * 0.98
    g = minute_grid(candles(closes))
    r = g["CloseF"].pct_change()
    excl, incl = r.iloc[20:50].std(), r.iloc[21:51].std()  # 이번 분 제외 / 포함
    k = 0.0201 / ((excl + incl) / 2)  # 제외하면 신호, 포함했다면 신호 아님
    assert k * excl < 0.02 < k * incl
    assert crash_signals(g, k=k, window=30).iloc[50]


def test_no_signal_across_missing_minute():
    closes = wiggle(60)
    closes[50] = closes[48] * 0.98
    g = minute_grid(candles(closes, drop_minutes=[49]))
    assert not crash_signals(g, k=6, window=30).iloc[50]
    assert not fixed_signals(g, 0.005).iloc[50]


def test_buys_next_open_sells_after_hold_and_no_overlap():
    closes = [100.0] * 100
    g = minute_grid(candles(closes))
    sig = pd.Series(False, index=g.index)
    sig.iloc[[10, 20, 50]] = True
    g.loc[g.index[11], "Open"] = 100.0
    g.loc[g.index[41], "Open"] = 102.0
    tr = simulate(g, sig, hold=30, cost=0.0015)
    assert list(tr["entry_time"]) == [g.index[11], g.index[51]]  # 20분 신호는 보유 중이라 무시
    assert tr["exit_time"].iloc[0] == g.index[41]
    assert np.isclose(tr["ret"].iloc[0], 1.02 * 0.9985 / 1.0015 - 1)


def test_waits_for_next_real_candle():
    closes = [100.0] * 100
    g = minute_grid(candles(closes, drop_minutes=[11, 12, 41]))
    sig = pd.Series(False, index=g.index)
    sig.iloc[10] = True
    tr = simulate(g, sig, hold=30)
    assert tr["entry_time"].iloc[0] == g.index[13]
    assert tr["exit_time"].iloc[0] == g.index[43]
    sig2 = pd.Series(False, index=g.index)
    g2 = minute_grid(candles(closes, drop_minutes=list(range(11, 20))))
    sig2 = pd.Series(False, index=g2.index); sig2.iloc[10] = True
    assert simulate(g2, sig2, hold=30, entry_wait=5).empty  # 5분 안에 캔들 없으면 건너뜀


def test_t_value():
    assert np.isclose(t_value(pd.Series([0.01, 0.03])), 0.02 / (np.std([0.01, 0.03], ddof=1) / np.sqrt(2)))
