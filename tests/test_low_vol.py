from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade import Prices
from autotrade.strategies import Eligibility, EligibleEqualWeight, LowVolatility

DAYS = 300
RULES = Eligibility(vol_window=252, min_traded_days=240, value_window=60, min_value=1e9)


def make(spec: dict[str, dict]) -> Prices:
    """spec: 종목 → {sd: 하루 흔들림, volume: 거래량, halt: (시작, 끝) 거래정지 구간}."""
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2020-01-01", periods=DAYS)
    close, volume = {}, {}
    for t, s in spec.items():
        px = 10_000 * np.exp(np.cumsum(rng.normal(0, s["sd"], DAYS)))
        vol = np.full(DAYS, float(s.get("volume", 1_000_000)))
        if "halt" in s:
            a, b = s["halt"]
            px[a:b] = np.nan  # load_prices(drop_no_trade=True) 처리 후 모양
            vol[a:b] = 0
        close[t], volume[t] = px, vol
    c = pd.DataFrame(close, index=dates)
    return Prices(c.copy(), c, pd.DataFrame(volume, index=dates))


def test_picks_least_volatile():
    p = make({"calm": {"sd": 0.005}, "mid": {"sd": 0.015}, "wild": {"sd": 0.04}})
    w = LowVolatility(top_n=2, rules=RULES).target_weights(p)
    assert set(w.index) == {"calm", "mid"}
    assert w.sum() == pytest.approx(1.0)


def test_long_halt_is_excluded_even_if_it_looks_calm():
    p = make({"halted": {"sd": 0.001, "halt": (100, 130)}, "normal": {"sd": 0.02}})
    vol = RULES.volatility(p)
    assert list(vol.index) == ["normal"]


def test_not_traded_today_is_excluded():
    p = make({"a": {"sd": 0.01, "halt": (DAYS - 1, DAYS)}, "b": {"sd": 0.02}})
    assert list(RULES.volatility(p).index) == ["b"]


def test_illiquid_is_excluded():
    # 가격 약 1만 원 × 거래량 1만 주 = 1억 원 < 10억 원
    p = make({"thin": {"sd": 0.005, "volume": 10_000}, "thick": {"sd": 0.02}})
    assert list(RULES.volatility(p).index) == ["thick"]


def test_return_across_short_halt_counts_on_reopen_day():
    p = make({"a": {"sd": 0.01, "halt": (200, 203)}, "b": {"sd": 0.01}})
    close = p.close.copy()
    close.iloc[203, 0] = close.iloc[199, 0] * 1.5  # 거래 재개일에 50% 급등
    jumped = Prices(close.copy(), close, p.volume)
    assert RULES.volatility(jumped)["a"] > RULES.volatility(p)["a"] * 2


def test_equal_weight_holds_all_eligible():
    p = make({"a": {"sd": 0.01}, "b": {"sd": 0.02}, "c": {"sd": 0.03, "volume": 10}})
    s = EligibleEqualWeight(RULES)
    w = s.target_weights(p)
    assert set(w.index) == {"a", "b"} and w.sum() == pytest.approx(1.0)
    assert list(s.counts.values()) == [2]
