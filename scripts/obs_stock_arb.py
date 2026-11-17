"""관찰(실험 아님): 주식 차익 틈 — KRX vs 넥스트레이드, 같은 지수 ETF 두 개. 주문은 내지 않는다.

    python scripts/obs_stock_arb.py run --minutes 60    # 장중에 실행. 기록은 data/paper/stock_arb.db
    python scripts/obs_stock_arb.py report              # 쌓인 기록 요약

- .env 에 KIS_APP_KEY, KIS_APP_SECRET 이 있어야 한다(실전 계좌 키. 실시간 호가는 모의투자 키로 안 될 수 있음). 값은 화면·로그에 내지 않는다.
- 한국투자증권 웹소켓은 한 연결에 등록 41개까지: 종목 10개 × 2시장 + ETF 8개 = 28개.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
import websockets  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from autotrade.stock_arb import ETF_PAIRS, STOCKS, TR_KRX, TR_NXT, StockGapWatch, parse_kis, summary  # noqa: E402

REAL_URL, PAPER_URL = "https://openapi.koreainvestment.com:9443", "https://openapivts.koreainvestment.com:29443"
WS_REAL, WS_PAPER = "ws://ops.koreainvestment.com:21000", "ws://ops.koreainvestment.com:31000"


def approval_key(paper: bool) -> str:
    load_dotenv(ROOT / ".env")
    key, secret = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()
    if not key or not secret:
        raise SystemExit(".env 에 KIS_APP_KEY / KIS_APP_SECRET 이 없습니다.")
    r = requests.post((PAPER_URL if paper else REAL_URL) + "/oauth2/Approval",
                      json={"grant_type": "client_credentials", "appkey": key, "secretkey": secret}, timeout=15)
    r.raise_for_status()
    return r.json()["approval_key"]


def subscriptions() -> list[tuple[str, str]]:
    subs = [(TR_KRX, c) for c in STOCKS] + [(TR_NXT, c) for c in STOCKS]
    for a, b, _ in ETF_PAIRS:
        subs += [(TR_KRX, a), (TR_KRX, b)]
    return subs


async def run(minutes: float) -> None:
    paper = os.getenv("KIS_PAPER", "false").strip().lower() == "true"
    akey = approval_key(paper)
    w = StockGapWatch()
    t0 = time.time()
    end = t0 + minutes * 60
    last_print = 0.0
    while time.time() < end:
        try:
            async with websockets.connect(WS_PAPER if paper else WS_REAL, ping_interval=None, open_timeout=15) as ws:
                for tr, code in subscriptions():
                    await ws.send(json.dumps({"header": {"approval_key": akey, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                                              "body": {"input": {"tr_id": tr, "tr_key": code}}}))
                    await asyncio.sleep(0.05)
                while time.time() < end:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    now = time.time()
                    if raw and raw[0] == "{":
                        msg = json.loads(raw)
                        if msg.get("header", {}).get("tr_id") == "PINGPONG":
                            await ws.send(raw)
                        elif msg.get("body", {}).get("rt_cd") not in (None, "0"):
                            print("등록 응답:", msg.get("body", {}).get("msg1"), msg.get("header", {}).get("tr_key"), flush=True)
                        continue
                    for tr, code, book in parse_kis(raw):
                        w.on(tr, code, book, now)
                    if now - last_print > 60:
                        last_print = now
                        print(f"[{time.strftime('%H:%M:%S')}] 호가 수신 KRX {w.msgs['KRX']} NXT {w.msgs['NXT']} | 열린 틈 {len(w.open)}", flush=True)
        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as e:
            print(f"[{time.strftime('%H:%M:%S')}] 연결 끊김({type(e).__name__}) — 3초 뒤 다시", flush=True)
            await asyncio.sleep(3)
    w.close_all(time.time())
    w.db.execute("INSERT INTO runs (start, end, msgs) VALUES (?,?,?)", (t0, time.time(), json.dumps(w.msgs)))
    w.db.commit()
    show(t0)


def show(since: float = 0.0) -> None:
    rows = summary(since=since)
    print("\n== 비용 뺀 뒤에도 남는 틈 (관찰, 수익 계산 아님) — 최저 수수료(유관기관 0.0036%만) 기준, 괄호는 보통 수수료(0.015%)로도 남은 횟수 ==")
    if not rows:
        print("없음 — 비용(교차: 수수료 2번 + 거래세, ETF 짝: 수수료 4번)을 넘는 틈이 한 번도 안 나왔습니다.")
    for r in rows:
        print(f"{r['kind']:4s} {r['name'][:28]:28s} {r['dir']:10s} {r['n']:5d}번({r['n_std'] or 0}) · 평균 {r['avg'] * 100:+.3f}% · 최대 {r['max'] * 100:+.3f}% "
              f"· 평균 {r['dur']:.1f}초 · 1초 넘게 {r['over1s']}번")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "run":
        mins = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 60.0
        asyncio.run(run(mins))
    else:
        show()
