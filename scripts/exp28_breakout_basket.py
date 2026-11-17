"""#28 #8 보강판: 돌파로 사고 60일선 아래에서만 팔기, 2020-12 거래대금 상위 10개 코인(BTC·ETH 제외) 나눠 담기.

    python scripts/exp28_breakout_basket.py universe    # 코인 고르기(가격·수익은 안 봄)
    python scripts/exp28_breakout_basket.py run         # 판정 기간 2021-01-01 ~ 2026-10-31 딱 한 번
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

from autotrade.breakout import (add_indicators, breakout_positions, breakout_trend_positions,  # noqa: E402
                                equity_from_positions, filter_positions)
from autotrade.public_rules import metrics  # noqa: E402
from autotrade.upbit import _candles_between, days_between  # noqa: E402

CACHE = ROOT / "data" / "cache" / "exp28"
OUT = ROOT / "results" / "exp28"
EXCLUDE = {"BTC", "ETH", "USDT", "USDC", "DAI", "TUSD", "BUSD"}
START, END = "2021-01-01", "2026-10-31 23:59"
COST = 0.0015


def universe() -> list[str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / "universe.json"
    if f.exists():
        return json.loads(f.read_text())["coins"]
    markets = [m["market"] for m in requests.get("https://api.upbit.com/v1/market/all", timeout=15).json() if m["market"].startswith("KRW-")]
    value = {}
    for m in markets:
        c = m[4:]
        if c in EXCLUDE:
            continue
        d = days_between(m, datetime(2020, 12, 1), datetime(2021, 1, 1))
        if len(d):
            value[c] = float(d["Value"].sum())
    coins = sorted(value, key=lambda c: -value[c])[:10]
    f.write_text(json.dumps({"coins": coins, "dec2020_value": {c: value[c] for c in coins}}, ensure_ascii=False, indent=1))
    return coins


def bars(coin: str) -> pd.DataFrame:
    f = CACHE / f"{coin}_240.csv"
    if f.exists():
        return pd.read_csv(f, index_col=0, parse_dates=True)
    d = _candles_between("/candles/minutes/240", f"KRW-{coin}", datetime(2020, 6, 1), datetime(2026, 11, 1), None, 0.12)
    d.to_csv(f)
    return d


def run() -> None:
    coins = universe()
    print("코인 10개:", ", ".join(coins))
    curves = {"#28 돌파+60일선 매도": {}, "㉡ 60일선 위에서만": {}, "㉢ #8 원래(추적 손절)": {}, "㉠ 그냥 보유": {}}
    trades = {k: 0 for k in curves}
    grid = None
    for c in coins:
        d = add_indicators(bars(c))
        idx = d.index
        first = int(np.searchsorted(idx, pd.Timestamp(START)))
        last = int(np.searchsorted(idx, pd.Timestamp(END), side="right"))
        for name, pos in (("#28 돌파+60일선 매도", breakout_trend_positions(d, first, last)),
                          ("㉡ 60일선 위에서만", filter_positions(d, first, last)),
                          ("㉢ #8 원래(추적 손절)", breakout_positions(d, first, last))):
            eq, tr = equity_from_positions(d, pos, COST)
            curves[name][c] = eq.iloc[first:last]
            trades[name] += len(tr)
        o = d["Open"].iloc[first:last]
        curves["㉠ 그냥 보유"][c] = o / o.iloc[0]
        grid = idx[first:last] if grid is None else grid.union(idx[first:last])
    rows = {}
    for name, per in curves.items():
        # 칸마다 10%: 각 칸의 자산을 시작 1로 맞춰 평균(칸끼리 옮기지 않음). 상장 전·빈 구간은 직전 값 유지.
        vals = pd.DataFrame({c: s / s.iloc[0] for c, s in per.items()}).reindex(grid).ffill().fillna(1.0)
        eq = vals.mean(axis=1)
        daily = eq.resample("1D").last().dropna()
        m = metrics(daily.pct_change().dropna())
        rows[name] = {**m, "거래": trades[name]}
    hold, filt = rows["㉠ 그냥 보유"], rows["㉡ 60일선 위에서만"]
    mine = rows["#28 돌파+60일선 매도"]
    crit = [mine["수익/흔들림"] > hold["수익/흔들림"], mine["가장 크게 떨어졌을 때"] > hold["가장 크게 떨어졌을 때"],
            mine["연평균"] > 0, mine["수익/흔들림"] > filt["수익/흔들림"]]
    print(f"\n== #28 판정 기간 {START} ~ {END[:10]} (한쪽 비용 {COST:.2%}) ==")
    for name, m in rows.items():
        print(f"  {name:18s} 총 {m['총수익']:+8.1%}  연 {m['연평균']:+7.1%}  흔들림 {m['흔들림']:6.1%}  수익/흔들림 {m['수익/흔들림']:+.2f}  "
              f"MDD {m['가장 크게 떨어졌을 때']:+7.1%}  거래 {m['거래']}")
    print("  기준 ①>보유 ②MDD<보유 ③연>0 ④>60일선만:", crit, "→", "합격" if all(crit) else "탈락")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps({"coins": coins, "rows": rows, "criteria": crit}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "") == "universe":
        print(universe())
    else:
        run()
