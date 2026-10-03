"""The repo the incident packets were taken from (BM-6, FA-6, S7, FA-4).

Every packet shows the world before this week's queue was worked on: the rules, labels and routing the task's
`environment/` ships. PagerDuty rows, Slack titles, the rule-manager log and the rules API paste are rendered from
that state (every item's `break_` applied), never from the fixed one. A page went to whatever rota the alert's
`team` label and the routing tree sent it to then; an alert the repo doesn't have never paged anyone.
"""

from __future__ import annotations

import re

from ..grader import notify
from . import evidence as ev

_BY = re.compile(r"\bby \(([^)]*)\)")


def state(w) -> tuple[dict, dict]:
    """(rule files, Alertmanager config) as the queue found them; computed once per world."""
    if "then" not in w.notes:
        from .build import materialize
        w.notes["then"] = materialize(w, list(w.items), set())
    return w.notes["then"]


def rules(w, name: str) -> list[tuple]:
    """(file spec, group, rule) for every pristine definition of an alert."""
    files, _ = state(w)
    return [(f, g, r) for f in files.values() for g in f.groups for r in g["rules"] if r.get("alert") == name]


def exists(w, name: str) -> bool:
    return bool(rules(w, name))


def labels(w, alert: str, svc: str | None = None, cluster: str | None = None) -> dict | None:
    """The labels a firing `alert` about `svc` carries in the pristine repo: static labels (group labels merged) and
    what the expression keeps (`service`, `cluster`). None when the repo has no such alert."""
    defs = rules(w, alert)
    if svc is not None:
        defs = [d for d in defs if f'service="{svc}"' in str(d[2].get("expr", ""))] or defs
    if not defs:
        return None
    _, g, r = defs[0]
    out = {"alertname": alert}
    out.update({str(k): str(v) for k, v in {**(g.get("labels") or {}), **(r.get("labels") or {})}.items()})
    expr = str(r.get("expr", ""))
    if svc is not None:
        out.setdefault("service", svc)
    if cluster and any("cluster" in m.group(1) for m in _BY.finditer(expr)):
        out["cluster"] = cluster
    return out


def page_alerts(w, svc: str, skip: set = frozenset()) -> list[str]:
    """Alerts about `svc` alone that page in the pristine repo (its own Down, error ratio, burn page)."""
    files, _ = state(w)
    out = []
    for f in files.values():
        for g in f.groups:
            for r in g["rules"]:
                name = r.get("alert")
                if not name or name in skip or name in out:
                    continue
                sev = str({**(g.get("labels") or {}), **(r.get("labels") or {})}.get("severity", ""))
                if sev in ("page", "critical") and f'service="{svc}"' in str(r.get("expr", "")):
                    out.append(name)
    return sorted(out)


def receivers(w, lab: dict) -> list[tuple[str, object]]:
    """[(receiver, group_by)] the pristine routing tree sends these labels to."""
    _, am = state(w)
    got = notify.walk(am, lab) or []
    return [(r, gb) for r, gb, _ in got]


def pager(w, lab: dict) -> str | None:
    """The PagerDuty service a page with these labels opened an incident on (`claims`, `sre`), or None."""
    for r, _ in receivers(w, lab):
        if r.startswith("pagerduty-"):
            return r[len("pagerduty-"):]
    return None


def group_by(w, lab: dict, receiver_prefix: str = "pagerduty-") -> list:
    """The grouping on the leaf that delivers these labels to a receiver of that kind: the team's own one first (a
    page also reaches #sre-fyi, which groups the root's way)."""
    got = receivers(w, lab)
    own = [x for x in got if x[0] == f"{receiver_prefix}{lab.get('team')}"]
    for r, gb in own + [x for x in got if x[0] != "slack-sre-fyi"] + got:
        if r.startswith(receiver_prefix):
            return sorted(lab) if gb == notify.ALL else list(gb)
    return ["alertname", "service"]


def title(w, lab: dict, status: str = "firing", n: int = 1, receiver_prefix: str = "pagerduty-") -> str:
    """Alertmanager's default title for a group of `n` alerts like `lab` on that receiver."""
    return ev.slack_title(status, [lab] * n if status == "firing" else [lab], group_by(w, lab, receiver_prefix))


def paged_team(w, svc: str) -> str | None:
    """The `team` label the service's own pages carried before this week's fixes (a service moved in the split
    still carries its old team; one whose rules lost the label carries none)."""
    for name in page_alerts(w, svc):
        lab = labels(w, name, svc)
        if lab is not None:
            return lab.get("team")
    tr = getattr(w, "trigger", None) or {}
    return tr["old"] if svc in tr.get("moved", []) else w.svc(svc).team


def team_word(w, svc: str) -> str:
    """The team to name in prose about who got (or didn't get) paged for `svc`."""
    return paged_team(w, svc) or w.svc(svc).team


def has_down_dedupe(w) -> bool:
    """The pristine Alertmanager holds Down alerts back with other Down alerts (H20's planted rule)."""
    _, am = state(w)
    return any(any(".*Down" in m or ".+Down" in m for m in (r.get("source_matchers") or []))
               for r in am.get("inhibit_rules") or [])
