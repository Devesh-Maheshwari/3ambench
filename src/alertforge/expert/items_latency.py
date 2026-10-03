"""E4 latency tickets.

H11: a latency SLI on the old layout's boundary (`le="0.25"`) while a rollout runs a second bucket layout without
that boundary: v42 pods count as all-bad and the canary pages on latency budget (sp4, sp17). Checks are value
probes at points where every accepted reading (the union of the per-layout boundaries, or the per-version form)
agrees.
H12: a latency page written as a p99 over an objective above the top finite bucket (p07): the quantile is capped
at that bucket and the page never fires."""

from __future__ import annotations

import random
from fractions import Fraction as Fr

from .. import promsim as ps
from . import prose_latency as prose
from .items import Item, outcome_entry, svc_class
from .items_svc import BLIP_BOUND, drop_for
from .model import RuleFileSpec, rules_named
from .xscen import G, Raw, background, ip, pod_name

# `le` in the canonical float form Prometheus 3 stores (S6): "1.0", not "1"
LAYOUTS = {"v41": ["0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "+Inf"],
           "v42": ["0.05", "0.1", "0.2", "0.35", "0.5", "1.0", "+Inf"]}
# healthy latency mass per bucket interval (upper bound -> share), sp17's tail
HEALTHY = [(0.05, 0.40), (0.1, 0.56), (0.2, 0.035), (0.25, 0.004), (0.35, 0.0006), (0.5, 0.0003), (1.0, 0.0001)]
WINDOWS = ["5m", "1h"]


def _shares(slow_at: float | None) -> list[tuple[float, float]]:
    return [(0.4, 1.0)] if slow_at else HEALTHY


def _hist(rng, labels: dict, layout: str, n: int, rps: float, shares) -> list[Raw]:
    les = LAYOUTS[layout]
    per = []
    for le in les:
        bound = float("inf") if le == "+Inf" else float(le)
        per.append(sum(sh for ub, sh in shares if ub <= bound + 1e-12))
    out, off = [], rng.randint(10**5, 10**6)
    tot_inc = [0] + [int(rps * 60 * rng.uniform(0.97, 1.03)) for _ in range(n)]
    cum_tot = []
    acc = 0
    for x in tot_inc:
        acc += x
        cum_tot.append(acc)
    for le, frac in zip(les, per):
        vals = [off + int(c * frac) for c in cum_tot]
        out.append(Raw("http_request_duration_seconds_bucket", {**labels, "le": le}, vals))
    out.append(Raw("http_request_duration_seconds_count", dict(labels), [off + c for c in cum_tot]))
    return out


class LatencyBuckets(Item):
    code, kind, ticket_like = "H11", "recording", True

    def setup(self, w, rng):
        cands = [s for s in w.services if s.name in w.notes.get("latency_services", [])]
        if not cands:
            return False
        s = cands[0]
        self.p.update(svc=s.name, objective="0.3", old="v41", new="v42")
        self.names = {f"slo:sli_latency:ratio_rate{x}" for x in WINDOWS}
        self.touch = set(self.names)
        stem = f"slo-{s.name}"
        # the page the canary tripped: a fast burn on the latency SLI (99% in 7d, ADR 0007's 3.36x over 1h and 5m)
        burn = f"{s.camel}LatencyBudgetBurnFast"
        sel = f'{{service="{s.name}"}}'
        page = {"alert": burn,
                "expr": (f"slo:sli_latency:ratio_rate1h{sel} > (3.36 * 0.01)\n"
                         f"and slo:sli_latency:ratio_rate5m{sel} > (3.36 * 0.01)"),
                "for": "2m", "labels": {"severity": "page", "team": s.team, "slo": f"{s.name}-latency"},
                "annotations": {"summary": "{{ $labels.service }} is spending its latency budget fast",
                                "runbook_url": w.runbook(burn)}}
        w.files[stem] = RuleFileSpec(stem, 2, [{"name": f"{s.name}-latency-sli", "rules": [
            {"record": f"slo:sli_latency:ratio_rate{win}", "expr": self._expr(win, 'le=~"0.25|0.2"')} for win in WINDOWS]
            + [page]}],
            [f"{s.name} latency SLI (docs/slo/{s.name}.md)."])
        return True

    def _expr(self, win, le_sel):
        s = self.p["svc"]
        return (f'1 - (sum by (service) (rate(http_request_duration_seconds_bucket{{service="{s}",{le_sel}}}[{win}]))'
                f' / sum by (service) (rate(http_request_duration_seconds_count{{service="{s}"}}[{win}])))')

    def break_(self, files, am, w):
        for win in WINDOWS:
            for _, _, r in rules_named(files, f"slo:sli_latency:ratio_rate{win}", "record"):
                r["expr"] = self._expr(win, 'le="0.25"')

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        n, groups = 70, []

        def grp(name, slow_old, slow_new):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            raws = []
            for ver, slow in (("v41", slow_old), ("v42", slow_new)):
                for i in range(2):
                    lab = {"service": s.name, "namespace": w.namespace, "pod": pod_name(rng, s.name),
                           "instance": f"{ip(rng)}:8080", "version": ver}
                    raws += _hist(rng, lab, ver, n, s.rps / 4, _shares(slow))
            g.series += raws
            for t in (n - 5, n):
                for win in WINDOWS:
                    v = self._expected(raws, t, int(win[:-1]) * (60 if win.endswith("h") else 1))
                    tol = max(1e-9, abs(v) * 1e-6)
                    g.count(f'count(abs(slo:sli_latency:ratio_rate{win}{{service="{s.name}"}} - {v!r}) <= {tol!r}) or vector(0)',
                            t, 1, "value")
            groups.append(g.done())

        grp("healthy_mixed", None, None)
        grp("new_slow", None, 0.4)
        grp("old_slow", 0.4, None)
        return self.entry(records={"names": sorted(self.names)}), groups

    @staticmethod
    def _expected(raws, t, wmin) -> float:
        good = Fr(0)
        tot = Fr(0)
        for r in raws:
            rate = ps.extrapolated(r.values, t, wmin)
            if rate is None:
                continue
            if r.metric.endswith("_count"):
                tot += rate
            elif (r.labels["version"], r.labels["le"]) in (("v41", "0.25"), ("v42", "0.2")):
                good += rate
        return float(1 - good / tot)

    def variants(self, w):
        def le(sel):
            def f(files, am):
                for win in WINDOWS:
                    for _, _, r in rules_named(files, f"slo:sli_latency:ratio_rate{win}", "record"):
                        r["expr"] = self._expr(win, sel)
            return f

        def per_version(files, am):
            s = self.p["svc"]
            for win in WINDOWS:
                for _, _, r in rules_named(files, f"slo:sli_latency:ratio_rate{win}", "record"):
                    b = "http_request_duration_seconds_bucket"
                    r["expr"] = (f'1 - (sum by (service) (rate({b}{{service="{s}",le="0.25",version="v41"}}[{win}]) or '
                                 f'rate({b}{{service="{s}",le="0.2",version="v42"}}[{win}])) / sum by (service) '
                                 f'(rate(http_request_duration_seconds_count{{service="{s}"}}[{win}])))')
        return [("oracle", lambda f, a: None, "accept"), ("per_version", per_version, "accept"),
                ("le_0_1", le('le="0.1"'), 0.0), ("le_0_5", le('le="0.5"'), 0.4), ("le_0_3_missing_bucket", le('le="0.3"'), 0.0),
                ("pristine", lambda f, a: self.break_(f, a, w), 0.34)]

    def ticket(self, w, rng):
        return prose.h11(self, w, rng)


# ---------------------------------------------------------------------------- H12

# cumulative share of the requests that finish fast, per bucket of the house layout (HIST_LES); the rest of a
# minute's requests (the slow share) land only in +Inf
FAST_CUM = {"0.05": 0.40, "0.1": 0.90, "0.25": 0.97, "0.5": 0.99, "1.0": 0.996, "2.5": 1.0}
TOP = "2.5"


def slow_hist(rng, labels: dict, n: int, rps: float, slow: list[float]) -> list[Raw]:
    """Request-duration histogram (house layout) where `slow[m]` of minute m's requests take far longer than the
    top finite bucket."""
    tot = [0] + [int(rps * 60 * rng.uniform(0.97, 1.03)) for _ in range(n)]
    slow_n = [int(round(t * f)) for t, f in zip(tot, slow)]
    off = rng.randint(10**5, 10**6)
    out = []
    for le in [*FAST_CUM, "+Inf"]:
        acc, vals = off, []
        for t, sn in zip(tot, slow_n):
            acc += t if le == "+Inf" else int((t - sn) * FAST_CUM[le])
            vals.append(acc)
        out.append(Raw("http_request_duration_seconds_bucket", {**labels, "le": le}, vals))
    out.append(Raw("http_request_duration_seconds_count", dict(labels), list(out[-1].values)))
    return out


class QuantileCap(Item):
    """H12: a latency page written as `histogram_quantile(0.99, ...) > 5` on a histogram whose highest finite bucket
    is 2.5s. The quantile is capped at 2.5, so the page can't fire however slow the tail gets (p07). ADR 0007: a
    latency objective is the fraction of requests slower than it, on the nearest boundary without going over."""
    code, kind, ticket_like = "H12", "outcome", True

    def setup(self, w, rng):
        busy = w.notes.setdefault("busy", set())
        cands = [s for s in w.services if s.kind == "api" and s.family == "http" and s.name not in busy]
        if not cands:
            return False
        s = rng.choice(cands)
        busy.add(s.name)
        name = f"{s.camel}SlowRequests"
        self.p.update(svc=s.name, alert=name, timeout="5", share="0.01")
        from .model import drop_rules, home_group
        drop_rules(w.files, {f"{s.camel}LatencyHigh"})   # this page is the service's latency alerting
        home_group(w, s)["rules"].append({
            "alert": name, "expr": self._frac(s.name, "5m"), "for": "10m", "labels": {"severity": "page", "team": s.team},
            "annotations": {"summary": "{{ $labels.service }}: over 1% of requests take longer than 5s",
                            "runbook_url": w.runbook(name)}})
        from .prose_latency import h12_runbook
        w.runbooks[name] = {"file": f"{s.name}-slow-requests.md", "alerts": [name], "card": "H12", "body": lambda: h12_runbook(s, name, w)}
        self.names = self.touch = {name}
        return True

    def _frac(self, svc, win, le=TOP, share="0.01"):
        b = "http_request_duration_seconds"
        return (f'(1 - sum by (service) (rate({b}_bucket{{service="{svc}",le="{le}"}}[{win}]))'
                f' / sum by (service) (rate({b}_count{{service="{svc}"}}[{win}]))) > {share}')

    def _quantile(self, svc, thr):
        return (f'histogram_quantile(0.99, sum by (service, le) (rate(http_request_duration_seconds_bucket'
                f'{{service="{svc}"}}[5m]))) > {thr}')

    def break_(self, files, am, w):
        for _, _, r in rules_named(files, self.p["alert"]):
            r["expr"] = self._quantile(self.p["svc"], self.p["timeout"])

    def spec(self, w, rng):
        s = w.svc(self.p["svc"])
        n, groups, on = 45, [], 15

        def grp(name, slow, fam):
            g = G(self.rid, name, n)
            g.series += background(w, rng, n)
            for _ in range(2):
                lab = {"service": s.name, "namespace": w.namespace, "pod": pod_name(rng, s.name), "instance": f"{ip(rng)}:8080",
                       "job": s.name}
                g.series += slow_hist(rng, lab, n, s.rps / 2, slow)
            fam(g)
            g.labelless()
            groups.append(g.done())

        tail = lambda f: [f if m >= on else 0.001 for m in range(n + 1)]  # noqa: E731
        # window + `for` + 5m from the runbook's 10 minutes on a 5m window
        grp("slow_tail", tail(0.03), lambda g: (g.fire(on + 20, s.name), g.quiet(0, on - 1, s.name)))
        # the milder detect: a tail just over the 1% the runbook names
        grp("small_tail", tail(0.016), lambda g: g.fire(on + 20, s.name))
        grp("bracket_low", tail(0.008), lambda g: g.quiet(0, n, s.name))
        grp("normal", [0.001] * (n + 1), lambda g: g.quiet(0, n, s.name))
        # two minutes of 10% slow requests (a cache flush): never 10 minutes over 1% on a 5m or 10m window
        grp("blip", [0.10 if 20 <= m < 22 else 0.001 for m in range(n + 1)], lambda g: g.quiet(0, n, s.name))
        return outcome_entry(self, w, {s.name: svc_class(w, s.name)}), groups

    def variants(self, w):
        svc, a = self.p["svc"], self.p["alert"]
        b = "http_request_duration_seconds_bucket"

        def expr(e, for_="10m"):
            def f(files, am):
                for _, _, r in rules_named(files, a):
                    r["expr"], r["for"] = e, for_
            return f

        inf_minus = (f'(sum by (service) (rate({b}{{service="{svc}",le="+Inf"}}[5m])) - sum by (service) '
                     f'(rate({b}{{service="{svc}",le="{TOP}"}}[5m]))) / sum by (service) (rate({b}{{service="{svc}",le="+Inf"}}[5m])) > 0.01')
        return [("oracle", lambda f, x: None, "accept"),
                ("inf_minus_top_bucket", expr(inf_minus), "accept"),
                ("window_10m", expr(self._frac(svc, "10m")), "accept"),
                ("quantile_at_top_bucket", expr(self._quantile(svc, TOP)), 0.0),
                ("over_2_percent", expr(self._frac(svc, "5m", share="0.02")), 0.5),
                ("boundary_1s", expr(self._frac(svc, "5m", le="1.0")), 0.75),
                ("for_zero", drop_for(self.touch), BLIP_BOUND),
                ("pristine", lambda f, x: self.break_(f, x, w), 0.0)]

    def ticket(self, w, rng):
        return prose.h12(self, w, rng)
