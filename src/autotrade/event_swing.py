"""공시(사건) 신호로 사서 N거래일 뒤 파는 계산 — #15 DART 공시 스윙.

run_backtest(비중 방식)와 따로 둔 이유: 신호가 종목마다 띄엄띄엄 오고, 종목마다 보유 기간이 따로 끝난다.

미래 참조 금지:
- 공시는 접수 날짜만 있으므로 **접수일 다음 거래일 시가**에 산다(장중 공시라도).
- 거래대금 거르기는 접수일 **전날까지** 20거래일만 본다.
- 팔기는 산 날부터 hold 거래일 뒤 **시가**. 그날 거래가 없으면 다시 거래되는 첫날 시가.
- 이후 가격이 아예 없으면(상장폐지) 0원으로 계산한다(가장 불리하게). 이 판단만 '뒤에 가격이 다시 없다'는
  사실을 쓰지만, 결과를 나쁘게만 만드는 쪽이라 유리한 미래 참조는 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from autotrade.data import Prices


@dataclass(frozen=True)
class SwingRules:
    hold: int = 20  # 보유 거래일
    slot: float = 0.05  # 신호 하나에 계좌의 몇 %
    max_positions: int | None = 20  # None 이면 제한 없음(참고 계산용, 계좌 곡선은 의미 없음)
    capital: float = 10_000_000
    min_value: float = 500_000_000  # 20거래일 평균 거래대금 하한(원)
    value_window: int = 20
    commission: float = 0.00015
    slippage: float = 0.002


@dataclass
class SwingResult:
    trades: pd.DataFrame
    equity: pd.Series  # 장 마감 기준 계좌 가치
    counts: dict = field(default_factory=dict)  # 빠진 신호 수 (이유별)


def simulate(
    signals: pd.DataFrame,
    prices: Prices,
    bench: str,
    rules: SwingRules,
    sell_tax: Callable[[pd.Timestamp], float],
) -> SwingResult:
    """signals: code, date(접수일), rcept_no 열. prices 에 bench(지수 ETF) 열이 있어야 한다.

    거래일 달력은 bench 시가가 있는 날이다.
    """
    cal = prices.open.index[prices.open[bench].notna()]
    op = prices.open.reindex(cal)
    cl = prices.close.reindex(cal)
    vol = prices.volume.reindex(cal) if prices.volume is not None else None
    value = (cl * vol).fillna(0.0) if vol is not None else None
    last_valid = {c: cl[c].last_valid_index() for c in cl.columns}
    end_i = len(cal) - 1
    buy_rate = rules.commission + rules.slippage

    counts = {"신호": len(signals), "가격없음": 0, "다음거래일없음": 0, "거래대금부족": 0, "산날거래없음": 0,
              "보유중중복": 0, "자리없음": 0}

    # 1) 신호마다 사는 날 정하기 + 거르기
    plan: dict[int, list[dict]] = {}
    for s in signals.sort_values(["date", "rcept_no"]).itertuples(index=False):
        code = s.code
        if code not in op.columns:
            counts["가격없음"] += 1
            continue
        i = int(cal.searchsorted(pd.Timestamp(s.date), side="right"))  # 접수일 다음 거래일
        if i > end_i:
            counts["다음거래일없음"] += 1
            continue
        j0 = int(cal.searchsorted(pd.Timestamp(s.date), side="left"))  # 접수일 자리 (전날까지 = j0 앞)
        if value is not None:
            w = value[code].iloc[max(0, j0 - rules.value_window) : j0]
            if len(w) < rules.value_window or w.mean() < rules.min_value:
                counts["거래대금부족"] += 1
                continue
        if not np.isfinite(op[code].iloc[i]):
            counts["산날거래없음"] += 1
            continue
        plan.setdefault(i, []).append({"code": code, "signal_date": pd.Timestamp(s.date), "rcept_no": s.rcept_no})

    # 2) 날짜 순서대로 사고팔기
    cash = rules.capital
    held: dict[str, dict] = {}
    trades: list[dict] = []
    equity = pd.Series(np.nan, index=cal)
    last_close: dict[str, float] = {}

    def close_pos(code: str, i: int, price: float, status: str) -> None:
        nonlocal cash
        p = held.pop(code)
        d = cal[i]
        sell_rate = rules.commission + rules.slippage + sell_tax(d)
        proceeds = p["shares"] * price * (1 - sell_rate)
        cash += proceeds
        b_exit_i = min(i, p["planned_i"]) if status == "상장폐지" else i
        b_ret = op[bench].iloc[b_exit_i] / op[bench].iloc[p["entry_i"]] - 1
        ret = proceeds / p["cost"] - 1
        trades.append({
            "code": code, "rcept_no": p["rcept_no"], "signal_date": p["signal_date"],
            "entry_date": cal[p["entry_i"]], "exit_date": d, "entry_price": p["price"], "exit_price": price,
            "ret": ret, "bench_ret": b_ret, "excess": ret - b_ret, "status": status,
        })

    for i, d in enumerate(cal):
        # 2-1) 시가에 팔기
        for code in list(held):
            p = held[code]
            if i < p["planned_i"]:
                continue
            o = op[code].iloc[i]
            if np.isfinite(o):
                close_pos(code, i, float(o), "정상" if i == p["planned_i"] else "늦게팖")
            elif last_valid[code] is None or last_valid[code] < d and last_valid[code] < cal[end_i]:
                close_pos(code, i, 0.0, "상장폐지")
        # 2-2) 시가에 사기
        eq_prev = equity.iloc[i - 1] if i > 0 else rules.capital
        for sig in plan.get(i, []):
            code = sig["code"]
            if code in held:
                counts["보유중중복"] += 1
                continue
            if rules.max_positions is not None and len(held) >= rules.max_positions:
                counts["자리없음"] += 1
                continue
            o = float(op[code].iloc[i])
            if rules.max_positions is None:
                cost = rules.capital * rules.slot  # 참고 계산: 돈 제약 없음
            else:
                cost = min(eq_prev * rules.slot, cash)
                if cost <= 0:
                    counts["자리없음"] += 1
                    continue
                cash -= cost
            held[code] = {
                "shares": cost / (o * (1 + buy_rate)), "cost": cost, "price": o, "entry_i": i,
                "planned_i": i + rules.hold, **sig,
            }
        # 2-3) 장 마감 평가
        for code in held:
            c = cl[code].iloc[i]
            if np.isfinite(c):
                last_close[code] = float(c)
        equity.iloc[i] = cash + sum(p["shares"] * last_close.get(c, p["price"]) for c, p in held.items())

    # 3) 기간 끝까지 못 판 것: 마지막 종가로 평가(파는 비용 포함), 표시
    for code in list(held):
        price = last_close.get(code, held[code]["price"])
        close_pos(code, end_i, price, "미완료")

    cols = ["code", "rcept_no", "signal_date", "entry_date", "exit_date", "entry_price", "exit_price",
            "ret", "bench_ret", "excess", "status"]
    return SwingResult(pd.DataFrame(trades, columns=cols), equity, counts)


# ---------------------------------------------------------------- 판정


def day_t(trades: pd.DataFrame) -> tuple[float, int]:
    """같은 날 산 신호는 하루로 묶어(그날 평균) t값을 낸다."""
    by_day = trades.groupby("entry_date")["excess"].mean()
    n = len(by_day)
    if n < 2 or by_day.std(ddof=1) == 0:
        return float("nan"), n
    return float(by_day.mean() / (by_day.std(ddof=1) / np.sqrt(n))), n


def drop_best_months(trades: pd.DataFrame, k: int = 3) -> float:
    """초과 수익 합이 가장 큰 k개월(산 날 기준)을 뺀 평균 초과 수익."""
    month = trades["entry_date"].dt.to_period("M")
    best = trades.groupby(month)["excess"].sum().nlargest(k).index
    rest = trades[~month.isin(best)]
    return float(rest["excess"].mean()) if len(rest) else float("nan")


def ret_over_vol(series: pd.Series) -> tuple[float, float, float]:
    """(1년 평균 수익률, 1년 흔들림, 수익/흔들림)."""
    s = series.dropna()
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1
    vol = s.pct_change().dropna().std() * np.sqrt(252)
    return float(cagr), float(vol), float(cagr / vol) if vol > 0 else float("nan")


def judge(res: SwingResult, bench_close: pd.Series, start: str, min_signals: int = 30) -> dict:
    t = res.trades
    eq = res.equity.loc[pd.Timestamp(start):]
    last_exit = t["exit_date"].max() if len(t) else eq.index[-1]
    eq = eq.loc[:last_exit]
    b = bench_close.reindex(eq.index).ffill()
    tt, ndays = day_t(t) if len(t) else (float("nan"), 0)
    s_c, s_v, s_r = ret_over_vol(eq)
    b_c, b_v, b_r = ret_over_vol(b)
    out = {
        "거래 수": len(t),
        "산 날 수": ndays,
        "평균 초과 수익": float(t["excess"].mean()) if len(t) else float("nan"),
        "평균 수익(비용 뺀 뒤)": float(t["ret"].mean()) if len(t) else float("nan"),
        "이긴 비율": float((t["excess"] > 0).mean()) if len(t) else float("nan"),
        "t값(하루 묶음)": tt,
        "상위 3개월 뺀 평균 초과": drop_best_months(t) if len(t) else float("nan"),
        "계좌 1년 평균": s_c, "계좌 흔들림": s_v, "계좌 수익/흔들림": s_r,
        "KODEX200 1년 평균": b_c, "KODEX200 흔들림": b_v, "KODEX200 수익/흔들림": b_r,
        "상장폐지 거래": int((t["status"] == "상장폐지").sum()),
        "미완료 거래": int((t["status"] == "미완료").sum()),
    }
    c = [
        out["평균 초과 수익"] > 0,
        out["t값(하루 묶음)"] >= 2.5,
        out["상위 3개월 뺀 평균 초과"] > 0,
        out["계좌 수익/흔들림"] > out["KODEX200 수익/흔들림"],
    ]
    out["기준1~4"] = c
    if len(t) < min_signals:
        out["판정"] = "신호 부족(판정 안 함)"
    else:
        out["판정"] = "합격" if all(c) else "탈락"
    return out
