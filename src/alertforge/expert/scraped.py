"""What Prometheus scrapes in every hidden scenario: the background world under each card's own series.

A scenario group used to hold `up` for every service and, beyond that, only the series its card needed, so a
service whose pods were up and serving had no request counters at all. A floor rule written to survive missing
series (`... or sum(up) * 0`, `absent(...)`, `unless`) read that as zero traffic and paged in every scenario where
the pods were up (afx-e1-s03, T1). No Prometheus shows pods that are up without their series, so the scenarios are
completed instead of the rules being bent. For every service with an `up` target in a group:

- app series per target: request counters for the kinds that serve requests (`http_requests_total` by code and
  method, `grpc_server_handled_total` by code and method) and every other per-pod family the repo's rules read about
  the service (request-duration histograms, DB pool gauges, queue depth, ...). Traffic flows while a target is up;
  a target that goes down or leaves discovery gets stale markers on the same scrape (as Prometheus writes them),
  and the service's traffic moves to the targets still serving;
- per-pod cAdvisor series (memory, CPU throttling) where the rules read them, for as long as the pod exists;
- the exporter series a card adds to the world (`Item.exporters`: a replica's lag, a consumer group, a volume)
  and the per-target gauges in `w.notes["background_series"]`;
- the ingress controller's request counters (`nginx_ingress_controller_requests`) for every HTTP service: the
  README says this Prometheus scrapes the apps and the ingress, so an error ratio read at the load balancer sees
  what the app's own counters saw (status = code, minute by minute), and while no target of the service is up,
  the 502s (targets down) or 503s (no targets left) the controller answers to the requests clients keep sending.

A family the group already has for a service is left alone: the card's own series and their traffic win. Where a
card's pods carry one family, the missing ones go on the same pods and agree with it (a request counter adds up to
the histogram's count). Values are healthy (no rule in the repo fires on them) and come from their own random stream
(task seed, resample, group name), so the scenario stream, the workspace and every series a card draws stay as they
were. A group can declare a service it leaves without app series on purpose (`G.bare`); `lint` checks the rest.
"""

from __future__ import annotations

import json
import os
import random
import re
from contextlib import contextmanager

from ..promsim import STALE
from . import xscen
from .vocab import public_zone
from .xscen import HIST_LES, Raw, dec, pod_name, rs_hash

SERVES = {"api": "http", "worker": "http", "latency": "http", "grpc": "grpc"}   # kinds that serve requests
FAMILY = {"http_requests_total": "http", "grpc_server_handled_total": "grpc",
          "http_request_duration_seconds_bucket": "hist", "http_request_duration_seconds_count": "hist",
          "http_request_duration_seconds_sum": "hist",
          "db_pool_connections_active": "dbpool", "db_pool_connections_max": "dbpool",
          "db_client_connection_errors_total": "dbclient", "work_queue_depth": "queue",
          "export_last_success_timestamp_seconds": "export",
          "container_memory_working_set_bytes": "mem", "container_spec_memory_limit_bytes": "mem",
          "container_cpu_cfs_periods_total": "cpu", "container_cpu_cfs_throttled_periods_total": "cpu"}
CADVISOR = {"mem", "cpu"}
PER_SERIES = {"le", "code", "method", "handler", "grpc_code", "grpc_method", "grpc_service", "grpc_type", "container"}
E_SHARE = 0.0002      # 5xx (or gRPC Unavailable) share of background traffic, far under every burn threshold
NOT_FOUND = 0.03      # 4xx share, as the cards' own request counters carry
# healthy request durations (cumulative share per house bucket): p99 about 0.2s, nothing near a latency objective
LATENCY = {"0.05": 0.40, "0.1": 0.96, "0.25": 0.999, "0.5": 0.9999, "1.0": 1.0, "2.5": 1.0, "+Inf": 1.0}
# a saturated service (exercise scenarios only): 40% of requests past the top finite bucket
LATENCY_HOT = {"0.05": 0.05, "0.1": 0.10, "0.25": 0.20, "0.5": 0.30, "1.0": 0.40, "2.5": 0.60, "+Inf": 1.0}
CPU_PERIODS = 600     # CFS periods per minute (100ms period), ~2% of them throttled

SCRAPE_SEED = 0x5C2A
INGRESS = "nginx_ingress_controller_requests"


def _num(v) -> bool:
    return v is not None and v != STALE and not isinstance(v, bool)


# ---------------------------------------------------------------------------- what the rules read

_STR = re.compile(r'"(?:[^"\\]|\\.)*"')
_SEL = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{([^{}]*)\}")
_MATCHER = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!=|!~|=)\s*"((?:[^"\\]|\\.)*)"')
_IDENT = re.compile(r"(?<![0-9.a-zA-Z_:])[a-zA-Z_:][a-zA-Z0-9_:]*")
_CLAUSE = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)")
_WORDS = {"and", "or", "unless", "bool", "offset", "inf", "nan", "Inf", "NaN"}


def selectors(expr: str) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """(metric, [(label, op, value)]) for every vector selector of a generated rule expression."""
    out = [(m.group(1), _MATCHER.findall(m.group(2))) for m in _SEL.finditer(expr)]
    rest = _STR.sub(" ", _SEL.sub(" ", expr))
    rest = _CLAUSE.sub(" ", re.sub(r"\[[^\]]*\]", " ", rest))
    for m in _IDENT.finditer(rest):
        if m.group(0) not in _WORDS and not rest[m.end():].lstrip().startswith("("):
            out.append((m.group(0), []))
    return out


def _services(matchers, names) -> list[str] | None:
    hit = None
    for lab, op, val in matchers:
        if lab != "service" or op not in ("=", "=~"):
            continue
        ok = [n for n in names if (n == val if op == "=" else re.fullmatch(val, n))]
        hit = ok if hit is None else [n for n in hit if n in ok]
    return hit


def reads(states, names: list[str]) -> dict[str, set[str]]:
    """service -> every metric a rule reads about it, in any of the given repo states (records followed: a burn alert
    on `slo:sli_error:ratio_rate1h{service="x"}` reads x's request counters)."""
    out: dict[str, set[str]] = {n: set() for n in names}
    for files in states:
        rules = [r for f in files.values() for g in f.groups for r in g["rules"]]
        rec: dict[str, list[str]] = {}
        for r in rules:
            if "record" in r:
                rec.setdefault(r["record"], []).append(str(r.get("expr", "")))

        def walk(expr, outer, depth=0):
            for metric, ms in selectors(expr):
                svcs = _services(ms, names)
                svcs = outer if svcs is None else svcs
                if metric in rec and depth < 6:
                    for e in rec[metric]:
                        walk(e, svcs, depth + 1)
                else:
                    for s in svcs:
                        out[s].add(metric)

        for r in rules:
            lab = r.get("labels") if isinstance(r.get("labels"), dict) else {}
            walk(str(r.get("expr", "")), [lab["service"]] if lab.get("service") in out else [])
    return out


# ---------------------------------------------------------------------------- the plan of a task's world

class Plan:
    """Which series every service of a world exports, and the filler that puts them into a scenario group."""

    def __init__(self, w, items, states, seed: str):
        self.w, self.seed = w, seed
        self.svcs = {s.name: s for s in w.services}
        rd = reads(states, list(self.svcs))
        # the request family comes from the kind (one SLI record name is defined once per family, so the rules
        # alone would hand every service both); the other families from what the rules read about the service
        self.need = {n: ({SERVES[s.kind]} if s.kind in SERVES else set())
                     | {FAMILY[m] for m in rd[n] if FAMILY.get(m) not in (None, "http", "grpc")}
                     for n, s in self.svcs.items()}
        self.templates = [dict(t) for it in items for t in it.exporters(w)]
        for e in w.notes.get("background_series", []):
            if e.get("service") in self.svcs:   # a per-target gauge (H10): scraped while its service's targets are
                self.templates.append({"service": e["service"], "metric": e["metric"], "labels": dict(e["labels"]),
                                       "key": ("service",), "values": ("const", e["value"]), "follow": True})
        covered = {t["metric"] for t in self.templates} | set(FAMILY) | {"up", INGRESS}
        self.uncovered = {n: sorted(m for m in rd[n] if m not in covered and not m.startswith("ALERTS"))
                          for n in self.svcs}
        # the ingress controller in front of the HTTP services: its own random stream, drawn once per task
        crng = random.Random(f"{w.tseed}|ingress-nginx")
        self.ingress = {"job": "ingress-nginx", "controller_class": "k8s.io/ingress-nginx",
                        "controller_namespace": "ingress-nginx",
                        "controller_pod": pod_name(crng, "ingress-nginx-controller", rs_hash(crng)),
                        "instance": f"10.{crng.randint(16, 31)}.0.{crng.randint(2, 99)}:10254"}
        self.zone = public_zone(w.domain)

    def rate(self, s) -> float:
        """Requests per second: the service's own, at least five times a catalog floor."""
        return max(float(s.rps), 5 * float(s.floor)) if s.floor else float(s.rps)

    def saturate(self, svc, targets, n, rng) -> list[Raw]:
        """The service's own per-pod families past every threshold its rules hold (slow requests, a full pool,
        memory at the limit, a backlog, a late export, throttling): exercise scenarios only, so that an untouched
        alert on them fires somewhere and a rewrite of it can be replayed. Its request counters stay healthy."""
        fams = sorted(self.need[svc] - {"http", "grpc"})
        if not fams:
            return []
        lanes = _lanes_from_targets(self.w, svc, targets, n, rng)
        _traffic(lanes, self.rate(self.svcs[svc]), n, rng)
        lim, pool = rng.choice([2, 4, 6]) * 1024 ** 3, rng.choice([20, 30, 50])
        return [r for lane in lanes for fam in fams
                for r in GEN[fam](lane, n, rng, svc=svc, ns=self.w.namespace, limit=lim, pool=pool, hot=True)]

    # ------------------------------------------------------------------ filler
    def complete(self, g) -> None:
        n, bare = g.minutes, g.extra.get("bare", {})
        ups: dict[str, list[Raw]] = {}
        for r in g.series:
            svc = r.labels.get("service")
            if r.metric == "up" and svc in self.svcs and r.labels.get("job") == svc:
                ups.setdefault(svc, []).append(r)
        add: list[Raw] = []
        for svc in [s for s in self.svcs if s in ups]:
            rng = random.Random(f"{self.seed}|{g.name}|{svc}")
            have = {FAMILY[r.metric] for r in g.series if r.labels.get("service") == svc and r.metric in FAMILY}
            missing = sorted(self.need[svc] - have - set(bare.get(svc, [])))
            if missing:
                add += self._app(svc, ups[svc], g.series, missing, n, rng)
            add += self._exporters(svc, ups[svc], g.series, n, rng)
        g.series += add
        g.series += self._ingress(g.name, ups, g.series, n)

    def _app(self, svc, targets, series, fams, n, rng) -> list[Raw]:
        s = self.svcs[svc]
        pods: dict[str, list[Raw]] = {}
        for r in series:
            lab = r.labels
            if (lab.get("service") == svc and "pod" in lab and "instance" in lab and r.metric != "up"
                    and not r.metric.startswith("container_") and lab.get("job", svc) == svc):
                pods.setdefault(lab["pod"], []).append(r)
        lanes = _lanes_from_pods(pods, n, rng) if pods else _lanes_from_targets(self.w, svc, targets, n, rng)
        _traffic(lanes, self.rate(s), n, rng)
        lim = rng.choice([2, 4, 6]) * 1024 ** 3
        pool = rng.choice([20, 30, 50])
        out: list[Raw] = []
        for lane in lanes:
            for fam in fams:
                out += GEN[fam](lane, n, rng, svc=svc, ns=self.w.namespace, limit=lim, pool=pool)
        return out

    def _exporters(self, svc, targets, series, n, rng) -> list[Raw]:
        out = []
        alive = [any(t.values[m] == 1 for t in targets) for m in range(n + 1)]
        gone = [any(t.values[m] in (0, STALE) for t in targets) for m in range(n + 1)]
        for t in self.templates:
            if t["service"] != svc:
                continue
            key = {k: t["labels"][k] for k in t["key"]}
            if any(r.metric == t["metric"] and all(r.labels.get(k) == v for k, v in key.items()) for r in series):
                continue
            vals = _values(t["values"], n, rng)
            if t.get("follow"):
                vals = _shape(vals, alive, gone)
            out.append(Raw(t["metric"], dict(t["labels"]), vals))
        return out


    def _ingress(self, gname, ups, series, n) -> list[Raw]:
        """nginx_ingress_controller_requests for every HTTP service with targets in the group. While a target
        serves, the controller counts what the app's own counters count (status = code, method = method); in a
        minute with no app sample and no target up, clients still call and the controller answers them itself: 502
        while targets exist but are down, 503 once none is left, at the rate the service had before."""
        out: list[Raw] = []
        for svc in [s for s in self.svcs if s in ups and SERVES.get(self.svcs[s].kind) == "http"]:
            app = [r for r in series if r.metric == "http_requests_total" and r.labels.get("service") == svc
                   and r.labels.get("job", svc) == svc]
            if not app:
                continue
            rng = random.Random(f"{self.seed}|{gname}|{svc}|ingress")
            inc: dict[tuple[str, str], list[float]] = {}
            served = [False] * (n + 1)
            for r in app:
                acc = inc.setdefault((str(r.labels.get("code", "")), str(r.labels.get("method", "GET"))),
                                     [0] * (n + 1))
                for m in range(n + 1):
                    served[m] = served[m] or _num(r.values[m])
                    if m and _num(r.values[m - 1]) and _num(r.values[m]):
                        a, b = r.values[m - 1], r.values[m]
                        acc[m] += b - a if b >= a else b
            total = [sum(v[m] for v in inc.values()) for m in range(n + 1)]
            before, nominal = [], self.rate(self.svcs[svc]) * 60
            for m in range(1, n + 1):
                if served[m] or any(t.values[m] == 1 for t in ups[svc]):
                    if served[m] and served[m - 1]:
                        before = (before + [total[m]])[-5:]
                    continue
                status = "502" if any(t.values[m] == 0 for t in ups[svc]) else "503"
                want = (sum(before) / len(before) if before else nominal) * rng.uniform(0.95, 1.05)
                inc.setdefault((status, "GET"), [0] * (n + 1))[m] += want
            base = {**self.ingress, "ingress": svc, "namespace": self.w.namespace, "service": svc,
                    "host": f"{svc}.{self.zone}", "path": "/"}
            for (code, method), xs in sorted(inc.items()):
                vals, v, carry, seen = [], None, 0.0, code in ("200", "404", "500")
                for m, x in enumerate(xs):
                    k = int(x + carry)
                    carry = x + carry - k
                    if v is None and (seen or k):
                        v = (rng.randint(10 ** 5, 10 ** 6) if code == "200" else
                             rng.randint(200, 4000) if seen else 0)
                    if v is not None:
                        v += k if m else 0
                    vals.append(v)
                out.append(Raw(INGRESS, {**base, "method": method, "status": code}, vals))
        return out


# ---------------------------------------------------------------------------- lanes: one pod each

def _lanes_from_targets(w, svc, targets, n, rng) -> list[dict]:
    """One pod per `up` target, in target order (unify_targets pairs the k-th pod with the k-th target). A target
    serves while it is up; a gap in discovery inside its life (an SD flap) does not stop the pod from serving."""
    rs = rs_hash(rng)
    lanes = []
    for t in targets:
        up = t.values
        scraped = [up[m] == 1 for m in range(n + 1)]
        on = [m for m in range(n + 1) if scraped[m]]
        lo, hi = (on[0], on[-1]) if on else (n + 1, -1)
        serving = [scraped[m] or (lo <= m <= hi and up[m] != 0) for m in range(n + 1)]
        exists = [up[m] in (0, 1) or (lo <= m <= hi) for m in range(n + 1)]
        lanes.append({"labels": {"service": svc, "namespace": w.namespace, "pod": pod_name(rng, svc, rs),
                                 "instance": t.labels.get("instance", ""), "job": svc},
                      "scraped": scraped, "stale": [up[m] in (0, STALE) for m in range(n + 1)], "serving": serving,
                      "exists": exists, "fresh": lo > 0, "inc": None})
    return lanes


def _lanes_from_pods(pods: dict, n, rng) -> list[dict]:
    """The card's own pods: same labels, sampled when the card's series are, traffic read off their counters."""
    lanes = []
    for pod, rs in pods.items():
        common = dict(rs[0].labels)
        for r in rs[1:]:
            common = {k: v for k, v in common.items() if r.labels.get(k) == v}
        base = {k: v for k, v in common.items() if k not in PER_SERIES}
        scraped = [any(_num(r.values[m]) for r in rs) for m in range(n + 1)]
        stale = [not scraped[m] and any(r.values[m] == STALE for r in rs) for m in range(n + 1)]
        src = ([r for r in rs if r.metric in ("http_requests_total", "grpc_server_handled_total")]
               or [r for r in rs if r.metric == "http_request_duration_seconds_count"])
        inc = None
        if src:
            inc = [0] * (n + 1)
            for r in src:
                for m in range(1, n + 1):
                    a, b = r.values[m - 1], r.values[m]
                    if _num(a) and _num(b):
                        inc[m] += b - a if b >= a else b
        on = [m for m in range(n + 1) if scraped[m]]
        lanes.append({"labels": base, "scraped": scraped, "stale": stale, "serving": scraped, "exists": scraped,
                      "fresh": bool(on) and on[0] > 0, "inc": inc})
    return lanes


def _traffic(lanes, rps, n, rng) -> None:
    """Integer requests per minute per lane: the service's rate split over the lanes serving that minute (lanes
    whose counters the card drew keep their own counts)."""
    wts = [rng.uniform(0.8, 1.2) for _ in lanes]
    tot = [rps * 60 * rng.uniform(0.95, 1.05) for _ in range(n + 1)]
    carry = [0.0] * len(lanes)
    for k, lane in enumerate(lanes):
        if lane["inc"] is None:
            lane["inc"] = [0] * (n + 1)
            lane["own"] = False
        else:
            lane["own"] = True
    for m in range(1, n + 1):
        live = [k for k, lane in enumerate(lanes) if not lane["own"] and lane["serving"][m]]
        ws = sum(wts[k] for k in live)
        for k in live:
            x = tot[m] * wts[k] / ws + carry[k]
            lanes[k]["inc"][m] = int(x)
            carry[k] = x - int(x)


def _shape(vals: list, scraped: list, stale: list) -> list:
    """A lane's values where it was scraped, a stale marker on the scrape its target went away, nothing else."""
    out, prev = [], False
    for m, v in enumerate(vals):
        if scraped[m]:
            out.append(v)
        elif prev and stale[m]:
            out.append(STALE)
        else:
            out.append(None)
        prev = scraped[m]
    return out


def _counter(start: int, incs: list[int]) -> list[int]:
    out, v = [], start
    for m, x in enumerate(incs):
        v += x if m else 0
        out.append(v)
    return out


def _split(incs: list[int], shares: list[float]) -> list[list[int]]:
    """Each minute's count split exactly: every code but the first takes its share with a carry (never negative,
    so every code's counter only grows), the first code takes the rest."""
    parts = [[0] * len(incs) for _ in shares]
    carry = [0.0] * len(shares)
    for m, t in enumerate(incs):
        left = t
        for j in range(1, len(shares)):
            x = t * shares[j] + carry[j]
            got = min(int(x), left)
            carry[j] = x - got
            parts[j][m] = got
            left -= got
        parts[0][m] = left
    return parts


def _counters(lane, metric, codes: list[tuple[dict, float, tuple[int, int]]], rng) -> list[Raw]:
    parts = _split(lane["inc"], [sh for _, sh, _ in codes])
    out = []
    for (lab, _, (lo, hi)), incs in zip(codes, parts):
        start = 0 if lane["fresh"] else rng.randint(lo, hi)
        out.append(Raw(metric, {**lane["labels"], **lab}, _shape(_counter(start, incs), lane["scraped"], lane["stale"])))
    return out


# ---------------------------------------------------------------------------- generators per family

def _http(lane, n, rng, **_):
    ok = (1 - E_SHARE) * (1 - NOT_FOUND)
    return _counters(lane, "http_requests_total", [({"code": "200", "method": "GET"}, ok, (10 ** 4, 10 ** 6)),
                                                   ({"code": "404", "method": "GET"}, (1 - E_SHARE) * NOT_FOUND, (200, 900)),
                                                   ({"code": "500", "method": "GET"}, E_SHARE, (50, 900))], rng)


def _grpc(lane, n, rng, **_):
    ok = (1 - E_SHARE) * (1 - NOT_FOUND)
    return _counters(lane, "grpc_server_handled_total",
                     [({"grpc_code": "OK", "grpc_method": "Get"}, ok, (10 ** 4, 10 ** 6)),
                      ({"grpc_code": "NotFound", "grpc_method": "Get"}, (1 - E_SHARE) * NOT_FOUND, (200, 900)),
                      ({"grpc_code": "Unavailable", "grpc_method": "Get"}, E_SHARE, (50, 900))], rng)


def _hist(lane, n, rng, hot=False, **_):
    """Buckets as floor(requests so far x cumulative share): every bucket only grows, never holds more than the next
    one up, and `+Inf` (and `_count`) is exactly the requests the lane's counters saw."""
    shares = LATENCY_HOT if hot else LATENCY
    total, acc = [], 0
    for m, x in enumerate(lane["inc"]):
        acc += x if m else 0
        total.append(acc)
    off = 0 if lane["fresh"] else rng.randint(10 ** 4, 10 ** 5)
    out = []
    for le in HIST_LES:
        vals = [off + (t if le == "+Inf" else int(t * shares[le])) for t in total]
        out.append(Raw("http_request_duration_seconds_bucket", {**lane["labels"], "le": le},
                       _shape(vals, lane["scraped"], lane["stale"])))
        off += 0 if lane["fresh"] else rng.randint(0, 40)
    out.append(Raw("http_request_duration_seconds_count", dict(lane["labels"]), list(out[-1].values)))
    return out


def _gauge(lane, metric, vals):
    return Raw(metric, dict(lane["labels"]), _shape(vals, lane["scraped"], lane["stale"]))


def _dbpool(lane, n, rng, pool=30, hot=False, **_):
    act = [pool if hot else rng.randint(2, max(3, pool // 4)) for _ in range(n + 1)]
    return [_gauge(lane, "db_pool_connections_active", act), _gauge(lane, "db_pool_connections_max", [pool] * (n + 1))]


def _dbclient(lane, n, rng, hot=False, **_):
    start = rng.randint(0, 40)
    return [_gauge(lane, "db_client_connection_errors_total", [start + (120 * m if hot else 0) for m in range(n + 1)])]


def _queue(lane, n, rng, hot=False, **_):
    return [_gauge(lane, "work_queue_depth", [rng.randint(20, 400) + (9000 if hot else 0) for _ in range(n + 1)])]


def _export(lane, n, rng, hot=False, **_):
    every = rng.choice([15, 20, 30])   # the last export finished on the job's schedule (four hours ago when hot)
    vals = [-4 * 3600] * (n + 1) if hot else [60 * (m - m % every) for m in range(n + 1)]
    return [_gauge(lane, "export_last_success_timestamp_seconds", vals)]


def _cadvisor(lane, svc, ns):
    pod = lane["labels"].get("pod") or f"{svc}-0"
    return {"service": svc, "container": svc, "namespace": ns, "pod": pod}


def _mem(lane, n, rng, svc="", ns="", limit=2 * 1024 ** 3, hot=False, **_):
    lab, base = _cadvisor(lane, svc, ns), rng.uniform(0.45, 0.65)
    base = 0.97 if hot else base
    ws = [int(limit * base * (1 + rng.uniform(-0.002, 0.002))) for _ in range(n + 1)]
    ex = lane["exists"]
    gone = [not ex[m] for m in range(n + 1)]
    return [Raw("container_memory_working_set_bytes", lab, _shape(ws, ex, gone)),
            Raw("container_spec_memory_limit_bytes", dict(lab), _shape([limit] * (n + 1), ex, gone))]


def _cpu(lane, n, rng, svc="", ns="", hot=False, **_):
    lab = _cadvisor(lane, svc, ns)
    per = [0] + [CPU_PERIODS + rng.randint(-5, 5) for _ in range(n)]
    thr = [0] + [rng.randint(5, 18) + (360 if hot else 0) for _ in range(n)]
    ex = lane["exists"]
    gone = [not ex[m] for m in range(n + 1)]
    start = rng.randint(10 ** 5, 10 ** 6)
    return [Raw("container_cpu_cfs_periods_total", lab, _shape(_counter(start, per), ex, gone)),
            Raw("container_cpu_cfs_throttled_periods_total", dict(lab), _shape(_counter(start // 50, thr), ex, gone))]


GEN = {"http": _http, "grpc": _grpc, "hist": _hist, "dbpool": _dbpool, "dbclient": _dbclient, "queue": _queue,
       "export": _export, "mem": _mem, "cpu": _cpu}


def _values(spec, n, rng) -> list:
    kind = spec[0]
    if kind == "const":
        return [spec[1]] * (n + 1)
    if kind == "ints":
        return [rng.randint(spec[1], spec[2]) for _ in range(n + 1)]
    if kind == "counter":   # (counter, low, high): per-second rate drawn once, a minute's increase around it
        r = rng.uniform(spec[1], spec[2])
        return _counter(rng.randint(10 ** 6, 10 ** 7), [0] + [int(r * 60 * rng.uniform(0.9, 1.1)) for _ in range(n)])
    raise ValueError(spec)


@contextmanager
def active(plan: Plan):
    """Complete every scenario group drawn inside the block (xscen.G.done calls the plan)."""
    prev = xscen.SCRAPE
    xscen.SCRAPE = plan.complete
    try:
        yield plan
    finally:
        xscen.SCRAPE = prev
