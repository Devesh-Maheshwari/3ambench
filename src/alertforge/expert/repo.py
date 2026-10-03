"""The healthy expert repo: services, three generations of rule files, live distractors, routing tree.

Generation 1 (legacy-<team>.yml): inline ratios, two-tier warning/critical copies, `job:` records, quoted
durations, TODO comments. Generation 2: slo-records.yml, slo-<service>.yml, service-health.yml,
capacity.yml, with `expr: |` blocks. Generation 3: PrometheusRule manifests for the teams on the operator.
Every rule here is correct; defects are planted on top by the queue items.
"""

from __future__ import annotations

import random

from ..distractors import CORPUS
from .model import RuleFileSpec, Svc, World
from .vocab import SERVICES, SHARED_TEAMS, TEAMS

WINDOWS = ["5m", "30m", "1h", "6h"]
FACTORS = {30: ("14.4", "6"), 7: ("3.36", "1.4")}
GRPC_ERR = "Unavailable|Internal|DeadlineExceeded|Unknown|DataLoss"
DISTRACTOR_FILES = ["node-exporter", "postgres", "redis", "kafka", "nginx", "jvm"]


def err_sel(family: str) -> tuple[str, str]:
    if family == "grpc":
        return "grpc_server_handled_total", f'grpc_code=~"{GRPC_ERR}"'
    return "http_requests_total", 'code=~"5.."'


def sli_record(family: str, window: str) -> dict:
    m, bad = err_sel(family)
    return {"record": f"slo:sli_error:ratio_rate{window}",
            "expr": f"sum by (service) (rate({m}{{{bad}}}[{window}])) / sum by (service) (rate({m}[{window}]))"}


def down_rule(w: World, s: Svc, for_: str = "3m") -> dict:
    by = "service, cluster" if s.cluster else "service"
    return {"alert": f"{s.camel}Down",
            "expr": f'sum by ({by}) (up{{service="{s.name}"}}) == 0 or absent(up{{service="{s.name}"}})',
            "for": for_, "labels": {"severity": "page", "team": s.team},
            "annotations": {"summary": f"{s.name} has no healthy scrape targets",
                            "runbook_url": w.runbook(f"{s.camel}Down")}}


def ingress_sel(s: Svc, bad: bool = False) -> str:
    """The service's requests as the ingress controller counts them (README: this Prometheus scrapes the ingress;
    scraped.py puts the same requests there): an error ratio may be read at the load balancer instead."""
    return f'nginx_ingress_controller_requests{{service="{s.name}"' + (',status=~"5.."}' if bad else "}")


def ingress_ratio_expr(s: Svc, window: str = "5m") -> str:
    return (f"sum by (service) (rate({ingress_sel(s, True)}[{window}]))"
            f" / sum by (service) (rate({ingress_sel(s)}[{window}])) > {s.err_thr}")


def error_ratio_rule(w: World, s: Svc, window: str = "5m", for_: str = "10m") -> dict:
    m, bad = err_sel(s.family)
    expr = (f'sum by (service) (rate({m}{{service="{s.name}",{bad}}}[{window}]))'
            f' / sum by (service) (rate({m}{{service="{s.name}"}}[{window}])) > {s.err_thr}')
    return {"alert": f"{s.camel}HighErrorRatio", "expr": expr, "for": for_,
            "labels": {"severity": "page", "team": s.team},
            "annotations": {"summary": "{{ $labels.service }} is failing {{ $value | humanizePercentage }} of requests",
                            "runbook_url": w.runbook(f"{s.camel}HighErrorRatio")}}


def burn_rules(w: World, s: Svc) -> list[dict]:
    fast, slow = FACTORS[s.window_d]
    b = f"{1 - float(s.target):.10f}".rstrip("0")
    out = []
    for name, lw, sw, k, for_, sev in ((f"{s.camel}ErrorBudgetBurnFast", "1h", "5m", fast, "2m", "page"),
                                        (f"{s.camel}ErrorBudgetBurnSlow", "6h", "30m", slow, "15m", "ticket")):
        sel = f'{{service="{s.name}"}}'
        out.append({"alert": name,
                    "expr": f"slo:sli_error:ratio_rate{lw}{sel} > ({k} * {b}) and slo:sli_error:ratio_rate{sw}{sel} > ({k} * {b})",
                    "for": for_, "labels": {"severity": sev, "team": s.team, "slo": f"{s.name}-availability"},
                    "annotations": {"summary": "{{ $labels.service }} is spending its error budget "
                                               + ("fast" if sev == "page" else "steadily"),
                                    "runbook_url": w.runbook(name)}})
    return out


def latency_two_tier(w: World, s: Svc) -> list[dict]:
    q = (f'histogram_quantile(0.99, sum by (service, le) (rate(http_request_duration_seconds_bucket{{service="{s.name}"}}[5m])))')
    name = f"{s.camel}LatencyHigh"
    return [{"alert": name, "expr": f"{q} > 0.5", "for": "10m", "labels": {"severity": "warning", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} p99 above 500ms", "runbook_url": w.runbook(name)}},
            {"alert": name, "expr": f"{q} > 1", "for": "10m", "labels": {"severity": "critical", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} p99 above 1s", "runbook_url": w.runbook(name)}}]


def db_two_tier(w: World, s: Svc) -> list[dict]:
    r = (f'max by (service) (db_pool_connections_active{{service="{s.name}"}}'
         f' / db_pool_connections_max{{service="{s.name}"}})')
    name = f"{s.camel}DbConnectionsHigh"
    return [{"alert": name, "expr": f"{r} > 0.8", "for": "5m", "labels": {"severity": "warning", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} is using over 80% of its DB pool", "runbook_url": w.runbook(name)}},
            {"alert": name, "expr": f"{r} > 0.95", "for": "5m", "labels": {"severity": "critical", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} DB pool nearly exhausted", "runbook_url": w.runbook(name)}}]


def memory_rule(w: World, s: Svc) -> dict:
    return {"alert": "ContainerMemoryNearLimit",
            "expr": (f'max by (service) (container_memory_working_set_bytes{{service="{s.name}",container="{s.name}"}}'
                     f' / container_spec_memory_limit_bytes{{service="{s.name}",container="{s.name}"}}) > 0.9'),
            "for": "15m", "labels": {"severity": "ticket", "team": s.team},
            "annotations": {"summary": "{{ $labels.service }} keeps running close to its memory limit",
                            "runbook_url": w.runbook("ContainerMemoryNearLimit")}}


def legacy_records(team_svcs: list[Svc], family: str) -> list[dict]:
    """The team's old dashboard records, scoped to its own services (one pair per SLI family)."""
    m, bad = err_sel(family)
    names = [s.name for s in team_svcs if s.family == family]
    sel = f'service="{names[0]}"' if len(names) == 1 else f'service=~"{"|".join(names)}"'
    return [{"record": "job:http_requests:rate5m" if family == "http" else "job:grpc_calls:rate5m",
             "expr": f"sum by (job, service) (rate({m}{{{sel}}}[5m]))"},
            {"record": "job:http_errors:rate5m" if family == "http" else "job:grpc_errors:rate5m",
             "expr": f"sum by (job, service) (rate({m}{{{sel},{bad}}}[5m]))"}]


# ---------------------------------------------------------------------------- services and teams

def pick_services(rng: random.Random, w_industry: str, n: int, need: dict) -> list[tuple[str, str, str]]:
    """n (name, kind, family) from the industry list, honouring `need` = {kind: count}."""
    pool = list(SERVICES[w_industry])
    rng.shuffle(pool)
    out = []
    for kind, k in need.items():
        cands = [x for x in pool if (x[2] == kind if kind == "latency" else x[1] == kind and x[2] != "latency")
                 and x not in out]
        out += cands[:k]
    for x in pool:
        if len(out) >= n:
            break
        if x not in out and x[2] != "latency":
            out.append(x)
    return out[:max(n, len(out))]


def assign(rng: random.Random, w: World, picked: list[tuple[str, str, str]], clusters: bool) -> None:
    teams = list(w.teams)
    for i, (name, kind, fam) in enumerate(picked):
        team = teams[i % len(teams)]
        s = Svc(name, team, kind, "http" if fam == "latency" else fam, cluster=clusters)
        s.target = rng.choice(["0.999", "0.995", "0.9995", "0.99"]) if kind in ("api", "grpc") else "0.99"
        s.rps = rng.choice([30, 45, 60, 80, 120]) if kind in ("api", "grpc") else rng.choice([8, 12, 20])
        s.floor = None
        s.pods = rng.choice([3, 4, 6])
        s.err_thr = rng.choice(["0.02", "0.05", "0.1"])
        s.window_d = 30
        w.services.append(s)


# ---------------------------------------------------------------------------- rule files

def owner_then(w: World, s: Svc) -> str:
    """The team a service belonged to when its rules were written: a service moved in this week's split still sits
    in its old team's file (R4: the new team has no files of its own yet)."""
    tr = w.trigger or {}
    return tr["old"] if s.name in tr.get("moved", []) else s.team


def build_rules(w: World, rng: random.Random) -> None:
    owners = [t for t in w.teams if t != (w.trigger or {}).get("new")]
    on_operator = set(rng.sample(owners, 1 if len(owners) < 5 else 2))
    legacy_teams = [t for t in owners if t not in on_operator]
    files: dict[str, RuleFileSpec] = {}
    rec = [sli_record(fam, win) for fam in sorted({s.family for s in w.services}) for win in WINDOWS]
    files["slo-records"] = RuleFileSpec("slo-records", 2, [{"name": "slo-sli", "rules": rec}],
                                        ["SLI recording rules. One record per window, every service that emits the",
                                         "family's metrics. Burn alerts read these, nothing else should."])
    health = RuleFileSpec("service-health", 2, [{"name": "service-health", "rules": []}],
                          ["One Down alert per service. See README, Service health."])
    capacity = RuleFileSpec("capacity", 2, [{"name": "capacity", "rules": []}],
                            ["Capacity tickets. Pages for crashes come from the platform Prometheus."])
    for t in sorted(on_operator):
        files[f"{t}-rules"] = RuleFileSpec(f"{t}-rules", 3, [], [], "yaml")
    for t in legacy_teams:
        files[f"legacy-{t}"] = RuleFileSpec(f"legacy-{t}", 1, [], [
            f"{t} alerts from before the conventions in README.md.",
            "Don't reformat in passing; move things out when you touch them for a reason."])
    for s in w.services:
        then = owner_then(w, s)
        op = then in on_operator
        s.home = f"{then}-rules" if op else f"legacy-{then}"
        home = files[s.home]
        grp = next((g for g in home.groups if g["name"] == s.name), None)
        if grp is None:
            grp = {"name": s.name, "rules": []}
            home.groups.append(grp)
        if op:
            grp["rules"].append(down_rule(w, s))
        else:
            health.groups[0]["rules"].append(down_rule(w, s))
        if s.kind in ("api", "grpc", "worker"):
            er = error_ratio_rule(w, s)
            if not op and rng.random() < 0.5:
                er["_comment"] = [f"threshold agreed with {then} in {w.company.key}-{rng.randint(1100, 1900)}"]
            grp["rules"].append(er)
        if s.kind in ("api", "grpc"):
            if op:
                grp["rules"] += burn_rules(w, s)
            else:
                stem = f"slo-{s.name}"
                files[stem] = RuleFileSpec(stem, 2, [{"name": f"slo-{s.name}", "rules": burn_rules(w, s)}])
        if not op and s.kind in ("api", "worker") and s.family == "http" and (s.kind == "api" or rng.random() < 0.5):
            grp["rules"] += latency_two_tier(w, s)
            if rng.random() < 0.3:
                grp["rules"][-1]["_comment"] = ["TODO(2024) move to a latency SLO once we have one"]
        if not op and s.kind in ("api", "worker") and rng.random() < 0.5:
            grp["rules"] += db_two_tier(w, s)
        if s.kind in ("indexer", "worker") and rng.random() < 0.8:
            capacity.groups[0]["rules"].append(memory_rule(w, s))
        if not op and s.kind in ("api", "worker", "grpc"):
            recs = next((g for g in home.groups if g["name"] == f"{then}-records"), None)
            if recs is None:
                recs = {"name": f"{then}-records", "rules": []}
                home.groups.insert(0, recs)
                recs["_comment"] = ["old dashboard records; the svc-overview board still reads these"]
            mates = [x for x in w.services if owner_then(w, x) == then and x.kind in ("api", "worker", "grpc")]
            recs["rules"] = [r for r in recs["rules"] if not r["record"].startswith(("job:http_", "job:grpc_"))]
            for fam in sorted({x.family for x in mates}):
                recs["rules"] += legacy_records(mates, fam)
    files["service-health"] = health
    if capacity.groups[0]["rules"]:
        files["capacity"] = capacity
    fams = sorted({x.family for x in w.services})
    dash = []
    for fam in fams:
        m, bad = err_sel(fam)
        pre = "http" if fam == "http" else "grpc"
        dash += [{"record": f"service:{pre}_requests:rate5m", "expr": f"sum by (service) (rate({m}[5m]))"},
                 {"record": f"service:{pre}_errors:rate5m", "expr": f"sum by (service) (rate({m}{{{bad}}}[5m]))"}]
    dash.append({"record": "service:http_request_duration_seconds:p99_5m",
                 "expr": "histogram_quantile(0.99, sum by (service, le) (rate(http_request_duration_seconds_bucket[5m])))"})
    files["dashboards"] = RuleFileSpec("dashboards", 2, [{"name": "dashboards", "rules": dash}],
                                       ["Records the svc-overview and team boards read. Not for alerting."])
    for stem in sorted(rng.sample(DISTRACTOR_FILES, rng.randint(5, 6))):
        files[stem] = distractor_file(w, stem)
    for f in files.values():
        f.groups = [g for g in f.groups if g["rules"]]
    w.files = {k: v for k, v in files.items() if v.groups}


# The ingress in front of the apps is ingress-nginx: its rules read the controller's own request counter, the
# series the incident packets pull (R8). Adapted from the awesome-prometheus-alerts nginx rules.
INGRESS_NGINX = [
    ("NginxHighHttp4xxErrorRate", 'sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"4.."}[5m])) / sum by (ingress) '
     '(rate(nginx_ingress_controller_requests[5m])) > 0.05', "5m", "warning", "High 4xx rate on ingress {{ $labels.ingress }}"),
    ("NginxHighHttp5xxErrorRate", 'sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"5.."}[5m])) / sum by (ingress) '
     '(rate(nginx_ingress_controller_requests[5m])) > 0.05', "5m", "warning", "High 5xx rate on ingress {{ $labels.ingress }}"),
    ("NginxLatencyHigh", 'histogram_quantile(0.99, sum by (ingress, le) (rate(nginx_ingress_controller_request_duration_seconds_bucket[5m]))) > 3',
     "5m", "warning", "High p99 on ingress {{ $labels.ingress }}"),
    ("NginxConfigReloadFailed", "nginx_ingress_controller_config_last_reload_successful == 0", "5m", "warning",
     "ingress-nginx can't load its config on {{ $labels.controller_pod }}"),
]


def distractor_file(w: World, stem: str) -> RuleFileSpec:
    rules = []
    for name, expr, for_, _sev, summary in (INGRESS_NGINX if stem == "nginx" else CORPUS[stem]):
        r = {"alert": name, "expr": expr}
        if for_ != "0m":
            r["for"] = for_
        r["labels"] = {"severity": "warning", "team": "infra"}
        r["annotations"] = {"summary": summary, "runbook_url": w.runbook(name)}
        rules.append(r)
    heads = [[f"Adapted from awesome-prometheus-alerts ({stem}). Fleet alerts: infra, warning."],
             [f"{stem} fleet alerts. Thresholds started from awesome-prometheus-alerts; infra owns them."],
             ["Fleet alerts, warning only. Nobody is paged from this file."],
             [f"Copied from awesome-prometheus-alerts ({stem}) and trimmed to what we scrape."], []]
    pick = sum(map(ord, f"{w.company.name}:{stem}")) % len(heads)
    return RuleFileSpec(stem, 1 if stem in ("node-exporter", "postgres") else 2, [{"name": stem, "rules": rules}],
                        heads[pick])


ALERT_FLOOR = 60   # SPEC 2.2: a repo has at least 60 alert rules
FILL_TARGET = 63   # the queue removes up to three rules from the starting repo (new burn alerts, a floor alert)


def n_alerts(w: World) -> int:
    return sum(1 for f in w.files.values() for g in f.groups for r in g["rules"] if "alert" in r)


def _pristine_alerts(w: World, items) -> int:
    """Alert rules in the repo the queue starts from (the items' breakage applied to a copy)."""
    files, am = w.state()
    for it in items or []:
        it.break_(files, am, w)
    return sum(1 for f in files.values() for g in f.groups for r in g["rules"] if "alert" in r)


def fill_to_floor(w: World, items=None) -> None:
    """Exporter files the draw left out go back in until the repo the queue starts from reaches the floor (fleet
    warnings nobody's queue is about), then the DB pool pair for services that don't have one yet: legacy homes
    first, then the teams on the operator."""
    def short() -> bool:
        return n_alerts(w) < FILL_TARGET or (items is not None and _pristine_alerts(w, items) < ALERT_FLOOR + 1)

    for stem in DISTRACTOR_FILES:
        if not short():
            return
        if stem not in w.files:
            w.files[stem] = distractor_file(w, stem)
    busy = w.notes.get("busy", set()) | set((w.trigger or {}).get("moved", []))
    have = {r.get("alert") for f in w.files.values() for g in f.groups for r in g["rules"]}
    from .model import home_group
    for legacy in (True, False):
        for sv in sorted(w.services, key=lambda x: x.name):
            if not short():
                return
            if (sv.home.startswith("legacy-") == legacy and sv.kind in ("api", "worker") and sv.name not in busy
                    and f"{sv.camel}DbConnectionsHigh" not in have):
                home_group(w, sv)["rules"] += db_two_tier(w, sv)
                have.add(f"{sv.camel}DbConnectionsHigh")


def catalog(w: World) -> dict:
    out = []
    for s in w.services:
        if s.kind not in ("api", "grpc"):
            continue
        d = {"name": s.name, "team": s.team, "slo_target": float(s.target), "window": f"{s.window_d}d",
             "sli_family": s.family}
        d["metrics"] = ["grpc_server_handled_total" if s.family == "grpc" else "http_requests_total"]
        if s.family == "grpc":
            d["error_codes"] = GRPC_ERR.split("|")
        if s.floor is not None:
            d["traffic_floor_rps"] = s.floor
        out.append(d)
    return {"services": out}


# ---------------------------------------------------------------------------- alertmanager

def team_route(team: str, legacy: bool = False) -> dict:
    if legacy:
        return {"match": {"team": team}, "receiver": f"slack-{team}",
                "routes": [{"match_re": {"severity": "page|critical"}, "receiver": f"pagerduty-{team}", "continue": True},
                           {"match": {"severity": "ticket"}, "receiver": f"jira-{team}", "continue": True},
                           {"receiver": f"slack-{team}"}]}
    return {"matchers": [f'team="{team}"'], "receiver": f"slack-{team}",
            "routes": [{"matchers": ['severity=~"page|critical"'], "receiver": f"pagerduty-{team}", "continue": True},
                       {"matchers": ['severity="ticket"'], "receiver": f"jira-{team}", "continue": True},
                       {"receiver": f"slack-{team}"}]}


def policy(team: str, severity: str) -> list[str]:
    if severity in ("page", "critical"):
        return sorted([f"pagerduty-{team}", f"slack-{team}", "slack-sre-fyi"])
    if severity == "ticket":
        return sorted([f"jira-{team}", f"slack-{team}"])
    return [f"slack-{team}"]


CRED_DIR = "/etc/alertmanager/secrets"


def pd_config(team: str) -> dict:
    """PagerDuty integration keys are mounted from the secret store, one file per service (R9)."""
    return {"routing_key_file": f"{CRED_DIR}/pagerduty/{team}"}


def receivers(w: World, teams: list[str]) -> list[dict]:
    d = w.domain
    out = [{"name": "slack-alerts-default", "slack_configs": [{"channel": "#alerts-default", "send_resolved": True}]},
           {"name": "slack-sre-fyi", "slack_configs": [{"channel": "#sre-fyi", "send_resolved": False}]},
           {"name": "slack-prometheus-meta", "slack_configs": [{"channel": "#prometheus-meta"}]},
           {"name": "pagerduty-sre", "pagerduty_configs": [pd_config("sre")]}]
    for t in teams:
        key = t.replace("-", "_")
        out += [{"name": f"pagerduty-{t}", "pagerduty_configs": [pd_config(t)]},
                {"name": f"slack-{t}", "slack_configs": [{"channel": f"#{t}-alerts", "send_resolved": True}]},
                {"name": f"jira-{t}", "webhook_configs": [{"url": f"https://jira-bridge.{d}/hooks/{key}"}]}]
    return out


DEAD_RECEIVERS = [("slack-old-oncall", "#oncall-old", "nothing routes here since the rota moved to PagerDuty; ask before deleting"),
                  ("slack-ops-legacy", "#ops-alerts-legacy", "kept for the platform Prometheus until its config is cleaned up"),
                  ("email-oncall", None, "unused since 2024, the SMTP relay it points at is gone")]


def drift(w: World, rng: random.Random, routes: list[dict]) -> None:
    """What years of hands-on edits leave in a shared Alertmanager (R9), all of it on routes and receivers this
    week's queue never touches: a shared team or two with their own timers, a receiver nothing routes to any more.
    Timers decide when a notification goes out, never where, so nothing graded depends on them."""
    shared = [r for r in routes if any(f'team="{t}"' in (r.get("matchers") or []) for t in w.shared_teams)]
    rng.shuffle(shared)
    tweaks = [("repeat_interval", "1h", "{t} asked for hourly repeats while something is still firing"),
              ("group_wait", "10s", None), ("group_interval", "10m", "{t}: fewer updates per group, too chatty at 5m")]
    for r, (key, val, note) in zip(shared, rng.sample(tweaks, rng.randint(1, 2))):
        t = next(t for t in w.shared_teams if f'team="{t}"' in r["matchers"])
        items = list(r.items())
        r.clear()
        for k, v in items:
            r[k] = v
            if k == "receiver":
                r[key] = val
        if note and not r.get("_comment"):
            r["_comment"] = [note.format(t=t)]
    name, chan, note = rng.choice(DEAD_RECEIVERS)
    w.notes["dead_receiver"] = name
    if chan:
        w.am_extra_receivers = [{"name": name, "slack_configs": [{"channel": chan, "send_resolved": True}], "_comment": [note]}]
    else:
        w.am_extra_receivers = [{"name": name, "email_configs": [{"to": f"oncall@{w.domain}", "send_resolved": False}],
                                 "_comment": [note]}]


def build_am(w: World, rng: random.Random) -> None:
    # the team still on `match:` is one that has been around: not the one this week's split creates
    tr = w.trigger or {}
    legacy = rng.choice([t for t in w.teams if t not in (tr.get("new"), tr.get("old"))] or w.teams)
    w.notes["legacy_match_team"] = legacy
    routes = [{"matchers": ['alertname=~"PrometheusRuleFailures|PrometheusRuleEvaluationSlow"'],
               "receiver": "slack-prometheus-meta",
               "_comment": ["rule evaluation problems. Nobody pages on these (yet)."]},
              {"matchers": ['severity=~"page|critical"'], "receiver": "slack-sre-fyi", "continue": True,
               "_comment": ["SRE sees every page as FYI; owners get paged by their own subtree below."]}]
    if w.clusters:
        routes.append({"matchers": ['alertname="ClusterUnreachable"'], "receiver": "pagerduty-platform"})
    for t in w.teams:
        r = team_route(t, legacy=(t == legacy))
        if t == legacy:
            r["_comment"] = [f"{t}: still on the old match syntax"]
        routes.append(r)
    for t in w.shared_teams:
        r = team_route(t)
        routes.append(r)
    routes[len(routes) - len(w.shared_teams)]["_comment"] = [
        "teams below route alerts from the platform and data Prometheus servers"]
    inh = [{"source_matchers": ['severity="critical"'], "target_matchers": ['severity="warning"'],
            "equal": ["alertname", "service"],
            "_comment": ["a critical copy holds back the warning copy of the same alert"]}]
    if w.clusters:
        inh.append({"source_matchers": ['alertname="ClusterUnreachable"'], "target_matchers": ['service=~".+"'],
                    "equal": ["cluster"]})
    drift(w, rng, routes)
    glob = {"resolve_timeout": "5m", "slack_api_url_file": f"{CRED_DIR}/slack-url"}
    if any("email_configs" in r for r in w.am_extra_receivers):
        glob.update({"smtp_smarthost": f"smtp.{w.domain}:587", "smtp_from": f"alertmanager@{w.domain}"})
    w.am = {"global": glob, "templates": ["/etc/alertmanager/templates/*.tmpl"],
            "route": {"receiver": "slack-alerts-default", "group_by": ["alertname", "service"], "group_wait": "30s",
                      "group_interval": "5m", "repeat_interval": "4h", "routes": routes},
            "receivers": receivers(w, sorted(set(w.teams) | set(w.shared_teams))) + w.am_extra_receivers,
            "inhibit_rules": inh}
