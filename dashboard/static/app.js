"use strict";
/* MiniArith optimizer dashboard - vanilla JS, no external dependencies.
   Every number shown comes from /api/* (generated artifacts or live compiler calls). */

const $ = (sel, root = document) => root.querySelector(sel);
const main = () => document.getElementById("main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 3) => (x === null || x === undefined || x === "" || Number.isNaN(+x)) ? "–" : (+x).toFixed(d);
const fmtInt = (x) => (x === null || x === undefined || x === "") ? "–" : Math.round(+x).toLocaleString("en-US");
const pct = (x, d = 1) => (x === null || x === undefined) ? "–" : `${(+x).toFixed(d)}%`;
const signedPct = (x, d = 1) => (x === null || x === undefined) ? "–" : `${+x > 0 ? "+" : ""}${(+x).toFixed(d)}%`;
const ns = (x) => x === null || x === undefined ? "–" : (+x >= 1e6 ? `${(x / 1e6).toFixed(2)} ms` : +x >= 1e3 ? `${(x / 1e3).toFixed(1)} µs` : `${(+x).toFixed(0)} ns`);

const STRATS = [
  { key: "unoptimized", name: "Unoptimized", color: "var(--series-base)" },
  { key: "greedy", name: "Greedy", color: "var(--series-greedy)" },
  { key: "fixed", name: "Fixed pipeline", color: "var(--series-fixed)" },
  { key: "beam", name: "Beam (w=4)", color: "var(--series-beam)" },
  { key: "greedy_noM8", name: "Greedy without M8", color: "var(--series-nom8)" },
];
const STRAT = Object.fromEntries(STRATS.map((s) => [s.key, s]));
const GROUP_NAMES = { controlled: "A · Controlled", randomized: "B · Randomized", external: "C · External" };
const byGroupOrder = (keys) => [...keys].sort((a, b) => Object.keys(GROUP_NAMES).indexOf(a) - Object.keys(GROUP_NAMES).indexOf(b));

/* ---------- data access ---------- */
const cache = {};
async function api(path, opts) {
  if (!opts && cache[path]) return cache[path];
  const res = await fetch(path, opts);
  let body;
  try { body = await res.json(); } catch (e) { throw new Error(`${path}: invalid response (${res.status})`); }
  if (!res.ok && !(body && body.ok === false)) throw new Error(body && body.error ? body.error : `${path}: HTTP ${res.status}`);
  if (!opts) cache[path] = body;
  return body;
}

/* ---------- tooltip ---------- */
const tip = () => document.getElementById("tooltip");
function showTip(ev, html) {
  const t = tip();
  t.innerHTML = html;
  t.style.display = "block";
  const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
function hideTip() { tip().style.display = "none"; }
function bindTips(root) {
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("mousemove", (ev) => showTip(ev, el.getAttribute("data-tip")));
    el.addEventListener("mouseleave", hideTip);
  });
}

/* ---------- charts (SVG) ---------- */
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function ticks(max, n = 4) { return Array.from({ length: n + 1 }, (_, i) => (max * i) / n); }
function roundedTopRect(x, y, w, h, r = 4) {
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function roundedRightRect(x, y, w, h, r = 4) {
  if (w <= 0) return "";
  r = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}
function legendHTML(series) {
  return `<div class="legend">${series.map((s) => `<span><i class="swatch" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}

/** Vertical grouped columns. series: [{key,name,color}], cats: [{key,label}], get(cat,ser)->number|null */
function groupedColumns({ cats, series, get, format = (v) => fmt(v, 3), yMax, yLabel = "", height = 260, tipExtra, width = 720 }) {
  const W = width, H = height, L = 48, R = 10, T = 14, B = 46;
  const vals = cats.flatMap((c) => series.map((s) => get(c, s))).filter((v) => v !== null && v !== undefined);
  const max = yMax ?? niceMax(Math.max(...vals, 0));
  const plotW = W - L - R, plotH = H - T - B;
  const band = plotW / cats.length;
  const barW = Math.min(24, (band * 0.8) / series.length - 2);
  const groupW = series.length * (barW + 2) - 2;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(yLabel)}"><g class="grid">`;
  for (const t of ticks(max)) {
    const y = T + plotH - (t / max) * plotH;
    svg += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${esc(format(t))}</text>`;
  }
  svg += `</g>`;
  cats.forEach((c, i) => {
    const x0 = L + i * band + (band - groupW) / 2;
    series.forEach((s, j) => {
      const v = get(c, s);
      if (v === null || v === undefined) return;
      const h = Math.max(0, (v / max) * plotH), x = x0 + j * (barW + 2), y = T + plotH - h;
      const t = `<b>${esc(c.label)}</b><br>${esc(s.name)}: ${esc(format(v))}${tipExtra ? tipExtra(c, s) : ""}`;
      svg += `<path class="mark" d="${roundedTopRect(x, y, barW, h)}" fill="${s.color}"/>`;
      svg += `<rect class="hit" x="${x - 1}" y="${T}" width="${barW + 2}" height="${plotH}" data-tip="${esc(t)}"/>`;
    });
    const maxChars = Math.max(4, Math.floor(band / 6.2));
    const lbl = c.label.length > maxChars ? c.label.slice(0, maxChars - 1) + "…" : c.label;
    svg += `<text x="${L + i * band + band / 2}" y="${H - B + 16}" text-anchor="middle">${esc(lbl)}</text>`;
  });
  svg += `<line class="baseline" x1="${L}" x2="${W - R}" y1="${T + plotH}" y2="${T + plotH}"/>`;
  if (yLabel) svg += `<text class="axis-label" x="${L}" y="${H - 6}">${esc(yLabel)}</text>`;
  svg += `</svg>`;
  return `${series.length > 1 ? legendHTML(series) : ""}<div class="chart">${svg}</div>`;
}

/** Horizontal bars, one series. rows: [{label, value, color?, tip?}] */
function hBars({ rows, format = (v) => fmtInt(v), max, color = "var(--accent)", label = "" }) {
  const W = 720, rowH = 26, L = 170, R = 70, T = 6;
  const H = T + rows.length * rowH + 6;
  const m = max ?? niceMax(Math.max(...rows.map((r) => r.value), 0));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">`;
  rows.forEach((r, i) => {
    const y = T + i * rowH, w = Math.max(0, ((W - L - R) * r.value) / m), bh = 16;
    svg += `<text x="${L - 8}" y="${y + bh - 3}" text-anchor="end">${esc(r.label)}</text>`;
    svg += `<path class="mark" d="${roundedRightRect(L, y, w, bh)}" fill="${r.color || color}"/>`;
    svg += `<text class="value-label" x="${L + w + 6}" y="${y + bh - 3}">${esc(format(r.value))}</text>`;
    svg += `<rect class="hit" x="${L}" y="${y - 3}" width="${W - L}" height="${rowH}" data-tip="${esc(r.tip || `<b>${r.label}</b>: ${format(r.value)}`)}"/>`;
  });
  svg += `<line class="baseline" x1="${L}" x2="${L}" y1="${T - 2}" y2="${H - 4}"/></svg>`;
  return `<div class="chart">${svg}</div>`;
}

function histogramData(values, lo, hi, nb) {
  const w = (hi - lo) / nb, counts = new Array(nb).fill(0);
  for (const v of values) counts[Math.min(nb - 1, Math.max(0, Math.floor((v - lo) / w)))]++;
  return counts.map((c, i) => ({ key: i, label: `${(lo + i * w).toFixed(1)}–${(lo + (i + 1) * w).toFixed(1)}`, count: c }));
}

/* ---------- small UI helpers ---------- */
function tile(label, value, note = "", extra = "") {
  return `<div class="card tile ${extra}"><div class="label">${esc(label)}</div><div class="value">${value}</div>${note ? `<div class="note">${note}</div>` : ""}</div>`;
}
function badge(text, kind = "") { return `<span class="badge ${kind}"><span class="dot"></span>${esc(text)}</span>`; }
function errorBox(msg) { return `<div class="callout err" role="alert">${esc(msg)}</div>`; }
function table(headers, rows, opts = {}) {
  const th = headers.map((h) => `<th class="${h.num ? "num" : ""}">${esc(h.label)}</th>`).join("");
  const body = rows.length ? rows.map((r) => `<tr ${r._attrs || ""}>${headers.map((h) => `<td class="${h.num ? "num" : ""}">${h.html ? h.html(r) : esc(h.get ? h.get(r) : r[h.key])}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${headers.length}" class="muted">${esc(opts.empty || "No rows")}</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function codeBlock(lines, opts = {}) {
  const marks = opts.marks || {};
  return `<div class="code" role="region" aria-label="${esc(opts.label || "code")}">${lines.map((l, i) => {
    const cls = marks[i] || (/^label /.test(l) ? "label" : "");
    return `<div class="ln ${cls}"><span class="no">${i}</span><span>${esc(l)}</span></div>`;
  }).join("") || '<div class="ln muted"><span class="no"></span><span>(empty program)</span></div>'}</div>`;
}
function stratTag(key) {
  const s = STRAT[key] || { name: key, color: "var(--text-muted)" };
  return `<span style="display:inline-flex;align-items:center;gap:6px"><i class="swatch" style="background:${s.color}"></i>${esc(s.name)}</span>`;
}
function download(path, label) { return `<a href="${path}" download>${esc(label)}</a>`; }

/* ========== PAGE: OVERVIEW ========== */
async function pageOverview(el) {
  const ov = await api("/api/overview");
  const st = ov.dataset_statistics, ex = ov.experiments;
  if (!st) { el.innerHTML = errorBox("Dataset not built yet. Run: python -m benchmarks.build"); return; }
  const tot = st.totals;
  const g = ex && ex.overall && ex.overall.greedy;
  const tim = ex && ex.timing && ex.timing.interp && ex.timing.interp.per_strategy && ex.timing.interp.per_strategy.greedy;
  const ve = ov.validator_eval;
  let html = `<div class="page-head"><div><h1>Mutation-based compiler optimizer</h1>
    <p class="lede">A MiniArith compiler produces naive three-address code (TAC). Rewrite rules (“mutations”) propose changes,
    a differential-testing validator rejects any candidate whose printed output or runtime error differs from the original,
    and a search strategy keeps the cheapest valid program under a weighted cost model.</p></div></div>
    <div class="grid g4">
      ${tile("Accepted benchmark programs", fmtInt(tot.accepted), `of ${fmtInt(tot.candidates)} candidate records · counted by the validator`, "hero")}
      ${tile("Correct optimized results", ex ? `${fmtInt(ex.correctness.correct_rows)} / ${fmtInt(ex.correctness.rows)}` : "–",
        ex ? `(program, strategy) pairs matching the AST reference on fixture + ${ex.config.heldout_inputs} held-out inputs` : "run experiments to populate")}
      ${tile("Greedy mean cost reduction", g ? pct(g.mean_cost_reduction_pct) : "–", `${badge("simulated cost model", "sim")} weighted, original = 1.0`)}
      ${tile("Greedy measured speed-up", tim ? `${fmt(tim.geomean_speedup_vs_unoptimized, 2)}×` : "–",
        tim ? `${badge("measured", "ok")} geometric mean vs naive TAC, Python TAC interpreter` : "timing not run")}
    </div>`;

  // dataset composition
  const subRows = st.by_category.reduce((acc, c) => { const k = `${c.group}/${c.subgroup}`; acc[k] = (acc[k] || 0) + c.accepted; return acc; }, {});
  const comp = Object.entries(subRows).map(([k, v]) => ({ label: k.replace("/", " · "), value: v }));
  html += `<div class="grid g2 section">
    <div class="card"><div class="card-head"><h2>Dataset composition</h2><span class="muted small">accepted programs by group · subgroup</span></div>
      ${hBars({ rows: comp, label: "accepted programs by subgroup" })}
      <p class="card-sub">Rejected ${fmtInt(tot.rejected)} · duplicates ${fmtInt(tot.duplicate)} · incompatible external ${fmtInt(tot.incompatible)}.
      Groups are always reported separately. <a href="#/dataset">Explore the dataset →</a></p></div>`;

  if (ex && ex.by_group) {
    const groups = byGroupOrder(Object.keys(ex.by_group.greedy || {}));
    const series = ["fixed", "greedy", "beam"].map((k) => STRAT[k]);
    html += `<div class="card"><div class="card-head"><h2>Mean final cost by group</h2>${badge("simulated cost", "sim")}</div>
      ${groupedColumns({ cats: groups.map((x) => ({ key: x, label: GROUP_NAMES[x] || x })), series,
        get: (c, s) => ex.by_group[s.key] && ex.by_group[s.key][c.key] ? ex.by_group[s.key][c.key].mean_final_cost : null,
        yMax: 1, format: (v) => fmt(v, 2), yLabel: "mean final cost (unoptimized = 1.0, lower is better)",
        tipExtra: (c, s) => `<br>programs: ${ex.by_group[s.key][c.key].programs}` })}</div>`;
  } else {
    html += `<div class="card"><h2>Experiment results</h2><div class="empty">No experiment results found in results/final/. Run <code>python -m experiments.run_experiments</code>.</div></div>`;
  }
  html += `</div>`;

  if (ex) {
    const cmp = (ex.comparisons || []).filter((c) => c.metric === "final_cost");
    html += `<div class="grid g2 section"><div class="card"><div class="card-head"><h2>Head-to-head (per program)</h2>${badge("simulated cost", "sim")}</div>
      ${table([{ label: "Comparison", html: (c) => `${stratTag(c.a)} vs ${stratTag(c.b)}` }, { label: "better", key: "a_better", num: true },
        { label: "tie", key: "tie", num: true }, { label: "worse", key: "a_worse", num: true }, { label: "mean Δ cost", get: (c) => fmt(c.mean_difference, 4), num: true }], cmp)}
      <p class="card-sub">Counts are programs where the first strategy has lower / equal / higher final cost. Regressions are listed on the Experiments page.</p></div>
      <div class="card"><div class="card-head"><h2>Latest experiment run</h2></div>
      <dl class="kv">
        <dt>Dataset</dt><dd>${esc(ex.dataset.manifest)} · ${fmtInt(ex.dataset.programs)} programs</dd>
        <dt>Manifest SHA-256</dt><dd class="mono">${esc((ex.dataset.manifest_sha256 || "").slice(0, 16))}…</dd>
        <dt>Finished</dt><dd>${new Date(ex.results_mtime * 1000).toLocaleString()}</dd>
        <dt>Python</dt><dd>${esc(ex.environment.python)} (${esc(ex.environment.implementation)}) · ${esc(ex.environment.platform)}</dd>
        <dt>Git commit</dt><dd class="mono">${esc((ex.environment.commit || "–").slice(0, 10))}${ex.environment.dirty_worktree ? " (uncommitted changes)" : ""}</dd>
        <dt>Validation</dt><dd>${esc(ex.config.validation_cases)} seeded vectors + structured vectors (${esc(ex.config.validation_protocol)})</dd>
        <dt>Wall time</dt><dd>${fmt(ex.seconds.total, 0)} s total</dd>
      </dl>
      ${ve ? `<div class="callout section">Validator self-test: ${fmtInt(ve.v2.totals.rejected)} of ${fmtInt(ve.v2.totals.rejected + (ve.v2.totals.accepted_but_different || 0))}
        non-equivalent mutants rejected (${pct(100 * ve.v2.detection_rate_on_nonequivalent, 2)}); ${fmtInt(ve.v2.totals.accepted_but_different || 0)} missed.
        Differential testing is not a proof.</div>` : ""}
      </div></div>`;
  }
  if (ov.verification) {
    const v = ov.verification;
    html += `<div class="card section"><div class="card-head"><h2>Reproducibility pipeline</h2>${badge(v.passed ? "all checks passed" : "checks failed", v.passed ? "ok" : "bad")}</div>
      ${table([{ label: "Step", key: "name" }, { label: "Status", html: (s) => badge(s.status, s.status === "passed" ? "ok" : s.status === "skipped" ? "warn" : "bad") },
        { label: "Seconds", get: (s) => fmt(s.seconds, 1), num: true }, { label: "Detail", get: (s) => s.detail || "" }], v.steps)}
      <p class="card-sub">From <code>results/final/verification_summary.json</code>, written by <code>python reproduce.py</code>.</p></div>`;
  }
  if (ov.plots && ov.plots.length) {
    html += `<div class="card section"><div class="card-head"><h2>Generated plots</h2><span class="muted small">results/final/plots · regenerated from saved logs</span></div>
      <div class="gallery">${ov.plots.map((p) => `<a href="/files/plots/${encodeURIComponent(p)}" target="_blank" rel="noopener"><img src="/files/plots/${encodeURIComponent(p)}" alt="${esc(p)}" loading="lazy"><span>${esc(p)}</span></a>`).join("")}</div></div>`;
  }
  el.innerHTML = html;
}

/* ========== PAGE: COMPILER ========== */
let compilerState = { source: null, inputs: "", strategies: ["fixed", "greedy", "beam"], weights: "baseline", unsafe: false, result: null, tab: "greedy" };

async function pageCompiler(el, params) {
  const samples = await api("/api/samples");
  if (compilerState.source === null) compilerState.source = samples.samples[0].source;
  el.innerHTML = `<div class="page-head"><div><h1>Interactive compiler</h1>
    <p class="lede">Compile MiniArith to TAC and optimize it with the real optimizer. Inputs are optional: each line is one input vector
    (space- or comma-separated integers). The program is always checked on validator vectors too.</p></div></div>
    <div class="split">
      <div class="card">
        <div class="toolbar" style="margin-bottom:10px">
          <label class="field" style="flex:1">Example program
            <select id="sample">${samples.samples.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join("")}</select></label>
          <label class="field">Cost weights
            <select id="weights">${samples.weights.map((w) => `<option ${w === compilerState.weights ? "selected" : ""}>${esc(w)}</option>`).join("")}</select></label>
        </div>
        <label class="field">MiniArith source
          <textarea id="source" rows="13" spellcheck="false" aria-describedby="lang-help">${esc(compilerState.source)}</textarea></label>
        <p class="card-sub" id="lang-help">Statements: <code>input a, b;</code> <code>x = expr;</code> <code>print expr;</code>
          <code>if (a &lt; b) { … } else { … }</code>. Operators <code>+ - * / %</code> (C truncation), unary minus, <code># comments</code>.</p>
      </div>
      <div class="card">
        <label class="field">Input vectors (optional)
          <textarea id="inputs" rows="5" spellcheck="false" placeholder="3 4&#10;-7 2&#10;5 0">${esc(compilerState.inputs)}</textarea></label>
        <div class="checks" style="margin:12px 0" role="group" aria-label="Strategies">
          ${["fixed", "greedy", "beam"].map((k) => `<label><input type="checkbox" name="strat" value="${k}" ${compilerState.strategies.includes(k) ? "checked" : ""}> ${stratTag(k)}</label>`).join("")}
        </div>
        <div class="checks" style="margin-bottom:12px"><label><input type="checkbox" id="unsafe" ${compilerState.unsafe ? "checked" : ""}>
          Add the unsafe operator MX (shows the validator rejecting wrong candidates)</label></div>
        <div class="toolbar"><button class="btn" id="run" type="button">Compile &amp; optimize</button>
          <span class="muted small" id="run-status" role="status" aria-live="polite"></span></div>
        <div class="callout section small">Limits: ${"≤ 6000 characters, ≤ 160 TAC instructions, ≤ 20 input vectors."} Requests only ever reach the
          MiniArith parser; no code is executed.</div>
      </div>
    </div>
    <div id="compile-out"></div>`;
  $("#sample").addEventListener("change", (e) => { $("#source").value = samples.samples[+e.target.value].source; });
  $("#run").addEventListener("click", runCompile);
  $("#source").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runCompile(); });
  if (compilerState.result) renderCompile(compilerState.result);
  else if (params && params.get("run")) runCompile();
}

async function runCompile() {
  const btn = $("#run"), status = $("#run-status"), out = $("#compile-out");
  compilerState.source = $("#source").value;
  compilerState.inputs = $("#inputs").value;
  compilerState.strategies = [...document.querySelectorAll('input[name="strat"]:checked')].map((x) => x.value);
  compilerState.weights = $("#weights").value;
  compilerState.unsafe = $("#unsafe").checked;
  if (!compilerState.strategies.length) { out.innerHTML = errorBox("Select at least one strategy."); return; }
  btn.disabled = true; status.textContent = "Optimizing…";
  try {
    const r = await api("/api/compile", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: compilerState.source, inputs: compilerState.inputs, strategies: compilerState.strategies, weights: compilerState.weights, unsafe: compilerState.unsafe }) });
    if (!r.ok) { status.textContent = ""; out.innerHTML = `<div class="section">${errorBox(`${r.stage === "parse" ? "Compile error" : "Request error"}: ${r.error}`)}</div>`; compilerState.result = null; return; }
    compilerState.result = r;
    if (!r.results[compilerState.tab]) compilerState.tab = compilerState.strategies[0];
    status.textContent = `Done in ${fmt(r.seconds, 2)} s`;
    renderCompile(r);
  } catch (e) {
    status.textContent = ""; out.innerHTML = `<div class="section">${errorBox(e.message)}</div>`;
  } finally { btn.disabled = false; }
}

function renderCompile(r) {
  const out = $("#compile-out");
  const keys = Object.keys(r.results);
  const k = r.results[compilerState.tab] ? compilerState.tab : keys[0];
  const res = r.results[k];
  const allMatch = res.checks.every((c) => c.match);
  const b = res.metrics_before, a = res.metrics_after;
  const tm = r.timing && r.timing.per_strategy;
  const metricRow = (label, key, d = 0) => ({ label, before: b[key], after: a[key], d });
  const mrows = [metricRow("TAC instructions", "instructions"), metricRow("Arithmetic operations", "arith_ops"), metricRow("Distinct temporaries", "temps"),
    metricRow("Simulated cycles (mean over validation vectors)", "cycles", 1)];
  const optSet = new Set(res.optimized_tac);
  const origMarks = Object.fromEntries(r.original_tac.map((l, i) => [i, optSet.has(l) ? "" : "del"]));
  const origSet = new Set(r.original_tac);
  const optMarks = Object.fromEntries(res.optimized_tac.map((l, i) => [i, origSet.has(l) ? "" : "add"]));

  let html = `<div class="card section"><div class="tabs" role="tablist">${keys.map((x) => `<button role="tab" aria-selected="${x === k}" data-tab="${x}">${stratTag(x)} <span class="muted">${fmt(r.results[x].final_cost, 3)}</span></button>`).join("")}</div>
    <div class="grid g4" style="margin-bottom:14px">
      ${tile("Final cost", fmt(res.final_cost, 4), `${badge("simulated", "sim")} weights: ${esc(r.weights.name)}`)}
      ${tile("Transformations applied", fmtInt(res.steps.length), `${fmtInt(res.candidates_evaluated)} candidates evaluated`)}
      ${tile("Rejected by validator", fmtInt(res.candidates_rejected), res.candidates_rejected ? "candidates whose behaviour differed" : "no candidate failed validation")}
      ${tile("Correctness", allMatch ? badge("matches reference", "ok") : badge("MISMATCH", "bad"), `${res.checks.length} input vectors vs AST reference`)}
    </div>
    ${r.control_flow ? `<div class="callout" style="margin-bottom:12px">Control-flow program: ${r.blocks.length} basic blocks. Local operators run per block; M10 folds constant branches.</div>` : ""}
    <div class="split"><div><h3>Original TAC <span class="muted small">(${r.original_tac.length} lines)</span></h3>${codeBlock(r.original_tac, { marks: origMarks, label: "original TAC" })}</div>
      <div><h3>Optimized TAC · ${esc((STRAT[k] || {}).name || k)} <span class="muted small">(${res.optimized_tac.length} lines)</span></h3>${codeBlock(res.optimized_tac, { marks: optMarks, label: "optimized TAC" })}</div></div>
    <p class="card-sub" style="margin-top:8px">Shaded lines: removed from (left) or new in (right) the program, compared as text.</p></div>`;

  html += `<div class="grid g2 section"><div class="card"><div class="card-head"><h2>Before and after</h2></div>
    ${table([{ label: "Metric", key: "label" }, { label: "Before", get: (m) => fmt(m.before, m.d), num: true }, { label: "After", get: (m) => fmt(m.after, m.d), num: true },
      { label: "Change", get: (m) => signedPct(m.before ? ((m.after - m.before) / m.before) * 100 : 0), num: true }], mrows)}
    ${tm ? `<h3 style="margin-top:14px">Measured time ${badge("measured", "ok")}</h3>
      ${table([{ label: "Program", html: (x) => stratTag(x.k) }, { label: "Median / execution", get: (x) => ns(x.median_ns), num: true }, { label: "IQR", get: (x) => ns(x.iqr_ns), num: true }],
        Object.entries(tm).map(([kk, v]) => ({ k: kk, ...v })))}
      <p class="card-sub">${esc(r.timing.backend)}, batch of ${r.timing.batch_size} input vectors, ${r.timing.repeats} interleaved repeats. Small differences are within noise.</p>` : ""}
    </div>
    <div class="card"><div class="card-head"><h2>Transformation trace</h2><span class="muted small">accepted mutations, in order</span></div>
      ${res.steps.length ? `<ol class="steps">${res.steps.map((s) => `<li><span class="op">${esc(s.op_id)}</span> ${esc(r.operator_names[s.op_id] || "")} — <span class="mono">${esc(s.description)}</span> <span class="muted">cost ${fmt(s.cost, 4)}</span></li>`).join("")}</ol>` : `<div class="empty">No improving transformation found.</div>`}
    </div></div>`;

  if (res.iterations) {
    html += `<div class="card section"><div class="card-head"><h2>Greedy search: candidates per iteration</h2><span class="muted small">hover a column for valid / improving counts</span></div>
      ${groupedColumns({ cats: res.iterations.map((it) => ({ key: it.iteration, label: String(it.iteration), it })), series: [{ key: "e", name: "evaluated", color: "var(--series-greedy)" }],
        get: (c) => c.it.evaluated, format: (v) => fmtInt(v), yLabel: "candidates evaluated per iteration", height: 200, width: 1100,
        tipExtra: (c) => `<br>valid: ${c.it.valid}<br>improving: ${c.it.improving}` })}
      ${res.rejected_candidates && res.rejected_candidates.length ? `<h3 style="margin-top:12px">Rejected candidates (${fmtInt(res.candidates_rejected)})</h3>
        ${table([{ label: "Iter", key: "iteration", num: true }, { label: "Operator", key: "op_id" }, { label: "Candidate", html: (x) => `<span class="mono">${esc(x.description)}</span>` }], res.rejected_candidates.slice(0, 30))}
        ${res.rejected_candidates.length > 30 ? `<p class="card-sub">Showing 30 of ${res.rejected_candidates.length}.</p>` : ""}` : ""}
    </div>`;
  }

  html += `<div class="card section"><div class="card-head"><h2>Correctness check</h2>${allMatch ? badge("all match", "ok") : badge("mismatch found", "bad")}</div>
    ${table([{ label: "Inputs", html: (c) => `<span class="mono">[${c.inputs.join(", ")}]</span>${c.user ? " " + badge("yours") : ""}` },
      { label: "Reference (AST)", html: (c) => obs(c.reference) }, { label: "Original TAC", html: (c) => obs(c.original) }, { label: "Optimized TAC", html: (c) => obs(c.optimized) },
      { label: "", html: (c) => c.match ? badge("match", "ok") : badge("differs", "bad") }], res.checks)}
    <p class="card-sub">${r.notes.map(esc).join(" ")}</p></div>`;
  out.innerHTML = html;
  out.querySelectorAll("[data-tab]").forEach((b2) => b2.addEventListener("click", () => { compilerState.tab = b2.dataset.tab; renderCompile(r); }));
  bindTips(out);
}
function obs(o) { return `<span class="mono">${esc(o.outputs.join(", ") || "∅")}</span>${o.error ? ` <span class="badge bad">${esc(o.error)}</span>` : ""}`; }

/* ========== PAGE: DATASET ========== */
let dsState = { q: "", group: "", status: "accepted", category: "", sort: "id", dir: 1, page: 0, selected: null };
async function pageDataset(el) {
  const [ds, ov] = await Promise.all([api("/api/dataset"), api("/api/overview")]);
  const recs = ds.records;
  const cats = [...new Set(recs.map((r) => r.category))].sort();
  const st = ov.dataset_statistics;
  el.innerHTML = `<div class="page-head"><div><h1>Dataset explorer</h1>
    <p class="lede">Release <code>miniarith-v1</code>: one record per candidate, whether accepted, rejected, duplicate or incompatible.
    Expected outputs come from the AST reference interpreter.</p></div>
    <div class="small">Download: ${download("/files/dataset/manifest.jsonl", "manifest.jsonl")} · ${download("/files/dataset/statistics.json", "statistics.json")} ·
      ${download("/files/dataset/statistics_by_category.csv", "by_category.csv")} · ${download("/files/dataset/statistics_rejections.csv", "rejections.csv")} ·
      ${download("/files/dataset/dedup_report.json", "dedup_report.json")}</div></div>
    <div class="grid g4">${["accepted", "rejected", "duplicate", "incompatible"].map((s) => tile(s[0].toUpperCase() + s.slice(1), fmtInt(st.totals[s]),
      s === "accepted" ? "usable benchmarks" : s === "incompatible" ? "external C outside the supported subset" : s === "duplicate" ? "kept, marked, not counted" : "failed a validation rule")).join("")}</div>
    <div class="card section">
      <div class="toolbar" style="margin-bottom:12px">
        <label class="field" style="flex:2;min-width:180px">Search (id, origin, reason, hash)<input type="search" id="ds-q" value="${esc(dsState.q)}" placeholder="e.g. SYN-CSE, always-fails, unsigned"></label>
        <label class="field">Group<select id="ds-group"><option value="">All</option>${Object.keys(GROUP_NAMES).map((g) => `<option value="${g}" ${dsState.group === g ? "selected" : ""}>${GROUP_NAMES[g]}</option>`).join("")}</select></label>
        <label class="field">Status<select id="ds-status"><option value="">All</option>${["accepted", "rejected", "duplicate", "incompatible"].map((s) => `<option ${dsState.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label class="field">Category<select id="ds-cat"><option value="">All</option>${cats.map((c) => `<option ${dsState.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
      </div>
      <div id="ds-table"></div>
    </div>
    <div id="ds-detail" class="drawer"></div>`;
  const rerender = () => renderDsTable(recs);
  $("#ds-q").addEventListener("input", (e) => { dsState.q = e.target.value; dsState.page = 0; rerender(); });
  $("#ds-group").addEventListener("change", (e) => { dsState.group = e.target.value; dsState.page = 0; rerender(); });
  $("#ds-status").addEventListener("change", (e) => { dsState.status = e.target.value; dsState.page = 0; rerender(); });
  $("#ds-cat").addEventListener("change", (e) => { dsState.category = e.target.value; dsState.page = 0; rerender(); });
  rerender();
  if (dsState.selected) showRecord(dsState.selected);
}
const DS_COLS = [
  { key: "id", label: "ID" }, { key: "status", label: "Status" }, { key: "group", label: "Group" }, { key: "category", label: "Category" },
  { key: "n_inputs", label: "Inputs", num: true }, { key: "n_tac", label: "TAC", num: true }, { key: "n_tests", label: "Tests", num: true },
  { key: "license", label: "License" }, { key: "reason", label: "Reason / note" },
];
function statusBadge(s) { return badge(s, s === "accepted" ? "ok" : s === "rejected" ? "bad" : "warn"); }
function renderDsTable(recs) {
  const q = dsState.q.trim().toLowerCase();
  let rows = recs.filter((r) => (!dsState.group || r.group === dsState.group) && (!dsState.status || r.status === dsState.status)
    && (!dsState.category || r.category === dsState.category)
    && (!q || [r.id, r.origin, r.reason, r.source_sha256, r.canonical_tac_sha256, r.seed, r.subgroup, r.duplicate_of].some((x) => String(x ?? "").toLowerCase().includes(q))));
  const k = dsState.sort, d = dsState.dir;
  rows.sort((a, b) => { const x = a[k] ?? "", y = b[k] ?? ""; return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * d; });
  const per = 40, pages = Math.max(1, Math.ceil(rows.length / per));
  dsState.page = Math.min(dsState.page, pages - 1);
  const slice = rows.slice(dsState.page * per, (dsState.page + 1) * per);
  const th = DS_COLS.map((c) => `<th class="sortable ${c.num ? "num" : ""}" tabindex="0" data-k="${c.key}" aria-sort="${k === c.key ? (d > 0 ? "ascending" : "descending") : "none"}">${esc(c.label)}${k === c.key ? (d > 0 ? " ↑" : " ↓") : ""}</th>`).join("");
  const body = slice.map((r) => `<tr class="clickable ${dsState.selected === r.id ? "selected" : ""}" tabindex="0" data-id="${esc(r.id)}">
    <td class="mono">${esc(r.id)}${r.review1 ? " " + badge("Review-1", "sim") : ""}</td><td>${statusBadge(r.status)}</td><td>${esc(GROUP_NAMES[r.group] || r.group)}</td>
    <td>${esc(r.category)}</td><td class="num">${r.n_inputs ?? "–"}</td><td class="num">${r.n_tac ?? "–"}</td><td class="num">${r.n_tests || "–"}${r.error_tests ? ` <span class="muted">(${r.error_tests} err)</span>` : ""}</td>
    <td>${esc(r.license)}</td><td class="small">${esc(r.duplicate_of ? `duplicate of ${r.duplicate_of}` : r.reason || "")}</td></tr>`).join("");
  $("#ds-table").innerHTML = `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${body || `<tr><td colspan="${DS_COLS.length}" class="muted">No records match these filters.</td></tr>`}</tbody></table></div>
    <div class="pager"><span>${fmtInt(rows.length)} records</span><button class="icon-btn" id="pg-prev" ${dsState.page === 0 ? "disabled" : ""}>Previous</button>
    <span>page ${dsState.page + 1} / ${pages}</span><button class="icon-btn" id="pg-next" ${dsState.page >= pages - 1 ? "disabled" : ""}>Next</button></div>`;
  document.querySelectorAll("#ds-table th.sortable").forEach((h) => {
    const act = () => { if (dsState.sort === h.dataset.k) dsState.dir *= -1; else { dsState.sort = h.dataset.k; dsState.dir = 1; } renderDsTable(recs); };
    h.addEventListener("click", act); h.addEventListener("keydown", (e) => { if (e.key === "Enter") act(); });
  });
  document.querySelectorAll("#ds-table tr[data-id]").forEach((tr) => {
    const act = () => { dsState.selected = tr.dataset.id; renderDsTable(recs); showRecord(tr.dataset.id); };
    tr.addEventListener("click", act); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") act(); });
  });
  $("#pg-prev").addEventListener("click", () => { dsState.page--; renderDsTable(recs); });
  $("#pg-next").addEventListener("click", () => { dsState.page++; renderDsTable(recs); });
}
async function showRecord(id) {
  const box = $("#ds-detail");
  box.innerHTML = `<div class="card loading">Loading ${esc(id)}…</div>`;
  let r;
  try { r = await api(`/api/dataset/${encodeURIComponent(id)}`); } catch (e) { box.innerHTML = errorBox(e.message); return; }
  const o = r.origin || {}, lic = r.license || {}, v = r.validation || {};
  const tests = r.tests || [];
  box.innerHTML = `<div class="card"><div class="card-head"><h2>${esc(r.id)} ${statusBadge(r.status)}</h2>
    <span class="small">${r.status === "accepted" ? `<a href="#/compiler" id="open-in-compiler">Open in compiler →</a> · <a href="#/experiments?program=${encodeURIComponent(r.id)}">Results →</a>` : ""}</span></div>
    <div class="grid g2">
      <dl class="kv"><dt>Group</dt><dd>${esc(GROUP_NAMES[r.group] || r.group)} · ${esc(r.subgroup)}</dd><dt>Category</dt><dd>${esc(r.category)}</dd>
        <dt>Review-1 member</dt><dd>${r.review1_member ? "yes (unchanged)" : "no"}</dd>
        <dt>Origin</dt><dd>${esc(o.generator || o.collection || "")} ${o.generator_version ? `v${esc(o.generator_version)}` : ""}</dd>
        ${o.seed !== undefined ? `<dt>Seed</dt><dd class="mono">${esc(o.seed)}</dd>` : ""}
        ${o.config ? `<dt>Generator config</dt><dd class="mono small">${esc(JSON.stringify(o.config)).slice(0, 400)}</dd>` : ""}
        ${o.local_file ? `<dt>Upstream file</dt><dd class="mono small">${esc(o.local_file)}</dd><dt>Function</dt><dd class="mono">${esc(o.function)}</dd><dt>Collection</dt><dd><a href="${esc(o.collection_url)}" target="_blank" rel="noopener">${esc(o.collection)}</a></dd>` : ""}
        <dt>Attribution</dt><dd class="small">${esc(r.attribution)}</dd>
      </dl>
      <dl class="kv"><dt>License status</dt><dd>${badge(lic.status, lic.status === "unverified" ? "warn" : "")}</dd>
        ${lic.collection_license ? `<dt>Collection license</dt><dd>${esc(lic.collection_license)} (${esc(lic.collection_license_status)})</dd><dt>Upstream function</dt><dd>${esc(lic.upstream_function_license)}</dd>` : `<dt>License</dt><dd class="small">${esc(lic.license || "")}</dd>`}
        <dt>Validation</dt><dd>${esc(v.status)}${v.reason ? ` — ${esc(v.reason)}` : ""}${v.message ? `<br><span class="small muted">${esc(v.message)}</span>` : ""}</dd>
        ${Object.keys(v.checks || {}).length ? `<dt>Checks</dt><dd class="small">${Object.entries(v.checks).map(([kk, vv]) => `${esc(kk)}: <b>${esc(vv)}</b>`).join("<br>")}</dd>` : ""}
        ${r.source_sha256 ? `<dt>Source SHA-256</dt><dd class="mono small">${esc(r.source_sha256)}</dd><dt>Canonical TAC SHA-256</dt><dd class="mono small">${esc(r.canonical_tac_sha256)}</dd>` : ""}
        ${r.int32_safe_on_tests !== undefined ? `<dt>int32-safe on tests</dt><dd>${r.int32_safe_on_tests ? "yes" : "no"}</dd>` : ""}
      </dl></div>
    ${r.source ? `<div class="split section"><div><h3>Source</h3><pre class="block">${esc(r.source)}</pre></div><div><h3>Naive TAC (${r.n_tac_instructions} instructions)</h3>${codeBlock((r.tac || "").split("\n"), { label: "TAC" })}</div></div>` : ""}
    ${o.upstream_function_source ? `<h3 class="section">Upstream C function (verbatim)</h3><pre class="block">${esc(o.upstream_function_source)}</pre>` : ""}
    ${r.conversion ? `<h3 class="section">Conversion</h3><ol class="small">${r.conversion.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      <p class="small"><b>Limitations:</b> ${r.conversion.limitations.map(esc).join("; ")}. <b>gcc cross-check:</b> ${esc(r.conversion.crosscheck.status)}
      (${esc(r.conversion.crosscheck.harness_vectors_checked)} vectors)${r.conversion.crosscheck.upstream_file_note ? ` — full upstream file: ${esc(r.conversion.crosscheck.upstream_file_note)}` : ""}</p>` : ""}
    ${tests.length ? `<h3 class="section">Tests (expected outputs from the AST reference)</h3>
      ${table([{ label: "Inputs", html: (t) => `<span class="mono">[${t.inputs.join(", ")}]</span>` }, { label: "Expected outputs", html: (t) => `<span class="mono">${esc(t.expected_outputs.join(", ") || "∅")}</span>` },
        { label: "Expected error", html: (t) => t.expected_error ? badge(t.expected_error, "bad") : "–" }], tests)}` : ""}
  </div>`;
  const link = $("#open-in-compiler");
  if (link) link.addEventListener("click", () => { compilerState.source = r.source; compilerState.inputs = tests.slice(0, 4).map((t) => t.inputs.join(" ")).join("\n"); compilerState.result = null; });
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ========== PAGE: EXPERIMENTS ========== */
let exState = { group: "", program: null, q: "" };
async function pageExperiments(el, params) {
  let summary;
  try { summary = await api("/api/results"); } catch (e) {
    el.innerHTML = `<h1>Experiments</h1>${errorBox(e.message)}`; return;
  }
  const rowsAll = (await api("/api/results/programs")).rows.map((r) => ({ ...r, final_cost: +r.final_cost }));
  if (params.get("program")) exState.program = params.get("program");
  const groups = byGroupOrder(Object.keys(summary.by_group.greedy || {}));
  const ov = summary.overall;
  const order = ["unoptimized", "fixed", "greedy", "beam", "greedy_noM8"].filter((k) => ov[k]);
  const files = ["summary.json", "per_program.csv", "sensitivity.csv", "timing.csv", "timing_raw.csv", "steps.jsonl", "validator_eval.json"];
  let html = `<div class="page-head"><div><h1>Experiments and results</h1>
    <p class="lede">All strategies run on the same ${fmtInt(summary.dataset.programs)} accepted programs with the same operators, validator and cost function.
    Simulated cost and measured runtime are reported separately.</p></div>
    <div class="small">Download: ${files.map((f) => download(`/files/results/${f}`, f)).join(" · ")}</div></div>
    <div class="card"><div class="card-head"><h2>Strategy summary · all groups</h2>${badge("simulated cost", "sim")}</div>
    ${table([{ label: "Strategy", html: (r) => stratTag(r.k) }, { label: "Programs", get: (r) => fmtInt(r.programs), num: true },
      { label: "Mean cost", get: (r) => fmt(r.mean_final_cost, 4), num: true }, { label: "Median", get: (r) => fmt(r.median_final_cost, 4), num: true },
      { label: "P10–P90", get: (r) => `${fmt(r.p10_final_cost, 3)}–${fmt(r.p90_final_cost, 3)}`, num: true },
      { label: "Δ instr", get: (r) => signedPct(r.mean_instructions_change_pct), num: true }, { label: "Δ arith", get: (r) => signedPct(r.mean_arith_ops_change_pct), num: true },
      { label: "Δ temps", get: (r) => signedPct(r.mean_temps_change_pct), num: true }, { label: "Δ sim. cycles", get: (r) => signedPct(r.mean_cycles_change_pct), num: true },
      { label: "Evals / program", get: (r) => fmtInt(r.mean_candidates_evaluated), num: true }, { label: "Search s", get: (r) => fmt(r.total_search_seconds, 1), num: true },
      { label: "Correct", get: (r) => `${r.correct}/${r.programs}`, num: true }], order.map((k) => ({ k, ...ov[k] })))}
    <p class="card-sub">Δ columns are mean per-program % change vs the unoptimized TAC. Correct = fixture tests + ${summary.config.heldout_inputs} held-out inputs + compiled backend all match the AST reference.</p></div>`;

  html += `<div class="card section"><div class="card-head"><h2>By dataset group</h2><label class="field" style="flex-direction:row;align-items:center;gap:8px">Group
    <select id="ex-group">${groups.map((g) => `<option value="${g}" ${exState.group === g ? "selected" : ""}>${GROUP_NAMES[g] || g}</option>`).join("")}</select></label></div><div id="ex-group-body"></div></div>`;

  // categories chart
  const catKeys = Object.keys(summary.by_category.greedy || {});
  html += `<div class="card section"><div class="card-head"><h2>Mean final cost by category</h2>${badge("simulated cost", "sim")}</div>
    ${groupedColumns({ cats: catKeys.map((c) => ({ key: c, label: c.split("/")[1] })), series: ["fixed", "greedy", "beam"].map((x) => STRAT[x]),
      get: (c, s) => summary.by_category[s.key][c.key] ? summary.by_category[s.key][c.key].mean_final_cost : null, yMax: 1, format: (v) => fmt(v, 2),
      yLabel: "mean final cost (lower is better)", width: 1100, tipExtra: (c, s) => `<br>group: ${c.key.split("/")[0]}<br>programs: ${summary.by_category[s.key][c.key].programs}` })}</div>`;

  // comparisons
  html += `<div class="card section"><div class="card-head"><h2>Per-program comparisons</h2><span class="muted small">no averaging: every program counted</span></div>
    ${table([{ label: "A vs B", html: (c) => `${stratTag(c.a)} vs ${stratTag(c.b)}` }, { label: "Metric", key: "metric" }, { label: "A better", key: "a_better", num: true },
      { label: "Tie", key: "tie", num: true }, { label: "A worse", key: "a_worse", num: true }, { label: "Mean Δ", get: (c) => fmt(c.mean_difference, 4), num: true },
      { label: "Programs where A is worse", html: (c) => c.a_worse_programs.length ? c.a_worse_programs.slice(0, 8).map((p) => `<a href="#/experiments?program=${encodeURIComponent(p)}">${esc(p)}</a>`).join(", ") + (c.a_worse_programs.length > 8 ? " …" : "") : "–" }],
      summary.comparisons)}</div>`;

  // sensitivity
  if (summary.sensitivity) {
    const sens = Object.entries(summary.sensitivity).map(([k, v]) => ({ k, ...v }));
    html += `<div class="card section"><div class="card-head"><h2>Cost-weight sensitivity</h2>${badge("simulated cost", "sim")}</div>
      ${table([{ label: "Weights", key: "k" }, { label: "Fixed mean cost", get: (s) => fmt(s.fixed_mean_cost, 4), num: true }, { label: "Greedy mean cost", get: (s) => fmt(s.greedy_mean_cost, 4), num: true },
        { label: "Greedy better / tie / worse", get: (s) => `${s.greedy_better} / ${s.tie} / ${s.greedy_worse}`, num: true },
        { label: "Greedy Δ instr", get: (s) => signedPct(s.greedy_mean_instructions_change_pct), num: true }, { label: "Greedy Δ cycles", get: (s) => signedPct(s.greedy_mean_cycles_change_pct), num: true },
        { label: "Same conclusion as baseline", html: (s) => s.direction_same_as_baseline ? badge("yes", "ok") : badge("no", "warn") }, { label: "Correct", get: (s) => `${s.correct}/${s.results}`, num: true }], sens)}
      <p class="card-sub">Weights: ${Object.entries(summary.config.weight_configs).map(([k, w]) => `<b>${esc(k)}</b> I${w.instructions}/A${w.arith_ops}/T${w.temps}/C${w.cycles}`).join(" · ")}. Costs under different weights are not comparable across rows.</p></div>`;
  }

  // timing
  if (summary.timing) {
    const t = summary.timing;
    html += `<div class="card section"><div class="card-head"><h2>Measured wall-clock time</h2>${badge("measured", "ok")}</div>
      <p class="card-sub">${esc(t.classification_rule)}. Speed-up = median time of the unoptimized TAC ÷ median time of the strategy's program, per program.</p>
      <div class="grid g2">${["interp", "compiled"].filter((b) => t[b]).map((b) => {
        const per = Object.entries(t[b].per_strategy).map(([k, v]) => ({ k, ...v }));
        return `<div><h3>${b === "interp" ? "TAC interpreter (Python)" : "TAC compiled to Python"}</h3>
          ${table([{ label: "Strategy", html: (x) => stratTag(x.k) }, { label: "Geomean speed-up", get: (x) => `${fmt(x.geomean_speedup_vs_unoptimized, 3)}×`, num: true },
            { label: "Improved / inconcl. / regressed", get: (x) => `${x.improved} / ${x.inconclusive} / ${x.regressed}`, num: true }], per)}
          <p class="card-sub">Spearman ρ (simulated cycles vs measured time): ${fmt(t[b].spearman_cycles_vs_time, 3)} · (instruction count vs time): ${fmt(t[b].spearman_instructions_vs_time, 3)}.<br>
          Reordering check: in ${t[b].reordering_check.programs} programs where M8 changed simulated cycles, the variant with fewer simulated cycles was also faster in
          ${t[b].reordering_check.agreements} (${pct(100 * (t[b].reordering_check.agreement_rate || 0), 0)}).</p></div>`;
      }).join("")}</div>
      ${t.interp ? groupedColumns({ cats: Object.keys(t.interp.median_ns_per_execution).map((k) => ({ key: k, label: (STRAT[k] || {}).name || k })), series: [{ key: "i", name: "TAC interpreter", color: "var(--series-greedy)" }, { key: "c", name: "compiled", color: "var(--series-fixed)" }],
        get: (c, s) => s.key === "i" ? t.interp.median_ns_per_execution[c.key] : (t.compiled ? t.compiled.median_ns_per_execution[c.key] : null), format: (v) => ns(v),
        yLabel: "median over programs of the per-execution median time", width: 1100, height: 240 }) : ""}</div>`;
  }

  html += `<div class="card section"><div class="card-head"><h2>Cost distribution</h2><span class="muted small">programs per final-cost bin · all groups</span></div><div id="ex-hist"></div></div>`;
  html += `<div class="card section" id="drill"><div class="card-head"><h2>Per-program drill-down</h2>
    <input type="search" id="ex-q" placeholder="Filter by program id or category" value="${esc(exState.q)}" aria-label="Filter programs"></div><div id="ex-prog-table"></div><div id="ex-prog-detail"></div></div>`;
  el.innerHTML = html;

  if (!exState.group) exState.group = groups[0];
  const renderGroup = () => {
    const bg = Object.fromEntries(order.map((k) => [k, summary.by_group[k] && summary.by_group[k][exState.group]]).filter(([, v]) => v));
    $("#ex-group-body").innerHTML = table([{ label: "Strategy", html: (r) => stratTag(r.k) }, { label: "Programs", key: "programs", num: true },
      { label: "Mean cost", get: (r) => fmt(r.mean_final_cost, 4), num: true }, { label: "Median", get: (r) => fmt(r.median_final_cost, 4), num: true },
      { label: "Improved", get: (r) => `${r.programs_improved}/${r.programs}`, num: true }, { label: "Δ instr", get: (r) => signedPct(r.mean_instructions_change_pct), num: true },
      { label: "Δ sim. cycles", get: (r) => signedPct(r.mean_cycles_change_pct), num: true }, { label: "Correct", get: (r) => `${r.correct}/${r.programs}`, num: true }],
      Object.entries(bg).map(([k, v]) => ({ k, ...v }))) +
      ((summary.comparisons_by_group || {})[exState.group] || []).map((c) => `<p class="card-sub">${esc((STRAT[c.a] || {}).name)} vs ${esc((STRAT[c.b] || {}).name)}: better on ${c.a_better}, tie ${c.tie}, worse ${c.a_worse}.</p>`).join("");
  };
  $("#ex-group").addEventListener("change", (e) => { exState.group = e.target.value; renderGroup(); });
  renderGroup();

  const series = ["fixed", "greedy", "beam"].map((k) => STRAT[k]);
  const hist = {};
  for (const s of series) hist[s.key] = histogramData(rowsAll.filter((r) => r.strategy === s.key).map((r) => r.final_cost), 0, 1, 10);
  $("#ex-hist").innerHTML = groupedColumns({ cats: hist.greedy.map((h) => ({ key: h.key, label: h.label })), series, get: (c, s) => hist[s.key][c.key].count,
    format: (v) => fmtInt(v), yLabel: "programs per final-cost bin (cost 1.0 = unoptimized)", width: 1100 });

  const byProg = {};
  for (const r of rowsAll) { (byProg[r.program_id] ||= { id: r.program_id, group: r.group, category: r.category })[r.strategy] = r; }
  const progs = Object.values(byProg);
  const renderProgTable = () => {
    const q = exState.q.trim().toLowerCase();
    const rows = progs.filter((p) => !q || p.id.toLowerCase().includes(q) || p.category.toLowerCase().includes(q)).slice(0, 60);
    $("#ex-prog-table").innerHTML = table([{ label: "Program", html: (p) => `<span class="mono">${esc(p.id)}</span>` }, { label: "Group", get: (p) => GROUP_NAMES[p.group] || p.group }, { label: "Category", key: "category" },
      ...["fixed", "greedy", "beam"].map((k) => ({ label: `${STRAT[k].name} cost`, get: (p) => p[k] ? fmt(p[k].final_cost, 3) : "–", num: true })),
      { label: "Correct", html: (p) => ["fixed", "greedy", "beam"].every((k) => p[k] && +p[k].correct === 1) ? badge("all", "ok") : badge("check", "bad") }],
      rows.map((p) => ({ ...p, _attrs: `class="clickable ${exState.program === p.id ? "selected" : ""}" tabindex="0" data-pid="${esc(p.id)}"` })));
    document.querySelectorAll("#ex-prog-table tr[data-pid]").forEach((tr) => {
      const act = () => { exState.program = tr.dataset.pid; renderProgTable(); showProgram(tr.dataset.pid); };
      tr.addEventListener("click", act); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") act(); });
    });
  };
  $("#ex-q").addEventListener("input", (e) => { exState.q = e.target.value; renderProgTable(); });
  renderProgTable();
  if (exState.program) { showProgram(exState.program); if (params.get("program")) $("#drill").scrollIntoView(); }
  bindTips(el);
}
async function showProgram(pid) {
  const box = $("#ex-prog-detail");
  box.innerHTML = `<div class="loading">Loading ${esc(pid)}…</div>`;
  const d = await api(`/api/results/program/${encodeURIComponent(pid)}`);
  if (!d.rows.length) { box.innerHTML = `<div class="empty">No results for ${esc(pid)}.</div>`; return; }
  const timing = d.timing.filter((t) => t.backend === "interp");
  box.innerHTML = `<h3 class="section">${esc(pid)}</h3>
    ${table([{ label: "Strategy", html: (r) => stratTag(r.strategy) }, { label: "Cost", get: (r) => fmt(r.final_cost, 4), num: true },
      { label: "Instr", get: (r) => `${r.instructions_before}→${r.instructions_after}`, num: true }, { label: "Arith", get: (r) => `${r.arith_ops_before}→${r.arith_ops_after}`, num: true },
      { label: "Temps", get: (r) => `${r.temps_before}→${r.temps_after}`, num: true }, { label: "Sim. cycles", get: (r) => `${fmt(r.cycles_before, 1)}→${fmt(r.cycles_after, 1)}`, num: true },
      { label: "Measured (interp)", get: (r) => { const t = timing.find((x) => x.strategy === r.strategy); return t ? ns(t.median_ns) : "–"; }, num: true },
      { label: "Evaluated", get: (r) => fmtInt(r.candidates_evaluated), num: true }, { label: "Steps", key: "transformations", num: true },
      { label: "Held-out", get: (r) => `${r.heldout_matching_reference}/${r.heldout_inputs}`, num: true }, { label: "Correct", html: (r) => +r.correct === 1 ? badge("yes", "ok") : badge("no", "bad") }], d.rows)}
    <div class="split section">${["unoptimized", "greedy"].map((k) => { const s = d.steps.find((x) => x.strategy === k); return s ? `<div><h3>${esc(STRAT[k].name)} TAC</h3>${codeBlock(s.optimized_tac.split("\n"))}</div>` : ""; }).join("")}</div>
    ${d.steps.filter((s) => s.steps.length && s.strategy !== "unoptimized").map((s) => `<details class="section"><summary>${stratTag(s.strategy)} — ${s.steps.length} transformations</summary>
      <ol class="steps">${s.steps.map((x) => `<li><span class="op">${esc(x.op_id)}</span> <span class="mono">${esc(x.description)}</span> <span class="muted">cost ${fmt(x.cost, 4)}</span></li>`).join("")}</ol></details>`).join("")}`;
}

/* ========== PAGE: METHODOLOGY ========== */
async function pageMethodology(el) {
  const ov = await api("/api/overview");
  const w = ov.cost_weights, ex = ov.experiments;
  const ops = Object.entries(ov.operator_names).filter(([k]) => k !== "MX");
  el.innerHTML = `<div class="page-head"><div><h1>Architecture and methodology</h1>
    <p class="lede">How programs flow through the system, how the dataset is built and validated, what each metric means, and where the boundaries are.</p></div></div>
    <div class="card"><h2>Pipeline</h2><p class="card-sub">The loop (blue) repeats until no valid candidate lowers the cost (greedy), a fixed pass order reaches a fixpoint (fixed), or the beam stalls (beam).</p>
      <div class="pipeline">
        <div class="stage"><b>MiniArith source</b><span>input / assign / print / if-else</span></div>
        <div class="stage"><b>Lexer + parser</b><span>frontend/ · AST, definite assignment</span></div>
        <div class="stage"><b>Naive TAC</b><span>frontend/tac.py · basic blocks, CFG</span></div>
        <div class="stage loop"><b>Mutation operators</b><span>optimizer/mutations.py · M1–M10, per block</span></div>
        <div class="stage loop"><b>Validator</b><span>execution/validator.py · differential testing</span></div>
        <div class="stage loop"><b>Cost model</b><span>optimizer/cost.py · weighted, normalised</span></div>
        <div class="stage loop"><b>Search strategy</b><span>fixed · greedy · beam</span></div>
        <div class="stage"><b>Independent checks</b><span>AST reference on fixtures + held-out inputs, compiled backend</span></div>
      </div></div>
    <div class="grid g2 section">
      <div class="card"><h2>Metrics</h2>
        <dl class="kv">
          <dt>Instructions (I)</dt><dd>TAC instructions, labels excluded. Weight ${w.instructions}.</dd>
          <dt>Arithmetic ops (A)</dt><dd>Instructions computing + − * / % &lt;&lt; neg or a comparison. Weight ${w.arith_ops}.</dd>
          <dt>Temporaries (T)</dt><dd>Distinct compiler temporaries (t1, t2, …) defined. Weight ${w.temps}.</dd>
          <dt>Simulated cycles (C)</dt><dd>${badge("simulated", "sim")} Deterministic model: + − &lt;&lt; copy neg io compare jump = 1, * = 3, / % = 10, +1 stall when an instruction reads the previous instruction's result. Mean over validation vectors. Weight ${w.cycles}. <b>Not CPU cycles.</b></dd>
          <dt>Cost</dt><dd>0.4·I/I₀ + 0.3·A/A₀ + 0.2·T/T₀ + 0.1·C/C₀ — the unoptimized program has cost 1.0.</dd>
          <dt>Measured time</dt><dd>${badge("measured", "ok")} Wall-clock median per execution, warm-up then interleaved repeats, GC disabled; Python TAC interpreter and TAC compiled to Python. Includes Python overhead; not native code.</dd>
        </dl></div>
      <div class="card"><h2>Operators</h2>${table([{ label: "ID", key: "k" }, { label: "Operator", key: "v" }], ops.map(([k, v]) => ({ k, v })))}
        <p class="card-sub">MX (unchecked reordering) is a deliberately unsafe operator used only to demonstrate validator rejections.</p></div>
    </div>
    <div class="grid g2 section">
      <div class="card"><h2>Correctness protocol</h2><ol class="small">
        <li><b>During search:</b> every candidate runs on 25 seeded vectors plus structured vectors (one input zero, equal pairs). Printed values and the error kind must equal the original's.</li>
        <li><b>After search (independent):</b> each final program is compared to the AST reference interpreter, which has its own arithmetic, on the dataset fixture tests and ${ex ? ex.config.heldout_inputs : 200} held-out inputs. It is also compared through a second backend (TAC compiled to Python).</li>
        <li><b>Operator soundness:</b> unit tests enumerate all inputs in [−4, 4]ⁿ for every candidate of every operator on hazard programs. Fuzzing covers straight-line and if/else programs.</li>
        <li><b>Validator self-test:</b> injected faults; ${ov.validator_eval ? `${pct(100 * ov.validator_eval.v2.detection_rate_on_nonequivalent, 2)} of non-equivalent mutants rejected, ${ov.validator_eval.v2.totals.accepted_but_different} missed` : "run experiments.validator_eval"}.</li>
        </ol><div class="callout warn small">Finite differential testing is not a proof of equivalence. No formal verification was implemented.</div></div>
      <div class="card"><h2>Dataset methodology</h2><ol class="small">
        <li><b>A. Controlled:</b> the 15 Review-1 programs, unchanged, plus template programs in 6 categories with recorded size and redundancy knobs.</li>
        <li><b>B. Randomized:</b> a grammar-based generator with a recorded config. It is not targeted at any operator and exercises negatives, redefinitions, shared expressions and division by variables.</li>
        <li><b>C. External:</b> a conservative C-subset translation of Jotai functions, cross-checked with gcc. Incompatible files are kept with a reason. Licenses are marked unverified.</li>
        <li>Validation needs three executors to agree (reference, TAC interpreter, compiled backend). Rejection rules are recorded per record. Duplicates are found by normalised source and canonical TAC.</li>
        </ol><p class="card-sub">Details: <code>dataset/README.md</code>.</p></div>
    </div>
    <div class="card section"><h2>Supported language and limitations</h2><ul class="small">
      <li>The core language is straight-line integer MiniArith. if/else, basic blocks and a CFG are a staged extension: operators run per block with conservative liveness, and M10 folds constant branches. There are no loops or functions.</li>
      <li>Integers are unbounded (Python int). 32-bit overflow is not modelled. External fragments are only tested where C behaviour is defined.</li>
      <li>The simulated cycle model drives M8 instruction reordering. Measured Python timings do not show the stall effect (see the reordering check on the Experiments page).</li>
      <li>Measured speed-ups are relative to deliberately naive TAC and are dominated by fewer interpreted instructions. They are not a statement about native code.</li>
      <li>The external group is tiny (7 programs) because most real C uses pointers, loops, branches or unsigned types.</li>
    </ul></div>
    <div class="card section"><h2>Run it yourself</h2><pre class="block">pip install -r requirements.txt      # matplotlib (plots only)
python reproduce.py                  # full pipeline: dataset check, tests, fuzzing, experiments, plots, verification
python -m dashboard.server           # this dashboard at http://127.0.0.1:8765
python -m experiments.run_experiments --limit 40 --out results/smoke   # quick subset run</pre></div>`;
}

/* ---------- router ---------- */
const ROUTES = { overview: pageOverview, compiler: pageCompiler, dataset: pageDataset, experiments: pageExperiments, methodology: pageMethodology };
async function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "overview";
  const [name, query] = hash.split("?");
  const page = ROUTES[name] ? name : "overview";
  document.querySelectorAll("nav a").forEach((a) => a.setAttribute("aria-current", a.getAttribute("href") === `#/${page}` ? "page" : "false"));
  const el = main();
  el.innerHTML = `<div class="loading">Loading…</div>`;
  hideTip();
  try {
    await ROUTES[page](el, new URLSearchParams(query || ""));
    bindTips(el);
  } catch (e) {
    el.innerHTML = `<h1>Something went wrong</h1>${errorBox(e.message)}<p class="muted">Is the server running (<code>python -m dashboard.server</code>)? Have the dataset and experiments been generated?</p>`;
  }
  document.title = `${page[0].toUpperCase() + page.slice(1)} · MiniArith Optimizer`;
}
window.addEventListener("hashchange", route);
document.getElementById("theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* storage unavailable */ }
});
route();
