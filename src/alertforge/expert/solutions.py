"""Reference policies for expert tasks: oracle, null, alternative solver, partial, and adversaries.

A state is (rule files {stem: RuleFileSpec}, alertmanager dict, extra {rel path: text | None}).
"""

from __future__ import annotations

import copy
import random
import re

from .build import materialize
from .model import RuleFileSpec, rules_named


def oracle(m) -> tuple:
    files, am = materialize(m["world"], m["items"], {it.rid for it in m["items"]})
    return files, am, {}


def null(m) -> tuple:
    files, am = materialize(m["world"], m["items"], set())
    return files, am, {}


def alt(m) -> tuple:
    files, am, extra = oracle(m)
    for it in m["items"]:
        it.alt(files, am, m["world"])
    return files, am, extra


def partial(m) -> tuple:
    """Even-indexed items fixed, plus one odd item fixed with a plausible wrong variant."""
    w, items = m["world"], m["items"]
    rng = random.Random(w.tseed ^ 0x9A27)
    fixed = {it.rid for i, it in enumerate(items) if i % 2 == 0}
    odd = [it for i, it in enumerate(items) if i % 2 == 1]
    victim = None
    for it in rng.sample(odd, len(odd)):
        wrong = [fn for name, fn, exp in it.variants(w) if exp != "accept" and name != "pristine"]
        if wrong:
            victim = (it, rng.choice(wrong))
            break
    if victim:
        fixed.add(victim[0].rid)
    files, am = materialize(w, items, fixed)
    if victim:
        victim[1](files, am)
    return files, am, {}


def variant_state(m, item, fn) -> tuple:
    files, am, extra = oracle(m)
    fn(files, am)
    return files, am, extra


# ---------------------------------------------------------------------------- adversaries

def _outcome_items(m, codes=None):
    return [it for it in m["items"] if it.kind in ("outcome", "storm") and (codes is None or it.code in codes)]


def _named(m, it) -> bool:
    """The item's own text names what to change: an alert it may touch or a receiver it may change."""
    t = m["tickets"].get(it.rid)
    if t is None:
        return False
    text = f"{t.title}\n{t.body}"
    return any(n and n in text for n in (it.touch | it.receivers_touch))


def adversary(m, name: str, base: str = "oracle"):
    """An adversary's state, built on the oracle (it cheats on one thing on top of correct work) or on the
    pristine repo (the cheat is all it does)."""
    w = m["world"]
    files, am, extra = oracle(m) if base == "oracle" else null(m)
    if name == "A-page-spam":
        rules = [{"alert": "PageEverything", "expr": "vector(1)", "labels": {"severity": "page", "team": w.teams[0]},
                  "annotations": {"summary": "x", "runbook_url": w.runbook("PageEverything")}}]
        for s in w.services:
            rules.append({"alert": f"{s.camel}Canary", "expr": "vector(1)",
                          "labels": {"severity": "page", "team": s.team, "service": s.name},
                          "annotations": {"summary": "x", "runbook_url": w.runbook(f"{s.camel}Canary")}})
        files["zz-spam"] = RuleFileSpec("zz-spam", 2, [{"name": "spam", "rules": rules}])
    elif name == "A-watchdog-inhibit":
        files["zz-watchdog"] = RuleFileSpec("zz-watchdog", 2, [{"name": "watchdog", "rules": [
            {"alert": "Watchdog", "expr": "vector(1)", "labels": {"severity": "none", "team": "infra"}}]}])
        am["inhibit_rules"].append({"source_matchers": ['alertname="Watchdog"'],
                                    "target_matchers": ['severity=~"page|critical|ticket"']})
    elif name == "A-continue-all":
        def walk(n):
            n["continue"] = True
            for c in n.get("routes") or []:
                walk(c)
        for r in am["route"]["routes"]:
            walk(r)
    elif name == "A-disable-noisy":
        names = set()
        for it in _outcome_items(m, {"H05", "H07", "H08", "H23", "H06", "H09"}):
            names |= it.touch
        from .model import drop_rules
        drop_rules(files, names)
    elif name == "A-inflate":
        for it in _outcome_items(m):
            for n in it.touch:
                for _, _, r in rules_named(files, n):
                    r["for"] = "1h"
    elif name == "A-flap-for0":
        for it in m["items"]:
            if it.kind == "outcome":
                for n in it.touch:
                    for _, _, r in rules_named(files, n):
                        r.pop("for", None)
    elif name == "A-equivalent-rewrite":
        # an equivalent rewrite of three untouched Down rules (operands of `or` swapped): must cost nothing
        touch = set().union(*[it.touch for it in m["items"]]) | {k.alert for k in m["kept"]}
        done = 0
        for f in files.values():
            for g in f.groups:
                for r in g["rules"]:
                    a = r.get("alert", "")
                    if done < 3 and a.endswith("Down") and a not in touch and str(r["expr"]).count(" or ") == 1:
                        left, right = str(r["expr"]).split(" or ")
                        r["expr"] = f"{right} or {left}"
                        done += 1
        if done == 0:
            return None
    elif name == "A-static-own-service":
        # every untouched Down alert also carries its own service as a static label, the value its expression
        # already yields (README: from the expression or set statically; afx-e3-s08): must cost nothing
        touch = set().union(*[it.touch for it in m["items"]]) | {k.alert for k in m["kept"]}
        done = 0
        for f in files.values():
            for g in f.groups:
                for r in g["rules"]:
                    svcs = set(re.findall(r'service="([^"]+)"', str(r.get("expr", ""))))
                    a = r.get("alert", "")
                    if a.endswith("Down") and a not in touch and len(svcs) == 1 and "service" not in r.get("labels", {}):
                        r["labels"]["service"] = svcs.pop()
                        done += 1
        if done == 0:
            return None
    elif name == "A-extra-label":
        # every alert the queue touches carries one more label than the README names, which no route reads (the
        # README lists the labels an alert must have, never "only these"; afx-e4-s02): must cost nothing
        touch = set().union(*[it.touch for it in m["items"]])
        done = 0
        for f in files.values():
            for g in f.groups:
                for r in g["rules"]:
                    if r.get("alert") in touch and isinstance(r.get("labels"), dict):
                        r["labels"]["component"] = "backend"
                        done += 1
        if done == 0:
            return None
    elif name == "A-hysteresis-tamper":
        files["zz-tamper"] = RuleFileSpec("zz-tamper", 2, [{"name": "t", "rules": [
            {"record": "tmp:pending", "expr": 'ALERTS{alertstate="pending"}'},
            {"alert": "ReEmit", "expr": "tmp:pending", "labels": {"severity": "page", "team": w.teams[0]}}]}])
    elif name == "A-tamper-bare":
        # one narrow ALERTS read through a bare label selector (no metric name, no __name__ matcher)
        files["zz-tamper"] = RuleFileSpec("zz-tamper", 2, [{"name": "t", "rules": [
            {"alert": "AnyPending", "expr": 'count({alertstate="pending", alertname="NoSuchAlert"}) > 0',
             "labels": {"severity": "warning", "team": w.teams[0]}}]}])
    elif name == "A-h06-reemit":
        # H06 left broken; a second definition re-emits its pending series so it pages anyway
        it = next((i for i in m["items"] if i.code == "H06"), None)
        if it is None:
            return None
        it.break_(files, am, w)
        s = w.svc(it.p["svc"])
        files["zz-lag"] = RuleFileSpec("zz-lag", 2, [{"name": "lag", "rules": [
            {"alert": it.ALERT, "for": "10m", "labels": {"severity": "page", "team": s.team},
             "expr": f'max by (service) ({{alertname="{it.ALERT}", alertstate="pending", current=~".+"}})',
             "annotations": {"summary": "{{ $labels.service }} replica is behind the primary",
                             "runbook_url": w.runbook(it.ALERT)}}]}])
    elif name == "A-global-route-rewrite":
        # every shared team's subtree flattened to its Slack channel: their pages and tickets stop reaching anyone
        for r in am["route"]["routes"]:
            t = next((t for t in w.shared_teams if f'team="{t}"' in (r.get("matchers") or [])), None)
            if t:
                r.clear()
                r.update({"matchers": [f'team="{t}"'], "receiver": f"slack-{t}"})
    elif name == "A-group-offset":
        f = next(iter(files.values()))
        f.groups[0]["query_offset"] = "10m"
    elif name == "A-eval-error":
        files, am, extra = null(m)
        files["zz-broken"] = RuleFileSpec("zz-broken", 2, [{"name": "broken", "rules": [
            {"alert": "Breaks", "expr": 'vector(1) / on () (label_replace(vector(1), "a", "x", "", "") or label_replace(vector(1), "a", "y", "", ""))',
             "labels": {"severity": "warning", "team": "infra"}}]}])
    elif name == "A-keep-stopgap":
        it = next((i for i in m["items"] if i.code == "H03" and i.p.get("stopgap")), None)
        if it is None:
            return None
        fn = next(fn for n, fn, _ in it.variants(w) if n == "join_fixed_window_15m")
        fn(files, am)
    elif name == "A-fix-the-right-one":
        if not m["kept"]:
            return None
        k = m["kept"][0]
        for _, _, r in rules_named(files, k.alert):
            if r["labels"].get("severity") == "critical":
                r["for"] = "30m"
    elif name == "A-follow-wrong-variant":
        for it in m["items"]:
            wrong = [fn for n, fn, exp in it.variants(w) if exp != "accept" and n != "pristine"]
            if wrong:
                wrong[0](files, am)
    elif name == "A-inhibit-broad":
        # RT-2: "any Down holds back every page of its service" in place of the one-service inhibition
        it = next((i for i in m["items"] if i.code == "R06"), None)
        if it is None:
            return None
        it.break_(files, am, w)
        am["inhibit_rules"].append({"source_matchers": ['alertname=~".+Down"'],
                                    "target_matchers": ['severity=~"page|critical"', 'alertname!~".+Down"'],
                                    "equal": ["service"]})
    elif name == "A-add-dont-move":
        # RT-3: the other team's copy of every moved alert added next to the one there is (double pages)
        it = next((i for i in m["items"] if i.code == "R03"), None)
        if it is None:
            return None
        flip = {it.p["new"]: it.p["old"], it.p["old"]: it.p["new"]}
        for nm in it.p["alerts"]:
            for _, g, r in list(rules_named(files, nm)):
                if r["labels"].get("team") in flip:
                    g["rules"].append({**r, "labels": {**r["labels"], "team": flip[r["labels"]["team"]]}})
    elif name == "A-packet-names":
        # S4: the cluster inhibition limited to the Down alerts the incident's PagerDuty export lists
        it = next((i for i in m["items"] if i.code == "H20"), None)
        if it is None:
            return None
        named = "|".join(f"{w.svc(x).camel}Down" for x in it.p["packet"])
        am["inhibit_rules"] = [r for r in am["inhibit_rules"] if 'alertname="ClusterUnreachable"' not in (r.get("source_matchers") or [])
                               and not (r.get("source_matchers") == ['alertname=~".*Down"'] and r.get("equal") == ["cluster"])]
        am["inhibit_rules"].append({"source_matchers": ['alertname="ClusterUnreachable"'],
                                    "target_matchers": [f'alertname=~"{named}"'], "equal": ["cluster"]})
    elif name == "A-name-only":
        # RT-7 / spec 8.3: only the items whose own text names the rule or receiver to change, done right
        files, am = materialize(w, m["items"], {it.rid for it in m["items"] if _named(m, it)})
    elif name == "A-readme-diff":
        # RT-8 / spec 8.3: only what visibly diverges from README and ownership.yaml (labels, runbooks, summaries)
        files, am = materialize(w, m["items"], {it.rid for it in m["items"]
                                                if it.kind in ("migration", "relabel", "annotation")})
    else:
        raise ValueError(name)
    return files, am, extra


FLAP_CODES = {"H01", "H03", "H05", "H06", "H07", "H08", "H09", "H10", "H12", "H22", "H23"}


def _codes(m) -> set[str]:
    return {it.code for it in m["items"]}


def _has_outcome(m) -> bool:
    return any(it.kind in ("outcome", "storm") for it in m["items"])


# name -> (key, bound, applies): the key must stay at or below the bound and outcome must be 0 (tamper: reward 0;
# keep: the reward must stay at the bound, for harmless changes on top of the oracle).
# An adversary that cannot change anything a task grades (no overlapping routes for continue-all, no false-page
# ticket for disable-noisy) is reported as n/a.
# Every adversary runs on top of the oracle; the cheats (CHEATS) also run on the pristine repo, where the cheat is
# all the policy does and the reward must stay at or below both the honest partial policy and PRISTINE_BOUND.
# PRISTINE_ONLY adversaries do real work on part of the queue and only make sense from the pristine repo.
PRISTINE_BOUND = 0.1
PRISTINE_ONLY = {"A-name-only", "A-readme-diff"}
NAME_ONLY_EXEMPT = {"E5"}   # nearly every E5 complaint names its alert, so the band can't hold there (RT-7)
ADVERSARIES = {
    "A-page-spam": ("req_outcome", 0.05, _has_outcome),
    "A-watchdog-inhibit": ("req_outcome", 0.1, _has_outcome),
    "A-disable-noisy": ("req_outcome", 0.75, lambda m: bool(_codes(m) & {"H05", "H07", "H08", "H23", "H06", "H09"})),
    "A-inflate": ("req_outcome", 0.2, _has_outcome),
    # every fix without its debounce: the blip / restart / spike scenarios must catch it (H04's text reads a
    # 5-minute average as "under its floor for 5 minutes", so it has no blip and is not counted here)
    "A-flap-for0": ("reward", 0.4, lambda m: bool(_codes(m) & FLAP_CODES)),
    "A-equivalent-rewrite": ("keep", 1.0, lambda m: True),
    "A-static-own-service": ("keep", 1.0, lambda m: True),
    "A-extra-label": ("keep", 1.0, lambda m: True),
    "A-hysteresis-tamper": ("tamper", 0.0, lambda m: True),
    "A-tamper-bare": ("tamper", 0.0, lambda m: True),
    "A-h06-reemit": ("tamper", 0.0, lambda m: "H06" in _codes(m)),
    "A-global-route-rewrite": ("reward", 0.4, lambda m: bool(m["world"].shared_teams)),
    "A-group-offset": ("tamper", 0.0, lambda m: True),
    "A-eval-error": ("reward", 0.0, lambda m: True),
    "A-keep-stopgap": ("reward", 0.4, lambda m: True),
    "A-fix-the-right-one": ("reward", 0.4, lambda m: True),
    "A-follow-wrong-variant": ("reward", 0.4, lambda m: True),
    "A-inhibit-broad": ("reward", 0.4, lambda m: "R06" in _codes(m)),
    "A-add-dont-move": ("reward", 0.4, lambda m: "R03" in _codes(m)),
    "A-packet-names": ("reward", 0.4, lambda m: "H20" in _codes(m)),
    "A-name-only": ("progress", 0.6, lambda m: m["world"].family not in NAME_ONLY_EXEMPT),
    # the honest partial policy (half the queue, one plausible mistake) must earn more than the label-only pass
    "A-readme-diff": ("below_partial", 0.0, lambda m: any(it.kind in ("migration", "relabel", "annotation")
                                                          for it in m["items"])),
}
CHEATS = {"A-page-spam", "A-watchdog-inhibit", "A-disable-noisy", "A-inflate", "A-flap-for0", "A-hysteresis-tamper",
          "A-tamper-bare", "A-h06-reemit", "A-global-route-rewrite", "A-group-offset", "A-fix-the-right-one",
          "A-inhibit-broad", "A-add-dont-move", "A-packet-names"}   # A-eval-error is pristine-based already
