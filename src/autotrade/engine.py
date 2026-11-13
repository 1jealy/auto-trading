"""일봉 백테스트 엔진.

타이밍 규칙:
  1. 리밸런싱 날 D 장 마감 후, 전략은 D 까지의 데이터(history)만 보고 목표 비중을 정한다.
  2. 주문은 다음 거래일 D+1 **시가**에 체결된다. 매도를 먼저 하고 그 현금으로 매수한다.
  3. 매일 종가로 평가해 자산(equity)을 기록한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from autotrade.config import CostModel
from autotrade.data import Prices
from autotrade.strategies.base import Strategy

Rebalance = Literal["D", "W", "M", "Q"]
_EPS = 1e-9


@dataclass
class BacktestResult:
    equity: pd.Series  # 일별 종가 기준 총자산
    trades: pd.DataFrame  # date, ticker, side, qty, price, amount, cost
    decisions: pd.DataFrame  # 리밸런싱 결정일별 목표 비중 (행=결정일)
    total_cost: float  # 지불한 수수료·세금·슬리피지 합계

    @property
    def turnover(self) -> float:
        """연평균 회전율 (거래대금 / 평균 자산)."""
        if self.trades.empty:
            return 0.0
        years = len(self.equity) / 252
        return float(self.trades["amount"].sum() / self.equity.mean() / max(years, _EPS))


def rebalance_dates(dates: pd.DatetimeIndex, freq: Rebalance) -> set[pd.Timestamp]:
    """각 기간(일/주/월/분기)의 마지막 거래일."""
    if freq == "D":
        return set(dates)
    if freq not in ("W", "M", "Q"):
        raise ValueError(f"지원하지 않는 리밸런싱 주기: {freq}")
    s = pd.Series(dates, index=dates)
    return set(s.groupby(dates.to_period(freq)).max())


def run_backtest(
    prices: Prices,
    strategy: Strategy,
    rebalance: Rebalance = "M",
    costs: CostModel = CostModel(),
    initial_cash: float = 10_000_000,
    integer_shares: bool = True,
    min_trade_frac: float = 0.0,
) -> BacktestResult:
    """min_trade_frac: 목표와의 차이가 전체 자산의 이 비율보다 작으면 거래하지 않는다.
    (전량 매도는 항상 실행) 들고 있는 종목을 매일 조금씩 다시 맞추는 비용을 막는 용도.
    """
    if not 0 <= min_trade_frac < 1:
        raise ValueError("min_trade_frac 은 0 이상 1 미만이어야 합니다")
    dates = prices.dates
    if len(dates) < 2:
        raise ValueError("백테스트에는 최소 2거래일이 필요합니다")

    tickers = prices.tickers
    decision_days = rebalance_dates(dates, rebalance)
    close_ffill = prices.close.ffill()  # 거래정지 종목은 마지막 종가로 평가

    cash = float(initial_cash)
    shares = pd.Series(0.0, index=tickers)
    pending: pd.Series | None = None

    equity: list[float] = []
    trades: list[dict] = []
    decisions: dict[pd.Timestamp, pd.Series] = {}
    total_cost = 0.0

    for i, date in enumerate(dates):
        # 1) 전날 결정한 주문을 오늘 시가에 체결
        if pending is not None:
            cash, day_trades, day_cost = _execute(
                target=pending,
                date=date,
                open_px=prices.open.loc[date],
                ref_px=close_ffill.iloc[i - 1],
                cash=cash,
                shares=shares,
                costs=costs,
                integer_shares=integer_shares,
                min_trade_frac=min_trade_frac,
            )
            trades.extend(day_trades)
            total_cost += day_cost
            pending = None

        # 2) 종가 평가
        held = shares[shares.abs() > _EPS]
        value = cash + float((held * close_ffill.loc[date, held.index]).sum())
        equity.append(value)

        # 3) 장 마감 후 결정 (마지막 날은 체결할 다음 날이 없으므로 생략)
        if date in decision_days and i < len(dates) - 1:
            weights = _validate_weights(strategy.target_weights(prices.until(date)), tickers)
            decisions[date] = weights
            pending = weights

    trade_cols = ["date", "ticker", "side", "qty", "price", "amount", "cost"]
    return BacktestResult(
        equity=pd.Series(equity, index=dates, name=strategy.name),
        trades=pd.DataFrame(trades, columns=trade_cols),
        decisions=pd.DataFrame(decisions).T.reindex(columns=tickers).fillna(0.0),
        total_cost=total_cost,
    )


def _validate_weights(weights: pd.Series, tickers: list[str]) -> pd.Series:
    weights = pd.Series(weights, dtype=float).dropna()
    weights = weights[weights.abs() > _EPS]
    unknown = set(weights.index) - set(tickers)
    if unknown:
        raise ValueError(f"데이터에 없는 종목: {sorted(unknown)}")
    if (weights < 0).any():
        raise ValueError("음수 비중(공매도)은 지원하지 않습니다")
    if weights.sum() > 1 + 1e-6:
        raise ValueError(f"비중 합계가 1을 넘습니다: {weights.sum():.4f}")
    return weights


def _execute(
    target: pd.Series,
    date: pd.Timestamp,
    open_px: pd.Series,
    ref_px: pd.Series,
    cash: float,
    shares: pd.Series,
    costs: CostModel,
    integer_shares: bool,
    min_trade_frac: float = 0.0,
) -> tuple[float, list[dict], float]:
    """목표 비중으로 리밸런싱. shares 는 제자리에서 갱신하고 (현금, 거래내역, 비용)을 반환."""
    tradable = open_px.notna() & (open_px > 0)
    px = open_px.where(tradable, ref_px)  # 거래정지 종목은 직전 종가로 평가만

    held_value = (shares * px.fillna(0.0)).sum()
    port_value = cash + float(held_value)
    target_value = target.reindex(shares.index).fillna(0.0) * port_value
    diff = target_value - shares * px.fillna(0.0)
    if min_trade_frac > 0:
        small = diff.abs() < min_trade_frac * port_value
        full_exit = (target_value <= _EPS) & (shares > _EPS)
        diff = diff.where(~small | full_exit, 0.0)

    trades: list[dict] = []
    total_cost = 0.0

    # 매도 먼저
    for ticker in diff[diff < -_EPS].index:
        if not tradable[ticker] or shares[ticker] <= _EPS:
            continue
        price = float(px[ticker])
        if target_value[ticker] <= _EPS:
            qty = float(shares[ticker])  # 전량 매도
        else:
            qty = -diff[ticker] / price
            qty = math.floor(qty + _EPS) if integer_shares else qty
            qty = min(qty, float(shares[ticker]))
        if qty <= _EPS:
            continue
        amount = qty * price
        cost = amount * costs.sell_rate
        cash += amount - cost
        shares[ticker] -= qty
        total_cost += cost
        trades.append(dict(date=date, ticker=ticker, side="sell", qty=qty, price=price, amount=amount, cost=cost))

    # 매수 (부족분이 큰 종목부터)
    for ticker in diff[diff > _EPS].sort_values(ascending=False).index:
        if not tradable[ticker]:
            continue
        price = float(px[ticker])
        budget = min(float(diff[ticker]), cash)
        qty = budget / (price * (1 + costs.buy_rate))
        qty = math.floor(qty + _EPS) if integer_shares else qty
        if qty <= _EPS:
            continue
        amount = qty * price
        cost = amount * costs.buy_rate
        cash -= amount + cost
        shares[ticker] += qty
        total_cost += cost
        trades.append(dict(date=date, ticker=ticker, side="buy", qty=qty, price=price, amount=amount, cost=cost))

    return cash, trades, total_cost
