"""덜 흔들리는 주식 (실험 #3, docs/experiments.md 참고).

매달 "고를 수 있는 종목" 중 최근 1년간 가장 덜 흔들린 N개를 똑같은 비중으로 보유한다.
비교용으로 고를 수 있는 종목 전부를 나눠 담는 전략도 함께 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


@dataclass(frozen=True)
class Eligibility:
    """그날 알 수 있는 정보만으로 '고를 수 있는 종목'을 정하는 조건."""

    vol_window: int = 252  # 흔들림을 잴 기간(거래일)
    min_traded_days: int = 240  # vol_window 중 최소 거래일 수
    value_window: int = 60  # 거래대금을 볼 기간(거래일)
    min_value: float = 1e9  # 하루 거래대금 중간값 하한(원)

    def volatility(self, history: Prices) -> pd.Series:
        """고를 수 있는 종목과 그 흔들림(하루 등락률 표준편차). 작을수록 덜 흔들림."""
        if history.volume is None:
            raise ValueError("거래량 데이터가 필요합니다 (load_prices(with_volume=True))")
        if len(history.dates) <= self.vol_window:
            return pd.Series(dtype=float)

        close = history.close.iloc[-(self.vol_window + 1):]
        volume = history.volume.iloc[-self.vol_window:].fillna(0)
        traded = close.notna()

        # 거래된 날끼리의 등락률 (거래정지 기간을 건너뛴 변화는 다시 거래된 날에 반영)
        daily = close.ffill().pct_change().where(traded).iloc[1:]

        traded_today = traded.iloc[-1] & (volume.iloc[-1] > 0)
        enough_days = (volume > 0).sum() >= self.min_traded_days
        value = (close.iloc[1:] * volume).fillna(0).iloc[-self.value_window:].median()
        liquid = value >= self.min_value

        ok = traded_today & enough_days & liquid
        vol = daily.loc[:, ok].std()
        return vol[vol > 0].dropna()


class LowVolatility(Strategy):
    name = "low_volatility"

    def __init__(self, top_n: int = 20, rules: Eligibility = Eligibility()) -> None:
        if top_n < 1:
            raise ValueError("top_n 은 1 이상이어야 합니다")
        self.top_n = top_n
        self.rules = rules

    def target_weights(self, history: Prices) -> pd.Series:
        vol = self.rules.volatility(history)
        if vol.empty:
            return pd.Series(dtype=float)
        picks = vol.nsmallest(self.top_n).index
        return pd.Series(1.0 / len(picks), index=picks)


class EligibleEqualWeight(Strategy):
    """비교용: 고를 수 있는 종목 전부를 똑같이 나눠 담는다."""

    name = "eligible_equal_weight"

    def __init__(self, rules: Eligibility = Eligibility()) -> None:
        self.rules = rules
        self.counts: dict[pd.Timestamp, int] = {}  # 결정일별 고를 수 있는 종목 수

    def target_weights(self, history: Prices) -> pd.Series:
        vol = self.rules.volatility(history)
        self.counts[history.dates[-1]] = len(vol)
        if vol.empty:
            return pd.Series(dtype=float)
        return pd.Series(1.0 / len(vol), index=vol.index)
