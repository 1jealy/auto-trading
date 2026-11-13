"""밤에만 들고 있기 (실험 #4, docs/experiments.md 참고).

장 마감 종가에 사서 다음 날 아침 시가에 판다. 엔진(다음 날 시가 체결)과 체결 시점이 달라서
따로 날마다 계산한다.

mode="pure": 다음 날 시가에 무조건 판다.
mode="wait": 시가가 본전 가격보다 낮으면 그날 낮에 본전 가격 지정가로 팔기를 시도하고,
             못 팔면 그다음 날 시가에 무조건 판다.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from autotrade.config import CostModel


def breakeven_price(buy_price: float, costs: CostModel) -> float:
    """팔았을 때 비용까지 빼고 손익이 0이 되는 가격."""
    return buy_price * (1 + costs.buy_rate) / (1 - costs.sell_rate)


def simulate(
    ohlc: pd.DataFrame,
    costs: CostModel,
    mode: Literal["pure", "wait"] = "wait",
    fill_margin: float = 0.0002,
) -> tuple[pd.Series, pd.DataFrame]:
    """(매일 종가 기준 자산, 매매 기록)을 반환한다. 자산은 1.0 에서 시작.

    ohlc: Open, High, Close 열이 있는 일봉 (날짜 오름차순, 결측 없음).
    fill_margin: 낮에 본전 가격 지정가가 팔렸다고 보려면 고가가 본전보다 이만큼 높아야 한다.
    """
    if mode not in ("pure", "wait"):
        raise ValueError("mode 는 'pure' 또는 'wait'")
    data = ohlc[["Open", "High", "Close"]]
    if data.isna().any().any():
        raise ValueError("시가·고가·종가에 빈 값이 있습니다")

    cash, shares = 1.0, 0.0
    buy_px = buy_date = None
    mornings = 0  # 산 뒤 맞은 아침 수
    equity, trades = [], []
    dates = data.index

    for i, (date, o, h, c) in enumerate(data.itertuples()):
        if shares > 0:
            mornings += 1
            target = breakeven_price(buy_px, costs)
            sell_px = kind = None
            if mode == "pure" or o >= target:
                sell_px, kind = o, "아침 매도"
            elif mornings >= 2:
                sell_px, kind = o, "다음날 강제 매도"
            elif h >= target * (1 + fill_margin):
                sell_px, kind = target, "낮에 본전 매도"
            if sell_px is not None:
                cash = shares * sell_px * (1 - costs.sell_rate)
                trades.append(dict(buy_date=buy_date, sell_date=date, kind=kind, buy_px=buy_px,
                                   sell_px=sell_px,
                                   ret=sell_px * (1 - costs.sell_rate) / (buy_px * (1 + costs.buy_rate)) - 1))
                shares = 0.0

        if shares == 0 and i < len(dates) - 1:  # 마지막 날은 다음 날이 없으므로 사지 않음
            shares = cash / (c * (1 + costs.buy_rate))
            cash, buy_px, buy_date, mornings = 0.0, c, date, 0

        equity.append(cash + shares * c)

    cols = ["buy_date", "sell_date", "kind", "buy_px", "sell_px", "ret"]
    return pd.Series(equity, index=dates, name=mode), pd.DataFrame(trades, columns=cols)


def night_day_returns(ohlc: pd.DataFrame) -> pd.DataFrame:
    """날마다 밤 동안(어제 종가→오늘 시가), 낮 동안(오늘 시가→오늘 종가) 수익률. 비용 없음."""
    return pd.DataFrame({
        "night": ohlc["Open"] / ohlc["Close"].shift(1) - 1,
        "day": ohlc["Close"] / ohlc["Open"] - 1,
    }).iloc[1:]


def annualized(returns: pd.Series, periods: int = 252) -> float:
    """날마다의 수익률을 이어 붙였을 때 연평균 수익률."""
    growth = float((1 + returns.dropna()).prod())
    n = returns.dropna().size
    return growth ** (periods / n) - 1 if n else float("nan")
