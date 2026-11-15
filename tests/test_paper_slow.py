"""느린 모의 전략(#12·#13·#14) 테스트 — 네트워크 없음."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from autotrade import CostModel, Prices, run_backtest
from autotrade.paper import slow
from autotrade.paper.run import completed, next_times


def prices_from(close: pd.DataFrame) -> Prices:
    return Prices(close.shift(1).bfill(), close)


def wiggle(n, vol, drift=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))


# ── #12 ──
def etf_frame(n=200, stock_drift=0.002, bond_drift=0.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"S1": wiggle(n, 0.02, stock_drift, 1), "S2": wiggle(n, 0.01, stock_drift, 2),
                         "B": wiggle(n, 0.004, bond_drift, 3), "SAFE": np.linspace(100, 101, n)}, index=idx)


def test_inverse_vol_weights_are_proportional_to_one_over_vol():
    p = prices_from(etf_frame())
    strat = slow.InverseVolTrend(["S1", "S2", "B"], ["S1", "S2"], "SAFE")
    rows = {r["ticker"]: r for r in strat.table(p)}
    w = strat.target_weights(p)
    assert w.sum() == pytest.approx(1.0)
    assert rows["B"]["base"] / rows["S1"]["base"] == pytest.approx(rows["S1"]["vol"] / rows["B"]["vol"])
    expected = p.close["S1"].pct_change().iloc[-60:].std()
    assert rows["S1"]["vol"] == pytest.approx(expected)


def test_only_stocks_move_to_safe_below_average():
    p = prices_from(etf_frame(stock_drift=-0.004, bond_drift=-0.002))  # 다 내려가는 중
    strat = slow.InverseVolTrend(["S1", "S2", "B"], ["S1", "S2"], "SAFE")
    rows = {r["ticker"]: r for r in strat.table(p)}
    assert not rows["S1"]["above"] and not rows["B"]["above"]
    w = strat.target_weights(p)
    assert "S1" not in w and "S2" not in w
    assert w["B"] == pytest.approx(rows["B"]["base"])  # 채권은 평균 아래여도 그대로
    assert w["SAFE"] == pytest.approx(rows["S1"]["base"] + rows["S2"]["base"])


def test_short_history_goes_all_safe():
    p = prices_from(etf_frame(n=100))
    w = slow.InverseVolTrend(["S1", "S2", "B"], ["S1", "S2"], "SAFE").target_weights(p)
    assert w.to_dict() == {"SAFE": 1.0}


# ── #14 ──
def coin_frame(drifts, n=40):
    idx = pd.date_range("2026-01-01", periods=n)
    return pd.DataFrame({f"C{i}": wiggle(n, 0.01, d, i) for i, d in enumerate(drifts)}, index=idx)


def test_rotation_picks_top_two_positive_scores():
    p = prices_from(coin_frame([0.02, 0.01, 0.03, -0.02]))
    strat = slow.WeeklySharpeRotation(["C0", "C1", "C2", "C3"])
    table = strat.table(p)
    c = p.close["C2"]
    row = next(r for r in table if r["ticker"] == "C2")
    assert row["ret"] == pytest.approx(c.iloc[-1] / c.iloc[-22] - 1)
    assert row["vol"] == pytest.approx(c.pct_change().iloc[-21:].std())
    best = [r["ticker"] for r in table[:2]]
    assert strat.target_weights(p).to_dict() == {best[0]: 0.5, best[1]: 0.5}


def test_rotation_holds_cash_for_missing_winners():
    one = slow.WeeklySharpeRotation(["C0", "C1", "C2"]).target_weights(prices_from(coin_frame([0.02, -0.02, -0.03])))
    assert one.to_dict() == {"C0": 0.5}
    none = slow.WeeklySharpeRotation(["C0", "C1"]).target_weights(prices_from(coin_frame([-0.02, -0.03])))
    assert none.empty


# ── #13 ──
def fk_prices(close, funding, kimchi, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(close))
    c = pd.DataFrame({slow.BTC: close, slow.FUNDING: funding, slow.KIMCHI: kimchi}, index=idx, dtype=float)
    o = c.copy()
    o[[slow.FUNDING, slow.KIMCHI]] = np.nan
    o[slow.BTC] = c[slow.BTC].shift(1).bfill()
    return Prices(o, c)


def test_funding_kimchi_buys_only_when_all_three_hold_and_sells_on_any():
    n = 130
    close = np.linspace(100, 160, n)  # 계속 오름 → 20일 평균 위
    kim = np.full(n, 0.01)
    fund = np.full(n, 0.0001)
    start = pd.Timestamp("2026-01-01") + pd.Timedelta(days=100)
    strat = slow.FundingKimchi(start)
    assert strat.walk(fk_prices(close, fund, kim))[0] is True

    hot = fund.copy(); hot[100:] = 0.0004  # 살 때 기준(0.03%) 초과 → 못 삼
    assert strat.walk(fk_prices(close, hot, kim))[0] is False

    f2 = fund.copy(); f2[120:] = 0.0004  # 이미 들고 있으면 0.06% 까지는 버팀
    assert strat.walk(fk_prices(close, f2, kim))[0] is True
    f3 = fund.copy(); f3[125] = 0.0007  # 과열 → 팔고, 다음 날 식었으면 다시 삼
    holding, last = strat.walk(fk_prices(close[:126], f3[:126], kim[:126]))
    assert holding is False and last["sell"]["funding"]

    k2 = kim.copy(); k2[129] = 0.05  # 김치 프리미엄이 직전 90일의 95백분위 초과
    assert strat.walk(fk_prices(close, fund, k2))[0] is False

    drop = close.copy(); drop[129] = close[110]  # 20일 평균 × 0.985 아래
    assert strat.walk(fk_prices(drop, fund, kim))[0] is False


def test_funding_kimchi_percentile_excludes_today_and_missing_data_keeps_state():
    n = 130
    close, fund = np.linspace(100, 160, n), np.full(130, 0.0001)
    kim = np.full(n, 0.01); kim[129] = 0.02
    strat = slow.FundingKimchi(pd.Timestamp("2026-01-01") + pd.Timedelta(days=129))
    frame = strat.frame(fk_prices(close, fund, kim))
    assert frame["k_buy"].iloc[-1] == pytest.approx(0.01)  # 오늘 값(0.02)은 기준에 안 들어감
    assert strat.walk(fk_prices(close, fund, kim))[0] is False  # 0.02 > 80백분위 0.01 → 안 삼

    gap = fund.copy(); gap[129] = np.nan
    s2 = slow.FundingKimchi(pd.Timestamp("2026-01-01") + pd.Timedelta(days=100))
    holding, last = s2.walk(fk_prices(close, gap, np.full(n, 0.01)))
    assert holding is True and last["complete"] is False  # 자료가 빈 날은 그대로 둔다


def test_funding_average_uses_only_prints_before_candle_close():
    times = pd.date_range("2026-01-01 00:00", periods=30, freq="8h")
    funding = pd.Series(np.arange(30) * 0.0001, index=times)
    days = pd.DatetimeIndex(["2026-01-03", "2026-01-04"])
    avg = slow.funding_average(funding, days)
    before = funding[funding.index < pd.Timestamp("2026-01-04")].iloc[-9:]
    assert avg["2026-01-03"] == pytest.approx(before.mean())
    assert before.index[-1] == pd.Timestamp("2026-01-03 16:00")  # 1-4 00:00 확정분은 제외
    stale = slow.funding_average(funding.iloc[:9], days)  # 마지막 확정이 1-3 16:00 → 1-4 에는 끊긴 자료
    assert not np.isnan(stale["2026-01-03"]) and np.isnan(stale["2026-01-04"])


# ── 다시 계산(시작일, 체결 시점) ──
class AlwaysIn(slow.Strategy):
    name = "always"

    def target_weights(self, history):
        return pd.Series({history.tickers[0]: 1.0})


def test_start_at_keeps_cash_before_start_and_fills_next_open():
    idx = pd.date_range("2026-11-10", periods=8)
    close = pd.DataFrame({"X": [100, 101, 102, 103, 104, 105, 106, 107]}, index=idx, dtype=float)
    open_ = close - 0.5
    res = run_backtest(Prices(open_, close), slow.StartAt(AlwaysIn(), pd.Timestamp("2026-11-13")), "D",
                       CostModel.zero(), integer_shares=False)
    assert res.trades["date"].iloc[0] == pd.Timestamp("2026-11-14")  # 11-13 마감 결정 → 11-14 시가
    assert res.trades["price"].iloc[0] == pytest.approx(103.5)
    assert (res.equity.loc[:"2026-11-13"] == 10_000_000).all()


def test_in_progress_last_bar_never_decides():
    idx = pd.date_range("2026-11-10", periods=4)
    close = pd.DataFrame({"X": [100.0, 101, 102, 103]}, index=idx)
    res = run_backtest(Prices(close.copy(), close), slow.StartAt(AlwaysIn(), idx[-1]), "D", CostModel.zero())
    # 시작일 = 진행 중인 마지막 봉 → 그 봉에서는 결정하지 않는다
    assert res.trades.empty and idx[-1] not in res.decisions.index and (res.decisions == 0).all().all()


def test_weekly_rotation_decides_on_sunday_candle():
    idx = pd.date_range("2026-11-02", periods=16)  # 월요일부터
    close = pd.DataFrame({"KRW-BTC": np.linspace(100, 130, 16)}, index=idx)
    res = run_backtest(Prices(close.copy(), close), slow.StartAt(AlwaysIn(), idx[0]), "W", CostModel.zero(),
                       integer_shares=False)
    assert [d.day_name() for d in res.decisions.index] == ["Sunday", "Sunday"]
    assert res.trades["date"].iloc[0].day_name() == "Monday"


def test_summarize_reports_holdings_and_value():
    idx = pd.date_range("2026-11-10", periods=8)
    close = pd.DataFrame({slow.BTC: np.linspace(100, 107, 8)}, index=idx)
    p = Prices(close.copy(), close)
    spec = slow.SPECS["c_btc"]
    res = run_backtest(p, slow.StartAt(slow.BuyAndHold(), pd.Timestamp("2026-11-12")), spec.freq, spec.costs,
                       integer_shares=False, min_trade_frac=0.03)
    d = slow.summarize("c_btc", res, p)
    assert d["holdings"][0]["ticker"] == slow.BTC and d["holdings"][0]["weight"] == pytest.approx(1.0, abs=0.01)
    assert d["value"] == pytest.approx(float(res.equity.iloc[-1]))
    assert len(d["trades"]) == 1 and d["trades"][0]["side"] == "buy"


def test_completed_drops_the_bar_still_in_progress():
    idx = pd.date_range("2026-11-13", periods=3)
    p = Prices(pd.DataFrame({"X": [1.0, 2, 3]}, index=idx), pd.DataFrame({"X": [1.0, 2, 3]}, index=idx))
    now = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)
    assert completed(p, "coin", now).dates[-1] == pd.Timestamp("2026-11-14")
    assert completed(p, "coin", datetime(2026, 11, 16, 0, 5, tzinfo=timezone.utc)).dates[-1] == pd.Timestamp("2026-11-15")
    assert completed(p, "etf", datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)).dates[-1] == pd.Timestamp("2026-11-14")  # 한국 12시
    assert completed(p, "etf", datetime(2026, 11, 15, 7, 0, tzinfo=timezone.utc)).dates[-1] == pd.Timestamp("2026-11-15")  # 한국 16시


def test_next_times_are_utc_midnight_monday_and_friday_close():
    now = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)  # 일요일
    nt = next_times(now)
    assert datetime.fromtimestamp(nt["s13"], timezone.utc) == datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(nt["s14"], timezone.utc) == datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(nt["s12"], timezone.utc) == datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc)
