"""하루·주 단위 모의 전략(#12·#13·#14)과 대조군. 규칙은 docs/experiments.md 에 등록한 그대로.

저장해 두는 상태가 없다. 시작일 이후 시세에 백테스트 엔진(run_backtest)을 그대로 돌려 **매번 다시 계산**하므로
컴퓨터가 꺼져 있던 날도 빠지지 않고, 체결은 엔진 규칙대로 결정 다음 봉 시가다.
아직 끝나지 않은 오늘 봉은 표의 마지막 줄로 들어가는데, 엔진은 마지막 줄에서 결정을 내리지 않으므로 평가에만 쓰인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from autotrade.config import CostModel
from autotrade.data import Prices
from autotrade.engine import BacktestResult, run_backtest
from autotrade.strategies.base import Strategy
from autotrade.strategies.buy_and_hold import BuyAndHold

CAPITAL = 10_000_000
ETF_COSTS = CostModel(commission=0.00015, sell_tax=0.0, slippage=0.0005)
COIN_COSTS = CostModel(commission=0.0005, sell_tax=0.0, slippage=0.0010)  # 한쪽 0.15%

ETF_NAMES = {"069500": "한국주식", "143850": "미국주식", "148070": "국고채10년", "138230": "달러", "411060": "금",
             "153130": "단기채권"}
ETF5 = ["069500", "143850", "148070", "138230", "411060"]
ETF4 = ["069500", "143850", "148070", "138230"]
ETF_STOCKS = ["069500", "143850"]
ETF_SAFE = "153130"
COINS4 = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
BTC = "KRW-BTC"
FUNDING, KIMCHI = "_funding", "_kimchi"  # #13 보조 자료를 가격 표의 열로 넘긴다(전략이 따로 자료를 읽지 않도록)

START_COIN = pd.Timestamp("2026-11-15")  # 이 날짜(UTC) 캔들 마감 결정부터
START_ETF = pd.Timestamp("2026-11-20")  # #12 첫 결정(금요일 마감). 대조군도 같은 날 결정 → 같은 날(11-23) 시가 체결


class StartAt(Strategy):
    """start 이전에는 현금. 그 뒤로는 안쪽 전략 그대로."""

    def __init__(self, inner: Strategy, start: pd.Timestamp) -> None:
        self.inner, self.start = inner, pd.Timestamp(start)
        self.name = inner.name

    def target_weights(self, history: Prices) -> pd.Series:
        if history.dates[-1] < self.start:
            return pd.Series(dtype=float)
        return self.inner.target_weights(history)


class InverseVolTrend(Strategy):
    """#12: 흔들림의 역수로 비중, 주식만 120일 평균 아래면 피난처로."""

    name = "inverse_vol_trend"

    def __init__(self, assets: Sequence[str] = ETF5, stocks: Sequence[str] = ETF_STOCKS, safe: str = ETF_SAFE,
                 vol_window: int = 60, ma_window: int = 120) -> None:
        self.assets, self.stocks, self.safe = list(assets), set(stocks), safe
        self.vol_window, self.ma_window = vol_window, ma_window

    def table(self, history: Prices) -> list[dict] | None:
        """자산별 흔들림·기본 비중·추세. 자료가 모자란 자산이 있으면 None."""
        rows = []
        for a in self.assets:
            c = history.close[a].dropna()
            if len(c) < max(self.ma_window, self.vol_window + 1):
                return None
            sd = float(c.pct_change().iloc[-self.vol_window:].std())
            if not sd > 0:
                return None
            ma = float(c.iloc[-self.ma_window:].mean())
            rows.append({"ticker": a, "vol": sd, "close": float(c.iloc[-1]), "ma": ma,
                         "above": bool(c.iloc[-1] >= ma), "is_stock": a in self.stocks})
        total = sum(1 / r["vol"] for r in rows)
        for r in rows:
            r["base"] = (1 / r["vol"]) / total
            r["hold"] = not (r["is_stock"] and not r["above"])
        return rows

    def target_weights(self, history: Prices) -> pd.Series:
        rows = self.table(history)
        if rows is None:
            return pd.Series({self.safe: 1.0})
        w: dict[str, float] = {}
        for r in rows:
            key = r["ticker"] if r["hold"] else self.safe
            w[key] = w.get(key, 0.0) + r["base"]
        return pd.Series(w, dtype=float)


class WeeklySharpeRotation(Strategy):
    """#14: 21일 수익률 ÷ 21일 흔들림 상위 2개(수익률이 플러스인 것만)에 50%씩."""

    name = "weekly_sharpe_rotation"

    def __init__(self, coins: Sequence[str] = COINS4, window: int = 21, top: int = 2) -> None:
        self.coins, self.window, self.top = list(coins), window, top

    def table(self, history: Prices) -> list[dict]:
        rows = []
        for coin in self.coins:
            c = history.close[coin].dropna()
            if len(c) < self.window + 1:
                continue
            ret = float(c.iloc[-1] / c.iloc[-1 - self.window] - 1)
            sd = float(c.pct_change().iloc[-self.window:].std())
            if sd > 0:
                rows.append({"ticker": coin, "ret": ret, "vol": sd, "score": ret / sd})
        rows.sort(key=lambda r: r["score"], reverse=True)
        picked = [r["ticker"] for r in rows if r["ret"] > 0][: self.top]
        for r in rows:
            r["pick"] = r["ticker"] in picked
        return rows

    def target_weights(self, history: Prices) -> pd.Series:
        return pd.Series({r["ticker"]: 1.0 / self.top for r in self.table(history) if r["pick"]}, dtype=float)


class FundingKimchi(Strategy):
    """#13: 펀딩비·김치 프리미엄이 과열이 아니고 20일 평균 위일 때만 비트코인 보유.

    들고 있는지에 따라 조건이 달라서, start 부터 하루씩 규칙을 따라가며 지금 상태를 구한다(넘겨받은 history 만 사용).
    """

    name = "funding_kimchi"
    BUY_FUNDING, SELL_FUNDING = 0.0003, 0.0006
    BUY_PCT, SELL_PCT = 0.80, 0.95
    MA, BUFFER, LOOKBACK = 20, 0.985, 90

    def __init__(self, start: pd.Timestamp, asset: str = BTC) -> None:
        self.start, self.asset = pd.Timestamp(start), asset

    def frame(self, history: Prices) -> pd.DataFrame:
        c = history.close[self.asset]
        k = history.close[KIMCHI]
        prev = k.shift(1).rolling(self.LOOKBACK, min_periods=self.LOOKBACK)  # 오늘을 뺀 직전 90일
        return pd.DataFrame({"close": c, "funding": history.close[FUNDING], "kimchi": k,
                             "ma": c.rolling(self.MA, min_periods=self.MA).mean(),
                             "k_buy": prev.quantile(self.BUY_PCT), "k_sell": prev.quantile(self.SELL_PCT)})

    def walk(self, history: Prices) -> tuple[bool, dict | None]:
        """(지금 보유 여부, 마지막 날의 조건 표)."""
        df = self.frame(history)
        holding, last = False, None
        for row in df.loc[self.start:].itertuples():
            complete = not any(pd.isna(v) for v in row[1:])
            if complete:
                sell = {"funding": row.funding > self.SELL_FUNDING, "kimchi": row.kimchi > row.k_sell,
                        "trend": row.close < row.ma * self.BUFFER}
                buy = {"funding": row.funding <= self.BUY_FUNDING, "kimchi": row.kimchi <= row.k_buy,
                       "trend": row.close > row.ma}
                if holding and any(sell.values()):
                    holding = False
                elif not holding and all(buy.values()):
                    holding = True
            else:
                sell = buy = None
            last = {"date": row.Index, "complete": complete, "buy": buy, "sell": sell, "holding": holding,
                    **{f: (None if pd.isna(getattr(row, f)) else float(getattr(row, f)))
                       for f in ("close", "funding", "kimchi", "ma", "k_buy", "k_sell")}}
        return holding, last

    def target_weights(self, history: Prices) -> pd.Series:
        holding, _ = self.walk(history)
        return pd.Series({self.asset: 1.0} if holding else {}, dtype=float)


# ── 자료 만들기 ──
def funding_average(funding: pd.Series, dates: pd.DatetimeIndex, count: int = 9) -> pd.Series:
    """날짜 D(UTC) 캔들이 끝나는 시각 **이전에** 확정된 최근 count 회 펀딩비의 평균. 자료가 끊겼으면 NaN."""
    out = {}
    for d in dates:
        end = d + pd.Timedelta(days=1)
        f = funding[funding.index < end].iloc[-count:]
        fresh = len(f) == count and f.index[-1] >= end - pd.Timedelta(hours=9)
        out[d] = float(f.mean()) if fresh else float("nan")
    return pd.Series(out, dtype=float)


def previous_close(series: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """날짜 D 에 'D 보다 앞선 날짜의 마지막 값'을 붙인다 (환율: 결정할 때 이미 확정돼 있던 값만, 주말은 그 전 값)."""
    s = series.dropna().sort_index()
    shifted = pd.Series(s.values, index=s.index + pd.Timedelta(days=1))  # D 의 값은 D+1 부터 쓸 수 있음
    shifted = shifted[~shifted.index.duplicated(keep="last")]
    return shifted.reindex(dates.union(shifted.index)).ffill().reindex(dates)


def with_funding_kimchi(btc: Prices, funding: pd.Series, usdt_close: pd.Series, usdkrw: pd.Series) -> Prices:
    """업비트 비트코인 일봉 표에 펀딩 평균·김치 프리미엄 열을 붙인다(시가는 비워 둬서 거래 대상이 아님)."""
    dates = btc.dates
    fx = previous_close(usdkrw, dates)
    kimchi = btc.close[BTC] / (usdt_close.reindex(dates) * fx) - 1
    close = btc.close[[BTC]].copy()
    close[FUNDING] = funding_average(funding, dates)
    close[KIMCHI] = kimchi
    open_ = btc.open[[BTC]].copy()
    open_[FUNDING] = float("nan")
    open_[KIMCHI] = float("nan")
    return Prices(open_, close)


# ── 다시 계산 ──
@dataclass(frozen=True)
class SlowSpec:
    key: str
    name: str
    kind: str  # "strategy" | "control"
    freq: str
    costs: CostModel
    integer_shares: bool
    start: pd.Timestamp
    controls: tuple[str, ...] = ()  # 이겨야 하는 대조군 key (전부)


SPECS = {
    "s12": SlowSpec("s12", "#12 흔들림 맞춰 나눠 담기 + 주식 방어", "strategy", "W", ETF_COSTS, True, START_ETF,
                    ("c_etf4", "c_etf5")),
    "s13": SlowSpec("s13", "#13 펀딩비·김치 프리미엄", "strategy", "D", COIN_COSTS, False, START_COIN, ("c_btc",)),
    "s14": SlowSpec("s14", "#14 코인 4개 주간 갈아타기", "strategy", "W", COIN_COSTS, False, START_COIN, ("c_btc",)),
    "c_btc": SlowSpec("c_btc", "비트코인 그냥 보유", "control", "D", COIN_COSTS, False, START_COIN),
    "c_etf4": SlowSpec("c_etf4", "ETF 4개 나눠 담기", "control", "D", ETF_COSTS, True, START_ETF),
    "c_etf5": SlowSpec("c_etf5", "ETF 5개 나눠 담기 (금 포함)", "control", "D", ETF_COSTS, True, START_ETF),
    "c_kodex": SlowSpec("c_kodex", "KODEX 200 보유", "control", "D", ETF_COSTS, True, START_ETF),
}


def make_strategy(key: str) -> Strategy:
    return {"s12": InverseVolTrend, "s14": WeeklySharpeRotation, "s13": lambda: FundingKimchi(START_COIN),
            "c_btc": BuyAndHold, "c_etf4": BuyAndHold, "c_etf5": BuyAndHold, "c_kodex": BuyAndHold}[key]()


def replay(key: str, prices: Prices, capital: float = CAPITAL) -> BacktestResult:
    spec = SPECS[key]
    return run_backtest(prices, StartAt(make_strategy(key), spec.start), spec.freq, spec.costs, capital,
                        integer_shares=spec.integer_shares, min_trade_frac=0.03)


def summarize(key: str, res: BacktestResult, prices: Prices, names: dict[str, str] | None = None) -> dict:
    """화면에 보낼 요약: 지금 값, 보유, 자산 곡선, 거래 기록."""
    spec, names = SPECS[key], names or {}
    eq = res.equity
    first = max(eq.index.searchsorted(spec.start) - 1, 0)  # 시작 직전 날부터 그린다
    curve = eq.iloc[first:]
    value = float(eq.iloc[-1])
    last_close = prices.close.ffill().iloc[-1]
    qty: dict[str, float] = {}
    for tr in res.trades.itertuples():
        qty[tr.ticker] = qty.get(tr.ticker, 0.0) + (tr.qty if tr.side == "buy" else -tr.qty)
    holdings = [{"ticker": t, "name": names.get(t, t.replace("KRW-", "")), "value": float(q * last_close[t]),
                 "weight": float(q * last_close[t] / value)} for t, q in qty.items() if q * last_close[t] > 1]
    holdings.sort(key=lambda h: -h["value"])
    invested = sum(h["value"] for h in holdings)
    peak = curve.cummax()
    return {
        "key": key, "name": spec.name, "kind": spec.kind, "controls": list(spec.controls),
        "start": spec.start.strftime("%Y-%m-%d"), "started": bool(prices.dates[-1] >= spec.start),
        "capital": float(eq.iloc[first]), "value": value, "ret": value / float(eq.iloc[first]) - 1,
        "max_drawdown": float((curve / peak - 1).min()), "cash": value - invested, "holdings": holdings,
        "curve": [[d.strftime("%Y-%m-%d"), round(float(v))] for d, v in curve.items()],
        "last_bar": prices.dates[-1].strftime("%Y-%m-%d"),
        "trades": [{"date": tr.date.strftime("%Y-%m-%d"), "ticker": tr.ticker, "name": names.get(tr.ticker, tr.ticker.replace("KRW-", "")),
                    "side": tr.side, "price": float(tr.price), "amount": float(tr.amount), "cost": float(tr.cost)}
                   for tr in res.trades.itertuples()],
        "total_cost": float(res.total_cost),
    }


def decisions(key: str, res: BacktestResult) -> dict[str, dict[str, float]]:
    """시작일 이후 결정일별 목표 비중 {날짜: {종목: 비중}} (0 인 종목은 뺀다). 결정을 그때그때 저장해 두는 데 쓴다."""
    spec = SPECS[key]
    out = {}
    for d, row in res.decisions.loc[res.decisions.index >= spec.start].iterrows():
        out[d.strftime("%Y-%m-%d")] = {t: round(float(w), 4) for t, w in row.items() if pd.notna(w) and abs(w) > 1e-9}
    return out
