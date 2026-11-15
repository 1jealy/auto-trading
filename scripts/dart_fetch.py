"""#15: DART 공시 목록과 해당 공시 문서를 내려받아 data/cache/dart/ 에 저장한다.

여러 번 실행해도 받은 것은 건너뛴다(하루 호출 한도에 걸리면 다음 날 다시 실행).
    python scripts/dart_fetch.py list    # 목록만
    python scripts/dart_fetch.py docs    # A·B·C 공시 문서
"""

from __future__ import annotations

import sys

import pandas as pd

from autotrade.dart import DartClient, DartLimitError, classify

START_YEAR, END = 2016, pd.Timestamp("2026-10-31")


def quarters(start_year: int = START_YEAR):
    for y in range(start_year, END.year + 1):
        for q in range(1, 5):
            if pd.Timestamp(year=y, month=3 * q - 2, day=1) <= END:
                yield y, q


def all_lists(workers: int = 4, page_workers: int = 4) -> pd.DataFrame:
    """분기별 목록을 동시에 여러 개 받는다(한 번 호출에 2초 넘게 걸려서)."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = [(ty, y, q) for ty in ("I001", "I002") for y, q in quarters()]

    def one(job):
        ty, y, q = job
        df = DartClient().list_quarter(ty, y, q, workers=page_workers)
        print(ty, y, q, len(df), flush=True)
        return df

    with ThreadPoolExecutor(workers) as pool:
        frames = list(pool.map(one, jobs))
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    c = DartClient()
    lst = all_lists()
    lst["kind"] = lst["report_nm"].map(classify)
    hits = lst[lst["kind"].notna()]
    print("종류별 공시 수:", hits["kind"].value_counts().to_dict())
    if sys.argv[1:] == ["docs"]:
        todo = [r for r in hits["rcept_no"] if not (c.cache_dir / "docs" / f"{r}.html").exists()]
        print("받을 문서:", len(todo), flush=True)
        try:
            for i, r in enumerate(todo):
                c.document(r)
                if i % 500 == 0:
                    print("docs", i, flush=True)
        except DartLimitError:
            print("하루 한도에 걸림 — 내일 다시 실행")


if __name__ == "__main__":
    main()
