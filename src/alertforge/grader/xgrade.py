"""Expert-tier grader (v0.2): one promtool pass over every scenario group, snapshot-based scoring.

Usage (inside the verifier, via tests/grade.py when spec.json has "tier": "expert"):
    python3 -I /tests/grade.py --workspace /workspace/monitoring --spec /tests/spec.json \
        --hidden /tests/hidden --out /logs/verifier/reward.json --details /logs/verifier/details.json

Reward (unchanged in shape from the core tier, plus the penalty-only `kept` term):
    s_r      = 1 if q_r = 1 else clip((q_r - q_r^pristine) / (1 - q_r^pristine), -0.25, 1)
    pen      = sum over kept items of (1 - k) * 3 * 0.25
    progress = max(0, (0.5 + 0.5 P) * (sum w_r s_r - pen) / sum w_r)
    reward   = 0.6 [all s_r = 1, all k = 1, P = 1, syntax ok, no tamper] + 0.4 progress
Reported next to it: `diagnosis` (weighted mean s over the symptom tickets, spec `ticket: true`) and `routine`
(the same over everything else).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time

from . import amcheck, notify, snap
from .loader import YamlError, index_rules, safe_yaml, scan
from .norm import core_eff, effective, preserved, same_rule
from .promrun import check_rules
from .static import am_hashes, am_tamper, rule_tamper
from .tamper import alerts_tamper
from .xscore import Ctx, alert_q, annotation_q, labels_cover, outcome_q, storm_q

CATEGORIES = ["req_outcome", "req_storm", "req_alerts", "req_repairs", "req_recording", "req_routing",
              "req_inhibit", "req_migration", "req_absence", "req_triage"]
KEYS = ["reward", "solved", "outcome", "progress", "diagnosis", "routine", "preservation", *CATEGORIES, "fire_rate", "silent_rate",
        "check_pass_rate", "syntax_ok", "tamper", "kept_broken", "masked_rule_count", "snapshots",
        "window_reruns", "labelless_pages"]
S_MIN = -0.25
KEPT_PENALTY = 3 * 0.25


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 1.0


def zero_keys(tamper: bool) -> dict:
    d = {k: 0.0 for k in KEYS}
    d["tamper"] = 1.0 if tamper else 0.0
    return d


class ExpertGrader:
    def __init__(self, spec: dict, hidden_dir: str):
        self.spec = spec
        with open(os.path.join(hidden_dir, "scenarios.json")) as fh:
            self.groups = json.load(fh)
        self.hidden_dir = hidden_dir
        self._exercise_groups: list[dict] | None = None
        self._cache: dict[str, tuple[dict, dict]] = {}

    def _exercise(self) -> list[dict]:
        """Scenarios that only the preservation replay reads (each service down, gone, erroring)."""
        if self._exercise_groups is None:
            p = os.path.join(self.hidden_dir, "exercise.json")
            self._exercise_groups = []
            if os.path.exists(p):
                with open(p) as fh:
                    self._exercise_groups = json.load(fh)
        return self._exercise_groups

    def grade(self, workspace: str) -> tuple[dict, dict]:
        ws = scan(workspace)
        if ws.fingerprint in self._cache and not ws.tamper:
            return self._cache[ws.fingerprint]
        tmp = tempfile.mkdtemp(prefix="af-xgrade-")
        try:
            out = self._grade(ws, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._cache[ws.fingerprint] = out
        return out

    # ------------------------------------------------------------------ flow
    def _grade(self, ws, tmp: str) -> tuple[dict, dict]:
        t0 = time.time()
        spec = self.spec
        syntax_err = check_rules(ws.rule_files, os.path.join(tmp, "check"))
        ok_files = [f for f in ws.rule_files if f.groups is not None and f.path not in syntax_err]
        alerts, records, every = index_rules(ok_files)
        tamper = list(ws.tamper) + rule_tamper(every, set(spec.get("input_metrics", [])))
        tamper += alerts_tamper([ref.rule for ref in every])
        am, am_ok, am_msg, am_path = None, False, "alertmanager.yml missing", ""
        if ws.am_text is not None:
            try:
                am = safe_yaml(ws.am_text)
                if isinstance(am, dict):
                    am_path = amcheck.route_config_path(tmp, ws.am_text)
                    am_ok, am_msg = amcheck.check_config(am_path, ws.am_text)
                    tamper += am_tamper(am)
                else:
                    am_msg = "alertmanager.yml is not a mapping"
            except YamlError as e:
                am_msg = str(e)
        syntax_ok = not syntax_err and am_ok
        details: dict = {"syntax_errors": syntax_err, "alertmanager": am_msg, "tamper": tamper, "requirements": {}}
        if tamper:
            details["seconds"] = round(time.time() - t0, 3)
            return zero_keys(True), details
        results, masks = snap.run_groups(self.groups, ok_files, tmp, "a")
        reruns: dict[tuple, list | None] = {}
        wanted: set[tuple] = set()

        def rerun(g, a, b):
            key = (g["name"], a, b)
            if key not in reruns:
                wanted.add(key)
                return None
            return reruns[key]

        ctx = Ctx(alerts, am if am_ok else None, am_path, ws.am_text, results, masks, rerun)
        self._score_all(ctx)
        if wanted:
            by_name = {g["name"]: g for g in self.groups}
            extra = []
            for k, (gname, a, b) in enumerate(sorted(wanted)):
                g = by_name[gname]
                probes = [{"type": "snap", "id": f"rr{k}.{t}", "t": t} for t in range(a, b + 1)]
                extra.append({"name": f"rr{k}|{gname}", "input_series": g["input_series"], "probes": probes,
                              "_key": (gname, a, b)})
            res2, _ = snap.run_groups(extra, ok_files, tmp, "b", masks={e["name"]: masks.get(e["_key"][0], [])
                                                                        for e in extra})
            for e in extra:
                r = res2.get(e["name"])
                reruns[e["_key"]] = None if r is None else [r[p["id"]] for p in e["probes"]]
        scores = self._score_all(ctx)
        P, pitems = self._preservation(every, alerts, am if am_ok else None, am_path, ws.am_text, masks,
                                       ok_files, tmp)
        kept = self._kept(alerts)
        return self._reward(scores, P, pitems, kept, syntax_ok, results, masks, reruns, ctx, details, t0)

    def _score_all(self, ctx: Ctx) -> list:
        out = []
        by_req: dict[str, list[dict]] = {}
        for g in self.groups:
            by_req.setdefault(g["req"], []).append(g)
        cl = set(self.spec.get("cluster_level", []))
        for r in self.spec["requirements"]:
            gs = by_req.get(r["id"], [])
            q, fams = self._score(r, gs, ctx, cl)
            out.append((r, q, fams))
        return out

    def _score(self, r, gs, ctx: Ctx, cl) -> tuple[float, dict]:
        kind = r["kind"]
        if kind == "outcome":
            return outcome_q(r, gs, ctx, cl)
        if kind == "storm":
            return storm_q(r, gs, ctx)
        if kind in ("new_alert", "repair"):
            return alert_q(r, gs, ctx)
        if kind == "annotation":
            return annotation_q(r, gs, ctx)
        if kind == "recording":
            vals = [1.0 if ctx.snap(g["name"], p["id"]) is True else 0.0 for g in gs for p in g["probes"]]
            return _mean(vals), {"values": _mean(vals)}
        if kind == "route":
            return self._route_q(r, ctx)
        if kind == "inhibit":
            return self._inhibit_q(r["inhibit"]["cases"], ctx)
        if kind == "migration":
            items = [self._labels_match(ctx, it) for it in r["migration"]["items"]]
            return _mean(items), {"items": items}
        if kind == "absence":
            ab = r["absence"]
            gone = all(not ctx.alerts.get(n) for n in ab.get("alerts", []))
            routes = not (isinstance(ctx.am, dict) and _route_mentions(ctx.am.get("route"), ab.get("route_values", [])))
            return (1.0 if gone and routes else 0.0), {"rules_gone": gone, "routes_gone": routes}
        if kind == "relabel":
            items = [self._relabel_ok(ctx, it) for it in r["relabel"]["items"]]
            return _mean(items), {"items": items}
        raise ValueError(kind)

    # ------------------------------------------------------------------ kinds
    def _route_q(self, r, ctx: Ctx) -> tuple[float, dict]:
        rt = r["route"]
        am = ctx.am
        pos, neg = [], []
        for c in rt.get("pos", []):
            got = ctx.receivers(c["labels"]) if am else None
            pos.append(amcheck.jaccard(set(c["expect"]), set(got)) if got is not None else 0.0)
        for c in rt.get("chain", []):
            defs = ctx.alerts.get(c["alert"], [])
            if not defs or not am:
                pos.append(0.0)
                continue
            best = 0.0
            for d in defs:
                lab = d.rule.get("labels") if isinstance(d.rule.get("labels"), dict) else {}
                lab = {**{str(k): str(v) for k, v in (d.group.get("labels") or {}).items()},
                       **{str(k): str(v) for k, v in lab.items()}, "alertname": c["alert"], "service": c["service"]}
                got = ctx.receivers(lab)
                want = c["expect"].get(lab.get("severity", ""), c["expect"].get("*", []))
                best = max(best, amcheck.jaccard(set(want), set(got)) if got is not None else 0.0)
            pos.append(best)
        for c in rt.get("neg", []):
            got = ctx.receivers(c["labels"]) if am else None
            neg.append(got is not None and not (set(got) & set(c["forbid"])))
        gate = 1.0
        if am and rt.get("retired") and _receiver_used(am.get("route"), rt["retired"], bool(rt.get("retired_literal"))):
            gate = 0.0
        if rt.get("legacy_team"):
            gate *= 0.0 if (not am or _legacy_left(am.get("route"), rt["legacy_team"])) else 1.0
        if rt.get("integrations") and am:
            gate *= 1.0 if _integrations_ok(am, rt["integrations"]) else 0.0
        q = gate * _mean(pos) * _mean(1.0 if x else 0.0 for x in neg)
        return q, {"pos": [round(x, 3) for x in pos], "neg": neg, "gate": gate}

    def _inhibit_q(self, cases, ctx: Ctx) -> tuple[float, dict]:
        sup, notsup, scope = [], [], True
        for c in cases:
            # `alts`: the same case with another accepted name for an alert in it (a Down rule fixed under its
            # pristine name). Held back under any spelling counts; not held back has to hold under every one.
            oks = []
            for v in [c] + list(c.get("alts") or []):
                got = None
                if isinstance(ctx.am, dict):
                    alerts = ([v["source"]] if v.get("source") else []) + [v["target"]]
                    mask = notify.inhibited(ctx.am, alerts)
                    got = None if mask is None else mask[-1]
                oks.append(got is not None and got == c["expect"])
            ok = any(oks) if c["expect"] else all(oks)
            (sup if c["expect"] else notsup).append(ok)
            if c.get("scope") and not ok:
                scope = False   # the request was limited to one service; a rule that reaches past it is not the fix
        q = _mean(1.0 if x else 0.0 for x in sup) * _mean(1.0 if x else 0.0 for x in notsup) * (1.0 if scope else 0.0)
        return q, {"suppressed": sup, "not_suppressed": notsup, "scope": scope}

    @staticmethod
    def _labels_match(ctx: Ctx, it: dict) -> float:
        """A label move counts only when it is a move: the item's labels are on a definition, every definition
        about the moved services carries one of the label sets the item's alert should have (an old-team copy
        kept next to the new one keeps paging the old team), and there are as many such definitions as before.
        A definition carries a label set when it has those labels with those values (labels_cover: the README
        names the labels an alert must have and never asks for no others)."""
        name, want = it["alert"], it["labels"]
        svcs = it.get("svcs")
        defs = [d for d in ctx.alerts.get(name, []) if svcs is None or any(mentions(d.rule, s) for s in svcs)]
        raws = [effective(d.rule, d.group)["labels"] for d in defs]
        own = svcs or []
        if not any(labels_cover(lab, want) and _own_or_none(lab, own, want) for lab in raws):
            return 0.0
        allowed = it.get("allowed")
        if allowed is not None and not all(any(labels_cover(lab, a) and _own_or_none(lab, own, a) for a in allowed)
                                           for lab in raws):
            return 0.0
        if it.get("n") is not None and len(defs) != it["n"]:
            return 0.0
        return 1.0

    @staticmethod
    def _relabel_ok(ctx: Ctx, it: dict) -> float:
        # `alt_labels`: other label sets that mean the same to the text (README: `critical` pages like `page`).
        # Every definition of the alert has to carry the change (a copy left behind keeps the old behaviour),
        # with the pristine number of definitions; one of them has to be the rule the item is about (`same`).
        wants = [it["labels"]] + list(it.get("alt_labels") or []) if it.get("labels") is not None else None
        svcs = [it["svc"]] if it.get("svc") else []
        defs = ctx.alerts.get(it["alert"], [])
        if not defs or (it.get("n") is not None and len(defs) != it["n"]):
            return 0.0
        same = False
        for d in defs:
            eff = effective(d.rule, d.group)
            if wants is not None and not any(labels_cover(eff["labels"], w) and _own_or_none(eff["labels"], svcs, w)
                                             for w in wants):
                return 0.0
            if it.get("runbooks") is not None and eff["ann"].get("runbook_url", "").strip() not in it["runbooks"]:
                return 0.0
            same = same or not it.get("same") or same_rule(core_eff(d.rule, d.group), it["same"])
        return 1.0 if same else 0.0

    def _kept(self, alerts) -> list[dict]:
        """An alert the queue asked to leave as is keeps its expression, `for`, group fields and severity; a severity
        written in another spelling the README gives for it (spec `severity_alias`: `critical` is the older `page`)
        is the same severity."""
        out = []
        alias = self.spec.get("severity_alias") or {}
        for k in self.spec.get("kept", []):
            ok = False
            for d in alerts.get(k["alert"], []):
                eff = core_eff(d.rule, d.group, ("severity",))
                ok = ok or any(same_rule(e, k["want"]) for e in _spellings(eff, alias))
            out.append({"alert": k["alert"], "k": 1.0 if ok else 0.0})
        return out

    def _preservation(self, every, alerts, am, am_path, am_text, masks, files=(), tmp=None) -> tuple[float, list[dict]]:
        pres = self.spec.get("preserve", {})
        have: dict[str, list] = {}
        for ref in every:
            eff = preserved(ref.rule, ref.group)
            have.setdefault((eff["k"], eff["n"]), []).append(eff)
        items, by_name = [], {}
        for it in pres.get("rules", []):
            cands = [_own_service(c, it.get("svc")) for c in have.get((it["kind"], it["name"]), [])]
            ok = any(same_rule(e, it) for e in cands) and (it["kind"] != "alert" or len(cands) == it.get("n", 1))
            items.append({"item": f"rule:{it['name']}", "ok": ok})
            by_name.setdefault((it["kind"], it["name"]), []).append(len(items) - 1)
        if tmp:
            self._replay(pres.get("rules", []), items, by_name, have, files, os.path.join(tmp, "replay"), masks)
        touched = set(self.spec.get("touch", []))
        for name in sorted({n for v in masks.values() for n in v} - touched):
            items.append({"item": f"eval_error:{name}", "ok": False})
        want = pres.get("am", {})
        cur = am_hashes(am) if isinstance(am, dict) else {"global": None, "receivers": {}}
        if want:
            items.append({"item": "am:global", "ok": cur["global"] == want.get("global")})
            for name, h in sorted(want.get("receivers", {}).items()):
                items.append({"item": f"am:receiver:{name}", "ok": cur["receivers"].get(name) == h})
        for i, c in enumerate(pres.get("route_cases", [])):
            got = amcheck.resolve(am_path, am_text, c["labels"]) if isinstance(am, dict) else None
            ok = got is not None and sorted(set(got)) == sorted(c["expect"])
            if ok and c.get("group_by") is not None:
                # an untouched route's grouping is part of what it does (how its channel reads)
                leaves = notify.walk(am, c["labels"])
                ok = bool(leaves) and all((gb if gb == notify.ALL else sorted(set(gb))) == c["group_by"]
                                          for _, gb, _ in leaves)
            items.append({"item": f"route_case:{i}", "ok": ok})
        for i, c in enumerate(pres.get("inhibit_cases", [])):
            mask = notify.inhibited(am, [c["source"], c["target"]]) if isinstance(am, dict) else None
            items.append({"item": f"inhibit_case:{i}", "ok": mask is not None and mask[-1] == c["expect"]})
        if not items:
            return 1.0, items
        return sum(1 for x in items if x["ok"]) / len(items), items

    def _replay(self, rules, items, by_name, have, files, workdir, masks) -> None:
        """Untouched alerts the fast path missed go through the behavioural replay (replay.py), each alert as a
        whole: all its pristine definitions against all its current ones. When they fire the same, every
        definition of it passes (a static label the expression already yields, an operand reorder, a copy that
        fires as the same alert)."""
        cases = []
        for key, idx in by_name.items():
            bad = [i for i in idx if not items[i]["ok"]]
            svc = rules[idx[0]].get("svc")
            cur = [_own_service(c, svc) for c in have.get(key, [])]
            if key[0] != "alert" or not bad or not cur or not all(rules[i].get("form") for i in idx):
                continue
            cases.append((key, bad, [rules[i]["form"] for i in idx],
                          [{"expr": c["expr"], "for": c["for"], "kff": c["kff"], "labels": c["labels"]} for c in cur]))
        if not cases:
            return
        from .replay import equivalent
        same = equivalent([(key, ps, cs) for key, _, ps, cs in cases], self.groups + self._exercise(), files,
                          workdir, masks)
        for key, bad, _, _ in cases:
            if key in same:
                for i in bad:
                    items[i].update(ok=True, replayed=True)

    # ------------------------------------------------------------------ reward
    def _reward(self, scores, P, pitems, kept, syntax_ok, results, masks, reruns, ctx, details, t0):
        s_all, by_cat = [], {c: [] for c in CATEGORIES}
        for r, q, fams in scores:
            qp = float(r.get("q_pristine", 0.0))
            q = round(q, 6)   # q_pristine is stored at this precision: an untouched requirement must give s = 0 exactly
            s = 1.0 if q >= 1 - 1e-9 else (q - qp) / (1 - qp) if qp < 1 else 0.0
            s = max(S_MIN, min(1.0, s))
            s_all.append((s, r["weight"]))
            by_cat[r["category"]].append((s, r["weight"]))
            details["requirements"][r["id"]] = {"kind": r["kind"], "q": round(q, 6), "s": round(s, 6),
                                                "q_pristine": qp, "families": fams}
        wsum = sum(w for _, w in s_all) or 1
        pen = sum((1 - k["k"]) * KEPT_PENALTY for k in kept)
        phi = (sum(s * w for s, w in s_all) - pen) / wsum
        progress = max(0.0, (0.5 + 0.5 * P) * phi)
        solved = all(s >= 1 - 1e-9 for s, _ in s_all) and all(k["k"] == 1.0 for k in kept)
        outcome = 1.0 if (solved and P >= 1 - 1e-9 and syntax_ok) else 0.0
        keys = zero_keys(False)
        keys.update({"outcome": outcome, "solved": outcome, "progress": round(progress, 6),
                     "preservation": round(P, 6), "syntax_ok": 1.0 if syntax_ok else 0.0,
                     "reward": round(0.6 * outcome + 0.4 * progress, 6)})
        for c in CATEGORIES:
            v = by_cat[c]
            keys[c] = round(max(0.0, sum(s * w for s, w in v) / sum(w for _, w in v)), 6) if v else 0.0
        # the symptom tickets (diagnosis) and the rest of the queue, each as a weighted mean of s: among runs that
        # don't solve a task, these say whether the progress came from diagnosing or from the routine edits
        for key, pick in (("diagnosis", True), ("routine", False)):
            v = [(s, r["weight"]) for (r, _, _), (s, _) in zip(scores, s_all) if bool(r.get("ticket")) == pick]
            keys[key] = round(max(0.0, sum(s * w for s, w in v) / sum(w for _, w in v)), 6) if v else 0.0
        fire = [f for _, _, fams in scores for k, f in fams.items() if k.startswith("F_") or k == "fire"]
        silent = [f for _, _, fams in scores for k, f in fams.items() if k.startswith("S_") or k == "silent"]
        keys["fire_rate"] = round(_mean(fire), 6) if fire else 0.0
        keys["silent_rate"] = round(_mean(silent), 6) if silent else 0.0
        keys["check_pass_rate"] = round(_mean([x["ok"] for x in pitems] + [q for _, q, _ in scores]), 6)
        keys["kept_broken"] = float(sum(1 for k in kept if k["k"] < 1))
        keys["masked_rule_count"] = float(len({n for v in masks.values() for n in v}))
        keys["snapshots"] = float(sum(1 for g in self.groups for p in g["probes"] if p["type"] != "count"))
        keys["window_reruns"] = float(len(reruns))
        keys["labelless_pages"] = float(sum(1 for g in self.groups for p in g["probes"] if p.get("family") == "G"
                                            and ctx.snap(g["name"], p["id"])))
        details.update({"preservation_items": pitems, "kept": kept, "masked_rules": masks,
                        "seconds": round(time.time() - t0, 3)})
        return keys, details


def _spellings(eff: dict, alias: dict) -> list[dict]:
    """The effective rule, plus the same rule with its severity written in each other spelling of that severity."""
    sev = eff["labels"].get("severity")
    same = {a for a, b in alias.items() if sev in (a, b)} | {b for a, b in alias.items() if sev in (a, b)}
    return [eff] + [{**eff, "labels": {**eff["labels"], "severity": s}} for s in sorted(same - {sev})]


def _own_or_none(labels: dict, svcs: list, want: dict) -> bool:
    """Other labels may come along (labels_cover), but a static `service` is the alert's own one (README: from the
    expression or set statically): another value makes it a different service's alert."""
    return "service" in want or labels.get("service") in (None, *svcs)


def _own_service(eff: dict, svc: str | None) -> dict:
    """An inhibition's source and target alerts may carry their own service as a static label (the label their
    `equal:` matches on): for such a preserved alert (spec `svc`) that label is left out of the comparison, on the
    pristine side too (build.preserve). Any other value of it is still a change."""
    if not svc or eff["labels"].get("service") != svc:
        return eff
    return {**eff, "labels": {k: v for k, v in eff["labels"].items() if k != "service"}}


def _walk_routes(node, depth=0):
    if not isinstance(node, dict) or depth > 60:
        return
    yield node
    for c in node.get("routes") or []:
        yield from _walk_routes(c, depth + 1)


def _route_mentions(root, values: list[str]) -> bool:
    for n in _walk_routes(root):
        parts = [str(m) for m in n.get("matchers") or []]
        for k in ("match", "match_re"):
            if isinstance(n.get(k), dict):
                parts += [str(v) for v in n[k].values()]
        if any(v in p for v in values for p in parts):
            return True
    return False


def _unconditional(route: dict) -> bool:
    return not (route.get("matchers") or route.get("match") or route.get("match_re"))


def _receiver_used(root, name: str, literal: bool = False) -> bool:
    """Whether a retired receiver is still in use. A literal ticket ("nothing may route to it") counts every route
    that names it. Otherwise only a route where an alert can still end up at it: one whose own or inherited
    receiver it is, that an alert can reach (no earlier unconditional sibling without `continue` takes everything
    first) and that does not hand every alert on to an unconditional child."""
    if literal:
        return any(n.get("receiver") == name for n in _walk_routes(root))

    def walk(node, inherited, depth=0) -> bool:
        if not isinstance(node, dict) or depth > 60:
            return False
        recv = node.get("receiver") or inherited
        kids = []
        for k in node.get("routes") or []:
            if not isinstance(k, dict):
                continue
            kids.append(k)
            if _unconditional(k) and not k.get("continue"):
                break   # the siblings after it are never reached
        if recv == name and not any(_unconditional(k) for k in kids):
            return True
        return any(walk(k, recv, depth + 1) for k in kids)

    return walk(root, None)


def _legacy_left(root, team: str) -> bool:
    for n in _walk_routes(root):
        text = json.dumps({k: n.get(k) for k in ("matchers", "match", "match_re")})
        if team in text:
            return any(n2.get("match") or n2.get("match_re") for n2 in _walk_routes(n))
    return True


def _integrations_ok(am: dict, names: dict) -> bool:
    by = {r.get("name"): r for r in am.get("receivers") or [] if isinstance(r, dict)}
    for name, key in names.items():
        r = by.get(name)
        if r is None or not r.get(key):
            return False
    return True


def mentions(rule: dict, svc: str) -> bool:
    """A rule is about a service when its expression names it or it carries it as a static label."""
    lab = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
    return str(lab.get("service", "")) == svc or bool(
        re.search(rf"(?<![\w-]){re.escape(svc)}(?![\w-])", str(rule.get("expr", ""))))


