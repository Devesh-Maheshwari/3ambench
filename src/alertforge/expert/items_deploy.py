"""Deploy-time tickets: H05 (average of per-pod ratios pages on every canary), H07 (rate of a recorded sum
pages on every rollout), H22 (a per-pod `for` that never completes during a bad rollout), H19 (per-pod pages
fanned out by `group_by: ['...']`), H20 (a dedupe inhibition that matches itself on both sides)."""

from __future__ import annotations

import random

from . import prose_deploy as prose
from .items import Item, outcome_entry, svc_class
from .model import home_group, World, add_rules, find_route, first_rule, rules_named
from .repo import err_sel, ingress_ratio_expr, ingress_sel
from .items_svc import BLIP_BOUND, blip_err, drop_for
from .xscen import G, background, counter_from_rates, http_series, ip, pod_name, series, up_values


def _pick(w: World, rng: random.Random, pred) -> object | None:
    busy = w.notes.setdefault("busy", set())
    cands = [s for s in w.services if pred(s) and s.name not in busy]
    if not cands:
        return None
    s = rng.choice(cands)
    busy.add(s.name)
    return s


def _set_expr(files, name, expr, **kw):
    for _, _, r in rules_named(files, name):
        r["expr"] = expr
        r.update(kw)


# ---------------------------------------------------------------------------- H05

class AvgOfRatios(Item):
    code, kind, ticket_like = "H05", "outcome", True

    def setup(self, w, rng):
        s = _pick(w, rng, lambda x: x.kind == "api" and x.family == "http")
        if s is None:
            return False
        from .people import rota
        from .repo import owner_then
        self.p.update(svc=s.name, alert=f"{s.camel}HighErrorRatio", stopgap=rng.random() < 0.4,
                      person=rng.choice(rota(w, owner_then(w, s))).first)
        # the canaries the ticket is about: working days of one week, office hours, now and then an evening hotfix;
        # the incident (and the stopgap's TODO) is the last of them
        days = w.cal.week_days(rng, rng.randint(3, 5))
        hotfix = rng.random() < 0.3
        self.p["canaries"] = [(d, w.cal.office_start(rng, hotfix and k == len(days) - 1), hotfix and k == len(days) - 1)
                              for k, d in enumerate(days)]
        d, hhmm, _ = self.p["canaries"][-1]
        self.p["inc"] = w.cal.inc(d, hhmm, 11)
        self.touch = {self.p["alert"]}
        return True

    def _avg(self, s, thr):
        m, bad = err_sel(s.family)
        return (f'avg by (service) (sum by (service, pod) (rate({m}{{service="{s.name}",{bad}}}[5m]))'
                f' / sum by (service, pod) (rate({m}{{service="{s.name}"}}[5m]))) > {thr}')

    def break_(self, files, am, w):
        s = w.svc(self.p["svc"])
        thr = s.err_thr if not self.p["stopgap"] else f"{float(s.err_thr) * 4:g}"
        for _, _, r in rules_named(files, self.p["alert"]):
            r["expr"] = self._avg(s, thr)
            c = ["average the pods so one busy pod can't hide a broken one"]
            if self.p["stopgap"]:
                c.append(f"TODO({self.p['person'].lower()}) was > {s.err_thr} before {self.p['inc']}; put it back once the "
                         f"canary noise is sorted")
            r["_comment"] = c

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        T, svc = float(s.err_thr), s.name
        groups = []

        def grp(name, n, plans, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            for pods, rps, err, canary in plans:
                names = [f"{svc}-canary-{pod_name(rng, 'x')[-5:]}" if canary else pod_name(rng, svc) for _ in range(pods)]
                g.series += http_series(rng, w, svc, n, rps, err, pods, pod_names=names)
            fam(g)
            g.labelless()
            groups.append(g.done())

        n = 42
        main = lambda: [s.rps * rng.uniform(0.95, 1.05) for _ in range(n + 1)]  # noqa: E731
        grp("canary_noise", n, [(2, main(), [0.001] * (n + 1), False), (1, [0.2] * (n + 1), [0.5] * (n + 1), True)],
            lambda g: g.quiet(0, n, svc, "S_defect"))
        half = [s.rps * 0.5 * rng.uniform(0.95, 1.05) for _ in range(n + 1)]
        grp("canary_is_real", n, [(2, half, [0.001] * (n + 1), False),
                                  (1, [s.rps * 0.5 if m >= 10 else 0.2 for m in range(n + 1)],
                                   [min(0.6, 5 * T) if m >= 10 else 0.0 for m in range(n + 1)], True)],
            lambda g: g.fire(30, svc, "F_regress"))
        grp("bracket_high", n, [(3, main(), [0.2 * T if m < 20 else 1.15 * T for m in range(n + 1)], False)],
            lambda g: g.fire(40, svc, "F_regress"))
        grp("bracket_low", n, [(3, main(), [0.85 * T] * (n + 1), False)], lambda g: g.quiet(0, n, svc))
        grp("blip", n, [(3, main(), blip_err(T, n), False)], lambda g: g.quiet(0, n, svc))
        # the milder detect: a canary with fewer errors (4x the threshold); the average of per-pod ratios still pages
        grp("canary_mild", n, [(2, main(), [0.001] * (n + 1), False), (1, [0.2] * (n + 1), [min(0.5, 4 * T)] * (n + 1), True)],
            lambda g: g.quiet(0, n, svc, "S_defect"))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}), groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        m, bad = err_sel(s.family)
        wo = "pod, instance, code, method"
        e, t = f'{m}{{service="{s.name}",{bad}}}', f'{m}{{service="{s.name}"}}'
        return [("oracle", lambda f, a: None, "accept"),
                ("sum_without_pod", lambda f, a: _set_expr(f, self.p["alert"], f"sum without ({wo}) (rate({e}[5m])) / sum without ({wo}) (rate({t}[5m])) > {s.err_thr}"), "accept"),
                ("threshold_x4", lambda f, a: _set_expr(f, self.p["alert"], self._avg(s, f"{float(s.err_thr) * 4:g}")), 0.0),
                ("exclude_canary", lambda f, a: _set_expr(f, self.p["alert"], self._avg(s, s.err_thr).replace(
                    f'service="{s.name}",', f'service="{s.name}",pod!~".*canary.*",')), 0.5),
                ("demote_to_ticket", lambda f, a: [r["labels"].update(severity="ticket") for _, _, r in rules_named(f, self.p["alert"])], 0.0),
                ("for_zero", drop_for(self.touch), BLIP_BOUND),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0),
                ("ratio_at_the_ingress", lambda f, a: _set_expr(f, self.p["alert"], ingress_ratio_expr(s)), "accept")]

    def ticket(self, w, rng):
        return prose.h05(self, w, rng)


# ---------------------------------------------------------------------------- H07

class SumThenRate(Item):
    code, kind, ticket_like = "H07", "outcome", True
    RECORD = "service:http_errors:sum"

    def setup(self, w, rng):
        s = _pick(w, rng, lambda x: x.kind == "api" and x.family == "http" and x.home.startswith("legacy-"))
        if s is None:
            return False
        x = max(1, round(s.rps * rng.choice([0.03, 0.05])))
        self.p.update(svc=s.name, alert=f"{s.camel}ErrorBurst", x=x)
        self.touch = {self.p["alert"]}
        home = w.files[s.home]
        recs = next((g for g in home.groups if g["name"].endswith("-records")), None)
        if recs is None:
            from .repo import owner_then
            recs = {"name": f"{owner_then(w, s)}-records", "rules": []}
            home.groups.insert(0, recs)
        if not any(r.get("record") == self.RECORD for r in recs["rules"]):
            recs["rules"].append({"record": self.RECORD, "expr": 'sum by (service) (http_requests_total{code=~"5.."})',
                                  "_comment": ["running total for the 'errors since deploy' stat on svc-overview"]})
        grp = next(g for g in home.groups if g["name"] == s.name)
        grp["rules"].append({"alert": self.p["alert"],
                             "expr": f'sum by (service) (rate(http_requests_total{{service="{s.name}",code=~"5.."}}[5m])) > {x}',
                             "for": "10m", "labels": {"severity": "page", "team": s.team},
                             "annotations": {"summary": "{{ $labels.service }} is returning more than " + f"{x} errors/s",
                                             "runbook_url": w.runbook(self.p["alert"])}})
        return True

    def break_(self, files, am, w):
        s = w.svc(self.p["svc"])
        _set_expr(files, self.p["alert"], f'rate({self.RECORD}{{service="{s.name}"}}[5m]) > {self.p["x"]}')

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        X, svc = self.p["x"], s.name
        groups = []
        base = s.rps

        def grp(name, n, fam, err_rate, deploy_at=None):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            err = [e / base for e in err_rate]
            if deploy_at is None:
                g.series += http_series(rng, w, svc, n, [base] * (n + 1), err, s.pods)
            else:
                k = 6  # the rollout replaces six pods, one every two minutes
                for i in range(k):
                    cut = deploy_at + 2 * i
                    old = http_series(rng, w, svc, n, [base / k if m < cut else None for m in range(n + 1)], err, 1,
                                      codes_err={"500": 1.0})
                    new = http_series(rng, w, svc, n, [base / k if m >= cut else None for m in range(n + 1)], err, 1,
                                      codes_err={"500": 1.0})
                    for r in old:
                        off = rng.randint(2 * 10**4, 9 * 10**4)  # the old pods have been up for days
                        r.values = [(v + off if isinstance(v, int) else v) for v in r.values]
                        r.values[cut:] = ["stale"] + [None] * (n - cut)
                    for r in new:
                        seen = [v for v in r.values if v is not None]
                        off = seen[0] if seen else 0
                        r.values = [None if (m < cut or v is None) else v - off for m, v in enumerate(r.values)]
                    g.series += old + new
            fam(g)
            g.labelless()
            groups.append(g.done())

        n = 45
        grp("rolling_deploy", n, lambda g: g.quiet(0, n, svc, "S_defect"), [0.2 * X] * (n + 1), deploy_at=15)
        grp("real_increase", n, lambda g: g.fire(40, svc, "F_regress"), [0.2 * X if m < 20 else 3 * X for m in range(n + 1)])
        grp("bracket_high", n, lambda g: g.fire(40, svc, "F_regress"), [0.2 * X if m < 20 else 1.15 * X for m in range(n + 1)])
        grp("bracket_low", n, lambda g: g.quiet(0, n, svc), [0.85 * X] * (n + 1))
        grp("blip", n, lambda g: g.quiet(0, n, svc), blip_err(X, n))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}), groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        X = self.p["x"]
        sel = f'http_requests_total{{service="{s.name}",code=~"5.."}}'
        return [("oracle", lambda f, a: None, "accept"),
                ("increase_over_300", lambda f, a: _set_expr(f, self.p["alert"], f"sum by (service) (increase({sel}[5m])) / 300 > {X}"), "accept"),
                ("sum_without_pod", lambda f, a: _set_expr(f, self.p["alert"], f"sum without (pod, instance, code, method) (rate({sel}[5m])) > {X}"), "accept"),
                ("pristine_for_30m", lambda f, a: (self.break_(f, a, w), _set_expr(f, self.p["alert"], f'rate({self.RECORD}{{service="{s.name}"}}[5m]) > {X}', **{"for": "30m"})), 0.0),
                ("for_zero", drop_for(self.touch), BLIP_BOUND),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0),
                ("errors_at_the_ingress", lambda f, a: _set_expr(f, self.p["alert"], f"sum by (service) (rate({ingress_sel(s, True)}[5m])) > {X}"), "accept")]

    def ticket(self, w, rng):
        return prose.h07(self, w, rng)


# ---------------------------------------------------------------------------- H22

class PerPodFor(Item):
    code, kind, ticket_like = "H22", "outcome", True

    def setup(self, w, rng):
        s = _pick(w, rng, lambda x: x.kind in ("api", "worker") and x.family == "http")
        if s is None:
            return False
        self.p.update(svc=s.name, alert=f"{s.camel}HighErrorRatio", version=f"v{rng.randint(40, 90)}")
        self.touch = {self.p["alert"]}
        return True

    def break_(self, files, am, w):
        s = w.svc(self.p["svc"])
        m, bad = err_sel(s.family)
        _set_expr(files, self.p["alert"],
                  f'sum by (service, pod) (rate({m}{{service="{s.name}",{bad}}}[5m])) / sum by (service, pod) '
                  f'(rate({m}{{service="{s.name}"}}[5m])) > {s.err_thr}')

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        T, svc, ver = float(s.err_thr), s.name, self.p["version"]
        groups = []

        def pods(n, spans, err, rps_each):
            out = []
            for a, b in spans:
                rps = [rps_each if a <= m < b else None for m in range(n + 1)]
                raws = http_series(rng, w, svc, n, rps, err, 1, extra_labels={"version": ver})
                for r in raws:
                    r.values = [None if m < a else v for m, v in enumerate(r.values)]
                    if b <= n:
                        r.values[b] = "stale"
                    seen = [v for v in r.values if isinstance(v, int)]
                    off = seen[0] if seen else 0
                    r.values = [v - off if isinstance(v, int) else v for v in r.values]
                out += raws
            return out

        def grp(name, n, build, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n) + build(n)
            fam(g)
            g.labelless()
            groups.append(g.done())

        each = s.rps / 3
        gen = lambda n: [(0, 20)] * 3 + [(a, a + 6) for a in range(20, n + 1, 6) for _ in range(3)]  # noqa: E731
        grp("rollout_generations", 45, lambda n: pods(n, gen(n), [0.2 * T if m < 20 else 5 * T for m in range(n + 1)], each),
            lambda g: (g.fire(40, svc), g.quiet(0, 19, svc)))
        grp("one_bad_pod", 40, lambda n: pods(n, [(0, 99)] * 3, [0.2 * T] * (n + 1), each)
            + pods(n, [(0, 99)], [5 * T] * (n + 1), each * 0.3), lambda g: g.quiet(0, 40, svc, "S_defect"))
        grp("sustained", 40, lambda n: pods(n, [(0, 99)] * 3, [0.2 * T if m < 10 else 3 * T for m in range(n + 1)], each),
            lambda g: g.fire(30, svc, "F_regress"))
        grp("normal", 40, lambda n: pods(n, [(0, 99)] * 3, [0.3 * T] * (n + 1), each), lambda g: g.quiet(0, 40, svc))
        grp("blip", 40, lambda n: pods(n, [(0, 99)] * 3, blip_err(T, n), each), lambda g: g.quiet(0, 40, svc))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}), groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        m, bad = err_sel(s.family)
        e, t = f'{m}{{service="{s.name}",{bad}}}', f'{m}{{service="{s.name}"}}'
        return [("oracle", lambda f, a: None, "accept"),
                ("by_service_version", lambda f, a: _set_expr(f, self.p["alert"], f"sum by (service, version) (rate({e}[5m])) / sum by (service, version) (rate({t}[5m])) > {s.err_thr}"), "accept"),
                ("per_pod_for_3m", lambda f, a: (self.break_(f, a, w), [r.update({"for": "3m"}) for _, _, r in rules_named(f, self.p["alert"])]), 0.0),
                ("for_zero", drop_for(self.touch), BLIP_BOUND),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0),
                ("ratio_at_the_ingress", lambda f, a: _set_expr(f, self.p["alert"], ingress_ratio_expr(s)), "accept")]

    def ticket(self, w, rng):
        return prose.h22(self, w, rng)


# ---------------------------------------------------------------------------- H19 storm

class StormGroupBy(Item):
    """Per-pod DB client alerts for one team's services go through a subtree with `group_by: ['...']`: a
    failover sends one page per pod."""
    code, kind, ticket_like = "H19", "storm", True
    ALERT = "DbClientConnectionErrors"

    def setup(self, w, rng):
        by_team: dict[str, list] = {}
        moved = set((w.trigger or {}).get("moved", []))
        for s in w.services:
            # not a team this week's split is creating: its subtree doesn't exist yet, so neither does the storm
            if s.kind in ("api", "worker") and s.name not in w.notes.get("busy", set()) and s.name not in moved:
                by_team.setdefault(s.team, []).append(s)
        teams = [t for t, v in by_team.items() if len(v) >= 2]
        if not teams:
            return False
        t = rng.choice(teams)
        a1, a2 = rng.sample(by_team[t], 2)
        other = [s for s in w.services if s.team != t and s.kind in ("api", "worker")]
        if not other:
            return False
        b = rng.choice(other)
        self.p.update(team=t, a1=a1.name, a2=a2.name, b=b.name, pods={a1.name: rng.randint(5, 8),
                                                                        a2.name: rng.randint(4, 6), b.name: rng.randint(3, 5)})
        for s in (a1, a2, b):
            rule = {"alert": self.ALERT, "expr": f'rate(db_client_connection_errors_total{{service="{s.name}"}}[2m]) > 0.5',
                    "for": "2m", "labels": {"severity": "page", "team": s.team},
                    "annotations": {"summary": "{{ $labels.pod }} can't get database connections",
                                    "runbook_url": w.runbook(self.ALERT)}}
            grp = home_group(w, s)
            grp["rules"].append(rule)
        self.names = {self.ALERT}
        self.touch = {self.ALERT}
        self.route_touch = {t}
        return True

    def break_(self, files, am, w):
        from .model import route_for_team
        r = route_for_team(am, self.p["team"])
        r["group_by"] = ["..."]
        r["_comment"] = ["every pod on its own so we can see which ones are affected"]

    def spec(self, w, rng):
        from .repo import policy
        p = self.p
        groups = []

        def grp(name, failing, split):
            n = 26
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            for svc, k in p["pods"].items():
                for i in range(k):
                    bad = svc in failing
                    rates = [2.0 if bad and 5 <= m < 20 else 0.0 for m in range(n + 1)]
                    g.series.append(series("db_client_connection_errors_total",
                                           {"service": svc, "pod": pod_name(rng, svc), "namespace": w.namespace,
                                            "instance": f"{ip(rng)}:8080"}, counter_from_rates(rng, rates, rng.randint(0, 40))))
            g.storm(9, 21)
            g.extra["storm"] = {"failing": failing, "split": split}
            g.labelless()
            groups.append(g.done())

        grp("failover", [p["a1"], p["a2"], p["b"]], True)
        grp("one_service", [p["a1"]], False)
        svcs = {s: {"team": w.svc(s).team, "expect": policy(w.svc(s).team, "page")} for s in (p["a1"], p["a2"], p["b"])}
        return self.entry(storm={"services": svcs, "alert": self.ALERT}), groups

    def variants(self, w):
        from .model import route_for_team

        def gb(value):
            def f(files, am):
                r = route_for_team(am, self.p["team"])
                if value is None:
                    r.pop("group_by", None)
                else:
                    r["group_by"] = value
            return f

        def aggregate(files, am):
            self.break_(files, am, w)
            for _, _, r in rules_named(files, self.ALERT):
                r["expr"] = "max by (service) (" + r["expr"].split(" > ")[0] + ") > 0.5"
                r["annotations"]["summary"] = "{{ $labels.service }} pods can't get database connections"

        def child_empty(files, am):
            self.break_(files, am, w)
            route_for_team(am, self.p["team"])["routes"][0]["group_by"] = []

        return [("oracle", lambda f, a: None, "accept"),
                ("aggregate_alert_keep_route", aggregate, "accept"),
                ("explicit_alertname_service", gb(["alertname", "service"]), "accept"),
                ("group_by_alertname", gb(["alertname"]), 0.34),
                ("group_by_team", gb(["team"]), 0.34),
                ("child_group_by_empty", child_empty, 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h19(self, w, rng)


# ---------------------------------------------------------------------------- H20

class ClusterInhibit(Item):
    code, kind, ticket_like = "H20", "storm", True

    def setup(self, w, rng):
        if not w.clusters:
            return False
        cands = [s for s in w.services if s.cluster]
        if len(cands) < 5:
            return False
        rng.shuffle(cands)
        # every service runs in both clusters; `late` only moved into c1 after the packet's incident, so its page is
        # in the hidden outage and not in the PagerDuty export (a matcher copied from the export misses it)
        late = cands[-1].name
        self.p.update(other=cands[3].name, late=late, c1=w.clusters[0], c2=w.clusters[1],
                      packet=[s.name for s in w.services if s.cluster and s.name != late])
        stem = "cluster"
        from .model import RuleFileSpec
        if stem not in w.files:
            w.files[stem] = RuleFileSpec(stem, 2, [{"name": "cluster", "rules": [
                {"alert": "ClusterUnreachable", "expr": 'sum by (cluster) (up{job="federate-platform"}) == 0',
                 "for": "2m", "labels": {"severity": "page", "team": "platform"},
                 "annotations": {"summary": "can't reach the platform federation endpoint in {{ $labels.cluster }}",
                                 "runbook_url": w.runbook("ClusterUnreachable")}}]}],
                ["Cluster-level alerts. No service label on purpose; see README, Inhibition."])
        w.notes.setdefault("background_series", []).extend(
            {"service": "", "metric": "up", "labels": {"job": "federate-platform", "cluster": c,
                                                       "instance": f"prometheus.{c}.internal:9090"}, "value": 1}
            for c in w.clusters)
        self.route_touch = {"platform"}
        return True

    def break_(self, files, am, w):
        am["inhibit_rules"] = [r for r in am["inhibit_rules"] if 'alertname="ClusterUnreachable"' not in (r.get("source_matchers") or [])]
        am["inhibit_rules"].append({"source_matchers": ['alertname=~".*Down"'], "target_matchers": ['alertname=~".*Down"'],
                                    "equal": ["cluster"],
                                    "_comment": ["don't page every service when a whole cluster drops"]})

    def spec(self, w, rng):
        from .repo import policy
        p = self.p
        n, groups = 22, []

        def outage(name, skip_c1, down_c2):
            g = G(self.rid, name, n)
            for s in w.services:
                for i, c in enumerate(w.clusters * 2):
                    if c == p["c1"] and s.name in skip_c1:
                        continue
                    lab = {"service": s.name, "job": s.name, "namespace": w.namespace, "instance": f"{ip(rng)}:8080",
                           "cluster": c}
                    down = (5, 99) if (c == p["c1"] or (s.name in down_c2 and c == p["c2"])) else None
                    g.series.append(series("up", lab, up_values(n, down)))
            for c in w.clusters:
                g.series.append(series("up", {"job": "federate-platform", "cluster": c, "instance": f"prometheus.{c}.internal:9090"},
                                       up_values(n, (5, 99) if c == p["c1"] else None)))
            g.storm(10, 20)
            # `muted` is every service whose page fires in c1 in the pristine state, read off the snapshots at
            # calibration (calib.derive_storms); a service also down in c2 still pages for that
            g.extra["storm"] = {"failing": [], "muted": [], "split": False, "cluster": "pagerduty-platform",
                                "derive": {"cluster": p["c1"], "paged": sorted(down_c2)}}
            g.labelless()
            groups.append(g.done())

        outage("cluster_down", set(), {p["other"]})
        # the milder detect: c1 drops as it did in the packet, before `late` moved in and with nothing down in c2 (a
        # fix that names the packet's services, or drops `equal`, holds this one)
        outage("cluster_down_before_move", {p["late"]}, set())
        svcs = {s.name: {"team": s.team, "expect": policy(s.team, "page")} for s in w.services}
        return self.entry(storm={"services": svcs}), groups

    def variants(self, w):
        def rule(src, tgt, equal):
            def f(files, am):
                am["inhibit_rules"] = [r for r in am["inhibit_rules"] if 'alertname="ClusterUnreachable"' not in (r.get("source_matchers") or [])]
                d = {"source_matchers": src, "target_matchers": tgt}
                if equal is not None:
                    d["equal"] = equal
                am["inhibit_rules"].append(d)
            return f

        cu = ['alertname="ClusterUnreachable"']
        named = "|".join(f"{w.svc(s).camel}Down" for s in self.p["packet"])
        return [("oracle", lambda f, a: None, "accept"),
                ("target_down_alerts", rule(cu, ['alertname=~".+Down"'], ["cluster"]), "accept"),
                ("equal_service", rule(cu, ['service=~".+"'], ["service"]), 0.0),
                # holds every page in every cluster back: the other cluster's outage goes unpaged
                ("no_equal", rule(cu, ['service=~".+"'], None), 0.5),
                # S4: only the Down alerts the PagerDuty export shows (the service that moved in since still pages)
                ("packet_names_only", rule(cu, [f'alertname=~"{named}"'], ["cluster"]), 0.5),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h20(self, w, rng)
