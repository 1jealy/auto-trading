"""벤치마크용 균등 보유 전략."""

from __future__ import annotations

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


class BuyAndHold(Strategy):
    """거래 가능한 종목을 균등 비중으로 보유한다.

    리밸런싱 주기를 길게 주거나, 종목이 하나(지수 ETF)일 때 벤치마크로 쓴다.
    """

    name = "buy_and_hold"

    def target_weights(self, history: Prices) -> pd.Series:
        available = history.close.iloc[-1].dropna().index
        if len(available) == 0:
            return pd.Series(dtype=float)
        return pd.Series(1.0 / len(available), index=available)
