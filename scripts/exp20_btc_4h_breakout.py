"""실험 #20: 비트코인 선물 4시간봉 돌파 — 롱·숏 양방향, 1배. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp20_btc_4h_breakout.py data         # 자료만 받고 빈 곳 점검 (수익률 안 봄)
    python scripts/exp20_btc_4h_breakout.py practice     # 연습 기간
    python scripts/exp20_btc_4h_breakout.py validation   # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from autotrade.breakout4h import BreakoutRules, hold_curve, judge, run  # noqa: E402

SYMBOL = "BTCUSDT"
DATA_START, DATA_END = "2020-01", "2026-10"
PERIODS = {
    "practice": ("2020-01-01", "2023-12-31 23:59"),
    "validation": ("2024-01-01", "2026-10-31 23:59"),
}
RULES = BreakoutRules(entry=20, exit=10, max_bars=24, cost=0.0010)
BASE = "https://data.binance.vision/data/futures/um/monthly"
CACHE = ROOT / "data" / "cache" / "exp20"
OUT = ROOT / "results" / "exp20"
UA = {"User-Agent": "Mozilla/5.0 (research; public market data)"}


def _month_csv(kind: str, month: str) -> pd.DataFrame:
    """바이낸스 공개 자료(월별 zip) 한 달. kind = '4h' 또는 'fundingRate'."""
    path = CACHE / f"{SYMBOL}-{kind}-{month}.csv"
    if path.exists():
        return pd.read_csv(path)
    sub = f"klines/{SYMBOL}/4h" if kind == "4h" else f"fundingRate/{SYMBOL}"
    r = requests.get(f"{BASE}/{sub}/{SYMBOL}-{kind}-{month}.zip", headers=UA, timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode()
    first = raw.split("\n", 1)[0]
    header = None if first.split(",")[0].strip().isdigit() else 0
    df = pd.read_csv(io.StringIO(raw), header=header)
    if kind == "4h":
        df = df.iloc[:, :6]
        df.columns = ["open_time", "open", "high", "low", "close", "volume"]
    else:
        df.columns = [c.strip() for c in df.columns]
        df = df[["calc_time", "last_funding_rate"]]
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def _ms(x: pd.Series) -> pd.DatetimeIndex:
    x = x.astype("int64")
    unit = "us" if x.max() > 10**14 else "ms"  # 일부 최근 파일은 마이크로초
    return pd.DatetimeIndex(pd.to_datetime(x, unit=unit))


def load() -> tuple[pd.DataFrame, pd.Series]:
    months = pd.period_range(DATA_START, DATA_END, freq="M").strftime("%Y-%m")
    k = pd.concat([_month_csv("4h", m) for m in months], ignore_index=True)
    bars = k.set_index(_ms(k["open_time"]))[["open", "high", "low", "close"]].astype(float)
    bars = bars[~bars.index.duplicated()].sort_index()
    f = pd.concat([_month_csv("fundingRate", m) for m in months], ignore_index=True)
    fund = pd.Series(f["last_funding_rate"].astype(float).to_numpy(), index=_ms(f["calc_time"])).sort_index()
    fund.index = fund.index.round("min")  # 정산 시각 몇 ms 어긋남 정리
    fund = fund[~fund.index.duplicated()]
    return bars, fund


def check_data() -> None:
    bars, fund = load()
    full = pd.date_range(bars.index[0], bars.index[-1], freq="4h")
    missing = full.difference(bars.index)
    print(f"4시간봉 {len(bars)}개 ({bars.index[0]} ~ {bars.index[-1]}), 빠진 봉 {len(missing)}개")
    if len(missing):
        print("  빠진 봉 예:", [str(t) for t in missing[:10]])
    gaps = fund.index.to_series().diff().dt.total_seconds().div(3600)
    print(f"펀딩비 {len(fund)}개 ({fund.index[0]} ~ {fund.index[-1]}), 간격(시간) 분포:",
          gaps.round().value_counts().head(5).to_dict())
    bad = bars[(bars["high"] < bars[["open", "close"]].max(axis=1)) | (bars["low"] > bars[["open", "close"]].min(axis=1))]
    print("고가·저가가 시가·종가와 안 맞는 봉:", len(bad))


def run_period(period: str) -> None:
    start, end = (pd.Timestamp(x) for x in PERIODS[period])
    bars, fund = load()
    warm = start - pd.Timedelta(hours=4 * (RULES.entry + 2))
    bars = bars.loc[warm:end]  # 이전 기간은 지표 준비용 22봉만, 이후 기간은 넣지 않음
    fund = fund.loc[:end]
    hold = hold_curve(bars, start, end, RULES.cost)
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, sides in [("양방향(판정)", (1, -1)), ("롱만(참고)", (1,)), ("숏만(참고)", (-1,))]:
        r = BreakoutRules(RULES.entry, RULES.exit, RULES.max_bars, RULES.cost, sides)
        res = run(bars, fund, r, start, end)
        j = judge(res, hold)
        summary[name] = j
        tag = {"양방향(판정)": "both", "롱만(참고)": "long", "숏만(참고)": "short"}[name]
        res.trades.to_csv(OUT / f"{period}_{tag}_trades.csv", index=False)
        res.equity.to_csv(OUT / f"{period}_{tag}_equity.csv")
        print(f"\n== #20 {name} ({period} {start.date()} ~ {end.date()}) ==")
        for k, v in j.items():
            if name != "양방향(판정)" and k in ("기준1~5", "판정"):
                continue
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
        if name == "양방향(판정)":
            t = res.trades
            print("  청산 이유:", t["status"].value_counts().to_dict())
            print("  해마다 거래당 평균:", t.groupby(pd.to_datetime(t["entry_time"]).dt.year)["ret"].mean().round(4).to_dict())
    (OUT / f"{period}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                               encoding="utf-8")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "data"
    check_data() if arg == "data" else run_period(arg)
