from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade.data import Prices


def make_prices(n_days: int = 300, tickers=("A", "B", "C"), seed: int = 0) -> Prices:
    """네트워크 없이 쓰는 가짜 일봉 데이터 (랜덤워크)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, len(tickers))), axis=0)),
        index=dates,
        columns=list(tickers),
    )
    gap = np.exp(rng.normal(0, 0.005, close.shape))
    open_ = close.shift(1).bfill() * gap
    return Prices(open_, close)


def constant_prices(n_days: int, open_: float, close: float, ticker: str = "A") -> Prices:
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    return Prices(
        pd.DataFrame({ticker: open_}, index=dates, dtype=float),
        pd.DataFrame({ticker: close}, index=dates, dtype=float),
    )
