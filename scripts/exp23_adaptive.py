"""실험 #23: 덩이 매수 + 코인별 움직임 폭(U) 기준 손절·익절·추적 손절·추세 이탈. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp23_adaptive.py practice             # 연습 기간 후보 8개 → 고르는 기준대로 하나 선택
    python scripts/exp23_adaptive.py validation T2        # 고른 후보로 검증 기간 딱 한 번
자료·기간·지표는 exp22_lots.py 를 그대로 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

import exp22_lots as e22  # noqa: E402
from autotrade.lots import AdaptiveRules, daily_unit, hold_equity, simulate_adaptive  # noqa: E402

MA = 60 * 6  # 4시간봉 60일 = 360개
VARIANTS = {
    "F1": dict(stop_mult=0.5, target_mult=3.0), "F2": dict(stop_mult=1.0, target_mult=3.0),
    "F3": dict(stop_mult=1.0, target_mult=6.0), "F4": dict(stop_mult=2.0, target_mult=6.0),
    "T1": dict(stop_mult=1.0, trail_mult=2.0), "T2": dict(stop_mult=1.0, trail_mult=3.0),
    "M1": dict(stop_mult=1.0, exit_ma_bars=20 * 6), "M2": dict(stop_mult=1.0, exit_ma_bars=60 * 6),
}
OUT = ROOT / "results" / "exp23"


def setup():
    hourly = e22.load_hourly()
    o, c = e22.frames(hourly, "4h")
    return o, c, daily_unit(c)


def run(name: str, o, c, u, end: str):
    return simulate_adaptive(o, c, u, AdaptiveRules(ma_bars=MA, **VARIANTS[name]), end=pd.Timestamp(end))


def practice() -> None:
    o, c, u = setup()
    a, b = e22.PRACTICE
    hold = e22.metrics(e22.window(hold_equity(o, c, pd.Timestamp(a), end=pd.Timestamp(b)), a, b))
    rows = {}
    OUT.mkdir(parents=True, exist_ok=True)
    for name in VARIANTS:
        res = run(name, o, c, u, b)
        rows[name] = e22.describe(res, e22.window(res.equity, a, b), None, a, b)
        t = res.trades[(res.trades["sell_t"] >= a) & (res.trades["sell_t"] <= b)]
        rows[name]["이유"] = t["why"].value_counts().to_dict()
        res.trades.to_csv(OUT / f"practice_{name}_trades.csv", index=False)
    df = pd.DataFrame(rows).T
    pd.set_option("display.width", 250)
    print(f"\n== #23 연습 기간 {a[:10]} ~ {b[:10]} (계좌 가치 기준) ==")
    print(f"그냥 보유: 연평균 {hold['연평균']:.1%} 흔들림 {hold['흔들림']:.1%} 수익/흔들림 {hold['수익/흔들림']:.2f} "
          f"가장 크게 떨어졌을 때 {hold['가장 크게 떨어졌을 때']:.1%}")
    print(df.drop(columns=["이유"]).to_string(float_format=lambda x: f"{x:.3f}"))
    print(df["이유"].to_string())
    best = df["수익/흔들림"].astype(float).idxmax()
    print(f"\n고르는 기준(수익/흔들림 최대)에 따른 선택: {best}  {VARIANTS[best]}")
    (OUT / "practice_summary.json").write_text(json.dumps({"rows": rows, "hold": hold, "selected": best},
                                                         ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def validation(name: str) -> None:
    o, c, u = setup()
    a, b = e22.VALIDATION
    pa, _ = e22.PRACTICE
    res = run(name, o, c, u, b)
    hold = hold_equity(o, c, pd.Timestamp(pa), end=pd.Timestamp(b))
    mine = e22.describe(res, e22.window(res.equity, a, b), None, a, b)
    t = res.trades[(res.trades["sell_t"] >= a) & (res.trades["sell_t"] <= b)]
    base = e22.metrics(e22.window(hold, a, b))
    crit = [mine["수익/흔들림"] > base["수익/흔들림"], mine["가장 크게 떨어졌을 때"] > base["가장 크게 떨어졌을 때"],
            mine["연평균"] > 0]
    print(f"\n== #23 검증 기간 {a[:10]} ~ {b[:10]} · 후보 {name} {VARIANTS[name]} (계좌 가치 기준) ==")
    for k, v in mine.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print("  판 이유:", t["why"].value_counts().to_dict())
    print("  그냥 보유:", {k: round(v, 4) for k, v in base.items()})
    print("  기준 ①수익/흔들림 > 보유  ②가장 크게 떨어졌을 때가 보유보다 작음  ③연평균 > 0 :", crit)
    print("  판정:", "합격" if all(crit) else "탈락")
    OUT.mkdir(parents=True, exist_ok=True)
    res.trades.to_csv(OUT / f"validation_{name}_trades.csv", index=False)
    (OUT / "validation_summary.json").write_text(json.dumps({"variant": name, "mine": mine, "hold": base, "criteria": crit},
                                                           ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    practice() if sys.argv[1] == "practice" else validation(sys.argv[2])
