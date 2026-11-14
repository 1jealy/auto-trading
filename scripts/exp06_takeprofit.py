"""실험 #6: 급락 직후 사서 목표 수익에 팔기. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp06_takeprofit.py practice      # 연습 기간 (2022~2023, UTC)
    python scripts/exp06_takeprofit.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from autotrade.data import load_prices  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.takeprofit import ExitRule, crash_signals, minute_grid, run_daily, run_signals, trades_equity  # noqa: E402
from autotrade.upbit import load_minutes  # noqa: E402

STORE = Path(__file__).resolve().parents[1] / "data" / "cache" / "crypto" / "upbit"
PERIODS = {"practice": ("2022-01-01", "2023-12-31"), "validation": ("2024-01-01", "2026-10-31")}
MAIN, REF, SAFE = "KRW-BTC", "KRW-ETH", "153130"
K_SIGMA = 3.0
RULE = ExitRule(take_profit=0.005, stop_loss=0.02, max_minutes=24 * 60, tp_margin=0.0002, stop_slip=0.001)
COST_A = 0.000109 / 2 + 0.0001  # 수수료 0 (코인원), 실험 #5에서 잰 가격 차이
COST_B = 0.0005 + 0.000099 / 2 + 0.0001  # 업비트 수수료


def load(market: str, start: str, end: str) -> pd.DataFrame:
    # 신호 계산용으로 시작 31일 전부터, 기간 끝까지만 (기간 밖 미래 데이터는 쓰지 않음)
    pre = (pd.Timestamp(start) - pd.Timedelta(days=31)).strftime("%Y-%m-%d")
    return minute_grid(load_minutes(STORE, market, pre, f"{end} 23:59"))


def equity_summary(trades: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    eq = trades_equity(trades, days)
    eq = pd.concat([pd.Series([1.0], index=[days[0] - pd.Timedelta(days=1)]), eq])
    return summarize(eq, periods_per_year=365)


def describe(tr: pd.DataFrame) -> str:
    if tr.empty:
        return "거래 없음"
    parts = []
    for why, grp in tr.groupby("why"):
        parts.append(f"{why} {len(grp)}건(평균 {grp['net'].mean() * 100:+.2f}%)")
    return (f"{len(tr)}건 · 건당 평균 {tr['net'].mean() * 100:+.3f}% · 이긴 비율 {(tr['net'] > 0).mean():.0%} · "
            f"평균 보유 {tr['minutes'].mean() / 60:.1f}시간 · " + ", ".join(parts))


def main(period: str) -> None:
    start, end = PERIODS[period]
    days = pd.date_range(start, end, freq="D")
    g = load(MAIN, start, end)
    sig = crash_signals(g, k=K_SIGMA)
    in_period = sig.loc[start:f"{end} 23:59"]
    print(f"[{period}] {start} ~ {end} (UTC)  KRW-BTC  급락 신호가 켜진 분: {int(in_period.sum()):,}분")

    trades = {name: run_signals(g, sig, c, RULE, start, end) for name, c in (("A", COST_A), ("B", COST_B))}
    base = {name: run_daily(g, c, RULE, start, end, hour=0) for name, c in (("A", COST_A), ("B", COST_B))}

    print("\n[급락 직후 매수]")
    for name in ("A", "B"):
        print(f"  비용 {name}: {describe(trades[name])}")
    print("[비교: 매일 00시(UTC) 매수, 같은 판매 규칙]")
    for name in ("A", "B"):
        print(f"  비용 {name}: {describe(base[name])}")

    res = {
        "급락 매수 A (수수료 0)": equity_summary(trades["A"], days),
        "급락 매수 B (업비트 수수료)": equity_summary(trades["B"], days),
    }
    hold_px = g["Open"].reindex(days).dropna()
    res["비트코인 그냥 보유"] = summarize(hold_px, periods_per_year=365)
    safe = load_prices([SAFE], "2014-08-01", "2026-10-31").close[SAFE].loc[start:end].dropna()
    res["단기채권 보유"] = summarize(safe)
    table = pd.DataFrame(res).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
    shown = table.copy().astype(object)
    for col in shown.columns:
        shown[col] = table[col].map(lambda x: f"{x:.2f}" if col == "sharpe" else f"{x * 100:.1f}%")
    shown.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    print("\n" + shown.to_string())

    print("\n연도별 (급락 매수 A):")
    ta = trades["A"]
    for year in sorted(set(days.year)):
        yt = ta[ta["entry"].dt.year == year]
        yd = days[days.year == year]
        cg = equity_summary(yt, yd)["cagr"] if len(yt) else 0.0
        print(f"  {year}: {len(yt)}건, 건당 평균 {yt['net'].mean() * 100 if len(yt) else 0:+.3f}%, 연평균 {cg * 100:+.1f}%")

    a, b = res["급락 매수 A (수수료 0)"], res["급락 매수 B (업비트 수수료)"]
    bond = res["단기채권 보유"]["cagr"]
    checks = {
        f"1. 신호가 충분히 나나 (30건 이상) — {len(ta)}건": len(ta) >= 30,
        f"2. 신호 효과 (건당 평균 {ta['net'].mean() * 100:+.3f}% > 아무 때나 {base['A']['net'].mean() * 100:+.3f}%)":
            len(ta) > 0 and ta["net"].mean() > base["A"]["net"].mean(),
        "3. 할 만한가 (A 연평균 > 단기채권)": a["cagr"] > bond,
        "4. 수수료가 생겨도 버티나 (B 연평균 > 단기채권)": b["cagr"] > bond,
        "5. 안전 (A 최대 하락폭 > -25%)": a["max_drawdown"] > -0.25,
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    # ── 참고: 이더리움 (판단에 쓰지 않음) ──
    ge = load(REF, start, end)
    te = run_signals(ge, crash_signals(ge, k=K_SIGMA), COST_A, RULE, start, end)
    be = run_daily(ge, COST_A, RULE, start, end)
    print(f"\n[참고] 이더리움 A: 급락 매수 {describe(te)}")
    print(f"                  아무 때나 건당 평균 {be['net'].mean() * 100:+.3f}%")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
