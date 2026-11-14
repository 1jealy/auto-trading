"""화면 여러 곳에서 같이 쓰는 것: 경로, 지표 표, 성장 그래프, 실험 기록 읽기."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from autotrade.metrics import compare  # noqa: E402

EXPERIMENTS_MD = ROOT / "docs" / "experiments.md"
PRACTICE = ("2015-09-01", "2020-12-31")
VALIDATION = ("2021-01-01", "2026-10-31")

METRIC_LABELS = {
    "total_return": "전체 수익률",
    "cagr": "1년 평균",
    "volatility": "흔들림",
    "sharpe": "흔들림 대비 수익",
    "max_drawdown": "가장 크게 떨어졌을 때",
}

# 첫 번째 선(전략)은 강조색. 자주 나오는 비교 대상은 어느 화면에서든 같은 색.
SERIES_COLORS = ["#2563C9", "#8C939D", "#C77A2E", "#3E8E5E", "#8A63B8"]
FIXED_COLORS = {"KODEX 200": "#8C939D", "단기채권": "#3E8E5E"}
OTHER_COLORS = ["#C77A2E", "#8A63B8", "#2E9AA8"]


def series_colors(names: list[str]) -> list[str]:
    colors, others = [], iter(OTHER_COLORS)
    for i, name in enumerate(names):
        fixed = next((c for key, c in FIXED_COLORS.items() if name.startswith(key)), None)
        colors.append(SERIES_COLORS[0] if i == 0 else fixed or next(others, "#B0B4BA"))
    return colors


def metrics_frame(curves: dict[str, pd.Series]) -> pd.DataFrame:
    """compare() 결과를 화면용 이름과 % 단위로 바꾼다."""
    table = compare(curves).rename(columns=METRIC_LABELS)
    for col in table.columns:
        if col != METRIC_LABELS["sharpe"]:
            table[col] = table[col] * 100
    return table.rename_axis("비교 대상").reset_index()


def show_metrics(curves: dict[str, pd.Series]) -> None:
    pct = st.column_config.NumberColumn(format="%.1f%%")
    st.dataframe(
        metrics_frame(curves),
        width="stretch",
        hide_index=True,
        column_config={
            "비교 대상": st.column_config.TextColumn(width="medium", pinned=True),
            METRIC_LABELS["total_return"]: pct,
            METRIC_LABELS["cagr"]: pct,
            METRIC_LABELS["volatility"]: pct,
            METRIC_LABELS["max_drawdown"]: pct,
            METRIC_LABELS["sharpe"]: st.column_config.NumberColumn(format="%.2f"),
        },
    )


def growth_chart(curves: dict[str, pd.Series], height: int = 360) -> None:
    """1,000만 원을 넣었다면 얼마가 됐을지(만 원 단위) 선 그래프."""
    frames = []
    for name, curve in curves.items():
        s = curve.dropna()
        if s.empty:
            continue
        frames.append(pd.DataFrame({"날짜": s.index, "금액(만 원)": 1000 * s / s.iloc[0], "비교 대상": name}))
    if not frames:
        st.info("그릴 데이터가 없습니다.")
        return
    data = pd.concat(frames, ignore_index=True)
    names = list(curves)
    chart = (
        alt.Chart(data)
        .mark_line(strokeWidth=1.6)
        .encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y", tickCount="year")),
            y=alt.Y("금액(만 원):Q", title="1,000만 원을 넣었다면 (만 원)", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "비교 대상:N",
                scale=alt.Scale(domain=names, range=series_colors(names)),
                legend=alt.Legend(orient="top", direction="vertical", title=None, labelLimit=400),
            ),
            tooltip=[
                alt.Tooltip("비교 대상:N"),
                alt.Tooltip("날짜:T", format="%Y-%m-%d"),
                alt.Tooltip("금액(만 원):Q", format=",.0f"),
            ],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


@st.cache_data(show_spinner=False)
def experiment_sections(mtime: float) -> dict[int, str]:
    """docs/experiments.md 를 실험 번호별 글로 나눈다. mtime 은 파일이 바뀌면 다시 읽게 하는 용도."""
    text = EXPERIMENTS_MD.read_text(encoding="utf-8")
    parts = re.split(r"^### #(\d+) ", text, flags=re.MULTILINE)
    sections = {}
    for number, body in zip(parts[1::2], parts[2::2]):
        sections[int(number)] = "### " + body.split("\n---\n")[0].strip()
    return sections


def load_sections() -> dict[int, str]:
    return experiment_sections(EXPERIMENTS_MD.stat().st_mtime)


def pick_line(section: str, label: str) -> str:
    """'- **가설:** ...' 또는 '**판단 ...:** ...' 항목의 내용만 꺼낸다(바로 이어지는 들여쓴 줄 포함)."""
    lines = section.splitlines()
    for i, line in enumerate(lines):
        cleaned = line.strip().lstrip("-").strip()
        if not cleaned.startswith(f"**{label}"):
            continue
        parts = [re.sub(r"^\*\*[^*]+\*\*\s*", "", cleaned)]
        for nxt in lines[i + 1:]:
            if not nxt.startswith("  ") or nxt.strip().startswith("-"):
                break
            parts.append(nxt.strip())
        return " ".join(p for p in parts if p)
    return ""
