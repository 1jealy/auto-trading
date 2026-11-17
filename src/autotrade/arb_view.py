"""차익 모의 체결(시험 가동) 결과 화면. 기록(arb.db)을 읽기만 한다.

- page(): 화면 뼈대(HTML). 숫자는 화면 안의 스크립트가 /api/arb 를 1초마다 받아 그 자리만 바꾼다(페이지 전체 새로 고침 없음).
- summary(): /api/arb 가 돌려주는 숫자. 새 기록이 없으면 지난 계산을 그대로 돌려준다(보는 사람이 많아도 가볍게).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from autotrade.arb import ARB_DB, REAL_CAPITAL, sim_report

SKIP = 300            # 처음 5분은 '원래 벌어져 있던 차이'라 따로 본다
SEED, TRADE = 100_000, 50_000   # 10만 원으로 시작: 원화 5만 + 코인 5만, 한 번에 5만 원어치 거래
PROJECT_AFTER = 1800  # 30분은 지나야 '이대로 가면' 숫자를 보여 준다
WALLET_TTL = 600      # 입출금 상태는 10분마다 다시 받는다
NAMES = {"S1": "S1 확실한 것만 (0.50%↑)", "S2": "S2 넉넉한 틈 (0.20%↑)", "S3": "S3 작은 틈 (0.05%↑)", "S4": "S4 본전만 넘으면 (0%↑)",
         "R1": "R1 확실한 것만 (0.50%↑)", "R2": "R2 넉넉한 틈 (0.20%↑)", "R3": "R3 작은 틈 (0.05%↑)", "R4": "R4 본전만 넘으면 (0%↑)"}
EXN = {"upbit": "업비트", "bithumb": "빗썸", "coinone": "코인원", "korbit": "코빗"}
MAIN_KEYS = ("S1/일반/0.1", "S2/일반/0.1", "S3/일반/0.1", "S4/일반/0.1",
             "R1/일반/0.1", "R2/일반/0.1", "R3/일반/0.1", "R4/일반/0.1")


# ── 입출금 막힌 코인 (빗썸·코인원 공개 자료. 업비트·코빗은 열쇠 없이는 못 봄) ──
_wallet = {"t": 0.0, "blocked": {}, "err": None, "busy": False}


def _fetch_wallet() -> None:
    import requests

    try:
        b = requests.get("https://api.bithumb.com/public/assetsstatus/ALL", timeout=10).json()["data"]
        c = requests.get("https://api.coinone.co.kr/public/v2/currencies", timeout=10).json()["currencies"]
        blocked = {
            "bithumb": sorted(k for k, v in b.items() if isinstance(v, dict) and not (v.get("withdrawal_status") and v.get("deposit_status"))),
            "coinone": sorted(x["symbol"].upper() for x in c if x.get("deposit_status") != "normal" or x.get("withdraw_status") != "normal"),
        }
        _wallet.update(t=time.time(), blocked=blocked, err=None)
    except Exception as e:  # noqa: BLE001
        _wallet.update(t=time.time(), err=f"{type(e).__name__}")
    finally:
        _wallet["busy"] = False


def wallet_blocked() -> dict[str, set[str]]:
    """막힌 코인 {거래소: {코인}}. 오래됐으면 뒤에서 새로 받는다(화면은 기다리지 않음)."""
    if time.time() - _wallet["t"] > WALLET_TTL and not _wallet["busy"]:
        _wallet["busy"] = True
        threading.Thread(target=_fetch_wallet, daemon=True).start()
    return {k: set(v) for k, v in _wallet["blocked"].items()}


def is_ok(r: dict, blocked: dict[str, set[str]]) -> bool:
    return not any(r["coin"] in blocked.get(e, ()) for e in (r["buy_ex"], r["sell_ex"]))


# ── 계산 ──
def read_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute("SELECT * FROM sim_trades ORDER BY t")]
    finally:
        db.close()


def _won(sum_net: float) -> float:
    """거래 순수익률 합 → 10만 원 시작(한 번에 5만 원)일 때 번 돈."""
    return sum_net * TRADE


def _series(rs: list[dict], t0: float, points: int = 60) -> list[list[float]]:
    """누적 번 돈(원) [분, 원]. 그림용으로 점 수를 줄인다."""
    out, cum = [], 0.0
    for r in rs:
        cum += _won(r["net"])
        out.append([round((r["t"] - t0) / 60, 2), round(cum)])
    if len(out) > points:
        step = len(out) / points
        out = [out[int(i * step)] for i in range(points - 1)] + [out[-1]]
    return out


def table_rows(rows: list[dict], seconds: float, t0: float, with_series: bool) -> list[dict]:
    hours = max(seconds / 3600, 1e-9)
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["key"], []).append(r)
    out = []
    for rep in sim_report(rows, seconds):
        key = rep["key"]
        done = [r for r in by.get(key, []) if r["net"] is not None]
        nets = sorted(r["net"] for r in done)
        dirs: dict[str, int] = {}
        for r in done:
            d = f"{r['buy_ex']}→{r['sell_ex']}"
            dirs[d] = dirs.get(d, 0) + 1
        top_dir = max(dirs.items(), key=lambda x: x[1]) if dirs else None
        won = _won(sum(nets))
        s, fee, lat = key.split("/")
        item = {
            "key": key, "name": NAMES.get(s, s), "fee": fee, "lat": lat,
            "filled": rep["filled"], "failed": rep["failed"], "win": rep["win"],
            "signal": rep["mean_signal"], "net": rep["mean_net"],
            "lost": (rep["mean_signal"] - rep["mean_net"]) if rep["mean_net"] is not None else None,
            "won": won, "balance": SEED + won, "ret": won / SEED,
            "ret_cap": won / REAL_CAPITAL if s.startswith("R") else None,
            "one_side": sum(1 for r in done if r.get("why") == "한쪽만 체결"),
            "worst": nets[0] if nets else None, "best": nets[-1] if nets else None,
            "won_wo_worst": _won(sum(nets) - nets[0]) if nets else None,
            "coins": rep["coins"],
            "top_dir": None if not top_dir else {"dir": top_dir[0], "share": top_dir[1] / len(done)},
            "per_hour": len(done) / hours,
        }
        if seconds >= PROJECT_AFTER:
            item["day_won"] = won / hours * 24
            item["day_pct"] = item["day_won"] / SEED
            item["month_pct"] = item["day_pct"] * 30
        if with_series:
            item["series"] = _series(done, t0)
        out.append(item)
    return out


def compute(rows: list[dict], now: float, blocked: dict[str, set[str]]) -> dict:
    if not rows:
        return {"now": now, "empty": True}
    t0 = min(r["t"] for r in rows)
    late = [r for r in rows if r["t"] > t0 + SKIP and is_ok(r, blocked)]
    late_all = [r for r in rows if r["t"] > t0 + SKIP]
    excluded = sorted({r["coin"] for r in late_all if not is_ok(r, blocked)})
    main = [r for r in late if r["key"] in MAIN_KEYS and r["net"] is not None]
    recent = sorted(main, key=lambda r: -r["t"])[:20]
    worst = sorted(main, key=lambda r: r["net"])[:5]
    fmt = lambda r: {"t": r["t"], "key": r["key"].split("/")[0], "coin": r["coin"],  # noqa: E731
                     "dir": f"{EXN.get(r['buy_ex'], r['buy_ex'])}→{EXN.get(r['sell_ex'], r['sell_ex'])}",
                     "signal": r["net_signal"], "net": r["net"], "won": _won(r["net"])}
    return {
        "now": now, "empty": False, "t0": t0, "last_t": max(r["t"] for r in rows),
        "minutes": (now - t0) / 60, "records": len(rows), "late_minutes": max(0.0, (now - t0 - SKIP) / 60),
        "seed": SEED, "trade": TRADE, "project_after_min": PROJECT_AFTER / 60,
        "excluded": excluded, "wallet_err": _wallet["err"], "wallet_t": _wallet["t"],
        "late": table_rows([r for r in late if r["key"][0] == "S"], now - t0 - SKIP, t0, True),
        "real": table_rows([r for r in late if r["key"][0] == "R"], now - t0 - SKIP, t0, True),
        "real_coins": _real_coins(),
        "all": table_rows(rows, now - t0, t0, False),
        "recent": [fmt(r) for r in recent], "worst": [fmt(r) for r in worst],
    }


def _real_coins() -> dict:
    try:
        d = json.loads((ARB_DB.parent / "arb_real_coins.json").read_text(encoding="utf-8"))
        return {"coins": d.get("coins", []), "move_pct": d.get("move_pct", {})}
    except (OSError, ValueError):
        return {"coins": [], "move_pct": {}}


_cache = {"sig": None, "data": None, "t": 0.0}
_lock = threading.Lock()


def summary(db_path: Path = ARB_DB) -> dict:
    """새 기록이 없고 1초가 안 지났으면 지난 계산을 다시 쓴다."""
    now = time.time()
    try:
        st = db_path.stat()
        sig = (st.st_mtime, st.st_size)
    except FileNotFoundError:
        sig = None
    with _lock:
        if _cache["data"] is not None and sig == _cache["sig"] and now - _cache["t"] < 1.0:
            return _cache["data"]
        data = compute(read_rows(db_path), now, wallet_blocked())
        _cache.update(sig=sig, data=data, t=now)
        return data


def summary_json(db_path: Path = ARB_DB) -> bytes:
    return json.dumps(summary(db_path), ensure_ascii=False).encode()


# ── 화면 ──
CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1b19;--ink2:#6b6862;--hair:#e4e1d9;--up:#d6336c;--down:#1c7ed6;--chip:#ecebe6}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe7;--ink2:#a09d96;--hair:#33322f;--up:#ff6b9a;--down:#6ab0ff;--chip:#2a2a27}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0}h2{font-size:16px;margin:22px 0 6px}
p{margin:4px 0;color:var(--ink2);font-size:13px}.card{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:12px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px;white-space:nowrap}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--hair)}th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:600;font-size:12px}.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--ink2)}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px;color:var(--ink2)}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px}.live{color:var(--up);font-weight:700}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:12px}
.tile .n{font-size:13px;color:var(--ink2)}.tile .big{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.tile .sub{font-size:12px;color:var(--ink2)}svg.spark{width:100%;height:46px;display:block;margin-top:6px}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;margin:8px 0}
"""

JS = r"""
const $=s=>document.querySelector(s);
const won=v=>(v>=0?'+':'')+Math.round(v).toLocaleString()+'원';
const krw=v=>Math.round(v).toLocaleString()+'원';
const pct=(v,d=3)=>v==null?'—':(v>=0?'+':'')+(v*100).toFixed(d)+'%';
const cls=v=>v==null?'':(v>=0?'up':'down');
const hm=t=>{const d=new Date(t*1000),z=n=>String(n).padStart(2,'0');return z(d.getHours())+':'+z(d.getMinutes())+':'+z(d.getSeconds());};
let last=null, lastOk=0, busy=false;
function spark(series){
  if(!series||series.length<2) return '';
  const xs=series.map(p=>p[0]), ys=series.map(p=>p[1]);
  const x0=Math.min(...xs), x1=Math.max(...xs)||1, y0=Math.min(0,...ys), y1=Math.max(0,...ys)||1;
  const W=200,H=46, X=x=>((x-x0)/((x1-x0)||1))*W, Y=y=>H-((y-y0)/((y1-y0)||1))*H;
  const pts=series.map(p=>X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join(' ');
  const end=ys[ys.length-1];
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="currentColor" stroke-opacity=".25" stroke-dasharray="3 3"/><polyline points="${pts}" fill="none" stroke="${end>=0?'var(--up)':'var(--down)'}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
function table(rows, proj, real){
  if(!rows.length) return '<p class="muted">아직 기록이 없어요</p>';
  let h='<table><tr><th>후보</th><th>수수료</th><th>지연</th><th>체결</th><th>못 함</th><th>이긴 비율</th><th>신호 때</th><th>실제 평균</th><th>늦어서 잃은 몫</th><th>10만 원이</th><th>수익률</th>'+(real?'<th>400만 원 기준</th><th>한쪽만 체결</th>':'')+'<th>가장 크게 잃은 1번</th><th>그 1번 빼면</th>'+(proj?'<th>이대로 하루</th><th>이대로 한 달</th>':'')+'<th>몰린 방향</th></tr>';
  let prev='';
  for(const r of rows){
    const first=r.name!==prev; prev=r.name;
    h+=`<tr${first?' style="border-top:2px solid var(--hair)"':''}><td>${first?r.name:''}</td><td>${r.fee}</td><td>${r.lat}초</td><td>${r.filled.toLocaleString()}</td><td>${r.failed.toLocaleString()}</td>`+
       `<td>${r.win==null?'—':Math.round(r.win*100)+'%'}</td><td>${pct(r.signal)}</td><td class="${cls(r.net)}">${pct(r.net)}</td><td>${pct(r.lost)}</td>`+
       `<td class="${cls(r.won)}"><b>${krw(r.balance)}</b></td><td class="${cls(r.ret)}">${pct(r.ret,2)}</td>`+(real?`<td class="${cls(r.ret_cap)}">${pct(r.ret_cap,3)}</td><td>${r.one_side}</td>`:'')+`<td class="${cls(r.worst)}">${pct(r.worst,1)}</td><td class="${cls(r.won_wo_worst)}">${r.won_wo_worst==null?'—':won(r.won_wo_worst)}</td>`+
       (proj?`<td class="${cls(r.day_won)}">${r.day_won==null?'—':won(r.day_won)}</td><td class="${cls(r.month_pct)}">${pct(r.month_pct,1)}</td>`:'')+
       `<td>${r.top_dir?r.top_dir.dir.replace('upbit','업비트').replace('bithumb','빗썸').replace('coinone','코인원').replace('korbit','코빗')+' '+Math.round(r.top_dir.share*100)+'%':'—'}</td></tr>`;
  }
  return h+'</table>';
}
function trades(list){
  if(!list.length) return '<p class="muted">아직 없어요</p>';
  let h='<table><tr><th>시각</th><th>판</th><th>코인</th><th>방향</th><th>신호 때</th><th>실제</th><th>번 돈(5만 원 거래)</th></tr>';
  for(const r of list) h+=`<tr><td>${hm(r.t)}</td><td>${r.key}</td><td>${r.coin}</td><td>${r.dir}</td><td>${pct(r.signal,2)}</td><td class="${cls(r.net)}">${pct(r.net,2)}</td><td class="${cls(r.won)}">${won(r.won)}</td></tr>`;
  return h+'</table>';
}
function render(d){
  if(d.empty){ $('#head').textContent='아직 기록이 없어요'; return; }
  $('#head').innerHTML=`<span class="chip">시작 ${hm(d.t0)}</span><span class="chip">${d.minutes.toFixed(0)}분째</span><span class="chip">기록 ${d.records.toLocaleString()}건</span><span class="chip" id="lastago"></span>`;
  last=d;
  const proj=d.late_minutes>=d.project_after_min;
  const tile=r=>`<div class="tile"><div class="n">${r.name} · 일반 수수료 · 0.1초</div><div class="big ${cls(r.won)}">${pct(r.ret,2)}</div><div class="sub">10만 원 → ${krw(r.balance)} (${won(r.won)})${r.ret_cap!=null?' · 400만 원 기준 '+pct(r.ret_cap,3):''} · ${r.filled}번 · 이긴 비율 ${r.win==null?'—':Math.round(r.win*100)+'%'}${proj&&r.day_won!=null?' · 이대로 하루 '+won(r.day_won):''}</div>${spark(r.series)}</div>`;
  const pick=(list,p)=>list.filter(r=>r.key.endsWith('/일반/0.1')&&r.key.startsWith(p));
  $('#tiles').innerHTML=pick(d.late,'S').map(tile).join('')||'<p class="muted">처음 5분이 지나면 나와요</p>';
  $('#rtiles').innerHTML=pick(d.real||[],'R').map(tile).join('')||'<p class="muted">처음 5분이 지나면 나와요</p>';
  $('#real').innerHTML=table(d.real||[], proj, true);
  const rc=d.real_coins||{coins:[],move_pct:{}};
  $('#realNote').textContent=(rc.coins.length?'코인 10개와 한 번 옮기는 비용(5만 원 거래 대비): '+rc.coins.map(c=>c+(rc.move_pct[c]!=null?' '+(rc.move_pct[c]*100).toFixed(1)+'%':'')).join(', ')+' · ':'')+'한 번에 5만 원 · 쌓이는 방향 거래마다 출금 수수료+원화 1,000원 뺌 · 한쪽만 체결되면 되판 손해 기록 · 필요한 돈 400만 원';
  $('#lateNote').textContent=`${d.late_minutes.toFixed(0)}분 동안의 거래 · 10만 원으로 시작(원화 5만 + 코인 5만, 한 번에 5만 원어치) · `+
    (d.excluded.length?`입출금 막힌 코인 뺌: ${d.excluded.join(', ')}`:'입출금 막힌 코인이 낀 거래 없음')+
    (d.wallet_err?` (입출금 상태 받기 실패: ${d.wallet_err})`:'')+
    (proj?'':` · '이대로 하루/한 달'은 ${d.project_after_min}분이 지나면 나와요`);
  $('#late').innerHTML=table(d.late, proj, false);
  $('#all').innerHTML=table(d.all, false, false);
  $('#recent').innerHTML=trades(d.recent);
  $('#worst').innerHTML=trades(d.worst);
}
function tick(){
  if(last&&!last.empty){ const s=Math.max(0,Date.now()/1000-last.last_t); const el=$('#lastago'); if(el) el.textContent='마지막 거래 '+(s<60?s.toFixed(1)+'초':Math.floor(s/60)+'분')+' 전'; }
  const age=(Date.now()-lastOk)/1000;
  $('#live').textContent=lastOk?(age<3?'● 실시간':'● 연결 끊김 '+age.toFixed(0)+'초'):'연결 중…';
  $('#live').className=age<3?'live':'down';
}
async function poll(){
  if(busy) return; busy=true;
  try{ const r=await fetch('/api/arb',{cache:'no-store'}); if(r.status===401){location.href='/login.html?next=/arb';return;}
       if(r.ok){ render(await r.json()); lastOk=Date.now(); } }
  catch(e){} finally{ busy=false; }
}
poll(); setInterval(poll, 1000); setInterval(tick, 100);
"""


def page() -> str:
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>차익 모의 체결</title><style>{CSS}</style></head><body><main>"
        "<h1>국내 거래소 차익 — 모의 체결 (시험 가동)</h1>"
        "<div class='chips'><span id='live'>연결 중…</span><span id='head'></span></div>"
        "<p>업비트·빗썸·코인원·코빗의 실제 호가로, 한 곳에서 사서 다른 곳에 바로 파는 거래를 계산만 해요. 주문은 내지 않아요. "
        "숫자는 1초마다 그 자리에서 바뀌어요(페이지 전체를 새로 고치지 않음). 수수료는 가정치(일반: 업비트 0.05%·빗썸 0.25%·코인원·코빗 0.20% / 할인: 모두 0.05%).</p>"
        "<h2>지금 수익률 — 기존판(S): 들고 있는 코인·옮기는 비용 무시</h2><div class='tiles' id='tiles'></div>"
        "<h2>지금 수익률 — 현실 계산판(R): 코인 10개만, 옮기는 비용·한쪽만 체결 손해 포함</h2><div class='tiles' id='rtiles'></div>"
        "<h2>기존판(S) 자세히 — 처음 5분 뺌</h2><p id='lateNote'></p><div class='card' id='late'></div>"
        "<h2>현실 계산판(R) 자세히 — 처음 5분 뺌</h2><p id='realNote'></p><div class='card' id='real'></div>"
        "<h2>최근 거래 20개 (일반 수수료·0.1초, S와 R)</h2><div class='card' id='recent'></div>"
        "<h2>가장 크게 잃은 거래 5개</h2><p>한 번 크게 잃으면 작은 이익 수십 번이 사라져요. 신호 때는 이익이었는데 체결해 보니 손해인 거래가 여기 모여요.</p>"
        "<div class='card' id='worst'></div>"
        "<details><summary>처음부터 전체 (시작할 때 이미 벌어져 있던 차이가 섞여 있음)</summary><div class='card' id='all'></div></details>"
        "<h2>읽는 법</h2><ul class='muted' style='font-size:13px'>"
        "<li>늦어서 잃은 몫 = 신호 때 순수익 − 실제 체결 순수익. 클수록 남이 먼저 가져가거나 가짜 틈이었다는 뜻.</li>"
        "<li>이대로 하루·한 달 = 지금까지 번 속도를 그대로 늘린 것. 크게 잃는 거래 한 번에 뒤집히니 참고만.</li>"
        "<li>몰린 방향이 한쪽으로 크면 코인이 한 거래소에 쌓여, 다시 옮기는 출금 수수료·시간이 들어요(계산에 없음).</li>"
        "<li>들고 있는 코인 5만 원어치의 값 변동도 계산에 없어요.</li></ul>"
        f"</main><script>{JS}</script></body></html>"
    )
