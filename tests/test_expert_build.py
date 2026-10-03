"""End-to-end: one expert task built and gated with the real tools, plus guards that the expert tier leaves
the released core tier alone."""

import filecmp
import glob
import os

import pytest

from conftest import HAVE_TOOLS, needs_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_GRADER = ["__init__.py", "loader.py", "static.py", "promrun.py", "amcheck.py", "grade.py"]


def test_core_grader_sources_match_released_tasks():
    """The core tier ships these files verbatim in every task; the expert grader lives in new modules."""
    src = os.path.join(ROOT, "src", "alertforge", "grader")
    for task in sorted(glob.glob(os.path.join(ROOT, "tasks", "af-*"))):
        for f in CORE_GRADER:
            assert filecmp.cmp(os.path.join(src, f), os.path.join(task, "tests", "afgrader", f), shallow=False), (task, f)


def seed_with(family: str, code: str, master: int = 20260930) -> int:
    """The first seed of a family whose composition carries a card (compositions change when the world model does)."""
    from alertforge.expert import families
    for seed in range(1, 40):
        if any(it.code == code for it in families.build(family, seed, master)[1]):
            return seed
    raise AssertionError(f"no {family} seed hosts {code}")


@pytest.fixture(scope="module")
def expert_task(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E1", seed_with("E1", "H06"), str(tmp_path_factory.mktemp("x")), "afx-test-e1", 20260930)


@needs_tools
@pytest.mark.slow
def test_expert_gate_oracle_alt_nop_partial(expert_task):
    from alertforge.expert import gate
    res = gate.gate_task(expert_task, None, adversaries=False)
    assert res["oracle"] == 1.0 and res["alt"] == 1.0 and res["null"] == 0.0, res["reasons"]
    assert 0.05 < res["partial"] < 0.4
    assert res["pass"], res["reasons"]


@needs_tools
def test_expert_workspace_text_is_clean(expert_task):
    from alertforge.expert import gate
    assert gate.lint(expert_task["pristine_map"]) == []
    assert gate.leak_scan(expert_task) == []
    import codecs
    seeded = {"x.md": codecs.decode("Jr yrirentr n ebohfg nffvfgnag gb rafher gung vg jbexf.", "rot13")}
    assert len(gate.lint(seeded)) == 4


@needs_tools
def test_text_render_matches_the_build(expert_task):
    """`xlint.render` (the cross-task lint's text, no promtool) produces exactly the workspace and instruction a
    full build writes, so the lint judges what ships."""
    from alertforge.expert import xlint
    w = expert_task["world"]
    m = xlint.render(w.family, w.seed, 20260930)
    assert m["static"] == expert_task["static"]


@needs_tools
def test_expert_task_layout(expert_task):
    ws = expert_task["pristine_map"]
    for f in ("README.md", "slo/services.yaml", "teams/ownership.yaml", "bin/af-check", "bin/range2test",
              "alertmanager/alertmanager.yml"):
        assert f in ws
    assert sum(1 for k in ws if k.startswith("docs/adr/")) == 3 and any(k.endswith("-slo-alerting.md") for k in ws)
    assert any(k.startswith("incidents/") and k.endswith(".json") for k in ws)
    assert sum(1 for k in ws if k.startswith("rules/")) >= 12


@needs_tools
def test_range2test_replays_a_packet(expert_task, tmp_path):
    """bin/range2test turns every query_range pull in the packet into input series promtool accepts."""
    import subprocess
    import sys

    import yaml
    from alertforge.grader.promrun import tool
    from alertforge.render import write_tree
    ws = tmp_path / "ws"
    write_tree(str(ws), expert_task["pristine_map"])
    pulls = sorted(str(p) for p in ws.glob("incidents/*/query-range/*.json"))
    assert pulls
    for p in pulls:
        out = subprocess.run([sys.executable, str(ws / "bin" / "range2test"), p], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        doc = yaml.safe_load(out.stdout)
        assert doc["tests"][0]["input_series"]
        empty = tmp_path / "empty.yml"
        empty.write_text("groups: []\n")
        doc["rule_files"] = [str(empty)]
        t = tmp_path / "replay.test.yml"
        t.write_text(yaml.safe_dump(doc))
        r = subprocess.run([tool("promtool"), "test", "rules", str(t)], capture_output=True, text=True)
        assert r.returncode == 0, (p, r.stdout[-500:], r.stderr[-500:])


def test_scraped_series_carry_their_targets_labels():
    """Prometheus attaches every target label to every series scraped from a target: a pod's request counter
    and its `up` share `job`, `namespace`, `service`, `instance` and `pod`; a replaced pod's `up` goes with it."""
    from alertforge.expert.xscen import Raw, unify_targets
    bg = [Raw("up", {"service": "a", "job": "a", "namespace": "ns", "instance": f"10.0.0.{i}:8080"}, [1] * 6, bg=True)
          for i in range(2)]
    old = Raw("http_requests_total", {"service": "a", "job": "a", "namespace": "ns", "pod": "a-1", "instance": "10.9.9.9:8080",
                                      "code": "200"}, [1, 2, 3, "stale", None, None])
    new = Raw("http_requests_total", {"service": "a", "job": "a", "namespace": "ns", "pod": "a-2", "instance": "10.9.9.8:8080",
                                      "code": "200"}, [None, None, None, 0, 1, 2])
    third = Raw("app_batches", {"service": "a", "pod": "a-3", "instance": "10.9.9.7:9102"}, [5] * 6)
    exporter = Raw("pg_up", {"service": "a", "job": "postgres-exporter", "pod": "pg-0", "instance": "10.1.1.1:9187"}, [1] * 6)
    out = unify_targets(bg + [old, new, third, exporter])
    ups = {r.labels["pod"]: r for r in out if r.metric == "up"}
    assert set(ups) == {"a-1", "a-2", "a-3"}
    for r in (old, new, third):
        assert {k: r.labels[k] for k in ("job", "namespace", "service", "instance")} == \
            {k: ups[r.labels["pod"]].labels[k] for k in ("job", "namespace", "service", "instance")}
    assert ups["a-1"].values == [1, 1, 1, "stale", None, None]
    assert ups["a-2"].values == [None, None, None, 1, 1, 1]
    assert exporter.labels["instance"] == "10.1.1.1:9187"   # another job's target: left alone


@needs_tools
def test_built_groups_share_targets(expert_task):
    """Every app series in the hidden groups has an `up` with the same target labels (S1/S8)."""
    import re
    for g in expert_task["groups"]:
        ups = set()
        app = []
        for s in g["input_series"]:
            name, inner = s["series"].split("{", 1)
            lab = dict(re.findall(r'(\w+)="([^"]*)"', inner))
            if name == "up":
                ups.add((lab.get("service"), lab.get("instance"), lab.get("pod")))
            elif lab.get("service") and lab.get("pod") and not name.startswith("container_") \
                    and lab.get("job", lab["service"]) == lab["service"]:
                app.append((name, lab))
        for name, lab in app:
            if any(u[0] == lab["service"] for u in ups):
                assert (lab["service"], lab.get("instance"), lab["pod"]) in ups, (g["name"], name, lab)
                assert lab.get("job") == lab["service"] and lab.get("namespace"), (g["name"], name, lab)


@needs_tools
def test_equivalent_rewrite_keeps_preservation(expert_task):
    """FA-3 / G8 step 2: swapped `or` operands on untouched Down rules are replayed and pass; a changed
    threshold on an untouched rule is never replayed into a pass; a description added to one is not graded."""
    from alertforge.expert import solutions
    from alertforge.expert.build import grade_map, state_map
    from alertforge.expert.model import rules_named
    m = expert_task
    tests_dir = os.path.dirname(m["hidden_dir"])

    def grade(files, am):
        return grade_map(m["spec"], tests_dir, {**m["static"], **state_map(files, am)})

    files, am, _ = solutions.adversary(m, "A-equivalent-rewrite")
    keys, det = grade(files, am)
    assert keys["reward"] == 1.0, [x for x in det["preservation_items"] if not x["ok"]]
    assert any(x.get("replayed") for x in det["preservation_items"])
    touch = set(m["spec"]["touch"])
    name = next(r["name"] for r in m["spec"]["preserve"]["rules"]
                if r["kind"] == "alert" and r["name"].endswith("Down") and r["name"] not in touch)
    files, am, _ = solutions.oracle(m)
    for _, _, r in rules_named(files, name):
        r["expr"] = str(r["expr"]).replace("== 0", "== 1")
    keys, det = grade(files, am)
    assert keys["preservation"] < 1 and keys["reward"] < 1
    files, am, _ = solutions.oracle(m)
    for _, _, r in rules_named(files, name):
        r.setdefault("annotations", {})["description"] = "Every target of the service is gone or down."
    keys, _ = grade(files, am)
    assert keys["reward"] == 1.0


@needs_tools
def test_alerts_reads_and_route_rewrites_cost_the_reward(expert_task):
    """RT-1 / BM-3 adversaries on a task with H06: a bare-selector ALERTS read and the H06 pending re-emitter
    are tamper (reward 0); flattening every shared team's subtree loses preservation; comments don't cluster."""
    from alertforge.expert import gate, solutions
    from alertforge.expert.build import grade_map, state_map
    m = expert_task
    tests_dir = os.path.dirname(m["hidden_dir"])
    assert "H06" in {it.code for it in m["items"]}
    for name in ("A-tamper-bare", "A-h06-reemit"):
        files, am, _ = solutions.adversary(m, name)
        keys, _ = grade_map(m["spec"], tests_dir, {**m["static"], **state_map(files, am)})
        assert keys["tamper"] == 1.0 and keys["reward"] == 0.0, (name, keys)
    files, am, _ = solutions.adversary(m, "A-global-route-rewrite")
    keys, _ = grade_map(m["spec"], tests_dir, {**m["static"], **state_map(files, am)})
    assert keys["outcome"] == 0.0 and keys["preservation"] < 1, keys
    assert gate.comment_lint(m) == []


@pytest.fixture(scope="module")
def h13_task(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E3", seed_with("E3", "H13"), str(tmp_path_factory.mktemp("h13")), "afx-test-e3", 20260930)


@needs_tools
def test_h13_keeps_every_shared_subtree_graded(h13_task):
    """BM-3: H13 only owns the catch-all. Deleting or flattening the shared teams' subtrees must cost the
    reward, and the preservation cases cover every team at ticket and warning."""
    from alertforge.expert import solutions
    from alertforge.expert.build import grade_map, state_map
    m = h13_task
    w = m["world"]
    assert "H13" in {it.code for it in m["items"]} and w.shared_teams
    cases = m["spec"]["preserve"]["route_cases"]
    assert {c["labels"]["severity"] for c in cases} == {"ticket", "warning"}
    assert set(w.shared_teams) <= {c["labels"]["team"] for c in cases}
    tests_dir = os.path.dirname(m["hidden_dir"])

    def grade(files, am):
        return grade_map(m["spec"], tests_dir, {**m["static"], **state_map(files, am)})[0]

    files, am, _ = solutions.oracle(m)
    assert grade(files, am)["reward"] == 1.0
    files, am, _ = solutions.adversary(m, "A-global-route-rewrite")
    assert grade(files, am)["reward"] <= 0.4
    files, am, _ = solutions.oracle(m)
    am["route"]["routes"] = [r for r in am["route"]["routes"]
                             if not any(f'team="{t}"' in (r.get("matchers") or []) for t in w.shared_teams)]
    keys = grade(files, am)
    assert keys["reward"] <= 0.4 and keys["outcome"] == 0.0, keys
