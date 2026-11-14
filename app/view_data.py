"""데이터 보기: 하루 단위 가격, 1분 데이터, 1분 데이터 모으기 상태."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from common import ROOT, SERIES_COLORS

CACHE = ROOT / "data" / "cache"
MINUTE_SOURCES = {
    "야후 파이낸스 (매일 자동으로 모으는 것)": ROOT / "data" / "minute",
    "한국투자증권 (최근 1년 치)": ROOT / "data" / "minute_kis",
}
LOG = ROOT / "data" / "minute" / "collect_log.txt"
UNIVERSE = CACHE / "universe_kospi_2026-11-14.csv"
DAILY_PATTERN = re.compile(r"^(\d{6})_2014-08-01_2026-10-31\.csv$")
ETF_NAMES = {
    "069500": "KODEX 200", "143850": "TIGER 미국S&P500선물(H)", "148070": "KOSEF 국고채10년",
    "138230": "KOSEF 미국달러선물", "153130": "KODEX 단기채권", "229200": "KODEX 코스닥150",
    "091160": "KODEX 반도체", "091170": "KODEX 은행", "117700": "KODEX 건설", "102970": "KODEX 증권",
    "139260": "TIGER 200 IT", "143860": "TIGER 헬스케어", "139250": "TIGER 200 에너지화학",
    "139280": "TIGER 200 생활소비재",
}
STALE_DAYS = 6
WEEKDAYS = "월화수목금토일"


@st.cache_data(show_spinner=False)
def ticker_names() -> dict[str, str]:
    names = dict(ETF_NAMES)
    if UNIVERSE.exists():
        uni = pd.read_csv(UNIVERSE, dtype={"Code": str})
        names.update(zip(uni["Code"], uni["Name"]))
    if LOG.exists():
        names.update(re.findall(r"^\s+(\d{6}) (.+?): ", LOG.read_text(encoding="utf-8"), re.MULTILINE))
    return names


def label(code: str) -> str:
    name = ticker_names().get(code)
    return f"{name} ({code})" if name else code


@st.cache_data(show_spinner=False)
def read_prices(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df[df["Close"] > 0] if "Close" in df else df


def price_and_volume(df: pd.DataFrame, x: str, axis_format: str, time_format: str) -> alt.VConcatChart:
    data = df.reset_index(names=x)
    base = alt.Chart(data).encode(x=alt.X(f"{x}:T", title=None, axis=alt.Axis(format=axis_format)))
    y_axis = {"minExtent": 72}  # 위아래 그래프의 가로축 시작점을 맞춤
    line = base.mark_line(strokeWidth=1.4, color=SERIES_COLORS[0]).encode(
        y=alt.Y("Close:Q", title="가격 (원)", scale=alt.Scale(zero=False), axis=alt.Axis(**y_axis)),
        tooltip=[alt.Tooltip(f"{x}:T", format=time_format), alt.Tooltip("Close:Q", title="종가", format=",.0f")],
    ).properties(height=300)
    if "Volume" not in data:
        return alt.vconcat(line)
    bars = base.mark_bar(color=SERIES_COLORS[1], opacity=0.7).encode(
        y=alt.Y("Volume:Q", title="거래량", axis=alt.Axis(**y_axis)),
        tooltip=[alt.Tooltip(f"{x}:T", format=time_format), alt.Tooltip("Volume:Q", title="거래량", format=",.0f")],
    ).properties(height=90)
    return alt.vconcat(line, bars, spacing=6).resolve_scale(x="shared")


def daily_view() -> None:
    codes = sorted(m.group(1) for p in CACHE.glob("*.csv") if (m := DAILY_PATTERN.match(p.name)))
    if not codes:
        st.info("하루 단위 가격 파일이 없어요. 실험 스크립트(scripts/exp0*.py)를 한 번 돌리면 data/cache 에 저장됩니다.")
        return
    default = codes.index("069500") if "069500" in codes else 0
    code = st.selectbox("종목 (이름이나 코드로 검색)", codes, index=default, format_func=label, key="daily-code")
    path = CACHE / f"{code}_2014-08-01_2026-10-31.csv"
    df = read_prices(str(path), path.stat().st_mtime)
    if df.empty:
        st.warning("이 파일에는 가격이 없습니다.")
        return
    first, last = df.index.min().date(), df.index.max().date()
    start, end = st.slider("기간", min_value=first, max_value=last, value=(first, last), format="YYYY-MM", key="daily-range")
    shown = df.loc[str(start):str(end)]
    st.altair_chart(price_and_volume(shown, "날짜", "%Y-%m", "%Y-%m-%d"), width="stretch")
    kind = "ETF — 분배금이 반영된 가격" if code in ETF_NAMES else "개별 주식 — 액면분할은 반영, **배당은 빠진** 가격"
    st.caption(f"{kind}. 거래가 멈춘 날은 빠져 있어요. {len(shown):,}일치.")


def minute_files(folder: Path) -> list[str]:
    return sorted(p.stem for p in folder.glob("*.csv")) if folder.exists() else []


def minute_view() -> None:
    source = st.radio("어디서 받은 데이터", list(MINUTE_SOURCES), horizontal=True, key="minute-src")
    folder = MINUTE_SOURCES[source]
    codes = minute_files(folder)
    if not codes:
        st.info(
            "아직 받은 데이터가 없어요. 한국투자증권 키를 `.env`에 넣고 "
            "`.venv/Scripts/python scripts/backfill_kis_minute.py`를 실행하면 여기에 최근 1년 치가 보입니다.",
            icon=":material/inbox:",
        )
        return
    default = codes.index("069500") if "069500" in codes else 0
    code = st.selectbox("종목", codes, index=default, format_func=label, key="minute-code")
    path = folder / f"{code}.csv"
    df = read_prices(str(path), path.stat().st_mtime)
    days = sorted({d.date() for d in df.index}, reverse=True)
    day = st.selectbox("날짜", days, format_func=lambda d: f"{d:%Y-%m-%d} ({WEEKDAYS[d.weekday()]})", key="minute-day")
    one = df.loc[str(day)]
    st.altair_chart(price_and_volume(one, "시각", "%H:%M", "%H:%M"), width="stretch")
    cols = st.columns(4)
    for col, (name, value) in zip(cols, [("시가", one["Open"].iloc[0]), ("고가", one["High"].max()),
                                         ("저가", one["Low"].min()), ("종가", one["Close"].iloc[-1])]):
        col.metric(name, f"{value:,.0f}원")
    st.caption(f"{len(one)}개 (1분마다 하나). 모은 날 {len(days)}일, {days[-1]} ~ {days[0]}.")


def parse_log(text: str) -> list[tuple[datetime, list[str]]]:
    runs: list[tuple[datetime, list[str]]] = []
    for line in text.splitlines():
        stamp = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]", line)
        if stamp:
            runs.append((datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M"), []))
        elif runs and line.strip():
            runs[-1][1].append(line.strip())
    return runs


def file_summary(folder: Path) -> pd.DataFrame:
    rows = []
    for code in minute_files(folder):
        idx = pd.read_csv(folder / f"{code}.csv", index_col=0, parse_dates=True, usecols=[0]).index
        rows.append({"종목": label(code), "모은 개수": len(idx), "모은 날": len({d.date() for d in idx}),
                     "처음": idx.min(), "마지막": idx.max()})
    return pd.DataFrame(rows)


def status_view() -> None:
    if not LOG.exists():
        st.info("아직 한 번도 모으지 않았어요. 작업 스케줄러의 **auto-trading minute collect** 작업이 매일 18:00에 실행합니다.")
        return
    runs = parse_log(LOG.read_text(encoding="utf-8"))
    last_time, last_lines = runs[-1]
    days_ago = (datetime.now() - last_time).days
    if days_ago > STALE_DAYS:
        st.error(f"마지막으로 모은 지 **{days_ago}일** 지났어요. 7일이 넘으면 그 사이 1분 데이터는 다시 받을 수 없습니다. "
                 "컴퓨터를 켜 두거나 `scripts/collect_minute.py`를 직접 실행하세요.", icon=":material/warning:")
    else:
        when = "오늘" if last_time.date() == datetime.now().date() else f"{max(days_ago, 1)}일 전"
        st.success(f"마지막으로 모은 때: **{last_time:%Y-%m-%d %H:%M}** ({when}). 매일 18:00에 자동으로 모읍니다.",
                   icon=":material/check_circle:")
    failed = [ln for ln in last_lines if "실패" in ln]
    if failed:
        st.warning("마지막 실행에서 실패한 종목: " + " / ".join(failed))
    for title, folder in MINUTE_SOURCES.items():
        st.markdown(f"**{title}**")
        summary = file_summary(folder)
        if summary.empty:
            st.caption("아직 없음")
            continue
        st.dataframe(summary, hide_index=True, width="stretch", column_config={
            "모은 개수": st.column_config.NumberColumn(format="localized"),
            "처음": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
            "마지막": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        })
    with st.expander(f"실행 기록 ({len(runs)}번)"):
        st.code("\n".join(f"[{t:%Y-%m-%d %H:%M}] " + " | ".join(lines) for t, lines in reversed(runs[-20:])),
                language=None)


def render() -> None:
    views = {"하루 단위 가격": daily_view, "1분 데이터": minute_view, "모으기 상태": status_view}
    choice = st.segmented_control("보기", list(views), default="하루 단위 가격", key="data-view",
                                  label_visibility="collapsed")
    views[choice or "하루 단위 가격"]()
