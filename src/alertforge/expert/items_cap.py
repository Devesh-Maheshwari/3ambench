"""E5 semantics defects: H08 (a ramp-and-flush memory series keeps restarting `for`), H09 (a 1m rate window
on a job scraped every minute), H10 (`or vector(0)` on a single-consumer record blinds its absent() guard),
H23 (`predict_linear` alone pages on nightly backups and misses a log flood). Shapes follow sp7, sp16, sp18
and sp19; hidden scenarios keep the evidence's mechanism and cadence."""

from __future__ import annotations

import random

from . import prose_cap as prose
from .items import Item, outcome_entry, svc_class
from .model import home_group, World, add_rules, rules_named
from .items_svc import drop_for
from .xscen import G, background, counter_from_rates, ip, pod_name, series

LOW = [0.62, 0.66, 0.70, 0.73, 0.76]
# Two scrapes over 0.9 per 5-minute cycle and three clearly under it. No value sits within the series' 0.2% noise
# of the threshold: a 0.90 step read 0.9018 on some cycles, which kept `max[3m]` and the 10m median over 0.9 for a
# whole `for` on afx-e5-s05 (RT-5).
RAMPS = [[0.82, 0.86, 0.89, 0.94, 0.97], [0.80, 0.85, 0.89, 0.93, 0.99]]
DWELL = [0.86, 0.88, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98]
NOISE = 0.002


def _set(files, name, **kw):
    for _, _, r in rules_named(files, name):
        r.update(kw)


# ---------------------------------------------------------------------------- H08

class MemorySawtooth(Item):
    code, kind, ticket_like = "H08", "outcome", True
    ALERT = "ContainerMemoryNearLimit"

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind == "indexer"]
        if not cands:
            return False
        s = cands[0]
        self.p.update(svc=s.name, limit=rng.choice([4, 6, 8]) * 1024 ** 3)
        if not any(self.ALERT == r.get("alert") and f'service="{s.name}"' in r["expr"]
                   for _, _, r in rules_named(w.files, self.ALERT)):
            from .repo import memory_rule
            add_rules(w.files, "capacity", "capacity", [memory_rule(w, s)])
        self.names = {self.ALERT}
        self.touch = {self.ALERT}
        self._fix(w.files, "5m", "10m")
        return True

    def _rule(self, files):
        s = self.p["svc"]
        return next(r for _, _, r in rules_named(files, self.ALERT) if f'service="{s}"' in r["expr"])

    def _fix(self, files, win, for_, fn="max_over_time", arg=""):
        s = self.p["svc"]
        ws = f'container_memory_working_set_bytes{{service="{s}",container="{s}"}}'
        lim = f'container_spec_memory_limit_bytes{{service="{s}",container="{s}"}}'
        r = self._rule(files)
        r["expr"] = f"max by (service) ({fn}({arg}{ws}[{win}]) / {lim}) > 0.9"
        r["for"] = for_

    def break_(self, files, am, w):
        s = self.p["svc"]
        r = self._rule(files)
        r["expr"] = (f'max by (service) (container_memory_working_set_bytes{{service="{s}",container="{s}"}}'
                     f' / container_spec_memory_limit_bytes{{service="{s}",container="{s}"}}) > 0.9')
        r["for"] = "15m"

    def _mem(self, rng, w, n, ratio):
        s, lim = self.p["svc"], self.p["limit"]
        lab = {"service": s, "container": s, "namespace": w.namespace, "pod": pod_name(rng, s)}
        ws = [int(lim * v * (1 + rng.uniform(-NOISE, NOISE))) if v is not None else None for v in ratio]
        return [series("container_memory_working_set_bytes", lab, ws),
                series("container_spec_memory_limit_bytes", lab, [lim] * (n + 1))]

    def spec(self, w, rng):
        s, n, groups = self.p["svc"], 100, []

        def grp(name, ratio, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n) + self._mem(rng, w, n, ratio)
            fam(g)
            g.labelless()
            groups.append(g.done())

        ramp = rng.choice(RAMPS)
        on = rng.randint(28, 38)
        saw = [ramp[(t - on) % 5] if t >= on else LOW[t % 5] for t in range(n + 1)]
        first = next(t for t, v in enumerate(saw) if v > 0.9)
        grp("sawtooth", saw, lambda g: (g.fire(first + 20, s, cls="ticket"), g.quiet(0, first - 1, s, cls="ticket")))
        exc = {30: 0.92, 31: 0.95, 32: 0.97, 33: 0.96, 34: 0.93}
        grp("busy_stretch", [exc.get(t, LOW[t % 5]) for t in range(n + 1)], lambda g: g.quiet(0, n, s, cls="ticket"))
        br = [0.78, 0.81, 0.84, 0.86, 0.88]
        grp("ramp_below", [br[(t - 30) % 5] if t >= 30 else LOW[t % 5] for t in range(n + 1)],
            lambda g: g.quiet(0, n, s, cls="ticket"))
        # the milder detect: a bigger buffer flushing every 8 minutes, six scrapes over 0.9 and two under. A mean, a
        # median or a 3-minute max over time stays over 0.9 here and still misses the 5-minute cycle
        on2 = rng.randint(28, 38)
        dwell = [DWELL[(t - on2) % 8] if t >= on2 else LOW[t % 5] for t in range(n + 1)]
        first2 = next(t for t, v in enumerate(dwell) if v > 0.9)
        grp("long_dwell", dwell, lambda g: g.fire(first2 + 20, s, cls="ticket"))
        e = outcome_entry(self, w, {s: svc_class(w, s, "ticket")}, named=self.ALERT)
        e["outcome"]["classes"] = {"ticket": svc_class(w, s, "ticket")}
        return e, groups

    def variants(self, w):
        v = lambda win, f, fn="max_over_time", arg="": (lambda files, am: self._fix(files, win, f, fn, arg))  # noqa: E731
        return [("oracle", lambda f, a: None, "accept"),
                ("max_10m_for_15m", v("10m", "15m"), "accept"),
                ("max_4m_for_8m", v("4m", "8m"), "accept"),
                ("quantile_90_5m_for_10m", v("5m", "10m", "quantile_over_time", "0.9, "), "accept"),
                ("avg_5m_for_10m", v("5m", "10m", "avg_over_time"), 0.5),
                ("median_10m_for_10m", v("10m", "10m", "quantile_over_time", "0.5, "), 0.5),
                ("max_3m_for_10m", v("3m", "10m"), 0.5),
                ("max_5m_for_5m", v("5m", "5m"), 0.7),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h08(self, w, rng)


# ---------------------------------------------------------------------------- H09

class KafkaStall(Item):
    code, kind, ticket_like = "H09", "outcome", True
    ALERT = "KafkaConsumerStalled"

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind in ("worker", "consumer") and s.name not in w.notes.get("busy", set())]
        if not cands or "kafka-consumers" in w.files:
            return False
        s = rng.choice(cands)
        w.notes.setdefault("busy", set()).add(s.name)
        self.p.update(svc=s.name, group=s.name.replace("-", "_") + "_cg", topic=rng.choice(["orders", "events", "jobs", "updates"]) + ".v1")
        from .model import RuleFileSpec
        rule = {"alert": self.ALERT, "expr": self._expr("4m"), "for": "10m",
                "labels": {"severity": "page", "team": s.team, "service": s.name},
                "annotations": {"summary": "consumer group {{ $labels.consumergroup }} stopped committing with lag waiting",
                                "runbook_url": w.runbook(self.ALERT)}}
        w.files["kafka-consumers"] = RuleFileSpec("kafka-consumers", 2, [{"name": "kafka-consumers", "rules": [rule]}],
                                                  [f"consumer groups we own. The kafka-exporter job is scraped every 60s (ADR {w.notes.get('adr', {}).get('scrape', '0003')})."])
        w.runbooks[self.ALERT] = {"file": "kafka-consumer-stalled.md", "alerts": [self.ALERT], "card": "H09", "body": lambda: prose.kafka_runbook(s, w.namespace, w)}
        self.names = self.touch = {self.ALERT}
        return True

    def _expr(self, win, fn="rate"):
        g = self.p["group"]
        return (f'sum by (consumergroup) ({fn}(kafka_consumergroup_current_offset_sum{{consumergroup="{g}"}}[{win}])) == 0'
                f' and on (consumergroup) sum by (consumergroup) (kafka_consumergroup_lag_sum{{consumergroup="{g}"}}) > 0')

    def break_(self, files, am, w):
        _set(files, self.ALERT, expr=self._expr("1m"))

    def _kafka_labels(self) -> dict:
        return {"consumergroup": self.p["group"], "topic": self.p["topic"], "job": "kafka-exporter",
                "instance": "kafka-exporter.monitoring:9308"}

    def exporters(self, w):
        # the consumer group keeps committing with a little lag in every scenario that isn't about it
        lab = self._kafka_labels()
        return [{"service": self.p["svc"], "metric": "kafka_consumergroup_current_offset_sum", "labels": lab,
                 "key": ("consumergroup",), "values": ("counter", 40, 90)},
                {"service": self.p["svc"], "metric": "kafka_consumergroup_lag_sum", "labels": lab,
                 "key": ("consumergroup",), "values": ("ints", 10, 300)}]

    def _kafka(self, rng, n, rates, lags):
        lab = self._kafka_labels()
        return [series("kafka_consumergroup_current_offset_sum", lab, counter_from_rates(rng, rates, rng.randint(10**6, 10**7))),
                series("kafka_consumergroup_lag_sum", lab, lags)]

    def spec(self, w, rng):
        s, n, groups = self.p["svc"], 80, []

        def grp(name, rates, lags, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n) + self._kafka(rng, n, rates, lags)
            fam(g)
            g.labelless()
            groups.append(g.done())

        base = rng.uniform(40, 90)
        st, en = rng.randint(28, 34), 60
        rates = [0.0 if st <= m < en else base * rng.uniform(0.9, 1.1) for m in range(n + 1)]
        lags = [rng.randint(20, 200) if m < st else (int(base * 60 * (m - st)) if m < en else max(0, int(base * 60 * (en - st) - base * 90 * (m - en)))) for m in range(n + 1)]
        # the last offset increase lands in the sample at st - 1, so the group stopped right after it: the
        # runbook's 15 minutes run from there (`[6m]` pages at st + 14, `[7m]` one minute too late)
        grp("stall", rates, lags, lambda g: (g.fire(st + 14, s), g.quiet(0, st - 1, s)))
        st2, en2 = 15, 35
        rates2 = [0.0 if st2 <= m < en2 else base * 1.6 for m in range(n + 1)]
        lags2 = [rng.randint(20, 200) if m < st2 else (int(base * 60 * (m - st2)) if m < en2 else max(5, int(base * 60 * 20 - base * 40 * (m - en2)))) for m in range(n + 1)]
        grp("recovered", rates2, lags2, lambda g: g.quiet(en2 + 10, n, s))
        grp("idle_no_lag", [0.0] * (n + 1), [0] * (n + 1), lambda g: g.quiet(0, n, s))
        # a rebalance holds commits for 4 minutes while lag builds (the runbook: that isn't a stall). A [4m] window
        # reads zero on two evaluations, so the rule's `for: 10m` (anything from 2m) stays quiet; no `for` pages.
        rb = 40
        rates3 = [0.0 if rb <= m < rb + 4 else base * rng.uniform(0.9, 1.1) for m in range(n + 1)]
        lags3 = [int(base * 60 * (m - rb + 1)) if rb <= m < rb + 4 else rng.randint(20, 200) for m in range(n + 1)]
        grp("rebalance", rates3, lags3, lambda g: g.quiet(0, n, s))
        grp("normal", [base * rng.uniform(0.9, 1.1) for _ in range(n + 1)], [rng.randint(10, 300) for _ in range(n + 1)],
            lambda g: g.quiet(0, n, s))
        # the milder detect: a stall nobody fixes, checked half an hour in (a window too long for the runbook's 15
        # minutes still pages by then; a rule that can't see a 1m-scraped counter never does)
        st4 = rng.randint(28, 34)
        rates4 = [0.0 if m >= st4 else base * rng.uniform(0.9, 1.1) for m in range(n + 1)]
        lags4 = [rng.randint(20, 200) if m < st4 else int(base * 60 * (m - st4)) for m in range(n + 1)]
        grp("long_stall", rates4, lags4, lambda g: g.fire(st4 + 29, s))
        e = outcome_entry(self, w, {s: svc_class(w, s)}, named=self.ALERT)
        return e, groups

    def variants(self, w):
        v = lambda win, fn="rate": (lambda f, a: _set(f, self.ALERT, expr=self._expr(win, fn)))  # noqa: E731
        return [("oracle", lambda f, a: None, "accept"), ("window_2m", v("2m"), "accept"), ("window_6m", v("6m"), "accept"),
                ("window_10m", v("10m"), 0.5), ("window_7m", v("7m"), 0.5), ("irate_1m", v("1m", "irate"), 0.0),
                ("for_zero", drop_for(self.touch), 0.85),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h09(self, w, rng)


# ---------------------------------------------------------------------------- H10

class OrVectorZero(Item):
    code, kind, ticket_like = "H10", "outcome", True

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind == "consumer" and s.name not in w.notes.get("busy", set())]
        if not cands:
            return False
        s = rng.choice(cands)
        w.notes.setdefault("busy", set()).add(s.name)
        u = s.name.replace("-", "_")
        self.p.update(svc=s.name, metric=f"{u}_pending_batches", record=f"{u}:pending_batches:max",
                      guard=f"{s.camel}MetricsMissing")
        home = home_group(w, s)
        home["rules"].insert(0, {"record": self.p["record"], "expr": f"max({self.p['metric']})"})
        home["rules"].append({"alert": self.p["guard"], "expr": f"absent({self.p['record']})", "for": "10m",
                              "labels": {"severity": "ticket", "team": s.team, "service": s.name},
                              "annotations": {"summary": f"no metrics from {s.name}",
                                              "runbook_url": w.runbook(self.p["guard"])}})
        # scraped from the consumer's own target: it carries that target's labels (unify_targets pairs it with an `up`)
        w.notes.setdefault("background_series", []).append(
            {"service": s.name, "metric": self.p["metric"], "value": 3,
             "labels": {"job": s.name, "instance": f"{ip(rng)}:9102", "service": s.name, "namespace": w.namespace,
                        "pod": pod_name(random.Random(s.name), s.name)}})
        self.touch = {self.p["guard"], self.p["record"]}
        return True

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.p["record"], "record"):
            r["expr"] = f"max({self.p['metric']}) or vector(0)"
            r["_comment"] = ["or vector(0) so the stat panel shows 0 instead of No data"]

    def spec(self, w, rng):
        s, n, groups = self.p["svc"], 60, []
        m = self.p["metric"]

        def grp(name, vals, up, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n, {s})
            inst = f"{ip(rng)}:9102"
            target = {"job": s, "instance": inst, "service": s, "namespace": w.namespace, "pod": pod_name(rng, s)}
            g.series += [series(m, target, vals), series("up", target, up)]
            fam(g)
            g.labelless()
            groups.append(g.done())

        wig = [rng.randint(2, 6) for _ in range(n + 1)]
        van = wig[:20] + ["stale"] + [None] * (n - 20)
        # The pods stay up and only the batch metric goes away: a target that vanishes is the Down alert's job
        # (it has `or absent(up{...})`), so the survey story only holds while `up` is still there.
        grp("vanish", van, [1] * (n + 1), lambda g: (g.fire(50, s, cls="ticket"), g.quiet(0, 19, s, cls="ticket")))

        def healthy(g):
            g.quiet(0, n, s, cls="ticket")
            g.count(f"count(abs({self.p['record']} - {wig[40]}) < 0.001) or vector(0)", 40, 1, "F_regress")
        grp("healthy", wig, [1] * (n + 1), healthy)
        grp("idle", [0] * (n + 1), [1] * (n + 1), lambda g: g.quiet(0, n, s, cls="ticket"))
        # the gauge drops out for two minutes while the consumer reconnects (the survey says that's normal): an
        # absent() guard is true on two evaluations, so any `for` from 2m stays quiet
        blink = wig[:20] + ["stale", None] + wig[22:]
        grp("reconnect", blink, [1] * (n + 1), lambda g: g.quiet(0, n, s, cls="ticket"))
        # the milder detect: the consumer scaled to zero, its target gone from discovery with the metric (a guard on
        # the target's own `up` sees this one, not the metric going away under a live target)
        grp("scaled_to_zero", van, [1] * 20 + ["stale"] + [None] * (n - 20), lambda g: g.fire(50, s, cls="ticket"))
        e = outcome_entry(self, w, {s: svc_class(w, s, "ticket")})
        e["outcome"]["classes"] = {"ticket": svc_class(w, s, "ticket")}
        return e, groups

    def variants(self, w):
        s = self.p["svc"]
        g, m = self.p["guard"], self.p["metric"]

        def guard(expr, for_="10m", keep_fallback=True):
            def f(files, am):
                if keep_fallback:
                    self.break_(files, am, w)
                _set(files, g, expr=expr, **{"for": for_})
            return f

        return [("oracle", lambda f, a: None, "accept"),
                ("guard_on_raw_metric", guard(f"absent({m})"), "accept"),
                ("guard_on_raw_metric_service", guard(f'absent({m}{{service="{s}"}})'), "accept"),
                ("guard_on_raw_metric_for_2m", guard(f"absent({m})", "2m"), "accept"),
                ("guard_on_raw_metric_for_1m", guard(f"absent({m})", "1m"), 0.8),
                ("for_zero", drop_for([g]), 0.8),
                ("guard_on_up", guard(f'absent(up{{job="{s}"}} == 1)'), 0.5),
                ("absent_over_time_10m", guard(f"absent_over_time({m}[10m])"), "accept"),
                ("guard_up_eq_0", guard(f'up{{job="{s}"}} == 0'), 0.0),
                ("absent_over_time_30m", guard(f"absent_over_time({m}[30m])"), 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h10(self, w, rng)


# ---------------------------------------------------------------------------- H23

class DiskPredict(Item):
    code, kind, ticket_like = "H23", "outcome", True
    ALERT = "DiskWillFillIn4h"

    def setup(self, w, rng):
        cands = [s for s in w.services if s.kind in ("api", "consumer", "worker") and s.home.startswith("legacy-")
                 and s.name not in w.notes.get("busy", set())]
        if not cands:
            return False
        s = rng.choice(cands)
        w.notes.setdefault("busy", set()).add(s.name)
        self.p.update(svc=s.name, page=f"{s.camel}DiskAlmostFull", size=200 * 10**9)
        sel = f'{{service="{s.name}",mountpoint="/var/lib/postgresql"}}'
        self.p["sel"] = sel
        grp = home_group(w, s)
        grp["rules"] += [
            {"alert": self.ALERT,
             "expr": f"predict_linear(node_filesystem_avail_bytes{sel}[1h], 4 * 3600) < 0 and node_filesystem_avail_bytes{sel} / node_filesystem_size_bytes{sel} < 0.15",
             "for": "30m", "labels": {"severity": "ticket", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} database volume will fill within 4 hours",
                             "runbook_url": w.runbook(self.ALERT)}},
            {"alert": self.p["page"], "expr": f"node_filesystem_avail_bytes{sel} / node_filesystem_size_bytes{sel} < 0.05",
             "for": "5m", "labels": {"severity": "page", "team": s.team},
             "annotations": {"summary": "{{ $labels.service }} database volume is under 5% free",
                             "runbook_url": w.runbook(self.p["page"])}}]
        self.touch = {self.ALERT, self.p["page"]}
        self.names = {self.ALERT}
        return True

    def exporters(self, w):
        # the database volume sits at a steady 58% free in every scenario that isn't about it
        s, size = self.p["svc"], self.p["size"]
        lab = {"service": s, "mountpoint": "/var/lib/postgresql", "device": "/dev/nvme1n1", "fstype": "ext4",
               "instance": f"{ip(random.Random(f'node-exporter:{s}'))}:9100", "job": "node"}
        return [{"service": s, "metric": m, "labels": lab, "key": ("service", "mountpoint"), "values": ("const", v)}
                for m, v in (("node_filesystem_avail_bytes", int(size * 0.58)), ("node_filesystem_size_bytes", size))]

    def break_(self, files, am, w):
        sel = self.p["sel"]
        _set(files, self.ALERT, expr=f"predict_linear(node_filesystem_avail_bytes{sel}[1h], 4 * 3600) < 0")
        for _, _, r in rules_named(files, self.ALERT):
            r["labels"]["severity"] = "page"
            r.pop("for", None)
            r["for"] = "5m"
        from .model import drop_rules
        drop_rules(files, {self.p["page"]})

    def spec(self, w, rng):
        s, size, groups = self.p["svc"], self.p["size"], []

        def grp(name, avail, fam):
            n = len(avail) - 1
            g = G(self.rid, name, n)
            lab = {"service": s, "mountpoint": "/var/lib/postgresql", "device": "/dev/nvme1n1", "fstype": "ext4",
                   "instance": f"{ip(rng)}:9100", "job": "node"}
            g.series += background(w, rng, n) + [series("node_filesystem_avail_bytes", lab, [int(x) for x in avail]),
                                                 series("node_filesystem_size_bytes", lab, [size] * (n + 1))]
            fam(g)
            g.labelless()
            groups.append(g.done())

        gb = 10**9
        slow = [60 * gb - 0.1 * gb * m for m in range(521)]
        grp("slow_fill", slow, lambda g: (g.quiet(0, 340, s, cls="ticket"), g.fire(500, s, cls="ticket"),
                                          g.quiet(0, 500, s, "S_defect", cls="page"), g.fire(510, s, cls="page")))
        # nightly backup at 50% free: ~55 GB written in 45 minutes (a 4h linear prediction goes below zero),
        # deleted an hour later; the level never gets near 15% free
        wr = rng.randint(52, 58)
        backup = [100 * gb - (wr * gb * (m - 120) / 45 if 120 <= m < 165 else wr * gb if 165 <= m < 225 else 0) for m in range(301)]
        grp("backup_sawtooth", backup, lambda g: (g.quiet(0, 300, s, cls="ticket"), g.quiet(0, 300, s, "S_defect", cls="page")))
        flood = [60 * gb if m < 120 else max(1 * gb, 60 * gb - 0.6 * gb * (m - 120)) for m in range(216)]
        cross = next(m for m, v in enumerate(flood) if v / size < 0.05)
        grp("log_flood", flood, lambda g: (g.fire(cross + 10, s, "F_regress", cls="page"), g.quiet(0, 119, s, cls="page")))
        # a volume sitting at 8% free dips under 5% for two minutes (a compaction's scratch files): ADR 0012 pages
        # at "under 5% free for 5 minutes", so the page's `for` (anything from 2m) stays quiet; no `for` pages
        dip = [int(size * (0.03 if 90 <= m < 92 else 0.08)) for m in range(151)]
        grp("short_dip", dip, lambda g: g.quiet(0, 150, s, cls="page"))
        e = outcome_entry(self, w, {s: svc_class(w, s, "ticket")})
        e["outcome"]["classes"] = {"ticket": svc_class(w, s, "ticket"), "page": svc_class(w, s, "page")}
        e["outcome"]["expected_count"] = 2
        return e, groups

    def variants(self, w):
        sel = self.p["sel"]
        pl = lambda win: f"predict_linear(node_filesystem_avail_bytes{sel}[{win}], 4 * 3600) < 0"  # noqa: E731
        lvl = f"node_filesystem_avail_bytes{sel} / node_filesystem_size_bytes{sel} < 0.15"

        def ticket(win, for_):
            return lambda f, a: _set(f, self.ALERT, expr=f"{pl(win)} and {lvl}", **{"for": for_})

        def pred_only_6h(files, am):
            self.break_(files, am, w)
            _set(files, self.ALERT, expr=pl("6h"))

        def keep_page(files, am):
            for _, _, r in rules_named(files, self.ALERT):
                r["labels"]["severity"] = "page"

        return [("oracle", lambda f, a: None, "accept"), ("mixin_6h_for_1h", ticket("6h", "1h"), "accept"),
                ("ticket_for_2h", ticket("1h", "2h"), "accept"), ("ticket_for_150m", ticket("1h", "150m"), 0.5),
                ("guarded_prediction_still_pages", keep_page, 0.3), ("six_hour_window_only", pred_only_6h, 0.0),
                ("page_for_zero", drop_for([self.p["page"]]), 0.8),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h23(self, w, rng)
