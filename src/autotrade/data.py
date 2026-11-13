"""가격 데이터 컨테이너와 로더."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


@dataclass(frozen=True)
class Prices:
    """일별 시가/종가(/거래량) 표. 행 = 날짜(오름차순), 열 = 종목코드.

    거래가 없던 날(상장 전, 상장폐지 후, 거래정지)은 NaN 으로 둔다.
    volume 은 선택 사항이다(없으면 None).
    """

    open: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        frames = [self.open, self.close] + ([self.volume] if self.volume is not None else [])
        for f in frames[1:]:
            if not f.index.equals(self.close.index):
                raise ValueError("open/close/volume 의 날짜 인덱스가 다릅니다")
            if list(f.columns) != list(self.open.columns):
                raise ValueError("open/close/volume 의 종목 열이 다릅니다")
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
        vol = self.volume.loc[:date].copy() if self.volume is not None else None
        return Prices(self.open.loc[:date].copy(), self.close.loc[:date].copy(), vol)

    def select(self, tickers: Iterable[str]) -> "Prices":
        cols = list(tickers)
        vol = self.volume[cols] if self.volume is not None else None
        return Prices(self.open[cols], self.close[cols], vol)


def load_prices(
    tickers: Iterable[str],
    start: str,
    end: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    with_volume: bool = False,
    drop_no_trade: bool = False,
    workers: int = 1,
    skip_errors: bool = False,
) -> Prices:
    """FinanceDataReader 로 일봉을 받아 Prices 로 만든다.

    Args:
        end: 지정하면 CSV 로 캐시한다. None 이면 최신까지 받고 캐시하지 않는다.
        with_volume: 거래량도 불러온다.
        drop_no_trade: 거래량 0인 날(거래정지)의 시가·종가를 비운다. 데이터 제공처가 거래정지일에
            직전 종가를 그대로 채워 넣기 때문에, 그대로 두면 "전혀 안 움직인 날"로 보인다.
        workers: 동시에 내려받을 개수.
        skip_errors: 받기 실패한 종목은 건너뛰고 경고만 출력한다.
    """
    if drop_no_trade and not with_volume:
        raise ValueError("drop_no_trade 에는 with_volume=True 가 필요합니다")
    tickers = list(dict.fromkeys(tickers))  # 중복 제거, 순서 유지

    cols = ["Open", "Close", "Volume"] if with_volume else ["Open", "Close"]

    def fetch(t: str) -> tuple[str, pd.DataFrame | None, str | None]:
        try:
            return t, _load_one(t, start, end, cache_dir, cols), None
        except Exception as e:  # noqa: BLE001
            if not skip_errors:
                raise
            return t, None, str(e)[:100]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(fetch, tickers))

    failed = [(t, err) for t, df, err in results if df is None]
    if failed:
        print(f"경고: {len(failed)}개 종목을 받지 못해 제외함 (예: {failed[:3]})")
    loaded = {t: df for t, df, _ in results if df is not None}

    open_ = pd.DataFrame({t: df["Open"] for t, df in loaded.items()}).sort_index()
    close = pd.DataFrame({t: df["Close"] for t, df in loaded.items()}).sort_index()
    volume = None
    if with_volume:
        volume = pd.DataFrame({t: df["Volume"] for t, df in loaded.items()}).sort_index()

    # 0 이하 가격은 결측으로 처리 (거래정지일에 시가 0 이 들어오는 경우)
    open_ = open_.where(open_ > 0)
    close = close.where(close > 0)
    if drop_no_trade:
        traded = volume > 0
        open_ = open_.where(traded)
        close = close.where(traded)
    for f in (open_, close, volume):
        if f is not None:
            f.index.name = "Date"
    return Prices(open_, close, volume)


def load_ohlc(ticker: str, start: str, end: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """종목 하나의 시가·고가·저가·종가·거래량 표. 0 이하 가격은 결측으로 처리."""
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = _load_one(ticker, start, end, cache_dir, cols).sort_index()
    prices = ["Open", "High", "Low", "Close"]
    df[prices] = df[prices].where(df[prices] > 0)
    df.index.name = "Date"
    return df


def _load_one(
    ticker: str, start: str, end: str | None, cache_dir: Path, cols: list[str]
) -> pd.DataFrame:
    cache_file = cache_dir / f"{ticker}_{start}_{end}.csv" if end else None
    if cache_file is not None and cache_file.exists():
        cached = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        if set(cols) <= set(cached.columns):
            return cached[cols]

    import FinanceDataReader as fdr  # 네트워크가 필요한 경우에만 import

    df = fdr.DataReader(ticker, start, end)
    if df.empty:
        raise ValueError(f"{ticker}: {start}~{end} 데이터가 없습니다")
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].astype(float)
    df.index = pd.to_datetime(df.index)

    if cache_file is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_file)
    return df[cols]
