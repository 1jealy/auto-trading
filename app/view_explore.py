"""숫자 바꿔 보기: 연습 기간에서만, 규칙의 숫자를 바꿔 기록된 규칙과 비교한다. 결과는 어디에도 저장하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

import runners
from common import PRACTICE, growth_chart, show_metrics
from view_results import EXPERIMENTS, SLOW, pick_experiment, show_facts


@dataclass(frozen=True)
class Knob:
    key: str  # runners 함수의 인자 이름
    label: str
    low: float
    high: float
    default: float
    step: float
    unit: str = ""
    scale: float = 1.0  # 화면 값 × scale = 함수에 넘길 값
    help: str = ""

    def to_arg(self, shown: float):
        value = shown * self.scale
        return int(round(value)) if float(self.step).is_integer() and self.scale >= 1 else round(value, 8)


KNOBS: dict[int, list[Knob]] = {
    1: [
        Knob("lookback", "몇 개월 수익률로 비교할까", 3, 24, 12, 1, "개월", 21, "기록된 규칙: 12개월"),
        Knob("slippage", "원하는 값에 못 사는 손해 (한 번)", 0.0, 0.3, 0.05, 0.01, "%", 0.01),
    ],
    2: [
        Knob("threshold", "평소 움직임의 몇 배 떨어지면 살까", 1.0, 4.0, 2.0, 0.25, "배"),
        Knob("hold_days", "며칠 들고 있을까", 1, 10, 3, 1, "일"),
        Knob("slots", "동시에 몇 개까지 살까", 1, 8, 4, 1, "개"),
        Knob("slippage", "원하는 값에 못 사는 손해 (한 번)", 0.0, 0.3, 0.1, 0.01, "%", 0.01),
    ],
    3: [
        Knob("top_n", "몇 종목을 고를까", 5, 50, 20, 5, "개"),
        Knob("vol_window", "흔들림을 몇 개월로 잴까", 3, 24, 12, 1, "개월", 21),
        Knob("min_value_eok", "하루 거래대금이 최소 얼마인 종목만", 1, 50, 10, 1, "억 원"),
        Knob("sell_tax", "팔 때 세금", 0.0, 0.3, 0.25, 0.05, "%", 0.01),
    ],
    4: [
        Knob("round_trip", "한 번 사고팔 때 드는 비용 (전체)", 0.0, 0.1, 0.05, 0.005, "%", 0.01),
        Knob("fill_margin", "낮에 본전에 팔렸다고 볼 여유", 0.0, 0.1, 0.02, 0.01, "%", 0.01),
    ],
}
MODE_LABELS = {"wait": "손해면 하루 더 기다리기 (기록된 규칙)", "pure": "아침에 무조건 팔기"}


def knob_widget(n: int, knob: Knob) -> float:
    is_int = float(knob.step).is_integer()
    cast = int if is_int else float
    decimals = 0 if is_int else (3 if knob.step < 0.01 else 2)
    return st.slider(
        f"{knob.label} ({knob.unit})" if knob.unit else knob.label,
        min_value=cast(knob.low), max_value=cast(knob.high), value=cast(knob.default),
        step=cast(knob.step), key=f"knob-{n}-{knob.key}", help=knob.help or None,
        format=f"%.{decimals}f" if decimals else None,
    )


def read_form(n: int) -> dict | None:
    with st.form(f"explore-form-{n}", border=True):
        cols = st.columns(2)
        shown = {}
        for i, knob in enumerate(KNOBS[n]):
            with cols[i % 2]:
                shown[knob.key] = knob_widget(n, knob)
        if n == 4:
            shown["mode"] = st.radio("손해 날 때", list(MODE_LABELS), format_func=MODE_LABELS.get,
                                     horizontal=True, key="knob-4-mode")
        submitted = st.form_submit_button("이 숫자로 계산하기", type="primary", icon=":material/play_arrow:")
    if not submitted:
        return None
    by_key = {k.key: k for k in KNOBS[n]}
    return {key: (by_key[key].to_arg(v) if key in by_key else v) for key, v in shown.items()}


def reset_button(n: int) -> None:
    if st.button("기록된 값으로 되돌리기", icon=":material/restart_alt:", key=f"reset-{n}"):
        for k in KNOBS[n]:
            st.session_state.pop(f"knob-{n}-{k.key}", None)
        st.session_state.pop("knob-4-mode", None)
        st.session_state.pop(f"explore-args-{n}", None)
        st.rerun()


def compare_runs(n: int, args: dict) -> None:
    run = EXPERIMENTS[n][1]
    with st.spinner(SLOW.get(n, "계산하는 중…")):
        tuned = run(**args) if n != 1 else run("practice", **args)
        recorded = run() if n != 1 else run("practice")
    t_curve = next(iter(tuned["curves"].values()))
    r_curve = next(iter(recorded["curves"].values()))
    kodex = {k: v for k, v in recorded["curves"].items() if k.startswith("KODEX 200")}
    curves = {"바꾼 숫자": t_curve, "기록된 규칙": r_curve, **kodex}
    growth_chart(curves)
    show_metrics(curves)
    st.markdown("**바꾼 숫자로 본 세부 내용**")
    show_facts(tuned["facts"])


def render() -> None:
    st.info(
        f"여기서 바꾼 숫자는 **어디에도 기록되지 않아요.** 연습 기간({PRACTICE[0][:7]} ~ {PRACTICE[1][:7]})만 볼 수 있습니다. "
        "결과를 보고 규칙을 바꾸고 싶어졌다면, 그건 새로운 시도로 docs/experiments.md 에 처음부터 다시 적어야 해요.",
        icon=":material/science:",
    )
    n = pick_experiment("explore-exp")
    args = read_form(n)
    if args is not None:
        st.session_state[f"explore-args-{n}"] = args
    reset_button(n)
    saved = st.session_state.get(f"explore-args-{n}")
    if saved is None:
        st.caption("숫자를 고른 뒤 **이 숫자로 계산하기**를 누르면 기록된 규칙과 나란히 비교해 보여 줍니다.")
        return
    compare_runs(n, saved)
