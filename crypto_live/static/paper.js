/* 모의 투자 화면 — 엔진(src/autotrade/paper)이 남긴 기록을 서버(/api/paper/*)에서 읽어 보여 준다.
 * 계산은 전부 엔진이 한다. 이 파일은 그리기만 한다.
 */
"use strict";

const KST = 9 * 3600;
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 없어도 동작 */ } },
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const won = (v, sign = false) => (v == null || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}원`);
const pct = (v, d = 2, sign = true) => (v == null || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const num = (v, d = 0) => (v == null || !isFinite(v) ? "—" : v.toLocaleString("ko-KR", { maximumFractionDigits: d }));
const dirOf = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const p2 = (n) => String(n).padStart(2, "0");
function clock(sec, withDate = false) {
  const d = new Date((sec + KST) * 1000);
  const hms = `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:${p2(d.getUTCSeconds())}`;
  return withDate ? `${d.getUTCMonth() + 1}월 ${d.getUTCDate()}일 ${hms}` : hms;
}
function span(sec) {
  if (sec == null || !isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}일 ${h}시간`;
  if (h) return `${h}시간 ${m}분`;
  if (m) return `${m}분 ${sec % 60}초`;
  return `${sec}초`;
}
const icon = (name) => `<svg width="20" height="20" aria-hidden="true"><use href="#i-${name}"/></svg>`;

const S = { data: null, seen: {}, trades: {}, tradeN: {}, failed: 0 };
const SLOW_INFO = {
  s13: { desc: "선물 펀딩비와 김치 프리미엄이 과열이 아니고, 가격이 20일 평균 위일 때만 비트코인을 듭니다. 매일 아침 9시에 확인.", color: "--s-b" },
  s14: { desc: "비트코인·이더리움·리플·솔라나 중 흔들림 대비 가장 잘 오른 둘을 50%씩. 다 내리는 중이면 현금. 매주 월요일 아침 9시.", color: "--s-c" },
  s12: { desc: "ETF 5개를 덜 흔들리는 것일수록 많이 담고, 주식 두 개만 120일 평균 아래면 단기채권으로 피합니다. 매주 마지막 거래일 장 마감 뒤 계산, 다음 거래일 아침 체결.", color: "--s-a" },
  c_btc: { color: "--ink-3", dash: "" },
  c_etf4: { color: "--ink-3", dash: "7 4" },
  c_etf5: { color: "--ink-3", dash: "12 3 2 3" },
  c_kodex: { color: "--ink-3", dash: "2 4" },
};
const SCALP_NAMES = { s17: "#17 틈 단타 (정식)", s18: "#18 비용을 넘는 틈만 자주" };

/** 내용이 바뀐 부분만 다시 그린다 (2초마다 전체를 갈아 끼우면 깜빡이고 가리키던 곳이 풀린다) */
function changed(key, value) {
  const s = JSON.stringify(value);
  if (S.seen[key] === s) return false;
  S.seen[key] = s;
  return true;
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (r.status === 401) { location.href = `/login.html?next=${encodeURIComponent(location.pathname)}`; throw new Error("PIN 필요"); }
  const b = await r.json().catch(() => null);
  if (!r.ok) throw new Error((b && b.error) || `HTTP ${r.status}`);
  return b;
}

/* ── 엔진 상태 ── */
function renderEngine(d) {
  const e = d.engine, badge = $("eng-badge");
  badge.dataset.state = e.alive ? "live" : "wait";
  badge.textContent = e.alive ? "기록 중" : "꺼져 있음";
  $("eng-since").textContent = d.uptime.first ? `첫 기록 ${clock(d.uptime.first, true).slice(0, -3)}` : "";
  $("eng-line").textContent = e.alive
    ? "단타와 호가 비용을 실시간으로 기록하고 있어요."
    : (e.last ? `마지막 기록 ${clock(e.last, true)}. 코인시세.bat 을 켜면 단타 기록이 다시 시작돼요.` : "아직 한 번도 켜지 않았어요. 코인시세.bat 으로 켭니다.");
  const facts = [
    ["해외 가격", e.source_name],
    ["켜져 있던 시간", span(d.uptime.seconds)],
    ["확인 한 번에", e.loop_ms ? `${(e.loop_ms / 1000).toFixed(2)}초` : "—"],
    ["느린 전략 계산", d.slow ? clock(d.slow.updated, true).slice(0, -3) : "아직"],
  ];
  $("eng-facts").innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  const warn = [];
  if (!e.official) warn.push(`OKX 선물에 연결되지 않아 <b>${esc(e.source_name)}</b> 가격으로 대신하고 있어요. 이 기록은 시험용이라 #17·#18 채점에 넣지 않습니다.`);
  if (e.errors && e.errors.feed) warn.push(`시세를 받지 못하고 있어요: ${esc(e.errors.feed)}`);
  $("eng-warn").hidden = !warn.length;
  $("eng-warn").innerHTML = warn.join("<br>");
  document.querySelectorAll("[data-source]").forEach((el) => { el.textContent = e.source_name; });
  const st = $("status");
  st.dataset.state = e.alive ? "live" : "closed";
  $("status-text").textContent = e.alive ? "기록 중" : "엔진 꺼짐";
}

/* ── 대결 ── */
function rankRows(d) {
  const rows = [];
  for (const key of ["s18", "s17"]) {
    const s = d.scalp[key];
    rows.push({ name: SCALP_NAMES[key], kind: "단타", control: false, value: s.value, ret: s.value / s.capital - 1,
      note: s.n ? `${num(s.n)}건` : "아직 거래 없음", started: s.n > 0 });
  }
  for (const it of Object.values((d.slow && d.slow.items) || {})) {
    const control = it.kind === "control";
    if (it.error) { rows.push({ name: it.name, control, error: true, note: "자료를 받지 못함" }); continue; }
    rows.push({ name: it.name, control, value: it.value, ret: it.ret, started: it.trades.length > 0,
      note: it.trades.length ? `거래 ${it.trades.length}번` : `${it.start} 시작 · 첫 체결 전` });
  }
  rows.sort((a, b) => (b.started ? 1 : 0) - (a.started ? 1 : 0) || (b.ret ?? -9) - (a.ret ?? -9));
  return rows;
}
function renderRank(d) {
  const rows = rankRows(d);
  if (!changed("rank", rows)) return;
  const max = Math.max(0.002, ...rows.map((r) => Math.abs(r.ret || 0)));
  $("rank").innerHTML = rows.map((r, i) => `
    <li class="race-row${r.control ? " control" : ""}">
      <span class="rank">${r.started ? i + 1 : "·"}</span>
      <span class="name">${esc(r.name)} <small>${esc(r.note)}</small></span>
      <span class="ret" data-dir="${dirOf(r.ret)}">${r.error ? "—" : `${won(r.value)} <small>${pct(r.ret)}</small>`}</span>
      <span class="race-bar"><i data-dir="${dirOf(r.ret)}" style="width:${r.ret ? Math.max(2, Math.abs(r.ret) / max * 100) : 0}%"></i></span>
    </li>`).join("");
}

/* ── 지금 해외와 국내 ── */
function renderLive(d) {
  const live = d.live, body = $("live-rows");
  if (!live || !live.coins || !Object.keys(live.coins).length) {
    if (changed("live", null)) body.innerHTML = `<tr><td colspan="6" class="empty">엔진이 켜지면 보여요</td></tr>`;
    return;
  }
  const now = d.now;
  body.innerHTML = Object.entries(live.coins).map(([coin, c]) => {
    const held = live.positions.filter((p) => p.coin === coin)
      .map((p) => `<span class="chip chip-hold">#${p.key.slice(1)} 보유 · ${Math.max(0, Math.round(p.exit_due - now))}초 안에 팜</span>`).join(" ");
    const lagCls = c.lag >= 0.002 ? "hot" : c.lag >= 0.0005 ? "warm" : "";
    return `<tr>
      <th scope="row"><span class="coin-cell" data-coin="${coin}"></span>${coin}</th>
      <td data-dir="${dirOf(c.move)}">${pct(c.move, 3)}</td>
      <td data-dir="${dirOf(c.u_move)}">${pct(c.u_move, 3)}</td>
      <td class="lag ${lagCls}">${c.lag == null ? "—" : `${(c.lag * 100).toFixed(3)}%p`}</td>
      <td>${pct(c.spread, 3, false)}${c.one_tick ? " <small>한 칸</small>" : ""}</td>
      <td>${held || '<span class="muted">기다리는 중</span>'}</td></tr>`;
  }).join("");
  body.querySelectorAll(".coin-cell").forEach((el) => el.replaceWith(COIN_LOGO.el(el.dataset.coin, 20)));
}

/* ── 지연 관측 ── */
function renderRadar(d) {
  const r = d.radar;
  if (!changed("radar", r)) return;
  if (!r || !r.n) {
    CHART.line($("radar-chart"), { series: [], empty: "해외가 3초에 +0.15% 넘게 오르는 일이 생기면 여기에 쌓여요" });
    $("radar-keys").innerHTML = ""; $("radar-table").innerHTML = ""; $("radar-note").textContent = "";
    return;
  }
  const series = [
    { name: "평균", color: "--s-c", points: r.curve.map((c) => [c.offset, c.catch * 100]) },
    { name: "가운데 값", color: "--s-b", points: r.curve.map((c) => [c.offset, c.catch_median * 100]) },
  ];
  if (r.days > 1) series.push({ name: "가장 많았던 하루를 빼면", color: "--ink-3", dash: "6 4",
    points: r.curve.filter((c) => c.catch_ex_top_day != null).map((c) => [c.offset, c.catch_ex_top_day * 100]) });
  $("radar-keys").innerHTML = series.map((s) => `<li><span class="key key-line" style="background:var(${s.color})"></span>${s.name}</li>`).join("");
  const xf = (x) => (x === 0 ? "신호 때" : `${x}초 뒤`);
  CHART.line($("radar-chart"), { series, categorical: true, xFmt: xf, yFmt: (v) => `${Math.round(v)}%`, lines: [{ y: 100, label: "다 따라옴" }] });
  $("radar-table").innerHTML = `<table class="cost"><thead><tr><th scope="col">언제</th><th scope="col">평균</th><th scope="col">가운데 값</th><th scope="col">실제 잰 시점</th><th scope="col">횟수</th></tr></thead><tbody>${
    r.curve.map((c) => `<tr><th scope="row">${xf(c.offset)}</th><td>${Math.round(c.catch * 100)}%</td><td>${Math.round(c.catch_median * 100)}%</td><td>${c.actual.toFixed(2)}초</td><td>${num(c.n)}</td></tr>`).join("")}</tbody></table>`;
  $("radar-note").textContent = `${num(r.n)}번 관찰 · ${r.days}일 · 가장 많았던 날 ${r.top_day} (${num(r.top_day_n)}번) · 그때 해외 오름폭 평균 ${pct(r.mean_move, 2)}. `
    + "100%에 빨리 닿을수록 틈이 금방 사라진다는 뜻이에요. '실제 잰 시점'이 목표보다 많이 늦으면 인터넷이 느린 곳에서 잰 기록입니다.";
}

/* ── 단타 카드 ── */
function criteria(s, official) {
  const need = s.rules.min_events, enough = s.n_events >= need;
  const mark = (ok, ready = true) => (!ready ? "wait" : ok ? "ok" : "no");
  const rows = [
    ["건당 평균이 플러스 (업비트 비용 뺀 것)", s.n ? pct(s.mean_net, 3) : "거래 없음", mark(s.mean_net > 0, s.n > 0)],
    ["우연이 아닐 것 (사건 단위 t값 2.5 이상)", s.t == null ? "거래가 더 필요해요" : `t값 ${s.t.toFixed(2)} (거래 하나하나로 세면 ${s.t_trades == null ? "—" : s.t_trades.toFixed(2)})`, mark(s.t >= 2.5, s.t != null)],
    ["가장 많이 번 3일을 빼도 플러스", s.mean_ex_top3 == null ? "날짜가 4일 이상 필요해요" : pct(s.mean_ex_top3, 3), mark(s.mean_ex_top3 > 0, s.mean_ex_top3 != null)],
    ["앞 절반·뒤 절반 모두 플러스", s.first_half == null ? "거래가 더 필요해요" : `${pct(s.first_half, 3)} / ${pct(s.second_half, 3)}`, mark(s.first_half > 0 && s.second_half > 0, s.first_half != null)],
  ];
  const head = !official ? "시험 기록이라 채점에 넣지 않아요 (참고로만 표시)"
    : enough ? `사건 ${num(s.n_events)}번 — 채점할 수 있어요` : `사건 ${num(s.n_events)} / ${num(need)}번 — 아직 채점 전 (지금 값은 참고)`;
  return `<h3 class="mini-title">채점 <small>${head}</small></h3>
    <p class="fine-note">60초 안에 이어진 신호(여러 코인이 같이 움직인 때 등)는 한 사건으로 묶어 셉니다. 거래 ${num(s.n)}건 = 사건 ${num(s.n_events || 0)}번.</p><ul class="check-list crit">${rows.map(([t, v, m]) => `
    <li class="check ${m}">${icon(m)}<div><p class="check-title">${t}</p><p class="check-detail">${v}</p></div></li>`).join("")}</ul>`;
}
function groupTable(title, groups) {
  const keys = Object.keys(groups || {});
  if (!keys.length) return "";
  return `<div class="table-scroll"><table class="cost tbl-s"><thead><tr><th scope="col">${title}</th><th scope="col">횟수</th><th scope="col">건당 평균</th></tr></thead><tbody>${
    keys.sort((a, b) => groups[b].n - groups[a].n).map((k) => `<tr><th scope="row">${esc(k)}</th><td>${num(groups[k].n)}</td><td data-dir="${dirOf(groups[k].mean)}">${pct(groups[k].mean, 3)}</td></tr>`).join("")}</tbody></table></div>`;
}
function renderScalp(d, key) {
  const s = d.scalp[key], box = document.querySelector(`.scalp-body[data-key="${key}"]`);
  if (!changed(`scalp-${key}`, [s, d.engine.official])) return;
  const pnl = s.value - s.capital;
  const tiles = [
    ["계좌 가치", won(s.value), `<span class="pnl-inline" data-dir="${dirOf(pnl)}">${won(pnl, true)} (${pct(pnl / s.capital)})</span>`],
    ["거래", `${num(s.n)}건`, s.n ? `오늘 ${num(s.today)}건 · ${s.days}일${s.refills ? ` · 돈 다시 채움 ${num(s.refills)}번` : ""}` : "아직 없음"],
    ["건당 평균", `<span data-dir="${dirOf(s.mean_net)}">${pct(s.mean_net, 3)}</span>`, `비용 빼기 전 ${pct(s.mean_gross, 3)}`],
    ["이긴 비율", s.n ? pct(s.win_rate, 0, false) : "—", "수수료까지 빼고 남은 거래"],
    ["코인원이었다면", `<span data-dir="${dirOf(s.mean_alt)}">${pct(s.mean_alt, 3)}</span>`, s.n_alt ? `수수료 0% 가정 · ${num(s.n_alt)}건 비교` : "수수료 0% 가정"],
  ];
  box.innerHTML = `
    <div class="tiles">${tiles.map(([l, v, sub]) => `<div class="tile"><span class="tile-label">${l}</span><b>${v}</b><small>${sub}</small></div>`).join("")}</div>
    <h3 class="mini-title">누적 손익 <small>거래가 끝날 때마다</small></h3>
    <div class="svg-chart" id="curve-${key}" role="img" aria-label="${SCALP_NAMES[key]} 누적 손익. 아래 거래 목록에 같은 내용이 있습니다."></div>
    ${criteria(s, d.engine.official)}
    <div class="two">${groupTable("파는 이유", s.reasons)}${groupTable("코인", s.coins)}</div>
    <h3 class="mini-title">최근 거래 <small>누르면 그때 호가창까지 볼 수 있어요</small></h3>
    <ol class="trade-list" id="trades-${key}"><li class="empty">아직 거래가 없어요</li></ol>
    <button type="button" class="link-btn" id="more-${key}" hidden>더 보기</button>`;
  const shortSpan = s.n > 0 && s.last_t - s.first_t < 1800;
  CHART.line($(`curve-${key}`), {
    series: [{ name: "누적 손익", color: "--eq", points: s.curve || [] }], empty: "첫 거래가 끝나면 그려져요",
    xFmt: (t) => (shortSpan ? clock(t) : clock(t).slice(0, 5)), tipFmt: (t) => clock(t, true), yFmt: (v) => won(v, true), lines: [{ y: 0 }],
  });
  $(`more-${key}`).addEventListener("click", () => loadTrades(key, true));
  drawTrades(key);
  if (S.tradeN[key] !== s.n) { S.tradeN[key] = s.n; loadTrades(key, false); }
}
async function loadTrades(key, more) {
  const cur = S.trades[key] || [];
  const q = new URLSearchParams({ key, limit: more ? "50" : "12", source: S.data.engine.source });
  if (more && cur.length) q.set("before", cur[cur.length - 1].id);
  try {
    const { trades } = await getJSON(`/api/paper/trades?${q}`);
    S.trades[key] = more ? cur.concat(trades) : trades.concat(cur.filter((t) => !trades.length || t.id < trades[trades.length - 1].id));
    drawTrades(key);
  } catch { /* 다음 번에 다시 */ }
}
function drawTrades(key) {
  const list = S.trades[key] || [], el = $(`trades-${key}`);
  if (!el || !list.length) return;
  el.innerHTML = list.map((t) => `
    <li><button type="button" class="trade" data-id="${t.id}">
      <span class="when">${clock(t.entry_t, true)}</span>
      <span class="what"><b>${t.coin}</b> ${esc(t.reason)} · ${(t.exit_t - t.entry_t).toFixed(1)}초</span>
      <span class="net" data-dir="${dirOf(t.net_pct)}">${pct(t.net_pct, 3)} <small>${won(t.net_krw, true)}</small></span>
    </button></li>`).join("");
  el.querySelectorAll(".trade").forEach((b) => b.addEventListener("click", () => openAudit(b.dataset.id)));
  $(`more-${key}`).hidden = list.length >= (S.data.scalp[key].n || 0);
}

/* ── 감사 기록 (거래 하나를 원자료와 대조) ── */
function bookTable(book, title) {
  if (!book) return "";
  const rows = [...book.asks].reverse().map((a) => `<tr class="ask"><td>${num(a[0], 4)}</td><td>${num(a[0] * a[1])}원</td></tr>`)
    .concat(book.bids.map((b) => `<tr class="bid"><td>${num(b[0], 4)}</td><td>${num(b[0] * b[1])}원</td></tr>`)).join("");
  return `<div><h3 class="mini-title">${title}</h3><table class="cost book-snap"><thead><tr><th scope="col">가격</th><th scope="col">잔량(금액)</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
async function openAudit(id) {
  const dlg = $("audit");
  $("audit-body").innerHTML = `<p class="empty">불러오는 중…</p>`;
  dlg.showModal();
  try {
    const t = await getJSON(`/api/paper/trade?id=${id}`), x = t.detail;
    const fee = t.amount * 0.0005 * 2 + (t.gross_pct * t.amount) * 0.0005;
    const steps = [
      [clock(t.signal_t, true), "신호", `해외가 3초에 ${pct(t.move, 3)} 올랐고 업비트는 ${(t.lag * 100).toFixed(3)}%p 뒤처져 있었어요. 그때 호가 차이 ${pct(x.spread_signal, 3, false)}.`],
      [clock(t.entry_t, true), "샀어요", `신호 ${(x.latency_in * 1000).toFixed(0)}ms 뒤에 새로 받은 호가창에서 ${won(t.amount)}어치를 평균 ${num(t.entry_price, 4)}원에 (호가 ${x.entry_levels}칸 사용, 가운데 값보다 ${pct(t.entry_price / x.entry_mid - 1, 3)}).`],
      [clock(t.exit_t, true), `팔았어요 — ${esc(t.reason)}`, `조건을 본 ${(x.latency_out * 1000).toFixed(0)}ms 뒤 호가창에서 평균 ${num(t.exit_price, 4)}원에 (가운데 값보다 ${pct(t.exit_price / x.exit_mid - 1, 3)}).`],
    ];
    $("audit-title").textContent = `${SCALP_NAMES[t.key]} · ${t.coin} · 기록 ${t.id}번`;
    $("audit-body").innerHTML = `
      <ol class="timeline">${steps.map(([w, h, b]) => `<li class="tl"><span class="when">${w}</span><span class="what"><b>${h}</b><br>${b}</span></li>`).join("")}</ol>
      <div class="tiles">
        <div class="tile"><span class="tile-label">비용 빼기 전</span><b data-dir="${dirOf(t.gross_pct)}">${pct(t.gross_pct, 3)}</b><small>호가 체결만 반영</small></div>
        <div class="tile"><span class="tile-label">업비트 수수료</span><b>${won(-fee)}</b><small>0.05% × 2</small></div>
        <div class="tile"><span class="tile-label">남은 것</span><b data-dir="${dirOf(t.net_krw)}">${won(t.net_krw, true)}</b><small>${pct(t.net_pct, 3)}</small></div>
        <div class="tile"><span class="tile-label">코인원이었다면</span><b data-dir="${dirOf(t.alt_net_pct)}">${pct(t.alt_net_pct, 3)}</b><small>수수료 0% 가정</small></div>
      </div>
      <p class="fine-note">해외 가격(${esc(t.source)}): 신호 3초 전 ${num(x.g_ref, 6)} → 신호 때 ${num(x.g_signal, 6)}. 신호 3초 전 업비트 파는 값 ${num(x.u_ref, 4)}원.</p>
      <div class="two">${bookTable(x.signal_book, "신호 때 호가창")}${bookTable(x.entry_book, "살 때 호가창")}${bookTable(x.exit_book, "팔 때 호가창")}</div>`;
  } catch (e) {
    $("audit-body").innerHTML = `<p class="empty">기록을 읽지 못했어요: ${esc(e.message)}</p>`;
  }
}

/* ── 느린 전략 ── */
function holdingsLine(it) {
  if (!it.holdings.length) return `<span class="chip">현금 100%</span>`;
  const cash = it.cash / it.value;
  return it.holdings.map((h) => `<span class="chip chip-hold">${esc(h.name)} ${pct(h.weight, 0, false)}</span>`).join(" ")
    + (cash > 0.005 ? ` <span class="chip">현금 ${pct(cash, 0, false)}</span>` : "");
}
function explain13(x) {
  if (!x) return `<p class="empty">아직 판단한 날이 없어요</p>`;
  if (!x.complete) return `<p class="fine-note">${x.date} 자료가 비어 있어 그날은 그대로 뒀어요.</p>`;
  const f = `${(x.funding * 100).toFixed(4)}%`, k = pct(x.kimchi, 2);
  const rows = x.holding
    ? [["펀딩비가 과열이 아님", `최근 9회 평균 ${f} · 0.06%를 넘으면 팝니다`, !x.sell.funding],
      ["김치 프리미엄이 과열이 아님", `${k} · 석 달 중 상위 5% 선 ${pct(x.k_sell, 2)}을 넘으면 팝니다`, !x.sell.kimchi],
      ["20일 평균 근처 위", `종가 ${num(x.close)}원 · 평균의 98.5%인 ${num(x.ma * 0.985)}원 아래면 팝니다`, !x.sell.trend]]
    : [["펀딩비가 식어 있음", `최근 9회 평균 ${f} · 0.03% 이하여야 삽니다`, x.buy.funding],
      ["김치 프리미엄이 높지 않음", `${k} · 석 달 중 상위 20% 선 ${pct(x.k_buy, 2)} 이하여야 삽니다`, x.buy.kimchi],
      ["20일 평균 위", `종가 ${num(x.close)}원 · 20일 평균 ${num(x.ma)}원`, x.buy.trend]];
  return `<p class="fine-note">${x.date} 캔들 마감 기준 · ${x.holding ? "들고 있어요. 아래 중 하나라도 깨지면 팝니다" : "쉬는 중이에요. 아래 셋이 다 맞아야 삽니다"}</p>
    <ul class="check-list crit">${rows.map(([t, v, ok]) => `<li class="check ${ok ? "ok" : "no"}">${icon(ok ? "ok" : "no")}<div><p class="check-title">${t}</p><p class="check-detail">${v}</p></div></li>`).join("")}</ul>`;
}
function explain14(x) {
  if (!x || !x.rows.length) return `<p class="empty">자료가 모자라요</p>`;
  return `<p class="fine-note">${x.as_of} 마감 기준 점수 (실제 판단은 일요일 캔들이 끝난 월요일 아침)</p>
    <div class="table-scroll"><table class="cost tbl-s"><thead><tr><th scope="col">코인</th><th scope="col">21일 수익률</th><th scope="col">점수</th><th scope="col">지금 계산하면</th></tr></thead><tbody>${
    x.rows.map((r) => `<tr><th scope="row">${r.ticker.replace("KRW-", "")}</th><td data-dir="${dirOf(r.ret)}">${pct(r.ret, 1)}</td><td>${r.score.toFixed(1)}</td><td>${r.pick ? "<b>담음</b>" : r.ret > 0 ? "순위 밖" : "내리는 중"}</td></tr>`).join("")}</tbody></table></div>`;
}
function explain12(x) {
  if (!x || !x.rows) return `<p class="empty">120거래일치 자료가 모자란 자산이 있어 전부 단기채권에 둡니다</p>`;
  return `<p class="fine-note">${x.as_of} 마감 기준 (실제 판단은 주 마지막 거래일)</p>
    <div class="table-scroll"><table class="cost tbl-s"><thead><tr><th scope="col">자산</th><th scope="col">하루 흔들림</th><th scope="col">기본 비중</th><th scope="col">120일 평균 대비</th><th scope="col">지금 계산하면</th></tr></thead><tbody>${
    x.rows.map((r) => `<tr><th scope="row">${esc(r.name)}</th><td>${pct(r.vol, 2, false)}</td><td>${pct(r.base, 0, false)}</td><td data-dir="${dirOf(r.close / r.ma - 1)}">${pct(r.close / r.ma - 1, 1)}</td><td>${r.hold ? "담음" : "<b>단기채권으로</b>"}</td></tr>`).join("")}</tbody></table></div>`;
}
function renderSlowCard(d, key) {
  const box = document.querySelector(`.slow-card[data-key="${key}"]`);
  const items = (d.slow && d.slow.items) || {}, it = items[key];
  if (!changed(`slow-${key}`, [it, d.slow && d.slow.next])) return;
  const title = (it && it.name) || { s12: "#12 흔들림 맞춰 나눠 담기 + 주식 방어", s13: "#13 펀딩비·김치 프리미엄", s14: "#14 코인 4개 주간 갈아타기" }[key];
  const head = (chip) => `<div class="card-head"><h2 class="card-title">${esc(title)}</h2>${chip}</div><p class="card-sub">${SLOW_INFO[key].desc}</p>`;
  if (!it) { box.innerHTML = `${head("")}<p class="empty">엔진이 한 번 계산하면 보여요 (켠 뒤 1분쯤)</p>`; return; }
  if (it.error) {
    const why = it.error;
    box.innerHTML = `${head('<span class="chip chip-warn">자료 없음</span>')}<p class="warn-line">${esc(why)}</p>`;
    return;
  }
  const until = d.slow.next[key];
  const tiles = [
    ["계좌 가치", won(it.value), `<span class="pnl-inline" data-dir="${dirOf(it.ret)}">${pct(it.ret)}</span>`],
    ["가장 크게 떨어졌을 때", pct(it.max_drawdown, 1), "시작 이후"],
  ];
  for (const c of (it.controls || []).map((k) => items[k])) {
    tiles.push([c && !c.error ? c.name : "비교 기준", c && !c.error ? `<span data-dir="${dirOf(c.ret)}">${pct(c.ret)}</span>` : "—",
      c && !c.error ? `같은 기간 · 가장 크게 떨어졌을 때 ${pct(c.max_drawdown, 1)}` : "같은 기간"]);
  }
  const fz = it.frozen;
  const frozenLine = !fz ? "" : !fz.n ? `<p class="fine-note">첫 결정부터 내린 순간의 값으로 저장해 둡니다(판정은 저장한 결정으로).</p>` : fz.changed.length
    ? `<p class="warn-line">처음 저장한 결정과 지금 다시 계산한 결정이 다른 날이 있어요: ${fz.changed.map(esc).join(", ")}. 판정은 처음 저장한 결정으로 합니다(자료가 나중에 고쳐졌을 수 있음).</p>`
    : `<p class="fine-note">결정 ${num(fz.n)}번을 내린 순간의 값으로 저장해 두었고, 다시 계산해도 모두 같아요.</p>`;
  const explain = { s13: explain13, s14: explain14, s12: explain12 }[key](it.explain);
  const trades = it.trades.slice(-8).reverse();
  box.innerHTML = `${head(`<span class="chip">${it.trades.length ? "진행 중" : `${it.start} 시작`}</span>`)}
    <div class="tiles">${tiles.map(([l, v, sub]) => `<div class="tile"><span class="tile-label">${esc(l)}</span><b>${v}</b><small>${sub}</small></div>`).join("")}</div>
    <div class="countdown">${icon("clock")}<span>다음 판단까지 <b data-until="${until}">—</b></span><span class="muted">${clock(until, true).slice(0, -3)}${key === "s12" ? " 무렵 (휴장이면 그 전 거래일)" : ""}</span></div>
    <h3 class="mini-title">지금 보유</h3><p class="chips">${holdingsLine(it)}</p>
    <h3 class="mini-title">판단 근거</h3>${explain}${frozenLine}
    <h3 class="mini-title">거래 기록 <small>비용 합계 ${won(it.total_cost)}</small></h3>
    ${trades.length ? `<ol class="timeline short">${trades.map((t) => `<li class="tl ${t.side === "buy" ? "buy" : "sell"}"><span class="when">${t.date} 아침</span><span class="what">${esc(t.name)} ${t.side === "buy" ? "삼" : "팖"} · ${won(t.amount)} · ${num(t.price, t.price >= 100 ? 0 : 2)}원에</span></li>`).join("")}</ol>`
    : `<p class="empty">아직 체결된 거래가 없어요</p>`}`;
  tick();
}
function renderEquity(d) {
  const items = (d.slow && d.slow.items) || {};
  const series = Object.values(items).filter((it) => !it.error && it.curve && it.curve.length > 1 && it.trades.length)
    .map((it) => ({ name: it.name, color: SLOW_INFO[it.key].color, dash: SLOW_INFO[it.key].dash,
      points: it.curve.map(([date, v]) => [Date.parse(`${date}T00:00:00Z`) / 1000, v]) }));
  if (!changed("equity", series)) return;
  $("eq-keys").innerHTML = series.map((s) => `<li><span class="key key-line${s.dash ? " key-dash" : ""}" style="${s.dash ? "" : `background:var(${s.color})`}"></span>${esc(s.name)}</li>`).join("");
  const day = (t) => { const x = new Date(t * 1000); return `${x.getUTCMonth() + 1}/${x.getUTCDate()}`; };
  CHART.line($("eq-chart"), { series, xFmt: day, yFmt: (v) => `${num(v / 10000)}만`, lines: [{ y: 10000000, label: "시작 1,000만 원" }],
    empty: "첫 체결이 일어난 뒤부터 그려져요 (코인 전략은 11월 16일 아침, ETF는 그다음 주)" });
  $("eq-note").textContent = series.length ? "하루에 한 점(그날 마지막 가격 기준). 색 선은 전략, 회색 선은 비교 기준입니다." : "";
}

/* ── 호가 비용 ── */
function renderCosts(d) {
  if (!changed("costs", d.costs)) return;
  if (!d.costs.length) return;
  const by = {};
  for (const c of d.costs) (by[c.coin] ||= {})[c.exchange] = c;
  const cell = (v) => `<td class="${v != null && v > 0.002 ? "over" : ""}">${v == null ? "잔량 부족" : pct(v, 3, false)}</td>`;
  const side = (c) => (c ? `${cell(c.spread)}${cell(c.rt1)}${cell(c.rt5)}${cell(c.rt10)}` : "<td>—</td>".repeat(4));
  $("cost-table").innerHTML = `<table class="cost"><thead>
    <tr><th rowspan="2" scope="col">코인</th><th colspan="4" scope="colgroup">업비트</th><th colspan="4" scope="colgroup">코인원</th></tr>
    <tr>${"<th scope=\"col\">호가 차이</th><th scope=\"col\">100만 원</th><th scope=\"col\">500만 원</th><th scope=\"col\">1,000만 원</th>".repeat(2)}</tr></thead><tbody>${
    Object.keys(by).sort().map((coin) => `<tr><th scope="row">${coin}</th>${side(by[coin].upbit)}${side(by[coin].coinone)}</tr>`).join("")}</tbody></table>
    <p class="fine-note">${num(Math.max(...d.costs.map((c) => c.n)))}번 잰 값의 가운데 값. 가정(0.20%)보다 큰 칸은 표시했어요. 업비트 수수료(왕복 0.10%)는 여기에 더해집니다.</p>`;
}

/* ── 돌리기 ── */
function tick() {
  const now = Date.now() / 1000;
  document.querySelectorAll("[data-until]").forEach((el) => { el.textContent = span(+el.dataset.until - now); });
}
function render(d) {
  S.data = d;
  renderEngine(d); renderRank(d); renderLive(d); renderRadar(d);
  renderScalp(d, "s18"); renderScalp(d, "s17");
  renderEquity(d); ["s13", "s14", "s12"].forEach((k) => renderSlowCard(d, k));
  renderCosts(d);
}
async function poll() {
  try {
    render(await getJSON("/api/paper/summary"));
    S.failed = 0;
  } catch (e) {
    S.failed += 1;
    if (S.failed >= 2) { $("status").dataset.state = "closed"; $("status-text").textContent = "연결 끊김"; }
  }
  setTimeout(poll, document.hidden ? 10000 : 2000);
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  store.set("theme", t);
  $("theme").querySelector("use").setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon");
}
async function init() {
  setTheme(store.get("theme", null) || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("theme").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $("audit-close").addEventListener("click", () => $("audit").close());
  $("audit").addEventListener("click", (e) => { if (e.target === $("audit")) $("audit").close(); });
  await NET.detect();
  if (NET.mode !== "server") {
    const note = $("offline-note");
    note.hidden = false;
    note.textContent = "모의 투자 기록은 엔진을 켜 둔 컴퓨터에서 열어야 보여요 (코인시세.bat). 이 공개 주소에는 기록을 올리지 않습니다.";
    $("main").hidden = true;
    document.querySelector(".jump").hidden = true;
    $("status").dataset.state = "closed"; $("status-text").textContent = "기록 없음";
    return;
  }
  setInterval(tick, 1000);
  poll();
}
init();
