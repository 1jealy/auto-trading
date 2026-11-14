"""실험 #5: 비트코인 새벽 2시간 (시간대 효과). 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp05_timeofday.py practice      # 연습 기간 (2022~2023, UTC)
    python scripts/exp05_timeofday.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from autotrade.data import load_prices  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.timeofday import after_cost, breakeven_side_cost, daily_equity, fill_forward_opens, window_returns  # noqa: E402
from autotrade.upbit import load_minutes  # noqa: E402

STORE = Path(__file__).resolve().parents[1] / "data" / "cache" / "crypto" / "upbit"
PERIODS = {"practice": ("2022-01-01", "2023-12-31"), "validation": ("2024-01-01", "2026-10-31")}
MAIN, REF = "KRW-BTC", "KRW-ETH"
START_HOUR, HOLD = 21, 2  # UTC 21:00 매수, 23:00 매도 (한국 06~08시)
SAFE = "153130"

# 측정한 가격 차이(중간값)로 미리 정한 공식: 한쪽 비용
COST_A = 0.000109 / 2 + 0.0001  # 수수료 0 (코인원)
COST_B = 0.0005 + 0.000099 / 2 + 0.0001  # 업비트 수수료
# 참고: 가격 차이 평균값을 넣었을 때 (판단에는 쓰지 않음)
COST_A_MEAN = 0.000128 / 2 + 0.0001
COST_B_MEAN = 0.0005 + 0.000212 / 2 + 0.0001


def equity_stats(trades: pd.Series, days: pd.DatetimeIndex) -> dict:
    eq = daily_equity(trades, days)
    eq = pd.concat([pd.Series([1.0], index=[days[0] - pd.Timedelta(days=1)]), eq])
    return summarize(eq, periods_per_year=365)


def pct(x: float, d: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{d}f}%"


def main(period: str) -> None:
    start, end = PERIODS[period]
    days = pd.date_range(start, end, freq="D")
    m = load_minutes(STORE, MAIN, start, f"{end} 23:59")  # 기간 밖 데이터는 쓰지 않는다
    filled = fill_forward_opens(m["Open"])

    # ── 데이터 점검 ──
    per_day = m["Open"].resample("D").count()
    jumps = m["Close"].pct_change().abs()
    print(f"[{period}] {start} ~ {end} (UTC)  KRW-BTC 1분 캔들 {len(m):,}개")
    print(f"  하루 캔들 수: 중간값 {per_day.median():.0f} / 최소 {per_day.min()} ({per_day.idxmin().date()})"
          f"  · 1분 사이 10% 넘는 변화 {int((jumps > 0.10).sum())}번")

    # ── 기준 1: 24개 시간대 ──
    rows = []
    for h in range(24):
        w = window_returns(filled, h, HOLD, days)
        rows.append(dict(시작=f"{h:02d}시(한국 {(h + 9) % 24:02d}시)", h=h, 건수=len(w),
                         평균=w["ret"].mean(), 이긴비율=(w["ret"] > 0).mean(),
                         누적=float((1 + w["ret"]).prod() - 1)))
    rank = pd.DataFrame(rows).sort_values("평균", ascending=False).reset_index(drop=True)
    rank.index = rank.index + 1
    my_rank = int(rank.index[rank["h"] == START_HOUR][0])
    show = rank.drop(columns="h").copy()
    show["평균"] = show["평균"].map(lambda x: f"{x * 100:+.4f}%")
    show["이긴비율"] = show["이긴비율"].map(lambda x: f"{x:.0%}")
    show["누적"] = show["누적"].map(lambda x: f"{x * 100:+.1f}%")
    print("\n24개 시간대 (정각에 사서 2시간 뒤 팔기, 비용 전) — 평균 높은 순")
    print(show.to_string())

    # ── 전략 ──
    w = window_returns(filled, START_HOUR, HOLD, days)
    gross = w["ret"]
    skipped = len(days) - len(w)
    res = {
        "A (수수료 0)": equity_stats(after_cost(gross, COST_A), days),
        "B (업비트 수수료)": equity_stats(after_cost(gross, COST_B), days),
        "비용 전 (참고)": equity_stats(gross, days),
    }
    hold_px = filled.reindex(days).dropna()
    res["비트코인 그냥 보유"] = summarize(hold_px, periods_per_year=365)
    safe = load_prices([SAFE], "2014-08-01", "2026-10-31").close[SAFE].loc[start:end].dropna()
    res["단기채권 보유"] = summarize(safe)

    table = pd.DataFrame(res).T[["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]]
    shown = table.copy().astype(object)
    for col in shown.columns:
        shown[col] = table[col].map(lambda x: f"{x:.2f}" if col == "sharpe" else f"{x * 100:.1f}%")
    shown.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    print(f"\n전략: 매일 UTC {START_HOUR}:00 매수 → {START_HOUR + HOLD}:00 매도  (거래 {len(w)}일, 쉰 날 {skipped}일)")
    print(f"한쪽 비용 A {COST_A * 100:.4f}%, B {COST_B * 100:.4f}%")
    print(shown.to_string())

    be = breakeven_side_cost(gross)
    print(f"\n본전이 되는 한쪽 비용: {be * 100:.4f}%  (왕복 약 {2 * be * 100:.3f}%)")
    print(f"참고(판단 안 함) — 가격 차이 평균값 넣으면: A {pct(equity_stats(after_cost(gross, COST_A_MEAN), days)['cagr'])}/년, "
          f"B {pct(equity_stats(after_cost(gross, COST_B_MEAN), days)['cagr'])}/년")

    print("\n연도별 (연평균 수익률):")
    for year in sorted(set(days.year)):
        yd = days[days.year == year]
        g = gross[gross.index.year == year]
        hp = hold_px[hold_px.index.year == year]
        print(f"  {year}: A {pct(equity_stats(after_cost(g, COST_A), yd)['cagr'])}, "
              f"B {pct(equity_stats(after_cost(g, COST_B), yd)['cagr'])}, "
              f"비용 전 {pct(equity_stats(g, yd)['cagr'])}, 그냥 보유 {pct(summarize(hp, 365)['total_return'])}")

    a, b = res["A (수수료 0)"], res["B (업비트 수수료)"]
    checks = {
        f"1. 시간대 효과 (21~23시가 24개 중 6위 안, 평균 > 0) — 지금 {my_rank}위, 평균 {gross.mean() * 100:+.4f}%":
            my_rank <= 6 and gross.mean() > 0,
        "2. 비용을 이기나 (A 연평균 > 단기채권)": a["cagr"] > res["단기채권 보유"]["cagr"],
        "3. 수수료가 생겨도 버티나 (B 연평균 > 단기채권)": b["cagr"] > res["단기채권 보유"]["cagr"],
        "4. 그냥 보유보다 나은가 (A 수익/흔들림 > 비트코인 보유)": a["sharpe"] > res["비트코인 그냥 보유"]["sharpe"],
    }
    print("\n통과 기준:")
    for name, ok in checks.items():
        print(f"  {'통과' if ok else '미달'}  {name}")

    # ── 참고: 이더리움 (판단에 쓰지 않음) ──
    me = load_minutes(STORE, REF, start, f"{end} 23:59")
    fe = fill_forward_opens(me["Open"])
    means = {h: window_returns(fe, h, HOLD, days)["ret"].mean() for h in range(24)}
    eth_rank = sorted(means, key=means.get, reverse=True).index(START_HOUR) + 1
    ge = window_returns(fe, START_HOUR, HOLD, days)["ret"]
    print(f"\n[참고] 이더리움: 21~23시 {eth_rank}위, 평균 {ge.mean() * 100:+.4f}%, "
          f"A 연평균 {pct(equity_stats(after_cost(ge, COST_A), days)['cagr'])}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if which not in PERIODS:
        raise SystemExit(f"기간은 {list(PERIODS)} 중 하나")
    main(which)
