"""실험 #4: 밤에만 들고 있기. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp04_overnight.py practice      # 연습 기간
    python scripts/exp04_overnight.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from autotrade import CostModel, compare  # noqa: E402
from autotrade.data import load_ohlc  # noqa: E402
from autotrade.overnight import annualized, night_day_returns, simulate  # noqa: E402

MAIN, REF, SAFE = "069500", "229200", "153130"  # KODEX 200, KODEX 코스닥150(참고), 단기채권
DATA_START, DATA_END = "2014-08-01", "2026-10-31"
PERIODS = {
    "practice": ("2015-09-01", "2020-12-31"),
    "validation": ("2021-01-01", "2026-10-31"),
}
COSTS = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.0001)  # 동시호가, ETF


def main(period: str) -> None:
    start, end = PERIODS[period]
    data = {t: load_ohlc(t, DATA_START, DATA_END).loc[:end] for t in (MAIN, REF, SAFE)}  # 이후 데이터는 버림
    kodex = data[MAIN].dropna(subset=["Open", "High", "Close"])

    wait_eq, wait_tr = simulate(kodex, COSTS, mode="wait")
    pure_eq, pure_tr = simulate(kodex, COSTS, mode="pure")
    curves = {
        "밤에만 (수정 규칙)": wait_eq,
        "밤에만 (원래 규칙, 참고)": pure_eq,
        "KODEX200 그냥 보유": kodex["Close"],
        "단기채권 보유": data[SAFE]["Close"].dropna(),
    }
    table = compare({k: v.loc[start:end] for k, v in curves.items()})
    print(f"[{period}] {start} ~ {end}  (KODEX 200)\n")
    print(_pretty(table))

    nd = night_day_returns(kodex).loc[start:end]
    night, day = annualized(nd["night"]), annualized(nd["day"])
    per_night = (1 + nd["night"]).prod() ** (1 / len(nd)) - 1
    breakeven = 1 - 1 / (1 + per_night)
    print(f"\n비용 빼기 전 — 밤 동안 연평균 {night:+.1%}, 낮 동안 연평균 {day:+.1%}")
    print(f"원래 규칙이 본전이 되는 왕복 비용: {breakeven:.3%} (이번 가정 {COSTS.buy_rate + COSTS.sell_rate:.3%})")

    w = wait_tr[(wait_tr["sell_date"] >= start) & (wait_tr["sell_date"] <= end)]
    print(f"\n수정 규칙 매매 {len(w)}건:")
    for kind, g in w.groupby("kind"):
        print(f"  {kind}: {len(g)}건, 평균 {g['ret'].mean():+.3%}, 가장 나쁜 {g['ret'].min():+.2%}")

    s, k, b = table.loc["밤에만 (수정 규칙)"], table.loc["KODEX200 그냥 보유"], table.loc["단기채권 보유"]
    checks = {
        "1. 밤 효과가 있나 (비용 전 밤 > 낮)": night > day,
        "2. 비용을 이기나 (연평균 > 단기채권)": s["cagr"] > b["cagr"],
        "3. 그냥 보유보다 나은가 (수익/흔들림 > KODEX200)": s["sharpe"] > k["sharpe"],
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    ref = data[REF].dropna(subset=["Open", "High", "Close"])
    rnd = night_day_returns(ref).loc[start:end]
    ref_eq, _ = simulate(ref, COSTS, mode="wait")
    ref_tab = compare({"x": ref_eq.loc[start:end]})
    print(f"\n[참고] 코스닥150 ({rnd.index.min():%Y-%m}~): 밤 {annualized(rnd['night']):+.1%}, "
          f"낮 {annualized(rnd['day']):+.1%}, 수정 규칙 연평균 {ref_tab.loc['x', 'cagr']:+.1%}")

    _warn_open_glitches(kodex, start, end)


def _warn_open_glitches(df: pd.DataFrame, start: str, end: str) -> None:
    prev = df["Close"].shift(1)
    gap = (df["Open"] / prev - 1).abs()
    move = (df["Close"] / prev - 1).abs()
    bad = df.index[(gap > 0.03) & (move < 0.015)]
    bad = bad[(bad >= start) & (bad <= end)]
    if len(bad):
        print(f"\n경고: 시가만 3% 넘게 튀고 종가는 제자리인 날 {len(bad)}일:", [d.date() for d in bad[:10]])


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
