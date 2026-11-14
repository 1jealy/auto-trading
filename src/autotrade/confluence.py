"""여러 차트 지표를 합친 단기 매매 (실험 #7, docs/experiments.md 참고). 1시간 캔들 기준."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autotrade.indicators import bollinger, ema, macd, rsi


@dataclass(frozen=True)
class Rules:
    ema_n: int = 50
    rsi_low: float = 50.0
    rsi_high: float = 70.0
    vol_mult: float = 1.2
    vol_n: int = 20
    stop: float = 0.03
    stop_slip: float = 0.001
    max_bars: int = 48


def add_indicators(bars: pd.DataFrame, r: Rules = Rules()) -> pd.DataFrame:
    out = bars.copy()
    out["ema"] = ema(out["Close"], r.ema_n)
    out = out.join(macd(out["Close"]))
    out["rsi"] = rsi(out["Close"])
    out = out.join(bollinger(out["Close"]))
    out["vol_avg"] = out["Volume"].rolling(r.vol_n).mean().shift(1)  # 직전 20개 (이번 캔들 제외)
    return out


def entry_signal(d: pd.DataFrame, r: Rules = Rules()) -> pd.Series:
    """캔들이 끝났을 때 다섯 조건이 모두 참이면 True."""
    turned_up = (d["hist"].shift(1) < 0) & (d["hist"] > 0)
    return (
        (d["Close"] > d["ema"])
        & turned_up
        & d["rsi"].between(r.rsi_low, r.rsi_high)
        & (d["Close"] < d["upper"])
        & (d["Volume"] >= r.vol_mult * d["vol_avg"])
    ).fillna(False)


def exit_signal(d: pd.DataFrame) -> pd.Series:
    """캔들이 끝났을 때 MACD 히스토그램 < 0 이거나 종가 < EMA → 다음 캔들 시가에 판다."""
    return ((d["hist"] < 0) | (d["Close"] < d["ema"])).fillna(False)


def simulate(d: pd.DataFrame, side_cost: float, r: Rules, start: str, end: str) -> pd.DataFrame:
    """신호 다음 캔들 시가에 사고, 손절/신호/48시간 중 먼저 오는 것으로 판다."""
    o, lo = d["Open"].to_numpy(), d["Low"].to_numpy()
    c_last = d["Close"].to_numpy()
    ent, ext = entry_signal(d, r).to_numpy(), exit_signal(d).to_numpy()
    idx = d.index
    first = idx.searchsorted(pd.Timestamp(start) + pd.Timedelta(days=3))  # 지표 준비 기간
    last = idx.searchsorted(pd.Timestamp(end) + pd.Timedelta(days=1))  # 기간 끝(이후 캔들 없음)
    n = min(len(idx), last)
    rows, free = [], first
    for s in np.flatnonzero(ent):
        e = s + 1
        if s < first or e < free or e >= n:
            continue
        buy = o[e]
        stop = buy * (1 - r.stop)
        x, sell, why = None, None, None
        for j in range(e, min(n, e + r.max_bars)):
            if lo[j] <= stop:  # 같은 캔들에서 겹치면 손절 먼저
                x, sell, why = j, stop * (1 - r.stop_slip), "손절"
                break
            if ext[j]:
                x, sell, why = j + 1, (o[j + 1] if j + 1 < n else c_last[j]), "지표 신호"
                break
        if x is None:
            j = min(n, e + r.max_bars)
            x, sell, why = j, (o[j] if j < n else c_last[n - 1]), "48시간"
        hours = (x - e) + (1 if why == "손절" else 0)
        rows.append(dict(entry=idx[e], exit=idx[min(x, len(idx) - 1)], why=why, buy=buy, sell=sell, hours=hours,
                         gross_log=float(np.log(sell / buy)),
                         net=sell * (1 - side_cost) / (buy * (1 + side_cost)) - 1))
        free = x + 1
    return pd.DataFrame(rows, columns=["entry", "exit", "why", "buy", "sell", "hours", "gross_log", "net"])
