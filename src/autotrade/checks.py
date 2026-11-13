"""데이터 품질 점검. 실험 스크립트에서 결과와 함께 경고를 출력한다."""

from __future__ import annotations

import pandas as pd

from autotrade.data import Prices


def warn_big_jumps(prices: Prices, limit: float = 0.15) -> None:
    """하루에 limit 넘게 움직인 종가 — 분할 등 데이터 문제일 수 있음."""
    jumps = prices.close.pct_change().abs().stack()
    big = jumps[jumps > limit]
    if not big.empty:
        print(f"경고: 하루 {limit:.0%} 넘는 가격 변화 — 데이터 문제일 수 있음\n{big}\n")


def warn_suspicious_fills(prices: Prices, trades: pd.DataFrame) -> None:
    """시가만 튀고 종가는 제자리인 날(데이터 오류 의심)에 체결된 매매."""
    prev_close = prices.close.shift(1)
    open_gap = (prices.open / prev_close - 1).abs()
    day_move = (prices.close / prev_close - 1).abs()
    suspicious = (open_gap > 0.03) & (day_move < 0.015)
    hits = [(d.date(), t) for d, t in zip(trades["date"], trades["ticker"]) if suspicious.loc[d, t]]
    if hits:
        print(f"\n경고: 데이터 오류가 의심되는 시가에 체결된 매매 {len(hits)}건:", hits[:10])
