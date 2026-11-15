/* 작은 선 그래프 (SVG, 라이브러리 없음). 색은 CSS 변수로 받아 밝은/어두운 화면을 따라간다.
 * CHART.line(el, { series: [{ name, color: "--s-a", dash, points: [[x, y], ...] }], xFmt, yFmt, categorical, lines })
 * - categorical: x 를 값이 아니라 순서대로 같은 간격에 놓는다 (0초, 0.2초, 0.5초 … 처럼 간격이 고르지 않을 때)
 * - lines: [{ y, label }] 가로 기준선
 */
"use strict";

const CHART = {
  NS: "http://www.w3.org/2000/svg",
  observers: new WeakMap(),

  line(el, opts) {
    el._chartOpts = opts;
    if (!this.observers.has(el)) {
      const ro = new ResizeObserver(() => el._chartOpts && this.draw(el, el._chartOpts));
      ro.observe(el);
      this.observers.set(el, ro);
    }
    this.draw(el, opts);
  },

  niceStep(raw) {
    const p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
  },

  draw(el, o) {
    const series = o.series.filter((s) => s.points.length);
    const W = Math.max(280, el.clientWidth || 600), H = o.height || (el.classList.contains("tall") ? 300 : 220);
    if (!series.length) {
      el.innerHTML = `<p class="empty chart-empty">${o.empty || "아직 기록이 없어요"}</p>`;
      return;
    }
    const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
    const ys = series.flatMap((s) => s.points.map((p) => p[1])).concat((o.lines || []).map((l) => l.y));
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
    const step = this.niceStep((hi - lo) / 4);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const yFmt = o.yFmt || ((v) => v.toLocaleString("ko-KR")), xFmt = o.xFmt || String;
    const labels = [];
    for (let v = lo; v <= hi + step * 1e-6; v += step) labels.push(v);
    const L = Math.min(96, 14 + Math.max(...labels.map((v) => yFmt(v).length)) * 7.2), R = 14, T = 10, B = 26;
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const X = (x) => (o.categorical ? L + (W - L - R) * (xs.length > 1 ? xs.indexOf(x) / (xs.length - 1) : 0.5)
      : L + (W - L - R) * (x1 > x0 ? (x - x0) / (x1 - x0) : 0.5));
    const Y = (y) => T + (H - T - B) * (hi - y) / (hi - lo);

    let g = "";
    for (const v of labels) {
      g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="grid${Math.abs(v) < step * 1e-6 ? " zero" : ""}"/>`;
      g += `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`;
    }
    const ticks = o.categorical ? xs : (xs.length <= 5 ? xs : [0, 0.25, 0.5, 0.75, 1].map((f) => xs[Math.round(f * (xs.length - 1))]));
    let lastEnd = -1e9;
    for (const x of [...new Set(ticks)]) {
      const label = xFmt(x), half = label.length * 3.6;
      const px = Math.min(Math.max(X(x), L + half), W - R - half);
      if (px - half < lastEnd + 6) continue; // 겹치는 눈금 글자는 건너뜀
      lastEnd = px + half;
      g += `<text x="${px}" y="${H - 8}" text-anchor="middle">${label}</text>`;
    }
    for (const l of o.lines || []) {
      g += `<line x1="${L}" x2="${W - R}" y1="${Y(l.y)}" y2="${Y(l.y)}" class="ref"/>`;
      if (l.label) g += `<text x="${W - R - 4}" y="${Y(l.y) - 5}" text-anchor="end" class="ref-label">${l.label}</text>`;
    }
    for (const s of series) {
      const pts = s.points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
      g += `<polyline points="${pts}" fill="none" stroke="var(${s.color})" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""}/>`;
      if (s.points.length <= 12 || o.categorical) {
        for (const p of s.points) g += `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="4" fill="var(${s.color})" stroke="var(--surface)" stroke-width="2"/>`;
      } else {
        const p = s.points[s.points.length - 1];
        g += `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="4" fill="var(${s.color})" stroke="var(--surface)" stroke-width="2"/>`;
      }
    }
    g += `<line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
    g += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${g}</svg><div class="tip" hidden></div>`;

    const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross");
    const show = (ev) => {
      const box = svg.getBoundingClientRect();
      const px = (ev.clientX - box.left) * (W / box.width);
      let best = xs[0];
      for (const x of xs) if (Math.abs(X(x) - px) < Math.abs(X(best) - px)) best = x;
      cross.setAttribute("x1", X(best)); cross.setAttribute("x2", X(best)); cross.setAttribute("visibility", "visible");
      const rows = series.map((s) => {
        let near = null;
        for (const p of s.points) if (p[0] <= best) near = p;
        if (o.categorical) near = s.points.find((p) => p[0] === best) || null;
        return near ? `<div class="tip-row"><span><i style="background:var(${s.color})"></i>${s.name}</span><b>${yFmt(near[1])}</b></div>` : "";
      }).join("");
      tip.innerHTML = `<div class="tip-head">${(o.tipFmt || xFmt)(best)}</div>${rows}`;
      tip.hidden = false;
      const left = X(best) / W * box.width;
      tip.style.left = `${Math.min(Math.max(left + 12, 0), Math.max(0, box.width - tip.offsetWidth))}px`;
    };
    const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); };
    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", hide);
  },
};
