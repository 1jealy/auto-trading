"""#22 덩이 매수·익절·손절 계산 테스트: 체결 시점, 60일선, 익절·손절, 사라진 코인, 비용."""

import numpy as np
import pandas as pd
import pytest

from autotrade.lots import LotRules, hold_equity, resample_ohlc, simulate

T = pd.date_range("2024-01-01", periods=40, freq="h")


def frames(prices: dict, opens: dict | None = None):
    c = pd.DataFrame(prices, index=T[: len(next(iter(prices.values())))], dtype=float)
    o = pd.DataFrame(opens, index=c.index, dtype=float) if opens else c.shift(1).fillna(c.iloc[0])
    return o, c


R = LotRules(ma_bars=3, target=0.10, stop=None, cost=0.0, lot_krw=100.0, max_lots=2, capital=1000.0)


def test_buys_next_open_only_above_ma_and_respects_max_lots():
    c = [10, 10, 10, 11, 12, 13, 14]  # 3봉째까지 평균 10, 4봉째부터 위
    o, cl = frames({"A": c})
    res = simulate(o, cl, LotRules(3, 0.50, None, 0.0, 100.0, 2, 1000.0))  # 익절이 안 걸리게 높게
    # 종가가 평균 위인 첫 봉(3번)에 정함 → 4번 봉 시가 = 직전 종가 11 에 체결, 그다음 봉도 한 덩이, 최대 2덩이
    assert res.buys == 2 and res.trades.empty
    held = res.open_lots
    assert list(held["buy_price"]) == [11.0, 12.0] and len(held) == 2
    # 3번째 봉에서 사라는 신호 전에는 사지 않는다
    assert (res.equity.iloc[:4] == 1000.0).all()


def test_take_profit_sells_only_that_lot_and_not_below_ma():
    c = [10, 10, 10, 11, 12, 14, 9]
    o, cl = frames({"A": c})
    res = simulate(o, cl, R)
    t = res.trades
    # 11에 산 덩이: 종가 12.1 이상(5번 봉 14)에서 익절 → 다음 봉 시가 14... 아니라 5번 봉 종가 14 에 판단, 6번 봉 시가 14 에 체결
    assert (t["why"] == "익절").sum() >= 1
    first = t[t["why"] == "익절"].iloc[0]
    assert first["buy_price"] == 11.0 and first["sell_price"] == 14.0 and first["ret"] == pytest.approx(14 / 11 - 1)


def test_no_sell_below_cost_without_stop_and_stop_sells_anywhere():
    c = [10, 10, 10, 11, 8, 7, 7, 7]  # 산 뒤 60일선 아래로 계속 하락
    o, cl = frames({"A": c})
    nostop = simulate(o, cl, R)
    assert nostop.trades.empty and len(nostop.open_lots) >= 1  # 손해는 안 판다
    withstop = simulate(o, cl, LotRules(3, 0.10, 0.10, 0.0, 100.0, 2, 1000.0))
    t = withstop.trades
    assert not t.empty and (t["why"] == "손절").all() and (t["ret"] < 0).all()  # 선 아래여도 손절은 작동


def test_costs_and_account_value():
    c = [10, 10, 10, 11, 12, 14, 9, 9]  # 판 뒤에는 60일선 아래로 내려가 다시 사지 않음
    o, cl = frames({"A": c})
    r = LotRules(3, 0.10, None, 0.01, 100.0, 1, 1000.0)
    res = simulate(o, cl, r)
    t = res.trades.iloc[0]
    assert t["ret"] == pytest.approx(14 * 0.99 / (11 * 1.01) - 1)
    assert res.equity.iloc[-1] == pytest.approx(1000 - 100 + 100 * 0.99 / 11 * 14 * 0.99)


def test_delisted_coin_lots_go_to_zero():
    c = [10, 10, 10, 11, 12, np.nan, np.nan, np.nan]
    o, cl = frames({"A": c, "B": [10, 10, 10, 10, 10, 10, 10, 10]})
    res = simulate(o, cl, R)
    t = res.trades
    assert (t["why"] == "상장폐지").all() and (t["sell_price"] == 0).all() and (t["ret"] == -1).all()
    assert res.equity.iloc[-1] == pytest.approx(1000 - 100 * len(t))


def test_hold_equity_equal_split_and_zero_after_delisting():
    c = {"A": [10, 10, 20, 20, 20], "B": [10, 10, 10, 5, np.nan]}
    o, cl = frames(c)
    h = hold_equity(o, cl, T[0], capital=200.0, cost=0.0)
    assert h.iloc[0] == pytest.approx(200.0)
    assert h.iloc[2] == pytest.approx(100 * 20 / 10 + 100)  # A 2배, B 그대로
    assert h.iloc[-1] == pytest.approx(200.0 + 0.0)  # B 사라짐 → 0원, A 2배(200)... 합 = 200


def test_resample_ohlc_uses_first_open_and_last_close():
    df = pd.DataFrame({"open": range(8), "close": range(10, 18)}, index=pd.date_range("2024-01-01", periods=8, freq="h"))
    r = resample_ohlc(df, "4h")
    assert list(r["open"]) == [0, 4] and list(r["close"]) == [13, 17]
