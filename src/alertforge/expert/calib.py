"""Calibration of a built expert task against its own pristine and oracle states.

  derive_storms       storm sets read off the pristine snapshots (H20: every service whose page fires in the
                      unreachable cluster has to be held back, not only the ones a packet happens to list)
  label-less exempts  alertnames that fire label-less in the oracle state of a scenario
  background_alerts   untouched alerts of a ticket's service that fire anyway (never the fix, left out of counts)
  q_pristine          per requirement, from one grade of the pristine workspace
"""

from __future__ import annotations

import math
import shutil
import tempfile

from ..grader.loader import parse_rule_file

PAGE_SEV = ("page", "critical")
TICKET_SHARE = 0.55     # BM-4: diagnosis (the symptom tickets) is at least this share of a task's weight
TICKET_WEIGHT = 6
ROUTE_TICKET_WEIGHT = 4  # E3's tickets are routing/inhibition kinds, graded by cheaper case matrices


def ticket_weights(spec: dict) -> None:
    """Symptom tickets weigh 6 (E3's route and inhibition tickets 4), then all of them are scaled up together,
    rounded up, until they hold TICKET_SHARE of the total weight, so that a run doing only the routine work never
    outranks one that diagnoses the tickets."""
    reqs = spec["requirements"]
    tick = [r for r in reqs if r.get("ticket")]
    if not tick:
        return
    for r in tick:
        r["weight"] = ROUTE_TICKET_WEIGHT if spec["family"] == "E3" and r["kind"] in ("route", "inhibit") else TICKET_WEIGHT
    routine = sum(r["weight"] for r in reqs if not r.get("ticket"))
    tw = sum(r["weight"] for r in tick)
    need = TICKET_SHARE / (1 - TICKET_SHARE) * routine
    if tw < need:
        k = need / tw
        for r in tick:
            r["weight"] = math.ceil(r["weight"] * k - 1e-9)


def _snapshots(groups: list, mapping: dict, tag: str) -> dict:
    from ..grader import snap
    files = [parse_rule_file(k, v) for k, v in sorted(mapping.items()) if k.startswith("rules/")]
    tmp = tempfile.mkdtemp(prefix=f"af-{tag}-")
    try:
        res, _ = snap.run_groups(groups, files, tmp, tag)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # a group without a result is a promtool failure (timeout, no junit, a temp dir it can't write), not "nothing
    # fired": calibrating on it would silently drop exemptions and storm sets, so the build stops here
    bad = sorted(g["name"] for g in groups if res.get(g["name"]) is None)
    if bad:
        raise RuntimeError(f"calibration ({tag}): promtool returned no result for {len(bad)} group(s) {bad[:3]}; "
                           "check AF_PROMTOOL and that TMPDIR is a writable directory")
    return res


def derive_storms(spec: dict, groups: list, pristine_map: dict) -> None:
    """A storm group with `derive: {cluster, paged}`: `muted` is every service with a page firing in that cluster
    during the storm window of the pristine state, minus the services that must still page (`paged`, down
    elsewhere too). The requirement's service table keeps exactly the services the group grades."""
    gs = [g for g in groups if (g.get("storm") or {}).get("derive")]
    if not gs:
        return
    res = _snapshots(gs, pristine_map, "storm")
    graded: dict[str, set] = {}
    for g in gs:
        d = g["storm"].pop("derive")
        fired = set()
        for p in g["probes"]:
            if p.get("family") != "storm":
                continue
            for a in (res.get(g["name"]) or {}).get(p["id"]) or []:
                if a.get("cluster") == d["cluster"] and a.get("severity") in PAGE_SEV and a.get("service"):
                    fired.add(a["service"])
        keep = sorted(set(d.get("paged", [])))
        g["storm"]["muted"] = sorted(fired - set(keep))
        g["storm"]["failing"] = g["storm"]["muted"] + keep
        graded.setdefault(g["req"], set()).update(g["storm"]["failing"])
    for r in spec["requirements"]:
        if r["id"] in graded:
            r["storm"]["services"] = {s: v for s, v in r["storm"]["services"].items() if s in graded[r["id"]]}


def calibrate(spec, groups, pristine_map, oracle_map, tests_dir) -> None:
    """Storm sets, label-less exemptions (alertnames that fire label-less in the oracle state of a scenario: a
    defect that pages label-less in the pristine state must not exempt itself), then q_pristine per requirement."""
    from .build import grade_map, write_tests
    derive_storms(spec, groups, pristine_map)
    gprobe = [{**g, "probes": [p for p in g["probes"] if p.get("family") == "G"]} for g in groups]
    gprobe = [g for g in gprobe if g["probes"]]
    res = _snapshots(gprobe, oracle_map, "cal")
    exempt: dict[str, set] = {}
    for g in gprobe:
        got = (res.get(g["name"]) or {}).get(g["probes"][0]["id"]) or []
        exempt.setdefault(g["name"], set()).update(a.get("alertname", "") for a in got)
    for g in groups:
        if g["name"] in exempt:
            g["g_exempt"] = sorted(exempt[g["name"]])
    background_alerts(spec, groups, pristine_map, oracle_map)
    write_tests(tests_dir, spec, groups)
    keys, det = grade_map(spec, tests_dir, pristine_map)
    for r in spec["requirements"]:
        r["q_pristine"] = det["requirements"][r["id"]]["q"]


def background_alerts(spec, groups, pristine_map, oracle_map) -> None:
    """Untouched alerts of the ticket's service that fire in a scenario in the pristine or the oracle state
    (a burn alert on the same outage, say) can never be the fix: they are left out of that group's counts."""
    from ..grader.xscore import CLASS_SEV
    reqs = {r["id"]: r for r in spec["requirements"] if r["kind"] == "outcome"}
    og = [g for g in groups if g["req"] in reqs]
    seen: dict[str, set] = {}
    for mp in (pristine_map, oracle_map):
        res = _snapshots(og, mp, "bg")
        for g in og:
            oc = reqs[g["req"]]["outcome"]
            for p in g["probes"]:
                if not str(p.get("family", "")).startswith(("F_", "S_")) or p["type"] == "count":
                    continue
                cls = p.get("class") or oc["services"][p["svc"]]["class"]
                for pid in p.get("any_of", [p["id"]]):
                    for a in (res.get(g["name"]) or {}).get(pid) or []:
                        if a.get("service") == p["svc"] and a.get("severity") in CLASS_SEV[cls]:
                            seen.setdefault(g["name"], set()).add(a.get("alertname", ""))
    for g in og:
        touch = set(reqs[g["req"]]["outcome"]["touch"]) | {reqs[g["req"]]["outcome"].get("alertname")}
        ex = sorted(seen.get(g["name"], set()) - touch)
        if ex:
            g["o_exempt"] = ex
