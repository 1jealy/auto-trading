"""성과 지표."""

from __future__ import annotations

import math

import pandas as pd

TRADING_DAYS = 252


def summarize(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> dict[str, float]:
    """자산 곡선 하나의 성과 요약. 무위험수익률은 0으로 가정."""
    equity = equity.dropna()
    rets = equity.pct_change().dropna()
    years = len(rets) / periods_per_year
    growth = equity.iloc[-1] / equity.iloc[0]
    std = rets.std()
    drawdown = equity / equity.cummax() - 1

    return {
        "total_return": growth - 1,
        "cagr": growth ** (1 / years) - 1 if years > 0 else math.nan,
        "volatility": std * math.sqrt(periods_per_year),
        "sharpe": rets.mean() / std * math.sqrt(periods_per_year) if std > 0 else math.nan,
        "max_drawdown": drawdown.min(),
    }


def compare(curves: dict[str, pd.Series]) -> pd.DataFrame:
    """여러 자산 곡선을 같은 기간으로 맞춰 나란히 비교한다."""
    df = pd.DataFrame(curves).dropna()
    table = pd.DataFrame({name: summarize(df[name]) for name in df.columns}).T
    return table[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
