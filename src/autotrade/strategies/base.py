"""전략 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from autotrade.data import Prices


class Strategy(ABC):
    """모든 전략의 부모 클래스.

    엔진은 리밸런싱 날마다 장 마감 후 `target_weights` 를 호출하고, 반환된 비중으로
    **다음 거래일 시가**에 포트폴리오를 맞춘다.
    """

    name: str = "strategy"

    @abstractmethod
    def target_weights(self, history: Prices) -> pd.Series:
        """목표 비중을 반환한다.

        Args:
            history: 결정일 장 마감까지의 데이터만 담긴 Prices. 이것 외의 데이터는 쓰지 않는다.

        Returns:
            index=종목코드, value=비중. 음수 불가, 합계 1 이하(나머지는 현금).
            빈 Series 를 반환하면 전액 현금.
        """
