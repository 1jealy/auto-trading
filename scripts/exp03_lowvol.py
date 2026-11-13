"""실험 #3: 덜 흔들리는 주식. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp03_lowvol.py practice      # 연습 기간
    python scripts/exp03_lowvol.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, compare, run_backtest  # noqa: E402
from autotrade.checks import warn_suspicious_fills  # noqa: E402
from autotrade.data import Prices, load_prices  # noqa: E402
from autotrade.strategies import BuyAndHold, EligibleEqualWeight, LowVolatility  # noqa: E402
from autotrade.universe import DEFAULT_SNAPSHOT, kospi_universe  # noqa: E402

KOSPI = "069500"
DATA_START, DATA_END = "2014-08-01", "2026-10-31"
PERIODS = {
    "practice": ("2015-09-01", "2020-12-31"),
    "validation": ("2021-01-01", "2026-10-31"),
}
TOP_N = 20
COSTS_STOCK = CostModel(commission=0.00015, sell_tax=0.0025, slippage=0.001)
COSTS_ETF = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.0005)
INITIAL = 100_000_000
MIN_TRADE_FRAC = 0.01


def main(period: str) -> None:
    start, end = PERIODS[period]
    end_ts = pd.Timestamp(end)

    uni = kospi_universe("2014-08-01", DEFAULT_SNAPSHOT)
    names = dict(zip(uni["Code"], uni["Name"]))
    full = load_prices(uni["Code"], DATA_START, DATA_END, with_volume=True,
                       drop_no_trade=True, workers=4, skip_errors=True)
    prices = _drop_empty(full.until(end_ts))  # 기간 끝 이후 데이터는 버린다
    delisted = {c: d for c, d in zip(uni["Code"], uni["DelistingDate"]) if pd.notna(d) and d <= end_ts}
    kospi = load_prices([KOSPI], DATA_START, DATA_END).until(end_ts)

    lv = run_backtest(prices, LowVolatility(TOP_N), "M", COSTS_STOCK, INITIAL,
                      integer_shares=False, min_trade_frac=MIN_TRADE_FRAC, delisted=delisted)
    ew_strategy = EligibleEqualWeight()
    ew = run_backtest(prices, ew_strategy, "M", COSTS_STOCK, INITIAL,
                      integer_shares=False, delisted=delisted)
    kp = run_backtest(kospi, BuyAndHold(), "M", COSTS_ETF, INITIAL, integer_shares=False)

    curves = {"덜 흔들리는 20개": lv.equity, "KODEX200 보유": kp.equity, "조건 통과 전부 나눠담기": ew.equity}
    table = compare({k: v.loc[start:end] for k, v in curves.items()})
    print(f"[{period}] {start} ~ {end}  (종목 {len(prices.tickers)}개, 그중 상장폐지 {len(delisted)}개)\n")
    print(_pretty(table))

    counts = pd.Series(ew_strategy.counts).loc[start:end]
    held = (lv.decisions.loc[start:end] > 0).sum().sort_values(ascending=False)
    in_window = lambda t: t[(t["date"] >= start) & (t["date"] <= end)]  # noqa: E731
    years = len(lv.equity.loc[start:end]) / 252
    turnover = in_window(lv.trades)["amount"].sum() / lv.equity.loc[start:end].mean() / years

    print(f"\n고를 수 있는 종목 수: 평균 {counts.mean():.0f}개 (최소 {counts.min()}, 최대 {counts.max()})")
    print(f"덜 흔들리는 20개 — 연 회전율 {turnover:.1f}배, 거쳐 간 종목 {int((held > 0).sum())}개")
    print("가장 오래 보유한 종목(개월):", {names.get(t, t): int(n) for t, n in held.head(10).items()})
    for label, res in (("덜 흔들리는 20개", lv), ("나눠담기", ew)):
        d = in_window(res.trades)
        d = d[d["side"] == "delist"]
        if not d.empty:
            print(f"{label} 보유 중 상장폐지 {len(d)}건:", [names.get(t, t) for t in d["ticker"]][:10])

    s, k, e = (table.loc[n] for n in curves)
    checks = {
        "1. 덜 흔들리나 (흔들림·최대 하락폭 모두 KODEX200보다 작음)":
            s["volatility"] < k["volatility"] and s["max_drawdown"] > k["max_drawdown"],
        "2. 수익이 크게 뒤처지지 않나 (KODEX200 - 2%p 이상)": s["cagr"] >= k["cagr"] - 0.02,
        "3. 고른 효과가 있나 (수익/흔들림 > 나눠담기)": s["sharpe"] > e["sharpe"],
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    warn_suspicious_fills(prices, in_window(lv.trades[lv.trades["side"] != "delist"]))
    _warn_held_jumps(prices, lv, start, end, names)


def _drop_empty(p: Prices) -> Prices:
    keep = p.close.columns[p.close.notna().any()]
    return p.select(keep)


def _warn_held_jumps(prices: Prices, res, start: str, end: str, names: dict) -> None:
    """보유 중 하루 30%(가격제한폭) 넘게 움직인 날 — 데이터 오류 가능성."""
    rets = prices.close.ffill().pct_change().loc[start:end]
    held = res.decisions.reindex(rets.index).ffill().fillna(0) > 0
    big = rets.where(held).abs().stack()
    big = big[big > 0.3]
    if not big.empty:
        print(f"\n경고: 보유 중 하루 30% 넘는 변화 {len(big)}건:",
              [(d.date(), names.get(t, t), f"{rets.loc[d, t]:+.0%}") for (d, t) in big.index[:10]])


def _pretty(table: pd.DataFrame) -> str:
    out = table.copy().astype(object)
    for col in out.columns:
        fmt = "{:.2f}" if col == "sharpe" else "{:.1%}"
        out[col] = table[col].map(fmt.format)
    out.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    return out.to_string()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
