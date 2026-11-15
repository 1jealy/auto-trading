"""#15 공시 스윙 계산 테스트: 사는 날·파는 날, 비용, 상장폐지, 거래대금 거르기, 자리 제한."""

import numpy as np
import pandas as pd
import pytest

from autotrade.data import Prices
from autotrade.event_swing import SwingRules, day_t, drop_best_months, simulate

DATES = pd.bdate_range("2021-01-04", periods=60)
NO_TAX = lambda d: 0.0  # noqa: E731
RULES0 = SwingRules(hold=5, commission=0.0, slippage=0.0, min_value=0.0, value_window=3)


def make(open_: dict, close: dict | None = None, volume: float = 1e9) -> Prices:
    o = pd.DataFrame(open_, index=DATES, dtype=float)
    c = pd.DataFrame(close if close else open_, index=DATES, dtype=float)
    v = pd.DataFrame(volume, index=DATES, columns=o.columns).where(o.notna(), 0.0)
    return Prices(o, c, v)


def sig(code, date, no="1"):
    return pd.DataFrame({"code": [code], "date": [pd.Timestamp(date)], "rcept_no": [no]})


def test_buys_next_day_open_and_sells_after_hold():
    a = np.arange(60) + 100.0
    p = make({"B": 100.0, "A": a})
    r = simulate(sig("A", DATES[10]), p, "B", RULES0, NO_TAX)
    t = r.trades.iloc[0]
    assert t.entry_date == DATES[11] and t.entry_price == a[11]  # 접수 다음 거래일 시가
    assert t.exit_date == DATES[16] and t.exit_price == a[16]  # 5거래일 뒤 시가
    assert t.ret == pytest.approx(a[16] / a[11] - 1)
    assert t.excess == pytest.approx(t.ret)  # 벤치마크는 그대로


def test_signal_on_holiday_buys_next_trading_day():
    p = make({"B": 100.0, "A": 100.0})
    r = simulate(sig("A", pd.Timestamp("2021-01-09")), p, "B", RULES0, NO_TAX)  # 토요일
    assert r.trades.iloc[0].entry_date == pd.Timestamp("2021-01-11")


def test_costs_included():
    p = make({"B": 100.0, "A": 100.0})
    rules = SwingRules(hold=5, commission=0.001, slippage=0.002, min_value=0, value_window=3)
    r = simulate(sig("A", DATES[10]), p, "B", rules, lambda d: 0.003)
    assert r.trades.iloc[0].ret == pytest.approx((1 - 0.006) / (1 + 0.003) - 1)


def test_suspended_exit_sells_on_first_trading_day_after():
    a = pd.Series(100.0, index=DATES)
    a.iloc[16:19] = np.nan
    a.iloc[19] = 120.0
    p = make({"B": 100.0, "A": a.values})
    t = simulate(sig("A", DATES[10]), p, "B", RULES0, NO_TAX).trades.iloc[0]
    assert t.exit_date == DATES[19] and t.exit_price == 120 and t.status == "늦게팖"


def test_delisted_counts_as_total_loss():
    a = pd.Series(100.0, index=DATES)
    a.iloc[14:] = np.nan  # 산 뒤 가격이 끊김
    p = make({"B": 100.0, "A": a.values})
    t = simulate(sig("A", DATES[10]), p, "B", RULES0, NO_TAX).trades.iloc[0]
    assert t.status == "상장폐지" and t.ret == pytest.approx(-1.0)


def test_unfinished_at_data_end_marked():
    p = make({"B": 100.0, "A": 100.0})
    t = simulate(sig("A", DATES[57]), p, "B", RULES0, NO_TAX).trades.iloc[0]
    assert t.status == "미완료"


def test_liquidity_filter_uses_only_days_before_signal():
    vol = pd.DataFrame(1.0, index=DATES, columns=["B", "A"])
    vol.loc[DATES[10]:, "A"] = 1e9  # 접수일부터 거래가 늘어남 → 그 전 3일은 적음
    o = pd.DataFrame(100.0, index=DATES, columns=["B", "A"])
    p = Prices(o, o.copy(), vol)
    rules = SwingRules(hold=5, commission=0, slippage=0, min_value=1e6, value_window=3)
    r = simulate(sig("A", DATES[10]), p, "B", rules, NO_TAX)
    assert len(r.trades) == 0 and r.counts["거래대금부족"] == 1
    r = simulate(sig("A", DATES[13]), p, "B", rules, NO_TAX)
    assert len(r.trades) == 1


def test_duplicate_while_held_and_full_slots():
    p = make({"B": 100.0, "A": 100.0, "C": 100.0})
    s = pd.concat([sig("A", DATES[10], "1"), sig("A", DATES[12], "2"), sig("C", DATES[12], "3")])
    rules = SwingRules(hold=5, max_positions=1, commission=0, slippage=0, min_value=0, value_window=3)
    r = simulate(s, p, "B", rules, NO_TAX)
    assert len(r.trades) == 1 and r.counts["보유중중복"] == 1 and r.counts["자리없음"] == 1


def test_equity_tracks_positions():
    a = np.full(60, 100.0)
    a[11:] = 200.0  # 산 날(시가 200) 이후 그대로
    c = a.copy()
    c[11:] = 220.0  # 산 날 종가 10% 상승
    p = make({"B": 100.0, "A": a}, {"B": 100.0, "A": c})
    r = simulate(sig("A", DATES[10]), p, "B", RULES0, NO_TAX)
    assert r.equity.iloc[11] == pytest.approx(1e7 * (1 + 0.05 * 0.1))


def test_day_t_groups_same_day():
    d1, d2, d3 = DATES[:3]
    t = pd.DataFrame({"entry_date": [d1, d1, d1, d2, d3], "excess": [0.1, 0.1, 0.1, 0.0, 0.05]})
    tt, n = day_t(t)
    assert n == 3
    by_day = pd.Series([0.1, 0.0, 0.05])
    assert tt == pytest.approx(by_day.mean() / (by_day.std() / np.sqrt(3)))


def test_drop_best_months():
    t = pd.DataFrame({
        "entry_date": pd.to_datetime(["2021-01-05", "2021-02-05", "2021-03-05", "2021-04-05", "2021-05-05"]),
        "excess": [0.5, 0.4, 0.3, -0.1, 0.0],
    })
    assert drop_best_months(t) == pytest.approx(-0.05)
