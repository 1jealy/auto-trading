"""자동매매 연구 노트 — 실험 결과와 데이터를 보는 화면.

    .venv/Scripts/python -m streamlit run app/app.py     (또는 실행.bat 더블클릭)
"""

from __future__ import annotations

import streamlit as st

import view_data
import view_explore
import view_results

st.set_page_config(page_title="자동매매 연구 노트", page_icon=":material/monitoring:", layout="wide")

# 글자 선택 색과 표·숫자 정렬을 화면 색에 맞춤 (고정된 CSS 만 넣음)
st.html("""
<style>
::selection { background: rgba(37, 99, 201, 0.28); }
h1, h2, h3, p, li, label { word-break: keep-all; overflow-wrap: anywhere; }
[data-testid="stMetricValue"], [data-testid="stMarkdownContainer"] li { font-variant-numeric: tabular-nums; }
</style>
""")

st.title("자동매매 연구 노트")
st.caption("결과를 보기 전에 규칙을 정하고, 연습 기간 → 기록 → 검증 기간 한 번 순서로 시험한 기록입니다.")

results_tab, explore_tab, data_tab = st.tabs([
    ":material/insights: 실험 결과", ":material/tune: 숫자 바꿔 보기", ":material/database: 데이터 보기",
])
with results_tab:
    view_results.render()
with explore_tab:
    view_explore.render()
with data_tab:
    view_data.render()
