"""DART 오픈 API 시세 외 자료(공시) 받기 — #15 DART 공시 스윙용.

- 키는 환경 변수 DART_API_KEY 에서만 읽는다. 키를 출력·저장하지 않는다.
- 받은 목록·문서는 data/cache/dart/ (Git 제외)에 저장해 다시 받지 않는다.
- 주문 기능 없음. 공시 목록(list.json)과 공시 문서(document.xml)만 읽는다.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autotrade.data import DEFAULT_CACHE_DIR

API = "https://opendart.fss.or.kr/api"
DART_CACHE = DEFAULT_CACHE_DIR / "dart"


class DartError(RuntimeError):
    pass


class DartLimitError(DartError):
    """하루 호출 한도 초과(상태 020). 다음 날 이어 받으면 된다."""


# ---------------------------------------------------------------- 공시 종류 고르기

_A = re.compile(r"^(연결재무제표기준)?영업\(잠정\)실적\(공정공시\)(\(\d{4}\.\s*\d분기\))?$")
_B = re.compile(r"^단일판매ㆍ공급계약체결(\(자율공시\))?$")
_C = re.compile(r"^(주식|자기주식)소각결정(\(자율공시\))?$")


def classify(report_nm: str) -> str | None:
    """공시 제목 → "A"(잠정실적), "B"(공급계약), "C"(주식 소각), 해당 없으면 None.

    정정·첨부 등 앞에 [ ]가 붙은 공시는 처음 공시가 아니므로 None.
    자회사 공시, 월별 실적, 계약 해지는 넣지 않는다.
    """
    name = re.sub(r"\s+", "", report_nm)
    if name.startswith("["):
        return None
    if _A.match(name):
        return "A"
    if _B.match(name):
        return "B"
    if _C.match(name):
        return "C"
    return None


# ---------------------------------------------------------------- 내려받기


@dataclass
class DartClient:
    cache_dir: Path = DART_CACHE
    min_interval: float = 0.0  # 호출 사이 최소 간격(초). 응답 자체가 2초 넘게 걸림
    _last: float = 0.0

    def _key(self) -> str:
        key = os.environ.get("DART_API_KEY", "").strip()
        if not key:
            raise DartError("환경 변수 DART_API_KEY 가 없습니다")
        return key

    def _get(self, endpoint: str, params: dict) -> bytes:
        import requests

        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = requests.get(f"{API}/{endpoint}", params={"crtfc_key": self._key(), **params}, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                time.sleep(2 ** attempt)
                continue
            return r.content
        raise DartError(f"{endpoint} 받기 실패 (5번 시도)")

    # 목록 -------------------------------------------------------------
    def list_quarter(self, detail_ty: str, year: int, q: int, workers: int = 1) -> pd.DataFrame:
        """한 분기 공시 목록 (pblntf_detail_ty 기준, 정정 포함 전부). 지난 분기는 캐시.

        workers: 2쪽부터 동시에 받을 개수 (한 번 호출에 2초 넘게 걸림).
        """
        start = pd.Timestamp(year=year, month=3 * q - 2, day=1)
        end = start + pd.offsets.QuarterEnd(0)
        f = self.cache_dir / "list" / f"{detail_ty}_{year}Q{q}.json"
        if f.exists():
            return pd.DataFrame(json.loads(f.read_text(encoding="utf-8")))

        def page(no: int) -> dict:
            j = json.loads(
                self._get(
                    "list.json",
                    {
                        "bgn_de": start.strftime("%Y%m%d"),
                        "end_de": end.strftime("%Y%m%d"),
                        "pblntf_detail_ty": detail_ty,
                        "page_count": 100,
                        "page_no": no,
                    },
                )
            )
            status = j.get("status")
            if status == "020":
                raise DartLimitError("DART 하루 호출 한도 초과")
            if status not in ("000", "013"):  # 013 = 자료 없음
                raise DartError(f"list.json 상태 {status}: {j.get('message')}")
            return j

        first = page(1)
        rows: list[dict] = list(first.get("list", []))
        total = int(first.get("total_page", 1) or 1)
        if total > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max(1, workers)) as pool:
                for j in pool.map(page, range(2, total + 1)):
                    rows += j.get("list", [])
        expected = int(first.get("total_count", 0) or 0)
        if len({r["rcept_no"] for r in rows}) < expected:
            raise DartError(f"{detail_ty} {year}Q{q}: {expected}건 중 {len(rows)}건만 받음")
        if end < pd.Timestamp.today().normalize():  # 끝난 분기만 저장
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return pd.DataFrame(rows)

    # 문서 -------------------------------------------------------------
    def document(self, rcept_no: str) -> str:
        """공시 문서 원문(HTML/XML 글자). 캐시에 있으면 그대로."""
        f = self.cache_dir / "docs" / f"{rcept_no}.html"
        if f.exists():
            return f.read_text(encoding="utf-8")
        raw = self._get("document.xml", {"rcept_no": rcept_no})
        if not raw.startswith(b"PK"):
            try:
                j = json.loads(raw)
            except ValueError as e:
                raise DartError(f"{rcept_no}: 알 수 없는 응답") from e
            if j.get("status") == "020":
                raise DartLimitError("DART 하루 호출 한도 초과")
            raise DartError(f"{rcept_no}: 상태 {j.get('status')} {j.get('message')}")
        z = zipfile.ZipFile(io.BytesIO(raw))
        parts = [decode(z.read(n)) for n in sorted(z.namelist())]
        text = "\n".join(parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return text


def decode(b: bytes) -> str:
    """문서 글자 인코딩: 거래소 공시는 대개 EUC-KR, 나머지는 UTF-8."""
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("cp949", errors="replace")


# ---------------------------------------------------------------- 문서 안의 표 읽기


def table_rows(doc: str) -> list[list[str]]:
    """문서의 모든 표 줄을 [칸 글자, ...] 목록으로. 칸 안의 공백은 하나로 줄인다."""
    import html as _html

    doc = re.sub(r"(?is)<style.*?</style>", "", doc)
    out = []
    for tr in re.findall(r"(?is)<tr\b.*?</tr>", doc):
        cells = re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", tr)
        cells = [_html.unescape(re.sub(r"<[^>]+>", " ", c)) for c in cells]
        cells = [re.sub(r"\s+", " ", c).replace("\xa0", " ").strip() for c in cells]
        if cells:
            out.append(cells)
    return out


def _key(s: str) -> str:
    return re.sub(r"[\s\-·ㆍ.:()]", "", s)


def number(s: str) -> float | None:
    """'1,234' → 1234, '(1,234)' / '△1,234' / '-1,234' → -1234, '-' 나 빈칸 → None."""
    t = s.replace(",", "").replace(" ", "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t[:1] in ("△", "▲", "-", "−", "Δ"):
        neg, t = True, t[1:]
    t = t.rstrip("%")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


@dataclass(frozen=True)
class Parsed:
    ok: bool  # 숫자를 읽었는가
    signal: bool  # 사는 조건을 만족하는가
    values: dict  # 읽은 숫자 (대조용)
    reason: str = ""  # 읽기 실패 이유


def parse_A(doc: str) -> Parsed:
    """잠정실적: 영업이익 '당해실적' 줄의 당기 값과 전년동기 값.

    사는 조건: 당기 > 0 이고 (전년동기 <= 0 이거나 당기 >= 전년동기 x 1.3).
    """
    rows = table_rows(doc)
    header = None
    for r in rows:
        keys = [_key(c) for c in r]
        if any(k.startswith("당기실적") for k in keys) and any(k.startswith("전년동기실적") for k in keys):
            header = keys
            break
    if header is None:
        return Parsed(False, False, {}, "머리줄(당기실적/전년동기실적) 없음")
    cols = [k for k in header if k not in ("구분", "")]
    i_cur = next(i for i, k in enumerate(cols) if k.startswith("당기실적"))
    i_yoy = next(i for i, k in enumerate(cols) if k.startswith("전년동기실적"))
    for r in rows:
        keys = [_key(c) for c in r]
        if not keys or keys[0] != "영업이익":
            continue
        rest = r[1:]
        if rest and _key(rest[0]) in ("당해실적", "당기", "분기", "당해"):
            rest = rest[1:]
        if len(rest) <= max(i_cur, i_yoy):
            return Parsed(False, False, {}, "영업이익 줄 칸 수 부족")
        cur, yoy = number(rest[i_cur]), number(rest[i_yoy])
        vals = {"영업이익_당기": cur, "영업이익_전년동기": yoy}
        if cur is None:
            return Parsed(False, False, vals, "당기 영업이익 숫자 아님")
        if yoy is None:
            return Parsed(False, False, vals, "전년동기 영업이익 없음")
        sig = cur > 0 and (yoy <= 0 or cur >= 1.3 * yoy)
        return Parsed(True, sig, vals)
    return Parsed(False, False, {}, "영업이익 줄 없음")


def _find(rows: list[list[str]], *labels: str) -> str | None:
    """표에서 이름 칸이 labels 중 하나인 칸 바로 다음 칸 글자."""
    want = {_key(x) for x in labels}
    for r in rows:
        for j, c in enumerate(r[:-1]):
            if _key(c) in want:
                return r[j + 1]
    return None


def parse_B(doc: str) -> Parsed:
    """공급계약: '매출액 대비(%)'. 없으면 계약금액 총액 / 최근 매출액으로 계산.

    사는 조건: 매출액 대비 >= 20%.
    """
    rows = table_rows(doc)
    pct = _find(rows, "매출액대비(%)", "매출액대비", "매출액대비비율(%)")
    amount = _find(rows, "계약금액총액(원)", "계약금액(원)", "계약금액총액")
    sales = _find(rows, "최근매출액(원)", "최근 매출액(원)", "최근매출액")
    p = number(pct) if pct is not None else None
    a = number(amount) if amount is not None else None
    s = number(sales) if sales is not None else None
    vals = {"매출액대비%": p, "계약금액": a, "최근매출액": s}
    if p is None and a is not None and s and s > 0:
        p = round(a / s * 100, 2)
        vals["매출액대비%(계산)"] = p
    if p is None:
        return Parsed(False, False, vals, "매출액 대비 % 없음")
    return Parsed(True, p >= 20, vals)


def parse_C(doc: str) -> Parsed:
    """주식 소각: 소각할 보통주 수 / 발행주식총수(보통주).

    사는 조건: 1% 이상.
    """
    rows = table_rows(doc)
    burn = total = None
    section = None
    for r in rows:
        k0 = _key(r[0])
        if "소각할주식" in k0:
            section = "burn"
        elif "발행주식총수" in k0:
            section = "total"
        elif re.match(r"^\d+", k0):
            section = None
        for j, c in enumerate(r[:-1]):
            if _key(c).startswith("보통주") and section:
                v = number(r[j + 1])
                if v is not None:
                    if section == "burn" and burn is None:
                        burn = v
                    elif section == "total" and total is None:
                        total = v
    vals = {"소각보통주": burn, "발행보통주": total}
    if not burn or not total:
        return Parsed(False, False, vals, "보통주 소각 수 또는 발행주식총수 없음")
    vals["비율%"] = round(burn / total * 100, 3)
    return Parsed(True, burn / total >= 0.01, vals)


PARSERS = {"A": parse_A, "B": parse_B, "C": parse_C}
