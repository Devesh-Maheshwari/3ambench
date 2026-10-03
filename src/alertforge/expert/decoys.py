"""Rationale comments on healthy rules and routes, and live look-alikes of the planted defects.

Queue items leave a comment on the rule they break (the reason someone gave when they made the change). A real
repo has the same kind of note on plenty of rules nobody is complaining about, so a handful of healthy rules and
one or two Alertmanager entries get one too. Every note here is true of the rule it sits on and agrees with the
README; none of them changes what a rule does (comments are dropped before anything is hashed or replayed).

`live` (knob `decoys`) adds healthy rules and one route that use the constructs the defects use: an average of
per-pod values, an average over a service's targets, per-pod grouping on a Slack-only route. Each is right for
what it does and says why. None is in any item's touch set, so preservation keeps them as they are: rewriting one
because it looks like a known bug costs the run its preservation item.
"""

from __future__ import annotations

import math
import random
import re

from . import knobs
from .model import World, home_group

_SVC = re.compile(r'service="([^"]+)"')


def _ticket(w: World, rng: random.Random) -> str:
    return f"{w.company.key}-{rng.randint(1100, 2900)}"


def _pct(x: str) -> str:
    return f"{float(x) * 100:g}%"


def _notes(r: dict, w: World, rng: random.Random) -> list[tuple[str, str]]:
    """(category, comment) candidates for one healthy rule; empty when nothing true can be said about it."""
    expr = str(r.get("expr", ""))
    m = _SVC.search(expr)
    s = next((x for x in w.services if m and x.name == m.group(1)), None)
    name = str(r.get("alert") or r.get("record") or "")
    sev = (r.get("labels") or {}).get("severity")
    out = []
    if "alert" in r and s is not None:
        if name == f"{s.camel}Down":
            out.append(("down", f"for: {r.get('for')} so a rolling restart doesn't page; {s.name} cycles one pod at a time"))
            out.append(("down", f"absent() too: {s.name} was scaled to zero by mistake once and the sum just went away"))
            if "cluster" in expr:
                out.append(("down", f"per cluster: {s.name} gone from one cluster is a page even if the other is fine"))
        elif name == f"{s.camel}HighErrorRatio":
            if s.family == "grpc":
                out.append(("ratio", "Cancelled isn't in the list on purpose: clients hang up on long polls"))
            else:
                out.append(("ratio", "5xx only; 429s are the rate limiter doing its job"))
            out.append(("ratio", f"{_pct(s.err_thr)} from {_ticket(w, rng)}; callers retry {s.name}, a few errors "
                                  f"never reach a user"))
        elif name.endswith("ErrorBudgetBurnFast"):
            out.append(("burn", "two windows so it stops paging a few minutes after the errors stop"))
            out.append(("burn", "factors from the SRE workbook's multiwindow table, don't tune per service"))
        elif name.endswith("ErrorBudgetBurnSlow"):
            out.append(("burn", "ticket, not page: at this rate there are days of budget left, not hours"))
        elif name.endswith("LatencyHigh") and sev == "warning":
            out.append(("latency", "p99 across the whole service; the per-route view is on the team board"))
        elif name.endswith("LatencyHigh") and sev == "critical":
            out.append(("latency", "1s: past this the gateway starts timing callers out"))
        elif name.endswith("DbConnectionsHigh") and sev == "warning" and w.family != "E5":
            out.append(("db", "max, not avg: one pod with a full pool already queues requests"))
            out.append(("db", "divides by the pool max from the app config, so a resize doesn't need a rule change"))
        elif name == "ContainerMemoryNearLimit":
            out.append(("mem", "ticket only: the platform Prometheus pages on OOM kills"))
            out.append(("mem", f"for: {r.get('for')}, {s.name}'s nightly batch sits near the limit for a few minutes"))
    elif "alert" in r and (r.get("labels") or {}).get("team") == "infra":
        out.append(("fleet", "FIXME threshold copied from the upstream example, nobody tuned it"))
        out.append(("fleet", "infra reads these in the morning, nothing here should wake anyone"))
    elif "record" in r:
        if name.startswith("slo:sli_error"):
            out.append(("rec", "one record per window so each burn alert doesn't run its own rate()"))
        elif name.startswith("job:"):
            out.append(("rec", "keep: finance reads this one for the monthly availability report"))
        elif name.endswith("p99_5m"):
            out.append(("rec", "p99 panel on svc-overview; alerts compute their own quantile"))
        elif name.startswith("service:"):
            out.append(("rec", "svc-overview's error panel reads this"))
    return out


def _planted(w: World, items: list) -> tuple[int, int, int]:
    """(rules in the queue's touch sets that carry a comment in the starting repo, touched rules, all rules):
    what the breakage itself leaves, counted once per world on a scratch copy."""
    if "planted" not in w.notes:
        files, am = w.state()
        for it in items:
            it.break_(files, am, w)
        touch = set().union(*[it.touch for it in items]) if items else set()
        rules = [r for f in files.values() for g in f.groups for r in g["rules"]]
        on_t = sum(1 for r in rules if (r.get("alert") or r.get("record")) in touch and r.get("_comment"))
        n_t = sum(1 for r in rules if (r.get("alert") or r.get("record")) in touch)
        w.notes["planted"] = (on_t, n_t, len(rules))
    return w.notes["planted"]


def add(files: dict, am: dict, w: World, items: list) -> None:
    """Comments on 5-8 rules no item touches (more when the queue's own comments would otherwise stand out) and on
    one or two untouched Alertmanager entries. Deterministic in the task seed and the item set, so the pristine and
    oracle states carry the same comments."""
    rng = random.Random(w.tseed ^ 0xC033E7)
    touch = set().union(*[it.touch for it in items]) if items else set()
    rtouch = set().union(*[it.route_touch for it in items]) if items else set()
    rcv = set().union(*[it.receivers_touch for it in items]) if items else set()
    cands, seen = [], set()
    for stem in sorted(files):
        for g in files[stem].groups:
            for r in g["rules"]:
                name = r.get("alert") or r.get("record")
                if name in touch or r.get("_comment"):
                    continue
                for cat, text in _notes(r, w, rng):
                    cands.append((cat, text, r))
    rng.shuffle(cands)
    want, used_cat, used_rule = rng.randint(5, 8), {}, set()
    # healthy notes so that the comments on touched rules stay within 15 points of the touched-rule share (the
    # gate's lint allows 20): house comments already on untouched rules count towards it
    on_t, n_t, n_all = _planted(w, items)
    house = sum(1 for f in files.values() for g in f.groups for r in g["rules"]
                if r.get("_comment") and (r.get("alert") or r.get("record")) not in touch)
    if on_t:
        want = max(want, math.ceil(on_t / (n_t / max(n_all, 1) + 0.15)) - on_t - house)
    cap = 2 if want <= 8 else 3
    for cat, text, r in cands:
        if len(used_rule) >= want:
            break
        if text in seen or id(r) in used_rule or used_cat.get(cat, 0) >= cap:
            continue
        r["_comment"] = [text]
        seen.add(text)
        used_rule.add(id(r))
        used_cat[cat] = used_cat.get(cat, 0) + 1
    _am(am, w, rng, rtouch, rcv)


def _am(am: dict, w: World, rng: random.Random, rtouch: set, rcv: set) -> None:
    routes = am["route"]["routes"]
    shared = [r for r in routes if not r.get("_comment") and any(f'team="{t}"' in (r.get("matchers") or [])
                                                                 for t in w.shared_teams if t not in rtouch)]
    notes = 0
    if shared:
        r = rng.choice(shared)
        t = next(t for t in w.shared_teams if f'team="{t}"' in r["matchers"])
        r["_comment"] = [f"{t} wanted tickets in Slack as well as Jira ({_ticket(w, rng)})"]
        notes += 1
    pds = [x for x in am.get("receivers", []) if x.get("name", "").startswith("pagerduty-")
           and x["name"] != "pagerduty-sre" and x["name"] not in rcv and not x.get("_comment")
           and x["name"][len("pagerduty-"):] not in rtouch]
    if pds and (notes == 0 or rng.random() < 0.6):
        rng.choice(pds)["_comment"] = [f"routing key rotated in {_ticket(w, rng)}"]


# ---------------------------------------------------------------------------- live decoys

DECOY_GROUP_BY = ["alertname", "pod", "service"]


def live(w: World, items: list) -> None:
    """Two healthy ticket rules on services no item uses, and per-pod grouping on the Slack leaf of a team no item
    routes for. Recorded in `w.notes["decoys"]` (rules) and `w.notes["decoy_route"]` (the preserved grouping)."""
    if not knobs.on("decoys"):
        return
    rng = random.Random(w.tseed ^ 0xDEC0)
    tr = w.trigger or {}
    touch = set().union(*[it.touch for it in items]) if items else set()
    busy = set(w.notes.get("busy", set())) | set(tr.get("moved", []))
    cands = [s for s in w.services if s.name not in busy and s.team != tr.get("old") and s.kind in ("api", "worker")]
    rng.shuffle(cands)
    made = []
    for s, build in zip(cands, (_half_down, _cpu_throttled)):
        rule = build(w, s)
        if rule["alert"] in touch or any(r.get("alert") == rule["alert"] for f in w.files.values() for g in f.groups
                                         for r in g["rules"]):
            continue
        home_group(w, s)["rules"].append(rule)
        made.append(rule["alert"])
    w.notes["decoys"] = made
    _route(w, items, rng)


def _half_down(w: World, s) -> dict:
    name = f"{s.camel}TargetsHalfDown"
    sel = f'up{{service="{s.name}"}}'
    return {"alert": name, "expr": f"avg by (service) ({sel}) < 0.5 and on (service) count by (service) ({sel}) >= 2",
            "for": "10m", "labels": {"severity": "ticket", "team": s.team},
            "annotations": {"summary": "{{ $labels.service }} has lost half of its targets", "runbook_url": w.runbook(name)},
            "_comment": ["average over the targets: half of them gone is a ticket even while the rest carry the load"]}


def _cpu_throttled(w: World, s) -> dict:
    name = f"{s.camel}CpuThrottled"
    sel = f'{{service="{s.name}",container="{s.name}"}}'
    expr = (f"avg by (service) (rate(container_cpu_cfs_throttled_periods_total{sel}[5m])"
            f" / rate(container_cpu_cfs_periods_total{sel}[5m])) > 0.25")
    return {"alert": name, "expr": expr, "for": "30m", "labels": {"severity": "ticket", "team": s.team},
            "annotations": {"summary": "{{ $labels.service }} pods spend over a quarter of their CPU periods throttled",
                            "runbook_url": w.runbook(name)},
            "_comment": ["average the pods: they all run with the same limit, so the mean is what we size it by"]}


def _route(w: World, items: list, rng: random.Random) -> None:
    rtouch = set().union(*[it.route_touch for it in items]) if items else set()
    legacy = w.notes.get("legacy_match_team")
    teams = [t for t in sorted(set(w.teams) | set(w.shared_teams)) if t not in rtouch and t != legacy
             and t not in ((w.trigger or {}).get("old"), (w.trigger or {}).get("new"))]
    rng.shuffle(teams)
    for t in teams:
        r = next((x for x in w.am["route"]["routes"] if x.get("matchers") == [f'team="{t}"']), None)
        leaf = (r or {}).get("routes", [])[-1:] or [None]
        if leaf[0] is None or leaf[0].get("matchers") or leaf[0].get("group_by"):
            continue
        leaf[0]["group_by"] = list(DECOY_GROUP_BY)
        leaf[0]["_comment"] = ["tickets and warnings per pod in Slack, so the channel shows which pod; pages group "
                               "per service above"]
        w.notes["decoy_route"] = {"team": t, "group_by": sorted(DECOY_GROUP_BY)}
        return


# ---------------------------------------------------------------------------- knob strip-locators

def _comments(obj, out: set) -> set:
    if isinstance(obj, dict):
        c = obj.get("_comment")
        out.update([c] if isinstance(c, str) else (c or []))
        for v in obj.values():
            _comments(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _comments(v, out)
    return out


def strip_locators(files: dict, am: dict, w: World, items: list) -> None:
    """Knob `strip-locators`: a comment that only the broken state has (what someone wrote when they made the
    change a ticket is about) is dropped, unless it is a TODO a ticket points at."""
    if "fixed_comments" not in w.notes:
        f0, a0 = w.state()
        add(f0, a0, w, items)
        w.notes["fixed_comments"] = _comments([[g for f in f0.values() for g in f.groups], a0], set())
    keep = w.notes["fixed_comments"]

    def walk(obj):
        if isinstance(obj, dict):
            c = obj.get("_comment")
            lines = [c] if isinstance(c, str) else (c or [])
            left = [x for x in lines if x in keep or "TODO(" in x]
            if lines and left != lines:
                if left:
                    obj["_comment"] = left
                else:
                    obj.pop("_comment")
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk([[g for f in files.values() for g in f.groups], am])
