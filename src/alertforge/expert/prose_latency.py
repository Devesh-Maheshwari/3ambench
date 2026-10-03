"""E4 text: the product owner's SLO note, the canary thread and the bucket pulls (H11), the timeout page (H12).

The H11 constraints (the 300ms objective stays, the v42 buckets can't change before v43, native histograms aren't
an option yet) come from different people at different points of the thread, the way they would (R2)."""

from __future__ import annotations

from . import evidence as ev
from . import variety
from .items import Ticket
from .people import by_trait, crew, product_owner
from .prose_svc import _day, _key, _thread, header, paged_team, part_of_day
from .xscen import ip, pods


def slo_note(s, w, waits_on: str = "the checkout page") -> str:
    owner = product_owner(w)
    return variety.card(w, "H11:slo", [
        f"""# {s.name}: latency objective

From {owner.first}, product. Agreed with {s.team} in the September planning.

99% of {s.name} requests should come back in under 300ms, over a rolling 7 days. This is the lookup
{waits_on} waits on, so it's the one number I care about for this service. Availability is covered by the
existing SLO.
""",
        f"""# Latency SLO: {s.name}

Owner: {owner.full} (product) · reviewed with {s.team}

- Objective: 99% of requests answered within 300ms
- Window: 7 days, rolling
- Why: {waits_on} blocks on this lookup. Above 300ms people notice; above a second they leave.

Availability has its own SLO in the catalog; this page is only about speed.
""",
        f"""# {s.name} latency

{owner.first} here. For the record, since it came up in planning again: we promise that 99 out of 100 {s.name}
calls finish in under 300ms, measured over the last 7 days. {waits_on[:1].upper() + waits_on[1:]} waits on it, which
is why this is the number and not the average. {s.team} signed off on it in September.
"""])


def h11(item, w, rng) -> Ticket:
    from .items_latency import HEALTHY, LAYOUTS, _hist
    s = w.svc(item.p["svc"])
    date = _day(w, rng, 1, 6, weekday=True)
    clock = ev.Clock(date, rng.choice(["10:00", "09:40", "11:15", "14:00"]), w.company.utc_offset, w.company.tz_label)
    n = 60
    raws = []
    for ver in ("v41", "v42"):
        pod = pods(rng, s.name, 1)[0]
        lab = {"service": s.name, "namespace": w.namespace, "pod": pod, "instance": f"{ip(rng)}:8080", "version": ver}
        raws += _hist(rng, lab, ver, n, s.rps / 2, HEALTHY)
    inc = w.cal.inc(date, clock.hm(0), 19)
    path = f"incidents/{inc}"
    files = {f"{path}/query-range/{s.name}-buckets.json": ev.query_range(raws, clock)}
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 4)
    po = product_owner(w)
    weight = rng.choice([10, 25, 50])
    p99 = round(0.186 * rng.uniform(0.96, 1.04) * 1000 / 5) * 5
    resume = w.cal.ahead(rng.randint(2, 3))
    shape = variety.card(w, "H11:thread", [0, 1, 2])
    if shape == 0:
        thread = [(clock.hm(12), p[0].handle, f"{s.name} v42 canary at {weight}%, looks clean on errors"),
                  (clock.hm(19), p[1].handle, f"uh, latency budget page for {s.name}?"),
                  (clock.hm(19), p[1].handle, f"tempo says p99 ~{p99}ms though"),
                  (clock.hm(21), p[0].handle, "pausing it"),
                  (clock.hm(24), p[2].handle, f"dashboard has the slow fraction at like half the requests on v42. that can't be right"),
                  (clock.hm(31), p[2].handle, "v42 is the release that moved us onto the framework http middleware fwiw"),
                  (clock.hm(40), p[3].handle, "middleware histograms come with its default buckets. we can make them configurable "
                                              "but that's v43, not this week"),
                  (clock.hm(41), p[0].handle, "can we just bump the objective for a few days"),
                  (clock.hm(43), po.handle, f"no, 300ms is the number (docs/slo/{s.name}.md)"),
                  (clock.hm(52), p[0].handle, f"ok, rollout stays paused until the SLI is fixed. back on it {resume:%A}")]
    elif shape == 1:
        thread = [(clock.hm(20), po.handle, f"why did {s.name} page on latency? p99 in tempo is {p99}ms"),
                  (clock.hm(23), p[0].handle, f"the v42 canary is at {weight}%, that's the only change today. pausing it"),
                  (clock.hm(29), p[1].handle, by_trait(p[1], terse="bucket layouts differ between versions",
                                                      chatty="I think it's the buckets, the two versions don't export the same ones",
                                                      asks="do v41 and v42 even export the same buckets?")),
                  (clock.hm(31), p[1].handle, "v41: 0.05 0.1 0.25 0.5 ... / v42: 0.05 0.1 0.2 0.35 0.5 ..., v42 is the "
                                              "framework's default set"),
                  (clock.hm(35), po.handle, "the objective stays at 300ms, before anyone asks"),
                  (clock.hm(38), p[2].handle, "custom buckets are in the v43 branch, can't backport that this week"),
                  (clock.hm(39), p[3].handle, "native histograms would make this go away but that's on the Q1 roadmap, not now"),
                  (clock.hm(44), p[0].handle, f"rollout resumes {resume:%A} once the SLI works on both versions")]
    else:
        thread = [(clock.hm(18), "incident-bot", f":rotating_light: {inc} declared by @{p[0].handle}: {s.name} latency budget page"),
                  (clock.hm(19), p[0].handle, f"v42 canary at {weight}% when it paged. paused"),
                  (clock.hm(22), p[1].handle, f"real latency is fine, p99 {p99}ms from traces"),
                  (clock.hm(26), p[2].handle, "```\n$ curl -s http://" + f"{s.name}-canary:8080/metrics | grep 'duration_seconds_bucket' | head -5\n"
                   + "\n".join(f'http_request_duration_seconds_bucket{{le="{le}",method="GET"}} ...' for le in LAYOUTS["v42"][:5])
                   + "\n```"),
                  (clock.hm(27), p[2].handle, "framework default buckets on v42, the old hand-rolled ones on v41"),
                  (clock.hm(33), p[3].handle, "making the buckets configurable is v43 work"),
                  (clock.hm(35), po.handle, "and the objective is not moving off 300ms"),
                  (clock.hm(41), p[0].handle, f"rollout stays paused, picking it up again {resume:%A}")]
    files[f"{path}/timeline.md"] = _thread(f"#{team} ({w.company.tz_label})" if shape != 2 else header(w, rng, inc), thread)
    title, body = variety.card(w, "H11", [
        (f"{s.name} latency SLI wrong during the v42 rollout",
         f"The {s.name} canary paged on latency budget while traces had p99 around {p99 - 5}-{p99 + 5}ms, well under the "
         f"300ms in `docs/slo/{s.name}.md`. The rollout is paused and resumes {resume:%A}. The latency SLI records "
         f"for {s.name} need to be right on both versions by then. Thread and a bucket pull from the canary: `{path}/`."),
        (f"{s.name}: latency page from the v42 canary",
         f"{s.name} v42 went to {weight}% and the latency budget page went off, although nothing was slow (p99 "
         f"~{p99}ms). We paused the rollout; it has to continue {w.cal.next_weekday(resume.strftime('%A'))}. Both "
         f"versions are in the pull in `{path}/`, and the thread has the rest."),
        (f"fix the {s.name} latency SLI before the rollout resumes",
         f"Canary paged for a latency problem that wasn't there. Until the SLI reads both v41 and v42 correctly the v42 "
         f"rollout stays paused ({resume:%A} at the latest, per {p[0].first}). Objective is in `docs/slo/{s.name}.md`. "
         f"Packet: `{path}/`.")])
    return Ticket(_key(w, rng), title, body, p[1], files)


def h12_runbook(s, alert: str, w=None) -> str:
    opts = [f"""---
alerts: [{alert}]
---
# {alert}

Clients call {s.name} with a 5 second timeout and give up after that, so a request slower than 5 seconds is a
failed request for whoever made it. {alert} pages {s.team} when more than 1% of {s.name} requests take longer
than 5 seconds for 10 minutes.

- Check the {s.name} database's slow-query log first; most of these have been a missing index after a migration.
- If only one pod is slow, cordon its node and let the deployment reschedule it.
""", f"""---
alerts: [{alert}]
---
# {s.name}: slow requests

Every caller of {s.name} gives up after 5 seconds. When over 1% of requests are slower than that for 10 minutes,
{s.team} gets paged: at that point those callers are seeing failures, whatever our error counters say.

1. Slow-query log on the {s.name} database. Nine times out of ten it's an index a migration dropped.
2. One slow pod only: cordon the node, delete the pod.
""", f"""---
alerts: [{alert}]
---
# {alert}

What it means: more than 1% of {s.name} requests have been taking over 5s for 10 minutes. 5s is the client
timeout, so to the client those requests failed. Pages {s.team}.

Where to look: the database's slow-query log, then per-pod latency on the service board (a single bad node
shows up there).
"""]
    return variety.card(w, "any:H12:runbook", opts) if w is not None else opts[0]


def h12(item, w, rng) -> Ticket:
    from .items_latency import slow_hist
    s = w.svc(item.p["svc"])
    date = _day(w, rng, 2, 14)
    clock = ev.Clock(date, rng.choice(["14:20", "16:45", "11:05", "09:35"]), w.company.utc_offset, w.company.tz_label)
    n, on = 70, rng.randint(15, 25)
    dur = rng.randint(28, 40)
    pct = rng.choice([3, 4, 5])
    slow = [pct / 100 if on <= m < on + dur else 0.001 for m in range(n + 1)]
    raws = []
    for pod in pods(rng, s.name, 2):
        lab = {"service": s.name, "namespace": w.namespace, "pod": pod, "instance": f"{ip(rng)}:8080", "job": s.name}
        raws += slow_hist(rng, lab, n, s.rps / 2, slow)
    inc = w.cal.inc(date, clock.hm(0), on + 6)
    path = f"incidents/{inc}"
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 3)
    files = {f"{path}/query-range/{s.name}-request-duration.json": ev.query_range(raws, clock)}
    shape = variety.card(w, "H12:thread", [0, 1, 2])
    if shape == 0:
        thread = [(clock.hm(on + 6), p[0].handle, f"support says {s.name} calls are timing out for some customers"),
                  (clock.hm(on + 9), p[1].handle, f"client side shows about {pct}% of calls hitting the 5s timeout, the rest are fine"),
                  (clock.hm(on + 12), p[0].handle, "and nothing paged us?"),
                  (clock.hm(on + dur + 2), p[1].handle, "slow query fixed (index was dropped in the migration), timeouts gone")]
    elif shape == 1:
        thread = [(clock.hm(on + 8), p[1].handle, f"{s.name} timeouts in the client logs, {pct}% or so, started ~{clock.hm(on)}"),
                  (clock.hm(on + 10), p[0].handle, by_trait(p[0], terse=f"{s.name} board looks normal",
                                                           chatty=f"the {s.name} board looks completely normal to me, p99 isn't moving",
                                                           asks=f"anyone see this on the {s.name} board? p99 looks flat")),
                  (clock.hm(on + 17), p[2].handle, "slow query log is full of the same lookup, no index on it since tuesday's migration"),
                  (clock.hm(on + dur), p[2].handle, "index back"),
                  (clock.hm(on + dur + 3), p[1].handle, "timeouts stopped. why didn't the latency page go off?")]
    else:
        thread = [(clock.hm(on + 5), "incident-bot", f":rotating_light: {inc} declared by @{p[0].handle}: {s.name} client timeouts"),
                  (clock.hm(on + 6), p[0].handle, f"partner support escalated, {s.name} calls failing at their 5s timeout"),
                  (clock.hm(on + 14), p[1].handle, "found it, a dropped index"),
                  (clock.hm(on + dur + 1), p[1].handle, "fixed, watching"),
                  (clock.hm(on + dur + 9), p[0].handle, "resolved. we were never paged for this, someone please look")]
    files[f"{path}/timeline.md"] = _thread(f"#{team} ({w.company.tz_label})" if shape != 2 else header(w, rng, inc), thread)
    title, body = variety.card(w, "H12", [
        (f"{s.name} timed out for {dur} minutes and nobody was paged",
         f"On {clock.day()} about {pct}% of {s.name} requests ran into the client's 5 second timeout for {dur} "
         f"minutes. The latency page didn't fire at any point, and that is exactly the case its runbook says it "
         f"pages for. Request-duration pull and the thread are in `{path}/`."),
        (f"{s.name} slow requests never page",
         f"{team} found out about {s.name} timing out from support, {dur} minutes in ({clock.day()}). The page "
         f"for it stayed quiet the whole time. `{path}/` has the histogram pull from that {part_of_day(clock, on)}."),
        (f"no page for {s.name} timeouts",
         f"{pct}% of {s.name} calls hit the 5s client timeout for {dur} minutes {w.cal.rel(clock.local(0).date())}. "
         f"The page that is supposed to catch that never fired. Pull and thread: `{path}/`.")])
    return Ticket(_key(w, rng), title, body, p[2], files)
