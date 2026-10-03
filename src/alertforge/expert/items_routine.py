"""Routine queue items: R01 (burn alerts from the ADR arithmetic), R06 (inhibition with a Down source),
R03 (reorg label moves derived from ownership.yaml), R04 (a new team subtree, optionally behind a legacy
service-regex route: H17), R05 (a receiver retirement)."""

from __future__ import annotations

import random
from fractions import Fraction as Fr

from . import prose_routine as prose
from .items import Item, wpolicy
from .model import World, add_rules, find_route, route_for_team, rules_named
from .repo import FACTORS, burn_rules, policy, team_route
from .xscen import G, background


# ---------------------------------------------------------------------------- R01

class BurnNew(Item):
    """Fast or slow burn alert for a service whose catalog window is 7 days (factors 3.36 / 1.4)."""
    code, kind = "R01", "new_alert"

    def __init__(self, rid, tier="fast"):
        super().__init__(rid)
        self.tier = tier

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind == "api" and s.family == "http" and s.window_d == 7]
        if not cands:
            return False
        s = cands[0]
        self.p.update(svc=s.name)
        fast, slow = burn_rules(w, s)
        self.rule = fast if self.tier == "fast" else slow
        self.names = {self.rule["alert"]}
        self.touch = set(self.names)
        w.notes.setdefault("burn_new", set()).add((s.name, self.tier))   # the queue starts without this alert
        if any(True for _ in rules_named(w.files, self.rule["alert"])):
            return True  # the repo already has it in the fixed state; the queue starts without it
        stem = f"slo-{s.name}"
        from .model import RuleFileSpec
        if stem not in w.files:
            w.files[stem] = RuleFileSpec(stem, 2, [{"name": stem, "rules": []}])
        w.files[stem].groups[0]["rules"].append(self.rule)
        return True

    def break_(self, files, am, w):
        from .model import drop_rules
        drop_rules(files, {self.rule["alert"]})

    def spec(self, w, rng):
        from ..alerts import AlertSpec
        from ..scenarios import build_alert_scenarios
        s = w.svc(self.p["svc"])
        kind = "burn_page" if self.tier == "fast" else "burn_ticket"
        fast, slow = FACTORS[s.window_d]
        a = AlertSpec(self.rid, kind, self.rule["alert"], s.name, s.family, dict(self.rule["labels"]),
                      self.rule["annotations"]["runbook_url"],
                      {"target": s.target, "slo_le": "0.5", "factor": fast if self.tier == "fast" else slow})
        other = next(x.name for x in w.services if x.name != s.name)
        built = build_alert_scenarios(a, random.Random(rng.random()), s.name, other, w.namespace)
        groups = []
        for b in built:
            g = G(self.rid, b.name, b.scen.minutes)
            from .xscen import Raw
            g.series += [Raw(x.metric, x.labels, x.values) for x in b.scen.series]
            g.series += background(w, rng, b.scen.minutes)
            for p in b.probes:
                op = "!=" if p.other else "="
                expr = (f'count(ALERTS{{alertname="{a.name}",alertstate="firing",service{op}"{p.service}"}}) or vector(0)')
                g.count(expr, p.t, 1 if (p.intent == "fire" and not p.other) else 0, p.family)
            if b.fire_point:
                g.probes.append({"type": "snap", "id": f"{g.name}.dyn", "t": b.fire_point[0], "family": "dyn",
                                 "svc": b.fire_point[1]})
            groups.append(g.done())
        e = self.entry(alert={"name": a.name, "labels": a.labels, "runbook": a.runbook, "expected_count": 1,
                              "service": s.name})
        return e, groups

    def variants(self, w):
        s = w.svc(self.p["svc"])
        name = self.rule["alert"]
        k30 = FACTORS[30][0 if self.tier == "fast" else 1]

        def factor(k):
            def f(files, am):
                for _, _, r in rules_named(files, name):
                    r["expr"] = self.rule["expr"].replace(f"({FACTORS[7][0 if self.tier == 'fast' else 1]} *", f"({k} *")
            return f

        b = 1 - Fr(s.target)
        k7 = FACTORS[7][0 if self.tier == "fast" else 1]
        thr = f"{float(Fr(k7) * b):.10f}".rstrip("0")

        def precomputed(files, am):
            for _, _, r in rules_named(files, name):
                r["expr"] = r["expr"].replace(f"({k7} * {f'{1 - float(s.target):.10f}'.rstrip('0')})", thr)

        def written_out(files, am):
            for _, _, r in rules_named(files, name):
                r["annotations"]["summary"] = f"{s.name} is burning its error budget ({self.tier} burn)"
                r["labels"]["service"] = s.name

        return [("oracle", lambda f, a: None, "accept"), ("precomputed_threshold", precomputed, "accept"),
                ("service_written_out", written_out, "accept"),
                ("thirty_day_factor", factor(k30), 0.5),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r01(self, w, rng)


# ---------------------------------------------------------------------------- R06

def _down_names(w, svc: str) -> list[str]:
    """Every name the text lets a service's Down alert have: the README name, and the pristine legacy rule's name
    where one rule covers the service (H01 accepts a fix that keeps that name)."""
    names = [f"{w.svc(svc).camel}Down"]
    legacy = w.notes.get("legacy_down", {}).get(svc)
    return names + ([legacy] if legacy and legacy not in names else [])


def _with_alts(case: dict, w) -> dict:
    """FA-1: the same case once per accepted Down name (source and target). The grader passes an expect-true
    case when any spelling is held back, and an expect-false case only when none is."""
    def spellings(a: dict) -> list[dict]:
        svc = a.get("service")
        if not svc or a.get("alertname") != f"{w.svc(svc).camel}Down":
            return [a]
        return [{**a, "alertname": n} for n in _down_names(w, svc)]
    alts = [{**case, "source": s, "target": t} for s in spellings(case["source"]) for t in spellings(case["target"])]
    if len(alts) > 1:
        case = {**case, "alts": [{"source": a["source"], "target": a["target"]} for a in alts[1:]]}
    return case


class InhibitDown(Item):
    code, kind = "R06", "inhibit"

    def setup(self, w, rng):
        taken = w.notes.setdefault("inhibit_sources", set())
        cands = [s for s in w.services if s.kind == "api" and s.name not in taken
                 and any(r.get("alert") == f"{s.camel}ErrorBudgetBurnFast" for f in w.files.values() for g in f.groups for r in g["rules"])]
        # FA-5: the ticket is about pages that reach the owner; a service whose pages reach no pager in the starting
        # repo (no team on its alerts, a routing block that never pages) is another ticket's story
        quiet = set(w.notes.get("pd_quiet", set())) | set(w.notes.get("teamless", set()))
        cands = [s for s in cands if s.name not in quiet] or cands
        pref = [s for s in cands if s.name in w.notes.get("prefer_inhibit", set())]
        if not cands:
            return False
        s = (pref or cands)[0] if pref else rng.choice(cands)
        taken.add(s.name)
        targets = sorted({f"{s.camel}ErrorBudgetBurnFast", f"{s.camel}ErrorBudgetBurnSlow", f"{s.camel}HighErrorRatio"})
        self.p.update(svc=s.name, source=f"{s.camel}Down", targets=targets)
        self.names = {self.p["source"], *targets}
        # `equal: [service]` reads the service on both sides: setting it statically on these alerts is in scope
        self.label_touch = {a: s.name for a in self.names}
        self.rule = {"source_matchers": [f'alertname="{self.p["source"]}"'],
                     "target_matchers": [f'alertname=~"{"|".join(targets)}"'], "equal": ["service"]}
        w.am["inhibit_rules"].append(dict(self.rule))
        return True

    def break_(self, files, am, w):
        am["inhibit_rules"] = [r for r in am["inhibit_rules"] if {k: v for k, v in r.items() if k != "_comment"} != self.rule]

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        other = next(x.name for x in w.services if x.name != s.name)
        src = {"alertname": self.p["source"], "service": s.name, "severity": "page", "team": s.team}
        # The ticket asks to hold back the pages (fast burn, error ratio). The slow burn is a ticket, so holding
        # it back too is the owner's call either way: it is not checked in either direction.
        cases = [{"source": src, "target": {"alertname": t, "service": s.name, "severity": "page", "team": s.team},
                  "expect": True} for t in self.p["targets"] if "Slow" not in t]
        t0 = self.p["targets"][0]
        cases += [{"source": src, "target": {"alertname": t0, "service": other, "severity": "page", "team": s.team}, "expect": False},
                  {"source": {"alertname": t0, "service": s.name, "severity": "page", "team": s.team},
                   "target": {**src}, "expect": False},
                  {"source": src, "target": {"alertname": "ContainerMemoryNearLimit", "service": s.name, "severity": "ticket",
                                             "team": s.team}, "expect": False},
                  {"source": {"alertname": f"{w.svc(other).camel}Down", "service": other, "severity": "page", "team": w.svc(other).team},
                   "target": {"alertname": t0, "service": s.name, "severity": "page", "team": s.team}, "expect": False}]
        # The ticket is about this one service ("other services' pages shouldn't change"): another service's Down
        # must not start holding back that service's own pages. `scope` cases fail the whole requirement.
        cases[-4]["scope"] = cases[-1]["scope"] = True
        taken = w.notes.get("inhibit_sources", set())
        o = next((x for x in w.services if x.name != s.name and x.name not in taken), None)
        if o is not None:
            down = {"alertname": f"{o.camel}Down", "service": o.name, "severity": "page", "team": o.team}
            for t in (f"{o.camel}HighErrorRatio", f"{o.camel}ErrorBudgetBurnFast"):
                cases.append({"source": down, "target": {"alertname": t, "service": o.name, "severity": "page", "team": o.team},
                              "expect": False, "scope": True})
        return self.entry(inhibit={"cases": [_with_alts(c, w) for c in cases]}), []

    def variants(self, w):
        def legacy(files, am):
            self.break_(files, am, w)
            am["inhibit_rules"].append({"source_match": {"alertname": self.p["source"]},
                                        "target_match_re": {"alertname": "|".join(self.p["targets"])}, "equal": ["service"]})

        def no_equal(files, am):
            self.break_(files, am, w)
            am["inhibit_rules"].append({k: v for k, v in self.rule.items() if k != "equal"})

        def pages_only(files, am):
            self.break_(files, am, w)
            pages = [t for t in self.p["targets"] if "Slow" not in t]
            am["inhibit_rules"].append({**self.rule, "target_matchers": [f'alertname=~"{"|".join(pages)}"']})

        def catchall_down(files, am):
            # "any Down holds back every page of its service": mutes every other service's pages too
            self.break_(files, am, w)
            am["inhibit_rules"].append({"source_matchers": ['alertname=~".+Down"'],
                                        "target_matchers": ['severity=~"page|critical"', 'alertname!~".+Down"'],
                                        "equal": ["service"]})

        def kept_legacy_name(files, am):
            # H01 fixed in place under the pristine rule's name (its runbook link follows the name, as README asks),
            # and the inhibition sourced from that alert
            for _, _, r in rules_named(files, self.p["source"]):
                r["alert"] = old_name
                if isinstance(r.get("annotations"), dict):
                    r["annotations"]["runbook_url"] = w.runbook(old_name)
            for r in am["inhibit_rules"]:
                if r.get("source_matchers") == self.rule["source_matchers"]:
                    r["source_matchers"] = [f'alertname="{old_name}"']

        old_name = _down_names(w, self.p["svc"])[-1]
        extra = [("h01_kept_legacy_name", kept_legacy_name, "accept")] if old_name != self.p["source"] else []
        return [("oracle", lambda f, a: None, "accept"), ("legacy_match_syntax", legacy, "accept"),
                ("pages_only", pages_only, "accept"), *extra,
                ("without_equal", no_equal, 0.0), ("catchall_down", catchall_down, 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r06(self, w, rng)


# ---------------------------------------------------------------------------- R03

class LabelMove(Item):
    """Services moved to a new team (the week's trigger event); every rule of theirs with a static `team`
    has to follow ownership.yaml. The request is derived, never an enumerated list."""
    code, kind = "R03", "migration"

    @staticmethod
    def split(w: World, old: str, new: str, moved: list[str]) -> dict | None:
        """The week's trigger: `new` was carved out of `old` and took `moved` with it. ownership.yaml already says
        so (the services carry `new` from here on); their rules still say `old` until this item is done."""
        if not moved or not any(s.team == old and s.name not in moved for s in w.services):
            return None
        for s in w.services:
            if s.name in moved:
                s.team = new
        w.teams.append(new)
        return {"old": old, "new": new, "moved": list(moved)}

    def setup(self, w, rng):
        tr = w.trigger
        if not tr.get("moved"):
            return False
        self.p.update(tr)
        items = []
        for f in w.files.values():
            for g in f.groups:
                for r in g["rules"]:
                    lab = r.get("labels") or {}
                    svc_hit = any(f'"{m}"' in r.get("expr", "") for m in tr["moved"])
                    if "alert" in r and lab.get("team") == tr["new"] and svc_hit:
                        items.append(r["alert"])
        self.p["alerts"] = sorted(set(items))[:12]
        self.touch = set(self.p["alerts"])
        return bool(self.p["alerts"])

    def break_(self, files, am, w):
        for name in self.p["alerts"]:
            for _, _, r in rules_named(files, name):
                if r["labels"].get("team") == self.p["new"]:
                    r["labels"]["team"] = self.p["old"]

    def spec(self, w, rng):
        from ..grader.xgrade import mentions
        from .model import rules_named as rn

        def labels(g, r):
            return {str(k): str(v) for k, v in {**(g.get("labels") or {}), **r["labels"]}.items()}

        items = []
        for name in self.p["alerts"]:
            defs = list(rn(w.files, name))
            for f, g, r in defs:
                if r["labels"].get("team") == self.p["new"]:
                    # the definitions about the moved services: all of them move, none is added or dropped
                    svcs = [m for m in self.p["moved"] if mentions(r, m)]
                    rel = [(g2, r2) for _, g2, r2 in defs if any(mentions(r2, m) for m in svcs)]
                    items.append({"alert": name, "labels": labels(g, r), "svcs": svcs, "n": len(rel),
                                  "allowed": [labels(g2, r2) for g2, r2 in rel]})
        return self.entry(migration={"items": items}), []

    def variants(self, w):
        def group_label(files, am):
            for f in files.values():
                for g in f.groups:
                    hits = [r for r in g["rules"] if r.get("alert") in self.p["alerts"]]
                    if hits and len(hits) == len([r for r in g["rules"] if "alert" in r]):
                        for r in hits:
                            r["labels"].pop("team", None)
                        g["labels"] = {"team": self.p["new"]}

        def miss_one(files, am):
            name = self.p["alerts"][-1]
            for _, _, r in rules_named(files, name):
                r["labels"]["team"] = self.p["old"]
                break

        def add_dont_move(files, am):
            # the new-team copies next to the old ones: double pages, and the old team keeps getting paged
            for name in self.p["alerts"]:
                for _, g, r in list(rules_named(files, name)):
                    if r["labels"].get("team") == self.p["new"]:
                        old = {**r, "labels": {**r["labels"], "team": self.p["old"]}}
                        g["rules"].append(old)

        def static_service(files, am):
            # README: service alerts may carry `service` as a static label
            for name in self.p["alerts"]:
                for _, _, r in rules_named(files, name):
                    hit = [m for m in self.p["moved"] if f'"{m}"' in r.get("expr", "")]
                    if len(hit) == 1 and "service" not in r["labels"]:
                        r["labels"]["service"] = hit[0]

        n = sum(1 for name in self.p["alerts"] for _ in rules_named(w.files, name))
        return [("oracle", lambda f, a: None, "accept"), ("group_level_label", group_label, "accept"),
                ("static_service_label", static_service, "accept"),
                ("one_rule_missed", miss_one, (n - 1) / n + 1e-9), ("add_dont_move", add_dont_move, 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r03(self, w, rng)


# ---------------------------------------------------------------------------- R04 (+ H17)

class TeamSubtree(Item):
    """Route the new team. When the old team kept a service-regex route for its services (H17), the new
    subtree has to sit above it or the regex has to stop matching the moved services."""
    code, kind = "R04", "route"

    def setup(self, w, rng):
        tr = w.trigger
        if not tr.get("new"):
            return False
        self.p.update(tr)
        self.p["legacy"] = rng.random() < 0.7
        self.route_touch = {tr["new"], tr["old"]}
        if self.p["legacy"]:
            old_svcs = [s.name for s in w.services if s.team == tr["old"]]
            self.p["kept"] = old_svcs
            if not old_svcs:
                self.p["legacy"] = False
            else:
                self.code = "H17"
                rx = "|".join(old_svcs)
                self.p["legacy_matcher"] = f'service=~"{rx}"'
                legacy = {"matchers": [f'service=~"{rx}"'], "receiver": f"slack-{tr['old']}",
                          "routes": [{"matchers": ['severity=~"page|critical"'], "receiver": f"pagerduty-{tr['old']}", "continue": True},
                                     {"receiver": f"slack-{tr['old']}"}],
                          "_comment": [f"{tr['old']} services from before team labels were everywhere"]}
                routes = w.am["route"]["routes"]
                idx = next(i for i, r in enumerate(routes) if r.get("matchers") == [f'team="{tr["new"]}"'] or
                           (r.get("match") or {}).get("team") == tr["new"])
                routes.insert(idx, legacy)
        return True

    def break_(self, files, am, w):
        new = route_for_team(am, self.p["new"])
        am["route"]["routes"].remove(new)
        if self.p["legacy"]:
            leg = next(r for r in am["route"]["routes"] if r.get("matchers", [None])[0] == self.p["legacy_matcher"])
            rx = "|".join(self.p["kept"] + self.p["moved"])
            leg["matchers"] = [f'service=~"{rx}"']

    def spec(self, w, rng):
        new, old = self.p["new"], self.p["old"]
        pos, neg = [], []
        for svc in self.p["moved"]:
            for sev in ("page", "critical", "ticket", "warning"):
                lab = {"alertname": f"{w.svc(svc).camel}HighErrorRatio" if sev != "ticket" else "ContainerMemoryNearLimit",
                       "severity": sev, "team": new, "service": svc}
                pos.append({"labels": lab, "expect": wpolicy(w, new, sev)})
            neg.append({"labels": {"alertname": "SyntheticPage", "severity": "page", "team": new, "service": svc},
                        "forbid": [f"pagerduty-{old}"]})
        for svc in self.p.get("kept", []):
            lab = {"alertname": f"{w.svc(svc).camel}Down", "severity": "page", "team": old, "service": svc}
            pos.append({"labels": lab, "expect": wpolicy(w, old, "page")})
        neg.append({"labels": {"alertname": "SyntheticTicket", "severity": "ticket", "team": new, "service": self.p["moved"][0]},
                    "forbid": [f"pagerduty-{new}"]})
        other = next(t for t in w.teams if t not in (new, old))
        neg.append({"labels": {"alertname": "SyntheticPage", "severity": "page", "team": other, "service": "x"},
                    "forbid": [f"pagerduty-{new}"]})
        rt = {"pos": pos, "neg": neg, "integrations": {f"pagerduty-{new}": "pagerduty_configs", f"slack-{new}": "slack_configs"}}
        return self.entry(route=rt), []

    def variants(self, w):
        def continue_legacy(files, am):
            if not self.p["legacy"]:
                return
            self.break_(files, am, w)
            am["route"]["routes"].append(team_route(self.p["new"]))
            leg = next(r for r in am["route"]["routes"] if any("service=~" in m for m in r.get("matchers", [])))
            leg["routes"][0]["matchers"] = ['severity=~"page|critical"', f'team="{self.p["old"]}"']
            leg["matchers"].append(f'team="{self.p["old"]}"')

        def at_bottom(files, am):
            self.break_(files, am, w)
            am["route"]["routes"].append(team_route(self.p["new"]))

        def above_legacy(files, am):
            self.break_(files, am, w)
            routes = am["route"]["routes"]
            idx = next(i for i, r in enumerate(routes) if any("service=~" in m for m in r.get("matchers", [])))
            routes.insert(idx, team_route(self.p["new"]))

        if not self.p["legacy"]:
            return [("oracle", lambda f, a: None, "accept"), ("subtree_appended_at_bottom", at_bottom, "accept"),
                    ("pristine", lambda f, a: self.break_(f, a, w), 0.34)]
        return [("oracle", lambda f, a: None, "accept"),
                ("new_subtree_above_legacy", above_legacy, "accept"),
                ("legacy_scoped_by_team", continue_legacy, "accept"),
                ("subtree_appended_at_bottom", at_bottom, 0.3),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.3)]

    def ticket(self, w, rng):
        return prose.r04(self, w, rng)


# ---------------------------------------------------------------------------- R05

class ReceiverMove(Item):
    code, kind = "R05", "route"

    def setup(self, w, rng):
        cands = [t for t in w.teams if t not in (w.trigger.get("new"), w.trigger.get("old"))
                 and t not in w.notes.get("route_busy", set())
                 and route_for_team(w.am, t) and route_for_team(w.am, t).get("matchers")]
        if not cands:
            return False
        t = rng.choice(cands)
        w.notes.setdefault("route_busy", set()).add(t)
        self.p.update(team=t, old=f"slack-{t}", new=f"slack-{t}-oncall", channel=f"#{t}-oncall")
        w.notes.setdefault("slack_of", {})[t] = self.p["new"]
        w.am["receivers"].append({"name": self.p["new"], "slack_configs": [{"channel": self.p["channel"], "send_resolved": True}]})
        self._swap(w.am, self.p["old"], self.p["new"])
        self.route_touch = {t}
        self.receivers_touch = {self.p["old"], self.p["new"]}
        return True

    @staticmethod
    def _swap(am, a, b):
        def walk(n):
            if n.get("receiver") == a:
                n["receiver"] = b
            for c in n.get("routes") or []:
                walk(c)
        for r in am["route"]["routes"]:
            walk(r)

    def break_(self, files, am, w):
        self._swap(am, self.p["new"], self.p["old"])
        am["receivers"] = [r for r in am["receivers"] if r["name"] != self.p["new"]]

    def spec(self, w, rng):
        t = self.p["team"]
        svc = next((s.name for s in w.services if s.team == t), "x")
        pos = []
        for sev in ("page", "ticket", "warning"):
            exp = [self.p["new"] if x == self.p["old"] else x for x in policy(t, sev)]
            pos.append({"labels": {"alertname": "Synthetic", "severity": sev, "team": t, "service": svc}, "expect": sorted(exp)})
        rt = {"pos": pos, "neg": [], "retired": self.p["old"], "integrations": {self.p["new"]: "slack_configs"}}
        if prose.r05_literal(w):
            rt["retired_literal"] = True   # "nothing may route to <old>": no route may name it, reachable or not
        return self.entry(route=rt), []

    def variants(self, w):
        def rename_in_place(files, am):
            self.break_(files, am, w)
            for r in am["receivers"]:
                if r["name"] == self.p["old"]:
                    r["name"] = self.p["new"]
                    r["slack_configs"][0]["channel"] = self.p["channel"]
            self._swap(am, self.p["old"], self.p["new"])

        def empty_receiver(files, am):
            for r in am["receivers"]:
                if r["name"] == self.p["new"]:
                    r.pop("slack_configs")

        def parent_receiver_left(files, am):
            # every alert of the team ends at its unconditional last child (the new receiver); the subtree's own
            # receiver, which no alert can reach any more, still names the old one
            def walk(n):
                kids = [k for k in n.get("routes") or [] if isinstance(k, dict)]
                if n.get("receiver") == self.p["new"] and any(
                        k.get("receiver") == self.p["new"] and not (k.get("matchers") or k.get("match") or k.get("match_re"))
                        for k in kids):
                    n["receiver"] = self.p["old"]
                for k in kids:
                    walk(k)
            for r in am["route"]["routes"]:
                walk(r)

        out = [("oracle", lambda f, a: None, "accept"), ("rename_receiver", rename_in_place, "accept"),
               ("receiver_without_integration", empty_receiver, 0.0), ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]
        if not prose.r05_literal(w):   # "everything that goes there should go to the new channel": a behaviour
            out.append(("parent_receiver_left", parent_receiver_left, "accept"))
        return out

    def ticket(self, w, rng):
        return prose.r05(self, w, rng)
