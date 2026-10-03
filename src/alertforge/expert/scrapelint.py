"""Gate lint for the scraped world of a built task (scraped.py): every hidden group, the preservation replay's
exercise groups included, has to look like something a Prometheus scraped.

1. completeness: a service with an `up` target at 1 has every series family it exports in this world (request
   counters for the kinds that serve requests, the per-pod families its rules read, its exporters), unless the
   group declares the gap (`bare`);
2. coverage: at every minute one of its targets is up, some request counter of the service has a sample (a card's
   own traffic can't stop short of its targets' lifetime);
3. consistency: an app series is only sampled while the target it was scraped from is up (a target that is down or
   gone has stale or no series, as Prometheus writes them);
4. reach: no rule in the repo reads a metric about a service that no scenario carries;
5. ingress: the README says this Prometheus scrapes the ingress, so every HTTP service with a target up has the
   controller's request counters (`nginx_ingress_controller_requests`).
"""

from __future__ import annotations

import json
import os
import re

from ..promsim import STALE
from .scraped import FAMILY, INGRESS, SERVES
from .xscen import dec

_LAB = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
MAX_REPORTS = 12


def _parse(g: dict) -> list[tuple[str, dict, list]]:
    out = []
    for s in g["input_series"]:
        name, inner = s["series"].split("{", 1)
        out.append((name, dict(_LAB.findall(inner)), dec(s["values"])))
    return out


def _num(v) -> bool:
    return v is not None and v != STALE


def check_groups(plan, groups: list[dict]) -> list[str]:
    bad: list[str] = []
    for g in groups:
        series = _parse(g)
        bare = g.get("bare", {})
        ups: dict[str, list[tuple[dict, list]]] = {}
        fams: dict[str, set] = {}
        for name, lab, vals in series:
            svc = lab.get("service")
            if name == "up" and svc in plan.svcs and lab.get("job") == svc:
                ups.setdefault(svc, []).append((lab, vals))
            if svc and name in FAMILY:
                fams.setdefault(svc, set()).add(FAMILY[name])
        for svc, targets in ups.items():
            n = max(len(v) for _, v in targets)
            live = [m for m in range(n) if any(m < len(v) and v[m] == 1 for _, v in targets)]
            if not live:
                continue
            skip = set(bare.get(svc, []))
            for fam in sorted(plan.need[svc] - fams.get(svc, set()) - skip):
                bad.append(f"{g['name']}: {svc} has targets up and no {fam} series")
            for t in plan.templates:
                if t["service"] == svc and not any(
                        name == t["metric"] and all(lab.get(k) == t["labels"][k] for k in t["key"])
                        for name, lab, _ in series):
                    bad.append(f"{g['name']}: {svc} has targets up and no {t['metric']}")
            req = SERVES.get(plan.svcs[svc].kind)
            if req and req not in skip:
                rs = [v for name, lab, v in series if lab.get("service") == svc and FAMILY.get(name) == req]
                gap = next((m for m in live if not any(m < len(v) and _num(v[m]) for v in rs)), None)
                if gap is not None:
                    bad.append(f"{g['name']}: {svc} has a target up at minute {gap} and no request series sample")
            if req == "http" and req not in skip and not any(name == INGRESS and lab.get("service") == svc
                                                             for name, lab, _ in series):
                bad.append(f"{g['name']}: {svc} has targets up and no ingress series")
        by_target = {(lab.get("service"), lab.get("instance"), lab.get("pod")): vals
                     for name, lab, vals in series if name == "up" and lab.get("pod")}
        for name, lab, vals in series:
            if name == "up" or name.startswith("container_") or not lab.get("pod"):
                continue
            up = by_target.get((lab.get("service"), lab.get("instance"), lab.get("pod")))
            if up is None or lab.get("job", lab.get("service")) != lab.get("service"):
                continue
            m = next((m for m, v in enumerate(vals) if _num(v) and not (m < len(up) and up[m] == 1)), None)
            if m is not None:
                bad.append(f"{g['name']}: {name}{{pod={lab['pod']}}} sampled at minute {m} while its target is not up")
    return bad


def lint(m: dict) -> list[str]:
    """The checks above on a built task (build_task's result), exercise groups included."""
    plan = m.get("scrape")
    if plan is None:
        return []
    groups = list(m["groups"])
    ex = os.path.join(m["hidden_dir"], "exercise.json")
    if os.path.exists(ex):
        with open(ex) as fh:
            groups += json.load(fh)
    bad = check_groups(plan, groups)
    for svc, metrics in sorted(plan.uncovered.items()):
        for metric in metrics:
            bad.append(f"rules read {metric} about {svc}, which no scenario carries")
    if len(bad) > MAX_REPORTS:
        bad = bad[:MAX_REPORTS] + [f"... {len(bad) - MAX_REPORTS} more scraped-world findings"]
    return [f"scraped world: {b}" for b in bad]
