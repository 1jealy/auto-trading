"""실험 #19: 2018년 초(2017-12-31 시가총액) 상위 코인 10개 — 나눠 담기 vs 60일 평균 규칙 (방법 A).
규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp19_top10_2018.py practice      # 연습 기간
    python scripts/exp19_top10_2018.py validation    # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from autotrade import CostModel, Prices, run_backtest  # noqa: E402
from autotrade.metrics import summarize  # noqa: E402
from autotrade.paper.slow import StartAt  # noqa: E402
from autotrade.strategies import BuyAndHold, TrendFilter  # noqa: E402

COINS = ["BTC", "XRP", "ETH", "BCH", "ADA", "LTC", "IOTA", "XEM", "DASH", "XLM"]  # 코인마켓캡 2017-12-31 1~10위
DATA_START, DATA_END = "2017-12-31", "2026-10-31"
DECIDE_FROM = pd.Timestamp("2018-02-28")  # 이날 마감 결정 → 2018-03-01 시가 체결
PERIODS = {"practice": ("2018-03-01", "2021-12-31"), "validation": ("2022-01-01", "2026-10-31")}
WINDOW = 60
MIN_TRADE = 0.03
COSTS = CostModel(commission=0.0015, sell_tax=0.0, slippage=0.0)
CACHE = ROOT / "data" / "cache" / "exp19"
COLS = ["total_return", "cagr", "volatility", "sharpe", "max_drawdown"]
NAMES = {"rule": "60일 평균 규칙", "equal": "10개 나눠 담기", "btc": "비트코인 보유"}
UA = {"User-Agent": "Mozilla/5.0 (research; public market data)"}


# ── 자료 ──
def yahoo(coin: str) -> pd.DataFrame:
    path = CACHE / f"yahoo_{coin}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    p1 = int(pd.Timestamp(DATA_START).timestamp())
    p2 = int((pd.Timestamp(DATA_END) + pd.Timedelta(days=1)).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{coin}-USD",
                     params={"period1": p1, "period2": p2, "interval": "1d"}, headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q["open"], "Close": q["close"]},
                      index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    df = df[~df.index.duplicated(keep="last")].dropna().loc[DATA_START:DATA_END]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def binance_month(symbol: str, month: str) -> pd.DataFrame | None:
    path = CACHE / "binance" / f"{symbol}-1d-{month}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
    r = requests.get(url, headers=UA, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]), header=None)
    raw = raw[pd.to_numeric(raw[0], errors="coerce").notna()]  # 머리글 줄이 있는 파일 대비
    ts = raw[0].astype("int64")
    unit = "us" if ts.iloc[0] > 10**14 else "ms"  # 2025년부터는 마이크로초
    df = pd.DataFrame({"Open": raw[1].astype(float).values, "Close": raw[4].astype(float).values},
                      index=pd.to_datetime(ts, unit=unit).dt.normalize().values)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def iota() -> tuple[pd.DataFrame, list[str]]:
    months = pd.period_range(DATA_START[:7], DATA_END[:7], freq="M").strftime("%Y-%m")
    parts, missing = {"IOTABTC": [], "BTCUSDT": []}, []
    for m in months:
        for sym in parts:
            df = binance_month(sym, m)
            if df is None:
                missing.append(f"{sym} {m}")
            else:
                parts[sym].append(df)
    a, b = (pd.concat(parts[s]).sort_index() for s in ("IOTABTC", "BTCUSDT"))
    a, b = a[~a.index.duplicated()], b[~b.index.duplicated()]
    idx = a.index.intersection(b.index)
    out = pd.DataFrame({"Open": a.loc[idx, "Open"] * b.loc[idx, "Open"],
                        "Close": a.loc[idx, "Close"] * b.loc[idx, "Close"]}).loc[DATA_START:DATA_END]
    return out, missing


def load() -> Prices:
    frames = {c: yahoo(c) for c in COINS if c != "IOTA"}
    frames["IOTA"], missing = iota()
    if missing:
        print("바이낸스에서 빠진 달:", ", ".join(missing))
    for c in COINS:
        df = frames[c]
        gaps = df.index.to_series().diff().dt.days.gt(1).sum()
        jumps = df.index[df["Close"].pct_change().abs() > 0.5].date.tolist()
        print(f"  {c:5s} {df.index[0].date()} ~ {df.index[-1].date()} {len(df)}일 빠진 곳 {gaps}"
              + (f"  하루 50% 넘는 변화: {jumps}" if jumps else ""))
    open_ = pd.DataFrame({c: frames[c]["Open"] for c in COINS}).sort_index()
    close = pd.DataFrame({c: frames[c]["Close"] for c in COINS}).sort_index()
    return Prices(open_, close)


# ── 계산 ──
def run(prices: Prices) -> dict:
    kw = {"integer_shares": False}
    return {
        "rule": run_backtest(prices, StartAt(TrendFilter(COINS, None, WINDOW), DECIDE_FROM), "D", COSTS,
                             min_trade_frac=MIN_TRADE, **kw),
        "equal": run_backtest(prices, StartAt(BuyAndHold(), DECIDE_FROM), "M", COSTS, **kw),
        "btc": run_backtest(prices.select(["BTC"]), StartAt(BuyAndHold(), DECIDE_FROM), "M", COSTS, **kw),
    }


def window(eq: pd.Series, a: str, b: str) -> pd.Series:
    """a 직전 마감 값부터 b 까지 (기간 첫날의 움직임도 포함)."""
    before = eq.loc[:pd.Timestamp(a) - pd.Timedelta(days=1)]
    part = eq.loc[a:b]
    return pd.concat([before.iloc[-1:], part]) if len(before) else part


def table(res: dict, a: str, b: str) -> pd.DataFrame:
    t = pd.DataFrame({NAMES[k]: summarize(window(r.equity, a, b), 365) for k, r in res.items()}).T[COLS]
    t.columns = ["전체 수익률", "연평균 수익률", "흔들림", "수익/흔들림", "최대 하락폭"]
    return t


def show(t: pd.DataFrame) -> str:
    s = t.astype(object)
    for c in t.columns:
        s[c] = t[c].map(("{:.2f}" if c == "수익/흔들림" else "{:+.1%}").format)
    return s.to_string()


def per_coin(prices: Prices, a: str, b: str) -> pd.DataFrame:
    rows = {}
    for c in COINS:
        p = prices.select([c])
        rule = run_backtest(p, StartAt(TrendFilter([c], None, WINDOW), DECIDE_FROM), "D", COSTS, integer_shares=False)
        hold = run_backtest(p, StartAt(BuyAndHold(), DECIDE_FROM), "M", COSTS, integer_shares=False)
        sr, sh = summarize(window(rule.equity, a, b), 365), summarize(window(hold.equity, a, b), 365)
        held = (rule.decisions.loc[a:b][c] > 0).mean()
        rows[c] = {"규칙 연평균": sr["cagr"], "보유 연평균": sh["cagr"], "규칙 최대하락": sr["max_drawdown"],
                   "보유 최대하락": sh["max_drawdown"], "들고 있던 날": held}
    return pd.DataFrame(rows).T


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in PERIODS:
        sys.exit("practice 또는 validation 을 지정하세요")
    a, b = PERIODS[mode]
    print("자료:")
    prices = load()
    res = run(prices)
    t = table(res, a, b)
    print(f"\n[{'연습' if mode == 'practice' else '검증'}] {a} ~ {b}, 한쪽 비용 0.15%\n{show(t)}")

    r, e = t.loc[NAMES["rule"]], t.loc[NAMES["equal"]]
    c1 = r["수익/흔들림"] > e["수익/흔들림"]
    c2 = r["최대 하락폭"] > e["최대 하락폭"]
    c3 = r["연평균 수익률"] >= e["연평균 수익률"] - 0.01
    print("\n기준 확인 (60일 규칙 vs 10개 나눠 담기):")
    print(f"  1. 수익/흔들림 {r['수익/흔들림']:.2f} > {e['수익/흔들림']:.2f} → {'통과' if c1 else '미달'}")
    print(f"  2. 최대 하락 {r['최대 하락폭']:.1%} 이 {e['최대 하락폭']:.1%} 보다 작음 → {'통과' if c2 else '미달'}")
    print(f"  3. 연평균 {r['연평균 수익률']:+.1%} ≥ {e['연평균 수익률']:+.1%} − 1%p → {'통과' if c3 else '미달'}")
    print(f"  → 이 기간 {'통과' if c1 and c2 and c3 else '미달'}")

    tr = res["rule"].trades
    tr = tr[(tr["date"] >= a) & (tr["date"] <= b)]
    print(f"\n60일 규칙: 매매 {len(tr)}건, 비용 {tr['cost'].sum():,.0f}원 (시작 1,000만 원 기준)")
    pc = per_coin(prices, a, b)
    fmt = pc.astype(object)
    for col in pc.columns:
        fmt[col] = pc[col].map("{:.0%}".format if col == "들고 있던 날" else "{:+.1%}".format)
    print("\n코인별 (참고, 판단 안 함):\n" + fmt.to_string())

    print("\n연도별 수익률 (규칙 / 나눠 담기 / 비트코인):")
    y0, y1 = int(a[:4]), int(b[:4])
    for y in range(y0, y1 + 1):
        ya, yb = max(f"{y}-01-01", a), min(f"{y}-12-31", b)
        vals = []
        for k in ("rule", "equal", "btc"):
            w = window(res[k].equity, ya, yb)
            vals.append(f"{w.iloc[-1] / w.iloc[0] - 1:+.1%}")
        print(f"  {y}: " + " / ".join(vals))


if __name__ == "__main__":
    main()
