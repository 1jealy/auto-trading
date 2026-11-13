"""예제: 대형주 모멘텀 vs KODEX 200 보유.

주의 — 이 예제의 종목군은 '지금' 대형주인 종목을 골랐으므로 생존 편향이 있다.
(과거에 대형주였다가 몰락한 종목이 빠져 있음) 구조를 보여주기 위한 예시일 뿐이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrade import CostModel, compare, run_backtest  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.strategies import BuyAndHold, Momentum  # noqa: E402

UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "035420",  # NAVER
    "051910",  # LG화학
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
    "012330",  # 현대모비스
    "035720",  # 카카오
]
BENCHMARK = "069500"  # KODEX 200
START, END = "2015-01-01", "2025-12-31"


def main() -> None:
    costs = CostModel()
    universe = load_prices(UNIVERSE, START, END)
    bench = load_prices([BENCHMARK], START, END)

    strat = run_backtest(universe, Momentum(lookback=252, skip=21, top_n=3), "M", costs)
    base = run_backtest(bench, BuyAndHold(), "Q", costs)

    table = compare({"momentum_top3": strat.equity, "kodex200_hold": base.equity})
    print(table.to_string(float_format=lambda x: f"{x:,.3f}"))
    print(f"\n모멘텀 거래 {len(strat.trades)}건, 비용 합계 {strat.total_cost:,.0f}원, "
          f"연 회전율 {strat.turnover:.1f}배")


if __name__ == "__main__":
    main()
