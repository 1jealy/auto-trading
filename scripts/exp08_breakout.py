"""실험 #8: 4시간봉 변동성 응축 돌파 + 60일 추세 필터. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp08_breakout.py practice      # 연습 기간 (참고용)
    python scripts/exp08_breakout.py validation    # 검증 기간 — 판단은 여기서만, 딱 한 번
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from autotrade.breakout import (Params, add_indicators, breakout_positions, equity_from_positions,  # noqa: E402
                                filter_positions, resample_bars)
from autotrade.data import load_prices  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.upbit import load_minutes  # noqa: E402

STORE = Path(__file__).resolve().parents[1] / "data" / "cache" / "crypto" / "upbit"
PERIODS = {"practice": ("2022-01-01", "2023-12-31"), "validation": ("2024-01-01", "2026-10-31")}
MAIN, REF, SAFE = "KRW-BTC", "KRW-ETH", "153130"
P = Params()
COST_A = 0.000109 / 2 + 0.0001
COST_B = 0.0005 + 0.000099 / 2 + 0.0001
WARMUP_DAYS = 200  # 6개월 응축 기준 + 60일 평균


def prepare(market: str, start: str, end: str):
    pre = (pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    d = add_indicators(resample_bars(load_minutes(STORE, market, pre, f"{end} 23:59"), "4h"), P)
    ready = d.index.get_loc(d["bw_rank"].first_valid_index())  # 응축 기준(6개월)이 처음 계산되는 캔들
    first = max(int(d.index.searchsorted(pd.Timestamp(start))), int(ready))
    last = len(d)
    return d, first, last


def daily(eq: pd.Series, start: str, end: str) -> pd.Series:
    e = eq.resample("D").last().loc[start:end].dropna()
    return e / e.iloc[0]


def describe(tr: pd.DataFrame) -> str:
    if tr.empty:
        return "거래 없음"
    return (f"{len(tr)}건 · 이긴 비율 {(tr['net'] > 0).mean():.0%} · 건당 평균 {tr['net'].mean() * 100:+.2f}% · "
            f"가장 큰 이익 {tr['net'].max() * 100:+.1f}% · 가장 큰 손실 {tr['net'].min() * 100:+.1f}% · "
            f"평균 보유 {tr['bars'].mean() * 4 / 24:.1f}일")


def main(period: str) -> None:
    start, end = PERIODS[period]
    d, first, last = prepare(MAIN, start, end)
    print(f"[{period}] {start} ~ {end} (UTC)  KRW-BTC 4시간 캔들 · 신호 계산 시작 {d.index[first]}")

    pos_b = breakout_positions(d, first, last, P)
    pos_f = filter_positions(d, first, last)
    runs = {
        "돌파 A (수수료 0)": equity_from_positions(d, pos_b, COST_A),
        "돌파 B (업비트 수수료)": equity_from_positions(d, pos_b, COST_B),
        "60일선 위에서만 B": equity_from_positions(d, pos_f, COST_B),
    }
    for name, (_, tr) in runs.items():
        print(f"  {name}: {describe(tr)}")
    in_period = pos_b[first:last]
    print(f"  투자해 있던 시간: 돌파 {in_period.mean():.0%}, 60일선 필터 {pos_f[first:last].mean():.0%}")

    res = {name: summarize(daily(eq, start, end), periods_per_year=365) for name, (eq, _) in runs.items()}
    hold = d["Open"].resample("D").first().loc[start:end].dropna()
    res["비트코인 그냥 보유"] = summarize(hold, periods_per_year=365)
    safe = load_prices([SAFE], "2014-08-01", "2026-10-31").close[SAFE].loc[start:end].dropna()
    res["단기채권 보유"] = summarize(safe)
    table = pd.DataFrame(res).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
    shown = table.copy().astype(object)
    for col in shown.columns:
        shown[col] = table[col].map(lambda x: f"{x:.2f}" if col == "sharpe" else f"{x * 100:.1f}%")
    shown.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    print("\n" + shown.to_string())

    print("\n연도별 (수익률):")
    for year in sorted(set(pd.date_range(start, end).year)):
        ys, ye = f"{year}-01-01", f"{year}-12-31"
        row = []
        for name in ("돌파 B (업비트 수수료)", "60일선 위에서만 B"):
            e = daily(runs[name][0], start, end).loc[ys:ye]
            row.append(f"{name.split(' ')[0]} {(e.iloc[-1] / e.iloc[0] - 1) * 100:+.1f}%" if len(e) > 1 else "")
        h = hold.loc[ys:ye]
        row.append(f"그냥 보유 {(h.iloc[-1] / h.iloc[0] - 1) * 100:+.1f}%")
        ntr = runs["돌파 B (업비트 수수료)"][1]
        row.append(f"거래 {int((ntr['entry'].dt.year == year).sum())}건")
        print(f"  {year}: " + ", ".join(row))

    b, f, h, s = (res[k] for k in ("돌파 B (업비트 수수료)", "60일선 위에서만 B", "비트코인 그냥 보유", "단기채권 보유"))
    trades_b = runs["돌파 B (업비트 수수료)"][1]
    checks = {
        f"1. 거래 15건 이상 — {len(trades_b)}건": len(trades_b) >= 15,
        "2. 비용 B 연평균 > 단기채권": b["cagr"] > s["cagr"],
        "3. 비용 B 수익/흔들림 > 비트코인 그냥 보유": b["sharpe"] > h["sharpe"],
        "4. 비용 B 수익/흔들림 > 60일선 위에서만 보유(B)": b["sharpe"] > f["sharpe"],
        "5. 비용 B 최대 하락폭 > -30%": b["max_drawdown"] > -0.30,
    }
    label = "통과 기준 (판단용)" if period == "validation" else "통과 기준 (연습 기간이라 참고용 — 판단은 검증 기간에서만)"
    print(f"\n{label}:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    de, fe, le = prepare(REF, start, end)
    eqe, tre = equity_from_positions(de, breakout_positions(de, fe, le, P), COST_B)
    se = summarize(daily(eqe, start, end), periods_per_year=365)
    print(f"\n[참고] 이더리움 B: {describe(tre)} · 연평균 {se['cagr'] * 100:+.1f}%, 최대 하락 {se['max_drawdown'] * 100:.1f}%")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
