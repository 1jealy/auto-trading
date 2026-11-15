"""공개 시세 받기 (키 필요 없음, 읽기 전용).

해외 가격 출처는 바이낸스 USDT 선물이 기준이다. 연결이 막힌 곳(예: 미국 클라우드)에서는 OKX 선물, 그다음 바이낸스 현물로
대신하고, 어떤 출처를 썼는지 기록에 남긴다(정식 판정에는 바이낸스 선물 기록만 쓴다).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

from autotrade.paper.book import Book

COINS = ("BTC", "ETH", "XRP", "SOL")
SOURCES = ("binance_futures", "okx_swap", "binance_spot")
SOURCE_NAMES = {"binance_futures": "바이낸스 선물", "okx_swap": "OKX 선물", "binance_spot": "바이낸스 현물"}
UA = {"User-Agent": "Mozilla/5.0 (paper-trading research; public market data only)"}


class FeedError(RuntimeError):
    pass


class Feeds:
    def __init__(self, coins=COINS, source: str | None = None, session: Any = None, timeout: float = 4.0) -> None:
        if session is None:
            import requests

            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
            session.mount("https://", adapter)
        self.s = session
        self.coins = tuple(coins)
        self.timeout = timeout
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.source = source

    def _get(self, url: str, params: dict | None = None, timeout: float | None = None) -> Any:
        r = self.s.get(url, params=params, headers=UA, timeout=timeout or self.timeout)
        if r.status_code != 200:
            raise FeedError(f"HTTP {r.status_code} {url.split('?')[0]}")
        return r.json()

    # ── 해외 가격 (가운데 값) ──
    def pick_source(self) -> str:
        """연결되는 첫 출처를 고른다."""
        errors = []
        for src in SOURCES:
            try:
                mids = self._global(src)
                if len(mids) == len(self.coins):
                    self.source = src
                    return src
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src}: {str(e)[:60]}")
        raise FeedError("해외 가격을 받을 곳이 없습니다 — " + "; ".join(errors))

    def global_mids(self) -> dict[str, float]:
        if self.source is None:
            self.pick_source()
        return self._global(self.source)

    def _global(self, src: str) -> dict[str, float]:
        if src == "binance_futures":
            def one(c):
                d = self._get("https://fapi.binance.com/fapi/v1/ticker/bookTicker", {"symbol": f"{c}USDT"})
                return c, (float(d["bidPrice"]) + float(d["askPrice"])) / 2
            return dict(self.pool.map(one, self.coins))
        if src == "okx_swap":
            def one(c):
                d = self._get("https://www.okx.com/api/v5/market/ticker", {"instId": f"{c}-USDT-SWAP"})["data"][0]
                return c, (float(d["bidPx"]) + float(d["askPx"])) / 2
            return dict(self.pool.map(one, self.coins))
        if src == "binance_spot":
            syms = "[" + ",".join(f'"{c}USDT"' for c in self.coins) + "]"
            rows = self._get("https://data-api.binance.vision/api/v3/ticker/bookTicker", {"symbols": syms})
            return {r["symbol"][:-4]: (float(r["bidPrice"]) + float(r["askPrice"])) / 2 for r in rows}
        raise FeedError(f"모르는 출처: {src}")

    # ── 국내 호가창 ──
    def upbit_books(self) -> dict[str, Book]:
        rows = self._get("https://api.upbit.com/v1/orderbook", {"markets": ",".join(f"KRW-{c}" for c in self.coins)})
        out = {}
        for r in rows:
            u = r["orderbook_units"]
            out[r["market"][4:]] = Book.make([(x["ask_price"], x["ask_size"]) for x in u],
                                             [(x["bid_price"], x["bid_size"]) for x in u], r.get("timestamp", 0) / 1000)
        return out

    def coinone_book(self, coin: str) -> Book:
        d = self._get(f"https://api.coinone.co.kr/public/v2/orderbook/KRW/{coin}", {"size": 15})
        if d.get("result") != "success":
            raise FeedError(f"코인원 {coin}: {d.get('error_code')}")
        return Book.make([(x["price"], x["qty"]) for x in d["asks"]], [(x["price"], x["qty"]) for x in d["bids"]],
                         float(d.get("timestamp", 0)) / 1000)

    def coinone_books(self, coins) -> dict[str, Book]:
        def one(c):
            try:
                return c, self.coinone_book(c)
            except Exception:  # noqa: BLE001  (비교용이라 실패해도 본 기록은 계속)
                return c, None
        return {c: b for c, b in self.pool.map(one, list(coins)) if b is not None}

    # ── 하루 단위 자료 (#13) ──
    def funding(self, symbol: str = "BTCUSDT") -> pd.Series:
        """바이낸스 선물 펀딩비 최근 1,000회. index = 확정 시각(UTC), 값 = 비율."""
        rows = self._get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1000}, timeout=15)
        return parse_funding(rows)

    def usdt_daily(self, symbol: str = "BTCUSDT", days: int = 400) -> pd.Series:
        """바이낸스 현물 일봉 종가. index = 그날(UTC) 날짜."""
        last = None
        for base in ("https://data-api.binance.vision", "https://api.binance.com"):
            try:
                rows = self._get(f"{base}/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": days}, timeout=15)
                return parse_klines(rows)
            except Exception as e:  # noqa: BLE001
                last = e
        raise FeedError(f"바이낸스 현물 일봉을 받지 못함: {last}")

    def usdkrw_daily(self) -> pd.Series:
        """야후 원/달러 환율 일봉 종가. index = 날짜."""
        d = self._get("https://query1.finance.yahoo.com/v8/finance/chart/KRW=X", {"range": "2y", "interval": "1d"},
                      timeout=15)
        return parse_yahoo_close(d)


def parse_funding(rows: list[dict]) -> pd.Series:
    s = pd.Series({pd.Timestamp(int(r["fundingTime"]), unit="ms"): float(r["fundingRate"]) for r in rows}, dtype=float)
    return s.sort_index()


def parse_klines(rows: list[list]) -> pd.Series:
    s = pd.Series({pd.Timestamp(int(r[0]), unit="ms").normalize(): float(r[4]) for r in rows}, dtype=float)
    return s.sort_index()


def parse_yahoo_close(d: dict) -> pd.Series:
    res = d["chart"]["result"][0]
    close = res["indicators"]["quote"][0]["close"]
    s = pd.Series({pd.Timestamp(int(t), unit="s").normalize(): c for t, c in zip(res["timestamp"], close) if c},
                  dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def now() -> float:
    return time.time()
