"""#20 4시간봉 돌파 계산 테스트: 새 돌파, 다음 봉 시가 체결, 청산 두 가지, 비용, 펀딩비, 숏."""

import numpy as np
import pandas as pd
import pytest

from autotrade.breakout4h import BreakoutRules, drop_best_months_total, max_drawdown, run, signals

T = pd.date_range("2020-01-01", periods=80, freq="4h")
R = BreakoutRules(entry=3, exit=2, max_bars=5, cost=0.0)
NOFUND = pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def bars_from_close(close, spread=0.5):
    c = pd.Series(close, index=T[: len(close)], dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c})


def test_new_breakout_only():
    c = [10, 10, 10, 10, 12, 13, 14, 15]
    s = signals(bars_from_close(c), R)
    assert s["long"].tolist() == [False] * 4 + [True, False, False, False]  # 계속 위에 있어도 다시 신호 아님


def test_enters_next_bar_open_and_exits_on_time():
    c = [10] * 5 + [12] + [13, 14, 15, 16, 17, 18, 19, 20]
    b = bars_from_close(c)
    res = run(b, NOFUND, R, T[0], T[len(c) - 1])
    t = res.trades.iloc[0]
    assert t.side == 1 and t.entry_time == T[6] and t.entry_price == b["open"].iloc[6]  # 신호 봉(5) 다음 봉 시가
    assert t.status == "24봉" and t.bars == 5 and t.exit_time == T[11]  # 5봉 보유 뒤 다음 봉 시가
    assert t.ret == pytest.approx(b["open"].iloc[11] / b["open"].iloc[6] - 1)


def test_exit_on_opposite_channel():
    c = [10] * 5 + [12, 13, 11, 10, 10, 10, 10]
    b = bars_from_close(c, spread=0.1)
    t = run(b, NOFUND, R, T[0], T[len(c) - 1]).trades.iloc[0]
    # 7번 봉 종가 11 은 직전 2봉(5·6번) 최저가 9.9 위 → 그대로.
    # 8번 봉 종가 10 < 직전 2봉(6·7번) 최저가 10.9 → 9번 봉 시가에 청산
    assert t.status == "반대 10봉" and t.exit_time == T[9]


def test_short_and_costs():
    c = [10] * 5 + [8, 7, 6, 5, 4, 3, 2, 2, 2, 2]
    b = bars_from_close(c)
    r = BreakoutRules(entry=3, exit=2, max_bars=5, cost=0.001)
    t = run(b, NOFUND, r, T[0], T[len(c) - 1]).trades.iloc[0]
    e, x = b["open"].iloc[6], b["open"].iloc[11]
    assert t.side == -1
    units = 1 / e
    assert t.ret == pytest.approx(-0.001 + units * (e - x) - units * x * 0.001)


def test_funding_long_pays_short_receives():
    c = [10] * 5 + [12] + [12] * 10
    b = bars_from_close(c, spread=0.0)
    b.loc[T[6]:, ["open", "high", "low", "close"]] = 12.0
    fund = pd.Series([0.01, 0.01, 0.01], index=[T[6], T[8], T[12]])  # 들어간 시각(6)은 안 냄, 8은 냄, 12는 나간 뒤
    t = run(b, fund, R, T[0], T[len(c) - 1]).trades.iloc[0]
    assert t.exit_time == T[11]
    assert t.funding == pytest.approx(-0.01) and t.ret == pytest.approx(-0.01)


def test_no_entry_before_start_and_closes_at_end():
    c = [10] * 5 + [12, 13, 14]
    b = bars_from_close(c)
    assert len(run(b, NOFUND, R, T[6], T[7]).trades) == 0  # 신호 봉이 시작 전
    t = run(b, NOFUND, R, T[0], T[7]).trades.iloc[0]
    assert t.status == "기간끝" and t.exit_price == 14


def test_equity_marked_at_close():
    c = [10] * 5 + [12, 13, 15]
    b = bars_from_close(c)
    res = run(b, NOFUND, R, T[0], T[7])
    assert res.equity.loc[T[6]] == pytest.approx(13 / 12)  # 6번 봉 시가 12에 사서 종가 13


def test_long_only_ignores_short():
    c = [10] * 5 + [8, 7, 6, 5, 4]
    r = BreakoutRules(entry=3, exit=2, max_bars=5, cost=0.0, sides=(1,))
    assert len(run(bars_from_close(c), NOFUND, r, T[0], T[9]).trades) == 0


def test_helpers():
    assert max_drawdown(pd.Series([1, 2, 1, 3])) == pytest.approx(-0.5)
    t = pd.DataFrame({"entry_time": pd.to_datetime(["2020-01-05", "2020-02-05", "2020-03-05", "2020-04-05"]),
                      "ret": [0.5, 0.4, 0.3, -0.1]})
    assert drop_best_months_total(t) == pytest.approx(-0.1)
