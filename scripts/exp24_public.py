"""#24~#27 공개 자동매매 전략 4개 (docs/experiments.md). 비트코인 일봉(업비트), 판정은 검증 기간 한 번.

    python scripts/exp24_public.py practice      # 연습 기간(원작자와 같은 기간) — 참고·재현 확인
    python scripts/exp24_public.py validation    # 검증 기간 딱 한 번
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from autotrade.public_rules import bbrsi_returns, breakout_returns, hold_returns, metrics, rsi2_returns  # noqa: E402
from autotrade.upbit import days_between  # noqa: E402

COST = 0.0005 + 0.0005  # 업비트 수수료 + 체결 차이(한쪽)
PRACTICE = ("2017-10-11", "2021-05-02")
VALIDATION = ("2021-05-03", "2026-10-31")
CACHE = ROOT / "data" / "cache" / "exp24"
OUT = ROOT / "results" / "exp24"
RULES = {
    "#24 P1 변동성 돌파": lambda d: breakout_returns(d, 0.5, None, COST),
    "#25 P2 돌파+15일선": lambda d: breakout_returns(d, 0.5, 15, COST),
    "#26 P3 RSI(2) 되돌림": lambda d: rsi2_returns(d, COST),
    "#27 P4 볼린저+RSI": lambda d: bbrsi_returns(d, COST),
}


def load(market: str) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{market}.csv"
    if f.exists():
        return pd.read_csv(f, index_col=0, parse_dates=True)
    df = days_between(market, datetime(2017, 9, 1), datetime(2026, 11, 2))
    df.to_csv(f)
    return df


def window(r: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    return r.loc[a:b]


def run(period: tuple[str, str], label: str) -> dict:
    a, b = period
    out = {}
    for market in ("KRW-BTC", "KRW-ETH", "KRW-XRP"):
        d = load(market)
        hold = metrics(window(hold_returns(d), a, b)["r"])
        rows = {"그냥 보유": {**hold, "거래": 0}}
        for name, f in RULES.items():
            w = window(f(d), a, b)
            m = metrics(w["r"])
            crit = [m["수익/흔들림"] > hold["수익/흔들림"], m["가장 크게 떨어졌을 때"] > hold["가장 크게 떨어졌을 때"], m["연평균"] > 0]
            rows[name] = {**m, "거래": int(w["traded"].sum()), "기준": crit}
        out[market] = rows
        print(f"\n== {label} {a} ~ {b} · {market}{' (판정)' if market == 'KRW-BTC' else ' (참고)'} ==")
        for name, m in rows.items():
            crit = m.get("기준")
            print(f"  {name:18s} 총 {m['총수익']:+9.1%}  연 {m['연평균']:+7.1%}  흔들림 {m['흔들림']:6.1%}  수익/흔들림 {m['수익/흔들림']:+.2f}  "
                  f"MDD {m['가장 크게 떨어졌을 때']:+7.1%}  거래 {m['거래']:4d}" + (f"  기준 {crit} → {'합격' if all(crit) else '탈락'}" if crit and label == '검증' and market == 'KRW-BTC' else ""))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "practice"
    if cmd == "practice":
        run(PRACTICE, "연습")
        d = load("KRW-BTC")
        nocost = breakout_returns(d, 0.5, None, 0.0).loc[PRACTICE[0]:PRACTICE[1]]["r"]
        print(f"\n재현 확인: P1 비용 0이면 연습 기간 총 {metrics(nocost)['총수익']:+.0%} (원작자 README +2,067%)")
    else:
        run(VALIDATION, "검증")
