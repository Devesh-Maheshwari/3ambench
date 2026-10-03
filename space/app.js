/* 3amBench Replay: renders data/runs.json (written by scripts/export_runs.py). No dependencies.
   board.js (loaded first) holds the tier switch, leaderboard, expert requirement panel and theme button. */
"use strict";

const GROUPS = ["alerts", "repairs", "recording", "routing", "inhibition", "preserved"];
const $ = (id) => document.getElementById(id);
const S = { data: null, tier: "core", task: null, run: null, i: 0, timer: null, resize: null };

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const fmt = (x, d = 3) => (x == null || isNaN(x) ? "–" : Number(x).toFixed(d));
const signed = (x) => (x > 0 ? "+" : "") + fmt(x, 3);

/* ---------- tiny markdown: headings, bullets, paragraphs, **bold**, `code` ---------- */
function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}
function markdown(src) {
  const out = []; let list = false; let para = [];
  const flush = () => { if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; } };
  for (const line of src.split("\n")) {
    const h = line.match(/^(#{1,4})\s+(.*)$/); const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (h || li || !line.trim()) flush();
    if (!li && list) { out.push("</ul>"); list = false; }
    if (h) out.push(`<h3>${inline(h[2])}</h3>`);
    else if (li) { if (!list) { out.push("<ul>"); list = true; } out.push("<li>" + inline(li[1]) + "</li>"); }
    else if (line.trim()) para.push(line.trim());
  }
  flush(); if (list) out.push("</ul>");
  return out.join("");
}

/* ---------- tiers and pickers ---------- */
const isExpert = (tid) => taskTier(S.data.tasks[tid]) === "expert";
function tierTasks(tier) { return Object.keys(S.data.tasks).filter((t) => taskTier(S.data.tasks[t]) === tier).sort(); }
function setTier(tier, tid, runId) {
  stop(); S.tier = tier;
  renderTierSwitch(); leaderboard(); fillTasks();
  const ids = tierTasks(tier);
  $("replay-section").hidden = !ids.length;
  if (ids.length) selectTask(tid && ids.includes(tid) ? tid : ids[0], runId);
}
function runLabel(r) {
  const m = r.model ? ` · ${r.model}` : "";
  return `${r.agent}${m} · reward ${fmt(r.reward)}${r.trial ? " · " + r.trial.slice(-8) : ""}${r.invalid ? " · invalid" : ""}`;
}
function fillTasks() {
  $("task-pick").innerHTML = tierTasks(S.tier).map((t) => {
    const T = S.data.tasks[t]; const n = S.data.runs.filter((r) => r.task_id === t).length;
    const what = isExpert(t) ? ` · ${T.workflow || T.family || "expert"}` : "";
    const diff = isExpert(t) ? "" : `${T.difficulty || T.tier}, `;
    return `<option value="${esc(t)}">${esc(t)}${esc(what)} (${esc(diff)}${n} run${n === 1 ? "" : "s"})</option>`;
  }).join("");
}
function selectTask(tid, runId) {
  S.task = tid; $("task-pick").value = tid;
  const runs = S.data.runs.filter((r) => r.task_id === tid);
  $("run-pick").innerHTML = runs.map((r) => `<option value="${r.id}">${esc(runLabel(r))}</option>`).join("");
  $("instruction").innerHTML = markdown(S.data.tasks[tid].instruction);
  selectRun(runId && runs.some((r) => r.id === runId) ? runId : runs[0].id);
}
function runMeta(r, T) {
  const k = r.keys || {}; const exp = taskTier(T) === "expert";
  const where = exp ? [T.workflow, T.family] : [T.workflow, T.difficulty || T.tier];
  const reward = r.reward_original != null ? `re-graded <b>${fmt(r.reward)}</b> (Harbor ${fmt(r.reward_original)})`
    : `final reward <b>${fmt(r.reward)}</b>`;
  const extra = exp ? ` · diagnosis ${fmt(k.diagnosis, 2)} · routine ${fmt(k.routine, 2)} · preserved ${fmt(k.preservation, 2)}` : "";
  let h = r.invalid ? `<div class="banner" role="note"><b>Invalid run</b>, left out of the leaderboard: ${esc(r.invalid)}</div>` : "";
  h += `<div><b>${esc(r.agent)}</b>${r.model ? " · " + esc(r.model) : ""} · ${where.filter(Boolean).map(esc).join(" · ")}
    · ${reward} · solved ${k.solved ? "yes" : "no"}${extra}${r.cost_usd ? " · $" + fmt(r.cost_usd, 2) : ""}</div>`;
  if (!exp) {
    const reqs = T.requirements.map((q) => {
      const s = r.req_scores ? r.req_scores[q.id] : null;
      return `<span title="${esc(q.title)}">${esc(q.id)} ${s == null ? "–" : fmt(s, 2)}</span>`;
    }).join(" · ");
    h += `<div class="small">Per-requirement score: ${reqs}</div>`;
  }
  if (r.reward_original != null) {
    const ko = r.keys_original || {};
    h += `<div class="note">Re-graded with the current hidden tests${r.regraded ? ` (${esc(r.regraded)})` : ""}; Harbor's
      own verifier gave ${fmt(r.reward_original)}${ko.solved != null ? `, solved ${ko.solved ? "yes" : "no"}` : ""}.</div>`;
  }
  if (r.exception) h += `<div class="note">Harbor recorded ${esc(r.exception)} for this run; it still counts.</div>`;
  return h + `<div class="note">${esc(r.curve_note || "")}</div>`;
}
function selectRun(id) {
  stop();
  S.run = S.data.runs.find((r) => r.id === id); $("run-pick").value = id;
  const r = S.run; const T = S.data.tasks[r.task_id];
  $("run-meta").innerHTML = runMeta(r, T);
  $("scrub").max = Math.max(0, r.steps.length - 1);
  buildTimeline(); drawCurve(); go(0);
  const h = `#${S.tier}/${encodeURIComponent(r.task_id)}/${r.id}`;
  if (location.hash !== h) history.replaceState(null, "", h);
}

/* ---------- step view ---------- */
function stepTitle(s) {
  if (s.kind === "think") return (s.text || "").split("\n")[0];
  if (s.kind === "tool_call") return `${s.tool} ${s.args || ""}`;
  return (s.output || "").split("\n")[0] || "(no output)";
}
function buildTimeline() {
  $("timeline").innerHTML = S.run.steps.map((s, i) => {
    const dr = s.r ? `<span class="dr ${s.r > 0 ? "up" : "down"}">${s.r > 0 ? "▲" : "▼"} ${signed(s.r)}</span>` : "";
    return `<li data-i="${i}"><span class="idx">${i}</span><span class="kind">${esc(s.kind.replace("_", " "))}</span>
      <span class="what">${esc(stepTitle(s))}</span>${dr}</li>`;
  }).join("");
}
function diffHtml(d) {
  return d.split("\n").map((l) => {
    const c = l.startsWith("+++") || l.startsWith("---") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del"
      : l.startsWith("@@") ? "hunk" : "";
    return c ? `<span class="${c}">${esc(l)}</span>` : esc(l) + "\n";
  }).join("");
}
function renderStep() {
  const s = S.run.steps[S.i]; let h = "";
  const cum = cumAt(S.i);
  h += `<div class="step-head"><span class="mono small muted">step ${S.i}</span><span class="tool">${esc(s.kind === "think" ?
    "thinking" : s.tool)}</span>${s.r != null ? `<span class="${s.r > 0 ? "up" : s.r < 0 ? "down" : "muted"}">step reward ${signed(s.r)}</span>` : ""}
    <span class="muted">cumulative ${fmt(cum)}</span>${s.phi != null ? `<span class="muted">progress ${fmt(s.phi)}</span>` : ""}
    ${s.ok === false ? '<span class="down">tool error</span>' : ""}</div>`;
  if (s.kind === "think") h += `<p class="think">${esc(s.text)}</p>`;
  if (s.args) h += `<div class="small muted">arguments</div><pre class="wrap-text">${esc(s.args)}</pre>`;
  if (s.diff) h += `<div class="small muted">file diff</div><pre class="diff">${diffHtml(s.diff)}</pre>`;
  if (s.kind === "observation") h += `<div class="small muted">output</div><pre class="wrap-text">${esc(s.output || "(empty)")}</pre>`;
  $("step-detail").innerHTML = h;
}
function cumAt(i) {
  for (let j = i; j >= 0; j--) if (S.run.steps[j].cum != null) return S.run.steps[j].cum;
  return 0;
}
function go(i) {
  const n = S.run.steps.length; S.i = Math.max(0, Math.min(n - 1, i));
  $("scrub").value = S.i; $("step-label").textContent = `${S.i} / ${n - 1}`;
  document.querySelectorAll("#timeline li").forEach((li) => {
    const k = +li.dataset.i; li.classList.toggle("cur", k === S.i); li.classList.toggle("future", k > S.i);
  });
  const cur = document.querySelector("#timeline li.cur");
  if (cur) { const box = $("timeline"); const t = cur.offsetTop - box.offsetTop;
    if (t < box.scrollTop || t > box.scrollTop + box.clientHeight - 30) box.scrollTop = t - box.clientHeight / 2; }
  renderStep(); (isExpert(S.run.task_id) ? renderReqs : renderChecks)(); moveCursor();
}

/* ---------- checks panel (core tier) ---------- */
function checksAt(i) {
  let prev = null; let cur = null;
  for (let j = 0; j <= i; j++) if (S.run.steps[j].checks) { prev = cur; cur = S.run.steps[j].checks; }
  const last = S.run.steps.length - 1;
  if (i === last && S.run.final_checks && cur !== S.run.final_checks) { prev = cur; cur = S.run.final_checks; }
  else if (!S.run.steps[i].checks) prev = null;   // highlight only changes made by this very step
  return { cur, prev };
}
function renderChecks() {
  const T = S.data.tasks[S.run.task_id]; const { cur, prev } = checksAt(S.i);
  const graded = S.run.steps.some((s) => s.checks);
  $("checks-title").textContent = "Checks";
  $("checks-when").textContent = cur ? (S.i === S.run.steps.length - 1 ? "(end of run)" : `(after step ${S.i})`) : "";
  if (!cur) {
    $("checks-summary").textContent = graded ? "Nothing graded yet at this step: the first edit has not happened."
      : "This run has checks only at the end (Harbor gives a terminal result). Jump to the last step.";
    $("checks").innerHTML = ""; return;
  }
  const byGroup = {}; let pass = 0; let tot = 0;
  T.checks.forEach((c, k) => {
    const v = cur[k]; if (v === "-") return;
    (byGroup[c.group] = byGroup[c.group] || []).push({ c, v, changed: prev && prev[k] !== v });
    tot++; if (v === "1") pass++;
  });
  const reqTitle = Object.fromEntries(T.requirements.map((q) => [q.id, q.title]));
  $("checks-summary").innerHTML = `<b>${pass}</b> of ${tot} checks pass. Highlighted rows changed at this step.`;
  $("checks").innerHTML = GROUPS.filter((g) => byGroup[g]).map((g) => {
    const items = byGroup[g]; const ok = items.filter((x) => x.v === "1").length;
    const open = g !== "preserved" || ok < items.length;
    const reqs = [...new Set(items.map((x) => x.c.req || ""))];
    const body = reqs.map((rid) => {
      const li = items.filter((x) => (x.c.req || "") === rid).map((x) => `<li class="${x.v === "1" ? "pass" : "fail"}${x.changed ? " changed" : ""}">
        <span class="mark" aria-label="${x.v === "1" ? "pass" : "fail"}">${x.v === "1" ? "✓" : "✗"}</span>
        <span class="lbl">${esc(x.c.label)}${x.c.note ? ` <span class="nt">${esc(x.c.note)}</span>` : ""}</span></li>`).join("");
      return `<div class="req">${rid ? `<div class="req-title">${esc(rid)} · ${esc(reqTitle[rid] || "")}</div>` : ""}<ul class="checks-list">${li}</ul></div>`;
    }).join("");
    return `<details class="check-group" ${open ? "open" : ""}><summary>${g} <span class="count">${ok}/${items.length}</span></summary>${body}</details>`;
  }).join("");
}

/* ---------- reward curve (inline SVG) ---------- */
const CV = { w: 900, h: 190, l: 44, r: 12, t: 12, b: 26 };
function drawCurve() {
  const terminal = S.run.curve === "terminal";
  $("curve-fig").hidden = terminal; $("curve-none").hidden = !terminal;
  if (terminal) {
    $("curve").innerHTML = ""; CV.X = null;
    $("curve-none").textContent = `Final reward only: Harbor grades the end state of this run (reward ${fmt(S.run.reward)}).`;
    return;
  }
  CV.w = Math.max(320, Math.round($("curve").clientWidth || 900));   // draw at the real width: legible on phones
  CV.h = CV.w >= 700 ? 230 : 190;
  const st = S.run.steps; const n = Math.max(1, st.length - 1);
  const pts = st.map((s, i) => ({ i, y: cumAt(i), r: s.r || 0 }));
  const X = (i) => CV.l + (i / n) * (CV.w - CV.l - CV.r);
  const Y = (v) => CV.t + (1 - v) * (CV.h - CV.t - CV.b);
  let g = "";
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    g += `<line x1="${CV.l}" x2="${CV.w - CV.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)" stroke-width="1"/>`;
    g += `<text x="${CV.l - 6}" y="${Y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${v.toFixed(2)}</text>`;
  }
  const every = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((e) => n / e <= 10) || Math.ceil(n / 10);
  for (let i = 0; i <= n; i += every)
    g += `<text x="${X(i)}" y="${CV.h - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">${i}</text>`;
  const d = pts.map((p, k) => `${k ? "L" : "M"}${X(p.i).toFixed(1)},${Y(Math.max(-0.05, Math.min(1.05, p.y))).toFixed(1)}`).join("");
  g += `<path d="${d}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>`;
  for (const p of pts) if (Math.abs(p.r) > 1e-9) {
    const x = X(p.i); const y = Y(Math.max(-0.05, Math.min(1.05, p.y))); const up = p.r > 0;
    const tri = up ? `${x},${y - 11} ${x - 5},${y - 3} ${x + 5},${y - 3}` : `${x},${y + 11} ${x - 5},${y + 3} ${x + 5},${y + 3}`;
    g += `<polygon points="${tri}" fill="${up ? "var(--good)" : "var(--bad)"}" stroke="var(--surface)" stroke-width="1"/>`;
  }
  g += `<line id="cursor" x1="0" x2="0" y1="${CV.t}" y2="${CV.h - CV.b}" stroke="var(--text-2)" stroke-width="1" stroke-dasharray="3 3"/>`;
  g += `<circle id="cursor-dot" r="4.5" fill="var(--series-1)" stroke="var(--surface)" stroke-width="2"/>`;
  g += `<rect id="hit" x="${CV.l}" y="0" width="${CV.w - CV.l - CV.r}" height="${CV.h}" fill="transparent" style="cursor:pointer"/>`;
  $("curve").innerHTML = `<svg viewBox="0 0 ${CV.w} ${CV.h}" role="img" aria-label="Cumulative reward by step">${g}</svg>`;
  const svg = $("curve").firstChild; const hit = svg.querySelector("#hit");
  const idxAt = (ev) => { const b = svg.getBoundingClientRect(); const x = ((ev.clientX - b.left) / b.width) * CV.w;
    return Math.round(((x - CV.l) / (CV.w - CV.l - CV.r)) * n); };
  hit.addEventListener("mousemove", (ev) => {
    const i = Math.max(0, Math.min(n, idxAt(ev))); const s = st[i]; const tip = $("tip");
    tip.hidden = false;
    tip.innerHTML = `<b>step ${i}</b> · ${esc(s.kind === "think" ? "thinking" : s.tool)}<br>cumulative ${fmt(cumAt(i))}` +
      (s.r ? ` · step ${signed(s.r)}` : "");
    const b = svg.getBoundingClientRect(); const px = (X(i) / CV.w) * b.width;
    tip.style.left = Math.max(0, Math.min(px + 10, b.width - 200)) + "px"; tip.style.top = "8px";
  });
  hit.addEventListener("mouseleave", () => { $("tip").hidden = true; });
  hit.addEventListener("click", (ev) => { stop(); go(idxAt(ev)); });
  CV.X = X; CV.Y = Y;
}
function moveCursor() {
  const c = document.getElementById("cursor"); const dot = document.getElementById("cursor-dot"); if (!c || !CV.X) return;
  const x = CV.X(S.i); const y = CV.Y(Math.max(-0.05, Math.min(1.05, cumAt(S.i))));
  c.setAttribute("x1", x); c.setAttribute("x2", x); dot.setAttribute("cx", x); dot.setAttribute("cy", y);
}

/* ---------- playback ---------- */
function stop() { if (S.timer) clearInterval(S.timer); S.timer = null; $("btn-play").textContent = "Play"; }
function play() {
  if (S.timer) return stop();
  if (S.i >= S.run.steps.length - 1) go(0);
  $("btn-play").textContent = "Pause";
  S.timer = setInterval(() => { if (S.i >= S.run.steps.length - 1) return stop(); go(S.i + 1); }, +$("speed").value);
}

function wire() {
  $("tier-switch").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tier]"); if (b && b.dataset.tier !== S.tier) setTier(b.dataset.tier);
  });
  $("theme-btn").onclick = cycleTheme;
  $("task-pick").addEventListener("change", (e) => selectTask(e.target.value));
  $("run-pick").addEventListener("change", (e) => selectRun(e.target.value));
  $("btn-first").onclick = () => { stop(); go(0); };
  $("btn-prev").onclick = () => { stop(); go(S.i - 1); };
  $("btn-next").onclick = () => { stop(); go(S.i + 1); };
  $("btn-last").onclick = () => { stop(); go(S.run.steps.length - 1); };
  $("btn-play").onclick = play;
  $("speed").onchange = () => { if (S.timer) { stop(); play(); } };
  $("scrub").addEventListener("input", (e) => { stop(); go(+e.target.value); });
  $("timeline").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) { stop(); go(+li.dataset.i); } });
  window.addEventListener("resize", () => {
    clearTimeout(S.resize); S.resize = setTimeout(() => { if (S.run) { drawCurve(); moveCursor(); } }, 150);
  });
  document.addEventListener("keydown", (e) => {
    if (!S.run || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") { stop(); go(S.i + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft") { stop(); go(S.i - 1); e.preventDefault(); }
    else if (e.key === " ") { play(); e.preventDefault(); }
  });
  $("copy-cmd").onclick = () => {
    const t = $("docker-cmd").textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => { $("copy-cmd").textContent = "Copied"; },
      () => {});
  };
}

async function main() {
  wire();
  try {
    const res = await fetch("data/runs.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    S.data = await res.json();
  } catch (e) {
    $("load-status").textContent = "Could not load data/runs.json: " + e.message; return;
  }
  if (!S.data.runs.length) { $("load-status").textContent = "No runs recorded yet."; return; }
  $("load-status").hidden = true; $("board-section").hidden = false; $("replay-section").hidden = false;
  // #<tier>/<task>/<run>/<step>; the older #<task>/<run>/<step> still works (the tier comes from the task)
  const m = decodeURIComponent(location.hash.slice(1)).split("/");
  let tier = TIER_ORDER.includes(m[0]) ? m.shift() : null;
  const tid = S.data.tasks[m[0]] ? m[0] : null;
  if (!tier || !tiersPresent().includes(tier)) tier = tid ? taskTier(S.data.tasks[tid]) : defaultTier();
  setTier(tier, tid, m[1]);
  if (m[2] && S.task === tid) go(+m[2] || 0);
}
main();
