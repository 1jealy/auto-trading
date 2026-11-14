"""하루 중 정해진 시각에 사서 몇 시간 뒤 파는 계산 (실험 #5, docs/experiments.md 참고).

시각은 모두 UTC. 1분 캔들의 시가로 사고판다. 그 분에 캔들이 없으면(거래 없음)
max_wait 분 안의 다음 캔들 시가를 쓰고, 그 안에도 없으면 그날은 거래하지 않는다.
"""

from __future__ import annotations

import pandas as pd


def fill_forward_opens(opens: pd.Series, max_wait: int = 5) -> pd.Series:
    """1분 간격으로 빈 분을 채운 시가. 비어 있는 분은 max_wait 분 안의 '다음' 캔들 시가로 채운다."""
    opens = opens.sort_index()
    grid = pd.date_range(opens.index.min().floor("D"), opens.index.max().ceil("D"), freq="1min", inclusive="left")
    return opens.reindex(grid).bfill(limit=max_wait)


def window_returns(filled: pd.Series, start_hour: int, hold_hours: int, days: pd.DatetimeIndex) -> pd.DataFrame:
    """날마다 start_hour 시 정각에 사서 hold_hours 시간 뒤 정각에 판 결과.

    Returns: index=날짜(UTC 자정), 열 buy, sell, ret(비용 전). 가격이 없는 날은 빠진다.
    """
    buy_ts = days + pd.Timedelta(hours=start_hour)
    sell_ts = buy_ts + pd.Timedelta(hours=hold_hours)
    buy = filled.reindex(buy_ts).to_numpy()
    sell = filled.reindex(sell_ts).to_numpy()
    out = pd.DataFrame({"buy": buy, "sell": sell}, index=days)
    out = out.dropna()
    out["ret"] = out["sell"] / out["buy"] - 1
    return out


def after_cost(gross_ret: pd.Series, side_cost: float) -> pd.Series:
    """한쪽 비용(비율)을 사고팔 때 각각 낸 뒤의 수익률."""
    return (1 + gross_ret) * (1 - side_cost) / (1 + side_cost) - 1


def daily_equity(trade_ret: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """거래한 날은 그 수익, 쉰 날은 0 으로 하루 단위 자산(1.0 에서 시작)."""
    daily = trade_ret.reindex(days).fillna(0.0)
    return (1 + daily).cumprod()


def breakeven_side_cost(gross_ret: pd.Series) -> float:
    """이 비용(한쪽)이면 전체 수익이 0 이 된다: (1+r)·(1-c)/(1+c) 를 다 곱해서 1 이 되는 c."""
    g = float((1 + gross_ret).prod()) ** (1 / len(gross_ret))  # 하루 평균 배율
    return (g - 1) / (g + 1)
