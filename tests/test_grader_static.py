"""Grader static layer: YAML safety, CRD extraction, tamper rules, label/annotation checks, preservation."""

import pytest

from alertforge.grader import loader, static
from alertforge.grader.amcheck import inhibited, jaccard, parse_matchers, Unsupported


def refs(doc_text: str):
    return loader.index_rules([loader.parse_rule_file("rules/x.yml", doc_text)])


def test_alias_bomb_is_rejected():
    bomb = "a: &a [x, x]\n" + "".join(f"{c}: &{c} [*{p}, *{p}]\n" for p, c in zip("abcdefghijklmnopqrstuvwxyz", "bcdefghijklmnopqrstuvwxyz"))
    with pytest.raises(loader.YamlError):
        loader.safe_yaml(bomb)


def test_prometheusrule_manifest_is_unwrapped():
    text = ("apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata: {name: x}\n"
            "spec:\n  groups:\n  - name: g\n    rules:\n    - alert: A\n      expr: up == 0\n")
    f = loader.parse_rule_file("rules/x.yml", text)
    assert f.groups and f.groups[0]["rules"][0]["alert"] == "A"


def test_unparsable_file_is_reported_not_raised():
    f = loader.parse_rule_file("rules/x.yml", "groups:\n- name: g\n  rules:\n  - alert: A\n     expr: up == 0\n")
    assert f.groups is None and f.error


@pytest.mark.parametrize("rule,needle", [
    ("- record: ALERTS\n      expr: vector(1)", "reserved"),
    ("- record: http_requests_total\n      expr: vector(1)", "input series"),
    ("- record: x:y\n      expr: vector(1)\n      labels: {alertname: Foo}", "reserved label"),
    ('- record: x:y\n      expr: label_replace(vector(1), "alertname", "Foo", "", "")', "label_replace"),
])
def test_series_injection_is_tamper(rule, needle):
    _, _, every = refs(f"groups:\n- name: g\n  rules:\n    {rule}\n")
    reasons = static.rule_tamper(every, {"http_requests_total"})
    assert any(needle in r for r in reasons), reasons


def test_group_interval_offset_limit_are_tamper():
    _, _, every = refs("groups:\n- name: g\n  interval: 30s\n  limit: 5\n  rules:\n  - alert: A\n    expr: up == 0\n")
    reasons = static.rule_tamper(every, set())
    assert any("interval" in r for r in reasons) and any("limit" in r for r in reasons)


def test_default_interval_is_not_tamper():
    _, _, every = refs("groups:\n- name: g\n  interval: 1m\n  rules:\n  - alert: A\n    expr: up == 0\n")
    assert static.rule_tamper(every, set()) == []


def test_mute_time_intervals_are_tamper():
    am = {"route": {"receiver": "x", "routes": [{"receiver": "y", "mute_time_intervals": ["nights"]}]},
          "time_intervals": [{"name": "nights"}]}
    assert len(static.am_tamper(am)) == 2


def test_labels_must_match_exactly():
    req = {"severity": "page", "team": "t"}
    assert static.labels_ok({"labels": {"severity": "page", "team": "t"}}, req)
    assert not static.labels_ok({"labels": {"severity": "page", "team": "t", "extra": "1"}}, req)
    assert not static.labels_ok({"labels": {"severity": "warning", "team": "t"}}, req)


def test_summary_and_template_labels():
    rule = {"annotations": {"summary": "{{ $labels.service }} on {{ $labels.instance }}",
                            "description": '{{ index $labels "pod" }} {{ .Labels.namespace }}'}}
    assert static.summary_ok(rule)
    assert static.template_labels(rule) == {"service", "instance", "pod", "namespace"}
    assert not static.summary_ok({"annotations": {"summary": "errors are high"}})


def test_canon_ignores_formatting_but_not_group_knobs():
    a = {"alert": "A", "expr": "up  ==\n 0", "for": "5m", "labels": {"severity": "page"}}
    b = {"alert": "A", "expr": "up == 0", "for": "300s", "labels": {"severity": "page"}}
    assert loader.canon(a, {"name": "g"}) == loader.canon(b, {"name": "other", "interval": "1m"})
    assert loader.canon(a, {"name": "g"}) != loader.canon(a, {"name": "g", "query_offset": "1m"})


def test_preservation_duplicate_untouched_name_is_not_preserved():
    alerts, _, every = refs("groups:\n- name: g\n  rules:\n  - alert: A\n    expr: up == 0\n  - alert: A\n    expr: up == 0\n")
    h = loader.canon(every[0].rule, every[0].group)
    p, items = static.preservation(every, alerts, {"rules": [{"kind": "alert", "name": "A", "hash": h}]}, None, [], [])
    assert p == 0.0


# ---------------------------------------------------------------------------- inhibition simulator

RULE = {"source_matchers": ['alertname="SvcDown"'], "target_matchers": ['alertname=~"Burn|Err"'], "equal": ["service"]}


def alert(name, svc):
    return {"alertname": name, "service": svc}


def test_inhibit_equal_service():
    am = {"inhibit_rules": [RULE]}
    assert inhibited(am, alert("SvcDown", "a"), alert("Burn", "a")) is True
    assert inhibited(am, alert("SvcDown", "a"), alert("Burn", "b")) is False
    assert inhibited(am, alert("Burn", "a"), alert("SvcDown", "a")) is False
    assert inhibited(am, None, alert("Burn", "a")) is False


def test_inhibit_without_equal_overinhibits():
    am = {"inhibit_rules": [{k: v for k, v in RULE.items() if k != "equal"}]}
    assert inhibited(am, alert("SvcDown", "a"), alert("Burn", "b")) is True


def test_inhibit_legacy_match_and_regex_anchoring():
    am = {"inhibit_rules": [{"source_match": {"alertname": "SvcDown"}, "target_match_re": {"alertname": "Bur"}}]}
    assert inhibited(am, alert("SvcDown", "a"), alert("Burn", "a")) is False  # regex is fully anchored


def test_inhibit_unsupported_constructs_fail_closed():
    assert inhibited({"inhibit_rules": [{**RULE, "foo": 1}]}, alert("SvcDown", "a"), alert("Burn", "a")) is None
    assert inhibited({"inhibit_rules": [{"source_matchers": ['{alertname="x"}'], "target_matchers": []}]},
                     alert("x", "a"), alert("y", "a")) is None
    with pytest.raises(Unsupported):
        parse_matchers({"source_matchers": ['alertname=~"("']}, "source")


def test_jaccard():
    assert jaccard({"a", "b"}, {"a"}) == 0.5
    assert jaccard(set(), set()) == 1.0
