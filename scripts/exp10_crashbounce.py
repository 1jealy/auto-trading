"""실험 #10: 1분 급락(평소의 6배) 직후 30분 보유. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp10_crashbounce.py judge       # 판단 기간 2017-10 ~ 2023-12 (딱 한 번)
    python scripts/exp10_crashbounce.py reference   # 참고: 2024-01 ~ 2026-10 (이미 본 기간)

1분 데이터는 먼저 scripts/download_upbit_minutes.py KRW-BTC 2017-09 로 받아 둘 것.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from autotrade.crashbounce import crash_signals, fixed_signals, simulate, t_value  # noqa: E402
from autotrade.takeprofit import minute_grid  # noqa: E402
from autotrade.upbit import load_minutes  # noqa: E402

STORE = ROOT / "data" / "cache" / "crypto" / "upbit"
MARKET = "KRW-BTC"
K, WINDOW, HOLD = 6.0, 1440, 30
COST = 0.0015  # 한쪽 0.15%
COST_UPBIT = 0.00065  # 참고: 업비트 기본(#5 측정)
PERIODS = {
    "judge": ("2017-10-01", "2023-12-31"),
    "reference": ("2024-01-01", "2026-10-31"),
}
HALVES = [("2017-10-01", "2020-12-31"), ("2021-01-01", "2023-12-31")]


def describe(tr: pd.DataFrame, label: str) -> dict:
    r = tr["ret"]
    if r.empty:
        return {"구분": label, "거래": 0}
    eq = (1 + r).cumprod()
    return {
        "구분": label, "거래": len(r), "건당 평균": f"{r.mean():+.3%}", "가운데 값": f"{r.median():+.3%}",
        "이긴 비율": f"{(r > 0).mean():.0%}", "t값": f"{t_value(r):.2f}",
        "누적": f"{eq.iloc[-1] - 1:+.1%}", "최대 하락": f"{(eq / eq.cummax() - 1).min():.1%}",
        "최고/최저 거래": f"{r.max():+.1%} / {r.min():+.1%}",
    }


def main(which: str) -> None:
    start, end = PERIODS[which]
    load_from = (pd.Timestamp(start) - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    m = load_minutes(STORE, MARKET, load_from, end + " 23:59")
    g = minute_grid(m)
    print(f"[{which}] {start} ~ {end} (UTC) — 데이터 {m.index.min()} ~ {m.index.max()}, 캔들 {len(m):,}개\n")

    sig = crash_signals(g, K, WINDOW)
    main_tr = simulate(g, sig, HOLD, COST, start=start, end=end)
    rows = [describe(main_tr, "규칙 (6배, 30분, 한쪽 0.15%)")]
    if which == "judge":
        for a, b in HALVES:
            rows.append(describe(main_tr[(main_tr.signal >= a) & (main_tr.signal < pd.Timestamp(b) + pd.Timedelta(days=1))],
                                 f"  {a[:4]}~{b[:4]}"))
    print(pd.DataFrame(rows).set_index("구분").to_string())

    if which == "judge":
        r = main_tr["ret"]
        halves = [main_tr[(main_tr.signal >= a) & (main_tr.signal < pd.Timestamp(b) + pd.Timedelta(days=1))]["ret"].mean()
                  for a, b in HALVES]
        print("\n기준 확인:")
        print(f"  1. 거래 {len(r)}건 ≥ 100 → {'통과' if len(r) >= 100 else '미달'}")
        print(f"  2. 건당 평균 {r.mean():+.3%} > 0 → {'통과' if r.mean() > 0 else '미달'}")
        print(f"  3. t값 {t_value(r):.2f} ≥ 2.5 → {'통과' if t_value(r) >= 2.5 else '미달'}")
        print(f"  4. 앞 절반 {halves[0]:+.3%}, 뒤 절반 {halves[1]:+.3%} 둘 다 > 0 → "
              f"{'통과' if min(halves) > 0 else '미달'}")

    print("\n연도별 (규칙):")
    by = main_tr.groupby(main_tr.signal.dt.year)["ret"]
    print(pd.DataFrame({"거래": by.size(), "건당 평균": by.mean().map("{:+.3%}".format),
                        "비용 전 평균": main_tr.groupby(main_tr.signal.dt.year)["gross"].mean().map("{:+.3%}".format)}).to_string())

    days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    inside = len(main_tr) * HOLD / (days * 1440)
    c0 = g["CloseF"].loc[start:].dropna().iloc[0]
    c1 = g["CloseF"].loc[:end + " 23:59"].dropna().iloc[-1]
    print(f"\n돈이 들어가 있던 시간 {inside:.2%}. 비교: 비트코인 그냥 보유 {c1 / c0 - 1:+.1%}")
    print(f"비용 전 건당 평균 {main_tr['gross'].mean():+.3%}")

    print("\n참고 (판단 안 함):")
    ref = [
        describe(simulate(g, sig, HOLD, COST_UPBIT, start=start, end=end), "업비트 기본 비용 0.065%"),
        describe(simulate(g, fixed_signals(g, 0.005), HOLD, COST, start=start, end=end), "0.5% 고정 기준"),
        describe(simulate(g, sig, 10, COST, start=start, end=end), "10분 보유"),
        describe(simulate(g, sig, 60, COST, start=start, end=end), "60분 보유"),
    ]
    print(pd.DataFrame(ref).set_index("구분").to_string())


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in PERIODS:
        raise SystemExit(f"사용법: {list(PERIODS)} 중 하나")
    main(which)
