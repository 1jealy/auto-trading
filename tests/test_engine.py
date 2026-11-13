"""엔진 정확성 테스트. 엔진을 고칠 때마다 반드시 통과해야 한다."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import constant_prices, make_prices

from autotrade import CostModel, Prices, run_backtest
from autotrade.strategies import BuyAndHold, Momentum, Strategy


class Scheduled(Strategy):
    """날짜 순번별로 정해진 비중을 내는 테스트용 전략."""

    name = "scheduled"

    def __init__(self, schedule: dict[int, dict[str, float]]):
        self.schedule = schedule
        self.calls = 0
        self.last = pd.Series(dtype=float)

    def target_weights(self, history: Prices) -> pd.Series:
        day = len(history.dates) - 1
        if day in self.schedule:
            self.last = pd.Series(self.schedule[day], dtype=float)
        self.calls += 1
        return self.last


class Recorder(Strategy):
    name = "recorder"

    def __init__(self):
        self.seen: list[pd.Timestamp] = []

    def target_weights(self, history: Prices) -> pd.Series:
        self.seen.append(history.dates[-1])
        return pd.Series(dtype=float)


# ── 미래 참조 방지 ──────────────────────────────────────────────


def test_strategy_only_sees_data_up_to_decision_day():
    prices = make_prices(120)
    rec = Recorder()
    result = run_backtest(prices, rec, rebalance="W")
    assert rec.seen == list(result.decisions.index)
    for seen in rec.seen:
        assert seen in prices.dates


def test_changing_future_prices_does_not_change_past_equity():
    prices = make_prices(400, tickers=("A", "B", "C", "D", "E"))
    cut = 250
    shock = np.ones(prices.close.shape)
    shock[cut + 1 :] = np.random.default_rng(1).uniform(0.3, 3.0, shock[cut + 1 :].shape)
    future_changed = Prices(prices.open * shock, prices.close * shock)

    strat = Momentum(lookback=60, skip=5, top_n=2)
    a = run_backtest(prices, strat, rebalance="W")
    b = run_backtest(future_changed, strat, rebalance="W")

    pd.testing.assert_series_equal(a.equity.iloc[: cut + 1], b.equity.iloc[: cut + 1])
    assert not a.equity.iloc[cut + 1 :].equals(b.equity.iloc[cut + 1 :])


def test_orders_fill_at_next_day_open_not_same_day_close():
    dates = pd.bdate_range("2020-01-01", periods=3)
    prices = Prices(
        pd.DataFrame({"A": [100.0, 110.0, 120.0]}, index=dates),
        pd.DataFrame({"A": [105.0, 115.0, 125.0]}, index=dates),
    )
    result = run_backtest(prices, Scheduled({0: {"A": 1.0}}), rebalance="D", costs=CostModel.zero(),
                          initial_cash=1_100, integer_shares=False)
    first = result.trades.iloc[0]
    assert first["date"] == dates[1]
    assert first["price"] == 110.0  # 다음 날 시가
    assert result.equity.iloc[0] == 1_100  # 결정일 당일은 아직 현금


# ── 비용 반영 ────────────────────────────────────────────────


def test_round_trip_cost_matches_cost_model():
    costs = CostModel(commission=0.001, sell_tax=0.002, slippage=0.0005)
    prices = constant_prices(4, open_=100.0, close=100.0)
    strat = Scheduled({0: {"A": 1.0}, 1: {}})
    result = run_backtest(prices, strat, rebalance="D", costs=costs,
                          initial_cash=1_000_000, integer_shares=False)

    expected = 1_000_000 / (1 + costs.buy_rate) * (1 - costs.sell_rate)
    assert result.equity.iloc[-1] == pytest.approx(expected)
    assert result.total_cost == pytest.approx(1_000_000 - expected)


def test_zero_cost_buy_and_hold_tracks_price():
    prices = make_prices(200, tickers=("A",))
    result = run_backtest(prices, BuyAndHold(), rebalance="Q", costs=CostModel.zero(),
                          integer_shares=False)
    fill_date = result.trades["date"].iloc[0]  # 첫 분기 말 다음 거래일
    assert (result.trades["side"] == "buy").all() and len(result.trades) == 1
    assert (result.equity.loc[: fill_date - pd.Timedelta(days=1)] == 10_000_000).all()
    expected = 10_000_000 / prices.open.loc[fill_date, "A"] * prices.close["A"].loc[fill_date:]
    pd.testing.assert_series_equal(result.equity.loc[fill_date:], expected, check_names=False)


def test_costs_always_reduce_equity():
    prices = make_prices(300)
    strat = Momentum(lookback=40, skip=0, top_n=1)
    free = run_backtest(prices, strat, rebalance="W", costs=CostModel.zero())
    paid = run_backtest(prices, strat, rebalance="W", costs=CostModel())
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    assert paid.total_cost > 0


# ── 기타 동작 ────────────────────────────────────────────────


def test_integer_shares_never_overspend():
    prices = make_prices(150, tickers=("A", "B"))
    result = run_backtest(prices, BuyAndHold(), rebalance="W", initial_cash=1_000)
    assert (result.trades["qty"] == result.trades["qty"].round()).all()
    assert (result.equity > 0).all()


def test_halted_ticker_is_not_traded():
    prices = make_prices(10, tickers=("A", "B"))
    open_ = prices.open.copy()
    open_.iloc[1, open_.columns.get_loc("B")] = np.nan  # 결정 다음 날 B 거래정지
    halted = Prices(open_, prices.close)
    result = run_backtest(halted, Scheduled({0: {"A": 0.5, "B": 0.5}}), rebalance="D",
                          integer_shares=False)
    day1 = result.trades[result.trades["date"] == prices.dates[1]]
    assert set(day1["ticker"]) == {"A"}


def test_small_drift_is_not_traded_but_full_exit_is():
    dates = pd.bdate_range("2020-01-01", periods=5)
    close = pd.DataFrame({"A": [100.0, 100, 103, 103, 103], "B": [100.0] * 5}, index=dates)
    prices = Prices(close.copy(), close)
    # 0일: A·B 50%씩 → 2일: 같은 비중 유지(A가 3% 올라 약간 어긋남) → 3일: B 전량 매도
    strat = Scheduled({0: {"A": 0.5, "B": 0.5}, 3: {"A": 0.5}})
    result = run_backtest(prices, strat, rebalance="D", costs=CostModel.zero(),
                          integer_shares=False, min_trade_frac=0.05)
    days = result.trades.groupby("date")["ticker"].apply(list)
    assert list(days.index) == [dates[1], dates[4]]  # 2·3일엔 작은 차이라 거래 없음
    assert days[dates[4]] == ["B"]
    assert result.trades.iloc[-1]["side"] == "sell"


def test_delisted_holding_is_sold_at_last_price():
    dates = pd.bdate_range("2020-01-01", periods=6)
    close = pd.DataFrame({"A": [100.0, 100, 80, 40, np.nan, np.nan]}, index=dates)
    prices = Prices(close.copy(), close)
    costs = CostModel(commission=0.0, sell_tax=0.01, slippage=0.0)
    result = run_backtest(prices, Scheduled({0: {"A": 1.0}}), rebalance="D", costs=costs,
                          initial_cash=1_000, integer_shares=False, delisted={"A": dates[4]})
    last = result.trades.iloc[-1]
    assert (last["side"], last["date"], last["price"]) == ("delist", dates[4], 40.0)
    assert result.equity.iloc[-1] == pytest.approx(1_000 / 100 * 40 * 0.99)


@pytest.mark.parametrize(
    "weights, message",
    [({"A": 0.7, "B": 0.6}, "1을 넘습니다"), ({"A": -0.1}, "음수"), ({"Z": 0.5}, "없는 종목")],
)
def test_invalid_weights_rejected(weights, message):
    prices = make_prices(10, tickers=("A", "B"))
    with pytest.raises(ValueError, match=message):
        run_backtest(prices, Scheduled({0: weights}), rebalance="D")
