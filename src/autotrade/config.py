"""거래 비용 설정. 세율·수수료는 이 파일 한 곳에서만 관리한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """거래 비용. 모든 값은 비율이다 (0.00015 = 0.015%).

    기본값은 국내 주식 가정치다. 실제 증권사 수수료와 해당 연도·시장의 증권거래세를
    확인한 뒤 수정할 것.
    """

    commission: float = 0.00015  # 위탁수수료(편도). 증권사·이벤트마다 다름
    sell_tax: float = 0.0020  # 증권거래세(매도 시에만). 연도·시장별로 다름 — 확인 필요
    slippage: float = 0.0005  # 호가 차이·체결 지연 가정(편도)

    def __post_init__(self) -> None:
        for name in ("commission", "sell_tax", "slippage"):
            value = getattr(self, name)
            if not 0 <= value < 0.1:
                raise ValueError(f"{name}={value} 는 0 이상 0.1 미만의 비율이어야 합니다")

    @property
    def buy_rate(self) -> float:
        return self.commission + self.slippage

    @property
    def sell_rate(self) -> float:
        return self.commission + self.sell_tax + self.slippage

    @classmethod
    def zero(cls) -> "CostModel":
        """비용 없음. 테스트 전용 — 성과 보고에 쓰지 말 것."""
        return cls(commission=0.0, sell_tax=0.0, slippage=0.0)


# 국내 주식 매도 시 증권거래세(농어촌특별세 포함 합계). 코스피(거래세+농특세 0.15%)와 코스닥 합계가 같다.
# (시작일, 세율). 출처: 증권거래세법 시행령 개정 연혁. 2026년 0.20%는 2025년 세법 개정안 기준 — 확인 필요.
STOCK_SELL_TAX = [
    ("1900-01-01", 0.0030),
    ("2019-06-03", 0.0025),
    ("2021-01-01", 0.0023),
    ("2023-01-01", 0.0020),
    ("2024-01-01", 0.0018),
    ("2025-01-01", 0.0015),
    ("2026-01-01", 0.0020),
]


def stock_sell_tax(date) -> float:
    """그 날짜에 국내 주식을 팔 때의 증권거래세 합계 비율."""
    import pandas as pd

    d = pd.Timestamp(date)
    rate = STOCK_SELL_TAX[0][1]
    for start, r in STOCK_SELL_TAX:
        if d >= pd.Timestamp(start):
            rate = r
    return rate
