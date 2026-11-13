"""1분 단위 데이터 모으기.

야후 파이낸스는 1분 데이터를 최근 7일 치만 준다. 그래서 매일(늦어도 6일 안에) 받아서
종목별 CSV 에 이어 붙여 쌓는다. 같은 시각 데이터가 겹치면 새로 받은 값으로 덮어쓴다.

주의: 야후 1분 데이터는 15:00 전후에서 끝나고, 15:20~15:30 종가 결정 시간이 빠져 있다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_recent(yahoo_ticker: str) -> pd.DataFrame:
    """최근 7일 1분 데이터. 인덱스는 한국 시간(시간대 정보 없음)."""
    import yfinance as yf

    df = yf.download(yahoo_ticker, interval="1m", period="7d", progress=False, auto_adjust=False)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLUMNS].astype(float)
    idx = df.index
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Seoul").tz_localize(None)
    df.index = idx.rename("Datetime")
    return df


def merge_into_store(new: pd.DataFrame, path: Path) -> tuple[int, int]:
    """new 를 path 의 CSV 에 합친다. (새로 늘어난 행 수, 전체 행 수)를 반환."""
    if path.exists():
        old = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        old = pd.DataFrame(columns=COLUMNS)
    before = len(old)
    merged = pd.concat([old, new[COLUMNS]])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.index.name = "Datetime"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    merged.to_csv(tmp)
    tmp.replace(path)  # 중간에 끊겨도 기존 파일이 깨지지 않도록
    return len(merged) - before, len(merged)
