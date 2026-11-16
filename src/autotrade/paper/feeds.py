"""공개 시세 받기 (키 필요 없음, 읽기 전용).

해외 가격 출처는 OKX USDT 무기한 선물이 기준이다(2026-11-15 시작 전 변경: 바이낸스 선물은 미국·일본 등에서 막힐 수 있어서).
OKX 에 연결되지 않으면 바이낸스 선물, 그다음 바이낸스 현물로 대신하고, 어떤 출처를 썼는지 기록에 남긴다
(정식 판정에는 OKX 선물 기록만 쓴다). #13 펀딩비도 OKX 기록을 쓴다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade.paper.book import Book

COINS = ("BTC", "ETH", "XRP", "SOL")
UNIVERSE_FILE = Path(__file__).with_name("scalp_universe.txt")
BULK_FROM = 8  # 코인이 이 수보다 많으면 해외 시세를 전체 한 번에 받는다(코인마다 요청하면 OKX 한도에 걸림)


def load_universe(path: Path = UNIVERSE_FILE) -> tuple[str, ...]:
    """#21 단타가 보는 코인 (등록 때 고정한 목록)."""
    rows = [x.strip() for x in path.read_text(encoding="utf-8").splitlines()]
    return tuple(x for x in rows if x and not x.startswith("#"))
SOURCES = ("okx_swap", "binance_futures", "binance_spot")
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
        self.base = tuple(c for c in COINS if c in self.coins) or self.coins[:4]
        self.up_lock = threading.Lock()
        self.up_last = 0.0
        self.active: tuple[str, ...] = self.coins  # 지금 실제로 받을 수 있는 코인(상장폐지·중단은 빠짐)
        self.bad_until = 0.0

    def refresh_active(self) -> tuple[str, ...]:
        """업비트 원화 마켓에 있고 OKX 선물 시세가 있는 코인만 남긴다. 기본 코인은 항상 남긴다."""
        try:
            mk = self._get("https://api.upbit.com/v1/market/all", {"isDetails": "true"}, timeout=10)
            up = {m["market"][4:] for m in mk if m["market"].startswith("KRW-") and not m.get("market_event", {}).get("warning")}
            tk = self._get("https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"}, timeout=10)["data"]
            ok = {d["instId"][:-10] for d in tk if d["instId"].endswith("-USDT-SWAP") and d.get("bidPx") and d.get("askPx")}
        except Exception:  # noqa: BLE001
            return self.active  # 확인 못 하면 그대로
        self.active = tuple(c for c in self.coins if c in self.base or (c in up and c in ok))
        return self.active

    UPBIT_SPACING = 0.105  # 업비트 요청 시작 사이 최소 간격(초당 10회 한도, 요청이 둘로 나뉘어도 넘지 않게)

    def _get(self, url: str, params: dict | None = None, timeout: float | None = None) -> Any:
        if "api.upbit.com" in url:
            with self.up_lock:
                wait = self.up_last + self.UPBIT_SPACING - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.up_last = time.time()
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
                if all(c in mids for c in self.base):
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
        if src in ("binance_futures", "binance_spot") and len(self.coins) > BULK_FROM:
            # 대체 출처는 기본 코인만 (정식 판정에 안 쓰는 기록이라 단순하게)
            coins = self.base
        else:
            coins = self.coins
        if src == "binance_futures":
            def one(c):
                d = self._get("https://fapi.binance.com/fapi/v1/ticker/bookTicker", {"symbol": f"{c}USDT"})
                return c, (float(d["bidPrice"]) + float(d["askPrice"])) / 2
            return dict(self.pool.map(one, self.coins))
        if src == "okx_swap" and len(self.coins) > BULK_FROM:
            rows = self._get("https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"})["data"]
            want = set(self.coins)
            out = {}
            for d in rows:
                c = d["instId"][:-10]
                if d["instId"].endswith("-USDT-SWAP") and c in want and d.get("bidPx") and d.get("askPx"):
                    out[c] = (float(d["bidPx"]) + float(d["askPx"])) / 2
            return out
        if src == "okx_swap":
            def one(c):
                d = self._get("https://www.okx.com/api/v5/market/ticker", {"instId": f"{c}-USDT-SWAP"})["data"][0]
                return c, (float(d["bidPx"]) + float(d["askPx"])) / 2
            return dict(self.pool.map(one, self.coins))
        if src == "binance_spot":
            syms = "[" + ",".join(f'"{c}USDT"' for c in coins) + "]"
            rows = self._get("https://data-api.binance.vision/api/v3/ticker/bookTicker", {"symbols": syms})
            return {r["symbol"][:-4]: (float(r["bidPrice"]) + float(r["askPrice"])) / 2 for r in rows}
        raise FeedError(f"모르는 출처: {src}")

    # ── 국내 호가창 ──
    def upbit_books(self, coins=None) -> dict[str, Book]:
        """코인 호가창. coins 를 안 주면 지금 받을 수 있는 코인 전부."""
        coins = tuple(coins) if coins is not None else self.active
        try:
            rows = self._get("https://api.upbit.com/v1/orderbook", {"markets": ",".join(f"KRW-{c}" for c in coins)})
        except FeedError:
            if len(coins) <= len(self.base):
                raise
            # 코인 하나가 사라지면 요청 전체가 실패한다 → 목록을 다시 확인하고, 기본 코인만이라도 받아 #17·#18 을 살려 둔다
            self.refresh_active()
            coins = tuple(c for c in coins if c in self.active) or self.base
            rows = self._get("https://api.upbit.com/v1/orderbook", {"markets": ",".join(f"KRW-{c}" for c in coins)})
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
    def funding(self, inst: str = "BTC-USDT-SWAP", pages: int = 3) -> pd.Series:
        """OKX 선물 펀딩비(확정된 것) 최근 pages×100회. index = 확정 시각(UTC), 값 = 비율.

        OKX 는 최근 약 3개월치만 주므로 엔진이 받은 값을 저장소에 쌓아 둔다(Store.add_funding).
        """
        rows: list[dict] = []
        after = None
        for _ in range(pages):
            params = {"instId": inst, "limit": 100}
            if after:
                params["after"] = after
            d = self._get("https://www.okx.com/api/v5/public/funding-rate-history", params, timeout=15)
            page = d.get("data") or []
            if not page:
                break
            rows += page
            after = min(int(r["fundingTime"]) for r in page)
        if not rows:
            raise FeedError("OKX 펀딩비를 받지 못함")
        return parse_okx_funding(rows)

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


def parse_okx_funding(rows: list[dict]) -> pd.Series:
    s = pd.Series({pd.Timestamp(int(r["fundingTime"]), unit="ms"): float(r["fundingRate"]) for r in rows}, dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


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
