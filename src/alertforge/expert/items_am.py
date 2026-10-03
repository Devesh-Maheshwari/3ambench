"""E3 routing and inhibition tickets: H13 (a page catch-all to the SRE pager above every team subtree), H15
(a legacy `match:` with an alternation that is a literal string), H21 (the severity inhibition on
`equal: [namespace]`), H25 (a new service's rules shipped without `team`, so they land on the root receiver)."""

from __future__ import annotations

import copy
import random

from . import prose_am as prose
from .items import Item, wpolicy
from .model import World, route_for_team, rules_named
from .repo import team_route


def _fyi(am) -> dict:
    return next(r for r in am["route"]["routes"] if r.get("receiver") == "slack-sre-fyi")


# ---------------------------------------------------------------------------- H13

class CatchAllFirst(Item):
    code, kind, ticket_like = "H13", "route", True

    def setup(self, w, rng):
        self.p.update(quarter=rng.choice(["Q1", "Q2"]))
        # only the catch-all moves: every team's subtree (own and shared) keeps its preservation cases at ticket
        # and warning; pages are checked here, for every team, because the catch-all decides where they go
        self.route_sev_touch = {"page"}
        w.notes["pd_via"] = "sre"
        return True

    def break_(self, files, am, w):
        f = _fyi(am)
        f.clear()
        f.update({"matchers": ['severity=~"page|critical"'], "receiver": "pagerduty-sre",
                  "_comment": [f"SRE covers every page while teams build their rotas ({self.p['quarter']})"]})

    def spec(self, w, rng):
        pos, neg = [], []
        for t in list(w.teams) + list(w.shared_teams):
            svc = next((s.name for s in w.services if s.team == t), f"{t}-svc")
            for sev in ("page", "critical"):
                lab = {"alertname": "SyntheticPage", "severity": sev, "team": t, "service": svc}
                pos.append({"labels": lab, "expect": wpolicy(w, t, sev)})
                neg.append({"labels": lab, "forbid": ["pagerduty-sre"]})
            for sev, name in (("ticket", "SyntheticTicket"), ("warning", "SyntheticWarning")):
                pos.append({"labels": {"alertname": name, "severity": sev, "team": t, "service": svc},
                            "expect": wpolicy(w, t, sev)})
        return self.entry(route={"pos": pos, "neg": neg}), []

    def variants(self, w):
        def split(files, am):
            routes = am["route"]["routes"]
            i = routes.index(_fyi(am))
            routes[i:i + 1] = [{"matchers": ['severity="page"'], "receiver": "slack-sre-fyi", "continue": True},
                               {"matchers": ['severity="critical"'], "receiver": "slack-sre-fyi", "continue": True}]

        def cont(files, am):
            self.break_(files, am, w)
            next(r for r in am["route"]["routes"] if r.get("receiver") == "pagerduty-sre")["continue"] = True

        def bottom(files, am):
            self.break_(files, am, w)
            routes = am["route"]["routes"]
            r = next(x for x in routes if x.get("receiver") == "pagerduty-sre")
            routes.remove(r)
            routes.append(r)

        return [("oracle", lambda f, a: None, "accept"), ("two_fyi_routes", split, "accept"),
                ("keep_sre_pager_with_continue", cont, 0.0), ("catchall_moved_to_bottom", bottom, 0.85),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h13(self, w, rng)


# ---------------------------------------------------------------------------- H15

class MatchLiteral(Item):
    code, kind, ticket_like = "H15", "route", True

    def setup(self, w, rng):
        t = w.notes.get("legacy_match_team")
        if not t or t in w.notes.get("route_busy", set()) or t in (w.trigger.get("new"), w.trigger.get("old")):
            return False
        w.notes.setdefault("route_busy", set()).add(t)
        self.p.update(team=t)
        self.route_touch = {t}
        w.notes.setdefault("pd_quiet", set()).update(s.name for s in w.services if s.team == t)
        return True

    def break_(self, files, am, w):
        r = route_for_team(am, self.p["team"])
        child = r["routes"][0]
        child.pop("match_re", None)
        child["match"] = {"severity": "page|critical"}

    def spec(self, w, rng):
        t = self.p["team"]
        svc = next((s.name for s in w.services if s.team == t), f"{t}-svc")
        pos = [{"labels": {"alertname": "Synthetic", "severity": sev, "team": t, "service": svc},
                "expect": wpolicy(w, t, sev)} for sev in ("page", "critical", "ticket", "warning")]
        neg = [{"labels": {"alertname": "Synthetic", "severity": "warning", "team": t, "service": svc},
                "forbid": [f"pagerduty-{t}"]}]
        return self.entry(route={"pos": pos, "neg": neg}), []

    def variants(self, w):
        def matchers(value):
            def f(files, am):
                child = route_for_team(am, self.p["team"])["routes"][0]
                child.pop("match_re", None)
                child.pop("match", None)
                child["matchers"] = [value]
            return f
        return [("oracle", lambda f, a: None, "accept"), ("matchers_regex", matchers('severity=~"page|critical"'), "accept"),
                ("matchers_literal", matchers('severity="page|critical"'), 0.9),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.9)]

    def ticket(self, w, rng):
        return prose.h15(self, w, rng)


# ---------------------------------------------------------------------------- H21

class SeverityInhibit(Item):
    code, kind, ticket_like = "H21", "inhibit", True

    def setup(self, w, rng):
        crit = sorted({r["alert"] for f in w.files.values() if f.gen == 1 for g in f.groups for r in g["rules"]
                       if r.get("labels", {}).get("severity") == "critical"})
        teamless = w.notes.get("teamless", set())   # H25's alerts carry no team: they never reach a team channel
        by_svc = {s.name: [a for a in crit if a.startswith(s.camel) and a[len(s.camel):] in ("LatencyHigh", "DbConnectionsHigh")]
                  for s in w.services if s.name not in teamless}
        both = [s for s, v in by_svc.items() if len(v) == 2]
        if not both:
            return False
        s1 = rng.choice(both)
        rest = [a for s, v in by_svc.items() if s != s1 for a in v]
        if not rest:
            return False
        self.p.update(alerts=[by_svc[s1][0], rng.choice(rest), by_svc[s1][1]])
        # `equal: [alertname, service]`: setting an alert's own service statically is in scope for this ticket
        self.label_touch = {a: s.name for a in self.p["alerts"] for s in w.services if a.startswith(s.camel)
                            and a[len(s.camel):] in ("LatencyHigh", "DbConnectionsHigh")}
        return True

    @staticmethod
    def _is(r):
        return (r.get("source_matchers") == ['severity="critical"'] and r.get("target_matchers") == ['severity="warning"'])

    def break_(self, files, am, w):
        r = next(x for x in am["inhibit_rules"] if self._is(x))
        r["equal"] = ["namespace"]
        r["_comment"] = ["a critical anywhere in the namespace makes the warnings noise"]

    def spec(self, w, rng):
        ns = w.namespace
        svc_of = {}
        for s in w.services:
            for a in self.p["alerts"]:
                if a.startswith(s.camel):
                    svc_of[a] = s
        a1, a2, other = self.p["alerts"]
        s1, s2 = svc_of[a1], svc_of[a2]

        def al(name, s, sev):
            return {"alertname": name, "service": s.name, "severity": sev, "team": s.team, "namespace": ns}

        cases = [{"source": al(a1, s1, "critical"), "target": al(a1, s1, "warning"), "expect": True},
                 {"source": al(a2, s2, "critical"), "target": al(a2, s2, "warning"), "expect": True},
                 {"source": al(a1, s1, "critical"), "target": al(a2, s2, "warning"), "expect": False},
                 {"source": al(a2, s2, "critical"), "target": al(a1, s1, "warning"), "expect": False}]
        if other:
            cases.append({"source": al(a1, s1, "critical"), "target": al(other, s1, "warning"), "expect": False})
        return self.entry(inhibit={"cases": cases}), []

    def variants(self, w):
        def eq(v):
            def f(files, am):
                r = next(x for x in am["inhibit_rules"] if self._is(x))
                r["equal"] = v
            return f
        return [("oracle", lambda f, a: None, "accept"), ("with_namespace_too", eq(["alertname", "service", "namespace"]), "accept"),
                ("equal_service_only", eq(["service"]), 0.9),
                ("removed", lambda f, a: a.update(inhibit_rules=[x for x in a["inhibit_rules"] if not self._is(x)]), 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h21(self, w, rng)


# ---------------------------------------------------------------------------- H25

class MissingTeam(Item):
    code, kind, ticket_like = "H25", "route", True

    def setup(self, w, rng):
        busy = w.notes.setdefault("busy", set())
        cands = [s for s in w.services if s.kind == "api" and s.name not in busy
                 and s.team not in (w.trigger.get("new"), w.trigger.get("old"))]
        if not cands:
            return False
        s = rng.choice(cands)
        busy.add(s.name)
        w.notes.setdefault("pd_quiet", set()).add(s.name)
        w.notes.setdefault("teamless", set()).add(s.name)
        self.p.update(svc=s.name, team=s.team)
        names = sorted({r["alert"] for f in w.files.values() for g in f.groups for r in g["rules"]
                        if "alert" in r and f'service="{s.name}"' in r.get("expr", "") and r.get("labels", {}).get("team") == s.team})
        self.p["alerts"] = names
        self.touch = set(names)
        self.names = set(names)
        return bool(names)

    def break_(self, files, am, w):
        for n in self.p["alerts"]:
            for _, _, r in rules_named(files, n):
                r["labels"].pop("team", None)

    def spec(self, w, rng):
        t, s = self.p["team"], self.p["svc"]
        chain = [{"alert": n, "service": s, "expect": {"page": wpolicy(w, t, "page"), "critical": wpolicy(w, t, "page"),
                                                       "ticket": wpolicy(w, t, "ticket"), "warning": wpolicy(w, t, "warning")}}
                 for n in self.p["alerts"]]
        other = next(x.name for x in w.services if x.name != s)
        neg = [{"labels": {"alertname": "SomethingDown", "severity": "page", "service": other}, "forbid": [f"pagerduty-{t}", "pagerduty-sre"]}]
        return self.entry(route={"pos": [], "chain": chain, "neg": neg}), []

    def variants(self, w):
        t, s = self.p["team"], self.p["svc"]

        def by_service(files, am):
            self.break_(files, am, w)
            r = copy.deepcopy(route_for_team(am, t))
            r.pop("match", None)
            r.pop("_comment", None)
            r["matchers"] = [f'service="{s}"']
            idx = next(i for i, x in enumerate(am["route"]["routes"]) if x.get("matchers") == [f'team="{t}"']
                       or (x.get("match") or {}).get("team") == t)
            am["route"]["routes"].insert(idx, r)

        def root_to_owner(files, am):
            self.break_(files, am, w)
            am["route"]["receiver"] = f"pagerduty-{t}"

        return [("oracle", lambda f, a: None, "accept"), ("route_by_service", by_service, "accept"),
                ("root_pages_owner", root_to_owner, 0.5), ("pristine", lambda f, a: self.break_(f, a, w), 0.5)]

    def ticket(self, w, rng):
        return prose.h25(self, w, rng)
