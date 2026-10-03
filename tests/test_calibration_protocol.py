"""Difficulty decisions require the declared balanced pilot, never infrastructure failures."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("expert_calibrate", SCRIPTS / "expert_calibrate.py")
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)


def manifest():
    return {f"task-{n}": {"task_id": f"task-{n}", "family": "E1", "seed": n,
                          "items": [f"T1:H0{n}", f"Q1:R0{n}"]} for n in range(1, 4)}


def row(tid, who="a/model", solved=True, **extra):
    return {"task_id": tid, "family": "E1", "who": who, "solved": solved, "reward": float(solved),
            "failing": [], "replayed": False, **extra}


def balanced():
    return [row(t, m) for t in manifest() for m in ("a/model", "b/model") for _ in range(3)]


def test_one_success_is_not_a_drop_or_a_family_decision():
    out = cal.decide([row("task-1")], manifest())
    assert out["tasks"]["task-1"]["verdict"] == "incomplete"
    assert out["families"]["E1"]["decision"] == "collect"
    assert out["families"]["E1"]["too_easy"] is None
    assert out["public"]["E1"] == []


def test_complete_pilot_can_trigger_hardening():
    out = cal.decide(balanced(), manifest())
    assert out["families"]["E1"]["decision"] == "harden"
    assert all(t["verdict"] == "drop" for t in out["tasks"].values())


def test_family_totals_cannot_hide_an_unbalanced_task_cell():
    rows = balanced()
    rows[0]["task_id"] = "task-2"
    out = cal.decide(rows, manifest())
    assert out["families"]["E1"]["decision"] == "collect"
    assert out["tasks"]["task-1"]["by_model"]["a/model"] == 2


def test_infrastructure_failure_is_excluded_and_requires_replacement():
    rows = balanced()
    rows[0].update(solved=False, invalid="provider rate limit")
    out = cal.decide(rows, manifest())
    assert out["valid_trials"] == 17
    assert len(out["excluded"]) == 1
    assert out["families"]["E1"]["decision"] == "collect"


def test_explicit_models_exclude_spread_runs():
    out = cal.decide(balanced() + [row("task-1", "small/model", False)], manifest(),
                     models=["a/model", "b/model"])
    assert out["families"]["E1"]["decision"] == "harden"
    assert out["valid_trials"] == 18


def test_one_zero_is_incomplete_and_six_zeros_require_audit():
    rows = balanced()
    for r in rows:
        if r["task_id"] == "task-1":
            r.update(solved=False, failing=[("T1", True)])
    out = cal.decide(rows, manifest())
    assert out["tasks"]["task-1"]["verdict"] == "audit"
    assert out["tasks"]["task-1"]["top_failure"] == ("T1", 6)
    assert "task-1" not in out["public"]["E1"]
    assert cal.decide([rows[0]], manifest())["tasks"]["task-1"]["verdict"] == "incomplete"


@pytest.mark.parametrize("reward", [{}, {"reward": None, "solved": None}, {"reward": float("nan"), "solved": 0}])
def test_missing_or_malformed_reward_is_invalid(tmp_path, reward):
    trial = tmp_path / "trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"task_name": "task-1", "verifier_result": {"rewards": reward}}))
    rows = cal.trials([str(tmp_path)], manifest(), None)
    assert rows[0]["invalid"] == "missing or invalid verifier reward"


def test_ci_exposes_small_sample_uncertainty():
    lo, hi = cal.wilson(1, 1)
    assert lo < 0.21 and hi == 1
    assert cal.wilson(0, 0) is None


def test_pooled_half_success_cannot_hide_one_model_saturating():
    rows = balanced()
    for r in rows:
        r["solved"] = r["who"] == "a/model"
    out = cal.decide(rows, manifest())
    assert out["families"]["E1"]["decision"] == "audit"
    assert out["families"]["E1"]["in_band"] is False
    assert out["public"]["E1"] == []
