"""차익 화면 숫자: 처음 5분 빼기, 막힌 코인 빼기, 10만 원 환산, 가장 크게 잃은 1번, 30분 전에는 '이대로 하루' 없음."""

from autotrade.arb_view import SEED, TRADE, compute, page


def row(key, coin, t, net, buy="bithumb", sell="upbit"):
    return {"key": key, "coin": coin, "buy_ex": buy, "sell_ex": sell, "t": t, "net_signal": (net or 0) + 0.002, "net": net, "why": ""}


def test_compute_skips_first_minutes_and_blocked_coins():
    t0 = 1_000_000.0
    rows = [row("S1/일반/0.1", "AAA", t0, 0.01),                 # 처음 5분: 진짜 표에서 빠짐
            row("S1/일반/0.1", "BBB", t0 + 400, 0.006),
            row("S1/일반/0.1", "CCC", t0 + 500, -0.10),          # 크게 잃은 1번
            row("S1/일반/0.1", "LIT", t0 + 600, 0.02),           # 빗썸 입출금 막힘 → 뺌
            row("S1/일반/0.1", "DDD", t0 + 700, None)]           # 체결 못 함
    d = compute(rows, t0 + 1000, {"bithumb": {"LIT"}})
    assert d["excluded"] == ["LIT"]
    s1 = next(r for r in d["late"] if r["key"] == "S1/일반/0.1")
    assert s1["filled"] == 2 and s1["failed"] == 1
    assert s1["balance"] == SEED + (0.006 - 0.10) * TRADE
    assert s1["worst"] == -0.10 and abs(s1["won_wo_worst"] - 0.006 * TRADE) < 1e-6
    assert "day_won" not in s1  # 30분 전에는 '이대로 하루'를 내지 않는다
    assert s1["series"][-1][1] == round((0.006 - 0.10) * TRADE)
    assert [x["coin"] for x in d["worst"]][0] == "CCC"


def test_projection_after_30_minutes_and_empty():
    t0 = 0.0
    rows = [row("S2/할인/0.5", "ZZZ", t0, None)]  # 시작 시각(처음 기록)
    rows += [row("S2/할인/0.5", "AAA", t0 + 400 + i * 60, 0.001) for i in range(30)]
    d = compute(rows, t0 + 300 + 3600, {})
    s = d["late"][0]
    assert abs(s["day_won"] - 30 * 0.001 * TRADE * 24) < 1e-6
    assert compute([], 0.0, {})["empty"] is True
    assert "/api/arb" in page() and "http-equiv='refresh'" not in page()
