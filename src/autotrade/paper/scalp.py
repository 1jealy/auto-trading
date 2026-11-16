"""단타(#17 정식, #18 비용을 넘는 틈만 자주)와 지연 관측의 판단 로직. 규칙은 docs/experiments.md 에 등록한 그대로.

네트워크를 쓰지 않는다. 실행 루프(run.py)가 시세를 받아 아래 두 함수로 넘겨 준다.
- on_tick(t, coin, g, book): 약 0.5초마다. t 는 **요청을 보내기 시작한 시각** — 호가창은 t 이후의 상태다.
- on_book(t, coin, book):    신호 뒤 지연(0.2초)이 지난 다음 새로 요청한 호가창. 체결은 여기서만 일어난다.
신호를 본 호가창으로는 체결하지 않는다(미래 참조·낙관적 체결 방지).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from autotrade.paper.book import Book, market_buy, market_sell

RADAR_OFFSETS = (0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0)
BASE_COINS = ("BTC", "ETH", "XRP", "SOL")  # #17·#18 이 보는 코인. 지연 관측·호가 비용 기록·화면 표도 이 코인만.


@dataclass(frozen=True)
class ScalpParams:
    key: str
    move: float  # 해외 3초 변화 기준
    lag: float  # 뒤처짐 기준 (해외 변화 − 업비트 매도 1호가 변화)
    amount: float  # 한 번에 사는 금액(원)
    max_hold: float  # 산 뒤 이 시간이 지나면 무조건 판다(초)
    need_tight_spread: bool = False  # 호가 차이 ≤ max_spread 또는 딱 한 칸
    max_spread: float = 0.0005
    min_depth3: float = 0.0  # 매도 1~3호가 금액 합(원)
    take: float | None = None
    stop: float | None = None
    origin_stop: bool = False  # 해외 가격이 신호 3초 전 값 이하로 돌아오면
    catchup: bool = False  # 업비트 매수 1호가가 해외 변화만큼 따라오면
    cost_gap: float | None = None  # 뒤처짐 ≥ 그 순간 업비트 호가 차이 + 수수료 왕복 + 이 여유일 때만 (#18)
    refill: bool = False  # 남은 돈이 한 번 살 돈보다 적으면 처음 금액으로 다시 채우고 기록 (#18)
    fee: float = 0.0005  # 업비트 수수료(한쪽)
    latency: float = 0.2
    capital: float = 10_000_000
    coins: tuple[str, ...] | None = BASE_COINS  # None 이면 엔진이 받는 모든 코인 (#21)
    cooldown: float = 0.0  # 같은 코인에서 산 뒤 이 시간(초) 안에는 새로 사지 않는다 (#21: 신호가 끊임없이 나오는 것을 막음)
    detail_levels: int = 5  # 감사 기록에 저장할 호가 칸 수 (#21 은 거래가 아주 많아 줄임)
    until: float | None = None  # 이 시각(유닉스 초) 뒤에는 새로 사지 않는다 (#21: 정한 기간만 돌림)


FORMAL = ScalpParams("s17", move=0.0035, lag=0.0020, amount=5_000_000, max_hold=60.0, need_tight_spread=True,
                     min_depth3=5_000_000, take=0.0025, stop=0.0030, origin_stop=True, catchup=True)
ALWAYS = ScalpParams("s18", move=0.0015, lag=0.0, amount=1_000_000, max_hold=30.0, origin_stop=True, catchup=True,
                     cost_gap=0.0005, refill=True)
# #21 "극도로 낮춘" 단타: 코인 전부, 호가 차이·잔량 조건 없음, 해외 3초 +0.07%, 업비트가 덜 오르기만 하면(뒤처짐 ≥ 0), 비용 조건 없음.
EXTREME_ON = False  # #21 은 2026-11-16 시작 전에 취소(사용자가 #22 로 대체). 켜려면 True 로 하고 docs 에 다시 등록할 것
# 예상은 탈락 — 거래를 많이 쌓아 어디에 비용을 넘는 틈이 있는지 보는 용도 (docs/experiments.md #21)
EXTREME = ScalpParams("s21", move=0.0007, lag=0.0, amount=300_000, max_hold=30.0, origin_stop=True, catchup=True,
                      refill=True, coins=None, cooldown=120.0, detail_levels=2, until=1795359600.0)  # 2026-11-23 00:00 KST 까지(1주)


@dataclass
class Sample:
    t: float
    g: float
    ask: float
    bid: float


@dataclass
class Position:
    key: str
    coin: str
    signal_t: float
    fill_t: float
    price: float
    qty: float
    amount: float
    g_ref: float
    u_ref: float
    move: float
    lag: float
    exit_due: float
    exit_reason: str = "시간"
    exit_signal_t: float | None = None
    locked: bool = False  # 파는 조건이 이미 정해짐
    alt_price: float | None = None
    alt_qty: float | None = None
    detail: dict = field(default_factory=dict)


class Scalper:
    def __init__(self, params=(FORMAL, ALWAYS), source: str = "okx_swap", cash: dict | None = None,
                 lookback: float = 3.0, max_age: float = 4.5, radar_move: float = 0.0015,
                 radar_cooldown: float = 5.0) -> None:
        self.params = {p.key: p for p in params}
        self.source = source
        self.cash = {p.key: p.capital for p in params}
        self.cash.update(cash or {})
        self.lookback, self.max_age = lookback, max_age
        self.radar_move, self.radar_cooldown = radar_move, radar_cooldown
        self.hist: dict[str, deque[Sample]] = {}
        self.pos: dict[tuple[str, str], Position] = {}
        self.entries: list[dict] = []
        self.radars: list[dict] = []
        self.last_radar: dict[str, float] = {}
        self.last_skip: dict[tuple[str, str, str], float] = {}
        self.last_entry: dict[tuple[str, str], float] = {}
        self.live: dict[str, dict] = {}
        self.out: list[dict] = []

    # ── 입력 ──
    def on_tick(self, t: float, coin: str, g: float, book: Book, alt: Book | None = None) -> None:
        if not book.ok or not g > 0:
            return
        self.on_book(t, coin, book, alt)  # 지연이 이미 지난 주문은 이 호가창으로 체결
        hist = self.hist.setdefault(coin, deque())
        ref = None
        for s in reversed(hist):
            if s.t <= t - self.lookback:
                ref = s if s.t >= t - self.max_age else None
                break
        hist.append(Sample(t, g, book.ask, book.bid))
        while hist[0].t < t - 30:
            hist.popleft()

        for (key, c), pos in self.pos.items():
            if c == coin and not pos.locked:
                reason = self._exit_reason(self.params[key], pos, g, book)
                if reason and t + self.params[key].latency < pos.exit_due:
                    pos.exit_reason, pos.exit_signal_t, pos.locked = reason, t, True
                    pos.exit_due = t + self.params[key].latency

        live = {"t": t, "g": g, "ask": book.ask, "bid": book.bid, "spread": book.spread,
                "one_tick": book.one_tick_spread(), "move": None, "u_move": None, "lag": None}
        base = coin in BASE_COINS
        if base:  # 화면의 '지금' 표는 기본 코인만
            self.live[coin] = live
        if ref is None:
            return
        move = g / ref.g - 1
        u_move = book.ask / ref.ask - 1
        lag = move - u_move
        live.update(move=move, u_move=u_move, lag=lag)

        if base and move >= self.radar_move and t - self.last_radar.get(coin, -1e18) >= self.radar_cooldown:
            self.last_radar[coin] = t
            self.radars.append({"coin": coin, "t0": t, "move": move, "lag": lag, "ref_ask": ref.ask,
                                "ask0": book.ask, "bid0": book.bid, "spread": book.spread,
                                "points": [{"offset": o, "due": t + o, "at": None} for o in RADAR_OFFSETS]})

        for p in self.params.values():
            if p.coins is not None and coin not in p.coins:
                continue
            if p.until is not None and t >= p.until:
                continue
            if p.cooldown and t - self.last_entry.get((p.key, coin), -1e18) < p.cooldown:
                continue
            if (p.key, coin) in self.pos or any(e["key"] == p.key and e["coin"] == coin for e in self.entries):
                continue
            if move < p.move or lag < p.lag:
                continue
            if p.cost_gap is not None and lag < book.spread + 2 * p.fee + p.cost_gap:
                continue
            why = None
            if p.need_tight_spread and not (book.spread <= p.max_spread or book.one_tick_spread()):
                why = "호가 차이가 넓음"
            elif p.min_depth3 and book.depth_krw("ask", 3) < p.min_depth3:
                why = "매도 1~3호가 잔량 부족"
            elif self.cash[p.key] < p.amount * (1 + p.fee) and not p.refill:
                why = "남은 돈 부족"
            if why:
                self._skip(t, p.key, coin, why, move, lag)
                continue
            self.entries.append({"key": p.key, "coin": coin, "signal_t": t, "due": t + p.latency, "move": move,
                                 "lag": lag, "g_ref": ref.g, "u_ref": ref.ask, "g": g, "spread": book.spread,
                                 "signal_book": book.top(min(3, p.detail_levels))})

    def on_book(self, t: float, coin: str, book: Book, alt: Book | None = None) -> None:
        """t 에 요청을 시작한 호가창. 지연이 지난(due ≤ t) 주문·관측만 처리한다."""
        if not book.ok:
            return
        for e in [e for e in self.entries if e["coin"] == coin and e["due"] <= t]:
            self.entries.remove(e)
            self._enter(t, e, book, alt)
        for key, pos in [(k, p) for (k, c), p in self.pos.items() if c == coin and p.exit_due <= t]:
            self._exit(t, key, pos, book, alt)
        for r in [r for r in self.radars if r["coin"] == coin]:
            for pt in r["points"]:
                if pt["at"] is None and pt["due"] <= t:
                    pt.update(at=t - r["t0"], ask=book.ask, bid=book.bid)
            if all(pt["at"] is not None for pt in r["points"]) or t - r["t0"] > 40:
                self.radars.remove(r)
                self.out.append({"type": "radar", "source": self.source, **{k: v for k, v in r.items() if k != "points"},
                                 "points": [{k: v for k, v in pt.items() if k != "due"} for pt in r["points"]
                                            if pt["at"] is not None]})

    # ── 실행 루프가 묻는 것 ──
    def next_due(self) -> float | None:
        """새 호가창이 필요한 가장 이른 시각."""
        dues = [e["due"] for e in self.entries] + [p.exit_due for p in self.pos.values()]
        dues += [pt["due"] for r in self.radars for pt in r["points"] if pt["at"] is None]
        return min(dues) if dues else None

    def due_coins(self, t: float) -> set[str]:
        """t 시점에 새 호가창이 필요한 코인 (지연이 지난 주문이 있는 코인, 모든 코인)."""
        return {e["coin"] for e in self.entries if e["due"] <= t} | \
               {c for (_, c), p in self.pos.items() if p.exit_due <= t}

    def alt_wanted(self, t: float) -> set[str]:
        """t 시점에 체결될 주문이 있는 코인 (코인원 호가도 같이 받으면 비교 기록이 남는다)."""
        coins = {e["coin"] for e in self.entries if e["due"] <= t} | \
                {c for (_, c), p in self.pos.items() if p.exit_due <= t}
        return {c for c in coins if c in BASE_COINS}  # 코인원 비교는 기본 코인만

    def drain(self) -> list[dict]:
        out, self.out = self.out, []
        return out

    def snapshot(self) -> dict:
        return {
            "source": self.source,
            "coins": self.live,
            "cash": dict(self.cash),
            "positions": [{"key": k, "coin": c, "price": p.price, "amount": p.amount, "fill_t": p.fill_t,
                           "exit_due": p.exit_due, "move": p.move} for (k, c), p in self.pos.items()],
        }

    # ── 내부 ──
    @staticmethod
    def _exit_reason(p: ScalpParams, pos: Position, g: float, book: Book) -> str | None:
        if p.stop is not None and book.bid <= pos.price * (1 - p.stop):
            return "손절"
        if p.origin_stop and g <= pos.g_ref:
            return "출발점 붕괴"
        if p.take is not None and book.bid >= pos.price * (1 + p.take):
            return "익절"
        if p.catchup and book.bid >= pos.u_ref * (1 + pos.move):
            return "따라잡음"
        return None

    def _skip(self, t: float, key: str, coin: str, why: str, move: float, lag: float) -> None:
        if t - self.last_skip.get((key, coin, why), -1e18) < 60:  # 같은 이유는 1분에 한 번만 기록
            return
        self.last_skip[(key, coin, why)] = t
        self.out.append({"type": "skip", "key": key, "coin": coin, "t": t, "why": why, "move": move, "lag": lag,
                         "source": self.source})

    def _enter(self, t: float, e: dict, book: Book, alt: Book | None) -> None:
        p = self.params[e["key"]]
        buy = market_buy(book, p.amount)
        if not buy.complete:
            return self._skip(t, p.key, e["coin"], "체결 시점 호가 잔량 부족", e["move"], e["lag"])
        if self.cash[p.key] < p.amount * (1 + p.fee):
            if not p.refill:
                return self._skip(t, p.key, e["coin"], "남은 돈 부족", e["move"], e["lag"])
            self.cash[p.key] += p.capital
            self.out.append({"type": "refill", "key": p.key, "t": t, "amount": p.capital, "source": self.source})
        self.cash[p.key] -= p.amount * (1 + p.fee)
        pos = Position(p.key, e["coin"], e["signal_t"], t, buy.price, buy.qty, p.amount, e["g_ref"], e["u_ref"],
                       e["move"], e["lag"], exit_due=t + p.max_hold + p.latency)
        pos.detail = {"g_signal": e["g"], "g_ref": e["g_ref"], "u_ref": e["u_ref"], "spread_signal": e["spread"],
                      "signal_book": e["signal_book"], "entry_book": book.top(p.detail_levels), "entry_mid": book.mid,
                      "entry_levels": buy.levels}
        if alt is not None and alt.ok:
            ab = market_buy(alt, p.amount)
            if ab.complete:
                pos.alt_price, pos.alt_qty = ab.price, ab.qty
                pos.detail["alt_entry_book"] = alt.top(3)
        self.pos[(p.key, e["coin"])] = pos
        self.last_entry[(p.key, e["coin"])] = e["signal_t"]

    def _exit(self, t: float, key: str, pos: Position, book: Book, alt: Book | None) -> None:
        p = self.params[key]
        sell = market_sell(book, pos.qty)
        proceeds = sell.krw + (pos.qty - sell.qty) * book.bids[-1][0]  # 잔량이 모자라면 나머지는 가장 낮은 호가로
        net_krw = proceeds * (1 - p.fee) - pos.amount * (1 + p.fee)
        self.cash[key] += proceeds * (1 - p.fee)
        alt_net = None
        if pos.alt_qty and alt is not None and alt.ok:
            s = market_sell(alt, pos.alt_qty)
            if s.complete:
                alt_net = s.krw / pos.amount - 1  # 코인원: 수수료 0% 가정
                pos.detail["alt_exit_book"] = alt.top(3)
        del self.pos[(key, pos.coin)]
        exit_signal = pos.exit_signal_t if pos.exit_signal_t is not None else pos.fill_t + p.max_hold
        self.out.append({
            "type": "trade", "key": key, "coin": pos.coin, "source": self.source,
            "signal_t": pos.signal_t, "entry_t": pos.fill_t, "exit_signal_t": exit_signal, "exit_t": t,
            "amount": pos.amount, "entry_price": pos.price, "exit_price": proceeds / pos.qty, "reason": pos.exit_reason,
            "gross_pct": proceeds / pos.amount - 1, "net_pct": net_krw / pos.amount, "net_krw": net_krw,
            "alt_net_pct": alt_net, "move": pos.move, "lag": pos.lag,
            "detail": {**pos.detail, "exit_book": book.top(p.detail_levels), "exit_mid": book.mid, "exit_levels": sell.levels,
                       "exit_complete": sell.complete, "latency_in": pos.fill_t - pos.signal_t,
                       "latency_out": t - exit_signal, "alt_entry_price": pos.alt_price},
        })
