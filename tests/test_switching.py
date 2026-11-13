from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade import Prices
from autotrade.strategies import AssetSwitching


def trend_prices(yearly: dict[str, float], days: int = 30) -> Prices:
    """종목별로 일정하게 오르내리는 가격. yearly 는 기간 전체 수익률."""
    dates = pd.bdate_range("2020-01-01", periods=days)
    close = pd.DataFrame(
        {t: 100 * (1 + r) ** (np.arange(days) / (days - 1)) for t, r in yearly.items()},
        index=dates,
    )
    return Prices(close.copy(), close)


def test_picks_the_strongest_candidate():
    prices = trend_prices({"KR": 0.05, "US": 0.20, "BOND": 0.02, "SAFE": 0.01})
    w = AssetSwitching(["KR", "US", "BOND"], "SAFE", lookback=29).target_weights(prices)
    assert w.to_dict() == {"US": 1.0}


def test_goes_to_safe_when_nothing_beats_it():
    prices = trend_prices({"KR": -0.10, "US": -0.05, "BOND": 0.00, "SAFE": 0.01})
    w = AssetSwitching(["KR", "US", "BOND"], "SAFE", lookback=29).target_weights(prices)
    assert w.to_dict() == {"SAFE": 1.0}


def test_holds_cash_until_enough_history():
    prices = trend_prices({"KR": 0.05, "SAFE": 0.01}, days=10)
    w = AssetSwitching(["KR"], "SAFE", lookback=29).target_weights(prices)
    assert w.empty
