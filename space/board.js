/* 3amBench Replay: tier switch, leaderboard, the expert requirement panel and the theme button.
   Loaded before app.js, which owns the shared helpers ($, esc, fmt) and the state S; everything here runs from main(). */
"use strict";

const TIER_ORDER = ["core", "expert"];
const TIER_FALLBACK = { core: { label: "Standard" }, expert: { label: "Expert" } };
const CORE_COLS = [["req_alerts", "alerts"], ["req_repairs", "repairs"], ["req_recording", "recording"],
  ["req_routing", "routing"], ["req_inhibit", "inhibition"]];
const EXPERT_COLS = [["diagnosis", "diagnosis"], ["routine", "routine"]];
const REQ_GROUPS = [["diagnosis", "Symptom tickets (diagnosis)"], ["routine", "Routine queue"]];
const THEMES = ["auto", "light", "dark"];

const meanOf = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
function taskTier(t) { return t && t.tier === "expert" ? "expert" : "core"; }
function runTier(r) { return r.tier === "expert" || r.tier === "core" ? r.tier : taskTier(S.data.tasks[r.task_id]); }
function tierInfo(t) { return Object.assign({}, TIER_FALLBACK[t] || { label: t }, (S.data.tiers || {})[t] || {}); }
function tiersPresent() { return TIER_ORDER.filter((t) => Object.values(S.data.tasks).some((x) => taskTier(x) === t)); }
function defaultTier() { return S.data.runs.some((r) => runTier(r) === "expert") ? "expert" : "core"; }
function familyOf(r) {
  return r.model_family || (r.source === "openenv" || !r.model ? "reference" : r.agent.replace(/^harbor: /, ""));
}
const famCmp = (a, b) => (a === "reference") - (b === "reference") || a.toLowerCase().localeCompare(b.toLowerCase());

/* ---------- tier switch ---------- */
function renderTierSwitch() {
  const tiers = tiersPresent(); const box = $("tier-switch");
  box.hidden = tiers.length < 2;
  box.innerHTML = tiers.map((t) => {
    const info = tierInfo(t); const n = S.data.runs.filter((r) => runTier(r) === t).length;
    return `<button type="button" data-tier="${esc(t)}" aria-pressed="${t === S.tier}">${esc(info.label)}` +
      `${info.release ? ` <span class="rel">${esc(info.release)}</span>` : ""}<span class="rel"> · ${plural(n, "run")}</span></button>`;
  }).join("");
}

/* ---------- leaderboard ---------- */
function boardScope(tier) {
  const all = S.data.runs.filter((r) => runTier(r) === tier);
  const lb = tier === "expert" && all.some((r) => (S.data.tasks[r.task_id] || {}).leaderboard != null);
  const scoped = lb ? all.filter((r) => S.data.tasks[r.task_id].leaderboard) : all;
  return { lb, runs: scoped.filter((r) => !r.invalid), invalid: scoped.filter((r) => r.invalid),
    outside: all.length - scoped.length };
}
function boardGroups(runs, cols) {
  const by = new Map();
  for (const r of runs) {
    const k = r.agent + "\u0000" + (r.model || "");
    if (!by.has(k)) by.set(k, { agent: r.agent, model: r.model || "", source: r.source, family: familyOf(r), runs: [] });
    by.get(k).runs.push(r);
  }
  return [...by.values()].map((g) => ({
    ...g, n: g.runs.length, tasks: new Set(g.runs.map((r) => r.task_id)).size,
    reward: meanOf(g.runs.map((r) => r.reward || 0)),
    solved: g.runs.filter((r) => r.keys && r.keys.solved >= 1).length,
    cols: cols.map(([k]) => meanOf(g.runs.filter((r) => r.keys && r.keys[k] != null).map((r) => r.keys[k]))),
  }));
}
function boardRow(g) {
  const pct = Math.round((g.solved / g.n) * 100);
  return `<tr><td>${esc(g.agent.replace(/^harbor: /, ""))} <span class="tag">${g.source === "harbor" ? "Harbor" : "OpenEnv"}</span></td>
    <td>${esc(g.model || "–")}</td><td class="num">${g.n}</td><td class="num">${g.tasks}</td>
    <td class="num"><span class="bar" style="width:${Math.round(Math.max(0, g.reward) * 60)}px"></span>${fmt(g.reward)}</td>
    <td class="num"><span class="sbar" title="${pct}% solved"><span style="width:${pct}%"></span></span>${g.solved}/${g.n}</td>
    ${g.cols.map((c) => `<td class="num">${fmt(c, 2)}</td>`).join("")}</tr>`;
}
function leaderboard() {
  const tier = S.tier; const sc = boardScope(tier); const info = tierInfo(tier);
  const cols = tier === "expert" ? EXPERT_COLS : CORE_COLS;
  const rows = boardGroups(sc.runs, cols);
  const fams = [...new Set(rows.map((g) => g.family))].sort(famCmp);
  const width = 6 + cols.length;
  const head = `<thead><tr><th>Agent</th><th>Model</th><th class="num">Runs</th><th class="num">Tasks</th>
    <th class="num">Mean reward</th><th class="num">Solved</th>${cols.map(([, n]) => `<th class="num">${n}</th>`).join("")}
    </tr></thead>`;
  const body = fams.map((f) => {
    const gs = rows.filter((g) => g.family === f).sort((a, b) => b.reward - a.reward || a.agent.localeCompare(b.agent));
    const n = gs.reduce((a, g) => a + g.n, 0);
    return `<tbody><tr class="fam"><th colspan="${width}" scope="rowgroup">${esc(f)}
      <span class="count">${f === "reference" ? `${gs.length} ${gs.length === 1 ? "policy" : "policies"}`
        : plural(gs.length, "model")} · ${plural(n, "run")}</span></th></tr>${gs.map(boardRow).join("")}</tbody>`;
  }).join("");
  $("board").innerHTML = rows.length ? head + body : `<tbody><tr><td>No valid runs in this tier yet.</td></tr></tbody>`;
  const nlb = (info.leaderboard_tasks || []).length ||
    Object.values(S.data.tasks).filter((t) => taskTier(t) === tier && t.leaderboard).length;
  $("board-tier").textContent = `${info.label}${info.release ? " · " + info.release : ""}`;
  $("board-caption").innerHTML = tier === "expert"
    ? `Mean over the valid runs on the ${sc.lb ? plural(nlb, "leaderboard task") : "expert tasks"}` +
      `${sc.outside ? `; the replay below also shows ${plural(sc.outside, "run")} on other expert tasks` : ""}. ` +
      "<em>diagnosis</em> and <em>routine</em> are weighted mean requirement scores over the symptom tickets and over " +
      "the rest of the queue. Rows are grouped by model family."
    : "Mean over the recorded runs. <em>reference</em> rows are scripted policies (no model) that calibrate the " +
      "reward scale. Rows are grouped by model family.";
  const inv = sc.invalid;
  $("board-invalid").hidden = !inv.length;
  $("board-invalid").innerHTML = inv.length ? `<summary>${plural(inv.length, "run")} excluded as invalid</summary><ul>` +
    inv.map((r) => `<li>${esc(r.agent.replace(/^harbor: /, ""))}${r.model ? " · " + esc(r.model) : ""} · ${esc(r.task_id)}: ${esc(r.invalid)}</li>`).join("") +
    "</ul>" : "";
}

/* ---------- expert requirement panel ---------- */
const sameArr = (a, b) => !!a && !!b && a.length === b.length && a.every((x, k) => x === b[k]);
function reqState(i) {
  const r = S.run; const T = S.data.tasks[r.task_id];
  const fin = T.requirements.map((q) => (r.req_scores && r.req_scores[q.id] != null ? r.req_scores[q.id] : null));
  if (r.curve === "terminal" || !r.steps.some((s) => Array.isArray(s.reqs)))
    return { cur: fin, prev: null, when: "terminal", final: true };
  let cur = null; let prev = null;
  for (let j = 0; j <= i; j++) if (Array.isArray(r.steps[j].reqs)) { prev = cur; cur = r.steps[j].reqs; }
  const last = r.steps.length - 1;
  if (i === last && !sameArr(cur, fin)) return { cur: fin, prev: cur, when: "end", final: true };
  if (!Array.isArray(r.steps[i].reqs)) prev = null;
  return { cur, prev, when: i === last ? "end" : cur ? "step" : "none", final: i === last };
}
function reqClass(s) { return s == null ? "unk" : s >= 0.999 ? "pass" : s > 0 ? "part" : "fail"; }
function reqRow(x, final) {
  const q = x.q; const cls = reqClass(x.s);
  const mark = { pass: "✓", part: "◐", fail: "✗", unk: "?" }[cls];
  const word = { pass: "solved", part: "partly solved", fail: "not solved", unk: "not graded" }[cls];
  const why = final && cls !== "pass" && S.run.req_why ? S.run.req_why[q.id] : "";
  const title = q.title || q.summary || q.id;
  const summary = q.summary && q.summary !== title ? `<span class="sub">${esc(q.summary)}</span>` : "";
  return `<li class="${cls}${x.changed ? " changed" : ""}"><span class="mark" aria-label="${word}">${mark}</span>
    <span class="lbl"><span class="rid">${esc(q.id)}</span> ${q.ref ? `<span class="ref">${esc(q.ref)}</span> · ` : ""}${esc(title)}
      <span class="tag">${esc(q.kind || "")}</span> <span class="nt">weight ${esc(q.weight)}</span>${summary}
      ${why ? `<span class="why">${esc(why)}</span>` : ""}</span>
    <span class="sv">${x.s == null ? "–" : fmt(x.s, 2)}</span></li>`;
}
function preservedHtml(r) {
  const P = r.keys ? r.keys.preservation : null; const fails = r.preservation_fails || [];
  let h = `<div class="pres small"><b>Preserved</b> ${P == null ? "–" : fmt(P, 3)}` +
    (fails.length ? ` · failing: ${esc(fails.slice(0, 6).join(", "))}${fails.length > 6 ? ", …" : ""}` : "") + "</div>";
  if (r.kept && r.kept.length)
    h += `<div class="pres small"><b>Left as is</b> ${r.kept.map((k) => `${esc(k.alert)} ${k.k >= 1 ? "✓" : "✗"}`).join(" · ")}</div>`;
  return h;
}
function renderReqs() {
  const r = S.run; const T = S.data.tasks[r.task_id]; const st = reqState(S.i);
  $("checks-title").textContent = "Requirements";
  $("checks-when").textContent = { terminal: "(end of run: Harbor grades only the final state)", end: "(end of run)",
    step: `(after step ${S.i})`, none: "" }[st.when];
  if (!st.cur) {
    $("checks-summary").textContent = "Nothing graded yet at this step: the first edit has not happened.";
    $("checks").innerHTML = ""; return;
  }
  const rows = T.requirements.map((q, k) => ({ q, s: st.cur[k], changed: !!st.prev && st.prev[k] !== st.cur[k] }));
  const done = rows.filter((x) => x.s != null && x.s >= 0.999).length;
  $("checks-summary").innerHTML = `<b>${done}</b> of ${rows.length} requirements solved.` +
    (st.prev ? " Highlighted rows changed at this step." : "");
  let h = REQ_GROUPS.map(([g, title]) => {
    const items = rows.filter((x) => (x.q.group === "diagnosis") === (g === "diagnosis"));
    if (!items.length) return "";
    const ok = items.filter((x) => x.s != null && x.s >= 0.999).length;
    return `<details class="check-group reqs" open><summary>${title} <span class="count">${ok}/${items.length}</span></summary>
      <ul class="req-list">${items.map((x) => reqRow(x, st.final)).join("")}</ul></details>`;
  }).join("");
  if (st.final) h += preservedHtml(r);
  $("checks").innerHTML = h;
}

/* ---------- theme (auto / light / dark) ---------- */
function applyTheme(t) {
  const root = document.documentElement;
  if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
  const b = document.getElementById("theme-btn");
  if (b) { b.dataset.theme = t; b.textContent = "Theme: " + t; }
}
function storedTheme() {
  try { const t = localStorage.getItem("af-theme"); return THEMES.includes(t) ? t : "auto"; } catch (e) { return "auto"; }
}
function cycleTheme() {
  const b = document.getElementById("theme-btn"); const cur = (b && b.dataset.theme) || "auto";
  const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
  applyTheme(next);
  try { localStorage.setItem("af-theme", next); } catch (e) { /* storage blocked: the choice lasts for this page only */ }
}
applyTheme(storedTheme());
