"""한국투자증권 API 로 최근 약 1년 치 1분 데이터를 받아 data/minute_kis/ 에 저장한다.

    python scripts/backfill_kis_minute.py            # 전체 종목
    python scripts/backfill_kis_minute.py 069500     # 특정 종목만

- .env 에 KIS_APP_KEY, KIS_APP_SECRET (모의투자 키면 KIS_PAPER=true) 가 있어야 한다.
- 이미 받은 날은 건너뛰므로 중간에 끊겨도 다시 실행하면 이어서 받는다.
- 최근 날짜부터 거꾸로 받고, 데이터 없는 거래일이 5일 연속이면 보관 기간 끝으로 보고 멈춘다.
- 야후 데이터(data/minute/)와는 시각 표기 방식이 다를 수 있어 섞지 않고 따로 저장한다.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from autotrade.kis import KISClient, KISError, minute_bars_for_day  # noqa: E402
from autotrade.minute import merge_into_store  # noqa: E402
from collect_minute import TICKERS  # noqa: E402  (모으는 종목 목록은 한 곳에서 관리)

STORE = ROOT / "data" / "minute_kis"
EMPTY_STOP = 5
SAVE_EVERY = 20


def trading_days(lookback_days: int = 370) -> list[str]:
    """최근 거래일 목록(오늘 제외, 최신순). KODEX 200 일봉 날짜를 달력으로 쓴다."""
    import FinanceDataReader as fdr

    start = date.today() - timedelta(days=lookback_days)
    idx = fdr.DataReader("069500", start.isoformat()).index
    days = [d.strftime("%Y%m%d") for d in idx if d.date() < date.today()]
    return sorted(days, reverse=True)


def saved_days(path: Path) -> set[str]:
    if not path.exists():
        return set()
    idx = pd.read_csv(path, index_col=0, parse_dates=True, usecols=[0]).index
    return set(idx.strftime("%Y%m%d"))


def main(codes: list[str]) -> None:
    load_dotenv(ROOT / ".env")
    paper = os.getenv("KIS_PAPER", "false").strip().lower() == "true"
    client = KISClient(
        os.getenv("KIS_APP_KEY", "").strip(),
        os.getenv("KIS_APP_SECRET", "").strip(),
        paper=paper,
        token_path=ROOT / ".kis_token.json",
        pause=0.5 if paper else 0.1,
    )
    days = trading_days()
    print(f"{'모의투자' if paper else '실전'} 서버, 거래일 {len(days)}일 ({days[-1]} ~ {days[0]})")

    for code in codes:
        name = TICKERS.get(code, ("", code))[1]
        path = STORE / f"{code}.csv"
        done = saved_days(path)
        todo = [d for d in days if d not in done]
        print(f"\n{code} {name}: 이미 {len(done)}일, 받을 날 {len(todo)}일")

        buffer: list[pd.DataFrame] = []
        empty_run = got = 0
        for i, day in enumerate(todo, 1):
            try:
                df = minute_bars_for_day(client, code, day)
            except KISError as e:
                print(f"  {day} 실패 — {e}")
                break
            if df.empty:
                empty_run += 1
                if empty_run >= EMPTY_STOP:
                    print(f"  {day}부터 데이터 없음이 {EMPTY_STOP}일 연속 → 보관 기간 끝으로 보고 멈춤")
                    break
                continue
            empty_run = 0
            got += 1
            buffer.append(df)
            if len(buffer) >= SAVE_EVERY:
                merge_into_store(pd.concat(buffer), path)
                buffer.clear()
                print(f"  {i}/{len(todo)}일 진행 (가장 오래된 날 {day})")
        if buffer:
            merge_into_store(pd.concat(buffer), path)
        print(f"  완료: 새로 받은 날 {got}일")


if __name__ == "__main__":
    try:
        main(sys.argv[1:] or list(TICKERS))
    except KISError as e:
        raise SystemExit(f"중단: {e}")
