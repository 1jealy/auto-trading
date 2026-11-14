"""급락 신호 + 목표 수익/손절/시간 제한 매매 (실험 #6, docs/experiments.md 참고).

1분 캔들(UTC)을 빈 분까지 채운 표로 만들어 쓴다. 시가·고가·저가는 실제 캔들이 있는 분만 값이 있고,
종가는 신호 계산용으로 마지막 값을 이어 붙인다(그 시점에 알 수 있는 값).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExitRule:
    take_profit: float = 0.005  # 비용 뺀 목표 수익
    stop_loss: float = 0.02  # 비용 뺀 손절 폭
    max_minutes: int = 24 * 60
    tp_margin: float = 0.0002  # 고가가 목표가보다 이만큼 높아야 팔린 것으로 봄
    stop_slip: float = 0.001  # 손절은 손절가보다 이만큼 더 낮게 팔린 것으로 봄
    entry_wait: int = 5  # 다음 분 캔들이 없으면 이 분 안의 첫 캔들 시가로 산다


def minute_grid(m: pd.DataFrame) -> pd.DataFrame:
    """1분 간격 표. Open/High/Low 는 실제 캔들만, CloseF 는 마지막 종가를 이어 붙인 값."""
    m = m.sort_index()
    grid = pd.date_range(m.index.min().floor("D"), m.index.max().ceil("min"), freq="1min", inclusive="left")
    g = m[["Open", "High", "Low", "Close"]].reindex(grid)
    g["CloseF"] = g["Close"].ffill()
    return g


def crash_signals(g: pd.DataFrame, k: float = 3.0, lookback_hours: int = 720) -> pd.Series:
    """급락 신호가 난 분(True). 그 분 종가까지의 정보만 쓴다.

    최근 60분 수익률 ≤ -k × (직전 lookback_hours 개의 '정시 끊은 1시간 수익률' 표준편차).
    진행 중인 시간은 표준편차 계산에서 뺀다.
    """
    close = g["CloseF"]
    r60 = close / close.shift(60) - 1
    # 정시마다 끊은 1시간 수익률: HH:59 종가 기준 (그 시간이 끝난 시각 = 다음 정시 라벨)
    hour_end = close[close.index.minute == 59]
    hourly = hour_end.pct_change()
    hourly.index = hourly.index + pd.Timedelta(minutes=1)  # HH+1:00 에 '끝난 시간' 이 됨
    sigma = hourly.rolling(lookback_hours, min_periods=int(lookback_hours * 0.9)).std()
    # 분마다: 그 분이 속한 시간이 시작될 때(정시)까지 끝난 시간들의 표준편차
    sig_min = sigma.reindex(g.index.floor("h")).to_numpy()
    sig = pd.Series(sig_min, index=g.index)
    return (r60 <= -k * sig) & sig.notna() & g["Close"].notna()


def simulate_trade(g_open, g_high, g_low, entry_i: int, side_cost: float, rule: ExitRule):
    """entry_i 분부터 찾아서 사고, 규칙대로 판다. (진입 위치, 청산 위치, 이유, 비용 뺀 수익) 또는 None."""
    n = len(g_open)
    # 진입: entry_i 부터 entry_wait 분 안의 첫 실제 캔들 시가
    e = entry_i
    while e < n and e < entry_i + rule.entry_wait and np.isnan(g_open[e]):
        e += 1
    if e >= n or np.isnan(g_open[e]):
        return None
    buy = g_open[e]
    gross_k = (1 + side_cost) / (1 - side_cost)
    tp = buy * (1 + rule.take_profit) * gross_k
    sl = buy * (1 - rule.stop_loss) * gross_k
    end = min(n, e + rule.max_minutes)
    hi, lo = g_high[e:end], g_low[e:end]
    hit_sl = np.where(lo <= sl)[0]
    hit_tp = np.where(hi >= tp * (1 + rule.tp_margin))[0]
    first_sl = hit_sl[0] if len(hit_sl) else None
    first_tp = hit_tp[0] if len(hit_tp) else None
    if first_sl is not None and (first_tp is None or first_sl <= first_tp):  # 같은 분이면 손절 먼저
        x, sell, why = e + first_sl, sl * (1 - rule.stop_slip), "손절"
    elif first_tp is not None:
        x, sell, why = e + first_tp, tp, "목표 도달"
    else:
        x = e + rule.max_minutes
        while x < n and np.isnan(g_open[x]):
            x += 1
        if x < n:
            sell, why = g_open[x], "24시간 초과"
        else:  # 데이터 끝(기간 끝): 마지막 실제 종가로 정리
            x = end - 1
            while x > e and np.isnan(g_open[x]):
                x -= 1
            sell, why = g_open[x], "기간 끝"
    net = sell * (1 - side_cost) / (buy * (1 + side_cost)) - 1
    return e, x, why, net


def run_signals(g: pd.DataFrame, signals: pd.Series, side_cost: float, rule: ExitRule,
                start: str, end: str) -> pd.DataFrame:
    """신호가 나면 다음 분에 사고 판다. 들고 있는 동안의 신호는 무시."""
    o, h, l = (g[c].to_numpy() for c in ("Open", "High", "Low"))
    idx = g.index
    sig_pos = np.flatnonzero(signals.to_numpy())
    lo_i, hi_i = idx.searchsorted(pd.Timestamp(start)), idx.searchsorted(pd.Timestamp(end) + pd.Timedelta(days=1))
    rows, free_from = [], lo_i
    for s in sig_pos:
        if s + 1 < free_from or s + 1 >= hi_i:
            continue
        t = simulate_trade(o, h, l, s + 1, side_cost, rule)
        if t is None:
            continue
        e, x, why, net = t
        rows.append(dict(signal=idx[s], entry=idx[e], exit=idx[min(x, len(idx) - 1)], why=why, net=net,
                         minutes=x - e))
        free_from = x + 1
    return pd.DataFrame(rows, columns=["signal", "entry", "exit", "why", "net", "minutes"])


def run_daily(g: pd.DataFrame, side_cost: float, rule: ExitRule, start: str, end: str, hour: int = 0) -> pd.DataFrame:
    """비교용: 매일 hour 시 정각에 산 경우를 하루마다 따로 계산(겹쳐도 됨)."""
    o, h, l = (g[c].to_numpy() for c in ("Open", "High", "Low"))
    idx = g.index
    rows = []
    for d in pd.date_range(start, end, freq="D"):
        i = idx.searchsorted(d + pd.Timedelta(hours=hour))
        t = simulate_trade(o, h, l, i, side_cost, rule)
        if t is None:
            continue
        e, x, why, net = t
        rows.append(dict(entry=idx[e], why=why, net=net, minutes=x - e))
    return pd.DataFrame(rows, columns=["entry", "why", "net", "minutes"])


def trades_equity(trades: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    """거래 수익을 판 날에 반영한 하루 단위 자산(1.0 에서 시작)."""
    daily = (1 + trades.set_index(trades["exit"].dt.floor("D"))["net"]).groupby(level=0).prod() - 1
    return (1 + daily.reindex(days).fillna(0.0)).cumprod()
