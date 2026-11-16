"""관찰(실험 아님): 해외(바이낸스 현물) 3초 상승 폭 기준별로 신호가 하루 몇 번 나오는지만 센다. 수익은 계산하지 않는다.

    python scripts/obs_scalp_freq.py 2026-11-07 2026-11-14
- 신호 = 3초 전 대비 상승률 >= 기준. 같은 코인은 60초 안에 이어지면 한 번(scalp 의 사건 묶음과 같은 방식).
- 업비트 쪽(뒤처짐·호가 차이)은 과거 자료가 없어 못 센다 → 아래 숫자는 '해외가 먼저 움직인 횟수'의 상한.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "freq"
COINS = ["BTC", "ETH", "XRP", "SOL"]
THRESHOLDS = [0.0035, 0.0025, 0.0020, 0.0015, 0.0010, 0.0007, 0.0005]
UA = {"User-Agent": "Mozilla/5.0 (research)"}


def day_close(coin: str, day: str) -> pd.Series:
    path = CACHE / f"{coin}USDT-{day}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    r = requests.get(f"https://data.binance.vision/data/spot/daily/klines/{coin}USDT/1s/{coin}USDT-1s-{day}.zip",
                     headers=UA, timeout=120)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), header=None, usecols=[0, 4], names=["t", "close"])
    t = df["t"].astype("int64")
    unit = "us" if t.max() > 10**14 else "ms"
    s = pd.Series(df["close"].astype(float).to_numpy(), index=pd.to_datetime(t, unit=unit))
    s = s[~s.index.duplicated()].resample("1s").last().ffill()
    CACHE.mkdir(parents=True, exist_ok=True)
    s.to_pickle(path)
    return s


def events(p: np.ndarray, th: float, gap: int = 60) -> int:
    r = p[3:] / p[:-3] - 1
    idx = np.flatnonzero(r >= th)
    n, last = 0, -10**9
    for i in idx:
        if i - last >= gap:
            n += 1
        last = i  # 계속 이어지는 동안은 마지막 신호 기준으로 60초
    return n


def main(a: str, b: str) -> None:
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(a, b)]
    rows = []
    for coin in COINS:
        cnt = {th: 0 for th in THRESHOLDS}
        for d in days:
            p = day_close(coin, d).to_numpy()
            for th in THRESHOLDS:
                cnt[th] += events(p, th)
        rows.append({"coin": coin, **{f"{th*100:.2f}%": cnt[th] / len(days) for th in THRESHOLDS}})
    df = pd.DataFrame(rows).set_index("coin")
    df.loc["합계(4코인)"] = df.sum()
    print(f"기간 {days[0]} ~ {days[-1]} ({len(days)}일), 하루 평균 횟수 (사건 묶음 60초)")
    print(df.round(1).to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
