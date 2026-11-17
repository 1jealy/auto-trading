"""관찰(실험 아님): 국내 거래소(업비트·빗썸·코인원·코빗) 호가를 웹소켓으로 받아, 수수료를 넘을 만한 가격 차이(틈)가
하루 몇 번, 얼마나 오래 열리는지 기록한다. 주문은 내지 않는다. 수익률 계산이 아니라 '틈의 크기·빈도·지속 시간'만 센다.

    python scripts/obs_arbitrage.py run --minutes 10      # 10분 관찰하고 요약 (기록은 data/paper/arb.db)
    python scripts/obs_arbitrage.py run                   # 계속 (Ctrl+C 로 멈춤)
    python scripts/obs_arbitrage.py report                # 쌓인 기록 요약

- 두 거래소 이상에 있는 코인 전부를 본다. 호가가 3초 넘게 안 오면 그 거래소는 그 코인 비교에서 뺀다(가짜 틈 방지).
- 서버 위치에 따라 지연이 달라진다: 시작할 때 거래소별로 호가가 오는 간격과 연결 시간을 함께 기록한다.
- 환경 변수 PAPER_DATA_DIR 로 기록 폴더를 바꿀 수 있다(시험할 때 진짜 기록과 섞지 않기).
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
import websockets  # noqa: E402

from autotrade.arb import EXCHANGES, PARSERS, ArbStore, PaperArb, Tracker, report, sim_report  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (research; public market data)"}
URLS = {"upbit": "wss://api.upbit.com/websocket/v1", "bithumb": "wss://ws-api.bithumb.com/websocket/v1",
        "coinone": "wss://stream.coinone.co.kr", "korbit": "wss://ws-api.korbit.co.kr/v2/public"}


def listed() -> dict[str, set[str]]:
    """거래소별 원화 마켓 코인 목록."""
    def get(url):
        return requests.get(url, headers=UA, timeout=15).json()

    out: dict[str, set[str]] = {}
    out["upbit"] = {m["market"][4:] for m in get("https://api.upbit.com/v1/market/all") if m["market"].startswith("KRW-")}
    out["bithumb"] = {m["market"][4:] for m in get("https://api.bithumb.com/v1/market/all") if m["market"].startswith("KRW-")}
    out["coinone"] = set()
    try:
        d = get("https://api.coinone.co.kr/public/v2/markets/KRW")
        out["coinone"] = {m["target_currency"].upper() for m in d.get("markets", [])
                          if m.get("maintenance_status") == 0 and m.get("trade_status") == 1}
    except Exception:  # noqa: BLE001
        pass
    d = get("https://api.korbit.co.kr/v2/currencyPairs")
    out["korbit"] = {p["symbol"][:-4].upper() for p in d.get("data", []) if p["symbol"].endswith("_krw") and p.get("status") == "launched"}
    return out


class Watch:
    def __init__(self, coins_by_ex: dict[str, set[str]], store: ArbStore) -> None:
        self.coins_by_ex, self.store = coins_by_ex, store
        self.tracker = Tracker()
        self.sim = PaperArb()
        self.msgs = {e: 0 for e in EXCHANGES}
        self.connects = {e: 0 for e in EXCHANGES}
        self.first_msg_ms = {}
        self.gaps: dict[str, list[float]] = {e: [] for e in EXCHANGES}  # 같은 코인 호가 사이 간격(초)
        self.last_t: dict[tuple, float] = {}
        self.stop = asyncio.Event()

    def on_book(self, ex: str, coin: str, book, now: float) -> None:
        self.msgs[ex] += 1
        k = (ex, coin)
        if k in self.last_t and len(self.gaps[ex]) < 20000:
            self.gaps[ex].append(now - self.last_t[k])
        self.last_t[k] = now
        self.tracker.update(coin, ex, book, now)
        self.sim.update(coin, ex, book, now)

    async def feed(self, ex: str) -> None:
        coins = sorted(self.common(ex))
        parse = PARSERS[ex]
        backoff = 1.0
        while not self.stop.is_set():
            try:
                t0 = time.time()
                async with websockets.connect(URLS[ex], open_timeout=15, ping_interval=None, max_size=2**24) as ws:
                    self.connects[ex] += 1
                    self.first_msg_ms.setdefault(ex, None)
                    if ex in ("upbit", "bithumb"):
                        await ws.send(json.dumps([{"ticket": f"obs-{ex}"}, {"type": "orderbook", "codes": [f"KRW-{c}" for c in coins]},
                                                  {"format": "DEFAULT"}]))
                    elif ex == "coinone":
                        for c in coins:
                            await ws.send(json.dumps({"request_type": "SUBSCRIBE", "channel": "ORDERBOOK",
                                                      "topic": {"quote_currency": "KRW", "target_currency": c}}))
                            await asyncio.sleep(0.02)
                    else:
                        for i in range(0, len(coins), 50):
                            await ws.send(json.dumps([{"method": "subscribe", "type": "orderbook",
                                                       "symbols": [f"{c.lower()}_krw" for c in coins[i:i + 50]]}]))
                    last_ping = time.time()
                    backoff = 1.0
                    while not self.stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 30)
                        except asyncio.TimeoutError:
                            raise ConnectionError("30초 동안 메시지 없음")
                        now = time.time()
                        if self.first_msg_ms.get(ex) is None:
                            self.first_msg_ms[ex] = (now - t0) * 1000
                        if ex == "coinone" and now - last_ping > 600:
                            await ws.send(json.dumps({"request_type": "PING"}))
                            last_ping = now
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        got = parse(msg) if isinstance(msg, dict) else None
                        if got:
                            coin, book, _ = got
                            self.on_book(ex, coin, book, now)
                        self.flush()
            except Exception as e:  # noqa: BLE001
                if self.stop.is_set():
                    break
                print(f"[{time.strftime('%H:%M:%S')}] {ex} 연결 끊김({type(e).__name__}: {str(e)[:60]}) — {backoff:.0f}초 뒤 다시", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def common(self, ex: str) -> set[str]:
        """다른 거래소 하나 이상에도 있는 코인만."""
        mine = self.coins_by_ex.get(ex, set())
        others = set().union(*(v for k, v in self.coins_by_ex.items() if k != ex))
        return mine & others

    _last_flush = 0.0

    def flush(self) -> None:
        eps = self.tracker.drain()
        if eps:
            self.store.add_episodes(eps)
        rows = self.sim.drain()
        if rows:
            self.store.add_sim(rows)

    async def run(self, minutes: float | None) -> dict:
        t0 = time.time()
        tasks = [asyncio.create_task(self.feed(e)) for e in EXCHANGES if self.coins_by_ex.get(e)]
        try:
            end = t0 + minutes * 60 if minutes else None
            while end is None or time.time() < end:
                await asyncio.sleep(5)
                if int(time.time() - t0) % 60 < 5:
                    print(f"[{time.strftime('%H:%M:%S')}] 호가 수신 " + " ".join(f"{e} {n}" for e, n in self.msgs.items())
                          + f" | 열린 틈 {len(self.tracker.open)}", flush=True)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for key in list(self.tracker.open):
                self.tracker._close(key, time.time())
            self.flush()
        gaps = {}
        for e, xs in self.gaps.items():
            xs = sorted(xs)
            gaps[e] = {"n": len(xs), "median_ms": xs[len(xs) // 2] * 1000 if xs else None,
                       "p90_ms": xs[int(len(xs) * 0.9)] * 1000 if xs else None}
        return {"seconds": time.time() - t0, "msgs": self.msgs, "connects": self.connects,
                "first_msg_ms": self.first_msg_ms, "update_gap": gaps,
                "coins": {e: len(self.common(e)) for e in EXCHANGES}}


def show(stats: dict, rep: dict) -> None:
    print(f"\n== 관찰 {stats['seconds'] / 60:.1f}분 · 서버 {socket.gethostname()} ==")
    print("거래소별 비교 코인 수:", stats["coins"])
    print("받은 호가 수:", stats["msgs"], "| 연결 횟수:", stats["connects"])
    print("첫 메시지까지(연결+구독, ms):", {k: (round(v) if v else None) for k, v in stats["first_msg_ms"].items()})
    print("같은 코인 호가 사이 간격(ms, 가운데/90%):",
          {e: (round(v["median_ms"]), round(v["p90_ms"])) if v["median_ms"] else None for e, v in stats["update_gap"].items()})
    print(f"틈 에피소드(수수료 전 0.10% 이상) {rep['episodes']}개 — 같은 틈이 사는 금액 두 가지(50만·200만 원)로 따로 세어져 실제 사건 수는 이보다 적음")
    print("※ 빠른 틈 = 호가가 바뀌어 2초 안에 닫힌 것(속도 싸움). 오래 가는 차이 = 2초 넘게 이어지거나 한쪽 호가가 조용해 안 닫힌 것(입출금 중단 등, 송금이 필요).")
    print("※ 이 컴퓨터가 거래소마다 호가를 받는 시차 때문에 생기는 '가짜 빠른 틈'이 섞여 있을 수 있음 — 서버 위치에 따라 달라지니 서울 서버에서 다시 재 볼 것.")
    for name, s in rep["scenarios"].items():
        r = lambda v: None if v is None else round(v, 4)  # noqa: E731
        rate = f"하루 {s['per_day']:.0f}개" if stats["seconds"] >= 3600 else "관찰 1시간 미만이라 하루 환산 생략"
        print(f"  {name}: {s['n']}개 ({rate}, 코인 {s['coins']}종), 지속 가운데 {r(s['dur_ms_median'])}ms / 90% {r(s['dur_ms_p90'])}ms, "
              f"남는 폭 가운데 {r(s['net_median'])}, 최대 {r(s['net_max'])}")
        if s["top_pairs"]:
            print("    방향:", s["top_pairs"], "| 코인:", s["top_coins"])


def start_in_thread():
    """화면 서버 안에서 함께 돌릴 때(server.py --arb). 끊기면 30초 뒤 다시 시작한다."""
    import threading

    def forever() -> None:
        while True:
            t0 = time.time()
            try:
                store = ArbStore()
                w = Watch(listed(), store)
                stats = asyncio.run(w.run(None))
                store.add_run(t0, time.time(), socket.gethostname(), "server --arb", stats)
            except Exception as e:  # noqa: BLE001
                print(f"차익 관찰 오류({type(e).__name__}: {str(e)[:80]}) — 30초 뒤 다시", flush=True)
            time.sleep(30)

    th = threading.Thread(target=forever, daemon=True, name="arb-watch")
    th.start()
    return th


def show_sim(rows: list[dict], seconds: float, odd: int | None = None) -> None:
    print(f"\n== 모의 체결 (50만 원씩, {seconds / 60:.0f}분) — 후보/수수료/지연(초) ==")
    print(f"{'후보':16s} {'체결':>5s} {'못함':>5s} {'시간당':>6s} {'이긴비율':>7s} {'신호때':>8s} {'실제평균':>8s} {'합계(원)':>10s} 코인수 · 많이 번/잃은 코인")
    for r in sim_report(rows, seconds):
        f = lambda v, p=3: "   —  " if v is None else f"{v * 100:+.{p}f}%"  # noqa: E731
        top = ", ".join(f"{c} {v * 100:+.2f}%" for c, v in r["top"])
        print(f"{r['key']:16s} {r['filled']:5d} {r['failed']:5d} {r['per_hour']:6.1f} {('—' if r['win'] is None else f'{r['win']:.0%}'):>7s} "
              f"{f(r['mean_signal']):>8s} {f(r['mean_net']):>8s} {r['total_krw']:>10,.0f} {r['coins']:3d} · {top}")
    if odd is not None:
        print(f"수수료 전 3% 넘는 이상한 차이를 본 횟수(거래 안 함): {odd:,}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    store = ArbStore()
    if cmd == "report":
        rows = [dict(r) for r in store.episodes()]
        runs = store.runs()
        secs = sum(r["end"] - r["start"] for r in runs)
        rep = report(rows, secs)
        stats = json.loads(runs[-1]["stats"]) if runs else {"seconds": secs, "coins": {}, "msgs": {}, "connects": {},
                                                           "first_msg_ms": {}, "update_gap": {}}
        stats["seconds"] = secs
        show(stats, rep)
        show_sim(store.sim_trades(), secs)
        return
    minutes = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else None
    coins = listed()
    print({e: len(c) for e, c in coins.items()}, "← 거래소별 원화 마켓 코인 수", flush=True)
    w = Watch(coins, store)
    t0 = time.time()
    try:
        stats = asyncio.run(w.run(minutes))
    except KeyboardInterrupt:
        stats = {"seconds": time.time() - t0, "msgs": w.msgs, "connects": w.connects, "first_msg_ms": w.first_msg_ms,
                 "update_gap": {}, "coins": {}}
    store.add_run(t0, time.time(), socket.gethostname(), "obs_arbitrage", stats)
    rows = [dict(r) for r in store.episodes(t0)]
    show(stats, report(rows, stats["seconds"]))
    show_sim(store.sim_trades(t0), stats["seconds"], w.sim.odd)


if __name__ == "__main__":
    main()
