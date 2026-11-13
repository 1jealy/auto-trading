from __future__ import annotations

import pandas as pd

from autotrade.minute import COLUMNS, merge_into_store


def bars(start: str, n: int, close: float) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="min", name="Datetime")
    return pd.DataFrame({c: close for c in COLUMNS}, index=idx)


def test_first_save_and_append(tmp_path):
    path = tmp_path / "x.csv"
    assert merge_into_store(bars("2026-11-10 09:00", 5, 100.0), path) == (5, 5)
    assert merge_into_store(bars("2026-11-10 09:05", 3, 101.0), path) == (3, 8)


def test_overlap_is_not_duplicated_and_newer_wins(tmp_path):
    path = tmp_path / "x.csv"
    merge_into_store(bars("2026-11-10 09:00", 5, 100.0), path)
    added, total = merge_into_store(bars("2026-11-10 09:03", 4, 200.0), path)
    assert (added, total) == (2, 7)
    saved = pd.read_csv(path, index_col=0, parse_dates=True)
    assert saved.index.is_monotonic_increasing
    assert saved.loc["2026-11-10 09:02", "Close"] == 100.0
    assert saved.loc["2026-11-10 09:03", "Close"] == 200.0
