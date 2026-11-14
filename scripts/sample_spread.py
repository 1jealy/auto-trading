"""업비트·코인원의 실제 '사겠다'·'팔겠다' 가격 차이를 일정 간격으로 재서 기록한다.

    python scripts/sample_spread.py 30 5     # 30분 동안 5초마다

결과: data/cache/crypto/spread_samples.csv (UTC 시각, 거래소, 마켓, 최고 매수호가, 최저 매도호가, 차이 %)
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import requests  # noqa: E402

from autotrade.upbit import top_of_book, utcnow  # noqa: E402

OUT = ROOT / "data" / "cache" / "crypto" / "spread_samples.csv"
MARKETS = ["KRW-BTC", "KRW-ETH"]


def coinone_top(session: requests.Session, market: str) -> tuple[float, float]:
    quote, base = market.split("-")
    body = session.get(f"https://api.coinone.co.kr/public/v2/orderbook/{quote}/{base}", timeout=10).json()
    if body.get("result") != "success":
        raise RuntimeError(f"코인원 호가 실패: {body.get('error_code')}")
    return float(body["bids"][0]["price"]), float(body["asks"][0]["price"])


def main(minutes: float, every: float) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    new = not OUT.exists()
    session = requests.Session()
    end = utcnow() + timedelta(minutes=minutes)
    n = errors = 0
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "exchange", "market", "bid", "ask", "spread_pct"])
        while utcnow() < end:
            t = utcnow().strftime("%Y-%m-%d %H:%M:%S")
            for market in MARKETS:
                for name, fn in (("upbit", lambda m: top_of_book(m, session)),
                                 ("coinone", lambda m: coinone_top(session, m))):
                    try:
                        bid, ask = fn(market)
                        mid = (bid + ask) / 2
                        w.writerow([t, name, market, bid, ask, round((ask - bid) / mid * 100, 5)])
                        n += 1
                    except Exception as e:  # noqa: BLE001 — 한 번 실패해도 계속 잰다
                        errors += 1
                        print(f"{t} {name} {market} 실패: {str(e)[:80]}")
            f.flush()
            time.sleep(every)
    print(f"기록 {n}건, 실패 {errors}건 → {OUT}")


if __name__ == "__main__":
    mins = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    step = float(sys.argv[2]) if len(sys.argv) > 2 else 5
    main(mins, step)
