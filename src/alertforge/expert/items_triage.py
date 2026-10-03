"""Triage items (mostly E5 survey answers, some routine): R08 decommission, R10/R11 severity changes,
R12 a Slack line that stops mid-sentence, R13 a runbook link to a retired wiki, R18 a named duplicate, and
`kept` (an alert that was right, penalty only)."""

from __future__ import annotations

import random

from ..grader.norm import core_entry
from ..series import histogram
from . import prose_triage as prose
from .items import Item
from .model import home_group, RuleFileSpec, World, first_rule, replace_rule, rules_named
from .repo import error_ratio_rule, latency_two_tier
from .evidence import canonical_le
from .vocab import camel
from .xscen import G, Raw, background, ip, pod_name


def _home_group(w: World, s):
    return home_group(w, s)


# ---------------------------------------------------------------------------- R08

class Decommission(Item):
    code, kind = "R08", "absence"

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind == "api" and s.family == "http" and s.home.startswith("legacy-")
                 and s.name not in w.notes.get("busy", set())]
        if not cands:
            return False
        s = rng.choice(cands)
        z = f"{s.name}-legacy"
        self.p.update(svc=s.name, gone=z, team=s.team, date=w.cal.ordinal(w.cal.past(rng, 14, 30)))
        from .model import Svc
        zs = Svc(z, s.team, "api", "http", err_thr=s.err_thr)
        self.p["rules"] = [error_ratio_rule(w, zs)] + latency_two_tier(w, zs)[:1]
        self.p["rules"][0]["_comment"] = [f"{z} is the old stack, kept until the migration finishes"]
        self.names = {r["alert"] for r in self.p["rules"]}
        self.touch = set(self.names)
        self.route_touch = {s.team}
        return True

    def break_(self, files, am, w):
        s = w.svc(self.p["svc"])
        grp = next(g for g in files[s.home].groups if g["name"] == s.name)
        grp["rules"] += [dict(r) for r in self.p["rules"]]
        am["route"]["routes"].insert(2, {"matchers": [f'service="{self.p["gone"]}"'], "receiver": f"slack-{s.team}",
                                         "routes": [{"matchers": ['severity=~"page|critical"'],
                                                     "receiver": f"pagerduty-{s.team}", "continue": True},
                                                    {"receiver": f"slack-{s.team}"}]})

    def spec(self, w, rng):
        return self.entry(absence={"alerts": sorted(self.names), "route_values": [self.p["gone"]]}), []

    def variants(self, w):
        def keep_route(files, am):
            self.break_(files, am, w)
            from .model import drop_rules
            drop_rules(files, self.names)

        def delete_survivor_too(files, am):
            from .model import drop_rules
            drop_rules(files, {f"{w.svc(self.p['svc']).camel}HighErrorRatio"})

        return [("oracle", lambda f, a: None, "accept"), ("rules_only", keep_route, 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r08(self, w, rng)


# ---------------------------------------------------------------------------- R10 / R11

class SeverityChange(Item):
    kind = "relabel"

    def __init__(self, rid, direction="up"):
        super().__init__(rid)
        self.direction = direction
        self.code = "R10" if direction == "up" else "R11"

    def setup(self, w, rng):
        busy = w.notes.setdefault("busy", set())
        if self.direction == "up":
            cands = [s for s in w.services if s.kind == "worker" and s.home.startswith("legacy-") and s.name not in busy]
            if not cands:
                return False
            s = rng.choice(cands)
            name = f"{s.camel}QueueBacklogHigh"
            rule = {"alert": name, "expr": f'max by (service) (work_queue_depth{{service="{s.name}"}}) > 5000',
                    "for": "15m", "labels": {"severity": "page", "team": s.team},
                    "annotations": {"summary": "{{ $labels.service }} has more than 5000 jobs waiting",
                                    "runbook_url": w.runbook(name)}}
            before = "warning"
        else:
            cands = [s for s in w.services if s.kind == "consumer" and s.home.startswith("legacy-") and s.name not in busy]
            if not cands:
                cands = [s for s in w.services if s.kind == "worker" and s.home.startswith("legacy-") and s.name not in busy]
            if not cands:
                return False
            s = rng.choice(cands)
            name = f"{s.camel}Late" if s.camel.endswith("Export") else f"{s.camel}ExportLate"
            rule = {"alert": name,
                    "expr": f'time() - max by (service) (export_last_success_timestamp_seconds{{service="{s.name}"}}) > 3 * 3600',
                    "for": "30m", "labels": {"severity": "ticket", "team": s.team},
                    "annotations": {"summary": "{{ $labels.service }} hasn't finished an export in over 3 hours",
                                    "runbook_url": w.runbook(name)}}
            before = "page"
        busy.add(s.name)
        _home_group(w, s)["rules"].append(rule)
        w.notes.setdefault("pristine_severity", {})[name] = before
        self.p.update(svc=s.name, alert=name, before=before, after=rule["labels"]["severity"])
        self.names = self.touch = {name}
        return True

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.p["alert"]):
            r["labels"]["severity"] = self.p["before"]

    def spec(self, w, rng):
        f, g, r = next(rules_named(w.files, self.p["alert"]))
        item = {"alert": self.p["alert"], "labels": dict(r["labels"]), "same": core_entry(r, g), "svc": self.p["svc"],
                "n": sum(1 for _ in rules_named(w.files, self.p["alert"]))}
        if self.direction == "up":
            # README: `critical` is the older spelling of `page` and pages the same way; in a legacy file that
            # still writes `critical` for its pages, matching the file is just as right
            item["alt_labels"] = [{**r["labels"], "severity": "critical"}]
        return self.entry(relabel={"items": [item]}), []

    def variants(self, w):
        def tweak_threshold(files, am):
            for _, _, r in rules_named(files, self.p["alert"]):
                r["expr"] = r["expr"].replace("5000", "8000").replace("3 * 3600", "6 * 3600")

        other = "warning" if self.direction == "up" else "warning"
        extra = ([("critical_spelling", lambda f, a: [r["labels"].update(severity="critical") for _, _, r in rules_named(f, self.p["alert"])], "accept")]
                 if self.direction == "up" else [])
        def copy_left_behind(files, am):
            for _, g, r in list(rules_named(files, self.p["alert"])):
                g["rules"].append({**r, "labels": {**r["labels"], "severity": self.p["before"]}})

        def static_service(files, am):
            # README: service alerts may carry `service` as a static label (S10/FA-2)
            for _, _, r in rules_named(files, self.p["alert"]):
                r["labels"]["service"] = self.p["svc"]

        return [("oracle", lambda f, a: None, "accept"), *extra,
                ("static_service_label", static_service, "accept"),
                ("copy_left_behind", copy_left_behind, 0.0),
                ("reformatted", lambda f, a: [r.update(expr=r["expr"].replace(" > ", " >  ")) for _, _, r in rules_named(f, self.p["alert"])], "accept"),
                ("changed_threshold_too", tweak_threshold, 0.0),
                (f"set_to_{other}", lambda f, a: [r["labels"].update(severity=other) for _, _, r in rules_named(f, self.p["alert"])], 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.severity(self, w, rng)


# ---------------------------------------------------------------------------- R12

class BlankAnnotation(Item):
    code, kind = "R12", "annotation"

    def setup(self, w, rng):
        busy = w.notes.setdefault("busy_annot", set())
        cands = sorted({(s.name, name) for s in w.services for f in w.files.values() for g in f.groups for r in g["rules"]
                        for name in [r.get("alert", "")] if name == f"{s.camel}LatencyHigh" and s.name not in busy})
        if not cands:
            return False
        sname, name = rng.choice(cands)
        s = w.svc(sname)
        busy.add(s.name)
        self.p.update(svc=s.name, alert=name)
        self.names = self.touch = {name}
        return True

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.p["alert"]):
            ms = "500ms" if r["labels"]["severity"] == "warning" else "1s"
            r["annotations"]["summary"] = f"p99 latency above {ms} on {{{{ $labels.instance }}}}"

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        n = 30
        g = G(self.rid, "slow", n)
        g.series += background(w, rng, n)
        for i in range(3):
            base = {"service": s.name, "namespace": w.namespace, "pod": pod_name(rng, s.name), "instance": f"{ip(rng)}:8080"}
            tot = [0] + [rng.randint(900, 1100) for _ in range(n)]
            slow = [int(t * 0.05) for t in tot]
            # the core histogram writes `le="1"`; Prometheus 3 stores it as "1.0" (S6)
            g.series += [Raw(x.metric, {**x.labels, **({"le": canonical_le(x.labels["le"])} if "le" in x.labels else {})}, x.values)
                         for x in histogram(rng, base, [a - b for a, b in zip(tot, slow)], slow, "1")]
        g.snap(26, "annot")
        return self.entry(annotation={"alert": self.p["alert"], "service": s.name}), [g.done()]

    def variants(self, w):
        def mod(text):
            def f(files, am):
                for _, _, r in rules_named(files, self.p["alert"]):
                    r["annotations"]["summary"] = text
            return f
        return [("oracle", lambda f, a: None, "accept"),
                ("service_written_out", mod(f"p99 latency on {self.p['svc']} is above its threshold"), "accept"),
                ("no_label", mod("p99 latency is above its threshold"), 0.0),
                ("pod_label", mod("p99 latency high on {{ $labels.pod }}"), 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r12(self, w, rng)


# ---------------------------------------------------------------------------- R13

class RunbookLink(Item):
    code, kind = "R13", "relabel"

    def setup(self, w, rng):
        busy = w.notes.setdefault("busy_runbook", set())
        cands = sorted({r["alert"] for f in w.files.values() if f.gen == 1 for g in f.groups for r in g["rules"]
                        if "alert" in r and r["alert"].endswith(("DbConnectionsHigh", "HighErrorRatio")) and r["alert"] not in busy})
        if not cands:
            return False
        name = rng.choice(cands)
        busy.add(name)
        stem = name.lower()
        self.p.update(alert=name, page=f"runbooks/{stem}.md", wiki=f"https://wiki.{w.domain}/display/{w.company.key}/{name}+runbook")
        w.runbooks[name] = {"file": f"{stem}.md", "alerts": [name], "card": "R13", "body": lambda: prose.generic_runbook(name, w)}
        self.names = self.touch = {name}
        return True

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.p["alert"]):
            r["annotations"]["runbook_url"] = self.p["wiki"]

    def spec(self, w, rng):
        n = sum(1 for _ in rules_named(w.files, self.p["alert"]))
        items = [{"alert": self.p["alert"], "labels": None, "same": None, "n": n,
                  "runbooks": [w.runbook(self.p["alert"]), self.p["page"]]}]
        return self.entry(relabel={"items": items}), []

    def variants(self, w):
        return [("oracle", lambda f, a: None, "accept"),
                ("relative_page", lambda f, a: [r["annotations"].update(runbook_url=self.p["page"]) for _, _, r in rules_named(f, self.p["alert"])], "accept"),
                ("other_runbook", lambda f, a: [r["annotations"].update(runbook_url=w.runbook("HostOutOfMemory")) for _, _, r in rules_named(f, self.p["alert"])], 0.0),
                ("dead_link_copy_kept", lambda f, a: [g["rules"].append({**r, "annotations": {**r["annotations"], "runbook_url": self.p["wiki"]}})
                                                      for _, g, r in list(rules_named(f, self.p["alert"]))], 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r13(self, w, rng)


# ---------------------------------------------------------------------------- R18

class Duplicate(Item):
    code, kind = "R18", "absence"
    OLD, NEW = "NodeDiskFull", "HostDiskAlmostFull"

    def setup(self, w, rng):
        if "disk" in w.files:
            return False
        keep = {"alert": self.NEW, "expr": 'node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes < 0.1',
                "for": "5m", "labels": {"severity": "warning", "team": "infra"},
                "annotations": {"summary": "{{ $labels.instance }} {{ $labels.mountpoint }} is under 10% free",
                                "runbook_url": w.runbook(self.NEW)}}
        w.files["disk"] = RuleFileSpec("disk", 1, [{"name": "disk", "rules": [keep]}], ["host disks (infra)"])
        self.names = {self.OLD}
        self.touch = {self.OLD}
        return True

    def break_(self, files, am, w):
        old = {"alert": self.OLD, "expr": '(node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100 < 10',
               "for": "10m", "labels": {"severity": "warning", "team": "infra"},
               "annotations": {"summary": "Disk full on {{ $labels.instance }}", "runbook_url": w.runbook(self.OLD)},
               "_comment": ["TODO(2023) superseded by HostDiskAlmostFull?"]}
        files["disk"].groups[0]["rules"].insert(0, old)

    def spec(self, w, rng):
        return self.entry(absence={"alerts": [self.OLD], "route_values": []}), []

    def variants(self, w):
        from .model import drop_rules

        def drop_the_new_one(files, am):
            self.break_(files, am, w)
            drop_rules(files, {self.NEW})

        return [("oracle", lambda f, a: None, "accept"), ("dropped_the_newer_alert", drop_the_new_one, 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.r18(self, w, rng)


# ---------------------------------------------------------------------------- kept

class Kept:
    """An E5 complaint about an alert that was right. Not a requirement: breaking it costs reward."""

    def __init__(self, w: World, rng: random.Random):
        cands = sorted({(r["alert"], f.stem) for f in w.files.values() for g in f.groups for r in g["rules"]
                        if r.get("alert", "").endswith("DbConnectionsHigh") and r["labels"].get("severity") == "critical"})
        self.ok = bool(cands)
        if not self.ok:
            return
        self.alert, _ = rng.choice(cands)
        f, g, r = next((f, g, r) for f, g, r in rules_named(w.files, self.alert) if r["labels"]["severity"] == "critical")
        self.want = core_entry(r, g, ("severity",))
        self.svc = next(s for s in w.services if self.alert.startswith(s.camel))

    def spec(self) -> dict:
        return {"alert": self.alert, "want": self.want}
