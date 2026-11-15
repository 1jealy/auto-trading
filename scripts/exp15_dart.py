"""실험 #15: DART 공시 스윙 (A 잠정실적 · B 공급계약 · C 주식 소각). 규칙은 docs/experiments.md 에 미리 정해 둠.

먼저 scripts/dart_fetch.py docs 로 목록·문서를 받아 둘 것.

    python scripts/exp15_dart.py check        # 문서 읽기 점검: 실패 수 + 무작위 30건 (수익률 안 봄)
    python scripts/exp15_dart.py practice     # 연습 기간
    python scripts/exp15_dart.py validation   # 검증 기간 — 연습 결과 기록 후 딱 한 번
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from autotrade.config import stock_sell_tax  # noqa: E402
from autotrade.dart import DART_CACHE, PARSERS, DartClient, classify, table_rows  # noqa: E402
from autotrade.data import load_prices  # noqa: E402
from autotrade.event_swing import SwingRules, judge, simulate  # noqa: E402
from autotrade.universe import FUND_LIKE  # noqa: E402

PERIODS = {"practice": ("2016-01-01", "2021-12-31"), "validation": ("2022-01-01", "2026-10-31")}
PRICE_START, PRICE_END = "2015-10-01", "2026-11-14"  # 값은 고정(캐시 파일 이름). 연습 계산은 2022-01 이후 가격을 거의 안 씀
BENCH = "069500"  # KODEX 200
RULES = SwingRules(hold=20, slot=0.05, max_positions=20, capital=10_000_000, min_value=500_000_000,
                   value_window=20, commission=0.00015, slippage=0.002)
NAMES = {"A": "A 잠정실적(+30%)", "B": "B 공급계약(매출 20%)", "C": "C 주식 소각(1%)"}
OUT = ROOT / "results" / "exp15"


# ── 공시 목록 ──
def disclosures() -> pd.DataFrame:
    """A·B·C 처음 공시 (코스피·코스닥, 2016-01 ~ 2026-10)."""
    from dart_fetch import all_lists

    lst = all_lists()
    lst["kind"] = lst["report_nm"].map(classify)
    d = lst[lst["kind"].notna() & (lst["stock_code"].str.strip() != "")].copy()
    d["market"] = d["rm"].str.extract(r"(유|코)")[0]
    d = d[d["market"].notna()]  # 코넥스(넥) 등 제외
    d["date"] = pd.to_datetime(d["rcept_dt"])
    d = d[(d["date"] >= "2016-01-01") & (d["date"] <= "2026-10-31")]
    d = d.rename(columns={"stock_code": "code"}).drop_duplicates("rcept_no")
    return d.sort_values("rcept_no").reset_index(drop=True)


ADMIN_DAYS = 365  # 관리종목 대신: '지정 사유 발생' 공시일부터 1년 (2026-11-15 사용자 결정)
ADMIN_TITLE = re.compile(r"^(내부결산시점관리종목지정|주권매매거래정지(기간변경)?\(관리종목지정사유발생\))")


def admin_periods() -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """관리종목으로 볼 기간. DART 에는 거래소의 '관리종목 지정/해제' 기록이 없어서,
    '관리종목 지정 사유 발생' 공시(회사 I001 + 거래소 거래정지 I003)일부터 ADMIN_DAYS 일 동안으로 대신한다.
    """
    from dart_fetch import quarters

    frames = [DartClient().list_quarter("I003", y, q, workers=4) for y, q in quarters(start_year=2014)]
    frames += [DartClient().list_quarter("I001", y, q, workers=4) for y, q in quarters()]
    m = pd.concat(frames, ignore_index=True)
    m = m[m["stock_code"].str.strip() != ""]
    name = m["report_nm"].str.replace(r"\s", "", regex=True)
    m = m[name.str.match(ADMIN_TITLE)]
    out: dict[str, list] = {}
    for code, g in m.groupby("stock_code"):
        out[code] = [(d, d + pd.Timedelta(days=ADMIN_DAYS)) for d in pd.to_datetime(g["rcept_dt"])]
    return out


def is_admin(spans, code: str, date: pd.Timestamp) -> bool:
    return any(a <= date < b for a, b in spans.get(code, []))


# ── 문서 읽기 ──
def read_all(d: pd.DataFrame) -> pd.DataFrame:
    """문서를 읽어 ok/signal/values/reason 열을 붙인다. 문서가 아직 없으면 ok=None."""
    c = DartClient()
    rows = []
    for r in d.itertuples():
        f = DART_CACHE / "docs" / f"{r.rcept_no}.html"
        if not f.exists():
            rows.append({"ok": None, "na": False, "signal": False, "values": {}, "reason": "문서 안 받음"})
            continue
        p = PARSERS[r.kind](c.document(r.rcept_no))
        rows.append({"ok": p.ok, "na": p.na, "signal": p.signal, "values": p.values, "reason": p.reason})
    return pd.concat([d.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def pick_A(d: pd.DataFrame) -> pd.DataFrame:
    """A: 같은 회사가 같은 날 연결·별도 실적을 둘 다 내면 연결 기준만 쓴다(처음 공시 1건으로 봄)."""
    a = d[d["kind"] == "A"].copy()
    a["consol"] = a["report_nm"].str.contains("연결")
    a = a.sort_values(["code", "date", "consol", "rcept_no"], ascending=[True, True, False, True])
    a = a.drop_duplicates(["code", "date"], keep="first")
    return pd.concat([a.drop(columns="consol"), d[d["kind"] != "A"]]).sort_values("rcept_no")


def excluded(d: pd.DataFrame, spans) -> pd.Series:
    """빼는 종목: 우선주, 스팩·리츠 등, 신호 날 관리종목. (거래대금은 계산 단계에서 거름)"""
    pref = ~d["code"].str.fullmatch(r"\d{5}0")
    fund = d["corp_name"].str.contains(FUND_LIKE)
    admin = pd.Series([is_admin(spans, c, t) for c, t in zip(d["code"], d["date"])], index=d.index)
    return pd.DataFrame({"우선주": pref, "스팩·리츠": fund, "관리종목": admin})


def load(read_docs: bool = True):
    d = pick_A(disclosures())
    spans = admin_periods()
    ex = excluded(d, spans)
    d["excluded"] = ex.any(axis=1)
    d["why_ex"] = ex.apply(lambda r: ",".join(k for k, v in r.items() if v), axis=1)
    if read_docs:
        d = read_all(d)
    return d


# ── 점검: 읽기 실패 수 + 무작위 30건 ──
def check() -> None:
    d = load()
    d["year"] = d["date"].dt.year
    print("공시 수 (빼는 종목 포함):")
    print(pd.crosstab(d["year"], d["kind"]))
    print("\n빼는 종목 이유별:", d.loc[d["excluded"], "why_ex"].value_counts().to_dict())
    u = d[~d["excluded"]]
    print("\n문서 읽기 결과 (빼는 종목 제외):")
    st = u["ok"].map({True: "읽음", False: "실패", None: "문서 없음"}).where(~u["na"].astype(bool), "해당 없음")
    print(pd.crosstab([u["kind"]], st, margins=True))
    print("\n실패 이유:")
    print(u[u["ok"] == False].groupby(["kind", "na", "reason"]).size())  # noqa: E712
    print("\n신호 수(사는 조건 만족, 거래대금 거르기 전):")
    print(pd.crosstab(u["year"], u["kind"], values=u["signal"], aggfunc="sum"))

    sample = u[u["ok"].notna()].groupby("kind", group_keys=False).apply(lambda g: g.sample(10, random_state=15))
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in sample.itertuples():
        doc = DartClient().document(r.rcept_no)
        rows = table_rows(doc)
        want = {"A": r"^영업이익|당기실적|전년동기실적", "B": r"매출액|계약금액", "C": r"소각|발행주식|보통주"}[r.kind]
        raw = [" | ".join(x) for x in rows if re.search(want, " ".join(x[:2]))][:6]
        lines.append({
            "kind": r.kind, "rcept_no": r.rcept_no, "회사": r.corp_name, "날짜": str(r.date.date()),
            "제목": r.report_nm, "읽은 값": r.values, "신호": bool(r.signal), "읽음": bool(r.ok), "이유": r.reason,
            "원문 표": raw, "원문": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.rcept_no}",
        })
    (OUT / "check30.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for x in lines:
        print(f"\n[{x['kind']}] {x['회사']} {x['날짜']} {x['제목']}  → 읽은 값 {x['읽은 값']} 신호={x['신호']} {x['이유']}")
        for t in x["원문 표"]:
            print("     ", t[:160])


# ── 계산 ──
def run(period: str) -> None:
    start, end = PERIODS[period]
    d = load()
    sig = d[~d["excluded"] & d["signal"].astype(bool) & (d["date"] >= start) & (d["date"] <= end)]
    codes = sorted(set(sig["code"])) + [BENCH]
    prices = load_prices(codes, PRICE_START, PRICE_END, with_volume=True, drop_no_trade=True, workers=8,
                         skip_errors=True)
    if period == "practice":  # 연습 계산은 2022-03 이후 가격을 보지 않는다(마지막 신호 + 20거래일이면 충분)
        cut = pd.Timestamp("2022-03-31")
        prices = type(prices)(prices.open.loc[:cut], prices.close.loc[:cut], prices.volume.loc[:cut])
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for kind in "ABC":
        s = sig[sig["kind"] == kind][["code", "date", "rcept_no"]]
        res = simulate(s, prices, BENCH, RULES, stock_sell_tax)
        j = judge(res, prices.close[BENCH], start)
        j["빠진 신호"] = res.counts
        summary[kind] = j
        res.trades.to_csv(OUT / f"{period}_{kind}_trades.csv", index=False)
        res.equity.to_csv(OUT / f"{period}_{kind}_equity.csv")
        print(f"\n== {NAMES[kind]} ({period} {start}~{end}) ==")
        for k, v in j.items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    fails = d[~d["excluded"] & (d["date"] >= start) & (d["date"] <= end)]
    summary["읽기"] = {k: {"전체": int((g["ok"].notna()).sum()), "해당없음": int(g["na"].astype(bool).sum()),
                         "실패": int(((g["ok"] == False) & ~g["na"].astype(bool)).sum())}  # noqa: E712
                     for k, g in fails.groupby("kind")}
    (OUT / f"{period}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                               encoding="utf-8")
    print("\n읽기:", summary["읽기"])


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "check"
    check() if arg == "check" else run(arg)
