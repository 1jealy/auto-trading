"""실험 #2: 업종 급락 후 며칠 보유. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp02_rebound.py practice      # 연습 기간
    python scripts/exp02_rebound.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, compare, run_backtest  # noqa: E402
from autotrade.checks import warn_big_jumps, warn_suspicious_fills  # noqa: E402
from autotrade.data import Prices, load_prices  # noqa: E402
from autotrade.strategies import BuyAndHold, ShortTermRebound  # noqa: E402

SECTORS = {
    "091160": "반도체",
    "091170": "은행",
    "117700": "건설",
    "102970": "증권",
    "139260": "IT",
    "143860": "헬스케어",
    "139250": "에너지화학",
    "139280": "생활소비재",
}
KOSPI = "069500"
SAFE = "153130"  # 단기채권
DATA_START = "2014-08-01"
PERIODS = {
    "practice": ("2015-09-01", "2020-12-31"),
    "validation": ("2021-01-01", "2026-10-31"),
}
HOLD_DAYS = 3
EXTEND = "fresh"  # docs/experiments.md 규칙 3 해석 — 결과 보기 전에 확정
COSTS = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.001)
MIN_TRADE_FRAC = 0.05


def main(period: str) -> None:
    start, end = PERIODS[period]
    sectors = list(SECTORS)
    prices = load_prices(sectors + [KOSPI, SAFE], DATA_START, end)
    warn_big_jumps(prices.select(sectors))

    strat = ShortTermRebound(sectors, hold_days=HOLD_DAYS, slots=4, extend=EXTEND)
    result = run_backtest(prices.select(sectors), strat, "D", COSTS, min_trade_frac=MIN_TRADE_FRAC)
    kospi = run_backtest(prices.select([KOSPI]), BuyAndHold(), "M", COSTS)
    equal = run_backtest(prices.select(sectors), BuyAndHold(), "M", COSTS)
    safe = run_backtest(prices.select([SAFE]), BuyAndHold(), "M", COSTS)

    curves = {
        "급락 후 보유": result.equity,
        "단기채권 보유": safe.equity,
        "KODEX200 보유(참고)": kospi.equity,
        "8개 나눠담기(참고)": equal.equity,
    }
    table = compare({k: v.loc[start:end] for k, v in curves.items()})
    print(f"[{period}] {start} ~ {end}\n")
    print(_pretty(table))

    eps = episode_returns(strat.episodes, prices, start, end)
    base = random_day_return(prices.select(sectors), start, end)
    invested = result.decisions.loc[start:end].sum(axis=1).reindex(
        result.equity.loc[start:end].index).ffill().fillna(0).mean()

    print(f"\n매매(보유 건수) {len(eps)}건, 평균 보유 {eps['days'].mean():.1f}일, "
          f"보유 일수 분포 {eps['days'].value_counts().sort_index().to_dict()}")
    print(f"평균 투자 비중 {invested:.0%} (나머지는 현금)")
    print(f"업종별 건수 {eps['ticker'].map(SECTORS).value_counts().to_dict()}")
    print(f"건당 평균 수익(비용 뺀 것) {eps['ret'].mean():+.2%}, 이긴 비율 {(eps['ret'] > 0).mean():.0%}")
    print(f"아무 날이나 사서 {HOLD_DAYS}일 보유 평균(비용 뺀 것) {base:+.2%}")

    s = table.loc["급락 후 보유"]
    checks = {
        "1. 신호 효과 (건당 수익 > 아무 날 평균)": eps["ret"].mean() > base,
        "2. 할 만한 가치 (연평균 > 단기채권)": s["cagr"] > table.loc["단기채권 보유", "cagr"],
        "3. 안전 (최대 하락폭 > -25%)": s["max_drawdown"] > -0.25,
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    warn_suspicious_fills(prices, result.trades)


def episode_returns(episodes: list[dict], prices: Prices, start: str, end: str) -> pd.DataFrame:
    """보유 1건마다: 신호 다음 날 시가에 사서 판 수익(비용 뺀 것).

    마지막 보유 결정일(last_decision) 다음 날 결정에서 빠지고, 그 다음 날 시가에 팔린다 → +2.
    """
    idx = prices.dates
    rows = []
    for e in episodes:
        buy_i = idx.get_loc(e["signal_date"]) + 1
        sell_i = idx.get_loc(e["last_decision"]) + 2
        if sell_i >= len(idx) or not (pd.Timestamp(start) <= idx[buy_i] <= pd.Timestamp(end)):
            continue
        o = prices.open[e["ticker"]]
        gross = o.iloc[sell_i] / o.iloc[buy_i]
        rows.append(dict(
            ticker=e["ticker"], buy=idx[buy_i], sell=idx[sell_i], days=sell_i - buy_i,
            ret=gross * (1 - COSTS.sell_rate) / (1 + COSTS.buy_rate) - 1,
        ))
    return pd.DataFrame(rows, columns=["ticker", "buy", "sell", "days", "ret"])


def random_day_return(prices: Prices, start: str, end: str) -> float:
    """기간 안 아무 날 시가에 사서 HOLD_DAYS 뒤 시가에 판 평균 수익(비용 뺀 것), 모든 업종 평균."""
    o = prices.open
    gross = (o.shift(-HOLD_DAYS) / o).loc[start:end]
    net = gross * (1 - COSTS.sell_rate) / (1 + COSTS.buy_rate) - 1
    return float(net.stack().mean())


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
