"""#16 뉴스 엔진 테스트 (네트워크·LLM 없이)."""

import json
import time

import pandas as pd

from autotrade.paper import news
from autotrade.paper.news import Answer, NewsWatcher, match_companies, parse_rss, signals_for_sim, similar
from autotrade.paper.store import KST, Store

NAMES = {"삼성전자": "005930", "삼성전기": "009150", "LG": "003550", "LG에너지솔루션": "373220", "한화": "000880",
         "알테오젠": "196170"}

RSS = """<rss><channel>
<item><title><![CDATA[알테오젠, 미국 FDA 허가 승인]]></title><link>https://x/1</link>
<description><![CDATA[<p>알테오젠이 &amp; 허가를 받았다</p>]]></description><pubDate>Mon, 16 Nov 2026 07:10:00 +0900</pubDate></item>
<item><title>오늘의 날씨</title><link>https://x/2</link><pubDate>Mon, 16 Nov 2026 07:11:00 +0900</pubDate></item>
<item><title>LG에너지솔루션 4조 수주…삼성전자도</title><link>https://x/3</link></item>
</channel></rss>"""


def test_parse_rss():
    items = parse_rss(RSS, "yna", 100.0)
    assert [i["link"] for i in items] == ["https://x/1", "https://x/2", "https://x/3"]
    assert items[0]["summary"] == "알테오젠이 & 허가를 받았다"
    assert items[0]["published"] == pd.Timestamp("2026-11-15 22:10", tz="UTC").timestamp()
    assert items[2]["published"] is None


def test_match_companies_prefers_longest_and_checks_latin_boundary():
    assert match_companies("LG에너지솔루션 4조 수주…삼성전자도", NAMES) == [("373220", "LG에너지솔루션"), ("005930", "삼성전자")]
    assert match_companies("LG 지주사 배당", NAMES) == [("003550", "LG")]
    assert match_companies("BLGX 상장", NAMES) == []  # 영문 이름은 단어 경계 필요
    assert match_companies("한화오션 수주", NAMES) == [("000880", "한화")]  # 한글은 경계 없이(LLM 이 다시 고름)


def test_ambiguous_names_excluded(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("Code,Name,Market\n001680,대상,KOSPI\n005930,삼성전자,KOSPI\n", encoding="utf-8")
    assert news.load_companies(f) == {"삼성전자": "005930"}


def test_similar():
    assert similar("알테오젠, 미국 FDA 허가 승인", "알테오젠 미국 FDA 허가 승인 획득") > 0.7
    assert similar("알테오젠, 미국 FDA 허가 승인", "삼성전자 3분기 실적") < 0.1


class FakeAsker:
    def __init__(self, answers, ok=True):
        self.answers, self.ok, self.prompts = answers, ok, []

    def __call__(self, prompt, model):
        self.prompts.append(prompt)
        return Answer(self.ok, self.answers if self.ok else [], json.dumps({"x": 1}), "" if self.ok else "한도")


def items(*rows, t=1000.0):
    return [{"link": l, "source": "yna", "title": ti, "summary": "", "published": t, "fetched": t} for l, ti in rows]


def test_step_signals_and_reasons():
    st = Store(":memory:")
    asker = FakeAsker([
        {"id": 0, "company_code": "196170", "positive_new": True, "strength": 5, "reason": "허가"},
        {"id": 1, "company_code": "373220", "positive_new": True, "strength": 3, "reason": "보통"},
        {"id": 2, "company_code": "999999", "positive_new": True, "strength": 5, "reason": "후보 밖"},
    ])
    w = NewsWatcher(st, asker, NAMES, start=0.0)
    done = w.step(items(("a", "알테오젠, 미국 FDA 허가 승인"), ("b", "LG에너지솔루션 수주"), ("c", "삼성전자 소식"),
                        ("d", "오늘의 날씨")))
    assert done == {"new": 4, "asked": 3, "signals": 1, "failed": 0}
    assert "후보: 알테오젠(196170)" in asker.prompts[0] and "날씨" not in asker.prompts[0]
    sig = st.news_signals()
    assert [s["code"] for s in sig] == ["196170"]
    why = {r["title"]: r["why_not"] for r in st.news_recent()}
    assert why["LG에너지솔루션 수주"] == "세기 3 < 4" and why["삼성전자 소식"] == "후보 밖 회사"
    assert st.news_counts() == {"asked": 3, "no_company": 1}
    # 같은 기사는 다시 묻지 않음
    assert w.step(items(("a", "알테오젠, 미국 FDA 허가 승인")))["new"] == 0


def test_novelty_blocks_similar_title_within_3_days():
    st = Store(":memory:")
    ans = [{"id": 0, "company_code": "196170", "positive_new": True, "strength": 5, "reason": "허가"}]
    w = NewsWatcher(st, FakeAsker(ans), NAMES, start=0.0)
    w.step(items(("a", "알테오젠, 미국 FDA 허가 승인"), t=1000.0))
    w.step(items(("b", "알테오젠 미국 FDA 허가 승인 획득"), t=1000.0 + 86400))
    w.step(items(("c", "알테오젠 미국 FDA 허가 승인 획득"), t=1000.0 + 5 * 86400))  # 3일 넘음 → 'a' 와 비교 안 함, 'b' 는 4일 전
    assert [s["link"] for s in st.news_signals()] == ["a", "c"]
    assert {r["link"]: r["why_not"] for r in st.news_recent()}["b"] == "3일 안 비슷한 기사"


def test_failed_llm_marks_failed_and_no_signal():
    st = Store(":memory:")
    w = NewsWatcher(st, FakeAsker([], ok=False), NAMES, start=0.0)
    done = w.step(items(("a", "알테오젠 허가")))
    assert done["failed"] == 1 and st.news_signals() == [] and st.news_counts() == {"failed": 1}
    assert st.news_ask_stats(0)["last_error"]["error"] == "한도"


def test_before_start_not_asked():
    st = Store(":memory:")
    asker = FakeAsker([])
    w = NewsWatcher(st, asker, NAMES, start=5000.0)
    w.step(items(("a", "알테오젠 허가")), now=4000.0)
    assert asker.prompts == [] and st.news_counts() == {"before_start": 1}
    assert NewsWatcher(st, asker, NAMES, start=None).step([])["skipped"]


def test_article_published_before_start_not_asked():
    """처음 켰을 때 RSS 에 남아 있던 등록 전 기사는 묻지 않는다."""
    st = Store(":memory:")
    asker = FakeAsker([{"id": 0, "company_code": "196170", "positive_new": True, "strength": 5, "reason": "x"}])
    w = NewsWatcher(st, asker, NAMES, start=5000.0)
    w.step(items(("old", "알테오젠 허가"), t=4000.0) + items(("new", "알테오젠 미국 FDA 허가 승인"), t=6000.0), now=6000.0)
    assert st.news_counts() == {"before_start": 1, "asked": 1}
    assert [s["link"] for s in st.news_signals()] == ["new"]


def kst(s):
    return pd.Timestamp(s).timestamp() - KST


def test_entry_day_rule():
    rows = [{"code": "1", "link": "a", "t_answer": kst("2026-11-16 08:49")},  # 월 08:49 → 그날(월) 시가
            {"code": "2", "link": "b", "t_answer": kst("2026-11-16 08:51")},  # 월 08:51 → 다음 거래일(화)
            {"code": "3", "link": "c", "t_answer": kst("2026-11-14 07:00")}]  # 토 07:00 → 월
    s = signals_for_sim(rows)
    cal = pd.bdate_range("2026-11-10", "2026-11-20")
    entry = [cal[cal.searchsorted(d, side="right")] for d in s["date"]]
    assert [e.strftime("%m-%d") for e in entry] == ["11-16", "11-17", "11-16"]
