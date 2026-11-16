"""실험 #22: 덩이 매수·익절·손절 (60일선 위에서만), 4시간봉·1시간봉. 규칙은 docs/experiments.md 에 미리 정해 둠.

    python scripts/exp22_lots.py data                 # 자료만 받고 빈 곳 점검 (수익 안 봄)
    python scripts/exp22_lots.py practice             # 연습 기간 후보 8개 → 고르는 기준대로 하나 선택
    python scripts/exp22_lots.py validation A3        # 고른 후보로 검증 기간 딱 한 번
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

from autotrade.lots import LotRules, hold_equity, resample_ohlc, simulate  # noqa: E402

COINS = ["BTC", "ETH", "XRP", "LTC", "ADA", "IOTA", "XLM", "TRX", "EOS", "DASH", "BCH"]
FIRST_MONTH, LAST_MONTH = "2017-12", "2026-10"
PRACTICE = ("2018-03-01", "2021-12-31 23:59")
VALIDATION = ("2022-01-01", "2026-10-31 23:59")
BARS_PER_DAY = {"1h": 24, "4h": 6}
TARGET = {"1h": 0.03, "4h": 0.05}
# 후보: 번호 → (봉, 익절 대비 손절 배율 또는 None=손절 없음)
VARIANTS = {"A1": ("4h", None), "A2": ("4h", 0.5), "A3": ("4h", 1.0), "A4": ("4h", 2.0),
            "B1": ("1h", None), "B2": ("1h", 0.5), "B3": ("1h", 1.0), "B4": ("1h", 2.0)}
CACHE = ROOT / "data" / "cache" / "exp22"
OUT = ROOT / "results" / "exp22"
UA = {"User-Agent": "Mozilla/5.0 (research; public market data)"}
COLS = ["연평균", "흔들림", "수익/흔들림", "가장 크게 떨어졌을 때"]


# ── 자료 ──
def _ms(x: pd.Series) -> pd.DatetimeIndex:
    x = x.astype("int64")
    return pd.DatetimeIndex(pd.to_datetime(x, unit="us" if x.max() > 10**14 else "ms"))


def month_frame(coin: str, month: str) -> pd.DataFrame | None:
    path = CACHE / f"{coin}USDT-1h-{month}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True) if path.stat().st_size > 5 else None
    r = requests.get(f"https://data.binance.vision/data/spot/monthly/klines/{coin}USDT/1h/{coin}USDT-1h-{month}.zip",
                     headers=UA, timeout=60)
    CACHE.mkdir(parents=True, exist_ok=True)
    if r.status_code == 404:
        path.write_text("")
        return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode()
    header = None if raw.split("\n", 1)[0].split(",")[0].strip().isdigit() else 0
    df = pd.read_csv(io.StringIO(raw), header=header).iloc[:, [0, 1, 4]]
    df.columns = ["t", "open", "close"]
    df.index = _ms(df["t"])
    df = df[["open", "close"]].astype(float)
    df = df[~df.index.duplicated()]
    df.to_csv(path)
    return df


def load_hourly() -> dict[str, pd.DataFrame]:
    months = pd.period_range(FIRST_MONTH, LAST_MONTH, freq="M").strftime("%Y-%m")
    jobs = [(c, m) for c in COINS for m in months]
    with ThreadPoolExecutor(8) as pool:
        frames = list(pool.map(lambda j: month_frame(*j), jobs))
    out: dict[str, list] = {c: [] for c in COINS}
    for (c, _), f in zip(jobs, frames):
        if f is not None:
            out[c].append(f)
    return {c: pd.concat(v).sort_index() for c, v in out.items() if v}


def frames(hourly: dict[str, pd.DataFrame], tf: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    full = pd.date_range(min(f.index[0] for f in hourly.values()), max(f.index[-1] for f in hourly.values()), freq="h")
    op, cl = {}, {}
    for c, df in hourly.items():
        df = df.reindex(full) if tf == "1h" else df
        r = df if tf == "1h" else resample_ohlc(df, "4h")
        op[c], cl[c] = r["open"], r["close"]
    o, c_ = pd.DataFrame(op), pd.DataFrame(cl)
    # 거래가 중간에 멈춘 봉은 비운다(채우지 않음): 시가·종가 모두 NaN
    return o.where(c_.notna()), c_


def check_data() -> None:
    h = load_hourly()
    full = len(pd.date_range(FIRST_MONTH + "-01", LAST_MONTH + "-30", freq="h"))
    for c, df in h.items():
        gaps = df.index.to_series().diff().dt.total_seconds().div(3600)
        print(f"{c:5s} {df.index[0]} ~ {df.index[-1]}  {len(df):6d}봉  1시간보다 긴 빈 곳 {int((gaps > 1).sum())}번 (가장 긴 {int(gaps.max())}시간)")
    print("받은 코인:", len(h), "/", len(COINS), "| 자료가 일찍 끝난 코인:",
          [c for c, df in h.items() if df.index[-1] < pd.Timestamp("2026-10-01")])


# ── 지표 ──
def metrics(eq: pd.Series) -> dict:
    d = eq.resample("1D").last().dropna()
    yrs = (d.index[-1] - d.index[0]).days / 365.0
    cagr = (d.iloc[-1] / d.iloc[0]) ** (1 / yrs) - 1 if d.iloc[0] > 0 and d.iloc[-1] > 0 else -1.0
    vol = d.pct_change().dropna().std() * np.sqrt(365)
    mdd = float((d / d.cummax() - 1).min())
    return {"연평균": float(cagr), "흔들림": float(vol), "수익/흔들림": float(cagr / vol) if vol > 0 else float("nan"),
            "가장 크게 떨어졌을 때": mdd}


def window(eq: pd.Series, a: str, b: str) -> pd.Series:
    w = eq.loc[a:b]
    return w / w.iloc[0]


def run_variant(name: str, o_c: dict, end: str | None) -> tuple:
    tf, mult = VARIANTS[name]
    o, c = o_c[tf]
    target = TARGET[tf]
    rules = LotRules(ma_bars=60 * BARS_PER_DAY[tf], target=target, stop=None if mult is None else target * mult)
    return rules, simulate(o, c, rules, end=pd.Timestamp(end) if end else None)


def describe(res, eq_window: pd.Series, hold_window: pd.Series, a: str, b: str) -> dict:
    m = metrics(eq_window)
    t = res.trades
    t = t[(t["sell_t"] >= a) & (t["sell_t"] <= b)]
    held = res.open_lots
    return {**m, "판 덩이": int(len(t)), "산 덩이": int(res.buys),
            "판 덩이 평균": float(t["ret"].mean()) if len(t) else None,
            "이긴 비율(판 것만)": float((t["ret"] > 0).mean()) if len(t) else None,
            "끝에 들고 있는 덩이": int(len(held)),
            "손절": int((t["why"] == "손절").sum()), "상장폐지": int((t["why"] == "상장폐지").sum())}


def practice() -> None:
    hourly = load_hourly()
    o_c = {tf: frames(hourly, tf) for tf in BARS_PER_DAY}
    a, b = PRACTICE
    OUT.mkdir(parents=True, exist_ok=True)
    rows, hold_m = {}, {}
    for tf in BARS_PER_DAY:
        o, c = o_c[tf]
        hold_m[tf] = metrics(window(hold_equity(o, c, pd.Timestamp(a), end=pd.Timestamp(b)), a, b))
    for name in VARIANTS:
        rules, res = run_variant(name, o_c, b)
        rows[name] = describe(res, window(res.equity, a, b), None, a, b)
        rows[name]["봉"] = VARIANTS[name][0]
        rows[name]["손절 폭"] = None if rules.stop is None else rules.stop
        res.trades.to_csv(OUT / f"practice_{name}_trades.csv", index=False)
    df = pd.DataFrame(rows).T
    pd.set_option("display.width", 250)
    print(f"\n== #22 연습 기간 {a[:10]} ~ {b[:10]} (계좌 가치 기준) ==")
    for tf in BARS_PER_DAY:
        print(f"그냥 보유({tf}) 수익/흔들림 {hold_m[tf]['수익/흔들림']:.2f} 연평균 {hold_m[tf]['연평균']:.1%} "
              f"흔들림 {hold_m[tf]['흔들림']:.1%} 가장 크게 떨어졌을 때 {hold_m[tf]['가장 크게 떨어졌을 때']:.1%}")
    print(df.to_string(float_format=lambda x: f"{x:.3f}"))
    best = df["수익/흔들림"].astype(float).idxmax()
    print(f"\n고르는 기준(수익/흔들림 최대)에 따른 선택: {best}  (손절 배율: {VARIANTS[best][1]})")
    (OUT / "practice_summary.json").write_text(json.dumps({"rows": rows, "hold": hold_m, "selected": best},
                                                         ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def validation(name: str) -> None:
    hourly = load_hourly()
    o_c = {tf: frames(hourly, tf) for tf in BARS_PER_DAY}
    a, b = VALIDATION
    pa, _ = PRACTICE
    tf = VARIANTS[name][0]
    o, c = o_c[tf]
    rules, res = run_variant(name, o_c, b)
    hold = hold_equity(o, c, pd.Timestamp(pa), end=pd.Timestamp(b))
    mine = describe(res, window(res.equity, a, b), None, a, b)
    base = metrics(window(hold, a, b))
    crit = [mine["수익/흔들림"] > base["수익/흔들림"], mine["가장 크게 떨어졌을 때"] > base["가장 크게 떨어졌을 때"],
            mine["연평균"] > 0]
    print(f"\n== #22 검증 기간 {a[:10]} ~ {b[:10]} · 후보 {name} (계좌 가치 기준) ==")
    for k, v in mine.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print("  그냥 보유:", {k: round(v, 4) for k, v in base.items()})
    print("  기준 ①수익/흔들림 > 보유  ②가장 크게 떨어졌을 때가 보유보다 작음  ③연평균 > 0 :", crit)
    print("  판정:", "합격" if all(crit) else "탈락")
    OUT.mkdir(parents=True, exist_ok=True)
    res.trades.to_csv(OUT / f"validation_{name}_trades.csv", index=False)
    (OUT / "validation_summary.json").write_text(json.dumps({"variant": name, "mine": mine, "hold": base, "criteria": crit},
                                                           ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "data"
    if arg == "data":
        check_data()
    elif arg == "practice":
        practice()
    else:
        validation(sys.argv[2])
