"""실험 결과 화면: 기록된 규칙 그대로 계산한 그래프와 표, 그리고 실험 기록 원문."""

from __future__ import annotations

import streamlit as st

import runners
from common import PRACTICE, VALIDATION, growth_chart, load_sections, pick_line, show_metrics

EXPERIMENTS = {
    1: ("강한 자산 갈아타기", runners.exp1),
    2: ("업종 급락 후 며칠 보유", runners.exp2),
    3: ("덜 흔들리는 주식", runners.exp3),
    4: ("밤에만 들고 있기", runners.exp4),
}
VALIDATION_DONE = {1}  # 검증 기간을 이미 한 번 돌린 실험
SLOW = {
    2: "처음 한 번은 매일 사고파는 계산이라 30초쯤 걸려요. 그다음부터는 바로 나옵니다.",
    3: "처음 한 번은 약 900개 종목을 불러오느라 1~2분 걸려요. 그다음부터는 바로 나옵니다.",
}


def pick_experiment(key: str) -> int:
    choice = st.segmented_control(
        "실험 고르기", options=list(EXPERIMENTS), default=1, key=key,
        format_func=lambda n: f"#{n} {EXPERIMENTS[n][0]}",
    )
    return choice or 1


def run_recorded(n: int, period: str) -> dict:
    if n == 1:
        return runners.exp1(period)
    result = EXPERIMENTS[n][1]()
    if n == 4:  # 처음 규칙(아침에 무조건 매도)도 참고로 함께
        pure = runners.exp4(mode="pure")
        name, curve = next(iter(pure["curves"].items()))
        result = {**result, "curves": {**result["curves"], f"{name} · 참고": curve}}
    return result


def period_picker(n: int) -> str:
    if n not in VALIDATION_DONE:
        st.caption(
            f":material/lock: 연습 기간({PRACTICE[0][:7]} ~ {PRACTICE[1][:7]})만 볼 수 있어요. "
            "검증 기간은 결과를 기록한 뒤 딱 한 번만 돌리는 규칙이라, 아직 잠가 두었습니다."
        )
        return "practice"
    labels = {
        "practice": f"연습 기간 ({PRACTICE[0][:7]} ~ {PRACTICE[1][:7]})",
        "validation": f"검증 기간 ({VALIDATION[0][:7]} ~ {VALIDATION[1][:7]})",
    }
    return st.radio("기간", list(labels), format_func=labels.get, horizontal=True, key=f"period-{n}")


def show_facts(facts: list[tuple[str, str]]) -> None:
    if facts:
        st.markdown("\n".join(f"- **{label}** — {value}" for label, value in facts))


def render() -> None:
    n = pick_experiment("results-exp")
    section = load_sections().get(n, "")
    title = EXPERIMENTS[n][0]
    verdict = pick_line(section, "판단")

    head, badge = st.columns([5, 1], vertical_alignment="center")
    head.subheader(f"#{n} {title}")
    if "탈락" in verdict:
        badge.badge("탈락", icon=":material/close:", color="red")
    elif "통과" in verdict:
        badge.badge("통과", icon=":material/check:", color="green")
    idea = pick_line(section, "가설")
    if idea:
        st.markdown(f"**아이디어** — {idea}")
    if verdict:
        st.markdown(f"**판단** — {verdict}")

    period = period_picker(n)
    with st.spinner(SLOW.get(n, "계산하는 중…")):
        result = run_recorded(n, period)
    growth_chart(result["curves"])
    show_metrics(result["curves"])
    show_facts(result["facts"])
    with st.expander("실험 기록 원문 보기 (docs/experiments.md)"):
        st.markdown(section or "기록을 찾지 못했습니다.")
