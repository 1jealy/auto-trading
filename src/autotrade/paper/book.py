"""호가창과 시장가 체결 계산."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fill:
    price: float  # 평균 체결가
    qty: float
    krw: float  # 체결 금액 (수수료 제외)
    levels: int  # 몇 번째 호가까지 썼나
    complete: bool  # 호가 잔량 안에서 다 체결됐나


@dataclass(frozen=True)
class Book:
    """asks: 싼 것부터 (가격, 수량), bids: 비싼 것부터 (가격, 수량)."""

    asks: tuple[tuple[float, float], ...]
    bids: tuple[tuple[float, float], ...]
    ts: float = 0.0  # 거래소가 알려 준 시각(초)

    @classmethod
    def make(cls, asks, bids, ts: float = 0.0) -> "Book":
        a = tuple(sorted(((float(p), float(q)) for p, q in asks if float(p) > 0 and float(q) > 0)))
        b = tuple(sorted(((float(p), float(q)) for p, q in bids if float(p) > 0 and float(q) > 0), reverse=True))
        return cls(a, b, ts)

    @property
    def ok(self) -> bool:
        return bool(self.asks) and bool(self.bids) and self.asks[0][0] > self.bids[0][0] > 0

    @property
    def ask(self) -> float:
        return self.asks[0][0]

    @property
    def bid(self) -> float:
        return self.bids[0][0]

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2

    @property
    def spread(self) -> float:
        """매도 1호가와 매수 1호가의 차이 ÷ 가운데 값."""
        return (self.ask - self.bid) / self.mid

    @property
    def tick(self) -> float:
        """가격 한 칸 크기 추정: 이웃한 호가 사이 간격 중 가장 작은 것."""
        gaps = [b[0] - a[0] for side in (self.asks, self.bids[::-1]) for a, b in zip(side, side[1:])]
        gaps.append(self.ask - self.bid)
        gaps = [g for g in gaps if g > 0]
        return min(gaps) if gaps else 0.0

    def one_tick_spread(self) -> bool:
        return self.ask - self.bid <= self.tick * 1.001

    def depth_krw(self, side: str, levels: int) -> float:
        rows = self.asks if side == "ask" else self.bids
        return sum(p * q for p, q in rows[:levels])

    def top(self, n: int = 5) -> dict:
        """기록용 요약."""
        return {"asks": [list(x) for x in self.asks[:n]], "bids": [list(x) for x in self.bids[:n]], "ts": self.ts}


def market_buy(book: Book, krw: float) -> Fill:
    """krw 원어치를 매도 호가 위에서부터 차례로 산다."""
    left, qty, used = krw, 0.0, 0
    for price, size in book.asks:
        used += 1
        take = min(size, left / price)
        qty += take
        left -= take * price
        if left <= krw * 1e-9:
            break
    spent = krw - max(left, 0.0)
    return Fill(spent / qty if qty else 0.0, qty, spent, used, left <= krw * 1e-9)


def market_sell(book: Book, qty: float) -> Fill:
    """qty 개를 매수 호가 위에서부터 차례로 판다."""
    left, got, used = qty, 0.0, 0
    for price, size in book.bids:
        used += 1
        take = min(size, left)
        got += take * price
        left -= take
        if left <= qty * 1e-9:
            break
    sold = qty - max(left, 0.0)
    return Fill(got / sold if sold else 0.0, sold, got, used, left <= qty * 1e-9)


def round_trip_cost(book: Book, krw: float) -> float | None:
    """지금 호가창에서 krw 원어치를 사자마자 되팔 때 잃는 비율 (수수료 제외). 잔량이 모자라면 None."""
    if not book.ok:
        return None
    buy = market_buy(book, krw)
    if not buy.complete:
        return None
    sell = market_sell(book, buy.qty)
    if not sell.complete:
        return None
    return 1 - sell.krw / buy.krw
