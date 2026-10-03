"""Preservation judges what an untouched alert does, not how it is written (grader/replay.py, xgrade._preservation).

Regression: on afx-e3-s08 a pilot run solved every requirement and added `service: prior-auth-svc` to
`PriorAuthSvcDown`, whose expression already yields that label on both branches (README: `service` from the
expression or set statically). The Down alert is also the source of the OPS ticket's inhibition (R06). The edit
cost the preservation item and the outcome (reward 0.399).
"""

import os

import pytest

from conftest import HAVE_TOOLS

MASTER = 20260930


def test_an_alert_is_replayed_as_a_whole():
    """Both sides must have the same set of (for, keep_firing_for, expression signature): an identical copy may be
    added, a threshold may not change."""
    from alertforge.grader.replay import candidates
    if not HAVE_TOOLS:
        pytest.skip("promtool not available")
    warn = {"expr": 'max by (service) (x{service="a"}) > 0.8', "for": 300.0, "kff": None, "labels": {"severity": "warning"}}
    crit = {**warn, "expr": 'max by (service) (x{service="a"}) > 0.95', "labels": {"severity": "critical"}}
    assert candidates([("k", [warn, crit], [crit, warn, dict(warn)])])          # reordered, one copy added
    assert candidates([("k", [warn], [{**warn, "labels": {"severity": "warning", "service": "a"}}])])
    assert not candidates([("k", [warn, crit], [warn, {**crit, "expr": crit["expr"].replace("0.95", "0.9")}])])
    assert not candidates([("k", [warn, crit], [warn])])                         # a copy dropped
    assert not candidates([("k", [warn], [{**warn, "for": 600.0}])])


@pytest.fixture(scope="module")
def e3s08(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E3", 8, str(tmp_path_factory.mktemp("e3s08")), "afx-e3-s08", MASTER)


def _labelled(m, name, value, label="service", only_first=False, expr=None):
    from alertforge.expert import solutions
    from alertforge.expert.model import rules_named
    files, am, _ = solutions.oracle(m)
    for k, (_, _, r) in enumerate(rules_named(files, name)):
        if only_first and k:
            continue
        r["labels"][label] = value
        if expr is not None and k == 0:
            r["expr"] = expr(r["expr"])
    return files, am


def _grade(m, files, am):
    """(keys, {item: ok}, {item: replayed}); an alert with two definitions has two items of the same name: ok when
    both are, replayed when either is."""
    from alertforge.expert.build import grade_map, state_map
    keys, det = grade_map(m["spec"], os.path.dirname(m["hidden_dir"]), {**m["static"], **state_map(files, am)})
    ok, rep = {}, {}
    for x in det["preservation_items"]:
        ok[x["item"]] = ok.get(x["item"], True) and x["ok"]
        rep[x["item"]] = rep.get(x["item"], False) or bool(x.get("replayed"))
    return keys, ok, rep


@pytest.mark.slow
def test_static_own_service_on_an_untouched_alert_keeps_the_task_solved(e3s08):
    """The pilot's edit, everything else the oracle: reward 1.0. Its alert is R06's source (the inhibition reads
    `service` on both sides); the same edit on an alert no item is about passes by the replay."""
    m = e3s08
    r06 = next(i for i in m["items"] if i.code == "R06")
    src = r06.p["source"]
    keys, ok, _ = _grade(m, *_labelled(m, src, r06.p["svc"]))
    assert keys["reward"] == 1.0, {k: v for k, v in ok.items() if not v}
    pres = {e["name"]: e for e in m["spec"]["preserve"]["rules"]}
    other = next(n for n, e in pres.items() if n.endswith("Down") and not e.get("svc") and e.get("n", 1) == 1)
    svc = next(s.name for s in m["world"].services if other == f"{s.camel}Down")
    keys, ok, rep = _grade(m, *_labelled(m, other, svc))
    assert keys["reward"] == 1.0 and rep[f"rule:{other}"], (ok[f"rule:{other}"], rep[f"rule:{other}"])
    # a second, identical definition of an untouched alert in a new file fires as the same alert (a pilot run on
    # afx-e4-s02 did this); a second one with another threshold does not
    from alertforge.expert import solutions
    from alertforge.expert.model import RuleFileSpec, rules_named
    burn = next(n for n, e in pres.items() if n.endswith("ErrorBudgetBurnSlow") and e.get("n", 1) == 1)
    for bump, want in ((False, 1.0), (True, 0.0)):
        files, am, _ = solutions.oracle(m)
        copy = dict(next(r for _, _, r in rules_named(files, burn)))
        if bump:
            copy["expr"] = copy["expr"].replace("> (", "> 2 * (")
        files["zz-copy"] = RuleFileSpec("zz-copy", 2, [{"name": "copy", "rules": [copy]}])
        keys, ok, _ = _grade(m, files, am)
        assert keys["outcome"] == want, (bump, ok[f"rule:{burn}"])


@pytest.mark.slow
def test_a_static_label_that_changes_what_fires_still_costs_preservation(e3s08):
    m = e3s08
    r06 = next(i for i in m["items"] if i.code == "R06")
    wrong = next(s.name for s in m["world"].services if s.name != r06.p["svc"])
    pres = {e["name"]: e for e in m["spec"]["preserve"]["rules"]}
    other = next(n for n, e in pres.items() if n.endswith("Down") and not e.get("svc") and e.get("n", 1) == 1)
    for name, value, label in ((r06.p["source"], wrong, "service"), (other, r06.p["svc"], "service"),
                               (other, "prod", "env")):
        keys, ok, _ = _grade(m, *_labelled(m, name, value, label))
        assert keys["outcome"] == 0.0 and not ok[f"rule:{name}"], (name, label, value)


@pytest.mark.slow
def test_replay_pairs_the_definitions_of_a_two_copy_alert(e3s08):
    """A warning and a critical copy under one name: each pristine copy the fast path misses is paired with a
    current one and replayed (the saturated exercise scenarios make them fire). A changed threshold or a wrong
    service on a copy still fails."""
    m = e3s08
    pres = {e["name"]: e for e in m["spec"]["preserve"]["rules"]}
    two = next(n for n, e in pres.items() if e.get("n", 1) == 2 and e["kind"] == "alert" and not e.get("svc"))
    svc = next(s.name for s in m["world"].services if two.startswith(s.camel))
    for only_first in (False, True):
        keys, ok, rep = _grade(m, *_labelled(m, two, svc, only_first=only_first))
        assert keys["reward"] == 1.0 and ok[f"rule:{two}"] and rep[f"rule:{two}"], only_first
    wrong = next(s.name for s in m["world"].services if s.name != svc)
    keys, _, _ = _grade(m, *_labelled(m, two, wrong))
    assert keys["outcome"] == 0.0
    bump = _labelled(m, two, svc, expr=lambda e: e.replace("> 0.5", "> 0.6").replace("> 0.8", "> 0.85"))
    keys, _, _ = _grade(m, *bump)
    assert keys["outcome"] == 0.0


# ---------------------------------------------------------------------------- labels beyond the required ones

def test_labels_cover_the_required_ones_and_extras_route_alike():
    from alertforge.grader.xscore import _routes_alike, labels_cover
    req = {"severity": "page", "team": "a"}
    assert labels_cover({"severity": "page", "team": "a", "slo": "x-availability"}, req)
    assert not labels_cover({"severity": "page"}, req) and not labels_cover({"severity": "critical", "team": "a"}, req)

    class Ctx:
        am = {"route": {}}

        @staticmethod
        def receivers(lab):
            return ["pagerduty-a"] + (["pagerduty-b"] if lab.get("env") == "prod" else [])
    assert _routes_alike(Ctx, "XDown", "x", {**req, "slo": "x-availability"}, req)
    assert not _routes_alike(Ctx, "XDown", "x", {**req, "env": "prod"}, req)


@pytest.fixture(scope="module")
def e4s02(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E4", 2, str(tmp_path_factory.mktemp("e4s02")), "afx-e4-s02", MASTER)


@pytest.mark.slow
def test_an_extra_label_on_the_floor_alert_keeps_the_task_solved(e4s02):
    """A pilot run's floor alert carried `slo: member-api-availability` next to the required labels: the README
    names the labels an alert carries and never says "only these" (T2 lost L, s 0.85). A wrong team still costs L."""
    from alertforge.expert import solutions
    from alertforge.expert.build import grade_map, state_map
    from alertforge.expert.model import rules_named
    m = e4s02
    it = next(i for i in m["items"] if i.code == "H04")
    s = m["world"].svc(it.p["svc"])
    for value, label, want in ((f"{s.name}-availability", "slo", 1.0), ("someone-else", "team", None)):
        files, am, _ = solutions.oracle(m)
        for _, _, r in rules_named(files, it.p["alert"]):
            r["labels"][label] = value
        keys, det = grade_map(m["spec"], os.path.dirname(m["hidden_dir"]), {**m["static"], **state_map(files, am)})
        r = det["requirements"][it.rid]
        if want is not None:
            assert keys["reward"] == want and r["s"] == 1.0, r
        else:
            assert r["s"] < 1 and r["families"]["L"] < 1, r


def test_label_moves_allow_extra_labels_but_not_another_service():
    """Migration (R03) and relabel (R10/R11) items: the wanted labels with their values, other labels allowed; a
    static `service` must still be the alert's own (another value makes it a different service's alert), and an
    old-team value still fails."""
    from alertforge.grader.loader import RuleRef
    from alertforge.grader.xgrade import ExpertGrader

    class C:
        def __init__(self, labels):
            self.alerts = {"XDown": [RuleRef("rules/x.yml", {"name": "x"},
                                             {"alert": "XDown", "expr": 'sum by (service) (up{service="x"}) == 0',
                                              "labels": labels})]}
    want = {"severity": "page", "team": "new"}
    move = {"alert": "XDown", "labels": want, "svcs": ["x"], "n": 1, "allowed": [want]}
    relabel = {"alert": "XDown", "labels": want, "svc": "x", "n": 1}
    for lab, q in (({**want}, 1.0), ({**want, "slo": "x-availability"}, 1.0), ({**want, "service": "x"}, 1.0),
                   ({**want, "service": "y"}, 0.0), ({"severity": "page", "team": "old"}, 0.0),
                   ({"severity": "warning", "team": "new"}, 0.0)):
        assert ExpertGrader._labels_match(C(lab), move) == q, lab
        assert ExpertGrader._relabel_ok(C(lab), relabel) == q, lab
