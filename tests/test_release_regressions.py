"""Regression coverage for review findings in calibration and deadline grading."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import expert_calibrate as cal
from run_meta import input_mismatch


def provenance(tmp_path):
    task, saved, trajectory = tmp_path / "task", tmp_path / "saved", tmp_path / "trajectory.json"
    base = task / "environment/workspace/monitoring"
    for root in (base, saved):
        (root / "docs").mkdir(parents=True)
        (root / "docs/survey.md").write_text("Customers cannot complete requests.\n")
    (task / "instruction.md").write_text("<!-- harbor-canary abc -->\nFix the queue.\n")
    traj = {"steps": [{"source": "user", "message": "Fix the queue."}]}
    trajectory.write_text(json.dumps(traj))
    return task, saved, trajectory, traj


def test_prompt_canary_omission_is_not_changed_input(tmp_path):
    task, saved, trajectory, _ = provenance(tmp_path)
    assert input_mismatch(str(task), str(saved), str(trajectory)) is None


def test_changed_untouched_evidence_requires_fresh_trial(tmp_path):
    task, saved, trajectory, _ = provenance(tmp_path)
    (saved / "docs/survey.md").write_text("The alert seems useful.\n")
    assert "task evidence differs" in input_mismatch(str(task), str(saved), str(trajectory))


def test_recorded_solver_edit_is_an_outcome_not_changed_input(tmp_path):
    task, saved, trajectory, traj = provenance(tmp_path)
    (saved / "docs/survey.md").write_text("Customers cannot complete requests.\nInvestigated.\n")
    traj["steps"].append({"source": "agent", "tool_calls": [{"function_name": "Edit", "arguments": {
        "file_path": "/workspace/monitoring/docs/survey.md", "old_string": "requests.\n",
        "new_string": "requests.\nInvestigated.\n"}}]})
    trajectory.write_text(json.dumps(traj))
    assert input_mismatch(str(task), str(saved), str(trajectory)) is None
    # An edit anchored on different old content cannot excuse a changed input.
    traj["steps"][-1]["tool_calls"][0]["arguments"]["old_string"] = "not in supplied input"
    trajectory.write_text(json.dumps(traj))
    assert input_mismatch(str(task), str(saved), str(trajectory)) is not None


def trial(root, identity="trial-1"):
    (root / "verifier").mkdir(parents=True)
    (root / "result.json").write_text(json.dumps({"id": identity, "task_name": "task-1",
        "verifier_result": {"rewards": {"reward": 1, "solved": 1}}}))


def test_duplicate_trial_roots_and_copied_ids_do_not_add_attempts(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    trial(a)
    trial(b)
    alias = tmp_path / "alias"
    alias.symlink_to(a, target_is_directory=True)
    rows = cal.trials([str(tmp_path), str(a), str(alias)], {"task-1": {"family": "E1"}}, None)
    assert len(rows) == 1


@pytest.mark.parametrize("has_artifact", [False, True])
def test_requested_regrade_cannot_fall_back_to_historical_success(tmp_path, monkeypatch, has_artifact):
    import replay_expert
    trial(tmp_path / "run")
    if has_artifact:
        (tmp_path / "run/artifacts/workspace/monitoring").mkdir(parents=True)
    monkeypatch.setattr(cal, "input_mismatch", lambda *a: None)
    def broken(*args):
        raise RuntimeError("grader unavailable")
    monkeypatch.setattr(replay_expert, "grade_with", broken)
    rows = cal.trials([str(tmp_path)], {"task-1": {"family": "E1"}}, str(tmp_path / "tasks"), regrade=True)
    assert "requested regrade failed" in rows[0]["invalid"]


@pytest.mark.parametrize("phase", ["build", "gate"])
def test_failed_rebuild_removes_accepted_and_policy_artifacts(tmp_path, monkeypatch, phase):
    from alertforge.expert import build, cli, gate
    tid = "afx-e1-s01"
    for sub in ("tasks", "partial", "null"):
        (tmp_path / sub / tid).mkdir(parents=True)
        (tmp_path / sub / tid / "old").write_text("old accepted content")
    def broken(*args, **kw):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(build, "build_task", broken if phase == "build" else lambda *a, **kw: {})
    monkeypatch.setattr(gate, "variant_sweep", lambda m: [])
    monkeypatch.setattr(gate, "gate_task", broken)
    result = cli._one(("E1", 1, str(tmp_path), 20260930, False))
    assert not result["pass"]
    assert (tmp_path / "rejected" / tid / "old").is_file()
    assert all(not (tmp_path / sub / tid).exists() for sub in ("tasks", "partial", "null"))


def test_early_background_alert_is_never_a_deadline_solution(monkeypatch):
    from alertforge.expert import calib
    from alertforge.expert.xscen import G
    from alertforge.grader.xscore import Ctx, outcome_q
    g = G("T1", "deadline", 10)
    g.fire_by(5, 10, "svc")
    group = g.done()
    p = next(p for p in group["probes"] if p.get("family") == "F_detect")
    results = {group["name"]: {pid: [] for pid in p["any_of"]}}
    results[group["name"]][p["any_of"][0]] = [{"alertname": "UnrelatedPage", "service": "svc", "severity": "page"}]
    req = {"id": "T1", "kind": "outcome", "outcome": {"touch": ["Disk"],
        "services": {"svc": {"class": "page"}}}}
    monkeypatch.setattr(calib, "_snapshots", lambda *a: results)
    calib.background_alerts({"requirements": [req]}, [group], {}, {})
    assert group["o_exempt"] == ["UnrelatedPage"]
    ctx = Ctx({}, {"route": {}}, "", "", results, {}, None)
    assert outcome_q(req, [group], ctx, set())[0] == 0


def test_deadline_inhibition_uses_the_same_minute(monkeypatch):
    from alertforge.expert.xscen import G
    from alertforge.grader import xscore
    g = G("T1", "deadline", 10)
    g.fire_by(5, 10, "svc")
    group = g.done()
    p = next(p for p in group["probes"] if p.get("family") == "F_detect")
    page = {"alertname": "Disk", "service": "svc", "severity": "page"}
    source = {"alertname": "Down", "service": "svc", "severity": "critical"}
    results = {group["name"]: {pid: [] for pid in p["any_of"]}}
    am = {"inhibit_rules": [{"source_matchers": ['alertname="Down"'],
                             "target_matchers": ['alertname="Disk"'], "equal": ["service"]}]}
    ctx = xscore.Ctx({}, am, "", "", results, {}, None)
    monkeypatch.setattr(xscore, "_route_ok", lambda *a: 1)
    monkeypatch.setattr(xscore, "_labels_ok", lambda *a: 1)
    req = {"outcome": {"exclude": ["Down"], "services": {"svc": {"class": "page"}}}}
    results[group["name"]][p["any_of"][0]] = [page]
    results[group["name"]][p["any_of"][-1]] = [source]
    assert xscore.outcome_q(req, [group], ctx, set())[0] == 1
    results[group["name"]][p["any_of"][0]] = [page, source]
    assert xscore.outcome_q(req, [group], ctx, set())[0] == 0


def test_calibration_stops_when_promtool_returns_no_result(monkeypatch):
    """A group without a result is a promtool failure, not "nothing fired": calibrating on it silently dropped the
    background exemptions of a parallel build (afx-e1-s05, T1). The build stops instead."""
    from alertforge.expert import calib
    from alertforge.grader import snap
    monkeypatch.setattr(snap, "run_groups", lambda groups, *a, **kw: ({g["name"]: None for g in groups}, {}))
    with pytest.raises(RuntimeError, match="promtool returned no result"):
        calib._snapshots([{"name": "T1.normal", "probes": [], "input_series": []}], {}, "bg")


def test_redaction_masks_emails_and_temp_paths_but_keeps_synthetic_addresses():
    """runs.json carried a harness git author e-mail and macOS temp paths from promtool errors; the tasks' own
    Alertmanager configs carry synthetic addresses at reserved `.example` names, which must pass `--check`.
    (The real-looking values are assembled here so that this file itself passes the check.)"""
    from redact import findings, redact
    real, tmp = "someone" + "@" + "vendor.com", "/var" + "/folders/__/v07ys0xn66lbj9bx3hvjr2580000gr/T"
    ptmp, pvar = "/private" + "/tmp", "/private/var" + "/folders/ab/cd/T"
    text = (f'git config user.email "{real}"; smtp_from: alertmanager@int.kdm-handel.example '
            f'to: oncall@halde.example {tmp}/af-episode-x/monitoring/rules/a.yml {pvar}/tmp1 {ptmp}/af-xgrade-1/rules '
            'prom/prometheus:v3.5.0@sha256:63805ebb')
    out = redact(text)
    assert real not in out and "user@example.com" in out
    assert "oncall@halde.example" in out and "alertmanager@int.kdm-handel.example" in out
    assert tmp not in out and pvar not in out and ptmp not in out and "/tmp/af-episode-x/monitoring/rules/a.yml" in out
    assert "v3.5.0@sha256:63805ebb" in out
    assert sorted(findings(text)) == sorted([real, tmp, pvar, ptmp])
    assert findings(out) == []
