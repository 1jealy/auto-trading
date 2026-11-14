"""차트 지표 (교과서 정의). 모든 값은 그 캔들이 끝난 시점까지의 데이터만 쓴다."""

from __future__ import annotations

import pandas as pd


def hourly_bars(m: pd.DataFrame) -> pd.DataFrame:
    """1분 캔들(UTC) → 1시간 캔들. 캔들이 하나도 없는 시간은 뺀다."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    bars = m[list(agg)].resample("1h", label="left", closed="left").agg(agg)
    return bars.dropna(subset=["Open", "Close"])


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """와일더 방식 RSI (0~100). 내린 적이 없으면 100."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss > 0, 100.0).where(gain.notna())


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})
