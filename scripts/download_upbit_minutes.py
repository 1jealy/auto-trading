"""업비트 1분 데이터를 달마다 받아 data/cache/crypto/upbit/<마켓>/YYYY-MM.csv.gz 로 저장한다.

    python scripts/download_upbit_minutes.py                      # 기본: BTC, ETH, 2022-01 ~ 지난달
    python scripts/download_upbit_minutes.py KRW-BTC 2024-01      # 마켓, 시작 달 지정

이미 받은 달은 건너뛰므로 끊겨도 다시 실행하면 이어서 받는다. 시각은 UTC.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from autotrade.upbit import download_month, last_complete_month, month_range  # noqa: E402

STORE = ROOT / "data" / "cache" / "crypto" / "upbit"
DEFAULT_MARKETS = ["KRW-BTC", "KRW-ETH"]  # 기간 내내 있었던 코인만 (사라진 코인 문제 피하기)
DEFAULT_FIRST = "2022-01"


def main(markets: list[str], first: str) -> None:
    last = last_complete_month()
    for market in markets:
        t0 = time.time()
        for y, m in month_range(first, last):
            path, n = download_month(STORE, market, y, m)
            status = "이미 있음" if n < 0 else f"{n:,}행"
            print(f"{market} {y}-{m:02d}: {status}")
        print(f"{market} 완료 ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    args = sys.argv[1:]
    markets = [a for a in args if a.startswith("KRW-")] or DEFAULT_MARKETS
    firsts = [a for a in args if a[:2] == "20"]
    main(markets, firsts[0] if firsts else DEFAULT_FIRST)
