"""실험 #1: 강한 자산 갈아타기. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp01_switching.py practice      # 연습 기간
    python scripts/exp01_switching.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, compare, run_backtest  # noqa: E402
from autotrade.checks import warn_big_jumps, warn_suspicious_fills  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.strategies import AssetSwitching, BuyAndHold  # noqa: E402

NAMES = {
    "069500": "한국주식",
    "143850": "미국주식",
    "148070": "한국채권",
    "138230": "달러",
    "153130": "단기채권(피난처)",
}
CANDIDATES = ["069500", "143850", "148070", "138230"]
SAFE = "153130"
DATA_START = "2014-08-01"  # 첫 1년은 수익률 계산용
PERIODS = {
    "practice": ("2015-09-01", "2020-12-31"),
    "validation": ("2021-01-01", "2026-10-31"),
}
COSTS = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.0005)  # ETF 는 거래세 없음


def main(period: str) -> None:
    start, end = PERIODS[period]
    # 연습 기간에는 그 이후 데이터를 아예 불러오지 않는다
    prices = load_prices(CANDIDATES + [SAFE], DATA_START, end)
    warn_big_jumps(prices)

    switching = run_backtest(prices, AssetSwitching(CANDIDATES, SAFE), "M", COSTS)
    kospi = run_backtest(prices.select(["069500"]), BuyAndHold(), "M", COSTS)
    equal = run_backtest(prices.select(CANDIDATES), BuyAndHold(), "M", COSTS)

    curves = {
        "갈아타기": switching.equity,
        "KODEX200 보유": kospi.equity,
        "4개 나눠담기": equal.equity,
    }
    window = {k: v.loc[start:end] for k, v in curves.items()}
    table = compare(window)
    table.columns = ["전체 수익률", "연평균 수익률", "흔들림(변동성)", "수익/흔들림 비율", "최대 하락폭"]
    print(f"[{period}] {start} ~ {end}\n")
    shown = table.copy().astype(object)
    for col in shown.columns:
        fmt = "{:.2f}" if col == "수익/흔들림 비율" else "{:.1%}"
        shown[col] = table[col].map(fmt.format)
    print(shown.to_string())

    held = switching.decisions.loc[start:end].idxmax(axis=1).map(NAMES)
    print("\n보유 개월 수:", held.value_counts().to_dict())
    print(f"매매 {len(switching.trades.loc[switching.trades['date'] >= start])}건, "
          f"전체 비용 {switching.total_cost:,.0f}원 (원금 1,000만 원 기준)")
    warn_suspicious_fills(prices, switching.trades)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
