"""실험 #9: 60일 평균 위에서만 보유 (방법 B). 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp09_trendfilter.py practice      # 연습 기간
    python scripts/exp09_trendfilter.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
    python scripts/exp09_trendfilter.py btc           # 비트코인 참고(판단 안 함)
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, Prices, compare, run_backtest  # noqa: E402
from autotrade.checks import warn_big_jumps, warn_suspicious_fills  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.strategies import BuyAndHold, TrendFilter  # noqa: E402
from autotrade.upbit import days_between  # noqa: E402

# 종목·기간·비용은 실험 #1과 같다
NAMES = {
    "069500": "한국주식",
    "143850": "미국주식",
    "148070": "한국채권",
    "138230": "달러",
    "153130": "단기채권(피난처)",
}
ASSETS = ["069500", "143850", "148070", "138230"]
SAFE = "153130"
WINDOW = 60
MIN_TRADE = 0.03
DATA_START = "2014-08-01"
PERIODS = {
    "practice": ("2015-09-01", "2020-12-31"),
    "validation": ("2021-01-01", "2026-10-31"),
}
COSTS = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.0005)  # ETF 는 거래세 없음

BTC = "KRW-BTC"
BTC_PERIODS = [("2017-12-01", "2021-12-31"), ("2022-01-01", "2026-10-31")]
BTC_COSTS = CostModel(commission=0.0005, sell_tax=0.0, slippage=0.0010)  # 한쪽 0.15% (#8 추가 시험)
BTC_CACHE = ROOT / "data" / "cache" / "upbit_KRW-BTC_days.csv"

COLS = ["전체 수익률", "연평균 수익률", "흔들림(변동성)", "수익/흔들림 비율", "최대 하락폭"]


def show(table: pd.DataFrame) -> str:
    table = table.copy()
    table.columns = COLS
    shown = table.astype(object)
    for col in COLS:
        fmt = "{:.2f}" if col == "수익/흔들림 비율" else "{:.1%}"
        shown[col] = table[col].map(fmt.format)
    return shown.to_string()


def trades_in(result, start: str) -> int:
    return int((result.trades["date"] >= start).sum())


def main(period: str) -> None:
    start, end = PERIODS[period]
    # 연습 기간에는 그 이후 데이터를 아예 불러오지 않는다
    prices = load_prices(ASSETS + [SAFE], DATA_START, end)
    warn_big_jumps(prices)

    trend = run_backtest(prices, TrendFilter(ASSETS, SAFE, WINDOW), "D", COSTS, min_trade_frac=MIN_TRADE)
    equal = run_backtest(prices.select(ASSETS), BuyAndHold(), "M", COSTS)
    kospi = run_backtest(prices.select(["069500"]), BuyAndHold(), "M", COSTS)
    safe = run_backtest(prices.select([SAFE]), BuyAndHold(), "M", COSTS)

    curves = {
        "60일선 4칸": trend.equity,
        "4개 나눠담기": equal.equity,
        "KODEX200 보유": kospi.equity,
        "단기채권 보유": safe.equity,
    }
    table = compare({k: v.loc[start:end] for k, v in curves.items()})
    print(f"[{period}] {start} ~ {end}\n")
    print(show(table))

    t, e = table.loc["60일선 4칸"], table.loc["4개 나눠담기"]
    print("\n기준 확인 (4개 나눠담기와 비교):")
    print(f"  1. 수익/흔들림 {t['sharpe']:.2f} > {e['sharpe']:.2f} → {'통과' if t['sharpe'] > e['sharpe'] else '미달'}")
    print(f"  2. 최대 하락 {t['max_drawdown']:.1%} 이 {e['max_drawdown']:.1%} 보다 작음 → "
          f"{'통과' if t['max_drawdown'] > e['max_drawdown'] else '미달'}")
    gap = t["cagr"] - e["cagr"]
    print(f"  3. 연평균 차이 {gap:+.1%}p (−1%p 보다 나아야) → {'통과' if gap >= -0.01 else '미달'}")

    held = trend.decisions.loc[start:end]
    print("\n칸별로 종목을 들고 있던 날 비율:",
          {NAMES[a]: f"{(held[a] > 0).mean():.0%}" for a in ASSETS})
    print(f"매매 {trades_in(trend, start)}건, 전체 비용 {trend.total_cost:,.0f}원 (원금 1,000만 원, 2014-08부터 누적)")
    warn_suspicious_fills(prices, trend.trades)

    print("\n종목별 (참고, 판단 안 함): 규칙 적용 vs 그냥 보유")
    rows = {}
    for a in ASSETS:
        one = run_backtest(prices.select([a, SAFE]), TrendFilter([a], SAFE, WINDOW), "D", COSTS,
                           min_trade_frac=MIN_TRADE)
        hold = run_backtest(prices.select([a]), BuyAndHold(), "M", COSTS)
        pair = compare({"r": one.equity.loc[start:end], "h": hold.equity.loc[start:end]})
        rows[NAMES[a]] = {
            "규칙 연평균": f"{pair.loc['r', 'cagr']:.1%}", "보유 연평균": f"{pair.loc['h', 'cagr']:.1%}",
            "규칙 수익/흔들림": f"{pair.loc['r', 'sharpe']:.2f}", "보유 수익/흔들림": f"{pair.loc['h', 'sharpe']:.2f}",
            "규칙 최대하락": f"{pair.loc['r', 'max_drawdown']:.1%}", "보유 최대하락": f"{pair.loc['h', 'max_drawdown']:.1%}",
            "매매": trades_in(one, start) // 2,
        }
    print(pd.DataFrame(rows).T.to_string())


def btc_days(end: str) -> pd.DataFrame:
    """업비트 비트코인 하루 캔들 (UTC 0시 시작). 받은 것은 저장해 다시 쓴다."""
    need = pd.Timestamp(end)
    if BTC_CACHE.exists():
        df = pd.read_csv(BTC_CACHE, index_col=0, parse_dates=True)
        if df.index.max() >= need:
            return df.loc[:end]
    df = days_between(BTC, datetime(2017, 9, 1), datetime.fromisoformat(end) + timedelta(days=1))
    BTC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(BTC_CACHE)
    return df.loc[:end]


def btc() -> None:
    print("비트코인 (참고, 판단 안 함) — 60일 평균 위에서만 보유, 아니면 현금. 한쪽 비용 0.15%, 1년 = 365일\n")
    for start, end in BTC_PERIODS:
        df = btc_days(end)
        close = df[["Close"]].rename(columns={"Close": BTC})
        opens = df[["Open"]].rename(columns={"Open": BTC})
        prices = Prices(opens, close)
        rule = run_backtest(prices, TrendFilter([BTC], None, WINDOW), "D", BTC_COSTS, integer_shares=False)
        hold = run_backtest(prices, BuyAndHold(), "M", BTC_COSTS, integer_shares=False)
        rows = {}
        for name, res in [("60일선 위에서만", rule), ("그냥 보유", hold)]:
            rows[name] = summarize(res.equity.loc[start:end], periods_per_year=365)
        table = pd.DataFrame(rows).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
        inside = (rule.decisions.loc[start:end][BTC] > 0).mean()
        print(f"[{start} ~ {end}]  첫 데이터 {df.index[0].date()}")
        print(show(table))
        print(f"매매 {trades_in(rule, start)}건, 들고 있던 날 {inside:.0%}\n")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which == "btc":
        btc()
    elif which in PERIODS:
        main(which)
    else:
        raise SystemExit(f"기간은 {list(PERIODS)} 또는 btc")
