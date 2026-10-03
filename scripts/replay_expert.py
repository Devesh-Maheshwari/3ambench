"""Expert-tier (v0.2) catalog for the replay Space: requirement labels, why a requirement lost credit, and grading
a workspace with a task's own grader.

An expert task's requirements (tests/spec.json: T1.., Q1..) are the tickets of one week's queue, or in E5 answers
in the on-call survey. The grader scores each with s in [-0.25, 1]. This module labels every requirement with the
ticket it came from ("SRE-2481 · booking-api down ~50 min Sun 13 Sep night, no page") by matching the
requirement's alerts, services and teams against the ticket texts, falls back to a label built from the spec, and
summarises the grader's families for requirements below s = 1. Symptom tickets (spec `ticket: true`) form the
`diagnosis` group, everything else the `routine` group, as in the grader's `diagnosis` / `routine` keys.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import subprocess
import sys
import tempfile

EXPERT_RE = re.compile(r"(afx-e\d-s\d{2})")
FAMILY_NAMES = {"E1": "quiet pager", "E2": "storm after a deploy", "E3": "reorg and routing migration",
                "E4": "latency SLO rollout", "E5": "legacy cleanup after an on-call survey"}
EXPERT_KEYS = ["reward", "solved", "outcome", "progress", "diagnosis", "routine", "preservation", "req_outcome",
               "req_storm", "req_alerts", "req_repairs", "req_recording", "req_routing", "req_inhibit",
               "req_migration", "req_absence", "req_triage", "fire_rate", "silent_rate", "check_pass_rate",
               "syntax_ok", "tamper", "kept_broken"]
PAYLOAD = {"outcome": "outcome", "storm": "storm", "new_alert": "alert", "repair": "alert", "recording": "records",
           "route": "route", "inhibit": "inhibit", "migration": "migration", "relabel": "relabel",
           "absence": "absence", "annotation": "annotation"}
CUES = {"outcome": "paged nobody didn fire fired missed false noisy never pages",
        "storm": "pages storm one pod", "storm-dependency": "database failover blip connections",
        "storm-cluster": "cluster unreachable dropped region",
        "new_alert": "add burn fast slow", "repair": "fix broken", "recording": "sli recording latency wrong",
        "route": "route routing routed rota pagerduty receivers pager pages channel slack archived starts",
        "route-broad": "sre first everyone every directly own rotas copy fyi",
        "absence": "remove drop leftovers legacy duplicate decommissioned retire gone",
        "inhibit": "inhibit suppress shouldn while whenever disappear quieter one mute muted",
        "migration": "labels label carry still say jira land project moved split fallout everywhere",
        "annotation": "notifications mid sentence summary say about",
        "relabel": "page wake ticket severity runbook link wiki"}
# ticket headers name a component (`routing` · from @someone); a requirement's kind maps onto one of them
COMPONENTS = {"outcome": "alerting", "storm": "alerting", "new_alert": "slo", "repair": "alerting",
              "recording": "slo", "route": "routing", "inhibit": "routing", "migration": "ownership",
              "absence": "cleanup", "relabel": "alerting", "annotation": "alerting"}
_COMPONENT = re.compile(r"(?m)^(?:_Reported by .* · (\w+)_|Reporter: .*\| component: (\w+)|`(\w+)` · from @)")
MIN_SCORE = 3.0


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1}


def camel(name: str) -> set[str]:
    return {_stem(w.lower()) for w in re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|\d+", name or "")}


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()


def first_sentence(text: str, n: int = 90) -> str:
    s = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"


# ---------------------------------------------------------------------------------------------- sources
def queue_tickets(instruction: str) -> list[dict]:
    """[{ref, title, text}] for every `### KEY: title` ticket of the instruction's queue."""
    out = []
    for part in re.split(r"(?m)^### ", instruction)[1:]:
        head, _, body = part.partition("\n")
        key, _, title = head.partition(": ")
        body = re.split(r"(?m)^## ", body)[0]
        m = _COMPONENT.search(body)
        out.append({"ref": key.strip(), "title": title.strip() or key.strip(), "text": head + "\n" + body,
                    "component": next((g for g in m.groups() if g), None) if m else None})
    return out


def survey_answers(text: str) -> list[dict]:
    """[{ref, title, text}] for every numbered answer (`**3.** (team) ...`) of an on-call survey."""
    out = []
    for m in re.finditer(r"(?ms)^\*\*(\d+)\.\*\*\s*(?:\(([^)\n]*)\)\s*)?(.*?)(?=^\*\*\d+\.\*\*|\Z)", text):
        body = " ".join(m.group(3).split())
        out.append({"ref": f"survey #{m.group(1)}", "title": first_sentence(body), "text": body})
    return out


# ---------------------------------------------------------------------------------------------- matching
def signature(r: dict) -> dict:
    """Names a ticket about this requirement is likely to mention. Route checks also cover bystander teams, so
    only the cases of the team most of them are about count; a label move lists every alert of the moved service,
    so those names count as weak hints."""
    k, p = r["kind"], r.get(PAYLOAD.get(r["kind"], ""), {}) or {}
    alerts, touch, svcs, teams, recv, extra = set(), set(), set(), set(), set(), set()

    def labels(lab: dict) -> None:
        if lab.get("alertname") and k != "route":   # routing checks use stand-in alert names
            alerts.add(lab["alertname"])
        svcs.update(x for x in [lab.get("service")] if x)
        teams.update(x for x in [lab.get("team")] if x)

    if k in ("outcome", "storm"):
        for svc, v in (p.get("services") or {}).items():
            svcs.add(svc)
            teams.add(v.get("team", ""))
        alerts.update(x for x in [p.get("alertname"), p.get("alert")] if x)
        touch.update(p.get("touch") or [])
    elif k in ("new_alert", "repair"):
        alerts.add(p.get("name", ""))
        svcs.update(x for x in [p.get("service")] if x)
        teams.add((p.get("labels") or {}).get("team", ""))
        extra.update(words(str((p.get("labels") or {}).get("severity", ""))))
    elif k == "recording":
        extra.update(words(" ".join(p.get("names") or [])))
    elif k == "route":
        pos = p.get("pos") or []
        tally: dict[str, int] = {}
        for c in pos:
            t = (c.get("labels") or {}).get("team", "")
            tally[t] = tally.get(t, 0) + 1
        top = max(tally.values(), default=0)
        broad = sum(1 for n in tally.values() if n == top) >= 4   # fleet-wide routing: no team stands out
        for c in pos:
            team = (c.get("labels") or {}).get("team", "")
            if not broad and tally.get(team) == top:
                labels(c.get("labels") or {})
                recv.update(x for x in c.get("expect") or [] if team and team in x)   # not the shared FYI channel
        for c in p.get("neg") or []:
            recv.update(c.get("forbid") or [])
        for c in p.get("chain") or []:
            alerts.add(c.get("alert", ""))
            svcs.update(x for x in [c.get("service")] if x)
            recv.update(x for v in (c.get("expect") or {}).values() for x in v)
        for key in ("retired", "legacy_team", "integrations"):
            extra.update(words(json.dumps(p.get(key) or "")))
    elif k == "inhibit":
        for c in p.get("cases") or []:
            if c.get("expect"):
                for side in ("source", "target"):
                    labels(c.get(side) or {})
                    extra.update(words(str((c.get(side) or {}).get("severity", ""))))
        touch.update(alerts)   # Down / burn / error-ratio names: other tickets about the service mention them too
        alerts.clear()
    elif k in ("migration", "relabel"):
        for it in p.get("items") or []:
            (touch if k == "migration" else alerts).add(it.get("alert", ""))
            svcs.update(it.get("svcs") or [x for x in [it.get("svc")] if x])
            teams.add((it.get("labels") or {}).get("team", ""))
            extra.update(words(str((it.get("labels") or {}).get("severity", ""))))
            if it.get("runbooks"):
                extra.add("runbook")
    elif k == "absence":
        alerts.update(p.get("alerts") or [])
        svcs.update(p.get("route_values") or [])
    elif k == "annotation":
        alerts.add(p.get("alert", ""))
        svcs.update(x for x in [p.get("service")] if x)
    for s in (alerts, touch, teams, recv):
        s.discard("")
    hinted = alerts if k == "migration" else alerts | touch
    toks = set().union(*(camel(a) for a in hinted), *(words(s) for s in svcs | teams | recv), extra)
    cues = CUES.get(k, "")
    if k == "storm":
        cues += " " + CUES["storm-dependency" if p.get("alert") else "storm-cluster"]
    broad = k == "route" and not teams and not p.get("chain")
    return {"alerts": alerts, "touch": touch - alerts, "svcs": svcs, "teams": teams, "recv": recv, "words": toks,
            "cues": words(cues + (" " + CUES["route-broad"] if broad else "")), "cue_cap": 3.0 if broad else 1.5,
            "component": COMPONENTS.get(k)}


def _mentions(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def score(sig: dict, src: dict, idf: dict) -> float:
    text = src["text"]
    s = 6.0 * min(2, sum(1 for a in sig["alerts"] if a in text))
    s += 2.0 * min(2, sum(1 for a in sig["touch"] if a in text))
    s += 3.0 * sum(1 for v in sig["svcs"] if _mentions(v, text)) + sum(1 for t in sig["teams"] if _mentions(t, text))
    s += 2.0 * min(3, sum(1 for v in sig["recv"] if _mentions(v, text)))
    s += sum(idf.get(w, 0.0) for w in sig["words"] & src["words"])
    if src.get("component") and sig.get("component") and src["component"] != sig["component"]:
        s -= 5.0
    return s + min(sig["cue_cap"], 0.5 * len(sig["cues"] & src["words"]))


def assign(scores: list[list[float]]) -> dict[int, int]:
    """One source per requirement and vice versa, maximising the total score (greedy, then pairwise swaps)."""
    pairs = sorted(((s, i, j) for i, row in enumerate(scores) for j, s in enumerate(row) if s >= MIN_SCORE),
                   reverse=True)
    got: dict[int, int] = {}
    used: set[int] = set()
    for _, i, j in pairs:
        if i not in got and j not in used:
            got[i] = j
            used.add(j)
    for _ in range(50):
        better = False
        for i1 in list(got):
            for i2 in list(got):
                j1, j2 = got[i1], got[i2]
                if i1 < i2 and scores[i1][j2] + scores[i2][j1] > scores[i1][j1] + scores[i2][j2] + 1e-9 \
                        and min(scores[i1][j2], scores[i2][j1]) >= MIN_SCORE:
                    got[i1], got[i2], better = j2, j1, True
        if not better:
            break
    left_r = [i for i in range(len(scores)) if i not in got]
    left_s = [j for j in range(len(scores[0]) if scores else 0) if j not in set(got.values())]
    if len(left_r) == 1 and len(left_s) == 1 and scores[left_r[0]][left_s[0]] > 0:
        got[left_r[0]] = left_s[0]   # one ticket per requirement: the last two left belong together
    return got


def summary(r: dict) -> str:
    """A label built from the spec alone (used when no ticket matches, and as the secondary line)."""
    k, p = r["kind"], r.get(PAYLOAD.get(r["kind"], ""), {}) or {}
    if k == "outcome":
        svcs = p.get("services") or {}
        teams = sorted({v.get("team", "") for v in svcs.values()})
        verb = "page" if any(v.get("class") == "page" for v in svcs.values()) else "open a ticket for"
        what = f"{p['alertname']} on {', '.join(svcs)}" if p.get("alertname") else ", ".join(svcs)
        return f"{verb} {', '.join(teams)}: {what}"
    if k == "storm":
        what = p.get("alert") or "a shared dependency"
        return f"one page per service when {what} fails ({', '.join(p.get('services') or {})})"
    if k in ("new_alert", "repair"):
        lab = p.get("labels") or {}
        verb = "add" if k == "new_alert" else "fix"
        return f"{verb} {p.get('name')} ({lab.get('severity', '')}, {lab.get('team', '')})"
    if k == "recording":
        return "record " + ", ".join(p.get("names") or [])
    if k == "route":
        teams = [(c.get("labels") or {}).get("team", "") for c in p.get("pos") or []]
        main = max(set(teams) - {""}, key=lambda t: (teams.count(t), t), default="")
        chain = sorted({c.get("service", "") for c in p.get("chain") or []} - {""})
        forbid = sorted({x for c in p.get("neg") or [] for x in c.get("forbid") or []})
        if chain:
            return "route " + ", ".join(chain) + "'s alerts as written"
        if len(set(teams) - {""}) >= 4 and forbid:
            return f"every team's pages reach its own receivers, not {', '.join(forbid)}"
        return f"route team {main}" if main else "routing"
    if k == "inhibit":
        srcs = sorted({(c.get("source") or {}).get("alertname", "") for c in p.get("cases") or [] if c.get("expect")})
        return "inhibit under " + ", ".join(x for x in srcs if x) if any(srcs) else "inhibition"
    if k == "migration":
        items = p.get("items") or []
        teams = sorted({(it.get("labels") or {}).get("team", "") for it in items} - {""})
        svcs = sorted({s for it in items for s in it.get("svcs") or []})
        return f"move {', '.join(svcs)} alerts to team {', '.join(teams)}"
    if k == "relabel":
        it = (p.get("items") or [{}])[0]
        if it.get("labels"):
            return f"{it.get('alert')}: severity {it['labels'].get('severity')}, team {it['labels'].get('team')}"
        return f"{it.get('alert')}: fix the runbook link" if it.get("runbooks") else f"relabel {it.get('alert')}"
    if k == "absence":
        rv = p.get("route_values") or []
        return "remove " + ", ".join(p.get("alerts") or []) + (f" and the routes for {', '.join(rv)}" if rv else "")
    if k == "annotation":
        return f"{p.get('alert')}: notifications say what they are about"
    return r["id"]


# ---------------------------------------------------------------------------------------------- per-run text
def why(rd: dict) -> str:
    """Compact reason a requirement is below s = 1, from the grader's families (details.json)."""
    fams, bits = rd.get("families") or {}, []
    for k, v in fams.items():
        if k in ("counted", "noisy", "refs"):
            continue
        if isinstance(v, bool):
            if not v:
                bits.append(f"{k} no")
        elif isinstance(v, (int, float)):
            if k == "fired":
                bits += ["never fired"] if not v else []
            elif v < 1 - 1e-9:
                bits.append(f"{k} {v:.2f}")
        elif isinstance(v, list) and v and all(isinstance(x, (bool, int, float)) for x in v):
            ok = sum(1 for x in v if x is True or (not isinstance(x, bool) and x >= 1 - 1e-9))
            if ok < len(v):
                bits.append(f"{k} {ok}/{len(v)}")
    noisy = []
    for n in (fams.get("noisy") or [])[:3]:   # "T1.flap_2m.s0:BookingApiTrafficLow" -> "flap_2m (BookingApiTrafficLow)"
        scen, _, names = n.partition(":")
        parts = scen.split(".")
        noisy.append((parts[1] if len(parts) > 2 else scen) + (f" ({names})" if names else ""))
    if noisy:
        bits.append("paged in quiet scenarios: " + ", ".join(noisy))
    text = " · ".join(bits)
    return text if len(text) <= 220 else text[:220] + " …"


def run_extras(details: dict | None, order: list[str]) -> dict:
    """req_scores, the per-requirement s list (aligned with `order`), req_why, preservation_fails, kept."""
    reqs = (details or {}).get("requirements") or {}
    out = {"req_scores": {rid: rd.get("s") for rid, rd in reqs.items()},
           "reqs": [round(float(reqs[rid]["s"]), 3) if rid in reqs and reqs[rid].get("s") is not None else None
                    for rid in order]}
    why_ = {rid: why(rd) for rid, rd in reqs.items() if (rd.get("s") or 0) < 1 - 1e-9}
    out["req_why"] = {k: v for k, v in why_.items() if v}
    fails = [it["item"] for it in (details or {}).get("preservation_items") or [] if not it.get("ok")]
    if fails:
        out["preservation_fails"] = fails[:12] + ([f"… {len(fails) - 12} more"] if len(fails) > 12 else [])
    if (details or {}).get("kept"):
        out["kept"] = details["kept"]
    if (details or {}).get("tamper"):
        out["req_why"] = {rid: "tamper: " + "; ".join(map(str, details["tamper"]))[:200] for rid in order}
    return out


# ---------------------------------------------------------------------------------------------- catalog
class ExpertCatalog:
    """Labels and metadata of one expert task directory."""

    def __init__(self, task_dir: str, leaderboard: bool = False):
        self.task_dir, self.tid = task_dir, os.path.basename(task_dir.rstrip("/"))
        with open(os.path.join(task_dir, "tests", "spec.json")) as fh:
            self.spec = json.load(fh)
        with open(os.path.join(task_dir, "instruction.md")) as fh:
            self.instruction = strip_comments(fh.read())
        self.leaderboard = leaderboard
        sources = queue_tickets(self.instruction)
        for p in sorted(glob.glob(os.path.join(task_dir, "environment", "workspace", "monitoring", "docs",
                                               "oncall-survey-*.md"))):
            with open(p) as fh:
                sources += survey_answers(fh.read())
        for s in sources:
            s["words"] = words(s["text"])
        df: dict[str, int] = {}
        for s in sources:
            for w in s["words"]:
                df[w] = df.get(w, 0) + 1
        idf = {w: math.log((len(sources) + 1) / (n + 0.5)) for w, n in df.items()}
        reqs = self.spec["requirements"]
        got = assign([[score(signature(r), s, idf) for s in sources] for r in reqs]) if sources else {}
        self.reqs = []
        for i, r in enumerate(reqs):
            src = sources[got[i]] if i in got else None
            row = {"id": r["id"], "kind": r["kind"], "group": "diagnosis" if r.get("ticket") else "routine",
                   "weight": r["weight"], "title": src["title"] if src else summary(r), "summary": summary(r)}
            if src:
                row["ref"] = src["ref"]
            self.reqs.append(row)

    @property
    def order(self) -> list[str]:
        return [r["id"] for r in self.reqs]

    def meta(self) -> dict:
        fam = self.spec.get("family", "")
        return {"tier": "expert", "difficulty": "expert", "workflow": FAMILY_NAMES.get(fam, fam), "family": fam,
                "leaderboard": self.leaderboard, "instruction": self.instruction, "requirements": self.reqs,
                "checks": []}


# ---------------------------------------------------------------------------------------------- grading
def grade_with(task_dir: str, workspace: str, timeout: int = 900) -> tuple[dict, dict]:
    """Grade `workspace` with the task's own tests/grade.py (the verifier's grader), in a subprocess."""
    tests = os.path.join(task_dir, "tests")
    with tempfile.TemporaryDirectory(prefix="af-regrade-") as tmp:
        out, det = os.path.join(tmp, "reward.json"), os.path.join(tmp, "details.json")
        p = subprocess.run([sys.executable, "-B", os.path.join(tests, "grade.py"), "--workspace", workspace,
                            "--spec", os.path.join(tests, "spec.json"), "--hidden", os.path.join(tests, "hidden"),
                            "--out", out, "--details", det], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError(f"grader failed ({p.returncode}): {p.stderr.strip()[-300:]}")
        with open(out) as fh:
            keys = json.load(fh)
        details = {}
        if os.path.isfile(det):
            with open(det) as fh:
                details = json.load(fh)
        return keys, details


def instruction_seen(traj: dict, instruction: str) -> bool | None:
    """Whether the run's prompt contains `instruction` (None when the trajectory records no user message)."""
    users = [st.get("message") for st in (traj.get("steps") or []) if st.get("source") == "user"][:3]
    if not users:
        return None
    text = " ".join(m if isinstance(m, str) else " ".join(str(p.get("text", "")) for p in m if isinstance(p, dict))
                    for m in users if m)
    norm = lambda s: " ".join(s.split())  # noqa: E731
    return norm(instruction) in norm(text)
