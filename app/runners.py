"""실험 #1~#4 계산. 기본값은 docs/experiments.md 에 기록된 규칙 그대로이고,
탐색 화면에서는 같은 함수에 다른 숫자를 넣어 돌린다.

결과는 {"curves": {이름: 자산곡선}, "facts": [(항목, 값)]} 모양의 dict.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import common  # noqa: F401  (src, scripts 경로 등록)
from autotrade import CostModel, run_backtest
from autotrade.data import Prices, load_ohlc, load_prices
from autotrade.overnight import annualized, night_day_returns, simulate
from autotrade.strategies import (
    AssetSwitching,
    BuyAndHold,
    Eligibility,
    EligibleEqualWeight,
    LowVolatility,
    ShortTermRebound,
)
from autotrade.universe import DEFAULT_SNAPSHOT, kospi_universe

import exp01_switching as e1
import exp02_rebound as e2
import exp03_lowvol as e3
import exp04_overnight as e4


def _window(curves: dict[str, pd.Series], start: str, end: str) -> dict[str, pd.Series]:
    return {name: c.loc[start:end] for name, c in curves.items()}


@st.cache_data(show_spinner=False)
def _exp1(period: str = "practice", lookback: int = 252, slippage: float = 0.0005) -> dict:
    start, end = e1.PERIODS[period]
    prices = load_prices(e1.CANDIDATES + [e1.SAFE], e1.DATA_START, end)
    costs = CostModel(commission=0.00015, sell_tax=0.0, slippage=slippage)
    sw = run_backtest(prices, AssetSwitching(e1.CANDIDATES, e1.SAFE, lookback), "M", costs)
    kp = run_backtest(prices.select(["069500"]), BuyAndHold(), "M", costs)
    eq = run_backtest(prices.select(e1.CANDIDATES), BuyAndHold(), "M", costs)

    held = sw.decisions.loc[start:end]
    held = held[held.sum(axis=1) > 0].idxmax(axis=1).map(e1.NAMES).value_counts()
    trades = sw.trades[(sw.trades["date"] >= start) & (sw.trades["date"] <= end)]
    return {
        "curves": _window({"강한 자산 갈아타기": sw.equity, "KODEX 200 그냥 보유": kp.equity,
                           "4개 똑같이 나눠 담기": eq.equity}, start, end),
        "facts": [("들고 있던 개월 수", ", ".join(f"{k} {v}" for k, v in held.items())),
                  ("사고판 횟수", f"{len(trades)}번")],
    }



def _episode_returns(episodes, prices: Prices, costs: CostModel, start: str, end: str) -> pd.Series:
    idx, rets = prices.dates, []
    for e in episodes:
        buy_i = idx.get_loc(e["signal_date"]) + 1
        sell_i = idx.get_loc(e["last_decision"]) + 2  # 결정에서 빠진 다음 날 시가에 매도
        if sell_i >= len(idx) or not (pd.Timestamp(start) <= idx[buy_i] <= pd.Timestamp(end)):
            continue
        o = prices.open[e["ticker"]]
        rets.append(o.iloc[sell_i] / o.iloc[buy_i] * (1 - costs.sell_rate) / (1 + costs.buy_rate) - 1)
    return pd.Series(rets, dtype=float)


@st.cache_data(show_spinner=False)
def _exp2(threshold: float = 2.0, hold_days: int = 3, slots: int = 4, slippage: float = 0.001) -> dict:
    start, end = e2.PERIODS["practice"]
    sectors = list(e2.SECTORS)
    prices = load_prices(sectors + [e2.KOSPI, e2.SAFE], e2.DATA_START, end)
    costs = CostModel(commission=0.00015, sell_tax=0.0, slippage=slippage)
    strat = ShortTermRebound(sectors, threshold=threshold, hold_days=hold_days, slots=slots, extend="fresh")
    res = run_backtest(prices.select(sectors), strat, "D", costs, min_trade_frac=e2.MIN_TRADE_FRAC)
    safe = run_backtest(prices.select([e2.SAFE]), BuyAndHold(), "M", costs)
    kospi = run_backtest(prices.select([e2.KOSPI]), BuyAndHold(), "M", costs)

    eps = _episode_returns(strat.episodes, prices.select(sectors), costs, start, end)
    o = prices.select(sectors).open
    anyday = ((o.shift(-hold_days) / o).loc[start:end] * (1 - costs.sell_rate) / (1 + costs.buy_rate) - 1).stack()
    return {
        "curves": _window({"급락 후 며칠 보유": res.equity, "단기채권 보유": safe.equity,
                           "KODEX 200 그냥 보유": kospi.equity}, start, end),
        "facts": [("사고판 횟수", f"{len(eps)}번"),
                  ("한 번당 평균 손익 (비용 뺀 것)", f"{eps.mean():+.2%}" if len(eps) else "-"),
                  ("이긴 비율", f"{(eps > 0).mean():.0%}" if len(eps) else "-"),
                  (f"아무 날이나 사서 {hold_days}일 들고 있었다면", f"{anyday.mean():+.2%}")],
    }



@st.cache_resource(show_spinner=False)
def _kospi_prices() -> tuple[Prices, dict, Prices]:
    """코스피 전 종목(상장폐지 포함)과 KODEX 200. 연습 기간 끝까지만 남긴다."""
    end = pd.Timestamp(e3.PERIODS["practice"][1])
    uni = kospi_universe("2014-08-01", DEFAULT_SNAPSHOT)
    full = load_prices(uni["Code"], e3.DATA_START, e3.DATA_END, with_volume=True,
                       drop_no_trade=True, workers=4, skip_errors=True)
    prices = e3._drop_empty(full.until(end))
    delisted = {c: d for c, d in zip(uni["Code"], uni["DelistingDate"]) if pd.notna(d) and d <= end}
    kospi = load_prices([e3.KOSPI], e3.DATA_START, e3.DATA_END).until(end)
    return prices, delisted, kospi


@st.cache_data(show_spinner=False)
def _exp3(top_n: int = 20, vol_window: int = 252, min_value_eok: float = 10.0, sell_tax: float = 0.0025) -> dict:
    start, end = e3.PERIODS["practice"]
    prices, delisted, kospi = _kospi_prices()
    rules = Eligibility(vol_window=vol_window, min_traded_days=round(vol_window * 240 / 252),
                        value_window=60, min_value=min_value_eok * 1e8)
    costs = CostModel(commission=0.00015, sell_tax=sell_tax, slippage=0.001)
    lv = run_backtest(prices, LowVolatility(top_n, rules), "M", costs, e3.INITIAL,
                      integer_shares=False, min_trade_frac=e3.MIN_TRADE_FRAC, delisted=delisted)
    ew_strategy = EligibleEqualWeight(rules)
    ew = run_backtest(prices, ew_strategy, "M", costs, e3.INITIAL, integer_shares=False, delisted=delisted)
    kp = run_backtest(kospi, BuyAndHold(), "M", e3.COSTS_ETF, e3.INITIAL, integer_shares=False)

    counts = pd.Series(ew_strategy.counts).loc[start:end]
    return {
        "curves": _window({f"덜 흔들리는 {top_n}개": lv.equity, "KODEX 200 그냥 보유": kp.equity,
                           "조건 통과 종목 전부 나눠 담기": ew.equity}, start, end),
        "facts": [("고를 수 있었던 종목 수 (평균)", f"{counts.mean():.0f}개"),
                  ("시험한 종목 수", f"{len(prices.tickers)}개 (중간에 상장폐지 {len(delisted)}개 포함)"),
                  ("주의", "개별 주식 가격에는 배당이 빠져 있어서, 배당이 포함된 KODEX 200보다 불리하게 나옵니다.")],
    }


@st.cache_data(show_spinner=False)
def _exp4(mode: str = "wait", round_trip: float = 0.0005, fill_margin: float = 0.0002) -> dict:
    start, end = e4.PERIODS["practice"]
    kodex = load_ohlc(e4.MAIN, e4.DATA_START, e4.DATA_END).loc[:end].dropna(subset=["Open", "High", "Close"])
    safe = load_ohlc(e4.SAFE, e4.DATA_START, e4.DATA_END).loc[:end, "Close"].dropna()
    costs = CostModel(commission=round_trip / 4, sell_tax=0.0, slippage=round_trip / 4)
    equity, trades = simulate(kodex, costs, mode=mode, fill_margin=fill_margin)

    nd = night_day_returns(kodex).loc[start:end]
    t = trades[(trades["sell_date"] >= start) & (trades["sell_date"] <= end)]
    kinds = ", ".join(f"{k} {len(g)}번 (평균 {g['ret'].mean():+.2%})" for k, g in t.groupby("kind"))
    label = "밤에만 들고 있기 (손해면 하루 더)" if mode == "wait" else "밤에만 들고 있기 (아침에 무조건 매도)"
    return {
        "curves": _window({label: equity, "KODEX 200 그냥 보유": kodex["Close"], "단기채권 보유": safe}, start, end),
        "facts": [("비용 빼기 전 1년 평균 — 밤 동안", f"{annualized(nd['night']):+.1%}"),
                  ("비용 빼기 전 1년 평균 — 낮 동안", f"{annualized(nd['day']):+.1%}"),
                  ("팔린 방식", kinds or "-")],
    }


# 화면에서 넘어오는 숫자를 같은 모양으로 맞춰서, 같은 규칙이면 저장된 계산 결과를 다시 쓰게 한다.
def exp1(period: str = "practice", lookback: int = 252, slippage: float = 0.0005) -> dict:
    return _exp1(period, int(lookback), round(float(slippage), 8))


def exp2(threshold: float = 2.0, hold_days: int = 3, slots: int = 4, slippage: float = 0.001) -> dict:
    return _exp2(round(float(threshold), 8), int(hold_days), int(slots), round(float(slippage), 8))


def exp3(top_n: int = 20, vol_window: int = 252, min_value_eok: float = 10.0, sell_tax: float = 0.0025) -> dict:
    return _exp3(int(top_n), int(vol_window), round(float(min_value_eok), 8), round(float(sell_tax), 8))


def exp4(mode: str = "wait", round_trip: float = 0.0005, fill_margin: float = 0.0002) -> dict:
    return _exp4(mode, round(float(round_trip), 8), round(float(fill_margin), 8))
