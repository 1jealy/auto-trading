"""실험 #7: 여러 차트 지표를 합친 단기 매매 (1시간 캔들). 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp07_confluence.py practice      # 연습 기간 (2022~2023, UTC)
    python scripts/exp07_confluence.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from autotrade.confluence import Rules, add_indicators, entry_signal, simulate  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.indicators import hourly_bars  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.takeprofit import trades_equity  # noqa: E402
from autotrade.upbit import load_minutes  # noqa: E402

STORE = Path(__file__).resolve().parents[1] / "data" / "cache" / "crypto" / "upbit"
PERIODS = {"practice": ("2022-01-01", "2023-12-31"), "validation": ("2024-01-01", "2026-10-31")}
MAIN, REF, SAFE = "KRW-BTC", "KRW-ETH", "153130"
RULES = Rules()
COST_A = 0.000109 / 2 + 0.0001
COST_B = 0.0005 + 0.000099 / 2 + 0.0001


def bars_for(market: str, start: str, end: str) -> pd.DataFrame:
    # 기간 시작 이전 데이터는 지표 준비용으로만 (연습 기간은 2022-01부터라 앞 데이터 없음 → 첫 사흘 신호 안 냄)
    pre = (pd.Timestamp(start) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    return add_indicators(hourly_bars(load_minutes(STORE, market, pre, f"{end} 23:59")), RULES)


def equity_summary(trades: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    eq = trades_equity(trades, days)
    eq = pd.concat([pd.Series([1.0], index=[days[0] - pd.Timedelta(days=1)]), eq])
    return summarize(eq, periods_per_year=365)


def describe(tr: pd.DataFrame) -> str:
    if tr.empty:
        return "거래 없음"
    why = ", ".join(f"{k} {len(g)}건(평균 {g['net'].mean() * 100:+.2f}%)" for k, g in tr.groupby("why"))
    return (f"{len(tr)}건 · 건당 평균 {tr['net'].mean() * 100:+.3f}% · 이긴 비율 {(tr['net'] > 0).mean():.0%} · "
            f"평균 보유 {tr['hours'].mean():.1f}시간 · {why}")


def edge(d: pd.DataFrame, tr: pd.DataFrame, start: str, end: str) -> tuple[float, float]:
    """(전략 비용 전 로그수익 합, 같은 시간 아무 때나 들고 있었을 때 기대값)"""
    p = d.loc[start:f"{end} 23:59", "Close"]
    mu = float(np.log(p / p.shift(1)).dropna().mean())
    return float(tr["gross_log"].sum()), mu * float(tr["hours"].sum())


def main(period: str) -> None:
    start, end = PERIODS[period]
    days = pd.date_range(start, end, freq="D")
    d = bars_for(MAIN, start, end)
    n_sig = int(entry_signal(d, RULES).loc[start:f"{end} 23:59"].sum())
    print(f"[{period}] {start} ~ {end} (UTC)  KRW-BTC 1시간 캔들 {len(d.loc[start:f'{end} 23:59']):,}개, 사는 신호 {n_sig}번")

    tr = {k: simulate(d, c, RULES, start, end) for k, c in (("A", COST_A), ("B", COST_B))}
    for k in ("A", "B"):
        print(f"  비용 {k}: {describe(tr[k])}")

    got, expected = edge(d, tr["A"], start, end)
    print(f"\n신호 효과(비용 전): 전략 로그수익 합 {got * 100:+.2f}%  vs  같은 시간 아무 때나 {expected * 100:+.2f}%")

    res = {"지표 종합 A (수수료 0)": equity_summary(tr["A"], days),
           "지표 종합 B (업비트 수수료)": equity_summary(tr["B"], days)}
    hold = d["Open"].reindex(days).dropna()
    res["비트코인 그냥 보유"] = summarize(hold, periods_per_year=365)
    safe = load_prices([SAFE], "2014-08-01", "2026-10-31").close[SAFE].loc[start:end].dropna()
    res["단기채권 보유"] = summarize(safe)
    table = pd.DataFrame(res).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
    shown = table.copy().astype(object)
    for col in shown.columns:
        shown[col] = table[col].map(lambda x: f"{x:.2f}" if col == "sharpe" else f"{x * 100:.1f}%")
    shown.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    print("\n" + shown.to_string())

    print("\n연도별 (비용 A):")
    ta = tr["A"]
    for year in sorted(set(days.year)):
        yt = ta[ta["entry"].dt.year == year]
        yd = days[days.year == year]
        cg = equity_summary(yt, yd)["cagr"] if len(yt) else 0.0
        hp = hold[hold.index.year == year]
        print(f"  {year}: {len(yt)}건, 건당 평균 {(yt['net'].mean() if len(yt) else 0) * 100:+.3f}%, "
              f"연평균 {cg * 100:+.1f}%, 비트코인 그냥 보유 {(hp.iloc[-1] / hp.iloc[0] - 1) * 100:+.1f}%")

    a, b = res["지표 종합 A (수수료 0)"], res["지표 종합 B (업비트 수수료)"]
    bond = res["단기채권 보유"]["cagr"]
    checks = {
        f"1. 거래 30건 이상 — {len(ta)}건": len(ta) >= 30,
        f"2. 신호 효과 (비용 전 {got * 100:+.2f}% > 아무 때나 {expected * 100:+.2f}%)": got > expected,
        "3. 비용 A 연평균 > 단기채권": a["cagr"] > bond,
        "4. 비용 B 연평균 > 단기채권": b["cagr"] > bond,
        "5. 비용 A 최대 하락폭 > -25%": a["max_drawdown"] > -0.25,
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    de = bars_for(REF, start, end)
    te = simulate(de, COST_A, RULES, start, end)
    g2, e2 = edge(de, te, start, end)
    print(f"\n[참고] 이더리움 A: {describe(te)}")
    print(f"                  신호 효과 {g2 * 100:+.2f}% vs 아무 때나 {e2 * 100:+.2f}%")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
