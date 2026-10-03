"""Episode / OpenEnv layer: parity with Harbor (M11), loop neutrality (M8), safety (H9, H12)."""

import os
import shutil
import sys

import pytest

from alertforge.episode import Episode

from conftest import needs_tools

OPENENV_DIR = os.path.join(os.path.dirname(__file__), "..", "openenv")


def task_dir_of(m):
    return os.path.dirname(os.path.dirname(m["hidden_dir"]))


def oracle_writes(m):
    """(path, content) writes that turn the pristine repo into the oracle repo."""
    return [(rel, text) for rel, text in sorted(m["oracle_map"].items()) if m["pristine_map"].get(rel) != text]


@needs_tools
def test_parity_sum_equals_harbor_reward(easy_task):
    ep = Episode(task_dir_of(easy_task), split="train", mode="parity", tier="easy")
    total = 0.0
    for rel, text in oracle_writes(easy_task):
        total += ep.step({"tool": "write_file", "path": rel, "content": text})["reward"]
    res = ep.step({"tool": "submit"})
    total += res["reward"]
    assert res["done"] and res["metadata"]["harbor_reward"] == 1.0
    assert abs(total - res["metadata"]["harbor_reward"]) <= 1e-6
    ep.close()


@needs_tools
def test_write_then_revert_is_neutral(easy_task):
    ep = Episode(task_dir_of(easy_task), split="train", mode="shaped", tier="easy")
    rel, text = oracle_writes(easy_task)[0]
    before = easy_task["pristine_map"].get(rel, "")
    r1 = ep.step({"tool": "write_file", "path": rel, "content": text})
    r2 = ep.step({"tool": "write_file", "path": rel, "content": before})
    shaping = (r1["reward"] + 0.002) + (r2["reward"] + 0.002)  # remove the fixed step cost
    assert abs(shaping) <= 1e-9
    assert r1["metadata"]["delta_phi"] == -r2["metadata"]["delta_phi"]
    ep.close()


@needs_tools
def test_invalid_actions_never_raise(easy_task):
    ep = Episode(task_dir_of(easy_task), split="train", tier="easy")
    for a in ({"tool": "read_file", "path": "../../etc/passwd"}, {"tool": "read_file", "path": "/etc/passwd"},
              {"tool": "replace_in_file", "path": "README.md", "old": "zzz-not-there", "new": "x"},
              {"tool": "nope"}, {"tool": "route_test", "labels": {}}, {"tool": "write_file", "path": "x.yml"}):
        res = ep.step(a)
        assert res["ok"] is False and res["metadata"]["invalid"] == 1
    assert ep.step({"tool": "route_test", "labels": {"severity": "page", "team": "x"}})["ok"]
    ep.close()


@needs_tools
def test_public_and_heldout_hide_phi(easy_task):
    ep = Episode(task_dir_of(easy_task), split="public", mode="parity", tier="easy")
    assert ep.mode == "outcome"
    rel, text = oracle_writes(easy_task)[0]
    res = ep.step({"tool": "write_file", "path": rel, "content": text})
    assert res["reward"] == 0.0 and res["metadata"]["phi"] is None and "delta_phi" not in res["metadata"]
    ep.close()


@needs_tools
def test_openenv_environment_in_process(easy_task, tmp_path, monkeypatch):
    # The repo's openenv/ directory is also an importable namespace when the
    # optional SDK is absent. Check the SDK, not that empty namespace.
    pytest.importorskip("openenv.core.env_server")
    ds = tmp_path / "ds" / "tasks"
    ds.mkdir(parents=True)
    shutil.copytree(task_dir_of(easy_task), ds / "af-001-slo-onboarding-easy-s1")
    monkeypatch.setenv("AF_DATASET_DIR", str(tmp_path / "ds"))
    sys.path.insert(0, os.path.abspath(OPENENV_DIR))
    import importlib
    import alertforge_env.server.alertforge_environment as mod
    importlib.reload(mod)
    mod.DATASET_DIR, mod._PUBLIC_CACHE = str(tmp_path / "ds"), None
    from alertforge_env.models import AlertForgeAction
    env = mod.AlertForgeEnvironment()
    obs = env.reset(seed=0, split="public", index=0)
    assert obs.task_id == "af-001-slo-onboarding-easy-s1" and "Change requests" in obs.instruction
    obs = env.step(AlertForgeAction(tool="read_file", path="README.md"))
    assert obs.ok and "Routing policy" in obs.output and obs.metadata["phi"] is None
    assert "spec" not in env.state.model_dump_json()
    obs = env.reset(seed=3, split="train", workflow="team-reorg-migration", tier="easy")
    assert obs.task_id.startswith("af-train-team-reorg-migration-easy")
    obs = env.step(AlertForgeAction(tool="submit"))
    assert obs.done and obs.reward == 0.0
    env.close()
