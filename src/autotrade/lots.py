"""#22 덩이 매수·익절·손절 (60일선 위에서만) 계산. 규칙은 docs/experiments.md #22.

봉이 끝난 뒤 종가로 판단하고 **다음 봉 시가**에 체결한다(미래 참조 금지). 비용은 한쪽 cost.
- 사기: 종가 > 60일 평균이면 그 코인을 한 덩이(lot_krw) 더 산다. 코인당 최대 max_lots.
- 익절: 60일선 위일 때, 종가가 덩이를 산 가격 x (1 + target) 이상이면 그 덩이만 판다.
- 손절: stop 이 있으면 60일선 위·아래 상관없이, 종가가 산 가격 x (1 - stop) 이하이면 그 덩이를 판다.
- 자료가 끝난 코인(상장폐지)의 덩이는 마지막 봉 다음에 0원으로 정리한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LotRules:
    ma_bars: int  # 60일에 해당하는 봉 수
    target: float
    stop: float | None
    cost: float = 0.0015
    lot_krw: float = 100_000.0
    max_lots: int = 5
    capital: float = 10_000_000.0


@dataclass
class LotResult:
    equity: pd.Series  # 봉 종가 기준 계좌 가치
    trades: pd.DataFrame  # 판 덩이: coin, buy_t, sell_t, buy_price, sell_price, ret, why
    open_lots: pd.DataFrame  # 끝까지 못 판 덩이
    buys: int


def simulate(open_: pd.DataFrame, close: pd.DataFrame, r: LotRules, end: pd.Timestamp | None = None) -> LotResult:
    """open_/close: 행 = 봉 시작 시각(UTC), 열 = 코인. 자료가 없는 곳은 NaN. end 이후 봉은 쓰지 않는다."""
    if end is not None:
        open_, close = open_.loc[:end], close.loc[:end]
    idx = close.index
    ma = close.rolling(r.ma_bars, min_periods=r.ma_bars).mean()
    above = (close > ma).to_numpy()
    o, c = open_.to_numpy(float), close.to_numpy(float)
    n, k = c.shape
    last_valid = [int(np.flatnonzero(~np.isnan(c[:, j]))[-1]) if (~np.isnan(c[:, j])).any() else -1 for j in range(k)]
    cash = r.capital
    lots: list[list[list[float]]] = [[] for _ in range(k)]  # [buy_price, qty, buy_i]
    eq = np.full(n, np.nan)
    sold: list[dict] = []
    buys = 0
    pend: list[list] = [[] for _ in range(k)]  # 다음 봉 시가에 할 일

    def sell(j: int, lot: list[float], i: int, price: float, why: str) -> None:
        nonlocal cash
        proceeds = lot[1] * price * (1 - r.cost) if price > 0 else 0.0
        cash += proceeds
        sold.append({"coin": close.columns[j], "buy_t": idx[int(lot[2])], "sell_t": idx[min(i, n - 1)],
                     "buy_price": lot[0], "sell_price": price, "ret": proceeds / (lot[1] * lot[0] * (1 + r.cost)) - 1,
                     "why": why})

    for i in range(n):
        for j in range(k):
            # 1) 이 봉 시가: 지난 봉 종가에 정한 주문 체결
            if pend[j] and not np.isnan(o[i, j]):
                for act in pend[j]:
                    if act[0] == "sell":
                        lot = act[1]
                        if lot in lots[j]:
                            lots[j].remove(lot)
                            sell(j, lot, i, o[i, j], act[2])
                    elif act[0] == "buy" and cash >= r.lot_krw and len(lots[j]) < r.max_lots:
                        cash -= r.lot_krw
                        lots[j].append([o[i, j], r.lot_krw * (1 - r.cost) / o[i, j], float(i)])
                        buys += 1
                pend[j] = []
            # 2) 자료가 끝난 코인: 남은 덩이는 0원
            if last_valid[j] >= 0 and i == last_valid[j] + 1 and lots[j]:
                for lot in list(lots[j]):
                    lots[j].remove(lot)
                    sell(j, lot, i, 0.0, "상장폐지")
            # 3) 이 봉 종가로 판단
            if np.isnan(c[i, j]) or i >= last_valid[j] or i == n - 1:
                continue
            up = bool(above[i, j])
            acts = []
            for lot in lots[j]:
                if r.stop is not None and c[i, j] <= lot[0] * (1 - r.stop):
                    acts.append(("sell", lot, "손절"))
                elif up and c[i, j] >= lot[0] * (1 + r.target):
                    acts.append(("sell", lot, "익절"))
            if up and len(lots[j]) < r.max_lots:
                acts.append(("buy",))
            pend[j] = acts
        # 4) 종가 기준 계좌 가치
        val = cash
        for j in range(k):
            if lots[j] and not np.isnan(c[i, j]):
                val += sum(lot[1] for lot in lots[j]) * c[i, j]
        eq[i] = val
    open_rows = [{"coin": close.columns[j], "buy_price": l[0], "qty": l[1], "buy_t": idx[int(l[2])]}
                 for j in range(k) for l in lots[j]]
    return LotResult(pd.Series(eq, index=idx), pd.DataFrame(sold, columns=["coin", "buy_t", "sell_t", "buy_price",
                                                                           "sell_price", "ret", "why"]),
                     pd.DataFrame(open_rows, columns=["coin", "buy_price", "qty", "buy_t"]), buys)


def hold_equity(open_: pd.DataFrame, close: pd.DataFrame, start: pd.Timestamp, capital: float = 10_000_000.0,
                cost: float = 0.0015, end: pd.Timestamp | None = None) -> pd.Series:
    """그냥 보유: 자본을 코인 수로 똑같이 나눠, 코인마다 start 이후 첫 봉 시가에 한 번 사서 끝까지 든다.
    자료가 끝난 코인은 마지막 봉 다음부터 0원."""
    if end is not None:
        open_, close = open_.loc[:end], close.loc[:end]
    k = close.shape[1]
    eq = pd.Series(0.0, index=close.index)
    cash_share = capital / k
    for col in close.columns:
        o, c = open_[col], close[col]
        valid = c.dropna().index
        if len(valid) == 0:
            eq += cash_share
            continue
        first = o.loc[start:].dropna()
        if first.empty:
            eq += cash_share
            continue
        t0, p0 = first.index[0], float(first.iloc[0])
        qty = cash_share * (1 - cost) / p0
        v = (c * qty).where(c.index >= t0, cash_share)
        v = v.where(c.index <= valid[-1], 0.0).ffill().fillna(cash_share)
        eq += v
    return eq


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """1시간봉(open/close 열) → rule 봉(UTC 기준). 시가=첫 값, 종가=마지막 값."""
    g = df.resample(rule, label="left", closed="left")
    return pd.DataFrame({"open": g["open"].first(), "close": g["close"].last()}).dropna(how="all")


# ---------------------------------------------------------------- #23: 코인별 움직임 폭(U) 기준 손절·익절·추적 손절·추세 이탈


@dataclass(frozen=True)
class AdaptiveRules:
    ma_bars: int  # 60일선 봉 수 (사는 조건, 익절 조건)
    stop_mult: float  # 손절 = 산 가격 x (1 - stop_mult x U)
    target_mult: float | None = None  # 익절 = 산 가격 x (1 + target_mult x U), 60일선 위에서만
    trail_mult: float | None = None  # 추적 손절 = 산 뒤 종가 최고가 x (1 - trail_mult x U)
    exit_ma_bars: int | None = None  # 종가가 이 평균 아래로 마감하면 판다
    cost: float = 0.0015
    lot_krw: float = 100_000.0
    max_lots: int = 5
    capital: float = 10_000_000.0


def daily_unit(close: pd.DataFrame, days: int = 20) -> pd.DataFrame:
    """U: 최근 days 일 동안 하루(UTC) 종가 변화율 절댓값의 평균. 그 봉이 속한 날 **전날까지** 완결된 날만 쓴다(미래 참조 금지)."""
    daily = close.resample("1D").last()
    u = daily.pct_change().abs().rolling(days, min_periods=days).mean().shift(1)  # 어제까지의 값을 오늘 쓴다
    return u.reindex(close.index, method="ffill")


def simulate_adaptive(open_: pd.DataFrame, close: pd.DataFrame, unit: pd.DataFrame, r: AdaptiveRules,
                      end: pd.Timestamp | None = None) -> LotResult:
    """simulate() 와 같은 틀. 덩이마다 [산 가격, 수량, 산 봉, U, 산 뒤 종가 최고가]를 들고 있다."""
    if end is not None:
        open_, close, unit = open_.loc[:end], close.loc[:end], unit.loc[:end]
    idx = close.index
    ma = close.rolling(r.ma_bars, min_periods=r.ma_bars).mean()
    above = (close > ma).to_numpy()
    xma = close.rolling(r.exit_ma_bars, min_periods=r.exit_ma_bars).mean().to_numpy() if r.exit_ma_bars else None
    o, c, uu = open_.to_numpy(float), close.to_numpy(float), unit.to_numpy(float)
    n, k = c.shape
    last_valid = [int(np.flatnonzero(~np.isnan(c[:, j]))[-1]) if (~np.isnan(c[:, j])).any() else -1 for j in range(k)]
    cash = r.capital
    lots: list[list[list[float]]] = [[] for _ in range(k)]  # [buy_price, qty, buy_i, u, peak]
    eq = np.full(n, np.nan)
    sold: list[dict] = []
    buys = 0
    pend: list[list] = [[] for _ in range(k)]

    def sell(j: int, lot: list[float], i: int, price: float, why: str) -> None:
        nonlocal cash
        proceeds = lot[1] * price * (1 - r.cost) if price > 0 else 0.0
        cash += proceeds
        sold.append({"coin": close.columns[j], "buy_t": idx[int(lot[2])], "sell_t": idx[min(i, n - 1)],
                     "buy_price": lot[0], "sell_price": price, "ret": proceeds / (lot[1] * lot[0] * (1 + r.cost)) - 1,
                     "why": why})

    for i in range(n):
        for j in range(k):
            if pend[j] and not np.isnan(o[i, j]):
                for act in pend[j]:
                    if act[0] == "sell":
                        lot = act[1]
                        if lot in lots[j]:
                            lots[j].remove(lot)
                            sell(j, lot, i, o[i, j], act[2])
                    elif act[0] == "buy" and cash >= r.lot_krw and len(lots[j]) < r.max_lots:
                        cash -= r.lot_krw
                        lots[j].append([o[i, j], r.lot_krw * (1 - r.cost) / o[i, j], float(i), act[1], o[i, j]])
                        buys += 1
                pend[j] = []
            if last_valid[j] >= 0 and i == last_valid[j] + 1 and lots[j]:
                for lot in list(lots[j]):
                    lots[j].remove(lot)
                    sell(j, lot, i, 0.0, "상장폐지")
            if np.isnan(c[i, j]) or i >= last_valid[j] or i == n - 1:
                continue
            up = bool(above[i, j])
            acts = []
            for lot in lots[j]:
                lot[4] = max(lot[4], c[i, j])  # 산 뒤 종가 최고가
                floor = lot[0] * (1 - r.stop_mult * lot[3])
                if r.trail_mult is not None:
                    floor = max(floor, lot[4] * (1 - r.trail_mult * lot[3]))
                if c[i, j] <= floor:
                    acts.append(("sell", lot, "추적 손절" if r.trail_mult is not None and floor > lot[0] * (1 - r.stop_mult * lot[3]) else "손절"))
                elif xma is not None and not np.isnan(xma[i, j]) and c[i, j] < xma[i, j]:
                    acts.append(("sell", lot, "추세 이탈"))
                elif up and r.target_mult is not None and c[i, j] >= lot[0] * (1 + r.target_mult * lot[3]):
                    acts.append(("sell", lot, "익절"))
            if up and len(lots[j]) < r.max_lots and not np.isnan(uu[i, j]):
                acts.append(("buy", float(uu[i, j])))
            pend[j] = acts
        val = cash
        for j in range(k):
            if lots[j] and not np.isnan(c[i, j]):
                val += sum(lot[1] for lot in lots[j]) * c[i, j]
        eq[i] = val
    open_rows = [{"coin": close.columns[j], "buy_price": l[0], "qty": l[1], "buy_t": idx[int(l[2])]}
                 for j in range(k) for l in lots[j]]
    return LotResult(pd.Series(eq, index=idx),
                     pd.DataFrame(sold, columns=["coin", "buy_t", "sell_t", "buy_price", "sell_price", "ret", "why"]),
                     pd.DataFrame(open_rows, columns=["coin", "buy_price", "qty", "buy_t"]), buys)
