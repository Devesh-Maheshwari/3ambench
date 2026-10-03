"""Expert-tier grader mechanics against the real tools: snapshot parsing (sp5), eval-error mask-and-rerun
(G1, p12/sp2), window probes that catch short fires between snapshots (hk2), the transitive ALERTS tamper
walk (sp23), two-sided inhibition and group_by inheritance (a03/a04, real Alertmanager when available)."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

import pytest
import yaml

from alertforge.grader import notify, snap
from alertforge.grader.loader import RuleFile, parse_rule_file
from alertforge.grader.tamper import alerts_tamper

from conftest import VENDOR_BIN, needs_tools

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "v02")
AM_BIN = os.environ.get("AF_ALERTMANAGER") or shutil.which("alertmanager") or os.path.join(VENDOR_BIN, "alertmanager")


def test_snapshot_parser_keeps_every_label():
    text = "".join(f.text for f in ET.parse(os.path.join(FIX, "sp5-junit.xml")).getroot().iter("failure"))
    got = snap.parse_got(text)
    assert len(got) == 4
    assert {g["alertname"] for g in got} == {"PodErr", "SvcDown"}
    assert 'p-3, "x"' in {g.get("pod") for g in got}
    assert all(g["af_snap"] == "S1" for g in got)


def _rules(text: str) -> list[RuleFile]:
    return [parse_rule_file("rules/x.yml", text)]


SP2 = """groups:
- name: d
  rules:
  - alert: D_ByCode
    expr: sum by (service, code) (rate(http_requests_total{code=~"5.."}[5m])) / on (service) sum by (service) (rate(http_requests_total[5m])) > 0.05
  - alert: F1_SumSum
    expr: sum by (service) (rate(http_requests_total{code=~"5.."}[5m])) / sum by (service) (rate(http_requests_total[5m])) > 0.05
"""
SP2_SERIES = [{"series": 'http_requests_total{service="pay",code="200"}', "values": "0+100x40"},
              {"series": 'http_requests_total{service="pay",code="500"}', "values": "0+2x14 30+5x25"},
              {"series": 'http_requests_total{service="pay",code="503"}', "values": "_x15 0+10x25"}]


@needs_tools
def test_eval_error_is_masked_and_the_rest_still_graded(tmp_path):
    """G1: promtool stops a group at the first evaluation error (p12). The grader masks the failing rule and
    re-runs, so the fix next to it is graded (sp2: F1 fires at 25m) and the broken rule fires nothing."""
    g = {"name": "g", "input_series": SP2_SERIES, "probes": [
        {"type": "snap", "id": "g.s25", "t": 25},
        {"type": "count", "id": "g.c10", "t": 10, "expect": 0,
         "expr": 'count(ALERTS{alertname="F1_SumSum",alertstate="firing"}) or vector(0)'}]}
    res, masks = snap.run_groups([g], _rules(SP2), str(tmp_path))
    assert masks == {"g": ["D_ByCode"]}
    names = {a["alertname"] for a in res["g"]["g.s25"]}
    assert names == {"F1_SumSum"}
    assert res["g"]["g.c10"] is True


@needs_tools
def test_window_probe_sees_a_fire_between_snapshots(tmp_path):
    """hk2: a page firing only from 9m to 11m passes point snapshots at 5m and 20m; a window over
    [0, 20m] prints it."""
    rules = "groups:\n- name: g\n  rules:\n  - alert: Spiky\n    expr: x > 5\n    labels: {severity: page}\n"
    g = {"name": "g", "input_series": [{"series": 'x{service="a"}', "values": "1x8 9 9 9 1x10"}], "probes": [
        {"type": "snap", "id": "g.p5", "t": 5}, {"type": "snap", "id": "g.p20", "t": 20},
        {"type": "window", "id": "g.w", "t0": 0, "t": 20}]}
    res, _ = snap.run_groups([g], _rules(rules), str(tmp_path))
    assert res["g"]["g.p5"] == [] and res["g"]["g.p20"] == []
    assert [a["alertname"] for a in res["g"]["g.w"]] == ["Spiky"]


def test_alerts_tamper_is_transitive_and_exempts_own_hysteresis():
    rules = []
    for f in ("sp23-hk2-rules.yml", "sp23-extra-rules.yml"):
        with open(os.path.join(FIX, f)) as fh:
            for g in yaml.safe_load(fh)["groups"]:
                rules += g["rules"]
    flagged = {r.split()[1] for r in alerts_tamper(rules)}
    assert flagged == {"SelfPending", "OtherFiring", "NameRegexWide", "TwoHop", "ReEmitViaRecord", "ReEmitViaNameRegex"}
    assert "PoolFlapping" not in flagged and "Benign" not in flagged


@needs_tools
def test_alerts_tamper_decodes_escapes_and_bare_selectors(tmp_path):
    """S3 / RT-1: escaped names (\\x, \\u, octal), quoted metric names and bare label selectors all reach ALERTS.
    promtool proves each Via* rule reads the pending series; the walk must flag every one and none of the controls."""
    for f in ("sp23-escape-rules.yml", "sp23-escape-test.yml"):
        shutil.copy(os.path.join(FIX, f), tmp_path / f)
    r = subprocess.run([os.environ.get("AF_PROMTOOL", "promtool"), "test", "rules", "sp23-escape-test.yml"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(FIX, "sp23-escape-rules.yml")) as fh:
        rules = yaml.safe_load(fh)["groups"][0]["rules"]
    flagged = {x.split()[1] for x in alerts_tamper(rules)}
    via = {x["alert"] for x in rules if x["alert"].startswith("Via")}
    assert flagged == via, flagged ^ via
    bad = [{"alert": "BadEscape", "expr": '{__name__="ALERT\\q"}'},
           {"alert": "Unclosed", "expr": 'count({alertstate="pending"'},
           {"alert": "HysteresisBare", "expr": 'x > 1 or on() count({alertname="HysteresisBare",alertstate="firing"}) > 0'}]
    names = {x.split()[-1] if x.startswith("unparsable") else x.split()[1] for x in alerts_tamper(bad)}
    assert names == {"BadEscape", "Unclosed", "HysteresisBare"}


# ---------------------------------------------------------------------------- Alertmanager semantics

A04 = {"inhibit_rules": [{"source_matchers": ['severity="critical"'], "target_matchers": ['severity=~"critical|warning"'],
                          "equal": ["cluster"]}]}
A04_ALERTS = [{"alertname": "CritA", "cluster": "c1", "severity": "critical"},
              {"alertname": "CritB", "cluster": "c1", "severity": "critical"},
              {"alertname": "Warn", "cluster": "c1", "severity": "warning"}]


def test_two_sided_inhibition_matches_alertmanager_a04():
    """a04 (real Alertmanager 0.28.1): two alerts that match both sides never mute each other; the warning is
    muted. An inhibited alert still mutes others (chain case)."""
    assert notify.inhibited(A04, A04_ALERTS) == [False, False, True]
    chain = {"inhibit_rules": [{"source_matchers": ['alertname="SiteDown"'], "target_matchers": ['alertname="ServiceDown"'], "equal": ["site"]},
                               {"source_matchers": ['alertname="ServiceDown"'], "target_matchers": ['alertname="HighLatency"'], "equal": ["site"]}]}
    al = [{"alertname": "SiteDown", "site": "fra"}, {"alertname": "ServiceDown", "site": "fra"}, {"alertname": "HighLatency", "site": "fra"}]
    assert notify.inhibited(chain, al) == [False, True, True]


def test_group_by_inheritance_a03():
    """a03: an unset or empty child group_by inherits; '...' groups on every label."""
    am = {"route": {"receiver": "r", "group_by": ["alertname"], "routes": [
        {"matchers": ['team="a"'], "receiver": "a", "group_by": ["..."]},
        {"matchers": ['team="b"'], "receiver": "b"},
        {"matchers": ['team="d"'], "receiver": "d", "group_by": []},
        {"matchers": ['team="e"'], "receiver": "e", "group_by": ["alertname", "service"]}]}}
    alerts = [{"alertname": "HighErrorRate", "service": "orders", "pod": "p1"},
              {"alertname": "HighErrorRate", "service": "orders", "pod": "p2"},
              {"alertname": "HighErrorRate", "service": "billing", "pod": "p3"},
              {"alertname": "HighLatency", "pod": "p4"}]
    want = {"a": 4, "b": 2, "d": 2, "e": 3}
    for team, n in want.items():
        keys = set()
        for a in alerts:
            lab = {**a, "team": team}
            (recv, gb, path), = notify.walk(am, lab)
            keys.add(notify.group_key(gb, lab))
        assert len(keys) == n, team


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.am
@pytest.mark.skipif(not os.path.exists(AM_BIN), reason="alertmanager binary not found")
def test_two_sided_inhibition_parity_with_real_alertmanager():
    d = tempfile.mkdtemp()
    cfg = {"route": {"receiver": "null"}, "receivers": [{"name": "null"}], **A04}
    with open(os.path.join(d, "am.yml"), "w") as fh:
        yaml.safe_dump(cfg, fh)
    port, cport = _port(), _port()
    proc = subprocess.Popen([AM_BIN, f"--config.file={d}/am.yml", f"--storage.path={d}/data",
                             f"--web.listen-address=127.0.0.1:{port}", "--cluster.listen-address=",
                             f"--cluster.advertise-address=127.0.0.1:{cport}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/api/v2"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/status", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        req = urllib.request.Request(f"{base}/alerts", json.dumps([{"labels": a} for a in A04_ALERTS]).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        got = {}
        for _ in range(30):
            time.sleep(0.3)
            got = {a["labels"]["alertname"]: bool(a["status"]["inhibitedBy"])
                   for a in json.load(urllib.request.urlopen(f"{base}/alerts", timeout=5))}
            if got.get("Warn"):
                break
        assert [got[a["alertname"]] for a in A04_ALERTS] == notify.inhibited(A04, A04_ALERTS)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- matcher strings (Alertmanager's grammar)

MATCHER_FORMS = [
    ['alertname="S0"'],                                   # one quoted matcher
    ['alertname="S1",severity="page"'],                   # two in one string
    ['{alertname="S2"}'],                                 # brace form
    ['alertname=S3'],                                     # unquoted value
    ['{ alertname = "S4" , severity=~"page|critical" }'],
    ['alertname="S5",severity="ticket"'],                 # second matcher does not match
    ['alertname=~S6|Zz'],                                 # unquoted regex
    ['{alertname="S7",}'],                                # trailing comma
    ['alertname=~"S"'],                                   # regexes are anchored
    ['alertname="S9"', 'note="a \\"q\\" b"'],             # escaped quotes
    ['alertname=~"S1.*", severity!="ticket"'],
    [' {alertname="S11"}'],                               # only the UTF-8 parser reads this one
    ['alertname="S12", note=~"x\\\\.y"'],
]


def _matcher_case():
    rules, alerts = [], []
    for i, f in enumerate(MATCHER_FORMS):
        rules.append({"source_matchers": f, "target_matchers": [f'alertname="T{i}"'], "equal": ["cluster"]})
        alerts.append({"alertname": f"S{i}", "severity": "page", "cluster": "c1", "note": 'a "q" b' if i == 9 else "x.y"})
        alerts.append({"alertname": f"T{i}", "cluster": "c1"})
    return {"route": {"receiver": "null"}, "receivers": [{"name": "null"}], "inhibit_rules": rules}, alerts


def test_matcher_strings_follow_alertmanager_grammar():
    from alertforge.grader import matchers
    assert matchers.parse('{alertname="X",severity=~"page|critical"}') == [("alertname", "=", "X"), ("severity", "=~", "page|critical")]
    assert matchers.parse('alertname="X",severity="page"') == [("alertname", "=", "X"), ("severity", "=", "page")]
    assert matchers.parse("alertname=X") == [("alertname", "=", "X")]
    assert matchers.parse('a="x\\\\.y"') == [("a", "=", "x\\.y")]
    assert matchers.parse('{alertname="X"') == [("alertname", "=", "X")]   # the classic parser only trims braces
    for bad in ('alertname="X', 'alertname', 'a=~"("'):
        with pytest.raises(matchers.Unsupported):
            matchers.parse(bad)
    cfg, alerts = _matcher_case()
    mask = notify.inhibited(cfg, alerts)
    assert [mask[2 * i + 1] for i in range(len(MATCHER_FORMS))] == [i not in (5, 8) for i in range(len(MATCHER_FORMS))]


def _real_inhibited(cfg: dict, alerts: list[dict]) -> dict[str, bool]:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "am.yml"), "w") as fh:
        yaml.safe_dump(cfg, fh)
    port, cport = _port(), _port()
    proc = subprocess.Popen([AM_BIN, f"--config.file={d}/am.yml", f"--storage.path={d}/data",
                             f"--web.listen-address=127.0.0.1:{port}", "--cluster.listen-address=",
                             f"--cluster.advertise-address=127.0.0.1:{cport}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/api/v2"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/status", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        req = urllib.request.Request(f"{base}/alerts", json.dumps([{"labels": a} for a in alerts]).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        time.sleep(2)
        return {a["labels"]["alertname"]: bool(a["status"]["inhibitedBy"])
                for a in json.load(urllib.request.urlopen(f"{base}/alerts", timeout=5))}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.am
@needs_tools
@pytest.mark.skipif(not os.path.exists(AM_BIN), reason="alertmanager binary not found")
def test_matcher_strings_parity_with_real_alertmanager(tmp_path):
    """Every spelling amtool accepts is read the same way by the simulator and by a real Alertmanager 0.28."""
    from alertforge.grader.promrun import tool
    cfg, alerts = _matcher_case()
    p = tmp_path / "am.yml"
    p.write_text(yaml.safe_dump(cfg))
    assert subprocess.run([tool("amtool"), "check-config", str(p)], capture_output=True).returncode == 0
    real = _real_inhibited(cfg, alerts)
    mask = notify.inhibited(cfg, alerts)
    assert {a["alertname"]: m for a, m in zip(alerts, mask)} == real


# ---------------------------------------------------------------------------- R05 retired receiver, kept severity

def _claims_root(last_child: dict) -> dict:
    team = {"matchers": ['team="claims"'], "receiver": "slack-claims", "routes": [
        {"matchers": ['severity=~"page|critical"'], "receiver": "pagerduty-claims", "continue": True},
        {"matchers": ['severity="ticket"'], "receiver": "jira-claims", "continue": True},
        last_child]}
    return {"receiver": "slack-alerts-default", "routes": [team]}


def test_a_retired_receiver_no_alert_can_reach_does_not_fail_the_move():
    """fairness G2: the channel move counts what alerts do. A subtree whose unconditional last child sends every alert
    to the new channel no longer uses the old receiver, even if its own (never reached) receiver still names it; a
    ticket that says "nothing may route to it" stays literal."""
    from alertforge.grader.xgrade import _receiver_used
    root = _claims_root({"receiver": "slack-claims-oncall"})
    assert not _receiver_used(root, "slack-claims")
    assert _receiver_used(root, "slack-claims", literal=True)
    # a warning-only last child: everything else still ends at the subtree's own receiver
    assert _receiver_used(_claims_root({"matchers": ['severity="warning"'], "receiver": "slack-claims-oncall"}),
                          "slack-claims")
    # an unconditional child without a receiver inherits the old one
    assert _receiver_used(_claims_root({}), "slack-claims")
    # a route after an unconditional sibling without `continue` is never reached
    shadowed = {"receiver": "slack-alerts-default", "routes": [
        {"receiver": "slack-catch-all"}, {"matchers": ['team="claims"'], "receiver": "slack-claims"}]}
    assert not _receiver_used(shadowed, "slack-claims")
    shadowed["routes"][0]["continue"] = True
    assert _receiver_used(shadowed, "slack-claims")


class _Def:
    def __init__(self, rule, group):
        self.rule, self.group = rule, group


def test_the_kept_check_reads_critical_and_page_as_one_severity():
    """fairness G3: README: `critical` is the older spelling of `page` (same routing, same page). Respelling the
    severity of an alert the queue asked to leave as is keeps it intact; any other severity change still breaks it."""
    from alertforge.grader.norm import core_entry
    from alertforge.grader.xgrade import ExpertGrader
    rule = {"alert": "BillingSvcDbConnectionsHigh", "for": "5m", "labels": {"severity": "critical", "team": "billing"},
            "expr": 'max by (service) (db_pool_connections_active{service="billing-svc"} / '
                    'db_pool_connections_max{service="billing-svc"}) > 0.95'}
    group = {"name": "billing", "rules": [rule]}
    g = ExpertGrader.__new__(ExpertGrader)
    g.spec = {"kept": [{"alert": rule["alert"], "want": core_entry(rule, group, ("severity",))}],
              "severity_alias": {"critical": "page"}}

    def kept(sev):
        d = _Def({**rule, "labels": {**rule["labels"], "severity": sev}}, group)
        return g._kept({rule["alert"]: [d]})[0]["k"]

    assert kept("critical") == 1.0 and kept("page") == 1.0
    assert kept("ticket") == 0.0 and kept("warning") == 0.0
    g.spec["severity_alias"] = {}
    assert kept("page") == 0.0
