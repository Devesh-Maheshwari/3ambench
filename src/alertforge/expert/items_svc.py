"""Service-alert tickets: H01 (regex absent over two services), H03 (join that breaks when a second 5xx code
appears), H04 (traffic into the void), H06 (value in labels keeps an alert pending)."""

from __future__ import annotations

import copy
import random

from . import prose_svc as prose
from .items import Item, outcome_entry, svc_class
from .model import home_group, World, add_rules, first_rule, replace_rule, rules_named
from .repo import down_rule, err_sel, error_ratio_rule, ingress_ratio_expr
from .xscen import G, background, http_series, series, up_values


def _targets(rng, w, svc, n, down=None, vanish=None, back=None, k=3):
    from .xscen import ip
    s = w.svc(svc)
    out = []
    for i in range(k):
        lab = {"service": svc, "job": svc, "namespace": w.namespace, "instance": f"{ip(rng)}:8080"}
        if s.cluster:
            lab["cluster"] = w.clusters[0]
        d = down[i] if isinstance(down, list) else down
        out.append(series("up", lab, up_values(n, d, vanish, back)))
    return out


# Error-ratio blip (RT-4): two minutes at 2.6x the threshold on a 0.2x baseline. A 5m window over 1m samples
# holds four increments: with both bad minutes in it the ratio is 1.4x, with one it is 0.8x, so the condition
# is true on three evaluations. README's `for: 10m` (anything from 3m up) stays silent; no `for` pages.
BLIP_AT = 20
BLIP_BOUND = 0.85


def blip_err(T: float, n: int, base: float = 0.2) -> list[float]:
    return [2.6 * T if BLIP_AT <= m < BLIP_AT + 2 else base * T for m in range(n + 1)]


def drop_for(names):
    """The fixed rules with their `for` removed (the A-flap-for0 shape)."""
    def f(files, am):
        for n in names:
            for _, _, r in rules_named(files, n):
                r.pop("for", None)
    return f


# ---------------------------------------------------------------------------- H01

class RegexAbsent(Item):
    """One legacy Down rule covers two services with `absent(up{service=~"a|b"})`: it never fires when only
    one of them leaves discovery (sp1)."""
    code, kind, ticket_like = "H01", "outcome", True

    @staticmethod
    def prepare(w: World, rng: random.Random) -> tuple[str, str] | None:
        by_prefix: dict[str, list] = {}
        for s in w.services:
            by_prefix.setdefault(s.name.split("-")[0], []).append(s)
        pairs = [v for v in by_prefix.values() if len(v) == 2 and v[0].team == v[1].team]
        if pairs:
            a, b = rng.choice(pairs)
            return a.name, b.name
        return None

    def setup(self, w, rng):
        pair = self.prepare(w, rng)
        if pair is None:
            return False
        a, b = pair
        self.p.update(a=a, b=b, prefix=a.split("-")[0], team=w.svc(a).team)
        self.p["legacy"] = f"{self.p['prefix'].capitalize()}Down"
        # FA-1: in the pristine repo both services' Down alert is this legacy rule; a fix that keeps its name is
        # accepted here, so the other items have to accept that name as "the service's Down alert" too
        legacy = w.notes.setdefault("legacy_down", {})
        legacy[a] = legacy[b] = self.p["legacy"]
        self.names = {f"{w.svc(a).camel}Down", f"{w.svc(b).camel}Down"}
        self.touch = self.names | {self.p["legacy"]}
        self.p["ops"] = f"{w.company.key}-{rng.randint(1500, 2400)}"
        return True

    def break_(self, files, am, w):
        a, b, pre = self.p["a"], self.p["b"], self.p["prefix"]
        sa = w.svc(a)
        rule = first_rule(files, f"{sa.camel}Down")
        legacy = {"alert": self.p["legacy"],
                  "expr": f'absent(up{{service=~"{a}|{b}"}}) or sum by (service) (up{{service=~"{pre}-.*"}}) == 0',
                  "for": rule.get("for", "3m"), "labels": dict(rule["labels"]),
                  "annotations": {"summary": f"{pre} has no healthy scrape targets",
                                  "runbook_url": w.runbook(self.p["legacy"])},
                  "_comment": [f"one rule for both {pre} services, see {self.p['ops']}"]}
        replace_rule(files, f"{sa.camel}Down", [legacy])
        replace_rule(files, f"{w.svc(b).camel}Down", [])

    def spec(self, w, rng):
        a, b, n, tv = self.p["a"], self.p["b"], 30, 20
        groups = []

        def grp(name, skip, fam, build):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n, skip) + build()
            fam(g)
            g.labelless()
            groups.append(g.done())

        grp("vanish_one", {a}, lambda g: (g.fire(tv + 10, a), g.quiet(0, tv + 1, a)),
            lambda: _targets(rng, w, a, n, vanish=tv))
        grp("vanish_other", {b}, lambda g: (g.fire(tv + 10, b), g.quiet(0, tv + 1, b)),
            lambda: _targets(rng, w, b, n, vanish=tv))
        grp("vanish_both", {a, b}, lambda g: (g.fire(tv + 10, a), g.fire(tv + 10, b)),
            lambda: _targets(rng, w, a, n, vanish=tv) + _targets(rng, w, b, n, vanish=tv))
        grp("flap_2m", {a}, lambda g: g.quiet(0, n, a), lambda: _targets(rng, w, a, n, vanish=tv, back=tv + 2))
        grp("partial", {a}, lambda g: g.quiet(0, n, a), lambda: _targets(rng, w, a, n, down=[(10, 99), None, None]))
        grp("all_up_zero", {a}, lambda g: (g.fire(20, a, "F_regress"), g.quiet(0, 11, a)),
            lambda: _targets(rng, w, a, n, down=(10, 99)))
        e = outcome_entry(self, w, {a: svc_class(w, a), b: svc_class(w, b)})
        return e, groups

    def variants(self, w):
        a, b = w.svc(self.p["a"]), w.svc(self.p["b"])

        def per_service(expr_fn, for_):
            def f(files, am):
                for s in (a, b):
                    for _, _, r in rules_named(files, f"{s.camel}Down"):
                        r["expr"], r["for"] = expr_fn(s.name), for_
            return f

        def keep_legacy(files, am):
            legacy = copy.deepcopy(files)
            self.break_(legacy, am, w)
            rule = first_rule(legacy, self.p["legacy"])
            home = next(f for f in files.values() for g in f.groups for r in g["rules"] if r.get("alert") == f"{a.camel}Down")
            home.groups[0]["rules"].append(rule)

        def pristine(files, am):
            self.break_(files, am, w)

        def static_label(files, am):
            self.break_(files, am, w)
            for _, _, r in rules_named(files, self.p["legacy"]):
                r["labels"]["service"] = a.name

        return [
            ("oracle", lambda f, m: None, "accept"),
            ("absent_over_time_5m_for_2m", per_service(
                lambda s: f'absent_over_time(up{{service="{s}"}}[5m]) or sum by (service) (up{{service="{s}"}}) == 0', "2m"),
             "accept"),
            ("absent_max_for_10m", per_service(
                lambda s: f'absent(up{{service="{s}"}}) or max by (service) (up{{service="{s}"}}) == 0', "10m"), "accept"),
            ("pristine_regex", pristine, 0.0),
            ("regex_plus_static_service", static_label, 0.3),
            ("new_rules_next_to_regex", keep_legacy, 0.7),
            ("for_1m", per_service(lambda s: f'sum by (service) (up{{service="{s}"}}) == 0 or absent(up{{service="{s}"}})',
                                   "1m"), 0.5),
        ]

    def ticket(self, w, rng):
        return prose.h01(self, w, rng)


# ---------------------------------------------------------------------------- H03

class ManyToOne(Item):
    """A service error-ratio alert joins `by (service, code)` to `by (service)` with `on (service)`. With one
    5xx code in history it works; the outage adds a second code and the rule fails to evaluate (sp2, sp14)."""
    code, kind, ticket_like = "H03", "outcome", True

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind == "api" and s.family == "http" and s.name not in w.notes.get("busy", set())]
        if not cands:
            return False
        s = rng.choice(cands)
        w.notes.setdefault("busy", set()).add(s.name)
        self.p.update(svc=s.name, alert=f"{s.camel}HighErrorRatio", stopgap=rng.random() < 0.6)
        self.touch = {self.p["alert"]}
        # whoever put the stopgap in was on call for it that night: someone on the rota the page should have reached
        from .people import rota
        from .repo import owner_then
        self.p["person"] = rng.choice(rota(w, owner_then(w, s))).first
        self.p["times"] = prose.h03_times(w, rng, self.p["stopgap"])
        self.p["inc"] = self.p["times"]["inc"]
        return True

    def broken_expr(self, s, window):
        return self._broken_expr(s, window)

    def _broken_expr(self, s, window):
        m, bad = err_sel(s.family)
        return (f'sum by (service, code) (rate({m}{{service="{s.name}",{bad}}}[{window}]))'
                f' / on (service) sum by (service) (rate({m}{{service="{s.name}"}}[{window}])) > {s.err_thr}')

    def break_(self, files, am, w):
        s = w.svc(self.p["svc"])
        for _, _, r in rules_named(files, self.p["alert"]):
            r["expr"] = self._broken_expr(s, "15m" if self.p["stopgap"] else "5m")
            r["annotations"]["summary"] = "{{ $labels.service }} is returning {{ $labels.code }}s ({{ $value | humanizePercentage }})"
            c = ["keep code on the alert so the Slack message says which 5xx it is"]
            if self.p["stopgap"]:
                c.append(f"TODO({self.p['person'].lower()}) revert, {self.p['inc']} stopgap, {self.p['times']['stop_hm']}")
            r["_comment"] = c

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        T, svc = float(s.err_thr), s.name
        groups = []

        def grp(name, n, err, codes, fam):
            g = G(self.rid, name, n)
            rps = [s.rps * rng.uniform(0.93, 1.07) for _ in range(n + 1)]
            g.series += background(w, rng, n) + http_series(rng, w, svc, n, rps, err, s.pods, codes)
            fam(g)
            g.labelless()
            groups.append(g.done())

        n = 52
        grp("second_code", n, [0.2 * T if m < 30 else 3 * T for m in range(n + 1)],
            {"500": [1.0 if m < 30 else 0.3 for m in range(n + 1)], "503": [0.0 if m < 30 else 0.7 for m in range(n + 1)]},
            lambda g: (g.fire(50, svc), g.quiet(0, 29, svc)))
        grp("split_codes", 32, [0.2 * T if m < 10 else 1.35 * T for m in range(33)],
            {"500": [1.0 if m < 10 else 0.5 for m in range(33)], "503": [0.0 if m < 10 else 0.5 for m in range(33)]},
            lambda g: (g.fire(30, svc), g.quiet(0, 9, svc)))
        grp("bracket_high", 42, [0.2 * T if m < 20 else 1.15 * T for m in range(43)], {"500": 1.0},
            lambda g: g.fire(40, svc, "F_regress"))
        grp("bracket_low", 40, [0.85 * T] * 41, {"500": 1.0}, lambda g: g.quiet(0, 40, svc))
        grp("normal", 40, [rng.uniform(0.2, 0.5) * T] * 41, {"500": 1.0}, lambda g: g.quiet(0, 40, svc))
        grp("blip", 40, blip_err(T, 40), {"500": 1.0}, lambda g: g.quiet(0, 40, svc))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}), groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        m, bad = err_sel(s.family)
        sel_e, sel_t = f'{m}{{service="{s.name}",{bad}}}', f'{m}{{service="{s.name}"}}'

        def expr(e, window="5m"):
            def f(files, am):
                for _, _, r in rules_named(files, self.p["alert"]):
                    r["expr"] = e.replace("WIN", window)
            return f

        wo = "code, instance, pod, method"
        v = [("oracle", lambda f, a: None, "accept"),
             ("sum_without_code", expr(f"sum without ({wo}) (rate({sel_e}[WIN])) / sum without ({wo}) (rate({sel_t}[WIN])) > {s.err_thr}"), "accept"),
             ("ignoring_code", expr(f"sum by (service) (rate({sel_e}[WIN])) / ignoring (code) sum by (service) (rate({sel_t}[WIN])) > {s.err_thr}"), "accept"),
             ("group_left", expr(f"sum by (service, code) (rate({sel_e}[WIN])) / on (service) group_left sum by (service) (rate({sel_t}[WIN])) > {s.err_thr}"), 0.5),
             ("literal_ignoring", expr(f"sum by (service, code) (rate({sel_e}[WIN])) / ignoring (code) sum by (service) (rate({sel_t}[WIN])) > {s.err_thr}"), 0.0),
             ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]
        v.append(("join_fixed_window_15m", expr(error_ratio_rule(w, s, "15m")["expr"]), 0.0))
        v.append(("for_zero", drop_for(self.touch), BLIP_BOUND))
        v.append(("ratio_at_the_ingress", expr(ingress_ratio_expr(s)), "accept"))   # the LB view (README: scraped)
        return v

    def ticket(self, w, rng):
        return prose.h03(self, w, rng)


# ---------------------------------------------------------------------------- H04

class TrafficVoid(Item):
    """Nothing pages when traffic stops while the pods stay up: ratios turn NaN and resolve. The catalog
    already has traffic floors (a postmortem action item that was done); the page that reads them was not."""
    code, kind, ticket_like = "H04", "outcome", True

    @staticmethod
    def prepare(w, rng):
        cands = [s for s in w.services if s.kind == "api" and s.family == "http" and s.name not in w.notes.get("busy", set())]
        if not cands:
            return None
        s = rng.choice(cands)
        s.rps = rng.choice([40, 50, 60, 75])
        s.floor = float(rng.choice([3, 4, 5, 6]))
        w.notes.setdefault("busy", set()).add(s.name)
        return s.name

    def setup(self, w, rng):
        s = next((x for x in w.services if x.floor is not None and x.name not in w.notes.get("floors_taken", set())), None)
        if s is None:
            return False
        w.notes.setdefault("floors_taken", set()).add(s.name)
        self.p.update(svc=s.name, alert=f"{s.camel}TrafficBelowFloor", floor=s.floor)
        self.touch = {self.p["alert"], f"{s.camel}HighErrorRatio"}
        f = "g" if s.floor != int(s.floor) else "d"
        rule = {"alert": self.p["alert"],
                "expr": (f'sum by (service) (rate(http_requests_total{{service="{s.name}"}}[5m])) < {int(s.floor) if f == "d" else s.floor}'
                         f' and on (service) sum by (service) (up{{service="{s.name}"}}) > 0'),
                "for": "5m", "labels": {"severity": "page", "team": s.team},
                "annotations": {"summary": "{{ $labels.service }} is serving under its traffic floor with its pods up",
                                "runbook_url": w.runbook(self.p["alert"])}}
        grp = next(g for g in w.files[s.home].groups if any(r.get("alert") == f"{s.camel}HighErrorRatio" for r in g["rules"]))
        add_rules(w.files, s.home, grp["name"], [rule], after=f"{s.camel}HighErrorRatio")
        return True

    def break_(self, files, am, w):
        replace_rule(files, self.p["alert"], [])

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        F_, svc = s.floor, s.name
        groups = []

        def grp(name, n, rps, fam):
            g = G(self.rid, name, n)
            err = [0.002] * (n + 1)
            g.series += background(w, rng, n) + http_series(rng, w, svc, n, rps, err, s.pods)
            fam(g)
            g.labelless()
            groups.append(g.done())

        n = 48
        norm = lambda: s.rps * rng.uniform(0.9, 1.1)  # noqa: E731
        # silent windows start after WARM: a `rate()` over a window that reaches back before the first sample
        # reads low (extrapolation stops half an interval past the data), which real servers never see
        warm = 20
        # the sample at 29 is the last one with requests in it: the postmortem's 15 minutes run from there
        grp("void", n, [norm() if m < 30 else 0.0 for m in range(n + 1)], lambda g: (g.fire(44, svc), g.quiet(warm, 29, svc)))
        # the milder detect: traffic under the floor, not gone (the postmortem's floor, not "zero", is the line)
        grp("under_floor", n, [norm() if m < 30 else 0.4 * F_ for m in range(n + 1)], lambda g: g.fire(44, svc))
        grp("night_trough", 70, [1.5 * F_ * rng.uniform(0.97, 1.03) for _ in range(71)], lambda g: g.quiet(warm, 70, svc))
        grp("restart_dip", 50, [0.0 if m == 30 else norm() for m in range(51)], lambda g: g.quiet(warm, 50, svc))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}), groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        fl = int(s.floor) if s.floor == int(s.floor) else s.floor
        rate = lambda win: f'sum by (service) (rate(http_requests_total{{service="{s.name}"}}[{win}]))'  # noqa: E731

        def floor_rule(expr, for_):
            def f(files, am):
                for _, _, r in rules_named(files, self.p["alert"]):
                    r["expr"] = expr
                    r["for"] = for_
            return f

        def or_into_ratio(files, am):
            replace_rule(files, self.p["alert"], [])
            for _, _, r in rules_named(files, f"{s.camel}HighErrorRatio"):
                r["expr"] = f"({r['expr']}) or ({rate('2m')} < {fl})"

        # Floors written to survive a request counter that isn't there at all (fresh pods that never served):
        # missing traffic reads as none. Correct readings of the action item; every scenario has request series
        # wherever pods are up and serving (scraped.py), so these stay quiet outside the outages, like the oracle.
        up = f'sum by (service) (up{{service="{s.name}"}})'
        raw = f'http_requests_total{{service="{s.name}"}}'
        tolerant = [("or_up_times_zero", f"({rate('5m')} or {up} * 0) < {fl} and on (service) {up} > 0"),
                  ("absent_counter", f"({rate('5m')} < {fl} or absent({raw})) and on (service) {up} > 0"),
                  ("unless_at_floor", f"{up} > 0 unless on (service) {rate('5m')} >= {fl}"),
                  ("or_labelled_vector_zero", f'({rate("5m")} or label_replace(vector(0), "service", "{s.name}", "", ""))'
                                              f" < {fl} and on (service) {up} > 0")]
        # appended after the existing list: the partial policy's wrong-variant draw and the alt solver's pick
        # (the second accepted variant) stay what they were
        return [("oracle", lambda f, a: None, "accept"),
                ("window_10m_for_5m", floor_rule(f"{rate('10m')} < {fl}", "5m"), "accept"),
                ("or_into_ratio_alert", or_into_ratio, "accept"),
                ("window_15m_for_5m", floor_rule(f"{rate('15m')} < {fl}", "5m"), 0.0),
                ("floor_from_packet", floor_rule(f"{rate('5m')} < {round(s.rps * 0.5)}", "5m"), 0.7),
                ("equals_zero", floor_rule(f"{rate('5m')} == 0", "5m"), 0.5),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0),
                *[(name, floor_rule(expr, "5m"), "accept") for name, expr in tolerant]]

    def ticket(self, w, rng):
        from .prose_pm import h04
        return h04(self, w, rng)


# ---------------------------------------------------------------------------- H06

class ValueInLabel(Item):
    """`labels: {current: "{{ $value }}"}` on an alert with `for`: every evaluation is a new series, so a
    steadily rising condition stays pending forever (p11)."""
    code, kind, ticket_like = "H06", "outcome", True
    ALERT = "ReplicationLagHigh"

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind in ("api", "worker") and s.home.startswith("legacy-")
                 and s.name not in w.notes.get("busy", set())]
        if not cands or any(r.get("alert") == self.ALERT for f in w.files.values() for g in f.groups for r in g["rules"]):
            return False
        s = rng.choice(cands)
        w.notes.setdefault("busy", set()).add(s.name)
        self.p.update(svc=s.name, thr=30, for_=10)
        self.names = self.touch = {self.ALERT}
        rule = {"alert": self.ALERT, "expr": f'max by (service) (pg_replication_lag_seconds{{service="{s.name}"}}) > 30',
                "for": "10m", "labels": {"severity": "page", "team": s.team},
                "annotations": {"summary": "{{ $labels.service }} replica is behind the primary",
                                "description": "Replication lag is {{ $value | humanize }}s.",
                                "runbook_url": w.runbook(self.ALERT)}}
        grp = home_group(w, s)
        grp["rules"].append(rule)
        w.runbooks[self.ALERT] = {"file": "replication-lag.md", "alerts": [self.ALERT],
                                 "card": "H06", "body": lambda: prose.replication_runbook(s, w)}
        return True

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.ALERT):
            r["labels"]["current"] = "{{ $value | humanize }}s"
            r["annotations"].pop("description", None)

    def _lag_labels(self, w) -> dict:
        return {"service": self.p["svc"], "instance": "10.40.3.12:9187", "job": "postgres-exporter",
                "namespace": w.namespace}

    def exporters(self, w):
        # the replica's exporter is scraped whatever the app's pods do: a healthy few seconds of lag elsewhere
        return [{"service": self.p["svc"], "metric": "pg_replication_lag_seconds", "labels": self._lag_labels(w),
                 "key": ("service",), "values": ("ints", 1, 4)}]

    def spec(self, w, rng):
        svc, groups = self.p["svc"], []

        def grp(name, vals, fam):
            n = len(vals) - 1
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            g.series.append(series("pg_replication_lag_seconds", self._lag_labels(w), vals))
            fam(g)
            g.labelless()
            groups.append(g.done())

        start = rng.randint(1, 3)
        rising = [start + 3 * m for m in range(61)]
        cross = next(m for m, v in enumerate(rising) if v > 30)
        grp("rising", rising, lambda g: (g.fire(cross + 15, svc), g.quiet(0, cross + 8, svc)))
        grp("plateau", [rng.randint(1, 3) if m < 10 else 120 for m in range(41)], lambda g: g.fire(25, svc, "F_regress"))
        grp("spike", [rng.randint(1, 4) if not 15 <= m < 21 else 90 for m in range(41)], lambda g: g.quiet(0, 40, svc))
        grp("normal", [rng.randint(1, 5) for _ in range(41)], lambda g: g.quiet(0, 40, svc))
        # the milder detect: a replica creeping past the threshold a few hundredths of a second a minute. The value
        # in the label still changes every evaluation; a label rounded to whole seconds holds still long enough
        creep = [round(29.0 + 0.03 * m, 2) for m in range(61)]
        c2 = next(m for m, v in enumerate(creep) if v > 30)
        grp("slow_creep", creep, lambda g: (g.fire(c2 + 15, svc), g.quiet(0, c2 + 8, svc)))
        return outcome_entry(self, w, {svc: svc_class(w, svc)}, named=self.ALERT), groups

    def variants(self, w):
        def mod(fn):
            def f(files, am):
                for _, _, r in rules_named(files, self.ALERT):
                    fn(r)
            return f

        def printf(r):
            r["labels"]["current"] = '{{ $value | printf "%.0f" }}'

        def for0(r):
            r["labels"]["current"] = "{{ $value | humanize }}s"
            r.pop("for", None)

        return [("oracle", lambda f, a: None, "accept"),
                ("dropped_value", mod(lambda r: r["annotations"].pop("description", None)), "accept"),
                ("printf_rounding", mod(printf), 0.5),
                ("for_zero", mod(for0), 0.5),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h06(self, w, rng)
