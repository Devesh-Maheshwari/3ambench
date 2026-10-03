"""Realism round (review-v02 R1-R4, R6-R11, BM-6, S6, S7, FA-4..FA-7): the expert tier's packets, text and world
history agree with themselves and with the repo a task ships. Most checks render the text of a whole block (5
families x 9 seeds) without promtool; the H04 replay and the Alertmanager check use the real tools."""

import csv
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys

import pytest
import yaml

from conftest import HAVE_TOOLS, needs_tools

MASTER = 20260930
FAMS = ["E1", "E2", "E3", "E4", "E5"]
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "v02")


@pytest.fixture(scope="module")
def block():
    from alertforge.expert import xlint
    return [xlint.render(f, s, MASTER) for f in FAMS for s in range(1, 10)]


def _with(block, code):
    return [m for m in block if any(it.code == code for it in m["items"])]


def test_survey_page_requests_identify_owner_and_user_impact(block):
    for m in _with(block, "R10"):
        if m["world"].family != "E5":
            continue
        survey = m["static"]["docs/oncall-survey-2026-q3.md"]
        for it in m["items"]:
            if it.code == "R10":
                answer = next(line for line in survey.splitlines() if it.p["alert"] in line)
                team = m["world"].svc(it.p["svc"]).team
                assert re.match(rf"\*\*\d+\.\*\* \({re.escape(team)}\) ", answer)
                assert "ustomers" in answer and "complete requests" in answer


# ---------------------------------------------------------------------------- R2: sameness

def test_no_sentence_in_more_than_a_third_of_its_tasks(block):
    from alertforge.expert import xlint
    bad, stats = xlint.check(block)
    assert bad == [], bad[:10]
    assert stats["max_jaccard_6gram"] < 0.4, stats


def test_house_documents_vary(block):
    readmes = [m["static"]["README.md"].replace(m["world"].company.name, "X").replace(m["world"].domain, "d")
               for m in block]
    assert len(set(readmes)) == len(readmes)
    adrs = {k for m in block for k in m["static"] if k.startswith("docs/adr/")}
    assert len(adrs) > 9, "ADR numbers are drawn per company"
    footers = {m["instruction"].rstrip().splitlines()[-1] for m in block}
    assert len(footers) >= 4


# ---------------------------------------------------------------------------- R3: org charts

def test_one_company_per_task_and_services_with_their_team(block):
    from alertforge.expert.vocab import AFFINITY
    assert len({m["world"].company.name for m in block}) == len(block)
    for m in block:
        w = m["world"]
        aff = AFFINITY[w.company.industry]
        tr = w.trigger or {}
        odd = (w.notes.get("oddity") or {}).get("svc")
        off = [s.name for s in w.services if s.name in aff and s.team != aff[s.name] and s.name != odd
               and not (s.name in tr.get("moved", []))]
        assert off == [], (w.task_id, off)
        own = yaml.safe_load(m["static"]["teams/ownership.yaml"])["teams"]
        if odd:
            assert f"- {odd}   # " in m["static"]["teams/ownership.yaml"]
            assert odd in own[w.svc(odd).team]["services"]


# ---------------------------------------------------------------------------- R4: history

def test_moved_services_live_in_the_old_teams_file(block):
    for m in block:
        w = m["world"]
        tr = w.trigger or {}
        if not tr.get("moved"):
            continue
        assert not any(f.startswith((f"legacy-{tr['new']}", f"{tr['new']}-rules")) for f in w.files), w.task_id
        for svc in tr["moved"]:
            assert w.svc(svc).home in (f"legacy-{tr['old']}", f"{tr['old']}-rules"), (w.task_id, svc, w.svc(svc).home)


def test_packets_agree_with_the_repo_and_the_calendar(block):
    """pagedteam/anachron (R4), deploy tags and hours, numbering, recency (R6), alert names (FA-4), rule dumps (S7)."""
    from alertforge.expert import textlint
    for m in block:
        assert textlint.lint(m) == [], (m["task_id"], textlint.lint(m)[:3])


def test_lints_catch_what_they_are_for(block):
    from alertforge.expert import textlint
    m = next(x for x in block if any(k.endswith("pagerduty-incidents.csv") for k in x["static"]))
    path, text = next((k, v) for k, v in m["static"].items() if k.endswith("pagerduty-incidents.csv"))
    seeded = {**m, "static": {**m["static"], path: text + "99999,2026-09-01T00:00:00Z,x (prod),[FIRING:1] NoSuchAlertDown "
                                                          "x (page x),high,resolved,,2026-09-01T00:10:00Z\n"}}
    assert any("NoSuchAlertDown" in r for r in textlint.paged_team_lint(seeded))
    dep = {**m, "static": {"incidents/INC-1/deploys.log":
                           "2026-09-08T02:55:00+01:00 rollouts x v2026.09.23-1 canary 1/3 started (x, weight 1%)\n"}}
    assert len(textlint.deploy_lint(dep)) == 2


# ---------------------------------------------------------------------------- R6: calendar

def test_queue_order_and_short_unexplained_outages(block):
    for m in block:
        text = m["instruction"]
        for it in m["items"]:
            if it.code == "R01" and it.tier == "slow" and "And the slow-burn ticket" in m["tickets"][it.rid].body:
                fast = next(o for o in m["items"] if o.code == "R01" and o.tier == "fast" and o.p["svc"] == it.p["svc"])
                assert text.index(m["tickets"][fast.rid].key) < text.index(m["tickets"][it.rid].key), m["task_id"]
        for it in [i for i in m["items"] if i.code == "H25"]:
            hm = re.findall(r"from (\d\d):(\d\d) to (\d\d):(\d\d)", m["tickets"][it.rid].title)
            for a, b, c, d in hm:
                assert (int(c) * 60 + int(d) - int(a) * 60 - int(b)) % 1440 <= 40


def test_e3_channel_exports_follow_the_starting_routing(block):
    """H25's #alerts-default export shows the Down page once, over the outage its ticket gives. The team-channel
    exports of H15 and H21 only carry alerts labelled with one of those teams: an alert with no `team` goes to
    #alerts-default, and a team without a route yet has no channel subtree to land in."""
    seen = 0
    for m in block:
        for it in [i for i in m["items"] if i.code in ("H15", "H21", "H25")]:
            t = m["tickets"][it.rid]
            mm = re.search(r"incidents/([A-Z]+-\d+)/", t.body)
            if mm is None:
                continue
            files = {p: x for p, x in m["static"].items() if p.startswith(f"incidents/{mm.group(1)}/")}
            if it.code == "H25":
                down = f"{m['world'].svc(it.p['svc']).camel}Down"
                lines = [ln for p, x in files.items() if p.endswith("alerts-default-channel.txt")
                         for ln in x.splitlines() if f"] {down} " in ln]
                if m["world"].notes.get("pd_via") or down not in it.p["alerts"]:
                    assert lines == [], (m["task_id"], lines)
                    continue
                assert [("FIRING" in ln) for ln in lines] == [True, False], (m["task_id"], lines)
                hm = [ln.split()[3] for ln in lines]
                span = re.search(r"from (\d\d:\d\d) to (\d\d:\d\d)", t.title)
                if span:
                    assert hm == [span.group(1), span.group(2)], (m["task_id"], hm, t.title)
                seen += 1
                continue
            for p, x in files.items():
                if "/slack-" not in p:
                    continue
                head, _, rest = x.partition("\n")
                teams = set(re.findall(r"#([a-z0-9-]+)-alerts", head))
                for ln in rest.splitlines():
                    if "[FIRING" in ln or "[RESOLVED" in ln:
                        assert ln.rstrip(")").split()[-1] in teams, (m["task_id"], p, ln)
                        seen += 1
    assert seen


def test_incidents_happen_before_the_handover(block):
    for m in block:
        today = m["world"].cal.today
        for path, text in m["static"].items():
            if path.startswith("incidents/"):
                for d in re.findall(r"(2026-\d\d-\d\d)", text[:20000]):
                    assert dt.date.fromisoformat(d) <= today, (m["task_id"], path, d, today)


# ---------------------------------------------------------------------------- R1: the H04 postmortem

def test_h04_narratives_are_spread_and_keep_the_graded_facts(block):
    ms = _with(block, "H04")
    kinds = []
    for m in ms:
        pm = next(v for k, v in m["static"].items() if k.endswith("postmortem.md"))
        assert "5 min" in pm and "15 min" in pm and "slo/services.yaml" in pm and "own request counter" in pm
        assert re.search(r"SEV[23]", pm) and "Impact" in pm and "went wrong" in pm
        title = pm.splitlines()[0]
        kinds.append(next(k for k, word in (("ingress", "ingress change"), ("edge", "edge"), ("weights", "traffic split"),
                                            ("dns", "failover drill")) if word in title))
    assert len(set(kinds)) == 4 and max(kinds.count(k) for k in set(kinds)) <= -(-len(kinds) // 3)


def _rates(doc, label):
    res = json.loads(doc)["data"]["result"]
    ts = sorted({int(t) for r in res for t, _ in r["values"]})
    inc = {}
    for r in res:
        vals = {int(t): float(v) for t, v in r["values"]}
        bad = r["metric"].get(label, "").startswith("5")
        for a, b in zip(ts, ts[1:]):
            if a in vals and b in vals:
                e, t = inc.get(b, (0.0, 0.0))
                inc[b] = (e + (vals[b] - vals[a] if bad else 0), t + vals[b] - vals[a])
    return inc


def test_h04_ingress_agrees_with_the_app(block):
    for m in _with(block, "H04"):
        ws = m["static"]
        d = next(k for k in ws if k.endswith("postmortem.md")).rsplit("/", 1)[0]
        app = _rates(next(v for k, v in ws.items() if k.startswith(d) and k.endswith("-requests.json")), "code")
        ing = _rates(next(v for k, v in ws.items() if k.startswith(d + "/query-range/ingress-")), "status")
        flow = [(e / t, ing[k][0] / ing[k][1]) for k, (e, t) in app.items() if t > 0 and ing.get(k, (0, 0))[1] > 0]
        assert flow and max(abs(a - b) for a, b in flow) < 0.01, m["task_id"]
        pm = ws[d + "/postmortem.md"]
        statuses = {r["metric"].get("status") for r in json.loads(next(v for k, v in ws.items()
                                                                      if k.startswith(d + "/query-range/ingress-")))["data"]["result"]
                    if "ingress" not in r["metric"]}
        if "default backend" in pm:
            assert statuses == {"404"}, (m["task_id"], statuses)


@needs_tools
def test_h04_alert_times_replay_with_promtool(block, tmp_path):
    """The times in slack-alerts.txt are what promtool gives for the repo's error-ratio rule over the packet's pull
    (the old checkpm.py, as a test): pending a minute into the flapping, firing 10 minutes later, resolved once the
    5m window holds no requests."""
    from alertforge.grader.promrun import tool
    from alertforge.render import write_tree
    checked = 0
    for m in _with(block, "H04"):
        ws = tmp_path / m["task_id"]
        write_tree(str(ws), {**m["static"], **{k: v for k, v in _pristine_rules(m).items()}})
        d = next(k for k in m["static"] if k.endswith("postmortem.md")).rsplit("/", 1)[0]
        pull = next(k for k in m["static"] if k.startswith(d) and k.endswith("-requests.json"))
        out = subprocess.run([sys.executable, str(ws / "bin" / "range2test"), str(ws / pull)], capture_output=True, text=True)
        doc = yaml.safe_load(out.stdout)
        first = min(int(t) for r in json.loads(m["static"][pull])["data"]["result"] for t, _ in r["values"])
        lines = m["static"][d + "/slack-alerts.txt"].splitlines()
        it = next(i for i in m["items"] if i.code == "H04")
        alert = f"{m['world'].svc(it.p['svc']).camel}HighErrorRatio"
        clock0 = dt.datetime.fromtimestamp(first, dt.timezone.utc) + dt.timedelta(hours=m["world"].company.utc_offset)
        fire_hm, res_hm = lines[0][:5], lines[1][:5]

        def minute(hm):
            h, mi = int(hm[:2]), int(hm[3:])
            return ((h * 60 + mi) - (clock0.hour * 60 + clock0.minute)) % 1440
        f, r = minute(fire_hm), minute(res_hm)
        doc["rule_files"] = [str(p) for p in sorted((ws / "rules").glob("*.yml"))]   # operator manifests aren't rule files
        if not any(alert in p.read_text() for p in (ws / "rules").glob("*.yml")):
            continue
        t = doc["tests"][0]
        count = 'count(ALERTS{{alertname="{a}",alertstate="{s}"}}) or vector(0)'
        t["promql_expr_test"] = [{"expr": count.format(a=alert, s=st), "eval_time": f"{x}m", "exp_samples": [{"labels": "{}", "value": v}]}
                                 for x, st, v in ((f - 1, "firing", 0), (f - 1, "pending", 1), (f, "firing", 1), (r, "firing", 0),
                                                  (r, "pending", 0))]
        tf = tmp_path / f"{m['task_id']}.test.yml"
        tf.write_text(yaml.safe_dump(doc))
        res = subprocess.run([tool("promtool"), "test", "rules", str(tf)], capture_output=True, text=True)
        assert res.returncode == 0, (m["task_id"], res.stdout[-800:])
        checked += 1
    assert checked >= 4


def _pristine_rules(m):
    from alertforge.expert.build import render_rules
    return render_rules(m["world"].notes["then"][0])


# ---------------------------------------------------------------------------- BM-6, S7, FA-4, FA-6: packets from the pristine world

def test_h03_log_shows_the_rule_as_it_was_that_night(block):
    from alertforge.expert import then
    seen = 0
    for m in _with(block, "H03"):
        it = next(i for i in m["items"] if i.code == "H03")
        ws = m["static"]
        log = next((v for k, v in ws.items() if k.endswith("prometheus.log")), None)
        _, _, rule = then.rules(m["world"], it.p["alert"])[0]
        if log is not None:
            assert f"team: {rule['labels']['team']}" in log.replace("\\n", "\n")
            if it.p["stopgap"]:
                assert "[5m]" in log and "[15m]" in log, m["task_id"]
                seen += 1
        if it.p["stopgap"]:
            stop = it.p["times"]["stop_hm"]
            thread = next(v for k, v in ws.items() if k.endswith("timeline.md") and it.p["inc"] in k)
            assert any(line.startswith(stop) for line in thread.splitlines()), (m["task_id"], stop)
    assert seen


def test_pd_rows_page_only_alerts_the_repo_has(block):
    from alertforge.expert import then
    for m in block:
        for path, text in m["static"].items():
            if not path.endswith(".csv") or not text.startswith("incident_number"):
                continue
            for row in csv.DictReader(io.StringIO(text)):
                mm = re.match(r"\[FIRING:\d+\] (\w+)", row["title"])
                if mm and mm.group(1) not in m["world"].cluster_level:
                    assert then.exists(m["world"], mm.group(1)), (m["task_id"], path, row["title"])
                if row["acknowledged_by"]:
                    assert " " in row["acknowledged_by"], "PagerDuty shows full names (R7)"


def test_r06_says_who_actually_got_the_pages(block):
    from alertforge.expert import then
    for m in _with(block, "R06"):
        it = next(i for i in m["items"] if i.code == "R06")
        t = m["tickets"][it.rid]
        lab = then.labels(m["world"], it.p["source"], it.p["svc"])
        if lab and then.pager(m["world"], lab) == "sre":
            assert "SRE" in t.title + t.body, m["task_id"]
        elif lab and then.pager(m["world"], lab) is None:
            assert "#" in t.body, m["task_id"]


# ---------------------------------------------------------------------------- S6, R8: data that looks scraped

def test_histogram_le_is_canonical(block):
    from alertforge.expert.xscen import Raw
    with pytest.raises(ValueError):
        Raw("http_request_duration_seconds_bucket", {"le": "1"}, [1]).encoded()
    assert Raw("http_request_duration_seconds_bucket", {"le": "1.0"}, [1]).encoded()
    for m in block:
        for path, text in m["static"].items():
            if "query-range/" in path and "le" in text:
                for r in json.loads(text)["data"]["result"]:
                    le = r["metric"].get("le")
                    assert le is None or le == "+Inf" or "." in le, (path, le)


def test_rule_dump_matches_a_real_prometheus():
    from alertforge.expert import evidence as ev
    real_log = open(os.path.join(FIX, "realprom-eval-fail.log")).read().splitlines()[0]
    rule = {"alert": "MemberApiHighErrorRatio", "for": "10m", "labels": {"team": "payer-integrations", "severity": "page"},
            "expr": 'sum by (service, code) (rate(http_requests_total{service="member-api",code=~"5.."}[15m])) / on (service) '
                    'sum by (service) (rate(http_requests_total{service="member-api"}[15m])) > 0.05',
            "annotations": {"summary": "{{ $labels.service }} is returning {{ $labels.code }}s"}}
    text = ev.rule_yaml(rule).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    assert f'rule="{text}"' in real_log
    real = json.load(open(os.path.join(FIX, "realprom-rules-api.json")))["data"]["groups"][0]
    clock = ev.Clock("2026-10-01", "03:00", 2, "CEST")
    mine = json.loads(ev.rules_api(clock, 5, real["file"], real["name"], rule, real["rules"][0]["lastError"], 0.0006))
    g = mine["data"]["groups"][0]
    assert list(g) == list(real) and list(g["rules"][0]) == list(real["rules"][0])
    assert g["rules"][0]["query"] == real["rules"][0]["query"]


def test_packets_look_scraped(block):
    for m in block:
        for path, text in m["static"].items():
            if "query-range/" not in path or "http_requests_total" not in text:
                continue
            res = json.loads(text)["data"]["result"]
            by_svc = {}
            for r in res:
                pod = r["metric"].get("pod", "")
                if r["metric"].get("__name__") == "http_requests_total" and "canary" not in pod:
                    by_svc.setdefault(r["metric"]["service"], set()).add(pod.rsplit("-", 2)[-2] if pod.count("-") >= 2 else pod)
            for svc, hashes in by_svc.items():
                assert len(hashes) <= 2, (m["task_id"], path, svc, hashes)   # one ReplicaSet (two across a fix-forward)
    logs = [v for m in block for k, v in m["static"].items() if k.endswith("app.log")]
    assert logs and all(re.search(r"\[[\w-]+,[0-9a-f]{16}\]", x) for x in logs)


# ---------------------------------------------------------------------------- R7, R9: people and the Alertmanager config

def test_people_ack_their_own_rota(block):
    for m in block:
        w = m["world"]
        full = {p.full: p for p in w.people}
        for path, text in m["static"].items():
            if path.endswith("pagerduty-incidents.csv"):
                for row in csv.DictReader(io.StringIO(text)):
                    p = full.get(row["acknowledged_by"])
                    pd = row["service_name"].split(" ")[0]
                    if p is not None and pd != "sre" and pd in w.teams:
                        assert p.team == pd, (m["task_id"], row)


@needs_tools
def test_alertmanager_drift_is_valid_config(tmp_path, block):
    from alertforge.expert.build import render_am
    from alertforge.grader.promrun import tool
    for m in block[::9]:
        am = m["world"].notes["then"][1]
        assert "templates" in am and all("routing_key_file" in c for r in am["receivers"] for c in r.get("pagerduty_configs", []))
        f = tmp_path / f"{m['task_id']}.yml"
        f.write_text(render_am(am))
        res = subprocess.run([tool("amtool"), "check-config", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, res.stdout + res.stderr
