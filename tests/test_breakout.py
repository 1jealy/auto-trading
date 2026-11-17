from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade.breakout import Params, add_indicators, atr, breakout_positions, equity_from_positions, filter_positions


def bars(opens, highs=None, lows=None, closes=None):
    n = len(opens)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    o = np.asarray(opens, float)
    c = np.asarray(closes if closes is not None else o, float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c), float)
    l = np.asarray(lows if lows is not None else np.minimum(o, c), float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1.0}, index=idx)


def test_atr_constant_range():
    d = bars([100] * 50, highs=[102] * 50, lows=[98] * 50)
    assert atr(d).iloc[-1] == pytest.approx(4.0)


def test_equity_costs_and_trade():
    d = bars([100, 100, 110, 121, 121, 121])
    pos = np.array([False, True, True, False, False, False])  # 캔들1 끝에 사기로 → 시가2 매수, 캔들3 끝에 팔기로 → 시가4 매도
    eq, tr = equity_from_positions(d, pos, 0.01)
    assert tr.iloc[0]["entry"] == d.index[2] and tr.iloc[0]["exit"] == d.index[4]
    assert tr.iloc[0]["net"] == pytest.approx(121 / 110 * 0.99 / 1.01 - 1)
    assert eq.iloc[-1] == pytest.approx(121 / 110 * 0.99 / 1.01)
    assert eq.iloc[2] == pytest.approx(1.0)  # 자산 값은 그 시가에 거래하기 '직전' 값
    assert eq.iloc[3] == pytest.approx(121 / 110 / 1.01)  # 시가2 매수(비용) 후 시가3까지 보유


def test_filter_positions_respect_period():
    d = bars(list(range(1, 30)))
    d["sma"] = 0.0
    pos = filter_positions(d, first=5, last=20)
    assert not pos[:5].any() and pos[5:19].all() and not pos[19:].any()


def test_trailing_stop_exits_after_peak_drop():
    n = 12
    d = bars([100] * n, highs=[100, 100, 100, 120, 130, 125, 110, 100, 100, 100, 100, 100],
             closes=[100, 100, 101, 118, 128, 124, 105, 100, 100, 100, 100, 100])
    d["sma"], d["atr"] = 50.0, 5.0
    d["entry"] = False
    d.loc[d.index[2], "entry"] = True
    pos = breakout_positions(d, first=0, last=n)
    # 캔들 2 끝에 매수 결정 → 3부터 보유. 고점 130(캔들 4), 130 - 15 = 115 → 캔들 6 종가 105 에서 팔기로
    assert list(np.flatnonzero(pos)) == [2, 3, 4, 5]


def test_entry_requires_recent_squeeze_and_breakout():
    rng = np.random.default_rng(5)
    # 오랫동안 크게 흔들리다(폭 넓음) 최근 조용해진 뒤(응축) 위로 뚫는 가격
    noisy = 100 + np.cumsum(rng.normal(0, 2.0, 1500))
    calm = noisy[-1] + rng.normal(0, 0.05, 30)
    jump = np.array([calm[-1] + 8])
    c = np.concatenate([noisy, calm, jump]) + 300
    d = add_indicators(bars(c, highs=c + 0.1, lows=c - 0.1, closes=c), Params(sma_n=50))
    last = d.iloc[-1]
    assert bool(last["recent_squeeze"]) and last["Close"] > last["dc_high"]
    assert bool(last["entry"]) == bool(last["Close"] > last["sma"])


def test_breakout_trend_positions_exits_only_below_sma():
    import numpy as np
    import pandas as pd

    from autotrade.breakout import breakout_trend_positions

    d = pd.DataFrame({"Close": [10, 12, 11, 9, 13], "sma": [11, 11, 10, 10, 10],
                      "entry": [False, True, False, False, True]})
    pos = breakout_trend_positions(d, 0, 5)
    # 1에서 사고, 2는 추적 손절이 없으니 계속(11 > 10), 3에서 종가 9 < 10 → 판다, 4는 기간 끝
    assert list(pos) == [False, True, True, False, False]
