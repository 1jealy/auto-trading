"""가격 데이터 컨테이너와 로더."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


@dataclass(frozen=True)
class Prices:
    """일별 시가/종가 표. 행 = 날짜(오름차순), 열 = 종목코드.

    거래가 없던 날(상장 전, 거래정지)은 NaN 으로 둔다.
    """

    open: pd.DataFrame
    close: pd.DataFrame

    def __post_init__(self) -> None:
        if not self.open.index.equals(self.close.index):
            raise ValueError("open/close 의 날짜 인덱스가 다릅니다")
        if list(self.open.columns) != list(self.close.columns):
            raise ValueError("open/close 의 종목 열이 다릅니다")
        if not self.close.index.is_monotonic_increasing:
            raise ValueError("날짜 인덱스는 오름차순이어야 합니다")
        if self.close.index.has_duplicates:
            raise ValueError("날짜 인덱스에 중복이 있습니다")

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def tickers(self) -> list[str]:
        return list(self.close.columns)

    def until(self, date: pd.Timestamp) -> "Prices":
        """date 장 마감 시점까지 알 수 있는 데이터만 남긴 사본."""
        return Prices(self.open.loc[:date].copy(), self.close.loc[:date].copy())

    def select(self, tickers: Iterable[str]) -> "Prices":
        cols = list(tickers)
        return Prices(self.open[cols], self.close[cols])


def load_prices(
    tickers: Iterable[str],
    start: str,
    end: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Prices:
    """FinanceDataReader 로 일봉을 받아 Prices 로 만든다.

    end 를 지정하면 CSV 로 캐시한다(같은 기간을 다시 받지 않음). end=None 이면 최신까지
    받으며 데이터가 계속 바뀌므로 캐시하지 않는다.
    """
    opens: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    for ticker in tickers:
        df = _load_one(ticker, start, end, cache_dir)
        opens[ticker] = df["Open"]
        closes[ticker] = df["Close"]

    open_ = pd.DataFrame(opens).sort_index()
    close = pd.DataFrame(closes).sort_index()
    # 거래정지일에 0 이 들어오는 경우가 있어 결측으로 처리
    open_ = open_.where(open_ > 0)
    close = close.where(close > 0)
    open_.index.name = close.index.name = "Date"
    return Prices(open_, close)


def _load_one(ticker: str, start: str, end: str | None, cache_dir: Path) -> pd.DataFrame:
    cache_file = cache_dir / f"{ticker}_{start}_{end}.csv" if end else None
    if cache_file is not None and cache_file.exists():
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)

    import FinanceDataReader as fdr  # 네트워크가 필요한 경우에만 import

    df = fdr.DataReader(ticker, start, end)
    if df.empty:
        raise ValueError(f"{ticker}: {start}~{end} 데이터가 없습니다")
    df = df[["Open", "Close"]].astype(float)
    df.index = pd.to_datetime(df.index)

    if cache_file is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_file)
    return df
