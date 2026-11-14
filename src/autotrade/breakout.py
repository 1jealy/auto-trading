"""4시간봉 변동성 응축 돌파 + 60일 추세 필터 (실험 #8, docs/experiments.md 참고).

사고파는 결정은 캔들이 끝났을 때, 체결은 다음 캔들 시가. 그래서 평가는 '시가 → 다음 시가'로 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autotrade.indicators import bollinger


@dataclass(frozen=True)
class Params:
    sma_n: int = 360  # 4시간 × 360 = 60일
    bb_n: int = 20
    squeeze_window: int = 1080  # 약 6개월
    squeeze_pct: float = 0.20
    squeeze_recent: int = 6  # 직전 하루(6개 캔들) 안
    breakout_n: int = 20
    atr_n: int = 14
    atr_mult: float = 3.0


def resample_bars(m: pd.DataFrame, freq: str) -> pd.DataFrame:
    """1분 캔들(UTC) → freq 캔들. 캔들이 하나도 없는 구간은 뺀다."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return m[list(agg)].resample(freq, label="left", closed="left").agg(agg).dropna(subset=["Open", "Close"])


def atr(d: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev).abs(), (d["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(bars: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    d = bars.copy()
    d["sma"] = d["Close"].rolling(p.sma_n).mean()
    bb = bollinger(d["Close"], p.bb_n)
    d["bw"] = (bb["upper"] - bb["lower"]) / bb["mid"]
    # 이번 폭이 직전 window 개(이번 포함) 중 몇 % 위치인지 → 하위 20% 이하면 응축
    d["bw_rank"] = d["bw"].rolling(p.squeeze_window, min_periods=p.squeeze_window).apply(
        lambda w: (w <= w[-1]).mean(), raw=True)
    d["squeeze"] = d["bw_rank"] <= p.squeeze_pct
    d["recent_squeeze"] = d["squeeze"].shift(1).astype(float).rolling(p.squeeze_recent).max() >= 1
    d["dc_high"] = d["High"].shift(1).rolling(p.breakout_n).max()
    d["atr"] = atr(d, p.atr_n)
    d["entry"] = (d["Close"] > d["sma"]) & d["recent_squeeze"] & (d["Close"] > d["dc_high"])
    return d


def breakout_positions(d: pd.DataFrame, first: int, last: int, p: Params = Params()) -> np.ndarray:
    """pos[t] = 캔들 t 가 끝났을 때 결정한 '다음 캔들 동안 들고 있을지'. first 전에는 사지 않고, last 이후는 판다."""
    hi, cl, sma, a, ent = (d[c].to_numpy() for c in ("High", "Close", "sma", "atr", "entry"))
    n = len(d)
    pos = np.zeros(n, dtype=bool)
    holding, peak = False, -np.inf
    for t in range(n):
        if holding:
            peak = max(peak, hi[t])  # 산 뒤 지금까지 가장 높은 고가
            if cl[t] < peak - p.atr_mult * a[t] or cl[t] < sma[t]:
                holding = False
        elif first <= t < last and bool(ent[t]):
            holding, peak = True, -np.inf  # 다음 캔들부터 들고 있음
        if t >= last - 1:
            holding = False  # 기간 끝에서 정리
        pos[t] = holding
    return pos


def filter_positions(d: pd.DataFrame, first: int, last: int) -> np.ndarray:
    """비교용: 종가 > 60일 평균이면 다음 캔들 동안 보유."""
    pos = (d["Close"] > d["sma"]).fillna(False).to_numpy().copy()
    pos[:first] = False
    pos[last - 1:] = False
    return pos


def equity_from_positions(d: pd.DataFrame, pos: np.ndarray, side_cost: float) -> tuple[pd.Series, pd.DataFrame]:
    """시가→다음 시가 평가. 자산은 각 캔들 시가 시각의 값(1.0 시작). 거래 목록도 돌려준다."""
    o = d["Open"].to_numpy()
    idx = d.index
    n = len(o)
    held = np.zeros(n, dtype=bool)
    held[1:] = pos[:-1]  # 구간 j = [시가 j, 시가 j+1) 는 캔들 j-1 끝의 결정을 따름
    value = np.ones(n)
    trades, entry_j = [], None
    for j in range(n - 1):
        v = value[j]
        if held[j] and not (j > 0 and held[j - 1]):  # 시가 j 에 매수
            v /= 1 + side_cost
            entry_j = j
        if not held[j] and j > 0 and held[j - 1]:  # 시가 j 에 매도
            v *= 1 - side_cost
            trades.append(dict(entry=idx[entry_j], exit=idx[j], bars=j - entry_j,
                               net=o[j] / o[entry_j] * (1 - side_cost) / (1 + side_cost) - 1))
        if held[j]:
            v *= o[j + 1] / o[j]
        value[j + 1] = v
    return pd.Series(value, index=idx), pd.DataFrame(trades, columns=["entry", "exit", "bars", "net"])
