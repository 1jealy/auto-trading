from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade import CostModel, Prices, run_backtest
from autotrade.strategies import ShortTermRebound

DROP_DAY = 70


def wiggle_prices(drops: dict[str, float], days: int = 90) -> Prices:
    """평소엔 ±0.5%씩 오르내리다가 DROP_DAY 하루에만 크게 떨어지는 가격."""
    wiggle = np.where(np.arange(days) % 2 == 0, 0.005, -0.005)
    cols = {}
    for ticker, drop in drops.items():
        r = wiggle.copy()
        r[0] = 0.0
        r[DROP_DAY] = drop
        cols[ticker] = 100 * np.cumprod(1 + r)
    close = pd.DataFrame(cols, index=pd.bdate_range("2020-01-01", periods=days))
    return Prices(close.copy(), close)


def held_days(result, ticker):
    w = result.decisions[ticker]
    return [result.decisions.index.get_loc(d) for d in w[w > 0].index]


def test_fresh_signal_holds_exactly_hold_days():
    prices = wiggle_prices({"A": -0.10, "B": 0.0})
    strat = ShortTermRebound(["A", "B"], hold_days=3, extend="fresh")
    result = run_backtest(prices, strat, "D", CostModel.zero(), integer_shares=False)

    assert held_days(result, "A") == [DROP_DAY, DROP_DAY + 1, DROP_DAY + 2]
    assert held_days(result, "B") == []
    sides = result.trades.set_index("date")["side"]
    assert sides[prices.dates[DROP_DAY + 1]] == "buy"  # 다음 날 시가 매수
    assert sides[prices.dates[DROP_DAY + 4]] == "sell"  # 3일 보유 후 4일째 시가 매도
    assert strat.episodes == [
        dict(ticker="A", signal_date=prices.dates[DROP_DAY], last_decision=prices.dates[DROP_DAY + 2])
    ]


def test_any_mode_extends_while_drop_stays_in_window():
    # 5일 하락폭 안에 급락일이 남아 있는 동안(5일) 신호가 계속 나서 보유가 연장된다
    prices = wiggle_prices({"A": -0.10})
    strat = ShortTermRebound(["A"], hold_days=3, extend="any")
    result = run_backtest(prices, strat, "D", CostModel.zero(), integer_shares=False)
    assert held_days(result, "A") == list(range(DROP_DAY, DROP_DAY + 4 + 3))


def test_slots_pick_the_biggest_drops():
    drops = {"A": -0.06, "B": -0.10, "C": -0.08, "D": -0.12, "E": -0.07}
    prices = wiggle_prices(drops)
    strat = ShortTermRebound(list(drops), slots=2, extend="fresh")
    result = run_backtest(prices, strat, "D", CostModel.zero(), integer_shares=False)
    first = result.decisions.loc[prices.dates[DROP_DAY]]
    assert set(first[first > 0].index) == {"B", "D"}
    assert first.sum() == 1.0


def test_no_signal_for_normal_wiggles():
    prices = wiggle_prices({"A": 0.0, "B": 0.0})
    result = run_backtest(prices, ShortTermRebound(["A", "B"]), "D")
    assert result.trades.empty
