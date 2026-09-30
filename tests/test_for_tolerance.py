"""A repair whose `for` the task text leaves open accepts any duration that fits the postmortem's intent;
durations the README fixes stay exact (af-029 P5: `ContainerRestartingOften` planted without `for`)."""

import pytest

from alertforge import bugs, render, solutions
from alertforge.alerts import AlertSpec
from alertforge.world import build_world

from conftest import HAVE_TOOLS, needs_tools

MASTER = 20260928


def _spec(kind: str) -> AlertSpec:
    return AlertSpec("P1", kind, "X", None if kind != "burn_page" else "svc", "http", {}, "", {"target": "0.999"})


def test_accepted_for_only_when_text_leaves_it_open():
    assert bugs.accepted_for(_spec("crashloop"), "B02") == (3, 20)
    assert bugs.accepted_for(_spec("mem_high"), "B02") == (3, 10)
    assert bugs.accepted_for(_spec("error_ratio"), "B02") == (5, 10)
    assert bugs.accepted_for(_spec("burn_page"), "B02") is None  # README: burn page `for: 2m`
    assert bugs.accepted_for(_spec("crashloop"), "B06") is None  # the planted rule keeps its `for`


def test_world_carries_range_for_b02_fleet_repair_only():
    w = build_world("team-reorg-migration", "hard", 1, MASTER)
    p5 = next(r for r in w.reqs if r.id == "P5")
    assert (p5.defect, p5.alert.kind) == ("B02", "crashloop")
    assert p5.alert.for_range == (3, 20) and p5.alert.for_min == 10
    for r in w.reqs:
        if r.alert is not None and r.id != "P5":
            assert r.alert.for_range == (r.alert.for_min, r.alert.for_min)


@pytest.fixture(scope="module")
def reorg_task(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    out = tmp_path_factory.mktemp("tasks")
    return render.build_task("team-reorg-migration", "hard", 1, str(out), "af-test-reorg", MASTER)


def _grade_with_for(m, name: str, for_: str | None):
    files, am, _ = solutions.oracle(m["world"])
    for _, x in solutions._alert_rules(files, name):
        if for_ is None:
            x.pop("for", None)
        else:
            x["for"] = for_
    return render.grade_state(m["world"], m["spec"], m["hidden_dir"], m["static"], files, am)


@needs_tools
@pytest.mark.parametrize("for_", ["5m", "3m", "10m", "20m"])
def test_any_for_that_fits_the_intent_gets_full_credit(reorg_task, for_):
    k, det = _grade_with_for(reorg_task, "ContainerRestartingOften", for_)
    assert det["requirements"]["P5"]["s"] == 1.0, det["requirements"]["P5"]
    assert k["reward"] == 1.0


@needs_tools
@pytest.mark.parametrize("for_", [None, "30s", "2m", "2h"])
def test_too_short_or_too_long_for_is_not_credited(reorg_task, for_):
    """30s/2m fire on a two-minute blip; 2h never fires during the incident."""
    k, det = _grade_with_for(reorg_task, "ContainerRestartingOften", for_)
    assert det["requirements"]["P5"]["s"] < 0.5, det["requirements"]["P5"]
    assert k["outcome"] == 0.0


@needs_tools
def test_readme_duration_stays_exact(reorg_task):
    """`<Svc>Down` is `for: 3m` per README (Service health); another value is still a mistake."""
    a1 = next(r for r in reorg_task["world"].reqs if r.id == "A1")
    assert a1.alert.kind == "target_down"
    _, det = _grade_with_for(reorg_task, a1.alert.name, "10m")
    assert det["requirements"]["A1"]["s"] < 1.0
