"""실험 #8 추가 시험: 현실 비용(한쪽 0.15%) + 처음 보는 2017~2021년. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp08r_realistic.py early        # 2017-10 ~ 2021-12 (판단의 중심)
    python scripts/exp08r_realistic.py validation   # 2024-01 ~ 2026-10 현실 비용 재계산 (확인용)

신호 규칙은 exp08_breakout.py 의 것을 그대로 쓴다(바꾸지 않음).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

import exp08_breakout as base  # noqa: E402  (신호 규칙·준비·평가 함수)
from autotrade.breakout import breakout_positions, equity_from_positions, filter_positions  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402

PERIODS = {"early": ("2017-10-01", "2021-12-31"), "validation": ("2024-01-01", "2026-10-31")}
COST_REAL = 0.0005 + 0.0010  # 수수료 0.05% + 체결 손해 0.10%
COST_OLD_B = base.COST_B  # 참고용 (기존 비용 B)


def evaluate(market: str, start: str, end: str, cost: float) -> tuple[dict, dict]:
    d, first, last = base.prepare(market, start, end)
    pos_b = breakout_positions(d, first, last, base.P)
    pos_f = filter_positions(d, first, last)
    runs = {"돌파": equity_from_positions(d, pos_b, cost), "60일선 위에서만": equity_from_positions(d, pos_f, cost)}
    stats = {k: summarize(base.daily(eq, start, end), periods_per_year=365) for k, (eq, _) in runs.items()}
    hold = d["Open"].resample("D").first().loc[start:end].dropna()
    stats["비트코인 그냥 보유"] = summarize(hold, periods_per_year=365)
    info = dict(first=d.index[first], trades=runs["돌파"][1], trades_f=runs["60일선 위에서만"][1],
                invested=pos_b[first:last].mean(), invested_f=pos_f[first:last].mean(), runs=runs, hold=hold)
    return stats, info


def main(period: str) -> None:
    start, end = PERIODS[period]
    stats, info = evaluate(base.MAIN, start, end, COST_REAL)
    safe = load_prices([base.SAFE], "2014-08-01", "2026-10-31").close[base.SAFE].loc[start:end].dropna()
    stats["단기채권 보유"] = summarize(safe)

    print(f"[{period}] {start} ~ {end} (UTC)  KRW-BTC 4시간 캔들 · 한쪽 비용 {COST_REAL * 100:.2f}% · 신호 계산 시작 {info['first']}")
    print(f"  돌파: {base.describe(info['trades'])}")
    print(f"  60일선 위에서만: {base.describe(info['trades_f'])}")
    print(f"  투자해 있던 시간: 돌파 {info['invested']:.0%}, 60일선 필터 {info['invested_f']:.0%}")

    table = pd.DataFrame(stats).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
    shown = table.copy().astype(object)
    for col in shown.columns:
        shown[col] = table[col].map(lambda x: f"{x:.2f}" if col == "sharpe" else f"{x * 100:.1f}%")
    shown.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    print("\n" + shown.to_string())

    print("\n연도별 (수익률):")
    for year in sorted(set(pd.date_range(start, end).year)):
        ys, ye = f"{year}-01-01", f"{year}-12-31"
        parts = []
        for name in ("돌파", "60일선 위에서만"):
            e = base.daily(info["runs"][name][0], start, end).loc[ys:ye]
            parts.append(f"{name} {(e.iloc[-1] / e.iloc[0] - 1) * 100:+.1f}%" if len(e) > 1 else f"{name} —")
        h = info["hold"].loc[ys:ye]
        parts.append(f"그냥 보유 {(h.iloc[-1] / h.iloc[0] - 1) * 100:+.1f}%" if len(h) > 1 else "그냥 보유 —")
        parts.append(f"거래 {int((info['trades']['entry'].dt.year == year).sum())}건")
        print(f"  {year}: " + ", ".join(parts))

    b, f, h, s = (stats[k] for k in ("돌파", "60일선 위에서만", "비트코인 그냥 보유", "단기채권 보유"))
    checks = {
        f"1. 거래 15건 이상 — {len(info['trades'])}건": len(info["trades"]) >= 15,
        "2. 연평균 > 단기채권": b["cagr"] > s["cagr"],
        "3. 수익/흔들림 > 비트코인 그냥 보유": b["sharpe"] > h["sharpe"],
        "4. 수익/흔들림 > 60일선 위에서만 보유 (같은 비용)": b["sharpe"] > f["sharpe"],
        "5. 최대 하락폭 > -30%": b["max_drawdown"] > -0.30,
    }
    print("\n통과 기준 (현실 비용):")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    old, _ = evaluate(base.MAIN, start, end, COST_OLD_B)
    print(f"\n[참고] 기존 비용 B({COST_OLD_B * 100:.3f}%)라면: 돌파 연 {old['돌파']['cagr'] * 100:+.1f}%·수익/흔들림 {old['돌파']['sharpe']:.2f}, "
          f"60일선 연 {old['60일선 위에서만']['cagr'] * 100:+.1f}%·{old['60일선 위에서만']['sharpe']:.2f}")
    try:
        es, ei = evaluate(base.REF, start, end, COST_REAL)
        print(f"[참고] 이더리움(현실 비용): {base.describe(ei['trades'])} · 연 {es['돌파']['cagr'] * 100:+.1f}%, "
              f"수익/흔들림 {es['돌파']['sharpe']:.2f}, 최대 하락 {es['돌파']['max_drawdown'] * 100:.1f}% "
              f"(그냥 보유 수익/흔들림 {es['비트코인 그냥 보유']['sharpe']:.2f})")
    except Exception as e:  # noqa: BLE001
        print(f"[참고] 이더리움 계산 못함: {e}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "early"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
