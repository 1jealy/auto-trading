"""업비트 공개 시세 API (키 필요 없음) — 1분 데이터 받기, 호가 확인.

- 1분 캔들: 한 번에 최대 200개, `to` 시각 이전 것을 최신순으로 준다. 거래가 없던 분은 빠져 있다.
- 시세 요청 한도: 초당 약 10회 → 요청 사이를 조금 띄운다.
- 시각은 모두 **UTC**(시간대 표시 없음)로 저장한다. 한국 시간 = UTC + 9시간.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

BASE = "https://api.upbit.com/v1"
COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Value"]  # Value = 거래대금(원)


class UpbitError(RuntimeError):
    pass


def utcnow() -> datetime:
    """현재 UTC 시각 (시간대 표시 없음)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _session(session: Any):
    if session is not None:
        return session
    import requests

    return requests.Session()


def _get(session: Any, path: str, params: dict, pause: float, retries: int = 5) -> Any:
    wait = 0.5
    for _ in range(retries):
        resp = session.get(f"{BASE}{path}", params=params, timeout=10)
        if resp.status_code == 429:  # 요청 한도 초과 → 쉬었다가 다시
            time.sleep(wait)
            wait *= 2
            continue
        if resp.status_code != 200:
            raise UpbitError(f"{path} 실패: HTTP {resp.status_code} {str(resp.json())[:120]}")
        time.sleep(pause)
        return resp.json()
    raise UpbitError(f"{path} 실패: 요청 한도 초과가 계속됨")


def minutes_between(market: str, start: datetime, end: datetime, session: Any = None,
                    pause: float = 0.12) -> pd.DataFrame:
    """[start, end) 구간의 1분 데이터 (UTC). end 부터 거꾸로 200개씩 받는다."""
    session = _session(session)
    rows: list[dict] = []
    cursor = end
    while cursor > start:
        page = _get(session, "/candles/minutes/1", {
            "market": market, "to": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"), "count": 200,
        }, pause)
        if not page:
            break
        rows.extend(page)
        oldest = datetime.strptime(page[-1]["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S")
        if oldest >= cursor:  # 안전장치: 더 과거로 못 가면 멈춤
            break
        cursor = oldest
        if len(page) < 200:
            break

    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame({
        "Datetime": pd.to_datetime([r["candle_date_time_utc"] for r in rows]),
        "Open": [float(r["opening_price"]) for r in rows],
        "High": [float(r["high_price"]) for r in rows],
        "Low": [float(r["low_price"]) for r in rows],
        "Close": [float(r["trade_price"]) for r in rows],
        "Volume": [float(r["candle_acc_trade_volume"]) for r in rows],
        "Value": [float(r["candle_acc_trade_price"]) for r in rows],
    })
    df = df[(df["Datetime"] >= start) & (df["Datetime"] < end)]
    return df.drop_duplicates("Datetime").set_index("Datetime").sort_index()


def month_file(store: Path, market: str, year: int, month: int) -> Path:
    return store / market / f"{year:04d}-{month:02d}.csv.gz"


def download_month(store: Path, market: str, year: int, month: int, session: Any = None) -> tuple[Path, int]:
    """한 달치를 받아 저장한다. 이미 파일이 있으면 건너뛴다. (파일 경로, 행 수) 반환."""
    path = month_file(store, market, year, month)
    if path.exists():
        return path, -1
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), month % 12 + 1, 1)
    if end > utcnow():
        raise UpbitError(f"{year}-{month:02d}은 아직 끝나지 않은 달입니다")
    df = minutes_between(market, start, end, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, compression="gzip")
    tmp.replace(path)  # 중간에 끊기면 파일이 안 생겨서 다음에 다시 받음
    return path, len(df)


def load_minutes(store: Path, market: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """저장된 월별 파일을 합쳐 읽는다 (UTC)."""
    files = sorted((store / market).glob("*.csv.gz"))
    if not files:
        raise UpbitError(f"{market} 데이터가 없습니다. 먼저 내려받으세요.")
    df = pd.concat(pd.read_csv(f, index_col=0, parse_dates=True) for f in files).sort_index()
    return df.loc[start:end]


def top_of_book(market: str, session: Any = None) -> tuple[float, float]:
    """지금 가장 높은 '사겠다' 가격, 가장 낮은 '팔겠다' 가격."""
    data = _get(_session(session), "/orderbook", {"markets": market}, pause=0.0)
    unit = data[0]["orderbook_units"][0]
    return float(unit["bid_price"]), float(unit["ask_price"])


def month_range(first: str, last: str) -> list[tuple[int, int]]:
    """'2022-01' ~ '2026-10' → [(2022, 1), ..., (2026, 10)]"""
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_complete_month(now: datetime | None = None) -> str:
    now = now or utcnow()
    first_of_this = datetime(now.year, now.month, 1)
    prev = first_of_this - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"
