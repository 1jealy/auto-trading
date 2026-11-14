from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade.confluence import Rules, add_indicators, entry_signal, simulate
from autotrade.indicators import bollinger, ema, hourly_bars, macd, rsi


def test_rsi_extremes_and_known_value():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert rsi(up).iloc[-1] == 100.0
    # 번갈아 +1, -1 → 오른 만큼 내림 → RSI 50 근처
    alt = pd.Series(100 + np.cumsum(np.tile([1.0, -1.0], 100)))
    assert rsi(alt).iloc[-1] == pytest.approx(50, abs=3)


def test_macd_and_ema_definitions():
    s = pd.Series(np.linspace(100, 200, 100))
    m = macd(s)
    assert (m["macd"].iloc[30:] > 0).all()  # 꾸준히 오르면 빠른 평균이 위
    pd.testing.assert_series_equal(m["hist"], m["macd"] - m["signal"], check_names=False)
    assert ema(s, 50).iloc[-1] < s.iloc[-1]


def test_bollinger_width():
    s = pd.Series([10.0] * 19 + [10.0])
    b = bollinger(s)
    assert b["upper"].iloc[-1] == b["mid"].iloc[-1] == 10.0


def test_hourly_bars_from_minutes():
    idx = pd.date_range("2023-01-01 00:00", periods=120, freq="1min")
    m = pd.DataFrame({"Open": np.arange(120.0), "High": np.arange(120.0) + 1, "Low": np.arange(120.0) - 1,
                      "Close": np.arange(120.0) + 0.5, "Volume": 1.0}, index=idx)
    h = hourly_bars(m)
    assert list(h.index) == list(pd.to_datetime(["2023-01-01 00:00", "2023-01-01 01:00"]))
    assert h.iloc[0].tolist() == [0.0, 60.0, -1.0, 59.5, 60.0]


def frame(rows):
    """rows: (open, high, low, close) — 지표 열은 직접 넣는다."""
    idx = pd.date_range("2023-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def with_signals(d, entry_at, exit_at=()):
    d = d.copy()
    d["hist"], d["ema"] = 1.0, 0.0  # 기본: 나가는 신호 없음
    d.loc[d.index[list(exit_at)], "hist"] = -1.0
    d["rsi"], d["upper"], d["Volume"], d["vol_avg"] = 60.0, 1e9, 2.0, 1.0
    d.loc[d.index[entry_at] - pd.Timedelta(hours=1), "hist"] = -1.0  # 이 캔들에서 0 위로 바뀜
    return d


R = Rules(max_bars=5)


def test_entry_next_open_exit_on_signal_next_open():
    d = with_signals(frame([(100, 101, 99, 100)] * 4 + [(100, 101, 99, 100), (110, 111, 109, 110),
                                                       (112, 113, 111, 112), (115, 116, 114, 115)] + [(100, 101, 99, 100)] * 4),
                     entry_at=4, exit_at=(6,))
    tr = simulate(d, 0.0, R, "2022-12-29", "2023-01-02")
    t = tr.iloc[0]
    assert (t.buy, t.sell, t.why) == (110, 115, "지표 신호")


def test_stop_loss_fill_and_priority():
    d = with_signals(frame([(100, 101, 99, 100)] * 5 + [(100, 100, 96.9, 98)] + [(100, 101, 99, 100)] * 6),
                     entry_at=4, exit_at=(5,))
    t = simulate(d, 0.0, R, "2022-12-29", "2023-01-02").iloc[0]
    assert t.why == "손절" and t.sell == pytest.approx(97 * 0.999)


def test_max_hold():
    d = with_signals(frame([(100, 101, 99, 100)] * 15), entry_at=4)
    t = simulate(d, 0.0, R, "2022-12-29", "2023-01-02").iloc[0]
    assert t.why == "48시간" and t.hours == 5


def test_entry_signal_needs_all_conditions():
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))),
                      index=pd.date_range("2023-01-01", periods=600, freq="1h"))
    bars = pd.DataFrame({"Open": close.shift(1).fillna(100), "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": rng.uniform(1, 3, 600)})
    d = add_indicators(bars)
    sig = entry_signal(d)
    hit = d[sig]
    assert len(hit) > 0
    assert (hit["Close"] > hit["ema"]).all() and hit["rsi"].between(50, 70).all()
    assert (hit["Volume"] >= 1.2 * hit["vol_avg"]).all() and (hit["Close"] < hit["upper"]).all()
