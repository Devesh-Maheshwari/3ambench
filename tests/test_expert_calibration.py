"""Calibration of the expert tier against what a benchmark run needs: storms graded on every service the outage
reaches (S4), the variant sweep and its scenario redraws (RT-5), diagnosis weighted above routine work and graded
with partial credit (BM-4), distinct tasks within a family (BM-5), decoys that cost preservation and the
difficulty knobs (BM-1), cheats graded from the pristine repo too (RT-8)."""

import csv
import io
import os
import random
import re

import pytest

from conftest import HAVE_TOOLS, needs_tools

MASTER = 20260930


def _grade(m, files, am):
    from alertforge.expert.build import grade_map, state_map
    return grade_map(m["spec"], os.path.dirname(m["hidden_dir"]), {**m["static"], **state_map(files, am)})


@pytest.fixture(scope="module")
def h20_task(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E2", 1, str(tmp_path_factory.mktemp("h20")), "afx-test-e2", MASTER)


# ---------------------------------------------------------------------------- S4

@needs_tools
def test_h20_holds_back_every_service_in_the_cluster(h20_task):
    """`muted` is read off the pristine snapshots: every service paging in the unreachable cluster, not the few a
    packet lists. The PagerDuty export misses the service that moved in since, so a matcher copied from it fails."""
    from alertforge.expert import solutions
    m = h20_task
    it = next(i for i in m["items"] if i.code == "H20")
    main = next(g for g in m["groups"] if g["name"].endswith(".cluster_down"))
    others = {s.name for s in m["world"].services} - {it.p["other"]}
    assert set(main["storm"]["muted"]) == others and len(others) >= 7
    assert it.p["other"] in main["storm"]["failing"] and it.p["late"] in main["storm"]["muted"]
    pd = next(v for k, v in m["tickets"][it.rid].files.items() if k.endswith("pagerduty-incidents.csv"))
    # titles follow each team's grouping (a team grouping on every label lists them all, without parentheses)
    titles = [row["title"] for row in csv.DictReader(io.StringIO(pd))]
    named = lambda s: any(re.search(rf"(^|\s){re.escape(s)}(\s|$)", t) for t in titles)  # noqa: E731
    assert all(named(s) for s in it.p["packet"]) and not named(it.p["late"])
    keys, det = _grade(m, *solutions.oracle(m)[:2])
    assert keys["reward"] == 1.0, det["requirements"][it.rid]
    for base in ("oracle", "pristine"):
        files, am, _ = solutions.adversary(m, "A-packet-names", base)
        keys, det = _grade(m, files, am)
        assert keys["outcome"] == 0.0 and det["requirements"][it.rid]["q"] <= 0.5, (base, det["requirements"][it.rid])


@needs_tools
def test_storm_counts_a_page_to_any_rota_for_a_held_back_service(h20_task):
    """A held-back service pages nobody: its page reaching another team's pager (a stale `team` label that the
    inhibition happens to skip) fails as much as a page to its owner."""
    from alertforge.expert import solutions
    from alertforge.expert.model import rules_named
    m = h20_task
    w = m["world"]
    it = next(i for i in m["items"] if i.code == "H20")
    svc = w.svc(next(s for s in sorted(it.p["packet"]) if s != it.p["other"]))
    stale = next(t for t in w.shared_teams if t != "platform")
    files, am, _ = solutions.oracle(m)
    for _, _, r in rules_named(files, f"{svc.camel}Down"):
        r["labels"]["team"] = stale
    for r in am["inhibit_rules"]:
        if 'alertname="ClusterUnreachable"' in (r.get("source_matchers") or []):
            r["target_matchers"] = ['service=~".+"', f'team!="{stale}"']
    keys, det = _grade(m, files, am)
    assert det["requirements"][it.rid]["families"]["owner_paged"] < 1.0, det["requirements"][it.rid]


# ---------------------------------------------------------------------------- RT-5

def test_h08_ramps_sit_clear_of_the_threshold():
    """No sawtooth value is within the series' noise of 0.9 (a 0.90 step read 0.9018 on some cycles)."""
    from alertforge.expert.items_cap import DWELL, LOW, NOISE, RAMPS
    for v in [x for r in RAMPS for x in r] + LOW + DWELL:
        assert abs(v * (1 + NOISE) - 0.9) > 1e-6 and (v * (1 - NOISE) > 0.9) == (v * (1 + NOISE) > 0.9), v


@needs_tools
def test_variant_sweep_flags_a_wrong_variant_that_scores(h20_task):
    """The gate's sweep grades every declared-wrong variant: one that in fact passes (here an accepted fix declared
    wrong) is reported; the real bank is clean."""
    from alertforge.expert import gate
    m = h20_task
    it = next(i for i in m["items"] if i.code == "H20")
    assert gate.variant_sweep(m, {"H20"}) == []
    orig = it.variants
    it.variants = lambda w: [(n, fn, 0.0 if n == "target_down_alerts" else e) for n, fn, e in orig(w)]
    try:
        bad = gate.variant_sweep(m, {"H20"})
    finally:
        it.variants = orig
    assert len(bad) == 1 and "target_down_alerts" in bad[0]


@needs_tools
def test_resample_redraws_only_the_hidden_scenarios(tmp_path):
    from alertforge.expert import build
    a = build.build_task("E5", 5, str(tmp_path / "a"), "afx-t", MASTER)
    b = build.build_task("E5", 5, str(tmp_path / "b"), "afx-t", MASTER, resample=1)
    assert a["pristine_map"] == b["pristine_map"] and a["oracle_map"] == b["oracle_map"]
    assert [g["input_series"] for g in a["groups"]] != [g["input_series"] for g in b["groups"]]


# ---------------------------------------------------------------------------- BM-4

@needs_tools
def test_tickets_hold_most_of_the_weight_and_are_reported_apart(h20_task):
    from alertforge.expert.build import materialize
    from alertforge.expert.calib import TICKET_SHARE
    m = h20_task
    reqs = m["spec"]["requirements"]
    tw = sum(r["weight"] for r in reqs if r["ticket"])
    assert tw >= TICKET_SHARE * sum(r["weight"] for r in reqs) - 1e-9
    w, items = m["world"], m["items"]
    for fixed, diag, routine in (({i.rid for i in items if i.ticket_like}, 1.0, 0.0),
                                 ({i.rid for i in items if not i.ticket_like}, 0.0, 1.0)):
        keys, _ = _grade(m, *materialize(w, items, fixed))
        assert keys["diagnosis"] == diag and keys["routine"] == routine, keys
    only_routine = _grade(m, *materialize(w, items, {i.rid for i in items if not i.ticket_like}))[0]
    only_tickets = _grade(m, *materialize(w, items, {i.rid for i in items if i.ticket_like}))[0]
    assert only_tickets["progress"] > only_routine["progress"]


def test_ticket_weights_reach_the_share_in_every_family():
    from alertforge.expert.calib import TICKET_SHARE, ticket_weights
    from alertforge.expert import families
    for fam in families.FAMILIES:
        for seed in range(1, 10):
            w, items = families.build(fam, seed, MASTER)
            spec = {"family": fam, "requirements": [it.entry() for it in items]}
            ticket_weights(spec)
            tw = sum(r["weight"] for r in spec["requirements"] if r["ticket"])
            assert tw >= TICKET_SHARE * sum(r["weight"] for r in spec["requirements"]) - 1e-9, (fam, seed)


# ---------------------------------------------------------------------------- BM-5

def test_routine_overlap_and_e4_pairs():
    """SPEC 3.0: within a family no two tasks share more than 60% of their drawn routine kinds; E4's ticket pair
    varies (each of the three pairs on a third of the seeds)."""
    import itertools
    from alertforge.expert import families as F
    for fam in F.FAMILIES:
        kinds = [F.routine_kinds(F.build(fam, s, MASTER)[1]) for s in range(1, 10)]
        for a, b in itertools.combinations(kinds, 2):
            assert F.overlap(a, b) <= F.MAX_OVERLAP + 1e-9, (fam, a, b)
    pairs = {tuple(sorted(i.code for i in F.build("E4", s, MASTER)[1] if i.ticket_like)) for s in range(1, 10)}
    assert pairs == {("H04", "H11"), ("H11", "H12"), ("H04", "H12")}


# ---------------------------------------------------------------------------- BM-1

@needs_tools
def test_decoys_cost_preservation(h20_task):
    """The live decoys are right as they are: rewriting the average as a sum, or the per-pod Slack grouping as
    per-service, costs the preservation item (and so the solve)."""
    from alertforge.expert import solutions
    from alertforge.expert.model import route_for_team, rules_named
    m = h20_task
    w = m["world"]
    assert w.notes["decoys"] and w.notes.get("decoy_route")
    files, am, _ = solutions.oracle(m)
    name = next(n for n in w.notes["decoys"] if n.endswith("TargetsHalfDown"))
    for _, _, r in rules_named(files, name):
        r["expr"] = r["expr"].replace("avg by", "min by")
    keys, _ = _grade(m, files, am)
    assert keys["preservation"] < 1 and keys["outcome"] == 0.0
    files, am, _ = solutions.oracle(m)
    route_for_team(am, w.notes["decoy_route"]["team"])["routes"][-1]["group_by"] = ["alertname", "service"]
    keys, _ = _grade(m, files, am)
    assert keys["preservation"] < 1 and keys["outcome"] == 0.0


def test_strip_locators_knob_keeps_only_referenced_todos():
    from alertforge.expert import families, knobs
    from alertforge.expert.build import materialize, state_map
    w, items = families.build("E2", 3, MASTER)
    codes = {i.code for i in items}
    assert {"H19", "H05"} <= codes
    before = "\n".join(state_map(*materialize(w, items, set())).values())
    saved = set(knobs.ACTIVE)
    knobs.set_active(saved | {"strip-locators"})
    try:
        after = "\n".join(state_map(*materialize(w, items, set())).values())
    finally:
        knobs.set_active(saved)
    for planted in ("every pod on its own", "average the pods so one busy pod"):
        assert planted in before and planted not in after
    assert before.count("TODO(") == after.count("TODO(")


def test_e3_tickets_carry_packets():
    from alertforge.expert import families
    for seed in range(1, 10):
        w, items = families.build("E3", seed, MASTER)
        rng = random.Random(w.tseed ^ 0x7E47)
        for it in items:
            t = it.ticket(w, rng)
            if it.ticket_like:
                assert t.files and all(k.startswith("incidents/") for k in t.files), (seed, it.code)
                if it.code == "H15" and w.notes.get("pd_via"):
                    chan = next(v for k, v in t.files.items() if k.endswith("-alerts.txt"))
                    assert "(page " not in chan and "(critical " not in chan


# ---------------------------------------------------------------------------- RT-8

@needs_tools
def test_cheats_from_the_pristine_repo_earn_nothing(h20_task):
    from alertforge.expert import solutions
    m = h20_task
    partial = _grade(m, *solutions.partial(m)[:2])[0]["reward"]
    for name in sorted(solutions.CHEATS):
        if not solutions.ADVERSARIES[name][2](m):
            continue
        st = solutions.adversary(m, name, "pristine")
        if st is None:
            continue
        keys, _ = _grade(m, *st[:2])
        assert keys["reward"] <= min(solutions.PRISTINE_BOUND, partial) + 1e-9, (name, keys["reward"])
