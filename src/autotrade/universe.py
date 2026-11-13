"""종목군 목록. 지금 상장된 종목과 상장폐지된 종목을 함께 모아 '사라진 회사' 누락을 막는다."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from autotrade.data import DEFAULT_CACHE_DIR

# 주식이라기보다 펀드에 가까운 것 (리츠, 인프라 펀드, 스팩, 선박·부동산 투자회사)
FUND_LIKE = re.compile(r"리츠$|인프라|스팩|기업인수목적|\d+호$")


def kospi_universe(delisted_since: str, snapshot: Path | None = None) -> pd.DataFrame:
    """코스피 보통주 목록 (지금 상장 + delisted_since 이후 상장폐지).

    Returns: Code, Name, DelistingDate(상장 중이면 NaT) 열을 가진 표.
    목록은 날마다 바뀌므로 snapshot 파일에 저장하고, 있으면 그대로 다시 쓴다.
    """
    if snapshot is not None and snapshot.exists():
        df = pd.read_csv(snapshot, dtype={"Code": str}, parse_dates=["DelistingDate"])
        return df

    import FinanceDataReader as fdr

    listed = fdr.StockListing("KOSPI")[["Code", "Name"]].copy()
    listed["DelistingDate"] = pd.NaT

    gone = fdr.StockListing("KRX-DELISTING")
    gone["DelistingDate"] = pd.to_datetime(gone["DelistingDate"])
    gone = gone[
        (gone["Market"] == "KOSPI")
        & (gone["SecuGroup"] == "주권")
        & (gone["DelistingDate"] >= pd.Timestamp(delisted_since))
    ].rename(columns={"Symbol": "Code"})[["Code", "Name", "DelistingDate"]]

    df = pd.concat([listed, gone[~gone["Code"].isin(listed["Code"])]], ignore_index=True)
    df = df.drop_duplicates("Code", keep="first")
    df = df[df["Code"].str.fullmatch(r"\d{5}0")]  # 보통주 (우선주는 끝자리가 0이 아님)
    df = df[~df["Name"].str.contains(FUND_LIKE)]
    df = df.sort_values("Code").reset_index(drop=True)

    if snapshot is not None:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(snapshot, index=False)
    return df


DEFAULT_SNAPSHOT = DEFAULT_CACHE_DIR / "universe_kospi_2026-11-14.csv"
