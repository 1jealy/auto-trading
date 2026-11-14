"""60일 평균 위에서만 보유 (실험 #9, docs/experiments.md 참고).

돈을 종목 수만큼 똑같이 나눠, 칸마다 종가가 최근 N일 종가 평균(오늘 포함)보다 높으면 그 종목을,
아니면 피난처를 보유한다. 피난처가 없으면(None) 그 칸은 현금.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


class TrendFilter(Strategy):
    name = "trend_filter"

    def __init__(self, assets: Sequence[str], safe: str | None, window: int = 60) -> None:
        if safe is not None and safe in assets:
            raise ValueError("피난처는 대상 목록에 넣지 않습니다")
        if window < 1:
            raise ValueError("window 는 1 이상이어야 합니다")
        if not assets:
            raise ValueError("대상이 하나 이상 있어야 합니다")
        self.assets = list(assets)
        self.safe = safe
        self.window = window

    def is_above(self, close: pd.Series) -> bool:
        """종가가 최근 window 일 평균보다 높은가. 데이터가 모자라면 False."""
        close = close.dropna()
        if len(close) < self.window:
            return False
        return bool(close.iloc[-1] > close.iloc[-self.window:].mean())

    def target_weights(self, history: Prices) -> pd.Series:
        share = 1.0 / len(self.assets)
        weights: dict[str, float] = {}
        for asset in self.assets:
            hold = asset if self.is_above(history.close[asset]) else self.safe
            if hold is not None:
                weights[hold] = weights.get(hold, 0.0) + share
        return pd.Series(weights, dtype=float)
