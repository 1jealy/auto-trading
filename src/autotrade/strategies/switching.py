"""강한 자산 갈아타기 (실험 #1, docs/experiments.md 참고).

후보 중 최근 수익률 1위를 전액 보유한다. 1위도 피난처보다 못하면 피난처를 보유한다.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


class AssetSwitching(Strategy):
    name = "asset_switching"

    def __init__(self, candidates: Sequence[str], safe: str, lookback: int = 252) -> None:
        if safe in candidates:
            raise ValueError("피난처는 후보 목록에 넣지 않습니다")
        if lookback < 1:
            raise ValueError("lookback 은 1 이상이어야 합니다")
        self.candidates = list(candidates)
        self.safe = safe
        self.lookback = lookback

    def target_weights(self, history: Prices) -> pd.Series:
        close = history.close[self.candidates + [self.safe]]
        if len(close) <= self.lookback:
            return pd.Series(dtype=float)  # 계산할 데이터가 부족 → 현금

        past_return = close.iloc[-1] / close.iloc[-1 - self.lookback] - 1
        safe_return = past_return[self.safe]
        scores = past_return[self.candidates].dropna()

        if pd.isna(safe_return):
            return pd.Series(dtype=float)
        if scores.empty or scores.max() < safe_return:
            return pd.Series({self.safe: 1.0})
        return pd.Series({scores.idxmax(): 1.0})
