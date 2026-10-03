"""Build one expert task directory (Harbor format) from a family and seed.

  world + items → pristine / oracle states → workspace text and packets → hidden checks → calibration
  (label-less exemptions per group, q_pristine per requirement) → tests/ with the grader → solution/solve.sh
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import shutil
import tempfile
import uuid

from ..grader.loader import parse_rule_file
from ..grader.norm import pristine_entry
from ..grader.static import am_hashes
from ..grader.xgrade import KEYS, ExpertGrader
from ..render import IMAGES, dump, patch_script, write_tree
from . import decoys, families, knobs, scraped, texts
from .calib import calibrate, ticket_weights
from .items_triage import Kept
from .model import World
from .readme import SEVERITY_ALIAS
from .vocab import flavor
from .yamlfmt import Style, emit, strip

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADER_FILES = ["__init__.py", "loader.py", "static.py", "promrun.py", "amcheck.py", "grade.py",
                "snap.py", "notify.py", "matchers.py", "tamper.py", "norm.py", "replay.py", "xscore.py",
                "xgrade.py"]
GEN_VERSION = "0.2.0"
STYLES = {1: Style(quote_dur=True, flow_labels=True), 2: Style(block_expr=True), 3: Style(block_expr=True, block_at=60)}


# ---------------------------------------------------------------------------- states

def materialize(w: World, items: list, fixed: set[str]) -> tuple[dict, dict]:
    files, am = w.state()
    decoys.add(files, am, w, items)
    for it in items:
        if it.rid not in fixed:
            it.break_(files, am, w)
    if knobs.on("strip-locators"):
        decoys.strip_locators(files, am, w, items)
    return files, am


def render_rules(files: dict) -> dict[str, str]:
    out = {}
    for stem, f in sorted(files.items()):
        groups = [g for g in f.groups if g["rules"]]
        if not groups:
            continue
        if f.gen == 3:
            doc = {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
                   "metadata": {"name": stem, "namespace": "monitoring", "labels": {"role": "alert-rules"}},
                   "spec": {"groups": groups}}
            out[f"rules/{stem}.{f.ext}"] = emit(doc, STYLES[3], f.header)
        else:
            out[f"rules/{stem}.{f.ext}"] = emit({"groups": groups}, STYLES[f.gen], f.header)
    return out


def render_am(am: dict) -> str:
    return emit(am, Style(), ["Shared Alertmanager config (this repo, platform and data Prometheus servers).",
                              "Receivers belong to the teams; ask before touching someone else's."])


def state_map(files: dict, am: dict) -> dict[str, str]:
    return {**render_rules(files), "alertmanager/alertmanager.yml": render_am(am)}


# ---------------------------------------------------------------------------- hidden spec

def _touch(items) -> set[str]:
    out = set()
    for it in items:
        out |= it.touch
    return out


def preserve(w: World, items: list, pristine_files: dict, pristine_am: dict, kept: list) -> dict:
    touch = _touch(items) | {k.alert for k in kept}
    own = {}   # alert -> its service: an inhibition item's source/target alerts may carry it statically (xgrade)
    for it in items:
        own.update(it.label_touch)
    rules, counts = [], {}
    for f in pristine_files.values():
        for g in f.groups:
            for r in g["rules"]:
                kind = "alert" if "alert" in r else "record"
                counts[(kind, r[kind])] = counts.get((kind, r[kind]), 0) + 1
    for f in pristine_files.values():
        for g in f.groups:
            for r in g["rules"]:
                kind = "alert" if "alert" in r else "record"
                if r[kind] in touch:
                    continue
                svc = own.get(r[kind]) if kind == "alert" else None
                rr = strip(r)
                if svc and isinstance(rr.get("labels"), dict) and rr["labels"].get("service") == svc:
                    rr = {**rr, "labels": {k: v for k, v in rr["labels"].items() if k != "service"}}
                e = pristine_entry(rr, strip(g))
                if svc:
                    e["svc"] = svc
                e["n"] = counts[(kind, r[kind])]
                rules.append(e)
    rtouch = set().union(*[it.route_touch for it in items]) if items else set()
    sev_touch = set().union(*[it.route_sev_touch for it in items]) if items else set()
    rcv_touch = set().union(*[it.receivers_touch for it in items]) if items else set()
    h = am_hashes(strip(pristine_am))
    h["receivers"] = {k: v for k, v in h["receivers"].items() if k not in rcv_touch}
    from .items import wpolicy
    cases = []
    decoy = w.notes.get("decoy_route") or {}
    for t in sorted(set(w.teams) | set(w.shared_teams)):
        if t in rtouch:
            continue
        svc = next((s.name for s in w.services if s.team == t), f"{t}-svc")
        for sev in ("page", "ticket", "warning"):
            if sev in sev_touch:
                continue
            case = {"labels": {"alertname": "PreservedCheck", "severity": sev, "team": t, "service": svc},
                    "expect": wpolicy(w, t, sev)}
            if decoy.get("team") == t and sev == "warning":
                case["group_by"] = decoy["group_by"]   # the per-pod Slack grouping (a live decoy) stays
            cases.append(case)
    inh = []
    if not any(it.code == "H21" for it in items):
        s = w.services[0]
        base = {"alertname": f"{s.camel}LatencyHigh", "service": s.name, "team": s.team, "namespace": w.namespace}
        inh += [{"source": {**base, "severity": "critical"}, "target": {**base, "severity": "warning"}, "expect": True},
                {"source": {**base, "severity": "critical"}, "target": {**base, "alertname": "Other", "severity": "warning"},
                 "expect": False}]
    return {"rules": rules, "am": h, "route_cases": cases, "inhibit_cases": inh}


def _patch_preserve_n(spec):
    """Preservation entries carry `n` (pristine copies of that name) for the duplicate check."""
    return spec


# ---------------------------------------------------------------------------- build

def build_task(family: str, seed: int, out_root: str, task_id: str, master: int = 20260930, resample: int = 0,
               *, composer=None) -> dict:
    """`resample` > 0 redraws the hidden scenarios only (the world, its text and its packets stay): the gate asks
    for it when a sampled scenario fails to separate a declared-wrong variant (RT-5)."""
    w, items = (composer or families.build)(family, seed, master, task_id)
    canary = str(uuid.UUID(int=(w.tseed << 64 | (w.tseed ^ 0xE2E7)) & ((1 << 128) - 1)))
    trng = random.Random(w.tseed ^ 0x7E47)
    hrng = random.Random((w.tseed ^ 0x5EED) + 7919 * resample)
    kept = []
    if family == "E5" and trng.random() < 0.7:
        k = Kept(w, trng)
        if k.ok:
            kept.append(k)
    # packets show the repo the queue starts from (then.py): rendered after the pristine state exists
    pristine_files, pristine_am = materialize(w, items, set())
    w.notes["then"] = (pristine_files, pristine_am)
    tickets = {}
    for it in items:
        t = it.ticket(w, trng)
        t.kind, t.symptom, t.code = it.kind, it.ticket_like, it.code
        tickets[it.rid] = t
    for it in items:   # "and the slow-burn ticket for X" sits right after X's fast-burn ticket
        if it.code == "R01" and getattr(it, "tier", "") == "slow":
            fast = next((o for o in items if o.code == "R01" and getattr(o, "tier", "") == "fast"
                         and o.p.get("svc") == it.p.get("svc")), None)
            tickets[it.rid].pair_of = tickets[fast.rid] if fast is not None else None
    oracle_files, oracle_am = materialize(w, items, {it.rid for it in items})
    spec_reqs, groups = [], []
    # every group is completed with what the world's targets and exporters export (scraped.py), from its own random
    # stream: the scenario stream `hrng` and every series a card draws stay as they were
    from .exercise import exercise_groups
    plan = w.notes.get("scrape_plan", scraped.Plan)(
        w, items, (pristine_files, oracle_files), f"{w.tseed ^ scraped.SCRAPE_SEED}:{resample}")
    with scraped.active(plan):
        for it in items:
            e, gs = it.spec(w, hrng)
            spec_reqs.append(e)
            groups += gs
        exercise = exercise_groups(w, w.tseed ^ 0xE8E8, plan)
    spec = {"version": 2, "tier": "expert", "task_id": task_id, "family": family, "requirements": spec_reqs,
            "touch": sorted(_touch(items)),
            "kept": [k.spec() for k in kept], "severity_alias": dict(SEVERITY_ALIAS), "cluster_level": w.cluster_level,
            "input_metrics": sorted({s["series"].split("{")[0] for g in groups for s in g["input_series"]}),
            "preserve": preserve(w, items, pristine_files, pristine_am, kept)}
    ticket_weights(spec)
    static = static_files(w, items, tickets, kept, trng, canary)
    task_dir = os.path.join(out_root, task_id)
    if os.path.exists(task_dir):
        shutil.rmtree(task_dir)
    tests_dir = os.path.join(task_dir, "tests")
    pristine_map = {**static["workspace"], **state_map(pristine_files, pristine_am)}
    oracle_map = {**static["workspace"], **state_map(oracle_files, oracle_am)}
    write_tests(tests_dir, spec, groups, exercise)
    calibrate(spec, groups, pristine_map, oracle_map, tests_dir)
    write_tests(tests_dir, spec, groups, exercise)
    write_tree(os.path.join(task_dir, "environment", "workspace", "monitoring"), pristine_map)
    n_checks = sum(len(g["probes"]) for g in groups) + sum(
        len(r.get("route", {}).get("pos", [])) + len(r.get("route", {}).get("neg", [])) + len(r.get("route", {}).get("chain", []))
        + len(r.get("inhibit", {}).get("cases", [])) + len(r.get("migration", {}).get("items", []))
        + len(r.get("relabel", {}).get("items", [])) + len(r.get("absence", {}).get("alerts", []))
        for r in spec_reqs) + len(spec["preserve"]["rules"]) + len(spec["preserve"]["route_cases"])
    write_tree(task_dir, {"task.toml": task_toml(w, items, n_checks, canary), "instruction.md": static["instruction"],
                          "README.md": task_card(w, spec, n_checks), "environment/Dockerfile": DOCKERFILE,
                          "solution/solve.sh": patch_script(pristine_map, oracle_map, "oracle: rewrites changed files to the oracle state")})
    return {"task_id": task_id, "world": w, "items": items, "kept": kept, "spec": spec, "groups": groups,
            "hidden_dir": os.path.join(tests_dir, "hidden"), "static": static["workspace"], "pristine_map": pristine_map,
            "oracle_map": oracle_map, "n_checks": n_checks, "tickets": tickets, "resample": resample, "scrape": plan}


def static_files(w: World, items, tickets, kept, rng, canary) -> dict:
    from . import adr
    ws = {"README.md": texts.readme(w), "slo/services.yaml": texts.catalog_yaml(w), "teams/ownership.yaml": texts.ownership(w)}
    ws.update(adr.files(w))
    ws.update(texts.runbook_files(w, random.Random(w.tseed ^ 0x2B00C)))
    for name in ("af-check", "range2test"):   # a composer may ship its own copy of a helper (w.notes["helpers"])
        with open((w.notes.get("helpers") or {}).get(name) or os.path.join(PKG, "data", name)) as fh:
            ws[f"bin/{name}"] = fh.read()
    ws["tests/visible/smoke.test.yml"] = smoke_test(w)
    survey_codes = {"H06", "H08", "H09", "H10", "H23", "R10", "R11", "R12", "R13", "R18"} if w.family == "E5" else set()
    queue, survey = [], []
    for it in items:
        t = tickets[it.rid]
        ws.update(t.files)
        (survey if it.code in survey_codes else queue).append(t)
    from .prose_triage import kept as kept_text
    for k in kept:
        t = kept_text(k, w, rng)
        ws.update(t.files)
        survey.append(t)
    if w.notes.get("latency_services"):
        from .prose_latency import slo_note
        for sname in w.notes["latency_services"]:
            ws[f"docs/slo/{sname}.md"] = slo_note(w.svc(sname), w, flavor(w.company.industry)["waits_on"])
    rng.shuffle(queue)
    queue = texts.order_queue(queue)
    rng.shuffle(survey)
    if survey:
        ws["docs/oncall-survey-2026-q3.md"] = texts.survey_doc(w, survey, rng)
    return {"workspace": ws, "instruction": texts.instruction(w, queue, rng, canary, bool(survey), items)}


def smoke_test(w: World) -> str:
    s = w.services[0]
    doc = {"rule_files": ["../../rules/*.yml"], "evaluation_interval": "1m", "tests": [
        {"name": f"{s.name} targets up, nothing fires", "interval": "1m",
         "input_series": [{"series": f'up{{service="{s.name}",job="{s.name}",instance="10.0.0.1:8080"}}', "values": "1x20"}],
         "alert_rule_test": [{"eval_time": "15m", "alertname": f"{s.camel}Down", "exp_alerts": []}]}]}
    return "# smoke test: CI runs this; it is not meant to cover behaviour\n" + dump(doc)


def grade_map(spec, tests_dir, mapping, grader=None):
    tmp = tempfile.mkdtemp(prefix="af-xws-")
    try:
        write_tree(tmp, mapping)
        g = grader or ExpertGrader(spec, os.path.join(tests_dir, "hidden"))
        return g.grade(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


ZERO = "{" + ",".join(f'"{k}":{"TAMPER" if k == "tamper" else "0.0"}' for k in KEYS) + "}"

TEST_SH = f"""#!/bin/bash
# Runs in a fresh copy of the task image (environment_mode = "separate"); only /workspace/monitoring is carried over.
set -uo pipefail
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt /logs/verifier/details.json \\
      /logs/verifier/grader_error.json /logs/verifier/checksum.log
ZERO='{ZERO}'
cd /tests
if ! sha256sum -c --quiet checksums.sha256 >/logs/verifier/checksum.log 2>&1; then
  echo "${{ZERO/TAMPER/1.0}}" > /logs/verifier/reward.json; cat /logs/verifier/reward.json; exit 0
fi
python3 -I /tests/grade.py --workspace /workspace/monitoring --spec /tests/spec.json \\
        --hidden /tests/hidden --out /logs/verifier/reward.json --details /logs/verifier/details.json \\
  || {{ echo "${{ZERO/TAMPER/0.0}}" > /logs/verifier/reward.json; echo '{{"grader_error":1}}' > /logs/verifier/grader_error.json; }}
cat /logs/verifier/reward.json
"""

GRADE_ENTRY = """#!/usr/bin/env python3
\"\"\"Entry point: the grader package lives next to this file (python3 -I drops the script dir).\"\"\"
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afgrader.xgrade import ExpertGrader  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    for k in ("--workspace", "--spec", "--hidden", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--details")
    a = ap.parse_args()
    with open(a.spec) as fh:
        spec = json.load(fh)
    keys, details = ExpertGrader(spec, a.hidden).grade(a.workspace)
    with open(a.out, "w") as fh:
        json.dump(keys, fh)
    if a.details:
        with open(a.details, "w") as fh:
            json.dump(details, fh, indent=1, default=str)
    print(json.dumps(keys))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

DOCKERFILE = f"""FROM {IMAGES['prom']} AS prom
FROM {IMAGES['am']} AS am
FROM {IMAGES['py']}
COPY --from=prom /bin/promtool /usr/local/bin/promtool
COPY --from=am /bin/amtool /usr/local/bin/amtool
RUN pip install --no-cache-dir pyyaml==6.0.2
COPY workspace/ /workspace/
RUN chmod +x /workspace/monitoring/bin/af-check /workspace/monitoring/bin/range2test
WORKDIR /workspace/monitoring
"""

VERIFIER_DOCKERFILE = f"""FROM {IMAGES['prom']} AS prom
FROM {IMAGES['am']} AS am
FROM {IMAGES['py']}
COPY --from=prom /bin/promtool /usr/local/bin/promtool
COPY --from=am /bin/amtool /usr/local/bin/amtool
RUN pip install --no-cache-dir pyyaml==6.0.2
COPY . /tests/
RUN chmod +x /tests/test.sh /tests/grade.py
WORKDIR /workspace
"""


def write_tests(tests_dir: str, spec: dict, groups: list, exercise: list | None = None) -> None:
    os.makedirs(os.path.join(tests_dir, "hidden"), exist_ok=True)
    os.makedirs(os.path.join(tests_dir, "afgrader"), exist_ok=True)
    for f in GRADER_FILES:
        shutil.copyfile(os.path.join(PKG, "grader", f), os.path.join(tests_dir, "afgrader", f))
    write_tree(tests_dir, {"grade.py": GRADE_ENTRY, "test.sh": TEST_SH, "Dockerfile": VERIFIER_DOCKERFILE,
                           "spec.json": json.dumps(spec, separators=(",", ":"), default=str) + "\n",
                           "hidden/scenarios.json": json.dumps(groups, separators=(",", ":"))})
    if exercise is not None:
        write_tree(tests_dir, {"hidden/exercise.json": json.dumps(exercise, separators=(",", ":"))})
    sums = []
    for dirpath, _, fns in os.walk(tests_dir):
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dirpath, fn), tests_dir)
            if rel == "checksums.sha256" or "__pycache__" in rel:
                continue
            with open(os.path.join(dirpath, fn), "rb") as fh:
                sums.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {rel}")
    with open(os.path.join(tests_dir, "checksums.sha256"), "w") as fh:
        fh.write("\n".join(sorted(sums, key=lambda s: s.split("  ")[1])) + "\n")


# Public tags name what kind of work a task holds, never which defects (those stay in the private manifest).
KIND_TAGS = {"outcome": "alert-logic", "storm": "alert-storms", "route": "routing", "inhibit": "inhibition",
             "migration": "ownership", "relabel": "labels", "annotation": "annotations", "absence": "coverage",
             "new_alert": "slo-alerts", "burn_page": "slo-alerts", "burn_ticket": "slo-alerts",
             "recording": "recording-rules"}


def task_toml(w: World, items, n_checks: int, canary: str) -> str:
    tags = [w.family] + sorted({KIND_TAGS.get(it.kind, "alerting") for it in items})
    est = {"E1": 150, "E2": 150, "E3": 180, "E4": 150, "E5": 150}[w.family]
    return f'''# harbor-canary GUID {canary}
schema_version = "1.4"
artifacts = ["/workspace/monitoring"]

[task]
name = "openenvforge/{w.task_id}"
version = "{GEN_VERSION}"
description = "expert ({w.family}): a week of the observability queue on a large legacy Prometheus/Alertmanager repo, graded by promtool/amtool replays."
keywords = ["alertforge", "3ambench", "prometheus", "alertmanager", "slo", "sre", "rl-environment", "dense-reward"]

[metadata]
difficulty = "expert"
category = "observability-as-code"
tags = [{", ".join(f'"{t}"' for t in tags)}]
family = "{w.family}"
tier = "expert"
n_requirements = {len(items)}
n_checks = {n_checks}
generator = "alertforge {GEN_VERSION}"
expert_time_estimate_min = {est}

[agent]
timeout_sec = 3600.0
network_mode = "no-network"

[verifier]
timeout_sec = 600.0
environment_mode = "separate"

[environment]
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096
storage_mb = 4096
'''


def task_card(w: World, spec: dict, n_checks: int) -> str:
    rows = "\n".join(f"| {r['id']} | {r['kind']} | {r['weight']} | {r['q_pristine']:.3f} |" for r in spec["requirements"])
    return f"""# {w.task_id}

- Tier `expert`, family `{w.family}`, {len(spec['requirements'])} requirements, {n_checks} checks,
  {len(spec['preserve']['rules'])} preserved rules, {len(spec['kept'])} kept alert(s).

| Requirement | Kind | Weight | q pristine |
|---|---|---|---|
{rows}

Graded by `tests/test.sh` (promtool + amtool replays of hidden scenarios).
"""
