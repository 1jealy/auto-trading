"""실험 #11: 비트코인 60일선 규칙을 처음 보는 2014-12 ~ 2017-09 로 확인. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp11_btc_trend_2014.py    # 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, Prices, run_backtest  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.strategies import BuyAndHold, TrendFilter  # noqa: E402

TICKER = "BTC-USD"
DATA_START, END = "2014-09-01", "2017-09-30"  # 2017-10 부터는 이미 본 기간 → 불러오지 않는다
START = "2014-12-01"
HALVES = [("2014-12-01", "2016-04-30"), ("2016-05-01", "2017-09-30")]
WINDOW = 60
COST = 0.0025
CACHE = ROOT / "data" / "cache" / f"yahoo_{TICKER}_{DATA_START}_{END}.csv"
COLS = ["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]


def load() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_csv(CACHE, index_col=0, parse_dates=True)
    import yfinance as yf

    df = yf.download(TICKER, start=DATA_START, end=(pd.Timestamp(END) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                     interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "Close"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.loc[:END]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE)
    return df


def run(prices: Prices, cost: float):
    costs = CostModel(commission=cost, sell_tax=0.0, slippage=0.0)
    rule = run_backtest(prices, TrendFilter([TICKER], None, WINDOW), "D", costs, integer_shares=False)
    hold = run_backtest(prices, BuyAndHold(), "M", costs, integer_shares=False)
    return rule, hold


def table(rule, hold, a: str, b: str) -> pd.DataFrame:
    t = pd.DataFrame({"60일선 위에서만": summarize(rule.equity.loc[a:b], 365),
                      "그냥 보유": summarize(hold.equity.loc[a:b], 365)}).T[COLS]
    t.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    return t


def show(t: pd.DataFrame) -> str:
    s = t.astype(object)
    for c in t.columns:
        s[c] = t[c].map(("{:.2f}" if c == "수익/흔들림" else "{:+.1%}").format)
    return s.to_string()


def main() -> None:
    df = load()
    gaps = df.index.to_series().diff().dt.days.gt(1).sum()
    print(f"데이터 {df.index[0].date()} ~ {df.index[-1].date()}, {len(df)}일, 빠진 날 {gaps}곳")
    jumps = (df["Close"].pct_change().abs() > 0.25)
    if jumps.any():
        print("경고: 하루 25% 넘는 변화:", df.index[jumps].date.tolist())
    prices = Prices(df[["Open"]].rename(columns={"Open": TICKER}), df[["Close"]].rename(columns={"Close": TICKER}))

    rule, hold = run(prices, COST)
    full = table(rule, hold, START, END)
    print(f"\n[판단] {START} ~ {END}, 한쪽 비용 {COST:.2%}\n{show(full)}")
    buys = rule.trades[(rule.trades["date"] >= START) & (rule.trades["side"] == "buy")]
    inside = (rule.decisions.loc[START:END][TICKER] > 0).mean()
    print(f"사기 {len(buys)}번, 들고 있던 날 {inside:.0%}")

    halves = []
    for a, b in HALVES:
        t = table(rule, hold, a, b)
        halves.append(t)
        print(f"\n[{a} ~ {b}]\n{show(t)}")

    r, h = full.loc["60일선 위에서만"], full.loc["그냥 보유"]
    print("\n기준 확인:")
    print(f"  1. 수익/흔들림 {r['수익/흔들림']:.2f} > {h['수익/흔들림']:.2f} → {'통과' if r['수익/흔들림'] > h['수익/흔들림'] else '미달'}")
    print(f"  2. 최대 하락 {r['최대 하락폭']:.1%} 이 {h['최대 하락폭']:.1%} 보다 작음 → "
          f"{'통과' if r['최대 하락폭'] > h['최대 하락폭'] else '미달'}")
    print(f"  3. 사기 {len(buys)}번 ≥ 10 → {'통과' if len(buys) >= 10 else '미달'}")
    ok4 = all(t.loc["60일선 위에서만", "수익/흔들림"] > t.loc["그냥 보유", "수익/흔들림"] for t in halves)
    print("  4. 절반씩: " + ", ".join(f"{t.loc['60일선 위에서만', '수익/흔들림']:.2f} vs {t.loc['그냥 보유', '수익/흔들림']:.2f}"
                                  for t in halves) + f" → {'통과' if ok4 else '미달'}")

    print("\n연도별 수익률 (60일선 / 그냥 보유):")
    for y in range(2014, 2018):
        a, b = max(f"{y}-01-01", START), f"{y}-12-31"
        er, eh = rule.equity.loc[a:b], hold.equity.loc[a:b]
        if len(er) > 1:
            # 그 해 첫날 직전 값부터 계산
            pr = rule.equity.loc[:a].iloc[-2] if len(rule.equity.loc[:a]) > 1 else er.iloc[0]
            ph = hold.equity.loc[:a].iloc[-2] if len(hold.equity.loc[:a]) > 1 else eh.iloc[0]
            print(f"  {y}{'(12월부터)' if y == 2014 else ''}{'(9월까지)' if y == 2017 else ''}: "
                  f"{er.iloc[-1] / pr - 1:+.1%} / {eh.iloc[-1] / ph - 1:+.1%}")

    print("\n참고 (판단 안 함): 비용만 바꿨을 때 전체 기간")
    for c in (0.0015, 0.005):
        rr, hh = run(prices, c)
        t = table(rr, hh, START, END)
        print(f"  한쪽 {c:.2%}: 60일선 연 {t.loc['60일선 위에서만', '연평균 수익률']:+.1%}, 수익/흔들림 "
              f"{t.loc['60일선 위에서만', '수익/흔들림']:.2f} (보유 {t.loc['그냥 보유', '수익/흔들림']:.2f}), "
              f"최대 하락 {t.loc['60일선 위에서만', '최대 하락폭']:.1%}")


if __name__ == "__main__":
    main()
