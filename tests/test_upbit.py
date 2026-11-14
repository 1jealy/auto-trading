"""업비트 코드 테스트 — 실제 서버 대신 가짜 응답 사용."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import autotrade.upbit as up


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


class FakeCandles:
    """from_ ~ until 사이에 매 분 캔들이 있는 가짜 서버. 'to' 이전 것을 최신순으로 200개까지."""

    def __init__(self, from_, until, busy_first=0):
        self.times = [from_ + timedelta(minutes=i) for i in range(int((until - from_).total_seconds() // 60))]
        self.calls, self.busy = 0, busy_first

    def get(self, url, params, timeout):
        self.calls += 1
        if self.busy:
            self.busy -= 1
            return Resp({"error": "too many"}, 429)
        to = datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%SZ")
        older = [t for t in self.times if t < to][::-1][: params["count"]]
        return Resp([dict(candle_date_time_utc=t.strftime("%Y-%m-%dT%H:%M:%S"), opening_price=1,
                          high_price=2, low_price=0.5, trade_price=1.5, candle_acc_trade_volume=3,
                          candle_acc_trade_price=4) for t in older])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(up.time, "sleep", lambda s: None)


def test_pages_back_to_start_without_gaps_or_duplicates():
    start, end = datetime(2024, 1, 1), datetime(2024, 1, 1, 10)  # 600분 → 3번 요청
    fake = FakeCandles(datetime(2023, 12, 31), datetime(2024, 1, 2))
    df = up.minutes_between("KRW-BTC", start, end, fake)
    assert len(df) == 600 and df.index.is_unique and df.index.is_monotonic_increasing
    assert df.index.min() == start and df.index.max() == end - timedelta(minutes=1)
    assert list(df.columns) == up.COLUMNS and df["Close"].iloc[0] == 1.5


def test_stops_when_market_did_not_exist_yet():
    fake = FakeCandles(datetime(2024, 1, 1, 9), datetime(2024, 1, 2))
    df = up.minutes_between("KRW-NEW", datetime(2024, 1, 1), datetime(2024, 1, 1, 10), fake)
    assert len(df) == 60


def test_rate_limit_retried():
    fake = FakeCandles(datetime(2024, 1, 1), datetime(2024, 1, 2), busy_first=2)
    df = up.minutes_between("KRW-BTC", datetime(2024, 1, 1), datetime(2024, 1, 1, 1), fake)
    assert len(df) == 60


def test_month_download_saves_and_skips_existing(tmp_path):
    fake = FakeCandles(datetime(2024, 1, 31), datetime(2024, 2, 2))
    path, n = up.download_month(tmp_path, "KRW-BTC", 2024, 1, fake)
    assert n == 24 * 60  # 1월 31일 하루치만 존재
    assert up.download_month(tmp_path, "KRW-BTC", 2024, 1, fake) == (path, -1)
    loaded = up.load_minutes(tmp_path, "KRW-BTC")
    assert len(loaded) == n and loaded.index.max() < pd.Timestamp("2024-02-01")


def test_unfinished_month_is_refused(tmp_path):
    now = up.utcnow()
    with pytest.raises(up.UpbitError, match="끝나지 않은"):
        up.download_month(tmp_path, "KRW-BTC", now.year, now.month, FakeCandles(now, now))


def test_month_helpers():
    assert up.month_range("2023-11", "2024-02") == [(2023, 11), (2023, 12), (2024, 1), (2024, 2)]
    assert up.last_complete_month(datetime(2026, 11, 14)) == "2026-10"
    assert up.last_complete_month(datetime(2026, 1, 3)) == "2025-12"
