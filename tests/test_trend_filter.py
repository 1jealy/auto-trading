from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade import CostModel, Prices, run_backtest
from autotrade.strategies import TrendFilter


def make_prices(series: dict[str, list[float]]) -> Prices:
    n = len(next(iter(series.values())))
    close = pd.DataFrame(series, index=pd.bdate_range("2020-01-01", periods=n), dtype=float)
    return Prices(close.copy(), close)


def test_each_slot_holds_asset_above_average_else_safe():
    up = list(np.linspace(100, 120, 10))
    down = list(np.linspace(120, 100, 10))
    p = make_prices({"UP": up, "DOWN": down, "SAFE": [100.0] * 10})
    w = TrendFilter(["UP", "DOWN"], "SAFE", window=5).target_weights(p)
    assert w.to_dict() == {"UP": 0.5, "SAFE": 0.5}


def test_average_includes_today_and_equal_is_not_above():
    flat = [100.0] * 5
    p = make_prices({"A": flat, "SAFE": flat})
    assert TrendFilter(["A"], "SAFE", window=5).target_weights(p).to_dict() == {"SAFE": 1.0}
    # 오늘 종가가 평균에 들어가므로 마지막 날만 오르면 평균보다 위
    p2 = make_prices({"A": flat[:-1] + [101.0], "SAFE": flat})
    assert TrendFilter(["A"], "SAFE", window=5).target_weights(p2).to_dict() == {"A": 1.0}


def test_not_enough_history_goes_safe_or_cash():
    p = make_prices({"A": [1.0, 2.0, 3.0], "SAFE": [1.0] * 3})
    assert TrendFilter(["A"], "SAFE", window=5).target_weights(p).to_dict() == {"SAFE": 1.0}
    assert TrendFilter(["A"], None, window=5).target_weights(p).empty


def test_uses_only_history_passed_in():
    # 결정일 이후 급락이 있어도, 결정일까지만 보면 위 → 보유
    up = list(np.linspace(100, 120, 8)) + [50.0, 40.0]
    p = make_prices({"A": up, "SAFE": [100.0] * 10})
    strat = TrendFilter(["A"], "SAFE", window=5)
    assert strat.target_weights(p.until(p.dates[7])).to_dict() == {"A": 1.0}


def test_in_engine_switches_next_day_at_open():
    close = [100.0] * 6 + [110.0, 120.0, 90.0, 80.0, 80.0]
    p = make_prices({"A": close, "SAFE": [100.0] * 11})
    res = run_backtest(p, TrendFilter(["A"], "SAFE", window=3), "D", CostModel.zero(),
                       integer_shares=False, min_trade_frac=0.03)
    buys = res.trades[(res.trades.ticker == "A") & (res.trades.side == "buy")]
    sells = res.trades[(res.trades.ticker == "A") & (res.trades.side == "sell")]
    assert buys.date.iloc[0] == p.dates[7]   # 6번째(0부터) 날 110 > 평균 → 다음 날 시가
    assert sells.date.iloc[0] == p.dates[9]  # 8번째 날 90 < 평균 → 다음 날 시가


def test_rejects_safe_inside_assets():
    with pytest.raises(ValueError):
        TrendFilter(["A", "SAFE"], "SAFE")
