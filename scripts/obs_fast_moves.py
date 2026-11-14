"""관찰: 짧은 시간에 0.5% 움직인 직후, 그때 샀다면 평균 몇 %가 되었나 (비트코인, 업비트). → moves.json

    python scripts/obs_fast_moves.py data/ticks/btc_ticks.pkl . data/ticks/moves.json
(1분 데이터는 먼저 scripts/download_upbit_minutes.py KRW-BTC 2024-01 로 받아 둘 것. 실험이 아니라 관찰 — docs/experiments.md 참고)
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

ticks_path, root, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
TH = 0.005
COOL = 300  # 같은 방향 사건은 5분에 한 번만 센다(연달아 세면 같은 움직임을 여러 번 셈)


def events(price: pd.Series, lookback: int, cool: int, th: float = TH):
    """price: 일정 간격(초 또는 분) 가격. lookback 칸 전 대비 ±0.5% 이상이면 사건."""
    r = price / price.shift(lookback) - 1
    out = {}
    for name, mask in [("rise", r >= th), ("fall", r <= -th)]:
        idx, last = [], -10**12
        for i in np.flatnonzero(mask.to_numpy()):
            if i - last >= cool:
                idx.append(i); last = i
        out[name] = np.array(idx, dtype=int)
    return out


def forward(price: np.ndarray, idx: np.ndarray, h: int):
    ok = idx + h < len(price)
    i = idx[ok]
    return price[i + h] / price[i] - 1


def summarize(price: pd.Series, lookback: int, cool: int, horizons: list[int], th: float = TH):
    p = price.to_numpy()
    ev = events(price, lookback, cool, th)
    res = {"count": {k: int(len(v)) for k, v in ev.items()}, "horizons": horizons, "series": {}}
    rng = np.random.default_rng(0)
    base_idx = rng.choice(np.arange(lookback, len(p) - max(horizons)), size=200_000, replace=True)
    for name, idx in list(ev.items()) + [("any", base_idx)]:
        rows = []
        for h in horizons:
            f = forward(p, idx, h)
            rows.append({
                "mean": float(f.mean() * 100) if len(f) else None,
                "median": float(np.median(f) * 100) if len(f) else None,
                "win013": float((f > 0.0013).mean() * 100) if len(f) else None,
                "n": int(len(f)),
            })
        res["series"][name] = rows
    return res


# 1) 체결 기록 → 1초 가격 (그 초의 마지막 체결가, 체결 없으면 직전 값)
t = pd.read_pickle(ticks_path)
sec = t.set_index("time")["price"].resample("1s").last().ffill().dropna()
tick_res = summarize(sec, lookback=10, cool=COOL, horizons=[10, 30, 60, 120, 300, 600], th=0.002)
tick_res["count_05"] = {k: int(len(v)) for k, v in events(sec, 10, COOL, 0.005).items()}
# 꼬리: 10초 안 최고-최저 폭이 0.5% 이상이었던 순간(5분에 한 번만 셈)과, 10초 뒤 가격이 그 폭의 어디쯤으로 돌아왔나
agg = t.set_index("time")["price"].resample("1s").agg(["max", "min"]).ffill()
hi, lo = agg["max"].rolling(10).max(), agg["min"].rolling(10).min()
spike = ((hi / lo - 1) >= 0.005).to_numpy()
idx, last = [], -10**9
for i in np.flatnonzero(spike):
    if i - last >= COOL:
        idx.append(i); last = i
tick_res["spikes_05"] = len(idx)
tick_res["move10_pct"] = {"p50": float((sec / sec.shift(10) - 1).abs().quantile(0.5) * 100),
                          "p99": float((sec / sec.shift(10) - 1).abs().quantile(0.99) * 100)}
tick_res["period"] = [str(sec.index[0])[:16], str(sec.index[-1])[:16]]

# 2) 1분 데이터 → 1분 동안 ±0.5%
store = root / "data" / "cache" / "crypto" / "upbit" / "KRW-BTC"
m = pd.concat(pd.read_csv(f, index_col=0, parse_dates=True) for f in sorted(store.glob("*.csv.gz"))).sort_index()
m = m[~m.index.duplicated()]
close = m["Close"].resample("1min").last().ffill().dropna()
min_res = summarize(close, lookback=1, cool=COOL // 60, horizons=[1, 2, 3, 5, 10, 15, 30, 60])
min_res["period"] = [str(close.index[0])[:10], str(close.index[-1])[:10]]

# 연도별(1분, 사고 5분 뒤) — 해마다 같은 모양인지
p = close.to_numpy()
ev = events(close, 1, COOL // 60)
yearly = {}
for name, idx in ev.items():
    f = forward(p, idx, 5)
    yrs = close.index[idx[: len(f)]].year
    yearly[name] = {int(y): {"mean": float(f[yrs == y].mean() * 100), "n": int((yrs == y).sum())} for y in np.unique(yrs)}
min_res["yearly_5min"] = yearly

json.dump({"tick": tick_res, "minute": min_res}, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps({"tick": tick_res["count"], "tick_period": tick_res["period"],
                  "minute": min_res["count"], "minute_period": min_res["period"]}, ensure_ascii=False))
for k in ("tick", "minute"):
    r = json.load(open(out))[k]
    for s in ("rise", "fall", "any"):
        print(k, s, [None if x["mean"] is None else round(x["mean"], 3) for x in r["series"][s]],
              "win>0.13%:", [None if x["win013"] is None else round(x["win013"]) for x in r["series"][s]])
print("yearly 5min", json.dumps(yearly))
