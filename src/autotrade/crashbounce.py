"""1분 급락 직후 정해진 시간만 보유 (실험 #10, docs/experiments.md 참고).

표는 takeprofit.minute_grid 로 만든다(1분 간격, Open/Close 는 실제 캔들만, CloseF 는 마지막 종가 이어 붙임).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade.takeprofit import minute_grid  # noqa: F401  (스크립트에서 함께 쓰기 편하게)


def crash_signals(g: pd.DataFrame, k: float = 6.0, window: int = 1440) -> pd.Series:
    """r ≤ -k × σ 인 분(True). r 은 이번 분 종가 ÷ 직전 분 종가 − 1 (두 분 모두 실제 캔들일 때만).
    σ 는 직전 window 분(이번 분 제외)의 1분 수익률 표준편차 — 빈 분은 이어 붙인 종가라 수익률 0.
    """
    r_filled = g["CloseF"].pct_change()
    sigma = r_filled.rolling(window, min_periods=window).std().shift(1)
    real = g["Close"].notna() & g["Close"].shift(1).notna()
    r = g["Close"] / g["Close"].shift(1) - 1
    return (real & (r <= -k * sigma)).fillna(False)


def fixed_signals(g: pd.DataFrame, drop: float = 0.005) -> pd.Series:
    """참고용: 1분에 drop 이상 하락 (두 분 모두 실제 캔들)."""
    r = g["Close"] / g["Close"].shift(1) - 1
    return (g["Close"].notna() & g["Close"].shift(1).notna() & (r <= -drop)).fillna(False)


def simulate(g: pd.DataFrame, signals: pd.Series, hold: int = 30, cost: float = 0.0015,
             entry_wait: int = 5, start=None, end=None) -> pd.DataFrame:
    """신호 다음 분 시가에 사서 hold 분 뒤 분의 시가에 판다. 한 번에 하나만.

    start~end 사이에 난 신호만 쓴다. 반환: 거래별 signal, entry_time, exit_time, entry, exit, ret(비용 뺀 것), gross.
    """
    opens = g["Open"].to_numpy()
    idx = g.index
    sig_pos = np.flatnonzero(signals.reindex(idx, fill_value=False).to_numpy())
    if start is not None:
        sig_pos = sig_pos[idx[sig_pos] >= pd.Timestamp(start)]
    if end is not None:
        sig_pos = sig_pos[idx[sig_pos] < pd.Timestamp(end) + pd.Timedelta(days=1)]
    n = len(opens)
    rows = []
    free_from = -1  # 이 위치(분)부터 새 신호를 받는다
    for s in sig_pos:
        if s < free_from:
            continue
        e = next((j for j in range(s + 1, min(s + 1 + entry_wait, n)) if not np.isnan(opens[j])), None)
        if e is None:
            continue
        x = next((j for j in range(e + hold, n) if not np.isnan(opens[j])), None)
        if x is None:
            break
        gross = opens[x] / opens[e]
        rows.append(dict(signal=idx[s], entry_time=idx[e], exit_time=idx[x], entry=opens[e], exit=opens[x],
                         gross=gross - 1, ret=gross * (1 - cost) / (1 + cost) - 1))
        free_from = x
    return pd.DataFrame(rows, columns=["signal", "entry_time", "exit_time", "entry", "exit", "gross", "ret"])


def t_value(rets: pd.Series) -> float:
    rets = rets.dropna()
    if len(rets) < 2 or rets.std() == 0:
        return float("nan")
    return float(rets.mean() / (rets.std() / np.sqrt(len(rets))))
