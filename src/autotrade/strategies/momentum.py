"""예시 전략: 상대 모멘텀.

구조를 보여주기 위한 예시이며, 수익을 낸다는 근거로 넣은 것이 아니다.

가설: 최근 1년간 상대적으로 강했던 종목이 다음 달에도 상대적으로 강하다(행동 편향 — 정보에
대한 과소반응). 최근 1개월은 단기 반전 효과가 있어 제외한다.
"""

from __future__ import annotations

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


class Momentum(Strategy):
    name = "momentum"

    def __init__(self, lookback: int = 252, skip: int = 21, top_n: int = 5) -> None:
        if not 0 <= skip < lookback:
            raise ValueError("0 <= skip < lookback 이어야 합니다")
        if top_n < 1:
            raise ValueError("top_n 은 1 이상이어야 합니다")
        self.lookback = lookback
        self.skip = skip
        self.top_n = top_n

    def target_weights(self, history: Prices) -> pd.Series:
        close = history.close
        if len(close) <= self.lookback:
            return pd.Series(dtype=float)  # 데이터 부족 → 현금

        end_px = close.iloc[-1 - self.skip]
        start_px = close.iloc[-1 - self.lookback]
        today_px = close.iloc[-1]
        score = (end_px / start_px - 1).dropna()
        score = score[today_px.reindex(score.index).notna()]  # 오늘 거래된 종목만
        if score.empty:
            return pd.Series(dtype=float)

        winners = score.nlargest(self.top_n).index
        return pd.Series(1.0 / len(winners), index=winners)
