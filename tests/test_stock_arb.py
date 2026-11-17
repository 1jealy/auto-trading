"""주식 차익 관찰: 한투 호가 메시지 읽기, 교차 순수익(수수료·거래세), ETF 짝 평소 비율, 틈 기록."""
import pytest

from autotrade.paper.book import Book
from autotrade.stock_arb import TR_KRX, TR_NXT, StockGapWatch, cross_net, pair_dev, parse_kis


def msg(tr, code, ask1, bid1, extra=0):
    asks = [str(ask1 + i * 100) for i in range(10)]
    bids = [str(bid1 - i * 100) for i in range(10)]
    qty = ["1000"] * 20
    f = [code, "093000", "0"] + asks + bids + qty + ["0"] * (16 + extra)
    return f"0|{tr}|001|" + "^".join(f)


def test_parse_kis_reads_krx_and_nxt_and_skips_others():
    (tr, code, b), = parse_kis(msg(TR_KRX, "005930", 70100, 70000))
    assert tr == TR_KRX and code == "005930" and b.ask == 70100 and b.bid == 70000 and b.asks[1][0] == 70200
    assert parse_kis(msg(TR_NXT, "005930", 70100, 70000, extra=6))[0][2].bid == 70000  # NXT 는 칸이 6개 더 많다
    assert parse_kis('{"header":{"tr_id":"PINGPONG"}}') == [] and parse_kis("1|H0STCNI0|001|암호") == []


def test_cross_net_counts_fees_and_sell_tax():
    buy = Book.make([(10000, 1000)], [(9990, 1000)])
    sell = Book.make([(10060, 1000)], [(10050, 1000)])
    net = cross_net(buy, sell, notional=1_000_000, fee=0.00015, tax=0.002)
    assert net == pytest.approx(10050 * (1 - 0.00015 - 0.002) / (10000 * 1.00015) - 1, abs=1e-6)
    assert net > 0  # 0.5% 틈은 0.23% 비용을 넘는다
    small = Book.make([(10020, 1000)], [(10010, 1000)])
    assert cross_net(buy, small, tax=0.002) < 0  # 0.1% 틈은 못 넘는다


def test_pair_dev_needs_history_then_flags_deviation():
    from collections import deque

    h = deque()
    a, b = Book.make([(10010, 9e9)], [(10000, 9e9)]), Book.make([(5005, 9e9)], [(5000, 9e9)])
    for i in range(40):
        assert pair_dev(a, b, h, i * 10.0) is None or i * 10.0 >= 300
    cheap_a = Book.make([(9960, 9e9)], [(9950, 9e9)])  # a 가 0.5% 싸짐
    net, side = pair_dev(cheap_a, b, h, 401.0)
    assert side == "a" and net == pytest.approx(2.0 / (9960 / 5000) - 1 - 4 * 0.00015, abs=1e-6)  # 매도·매수 호가로 계산


def test_watch_records_gap_until_it_closes(tmp_path):
    w = StockGapWatch(tmp_path / "s.db")
    w.tax = 0.002
    w.on(TR_KRX, "005930", Book.make([(70000, 1000)], [(69900, 1000)]), 0.0)
    w.on(TR_NXT, "005930", Book.make([(70500, 1000)], [(70400, 1000)]), 0.1)  # KRX 사고 NXT 팔기 +0.57%
    assert ("교차", "삼성전자", "KRX→NXT") in w.open
    w.on(TR_NXT, "005930", Book.make([(70100, 1000)], [(70000, 1000)]), 0.6)  # 닫힘
    assert not w.open
    (kind, name, d, dur, mx, std) = w.db.execute("SELECT kind,name,dir,dur,max_net,max_std FROM gaps").fetchone()
    assert (kind, name, d) == ("교차", "삼성전자", "KRX→NXT") and dur == pytest.approx(0.5) and mx > 0.003
    assert std < mx  # 보통 수수료로는 조금 덜 남는다
