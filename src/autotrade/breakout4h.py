"""#20 비트코인 선물 4시간봉 돌파 (롱·숏, 1배) 계산.

규칙은 docs/experiments.md #20. 봉 하나가 끝난 뒤(종가) 결정하고 **다음 봉 시가**에 체결한다.
- 롱: 종가 > 직전 20봉 최고가 (직전 봉 종가는 그 봉의 직전 20봉 최고가 이하 = 새 돌파)
- 숏: 종가 < 직전 20봉 최저가 (새 돌파)
- 청산: 롱은 종가 < 직전 10봉 최저가, 숏은 종가 > 직전 10봉 최고가, 또는 24봉 보유 → 다음 봉 시가
- 비용: 한쪽 cost(거래 금액 대비), 펀딩비: 보유 중(들어간 시각 < t <= 나온 시각) 정산 시각마다 -방향 x 비율 x 거래 금액
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BreakoutRules:
    entry: int = 20
    exit: int = 10
    max_bars: int = 24
    cost: float = 0.0010  # 한쪽 (수수료 0.05% + 체결 손해 0.05%)
    sides: tuple[int, ...] = (1, -1)  # (1,) 이면 롱만, (-1,) 이면 숏만 (참고 계산용)


@dataclass
class BreakoutResult:
    trades: pd.DataFrame
    equity: pd.Series  # 봉 종가 기준 계좌 (처음 1.0)


def signals(bars: pd.DataFrame, r: BreakoutRules) -> pd.DataFrame:
    """봉마다 (종가 기준) 롱/숏 새 돌파, 청산 조건. 직전 N봉 = 그 봉을 뺀 앞의 N개."""
    h, l, c = bars["high"], bars["low"], bars["close"]
    hi_e = h.shift(1).rolling(r.entry).max()
    lo_e = l.shift(1).rolling(r.entry).min()
    hi_x = h.shift(1).rolling(r.exit).max()
    lo_x = l.shift(1).rolling(r.exit).min()
    long_ = (c > hi_e) & (c.shift(1) <= hi_e.shift(1))
    short = (c < lo_e) & (c.shift(1) >= lo_e.shift(1))
    return pd.DataFrame({"long": long_, "short": short, "exit_long": c < lo_x, "exit_short": c > hi_x})


def run(
    bars: pd.DataFrame,
    funding: pd.Series,
    r: BreakoutRules,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> BreakoutResult:
    """bars: 시작 시각 인덱스, open/high/low/close. funding: 정산 시각 인덱스, 비율.

    start 이전 봉은 지표 준비에만 쓰고(그때는 들어가지 않음), end 이후 봉은 넣지 않는다.
    끝까지 못 판 포지션은 마지막 봉 종가에 판다(비용 포함, status='기간끝').
    """
    bars = bars.loc[:end]
    sig = signals(bars, r)
    times = bars.index
    o, c = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
    fund = funding.sort_index()
    first = int(times.searchsorted(start))
    n = len(bars)

    eq = 1.0  # 현금(포지션 없을 때 계좌)
    pos = None  # dict(side, e, units, base, fund)
    trades: list[dict] = []
    curve = np.full(n, np.nan)

    def funding_paid(p: dict, t1) -> float:
        """들어간 시각 < 정산 시각 <= t1 의 펀딩비(받으면 +). 그 시각 가격은 그 시각에 시작하는 봉의 시가."""
        f = fund[(fund.index > p["t_entry"]) & (fund.index <= t1)]
        if not len(f):
            return 0.0
        px = bars["open"].reindex(f.index, method="ffill").to_numpy(float)
        return -p["side"] * float((f.to_numpy(float) * p["units"] * px).sum())

    def close(i_exit: int, price: float, t_exit, status: str) -> None:
        nonlocal eq, pos
        p = pos
        pnl = p["side"] * p["units"] * (price - p["entry_price"])
        fpay = funding_paid(p, t_exit)
        new = p["base"] - p["base"] * r.cost + pnl - p["units"] * price * r.cost + fpay
        trades.append({
            "side": p["side"], "entry_time": p["t_entry"], "exit_time": t_exit, "entry_price": p["entry_price"],
            "exit_price": price, "bars": i_exit - p["e"], "funding": fpay / p["base"], "ret": new / p["base"] - 1,
            "status": status,
        })
        eq = new
        pos = None

    pending = None  # 지난 봉 종가에 정한 주문: (종류, 청산 이유, 새 방향)
    for i in range(n):
        # 1) 이번 봉 시가: 지난 봉 종가에 정한 주문 체결
        if pending is not None:
            act = pending
            if act[0] == "exit" or act[0] == "reverse":
                close(i, o[i], times[i], act[1])
            if act[0] in ("enter", "reverse"):
                side = act[2]
                pos = {"side": side, "e": i, "units": eq / o[i], "base": eq, "entry_price": o[i], "t_entry": times[i]}
        pending = None
        # 2) 이번 봉 종가: 평가
        if pos is not None:
            fpay = funding_paid(pos, times[i])
            curve[i] = pos["base"] * (1 - r.cost) + pos["side"] * pos["units"] * (c[i] - pos["entry_price"]) + fpay
        else:
            curve[i] = eq
        if i < first or i == n - 1:
            continue
        # 3) 다음 봉 시가에 할 일 정하기
        exit_now = False
        if pos is not None:
            s = pos["side"]
            hit = sig["exit_long"].iat[i] if s == 1 else sig["exit_short"].iat[i]
            timeout = i >= pos["e"] + r.max_bars - 1
            if hit or timeout:
                exit_now = True
                why = "반대 10봉" if hit else "24봉"
        new_side = None
        if pos is None or exit_now:
            if 1 in r.sides and sig["long"].iat[i]:
                new_side = 1
            elif -1 in r.sides and sig["short"].iat[i]:
                new_side = -1
            if pos is not None and new_side == pos["side"]:
                new_side = None  # 같은 방향 재진입은 새 돌파가 아님(청산 조건과 동시에 날 수 없음)
        if exit_now and new_side is not None:
            pending = ("reverse", why, new_side)
        elif exit_now:
            pending = ("exit", why, None)
        elif new_side is not None:
            pending = ("enter", None, new_side)

    if pos is not None:
        close(n - 1, c[-1], times[-1], "기간끝")
        curve[-1] = eq
    equity = pd.Series(curve, index=times).iloc[first:]
    cols = ["side", "entry_time", "exit_time", "entry_price", "exit_price", "bars", "funding", "ret", "status"]
    return BreakoutResult(pd.DataFrame(trades, columns=cols), equity)


# ---------------------------------------------------------------- 판정


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def ret_over_vol(equity: pd.Series) -> tuple[float, float, float]:
    """하루 단위(UTC 마지막 봉)로 (1년 평균, 1년 흔들림, 수익/흔들림). 코인은 1년 365일."""
    d = equity.resample("1D").last().dropna()
    years = (d.index[-1] - d.index[0]).days / 365.0
    cagr = (d.iloc[-1] / d.iloc[0]) ** (1 / years) - 1
    vol = d.pct_change().dropna().std() * np.sqrt(365)
    return float(cagr), float(vol), float(cagr / vol) if vol > 0 else float("nan")


def hold_curve(bars: pd.DataFrame, start, end, cost: float) -> pd.Series:
    """그냥 보유: 첫 봉 시가에 한 번 사는 비용만."""
    b = bars.loc[start:end]
    return (1 - cost) * b["close"] / b["open"].iloc[0]


def drop_best_months_total(trades: pd.DataFrame, k: int = 3) -> float:
    """수익이 가장 큰 k개월(들어간 달 기준, 로그수익 합)을 뺀 나머지 거래의 누적 수익."""
    m = pd.to_datetime(trades["entry_time"]).dt.to_period("M")
    lr = np.log1p(trades["ret"])
    best = lr.groupby(m).sum().nlargest(k).index
    rest = trades["ret"][~m.isin(best)]
    return float(np.prod(1 + rest) - 1)


def judge(res: BreakoutResult, hold: pd.Series) -> dict:
    t = res.trades
    n = len(t)
    mean = float(t["ret"].mean()) if n else float("nan")
    tval = float(mean / (t["ret"].std(ddof=1) / np.sqrt(n))) if n > 1 else float("nan")
    s_c, s_v, s_r = ret_over_vol(res.equity)
    h_c, h_v, h_r = ret_over_vol(hold)
    out = {
        "거래 수": n,
        "롱/숏": f"{int((t['side'] == 1).sum())}/{int((t['side'] == -1).sum())}",
        "거래당 평균 수익": mean,
        "이긴 비율": float((t["ret"] > 0).mean()) if n else float("nan"),
        "t값": tval,
        "전체 수익": float(res.equity.iloc[-1] / res.equity.iloc[0] - 1),
        "상위 3개월 뺀 누적 수익": drop_best_months_total(t) if n else float("nan"),
        "1년 평균": s_c, "흔들림": s_v, "수익/흔들림": s_r,
        "가장 크게 떨어졌을 때": max_drawdown(res.equity),
        "보유 1년 평균": h_c, "보유 흔들림": h_v, "보유 수익/흔들림": h_r, "보유 MDD": max_drawdown(hold),
        "펀딩비 합(거래 금액 대비)": float(t["funding"].sum()) if n else 0.0,
        "평균 보유 봉": float(t["bars"].mean()) if n else float("nan"),
    }
    c = [n >= 50, mean > 0 and tval >= 2.5, out["상위 3개월 뺀 누적 수익"] > 0, s_r > h_r,
         out["가장 크게 떨어졌을 때"] >= -0.25]
    out["기준1~5"] = c
    out["판정"] = "합격" if all(c) else "탈락"
    return out
