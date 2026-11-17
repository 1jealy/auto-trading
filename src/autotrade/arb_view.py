"""차익 모의 체결(시험 가동) 결과 화면 만들기. 기록(arb.db)을 읽기만 한다."""
from __future__ import annotations

import html
import sqlite3
import time
from pathlib import Path

from autotrade.arb import ARB_DB, sim_report

SKIP = 300  # 처음 5분은 '원래 벌어져 있던 차이'라 따로 본다
NAMES = {"S1": "S1 확실한 것만 (0.50%↑)", "S2": "S2 (0.20%↑)", "S3": "S3 (0.05%↑)", "S4": "S4 본전만 넘으면 (0%↑)"}
CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1b19;--ink2:#6b6862;--hair:#e4e1d9;--up:#d6336c;--down:#1c7ed6}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe7;--ink2:#a09d96;--hair:#33322f;--up:#ff6b9a;--down:#6ab0ff}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}
main{max-width:900px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0}h2{font-size:16px;margin:18px 0 6px}
p{margin:4px 0;color:var(--ink2);font-size:13px}.card{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:12px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px;white-space:nowrap}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--hair)}th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:600;font-size:12px}.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--ink2)}
"""


def rows_since(db_path: Path):
    if not db_path.exists():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute("SELECT * FROM sim_trades ORDER BY t")]
    finally:
        db.close()


def table(rows, seconds) -> str:
    if not rows or seconds <= 0:
        return '<p class="muted">아직 기록이 없어요</p>'
    out = ["<table><tr><th>후보</th><th>수수료</th><th>지연</th><th>체결</th><th>못 함</th><th>1시간에</th><th>이긴 비율</th>"
           "<th>신호 때</th><th>실제 평균</th><th>합계</th><th>코인 수</th></tr>"]
    for r in sim_report(rows, seconds):
        s, fee, lat = r["key"].split("/")
        cls = "" if r["mean_net"] is None else ("up" if r["mean_net"] > 0 else "down")
        pct = lambda v: "—" if v is None else f"{v * 100:+.3f}%"  # noqa: E731
        win = "—" if r["win"] is None else f"{r['win']:.0%}"
        out.append(f"<tr><td>{html.escape(NAMES.get(s, s))}</td><td>{fee}</td><td>{lat}초</td><td>{r['filled']:,}</td><td>{r['failed']:,}</td>"
                   f"<td>{r['per_hour']:.1f}번</td><td>{win}</td><td>{pct(r['mean_signal'])}</td>"
                   f"<td class='{cls}'>{pct(r['mean_net'])}</td><td class='{cls}'>{r['total_krw']:+,.0f}원</td><td>{r['coins']}</td></tr>")
    return "".join(out) + "</table>"


def page() -> str:
    rows = rows_since(ARB_DB)
    now = time.time()
    if rows:
        t0 = min(r["t"] for r in rows)
        mins = (now - t0) / 60
        late = [r for r in rows if r["t"] > t0 + SKIP]
        head = f"시작 {time.strftime('%H:%M:%S', time.localtime(t0))} · {mins:.0f}분째 · 기록 {len(rows):,}건"
        body = (f"<h2>처음 5분을 뺀 것 — 반복되는 틈 (이 표가 진짜)</h2><div class='card'>{table(late, now - t0 - SKIP)}</div>"
                f"<h2>전체 (처음부터)</h2><p>시작할 때 이미 벌어져 있던 차이를 코인마다 한 번씩 먹은 거래가 섞여 있어요. 그런 차이는 다시 나오지 않습니다.</p>"
                f"<div class='card'>{table(rows, now - t0)}</div>")
    else:
        head, body = "아직 기록이 없어요", ""
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta http-equiv='refresh' content='1'><title>차익 모의 체결</title><style>{CSS}</style></head><body><main>"
            f"<h1>국내 거래소 차익 — 모의 체결 (시험 가동)</h1><p>{head} · 1초마다 새로 고침 · 화면 시각 {time.strftime('%H:%M:%S')}</p>"
            f"<p>50만 원씩, 신호 뒤 '지연' 시간이 지난 호가로 양쪽을 체결했다고 계산. 주문은 내지 않아요. 수수료는 가정치(일반: 업비트 0.05%·빗썸 0.25%·코인원·코빗 0.20% / 할인: 모두 0.05%).</p>"
            f"{body}</main></body></html>")
