"""1분 단위 데이터를 받아 data/minute/ 에 쌓는다. 매일 장 마감 후 자동 실행(윈도우 작업 스케줄러).

    python scripts/collect_minute.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if sys.stdout is not None:  # pythonw(창 없이 실행)에서는 출력 창이 없음
    sys.stdout.reconfigure(encoding="utf-8")

from autotrade.minute import fetch_recent, merge_into_store  # noqa: E402

STORE = ROOT / "data" / "minute"
LOG = STORE / "collect_log.txt"

# 종목코드 → (야후 티커, 이름). 코스피 상장은 .KS, 코스닥 상장은 .KQ
TICKERS = {
    "069500": ("069500.KS", "KODEX 200"),
    "122630": ("122630.KS", "KODEX 레버리지"),
    "114800": ("114800.KS", "KODEX 인버스"),
    "229200": ("229200.KS", "KODEX 코스닥150"),
    "233740": ("233740.KS", "KODEX 코스닥150레버리지"),
    "005930": ("005930.KS", "삼성전자"),
    "000660": ("000660.KS", "SK하이닉스"),
}


def main() -> int:
    lines = [f"[{datetime.now():%Y-%m-%d %H:%M}]"]
    failures = 0
    for code, (yahoo, name) in TICKERS.items():
        try:
            new = fetch_recent(yahoo)
            if new.empty:
                raise ValueError("받은 데이터 없음")
            added, total = merge_into_store(new, STORE / f"{code}.csv")
            lines.append(f"  {code} {name}: +{added}행 (전체 {total}행, "
                         f"{new.index.min():%m-%d}~{new.index.max():%m-%d %H:%M})")
        except Exception as e:  # noqa: BLE001 — 한 종목 실패가 나머지를 막지 않게
            failures += 1
            lines.append(f"  {code} {name}: 실패 — {str(e)[:120]}")

    text = "\n".join(lines)
    if sys.stdout is not None:
        print(text)
    STORE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
    return 1 if failures == len(TICKERS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
