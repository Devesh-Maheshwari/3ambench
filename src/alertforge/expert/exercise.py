"""Exercise scenarios for the preservation replay (grader/replay.py): for every service, its targets go down,
its targets leave discovery, (HTTP services) half its requests fail, and (services whose rules read more than
their request counters) its other families run past every threshold: slow requests, a full pool, memory at the
limit, a backlog, a late export, throttling. They carry no probes of their own; the grader only reads them when an
untouched alert's text changed, to check that the change behaves the same."""

from __future__ import annotations

import random

from .items_svc import _targets
from .xscen import G, background, http_series

N = 30
N_SATURATED = 50   # the slowest `for` on these families is 30m


def exercise_groups(w, seed: int, plan=None) -> list[dict]:
    rng = random.Random(seed)
    out = []

    def grp(name, skip, build, n=N):
        g = G("__exercise", name, n)
        g.series += background(w, rng, n, skip) + build(n)
        g.extra["minutes"] = n
        out.append(g.done())

    for s in w.services:
        grp(f"{s.name}.down", {s.name}, lambda n, s=s: _targets(rng, w, s.name, n, down=(5, 99)))
        grp(f"{s.name}.gone", {s.name}, lambda n, s=s: _targets(rng, w, s.name, n, vanish=5))
        if s.family == "http":
            grp(f"{s.name}.errors", set(), lambda n, s=s: http_series(
                rng, w, s.name, n, [max(s.rps, 1.0)] * (n + 1), [0.002 if m < 5 else 0.5 for m in range(n + 1)], 3), n=40)
    # after every group above, so that their draws stay what they were
    for s in w.services if plan is not None else []:
        def hot(n, s=s):
            ups = _targets(rng, w, s.name, n)
            return ups + plan.saturate(s.name, ups, n, random.Random(f"{seed}:saturated:{s.name}"))
        if plan.need[s.name] - {"http", "grpc"}:
            grp(f"{s.name}.saturated", {s.name}, hot, n=N_SATURATED)
    return out
