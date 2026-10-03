"""Scenario helpers for expert checks: series encoding (ints, floats, gaps, stale markers), the healthy
background every scenario carries, and probe/group builders.

Probe families: F_detect / F_regress (a notification of the class must be delivered at t), S_defect / S_std
(nothing counted in [t0, t]), storm (1-minute snapshots), G (label-less pages over the whole scenario),
fire / silent / timing / dyn (named alerts, core style), annot, value.
"""

from __future__ import annotations

import random
import re

from ..promsim import STALE
from .evidence import canonical_le, le_is_canonical

# canonical float strings: Prometheus 3 stores `le="1"` as "1.0" on ingestion (S6)
HIST_LES = ["0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "+Inf"]


def fmt_num(v) -> str:
    if isinstance(v, int):
        return str(v)
    s = f"{float(v):.6g}"
    return s


_NUM = r"-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?"
_TOKEN = re.compile(rf"^(?P<a>{_NUM}|_)(?:(?P<op>[+-])(?P<b>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?))?(?:x(?P<n>\d+))?$")


def dec(text: str) -> list:
    """The values `enc` writes, read back (int where the notation is integral, None for `_`, STALE)."""
    out: list = []
    for tok in text.split():
        if tok == "stale":
            out.append(STALE)
            continue
        m = _TOKEN.match(tok)
        if m is None:
            raise ValueError(f"bad series token {tok!r}")
        a, op, b, k = m.group("a"), m.group("op"), m.group("b"), int(m.group("n") or 0)
        if a == "_":
            out += [None] * (k or 1)
            continue
        num = float(a) if any(c in a for c in ".eE") else int(a)
        step = 0 if b is None else (float(b) if any(c in b for c in ".eE") else int(b))
        step = -step if op == "-" else step
        out += [num + step * i for i in range(k + 1)]
    return out


def enc(values: list) -> str:
    """promtool input notation; constant runs as `vxN`, integer steps as `a+bxN`."""
    out, i, n = [], 0, len(values)
    while i < n:
        v = values[i]
        if v is None or v == STALE:
            j = i
            while j + 1 < n and values[j + 1] == v:
                j += 1
            k = j - i + 1
            out.append(("_" if k == 1 else f"_x{k}") if v is None else " ".join(["stale"] * k))
            i = j + 1
            continue
        j = i
        if isinstance(v, int) and i + 1 < n and isinstance(values[i + 1], int) and not isinstance(values[i + 1], bool):
            d = values[i + 1] - v
            while j + 1 < n and isinstance(values[j + 1], int) and values[j + 1] - values[j] == d:
                j += 1
            if j > i:
                out.append(f"{v}{'+' if d >= 0 else '-'}{abs(d)}x{j - i}")
                i = j + 1
                continue
        while j + 1 < n and values[j + 1] == v and not isinstance(values[j + 1], bool):
            j += 1
        out.append(fmt_num(v) if j == i else f"{fmt_num(v)}x{j - i}")
        i = j + 1
    return " ".join(out)


class Raw:
    """One input series with its per-minute values (int, float, None for a gap, STALE)."""

    def __init__(self, metric: str, labels: dict, values: list, bg: bool = False):
        self.metric, self.labels, self.values = metric, dict(labels), list(values)
        self.bg = bg   # a background `up` (healthy for the whole scenario), free to follow its pod's lifetime

    def encoded(self) -> dict:
        if "le" in self.labels and not le_is_canonical(self.labels["le"]):
            raise ValueError(f"non-canonical le={self.labels['le']!r} on {self.metric}: Prometheus 3 stores it as "
                             f"{canonical_le(self.labels['le'])!r}")
        inner = ",".join(f'{k}="{v}"' for k, v in sorted(self.labels.items()))
        return {"series": f"{self.metric}{{{inner}}}", "values": enc(self.values)}


def series(metric: str, labels: dict, values: list) -> Raw:
    return Raw(metric, labels, values)


SAFE = "bcdfghjklmnpqrstvwxz2456789"


def rs_hash(rng: random.Random) -> str:
    """A ReplicaSet's pod-template-hash: every pod of one rollout of a Deployment carries the same one (R8)."""
    return "".join(rng.choice(SAFE) for _ in range(rng.choice([8, 9, 10])))


def pod_name(rng: random.Random, svc: str, rs: str | None = None) -> str:
    b = "".join(rng.choice(SAFE) for _ in range(5))
    return f"{svc}-{rs or rs_hash(rng)}-{b}"


def pods(rng: random.Random, svc: str, k: int, rs: str | None = None) -> list[str]:
    """`k` pods of one ReplicaSet of `svc`."""
    rs = rs or rs_hash(rng)
    out = []
    while len(out) < k:
        p = pod_name(rng, svc, rs)
        if p not in out:
            out.append(p)
    return out


def ip(rng: random.Random) -> str:
    return f"10.{rng.randint(16, 31)}.{rng.randint(0, 255)}.{rng.randint(2, 254)}"


def up_values(n: int, down: tuple[int, int] | None = None, vanish: int | None = None,
              back: int | None = None) -> list:
    v: list = [1] * (n + 1)
    if down:
        for m in range(down[0], min(down[1], n + 1)):
            v[m] = 0
    if vanish is not None:
        end = n + 1 if back is None else back
        for m in range(vanish, min(end, n + 1)):
            v[m] = None
        v[vanish] = STALE
    return v


def background(w, rng: random.Random, n: int, skip: set[str] = frozenset()) -> list[Raw]:
    """Healthy `up` for every service's targets (so absent()-style Down alerts stay quiet) and a healthy
    value for the exporters that other checks read through absent()."""
    out = []
    for s in w.services:
        if s.name in skip:
            continue
        for i in range(min(s.pods, 3)):
            lab = {"service": s.name, "job": s.name, "namespace": w.namespace, "instance": f"{ip(rng)}:8080"}
            if s.cluster:
                lab["cluster"] = w.clusters[i % len(w.clusters)]
            out.append(Raw("up", lab, [1] * (n + 1), bg=True))
    for extra in w.notes.get("background_series", []):
        if extra["service"] in skip:
            continue
        out.append(series(extra["metric"], extra["labels"], [extra["value"]] * (n + 1)))
    return out


def counter_from_rates(rng: random.Random, rates: list[float], start: int | None = None) -> list:
    """Integer counter whose per-minute increase follows rate*60 (rates in 1/s), with carry."""
    v = start if start is not None else rng.randint(10**4, 10**6)
    out, carry = [v], 0.0
    for r in rates[1:]:
        if r is None:
            out.append(None)
            continue
        x = r * 60 + carry
        inc = int(x)
        carry = x - inc
        v += inc
        out.append(v)
    return out


# ---------------------------------------------------------------------------- probes and groups

# Set by scraped.active() while a task's hidden scenarios are drawn: completes each group with the series the
# world's targets and exporters export (background traffic) before the targets are unified.
SCRAPE = None


class G:
    """A scenario group under construction."""

    def __init__(self, req: str, name: str, minutes: int):
        self.req, self.name, self.minutes = req, f"{req}.{name}", minutes
        self.series: list[Raw] = []
        self.probes: list[dict] = []
        self.extra: dict = {}

    def _id(self, tag: str) -> str:
        return f"{self.name}.{tag}{len(self.probes)}"

    def fire(self, t: int, svc: str, family: str = "F_detect", cls: str | None = None) -> None:
        p = {"type": "snap", "id": self._id("f"), "t": t, "family": family, "svc": svc}
        if cls:
            p["class"] = cls
        self.probes.append(p)

    def fire_by(self, t0: int, deadline: int, svc: str, family: str = "F_detect") -> None:
        """At least one delivered alert within an inclusive deadline window.

        Keep individual snapshots: merging label sets across time would let a late
        inhibition source retroactively mute an earlier delivered page.
        """
        if not 0 <= t0 <= deadline <= self.minutes:
            raise ValueError("fire_by window must be within the scenario")
        ids = []
        for minute in range(t0, deadline + 1):
            self.snap(minute, "delivery_trace", svc)
            ids.append(self.probes[-1]["id"])
        self.fire(deadline, svc, family)
        self.probes[-1]["any_of"] = ids

    def quiet(self, t0: int, t1: int, svc: str, family: str = "S_std", cls: str | None = None) -> None:
        p = {"type": "window", "id": self._id("s"), "t0": max(0, t0), "t": t1, "family": family, "svc": svc}
        if cls:
            p["class"] = cls
        self.probes.append(p)

    def count(self, expr: str, t: int, expect: int, family: str) -> None:
        self.probes.append({"type": "count", "id": self._id("c"), "expr": expr, "t": t, "expect": expect,
                            "family": family})

    def snap(self, t: int, family: str, svc: str | None = None) -> None:
        p = {"type": "snap", "id": self._id("p"), "t": t, "family": family}
        if svc:
            p["svc"] = svc
        self.probes.append(p)

    def storm(self, t0: int, t1: int) -> None:
        for t in range(t0, t1 + 1):
            self.snap(t, "storm")

    def labelless(self) -> None:
        self.probes.append({"type": "window", "id": self._id("g"), "t0": 0, "t": self.minutes, "family": "G",
                            "sel": 'service=""'})

    def bare(self, svc: str, families: tuple[str, ...] = ("http", "grpc")) -> None:
        """Declare that this scenario leaves `svc` without those app series on purpose (its pods up, nothing
        scraped from them): the scraped world (scraped.py) adds none and its lint accepts the gap."""
        have = set(self.extra.setdefault("bare", {}).get(svc, []))
        self.extra["bare"][svc] = sorted(have | set(families))

    def done(self) -> dict:
        n = self.minutes
        for r in self.series:
            r.values = (r.values + [None] * (n + 1))[: n + 1]
        if SCRAPE is not None:
            SCRAPE(self)   # the rest of what Prometheus scrapes in this world (scraped.py)
        self.series = unify_targets(self.series)
        d = {"name": self.name, "req": self.req, "input_series": [r.encoded() for r in self.series],
             "probes": self.probes}
        d.update(self.extra)
        return d


def http_series(rng: random.Random, w, svc: str, n: int, rps: list[float], err: list[float],
                pods: int = 3, codes_err: dict[str, float] | None = None, pod_names: list[str] | None = None,
                weights: list[float] | None = None, extra_labels: dict | None = None, post: float = 0.0) -> list[Raw]:
    """http_requests_total per pod and code. `err` is the 5xx share per minute; codes_err splits it
    across 5xx codes (default all 500). A small 404 share rides along (4xx is the caller's problem).
    `post` > 0 splits every series into GET and POST (evidence pulls: a real API takes writes too)."""
    codes_err = codes_err or {"500": 1.0}
    if pod_names is None:
        rs = rs_hash(rng)
        names = [pod_name(rng, svc, rs) for _ in range(pods)]
    else:
        names = pod_names
    wts = weights or [rng.uniform(0.8, 1.2) for _ in names]
    tot = sum(wts)
    methods = [("GET", 1.0)] if post <= 0 else [("GET", 1 - post), ("POST", post)]
    out = []
    for name, wt in zip(names, wts):
        inst = f"{ip(rng)}:8080"
        for method, msh in methods:
            share = wt / tot * msh
            base = {"service": svc, "namespace": w.namespace, "pod": name, "instance": inst,
                    "job": svc, "method": method}
            base.update(extra_labels or {})
            r_ok = [None if r is None else r * share * (1 - e) * 0.97 for r, e in zip(rps, err)]
            r_404 = [None if r is None else r * share * (1 - e) * 0.03 for r, e in zip(rps, err)]
            out.append(series("http_requests_total", {**base, "code": "200"}, counter_from_rates(rng, r_ok)))
            out.append(series("http_requests_total", {**base, "code": "404"}, counter_from_rates(rng, r_404, rng.randint(200, 900))))
            for code, frac in codes_err.items():
                fr = frac if isinstance(frac, list) else [frac] * len(rps)
                r_e = [None if r is None else r * share * e * f for r, e, f in zip(rps, err, fr)]
                vals = counter_from_rates(rng, r_e, 0 if isinstance(frac, list) else rng.randint(50, 900))
                if isinstance(frac, list):  # a code nobody has returned before: the series starts when it is first seen
                    first = next((i for i, v in enumerate(vals) if v not in (None, 0)), None)
                    if first is None:
                        continue
                    vals = [None] * first + vals[first:]
                out.append(series("http_requests_total", {**base, "code": code}, vals))
    return out


# ---------------------------------------------------------------------------- scrape targets

TARGET_LABELS = ("job", "namespace", "service", "instance", "pod")


def _alive(rs: list[Raw], m: int) -> bool:
    return any(r.values[m] is not None and r.values[m] != STALE for r in rs)


def unify_targets(raws: list[Raw]) -> list[Raw]:
    """Prometheus attaches every target label to every series it scrapes from that target. A service's app
    series (request counters, histograms, its own gauges) are grouped by pod and each pod is given one of the
    service's `up` targets: the series take the target's labels (`job`, `namespace`, `service`, `instance`,
    `pod`) and the `up` series gets `pod`. Pods beyond the `up` series drawn for the group get their own `up`
    (a copy of the first one); a background `up` follows its pod's lifetime (a replaced pod's target goes
    stale with it). Exporter series (another `job`) and federated cAdvisor series are not app targets."""
    ups: dict[str, list[Raw]] = {}
    for r in raws:
        svc = r.labels.get("service")
        if r.metric == "up" and svc and r.labels.get("job") == svc:
            ups.setdefault(svc, []).append(r)
    pods: dict[str, dict[str, list[Raw]]] = {}
    for r in raws:
        lab = r.labels
        svc = lab.get("service")
        if (r.metric == "up" or not svc or "pod" not in lab or "instance" not in lab or r.metric.startswith("container_")
                or lab.get("job", svc) != svc):
            continue
        pods.setdefault(svc, {}).setdefault(lab["pod"], []).append(r)
    extra: list[Raw] = []
    for svc, by_pod in pods.items():
        targets = ups.get(svc, [])
        if not targets:
            continue
        # a pod beyond the drawn targets copies the first target as drawn: by the time it is reached, that target
        # follows its own pod's lifetime, and a pod started after that one died would get a target that's gone
        base_labels, base_values, base_bg = dict(targets[0].labels), list(targets[0].values), targets[0].bg
        for k, (pod, rs) in enumerate(by_pod.items()):
            if k < len(targets):
                up = targets[k]
            else:
                up = Raw("up", {**base_labels, "instance": rs[0].labels["instance"]}, list(base_values), bg=base_bg)
                extra.append(up)
            up.labels["pod"] = pod
            tl = {x: up.labels[x] for x in TARGET_LABELS if x in up.labels}
            for r in rs:
                r.labels.update(tl)
            if up.bg:
                vals, seen = [], False
                for m, v in enumerate(up.values):
                    if _alive(rs, m):
                        vals.append(v)
                        seen = True
                    else:
                        vals.append(STALE if seen and vals and vals[-1] not in (None, STALE) else None)
                up.values = vals
    return raws + extra
