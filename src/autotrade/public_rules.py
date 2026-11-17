"""#24~#27 공개 자동매매 프로젝트의 일봉 규칙 4개 (docs/experiments.md). 숫자는 원래 규칙에 적힌 값 그대로.

모든 결과는 '시가에서 다음 시가까지' 하루 수익률로 맞춘다(계좌 가치는 매일 시가로 본다).
- P1·P2 변동성 돌파: 그날 시가 + K × 어제 범위에 닿으면 그 값에 사고(미리 걸어 둔 주문), 다음 날 시가에 판다.
- P3·P4 되돌림: 그날 종가로 신호를 보고 다음 날 시가에 사고판다(종가를 보고 같은 날 종가에 체결하지 않음).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade.indicators import rsi


def breakout_returns(df: pd.DataFrame, k: float = 0.5, ma: int | None = None, cost: float = 0.001) -> pd.DataFrame:
    """P1(ma=None)·P2(ma=15). cost = 한쪽 비용(수수료+체결 차이). 반환: 날짜별 r(시가→다음 시가), traded."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    target = o + k * (h.shift(1) - l.shift(1))
    hit = h >= target
    if ma is not None:
        hit &= c.shift(1) > c.shift(2).rolling(ma).mean()  # 어제 종가 > 그 전 15일 종가 평균(어제까지 아는 값)
    nxt = o.shift(-1)
    r = (nxt * (1 - cost)) / (target * (1 + cost)) - 1
    out = pd.DataFrame({"r": r.where(hit, 0.0), "traded": hit})
    return out.iloc[1:-1]  # 첫날(어제 없음)·마지막 날(다음 시가 없음) 제외


def _hold_signal_returns(df: pd.DataFrame, enter: pd.Series, exit_: pd.Series, cost: float) -> pd.DataFrame:
    """종가 신호 → 다음 날 시가부터 보유. 보유 중인 날은 시가→다음 시가 수익, 사고판 날 비용."""
    pos, held = [], False
    for e, x in zip(enter.fillna(False), exit_.fillna(False)):
        if held and x:
            held = False
        elif not held and e:
            held = True
        pos.append(held)
    p = pd.Series(pos, index=df.index).shift(1, fill_value=False)  # 신호 다음 날 시가부터
    o = df["Open"]
    r = (o.shift(-1) / o - 1).where(p, 0.0)
    buy = p & ~p.shift(1, fill_value=False)
    sell = p & ~p.shift(-1, fill_value=False)
    r = (1 + r) * np.where(buy, 1 / (1 + cost), 1.0) * np.where(sell, 1 - cost, 1.0) - 1
    out = pd.DataFrame({"r": r, "traded": buy}, index=df.index)
    return out.iloc[1:-1]


def rsi2_returns(df: pd.DataFrame, cost: float = 0.001) -> pd.DataFrame:
    """P3 코너스 RSI(2): 종가>200일 평균 & RSI2<10 → 사고, 종가>5일 평균 → 판다."""
    c = df["Close"]
    enter = (c > c.rolling(200).mean()) & (rsi(c, 2) < 10)
    return _hold_signal_returns(df, enter, c > c.rolling(5).mean(), cost)


def bbrsi_returns(df: pd.DataFrame, cost: float = 0.001) -> pd.DataFrame:
    """P4 볼린저+RSI: 종가<하단(20,2) & RSI14<30 → 사고, 종가>가운데선 → 판다."""
    c = df["Close"]
    mid = c.rolling(20).mean()
    low = mid - 2 * c.rolling(20).std()
    return _hold_signal_returns(df, (c < low) & (rsi(c, 14) < 30), c > mid, cost)


def hold_returns(df: pd.DataFrame) -> pd.DataFrame:
    o = df["Open"]
    return pd.DataFrame({"r": o.shift(-1) / o - 1, "traded": False}).iloc[1:-1]


def metrics(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    yrs = max((r.index[-1] - r.index[0]).days / 365.0, 1e-9)
    total = float(eq.iloc[-1] - 1)
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0
    vol = float(r.std() * np.sqrt(365))
    mdd = float((eq / eq.cummax() - 1).min())
    return {"총수익": total, "연평균": cagr, "흔들림": vol, "수익/흔들림": cagr / vol if vol > 0 else float("nan"),
            "가장 크게 떨어졌을 때": mdd}
