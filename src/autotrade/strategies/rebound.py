"""업종 급락 후 며칠 보유 (실험 #2, docs/experiments.md 참고).

최근 며칠 동안 평소 움직임보다 훨씬 크게 떨어진 종목을 사서 정해진 날짜만큼 들고 있다가 판다.
엔진이 매일(rebalance="D") 순서대로 호출한다고 가정하며, 보유 상태를 내부에 기억한다.
"""

from __future__ import annotations

import math
from typing import Literal, Sequence

import pandas as pd

from autotrade.data import Prices
from autotrade.strategies.base import Strategy


class ShortTermRebound(Strategy):
    name = "short_term_rebound"

    def __init__(
        self,
        universe: Sequence[str],
        vol_window: int = 60,
        drop_days: int = 5,
        threshold: float = 2.0,
        hold_days: int = 3,
        slots: int = 4,
        extend: Literal["any", "fresh"] = "any",
    ) -> None:
        """
        Args:
            vol_window: 평소 흔들림을 잴 기간(거래일)
            drop_days: 하락을 잴 기간(거래일)
            threshold: 평소 흔들림의 몇 배 넘게 떨어지면 신호인가
            hold_days: 신호 다음 날 사서 며칠 보유하나
            slots: 동시에 들고 있을 최대 종목 수 (한 종목 비중 = 1/slots)
            extend: 보유 중 신호가 다시 나면 연장하는 방식.
                "any"   — 신호 조건을 만족하는 날마다 연장
                "fresh" — 전날엔 신호가 아니었다가 새로 생긴 날만 연장
        """
        if min(vol_window, drop_days, hold_days, slots) < 1 or threshold <= 0:
            raise ValueError("기간·자리 수는 1 이상, threshold 는 0보다 커야 합니다")
        if extend not in ("any", "fresh"):
            raise ValueError("extend 는 'any' 또는 'fresh'")
        self.universe = list(universe)
        self.vol_window = vol_window
        self.drop_days = drop_days
        self.threshold = threshold
        self.hold_days = hold_days
        self.slots = slots
        self.extend = extend
        self.reset()

    def reset(self) -> None:
        self._expiry: dict[str, int] = {}  # 종목 → 마지막으로 보유 결정을 내리는 날(순번)
        self._entry: dict[str, int] = {}  # 종목 → 처음 신호가 난 날(순번)
        self._last_n = -1
        self.episodes: list[dict] = []  # 끝난 보유 기록: ticker, signal_date, last_decision

    def target_weights(self, history: Prices) -> pd.Series:
        n = len(history.dates) - 1
        if n <= self._last_n:  # 새 백테스트가 시작됨
            self.reset()
        self._last_n = n

        for ticker in [t for t, last in self._expiry.items() if last < n]:
            self.episodes.append(
                dict(
                    ticker=ticker,
                    signal_date=history.dates[self._entry[ticker]],
                    last_decision=history.dates[self._expiry[ticker]],
                )
            )
            del self._expiry[ticker], self._entry[ticker]

        close = history.close[self.universe]
        today = self._signals(close)
        if self.extend == "fresh" and not today.empty:
            yesterday = self._signals(close.iloc[:-1])
            fresh = today.index.difference(yesterday.index)
            today = today[today.index.isin(fresh)]

        until = n + self.hold_days - 1
        for ticker in today.index:
            if ticker in self._expiry:
                self._expiry[ticker] = until
        for ticker in today.sort_values().index:  # 가장 많이 떨어진 것부터
            if ticker in self._expiry:
                continue
            if len(self._expiry) >= self.slots:
                break
            self._expiry[ticker] = until
            self._entry[ticker] = n

        return pd.Series(1.0 / self.slots, index=list(self._expiry), dtype=float)

    def _signals(self, close: pd.DataFrame) -> pd.Series:
        """신호가 난 종목과 점수(하락폭 ÷ 평소 흔들림, 음수일수록 강함)."""
        need = max(self.vol_window, self.drop_days) + 1
        if len(close) < need:
            return pd.Series(dtype=float)
        daily = close.iloc[-need:].pct_change().iloc[-self.vol_window :]
        usual = daily.std() * math.sqrt(self.drop_days)
        drop = close.iloc[-1] / close.iloc[-1 - self.drop_days] - 1
        score = (drop / usual)[usual > 0].dropna()
        return score[score < -self.threshold]
