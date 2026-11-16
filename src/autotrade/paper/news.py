"""#16 LLM 뉴스 스윙 (모의 기록 전용). 규칙은 docs/experiments.md #16.

흐름 (2분마다):
  RSS 받기 → 처음 본 기사 저장 → 제목에 상장 회사 이름이 있는 기사만 → LLM(Claude Haiku, Claude Code 구독 로그인)에
  한 번에 묶어 묻기 → 답 원문·모델·시각 저장 → 사는 신호(세기 4 이상, 새 소식)를 저장.
판정 계산은 저장한 신호로 매번 다시 한다(event_swing.simulate, #15와 같은 계산). 주문 기능 없음.

기준 시각은 LLM이 답한 시각: 거래일 08:50(한국) 전이면 그날 시가, 아니면 다음 거래일 시가에 산다.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from autotrade.paper.store import DATA_DIR, KST, Store

FEEDS = {
    "yna_economy": "https://www.yna.co.kr/rss/economy.xml",
    "yna_market": "https://www.yna.co.kr/rss/market.xml",
    "hk_finance": "https://www.hankyung.com/feed/finance",
}
POLL = 120.0  # 초
MODEL = "claude-haiku-5-5"  # 등록 때 고정. 바꾸면 기록해야 함
MIN_STRENGTH = 4
NOVELTY_DAYS = 3
NOVELTY_JACCARD = 0.5
BUY_CUTOFF = (8, 50)  # 한국 시각 08:50 전 신호 → 그날 시가
HOLD = 5
START: float | None = None  # 등록하는 순간(유닉스 초). None 이면 기록하지 않음
NEWS_PATH = DATA_DIR / "news.json"
COMPANIES = Path(__file__).with_name("news_companies.csv")
UA = {"User-Agent": "Mozilla/5.0 (personal research; RSS reader)"}

# 흔한 낱말과 헷갈리는 두 글자 회사 이름 (등록 때 고정)
AMBIGUOUS = {
    # 일반 낱말·지명·인명과 겹쳐 제목에서 회사가 아닌 뜻으로 자주 쓰이는 것
    "나노", "남성", "노을", "대상", "대교", "대동", "대원", "대현", "동방", "동서", "동양", "레몬", "레이", "러셀", "배럴",
    "백산", "삼일", "삼진", "삼영", "삼기", "상보", "서남", "서산", "서원", "서한", "서연", "선진", "성우", "세중", "세방",
    "신원", "신흥", "영광", "오공", "오아", "우성", "우양", "우진", "유신", "율촌", "일승", "전방", "조흥", "조비", "진도",
    "진영", "천보", "청보", "캐리", "태양", "태성", "포톤", "피노", "핑거", "하스", "화신", "흥국", "금비", "꿈비", "노타",
    "대모", "대창", "더즌", "알트", "카스", "코디", "국전", "경농", "한탑",
    "EG", "KD", "KX", "SG", "TP", "YW", "3S", "E8", "CS",
}


# ---------------------------------------------------------------- RSS


def _text(tag: str, item: str) -> str:
    m = re.search(rf"(?s)<{tag}[^>]*>(.*?)</{tag}>", item)
    if not m:
        return ""
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
    s = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s)))
    return re.sub(r"\s+", " ", s).strip()


def parse_rss(xml: str, source: str, fetched: float) -> list[dict]:
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", xml):
        link, title = _text("link", item), _text("title", item)
        if not link or not title:
            continue
        pub = _text("pubDate", item)
        try:
            published = parsedate_to_datetime(pub).timestamp() if pub else None
        except (TypeError, ValueError):
            published = None
        out.append({"link": link, "source": source, "title": title, "summary": _text("description", item)[:400],
                    "published": published, "fetched": fetched})
    return out


def fetch_feeds(session=None) -> tuple[list[dict], dict[str, str]]:
    import requests

    s = session or requests
    items, errors = [], {}
    for name, url in FEEDS.items():
        try:
            r = s.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            items += parse_rss(r.text, name, time.time())
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {str(e)[:80]}"
    return items, errors


# ---------------------------------------------------------------- 회사 이름 거르기


def load_companies(path: Path = COMPANIES) -> dict[str, str]:
    """이름 → 종목코드 (등록 때 만든 목록, 두 글자 헷갈리는 이름 제외)."""
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["Name"]: r["Code"] for r in rows if r["Name"] not in AMBIGUOUS}


def match_companies(title: str, names: dict[str, str]) -> list[tuple[str, str]]:
    """제목에 나오는 회사 (코드, 이름). 긴 이름 우선으로 겹치지 않게. 영문 이름은 앞뒤가 영문·숫자가 아닐 때만."""
    found = []
    for name in sorted(names, key=len, reverse=True):
        for m in re.finditer(re.escape(name), title):
            a, b = m.span()
            if re.fullmatch(r"[A-Za-z0-9&]+", name):
                if (a > 0 and re.match(r"[A-Za-z0-9]", title[a - 1])) or (b < len(title) and re.match(r"[A-Za-z0-9]", title[b])):
                    continue
            if any(a < y and x < b for x, y, _ in found):
                continue
            found.append((a, b, name))
    found.sort()
    seen, out = set(), []
    for _, _, name in found:
        if names[name] not in seen:
            seen.add(names[name])
            out.append((names[name], name))
    return out


def bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s\W_]+", "", s)
    return {s[i : i + 2] for i in range(len(s) - 1)}


def similar(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


# ---------------------------------------------------------------- LLM 묻기

SYSTEM = (
    "너는 한국 주식 뉴스를 분류하는 도구다. 각 기사에 대해 아래만 판단해 JSON으로 답한다. 설명 문장은 쓰지 않는다.\n"
    "1) company_code: 기사가 '주로' 다루는 회사의 종목코드. 반드시 그 기사의 후보 목록 안에서 고르고, 주인공이 후보에 없으면 null.\n"
    "2) positive_new: 그 회사의 주가에 좋은 '새로운 사실'이면 true. 다음은 false: 증권사 목표가·투자의견·실적 전망, "
    "이미 알려진 일의 반복·후속 보도, 시황 정리·특징주 나열, 주가가 올랐다는 사실만 전하는 기사, 나쁜 소식.\n"
    "3) strength: 1~5. 5 = 회사 가치가 크게 바뀔 만한 일(매출 대비 큰 수주, 신약·제품 허가 승인, 인수합병, 큰 정부 계약), "
    "3 = 보통의 좋은 소식, 1 = 거의 영향 없음. positive_new 가 false 면 1.\n"
    "4) reason: 한국어 한 줄(40자 이내).\n"
    "기사에 없는 사실을 추측하지 않는다."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "company_code": {"type": ["string", "null"]},
                    "positive_new": {"type": "boolean"},
                    "strength": {"type": "integer", "minimum": 1, "maximum": 5},
                    "reason": {"type": "string"},
                },
                "required": ["id", "company_code", "positive_new", "strength", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def build_prompt(batch: list[dict]) -> str:
    lines = ["다음 기사들을 분류하라.", ""]
    for i, n in enumerate(batch):
        cands = ", ".join(f"{name}({code})" for code, name in n["candidates"])
        lines.append(f"[{i}] 제목: {n['title']}")
        if n.get("summary"):
            lines.append(f"    요약: {n['summary']}")
        lines.append(f"    후보: {cands}")
    return "\n".join(lines)


@dataclass
class Answer:
    ok: bool
    items: list[dict]
    raw: str
    error: str = ""
    cost: float | None = None


def claude_bin() -> str:
    """claude 실행 파일 위치. 서비스(systemd)의 PATH 에는 ~/.local/bin 이 없을 수 있어 직접 찾는다."""
    import shutil

    for c in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude")):
        if c and Path(c).exists():
            return c
    return "claude"


def ask_claude_code(prompt: str, model: str = MODEL, timeout: float = 180.0) -> Answer:
    """Claude Code(구독 로그인)로 한 번 묻는다. 도구 없음, 세션 저장 안 함, 빈 폴더에서 실행(프로젝트 설정을 안 읽게).

    ANTHROPIC_API_KEY 가 있으면 Claude Code 가 그 키(유료 API)를 먼저 쓰므로 하위 프로세스에서는 뺀다(2026-11-16 사용자 결정:
    구독 로그인). 서버에는 CLAUDE_CODE_OAUTH_TOKEN(claude setup-token)이 있어야 한다.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cwd = Path(tempfile.gettempdir()) / "autotrade-news-llm"
    cwd.mkdir(exist_ok=True)
    cmd = [claude_bin(), "-p", prompt, "--model", model, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--system-prompt", SYSTEM, "--tools", "", "--no-session-persistence"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    except FileNotFoundError:
        return Answer(False, [], "", "claude 명령이 없음(서버에 Claude Code 설치 필요)")
    except subprocess.TimeoutExpired:
        return Answer(False, [], "", "시간 초과")
    raw = p.stdout
    try:
        j = json.loads(raw)
    except ValueError:
        return Answer(False, [], raw[-2000:], f"JSON 아님 (종료 코드 {p.returncode}): {p.stderr[-200:]}")
    so = j.get("structured_output")
    if j.get("is_error") or not isinstance(so, dict) or not isinstance(so.get("items"), list):
        return Answer(False, [], raw[-4000:], str(j.get("result") or j.get("subtype") or "구조화 답 없음")[:200],
                      j.get("total_cost_usd"))
    return Answer(True, so["items"], raw[-8000:], "", j.get("total_cost_usd"))


# ---------------------------------------------------------------- 한 바퀴


def decide_time_kst(t: float) -> tuple[str, bool]:
    """(한국 날짜, 08:50 전인가)."""
    g = time.gmtime(t + KST)
    return time.strftime("%Y-%m-%d", g), (g.tm_hour, g.tm_min) < BUY_CUTOFF


class NewsWatcher:
    def __init__(self, store: Store, asker=ask_claude_code, names: dict[str, str] | None = None,
                 start: float | None = START, model: str = MODEL) -> None:
        self.store, self.asker, self.start, self.model = store, asker, start, model
        self.names = names if names is not None else load_companies()
        self.last_errors: dict[str, str] = {}

    def step(self, items: list[dict] | None = None, now: float | None = None) -> dict:
        """RSS 한 번 처리. items 를 주면 받기를 건너뜀(테스트용). 반환: 이번에 한 일 요약."""
        if self.start is None:
            return {"skipped": "아직 등록 전(START 없음)"}
        if items is None:
            items, self.last_errors = fetch_feeds()
        now = now or time.time()
        new = self.store.news_add(items)
        todo = []
        for n in new:
            cands = match_companies(n["title"], self.names)
            status = "before_start" if now < self.start else ("candidate" if cands else "no_company")
            self.store.news_mark(n["link"], status, cands)
            if status == "candidate":
                n["candidates"] = cands
                todo.append(n)
        done = {"new": len(new), "asked": 0, "signals": 0, "failed": 0}
        for i in range(0, len(todo), 25):  # 한 번에 최대 25건 묶어서
            batch = todo[i : i + 25]
            t_ask = time.time()
            ans = self.asker(build_prompt(batch), self.model)
            t_ans = time.time()
            ask_id = self.store.news_ask(t_ask, t_ans, self.model, len(batch), ans.ok, ans.error, ans.raw, ans.cost)
            if not ans.ok:
                for n in batch:
                    self.store.news_mark(n["link"], "failed", n["candidates"])
                done["failed"] += len(batch)
                continue
            by_id = {a.get("id"): a for a in ans.items if isinstance(a, dict)}
            for k, n in enumerate(batch):
                a = by_id.get(k)
                if a is None:
                    self.store.news_mark(n["link"], "failed", n["candidates"])
                    done["failed"] += 1
                    continue
                code = a.get("company_code")
                valid = {c for c, _ in n["candidates"]}
                why = ""
                if code not in valid:
                    why = "후보 밖 회사" if code else "주인공 회사 없음"
                    code = code if code in valid else None
                elif not a.get("positive_new"):
                    why = "새 호재 아님"
                elif int(a.get("strength") or 0) < MIN_STRENGTH:
                    why = f"세기 {a.get('strength')} < {MIN_STRENGTH}"
                else:
                    prev = self.store.news_recent_titles(code, n["fetched"] - NOVELTY_DAYS * 86400, n["link"])
                    if any(similar(n["title"], p) >= NOVELTY_JACCARD for p in prev):
                        why = "3일 안 비슷한 기사"
                sig = why == ""
                self.store.news_answer(n["link"], ask_id, code, a, sig, why, t_ans)
                self.store.news_mark(n["link"], "asked", n["candidates"])
                done["asked"] += 1
                done["signals"] += int(sig)
        return done


# ---------------------------------------------------------------- 판정 계산


def signals_for_sim(rows: list[dict]):
    """저장한 신호 → event_swing 입력. '08:50 전' 신호는 접수일을 하루 앞으로 돌려 '그날 시가'에 사게 한다
    (simulate 는 날짜 '다음' 거래일에 산다)."""
    import pandas as pd

    out = []
    for r in rows:
        day, early = decide_time_kst(r["t_answer"])
        d = pd.Timestamp(day) - pd.Timedelta(days=1 if early else 0)
        out.append({"code": r["code"], "date": d, "rcept_no": r["link"]})
    return pd.DataFrame(out, columns=["code", "date", "rcept_no"])


def compute_summary(store: Store, now: float | None = None) -> dict:
    """화면용 요약(news.json). 신호가 있으면 하루 가격으로 다시 계산한다."""
    now = now or time.time()
    out = {"t": now, "start": START, "model": MODEL, "counts": store.news_counts(), "recent": store.news_recent(30),
           "asks": store.news_ask_stats(now - 86400)}
    sig = store.news_signals()
    out["n_signals"] = len(sig)
    if not sig:
        return out
    import pandas as pd

    from autotrade.config import stock_sell_tax
    from autotrade.data import load_prices
    from autotrade.event_swing import SwingRules, day_t, simulate

    s = signals_for_sim(sig)
    first = s["date"].min() - pd.Timedelta(days=45)
    p = load_prices(sorted(set(s["code"])) + ["069500"], first.strftime("%Y-%m-%d"), None, with_volume=True,
                    drop_no_trade=True, workers=4, skip_errors=True)
    res = simulate(s, p, "069500", SwingRules(hold=HOLD), stock_sell_tax)
    t = res.trades
    tt, ndays = day_t(t) if len(t) > 1 else (None, len(t))
    out["sim"] = {
        "trades": len(t), "days": ndays, "skipped": res.counts,
        "mean_excess": float(t["excess"].mean()) if len(t) else None,
        "t": tt, "open": int((t["status"] == "미완료").sum()) if len(t) else 0,
        "rows": json.loads(t.tail(30).to_json(orient="records", date_format="iso", force_ascii=False)) if len(t) else [],
    }
    return out
