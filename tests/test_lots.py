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


# ── #23 ──
from autotrade.lots import AdaptiveRules, daily_unit, simulate_adaptive  # noqa: E402


def adaptive(c, rules, u=0.05, opens=None):
    o, cl = frames({"A": c}, opens)
    unit = pd.DataFrame({"A": [u] * len(c)}, index=cl.index)
    return simulate_adaptive(o, cl, unit, rules)


def test_daily_unit_uses_only_completed_days():
    idx = pd.date_range("2024-01-01", periods=24 * 25, freq="4h")
    c = pd.DataFrame({"A": np.where(np.arange(len(idx)) // 6 % 2 == 0, 100.0, 110.0)}, index=idx)  # 하루마다 ±10%
    u = daily_unit(c, days=3)
    assert u["A"].iloc[:6 * 4].isna().all()  # 3일치 변화율이 모이기 전엔 없음
    first = u["A"].dropna()
    assert first.index[0] == pd.Timestamp("2024-01-05")  # 1~4일 완결 → 5일 첫 봉부터
    assert first.iloc[0] == pytest.approx((0.10 + 1 / 11 + 0.10) / 3, abs=1e-6)  # 1~3일째 변화율 +10%, −9.09%, +10%의 평균


def test_unit_stop_and_target_use_unit_at_buy():
    c = [10, 10, 10, 11, 11, 10.4, 10.3, 10.3]  # 11에 사고 U=5% → 손절가 11 x 0.95 = 10.45
    res = adaptive(c, AdaptiveRules(3, stop_mult=1.0, target_mult=3.0, cost=0.0, lot_krw=100.0, max_lots=1, capital=1000.0))
    t = res.trades
    assert len(t) == 1 and t.iloc[0]["why"] == "손절" and t.iloc[0]["buy_price"] == 11.0
    assert t.iloc[0]["sell_price"] == 10.3 or t.iloc[0]["sell_price"] == 10.4  # 손절 판단 봉의 다음 봉 시가


def test_trailing_stop_follows_the_peak_and_has_no_target():
    c = [10, 10, 10, 11, 12, 14, 16, 15.1, 14.8, 14.8]  # 16까지 오른 뒤 내려옴: 추적 폭 1.0 x 5% → 16 x 0.95 = 15.2
    res = adaptive(c, AdaptiveRules(3, stop_mult=1.0, trail_mult=1.0, cost=0.0, lot_krw=100.0, max_lots=1, capital=1000.0))
    t = res.trades
    assert len(t) == 1 and t.iloc[0]["why"] == "추적 손절"
    assert t.iloc[0]["sell_price"] == 15.1  # 종가 15.1 ≤ 15.2 → 다음 봉 시가(= 그 봉 종가 15.1)에 팖
    assert t.iloc[0]["ret"] > 0.3  # 오르는 동안 계속 들고 있었다(익절 없음)


def test_trend_exit_sells_below_exit_ma_even_below_entry_ma():
    c = [10, 10, 10, 11, 12, 13, 12.9, 11.5, 11.4, 11.4]
    res = adaptive(c, AdaptiveRules(3, stop_mult=5.0, exit_ma_bars=2, cost=0.0, lot_krw=100.0, max_lots=1, capital=1000.0), u=0.05)
    assert (res.trades["why"] == "추세 이탈").all() and len(res.trades) >= 1


def test_adaptive_waits_until_unit_is_known_and_target_only_above_ma():
    c = [10, 10, 10, 11, 12, 13, 14, 15]
    o, cl = frames({"A": c})
    unit = pd.DataFrame({"A": [np.nan] * 5 + [0.05] * 3}, index=cl.index)
    res = simulate_adaptive(o, cl, unit, AdaptiveRules(3, 1.0, 3.0, cost=0.0, lot_krw=100.0, max_lots=1, capital=1000.0))
    assert res.buys == 1 and res.open_lots["buy_t"].iloc[0] > T[5]  # U 가 생긴 뒤에야 산다
