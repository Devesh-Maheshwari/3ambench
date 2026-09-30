"""End-to-end grading of built tasks with the real promtool/amtool (§12.1 behaviors)."""

import copy
import hashlib
import json
import os
import random
import subprocess
import sys

from alertforge import render, solutions
from alertforge.grader.grade import KEYS, Grader

from conftest import needs_tools


def grade(m, state, grader=None):
    files, am, extra = state
    return render.grade_state(m["world"], m["spec"], m["hidden_dir"], m["static"], files, am, grader, extra)


def req_of(m, kind):
    return next(r for r in m["world"].reqs if r.kind == kind)


@needs_tools
def test_oracle_null_partial(easy_task):
    ko, _ = grade(easy_task, solutions.oracle(easy_task["world"]))
    kn, _ = grade(easy_task, solutions.null(easy_task["world"]))
    kp, _ = grade(easy_task, solutions.partial(easy_task["world"]))
    assert set(ko) == set(KEYS)
    assert ko["reward"] == 1.0 and ko["solved"] == 1.0 and ko["preservation"] == 1.0
    assert kn["reward"] == 0.0 and kn["progress"] == 0.0 and kn["preservation"] == 1.0
    assert kp["outcome"] == 0.0 and 0.05 < kp["reward"] < 0.4


@needs_tools
def test_hard_task_oracle_is_one(hard_task):
    ko, det = grade(hard_task, solutions.oracle(hard_task["world"]))
    assert ko["reward"] == 1.0, det["requirements"]


@needs_tools
def test_duplicate_definition_costs_twenty_percent_not_everything(easy_task):
    """S4: a duplicate (fixed copy next to the broken one) keeps most of the credit."""
    w = easy_task["world"]
    files, am, _ = solutions.oracle(w)
    a = req_of(easy_task, "new_alert").alert
    rule = copy.deepcopy(next(x for _, x in solutions._alert_rules(files, a.name)))
    files["scratch"] = [{"name": "scratch", "rules": [rule]}]
    k, det = grade(easy_task, (files, am, {}))
    q = det["requirements"][a.req_id]["q"]
    assert 0.75 <= q < 1.0 and k["outcome"] == 0.0


@needs_tools
def test_missing_continue_gets_jaccard_credit(easy_task):
    """S6: a route that reaches PagerDuty but not Slack earns partial credit, not zero."""
    w = easy_task["world"]
    files, am, _ = solutions.oracle(w)
    team = req_of(easy_task, "route").route["team"]
    for rt in am["route"]["routes"]:
        if rt.get("matchers") == [f'team="{team}"']:
            rt["routes"][0].pop("continue")
    _, det = grade(easy_task, (files, am, {}))
    s = det["requirements"][req_of(easy_task, "route").id]["s"]
    assert 0.2 < s < 1.0


@needs_tools
def test_stray_files_are_ignored_and_yaml_error_costs_at_most_half(easy_task):
    """S5: scratch files never zero the reward; one unparsable file keeps >= half of progress."""
    w = easy_task["world"]
    files, am, _ = solutions.oracle(w)
    k, _ = grade(easy_task, (files, am, {"notes/todo.md": "x", "scratch.yml": "groups: [["}))
    assert k["reward"] == 1.0
    stem = "fleet"
    k2, det = grade(easy_task, (files, am, {f"rules/{stem}.yml": solutions.break_indent}))
    assert k2["syntax_ok"] == 0.0 and k2["outcome"] == 0.0 and k2["tamper"] == 0.0
    assert k2["progress"] > 0.4


@needs_tools
def test_label_error_costs_label_family_only(easy_task):
    """S3b: a wrong severity keeps Fire/Silent credit (probes select only alertname/alertstate/service)."""
    w = easy_task["world"]
    files, am, _ = solutions.oracle(w)
    a = req_of(easy_task, "new_alert").alert
    for _, x in solutions._alert_rules(files, a.name):
        x["labels"]["severity"] = "warning"
    _, det = grade(easy_task, (files, am, {}))
    fams = det["requirements"][a.req_id]["families"]["integrated"]
    assert fams["fire"] == 1.0 and fams["silent"] == 1.0 and fams["label_annot"] < 1.0
    assert 0.7 <= det["requirements"][a.req_id]["q"] < 1.0


@needs_tools
def test_decoupled_pass_gives_half_credit_when_sli_is_broken(easy_task):
    """S3a: a correct burn alert on top of broken SLI recording rules keeps about half its credit."""
    w = easy_task["world"]
    files, am, _ = solutions.oracle(w)
    for g in files["slo-recording"]:
        for x in g["rules"]:
            x["expr"] = x["expr"].replace("sum by (service) ", "sum ")
    _, det = grade(easy_task, (files, am, {}))
    a = req_of(easy_task, "new_alert").alert
    assert det["requirements"][a.req_id]["families"]["decoupled"]["fire"] == 1.0
    assert 0.4 <= det["requirements"][a.req_id]["q"] <= 0.6


@needs_tools
def test_tamper_adversaries_zero_everything(easy_task):
    w = easy_task["world"]
    for name in ("A-alerts-inject", "A-shadow-series", "A-group-interval"):
        k, _ = grade(easy_task, solutions.adversary(w, name))
        assert k["tamper"] == 1.0 and k["reward"] == 0.0, name


@needs_tools
def test_always_fire_and_rename_score_zero_on_alert_requirements(easy_task):
    w = easy_task["world"]
    for name in ("A-fire", "A-silent"):
        k, _ = grade(easy_task, solutions.adversary(w, name))
        assert k["req_alerts"] <= 0.05 and k["req_repairs"] <= 0.05 and k["outcome"] == 0.0, name


@needs_tools
def test_receiver_null_and_over_inhibit_block_outcome(easy_task):
    w = easy_task["world"]
    k, _ = grade(easy_task, solutions.adversary(w, "A-receiver-null"))
    assert k["outcome"] == 0.0 and k["preservation"] < 1.0
    k, _ = grade(easy_task, solutions.adversary(w, "A-inhibit-all"))
    assert k["req_inhibit"] <= 0.05


@needs_tools
def test_pmut_is_interior(easy_task):
    rng = random.Random(3)
    g = Grader(easy_task["spec"], easy_task["hidden_dir"])
    xs = [grade(easy_task, solutions.pmut(easy_task["world"], rng)[0], g)[0]["reward"] for _ in range(6)]
    assert all(0.0 <= x <= 1.0 for x in xs) and max(xs) > 0.05


@needs_tools
def test_grader_cli_writes_all_keys(easy_task, tmp_path):
    """tests/grade.py (the copied grader) produces the same keys as the library."""
    task_dir = os.path.dirname(os.path.dirname(easy_task["hidden_dir"]))
    ws = os.path.join(task_dir, "environment", "workspace", "monitoring")
    out = tmp_path / "reward.json"
    subprocess.run([sys.executable, "-I", os.path.join(task_dir, "tests", "grade.py"), "--workspace", ws,
                    "--spec", os.path.join(task_dir, "tests", "spec.json"), "--hidden",
                    os.path.join(task_dir, "tests", "hidden"), "--out", str(out)], check=True,
                   env={**os.environ}, capture_output=True)
    keys = json.loads(out.read_text())
    assert set(keys) == set(KEYS) and keys["reward"] == 0.0


def _tree_hash(root):
    h = hashlib.sha256()
    for dp, _, fns in sorted(os.walk(root)):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


@needs_tools
def test_build_is_deterministic(tmp_path):
    a = render.build_task("alert-storm-cleanup", "medium", 2, str(tmp_path / "a"), "t", 7)
    b = render.build_task("alert-storm-cleanup", "medium", 2, str(tmp_path / "b"), "t", 7)
    assert _tree_hash(os.path.join(tmp_path, "a", "t")) == _tree_hash(os.path.join(tmp_path, "b", "t"))
    assert a["n_checks"] == b["n_checks"]


@needs_tools
def test_checksums_and_solution_scripts(easy_task):
    task_dir = os.path.dirname(os.path.dirname(easy_task["hidden_dir"]))
    r = subprocess.run(["shasum", "-a", "256", "-c", "checksums.sha256"], cwd=os.path.join(task_dir, "tests"),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    solve = open(os.path.join(task_dir, "solution", "solve.sh")).read()
    assert solve.startswith("#!/bin/bash") and "AF_EOF" in solve


@needs_tools
def test_grading_is_deterministic_across_fresh_graders(hard_task):
    """M10: promtool group order is pinned (group_eval_order), so repeated grades are bit-identical."""
    st = solutions.partial(hard_task["world"])
    vals = {grade(hard_task, st)[0]["reward"] for _ in range(3)}
    assert len(vals) == 1
