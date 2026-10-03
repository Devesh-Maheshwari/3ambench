"""The four H04 postmortem narratives (R1), written the way four different teams write them up.

Shared facts, worded differently in each: action item 1 (traffic floors in `slo/services.yaml`, on the service's
own request counter) is done; action item 2 asks for a page to the owning team when the service is under its floor
for 5 minutes while its pods are up, inside 15 minutes of traffic stopping. Times, counts and names come from the
packet (`prose_pm.h04`)."""

from __future__ import annotations

from .vocab import flavor


def _sev(rng, dur: int) -> int:
    return rng.choices([2, 3], weights=[3, 1] if dur >= 60 else [1, 2])[0]


def _lucky(kind: str, hour: int) -> str:
    day = 9 <= hour < 18
    return {
        "ingress": ("It happened during working hours, with people at their desks. At night the same resolve would "
                    "have looked like recovery for hours." if day else
                    "It happened in the early evening, while people were still online. An hour later the resolve "
                    "would have sent everyone to bed."),
        "edge": ("Office hours: support had people on the phones, which is how we heard at all." if day else
                 "Support's evening shift was still on; after 22:00 nobody would have picked up the escalation."),
        "weights": ("The canary was being watched by the people who started it, in the middle of the day." if day else
                    "Two of the people who knew the traffic split were still online after dinner."),
        "dns": ("The drill was scheduled for office hours, so the network team was around to undo it." if day else
                "The drill ran late but the network team hadn't logged off yet."),
    }[kind]


def postmortem(kind: str, w, rng, *, s, pm, date, clock, team, p, alert, fire, res, drop, back, found, err_on, lost,
               keys, host) -> str:
    tz = w.company.tz_label
    up = flavor(w.company.industry)["upstream"]
    k1, key, k3 = keys
    dur, gone = back - drop, found - drop
    t = clock.hm
    u0, u1 = clock.utc(drop).strftime("%H:%M"), clock.utc(back).strftime("%H:%M")
    sev = _sev(rng, dur)
    lucky = _lucky(kind, clock.local(drop).hour)
    if kind == "ingress":
        return f"""# {pm}: {s.name} unreachable for {dur} minutes after an ingress change

Date: {date}
Status: action items open
Severity: SEV{sev}
Impact window (UTC): {u0} to {u1}
Author: {p[1].full}

## Summary

The {s.name} Ingress was edited to move its host to another backend pool. The pool name in the change didn't
exist, so from {t(drop)} requests for `{host}` matched no rule and fell through to the controller's default
backend, which answers 404. Customers got that 404 page for {dur} minutes. `{s.name}` itself was healthy throughout:
its pods stayed up and ready and simply stopped receiving requests.

## Impact

- {lost:,} requests to `{host}` were answered with the default backend's 404 between {t(drop)} and {t(back)} {tz}.
- When the revert went out, client retries pushed {s.name} to about twice its normal rate for ten minutes. It held.

## Detection

`{alert}` paged {team} at {t(fire)} {tz} on the errors that led up to the change and resolved at {t(res)},
{res - drop} minutes into the outage. {p[0].first} had acked the page and took the resolve as recovery. The outage
was found at {t(found)} from the support queue.

## Timeline ({tz})

| time | what |
|---|---|
| {t(err_on)} | error ratio on {s.name} starts climbing (upstream dependency flapping) |
| {t(fire)} | `{alert}` pages {team}, {p[0].first} acks |
| {t(drop - 1)} | ingress change applied to fix the flapping route |
| {t(drop)} | requests to {s.name} stop; pods up and ready |
| {t(res)} | `{alert}` resolves |
| {t(found)} | support escalation reaches the {team} channel |
| {t(back)} | ingress change reverted, traffic back |

## What went wrong

- The only page {s.name} had compares errors with requests. With no requests there is nothing to compare.
- The default backend's 404s were on the ingress dashboard the whole time. Nothing alerts on them.

## What went well

- Once support escalated, the revert took two minutes.

## Action items

| id | action | owner | status | ticket |
|---|---|---|---|---|
| 1 | add traffic floors to `slo/services.yaml`, measured on the service's own request counter | {p[1].first} | DONE | {k1} |
| 2 | page the owner when {s.name} serves under its floor for 5 minutes while its pods are up; we want that page inside 15 minutes of traffic stopping | monitoring | TODO | {key} |
| 3 | ingress change review checklist | platform | TODO | {k3} |

## Where we got lucky

{lucky}
"""
    if kind == "edge":
        return f"""# {pm}: {s.name} requests blocked at the edge for {dur} minutes

Severity: SEV{sev} · Status: open, action items in flight · Author: {p[1].full}
Date: {date} · Impact (UTC): {u0}-{u1}

## Summary

While {up} was timing out, {s.name} retried into it and its error ratio climbed. To shed load, {p[1].first} added a
CDN rule to rate-limit `/v2/` on `{host}`. The match was inverted and every request on that host got a 403 at the
edge. Nothing reached origin, so {s.name}'s own counters went flat and its error ratio emptied out.

## Impact

About {round(lost / 1000)}k requests were answered with 403 by the CDN over {dur} minutes (its export is in this folder).
No data loss. Clients retried once the rule was gone, which is the bump right after {t(back)}.

## Detection

`{alert}` paged {team} at {t(fire)} for the retry errors. {p[0].first} acked, saw it resolve at {t(res)} and read that
as the rate limit working. Support escalated at {t(found)}.

## What went wrong

- The only page we had for {s.name} measures errors among requests that arrive. No requests, no ratio, no page.
- The CDN change went in from the console during the incident, with no second pair of eyes.

## Timeline ({tz})

| when | event |
|---|---|
| {t(err_on)} | {up} starts timing out, {s.name} errors climb |
| {t(fire)} | {alert} pages {team} ({p[0].first} on call) |
| {t(drop - 1)} | rate-limit rule added in the CDN console |
| {t(drop)} | edge answers 403 for everything on `{host}`; origin traffic goes to zero |
| {t(res)} | {alert} clears. "ratio's back under, looks like the rate limit worked" |
| {t(found)} | first support escalation |
| {t(back)} | rule removed, traffic returns |

## Action items

| # | action | owner | status | ticket |
|---|---|---|---|---|
| 1 | per-service traffic floors (`traffic_floor_rps`) in `slo/services.yaml`, read off the service's own request counter | {p[1].first} | DONE | {k1} |
| 2 | page the owning team if {s.name} sits under its floor for 5 min while its pods are up. We lost {gone} min here; we want that page within 15 min of traffic stopping | monitoring | TODO | {key} |
| 3 | CDN rule changes through Terraform only | edge | IN PROGRESS | {k3} |

## Where we got lucky

{lucky}
"""
    if kind == "weights":
        return f"""# {pm}: {s.name} traffic split left with no backend ({dur} min)

| | |
|---|---|
| Date | {date} |
| Severity | SEV{sev} |
| State | follow-ups open |
| Customer impact (UTC) | {u0} until {u1} |
| Written by | {p[1].first} |

## What happened

{s.name} was half way through a canary, with its Ingress splitting traffic between the stable and the canary
backend. To take traffic off the canary while {up} was flapping, someone pushed a change setting the canary weight
to 0. The branch had been rebased onto an older one and also carried a stable weight of 0. With no backend left,
the ingress answered 503 for `{host}`. {s.name}'s pods were up, ready and idle.

## Impact

{lost:,} requests got a 503 from the ingress in those {dur} minutes. Nobody's data was touched.

## How we found out

`{alert}` had paged {team} at {t(fire)} on the upstream errors. It resolved at {t(res)}, a few minutes after the
change, because {s.name} stopped seeing any requests. {p[0].first} took that as the canary pull working. The
support queue caught it at {t(found)}.

## Timeline ({tz})

| | |
|---|---|
| {t(err_on)} | {up} flaps; {s.name} 5xx share rises |
| {t(fire)} | page from `{alert}` to {team}, acked by {p[0].first} |
| {t(drop - 1)} | weights change merged and synced |
| {t(drop)} | ingress returns 503 for the host, {s.name} request rate drops to zero |
| {t(res)} | `{alert}` back to normal |
| {t(found)} | support ticket escalated to {team} |
| {t(back)} | stable weight back to 100 |

## What went wrong

- Two zero weights passed review. Nobody reads weights in a diff.
- The ingress's own 5xx counter showed the outage the whole time; it isn't a page.

## What went well

- Putting the weight back took one command once someone was looking at the right thing.

## Follow-ups

| # | what | who | state | ticket |
|---|---|---|---|---|
| 1 | traffic floor per service in `slo/services.yaml` (`traffic_floor_rps`), on the service's own request counter | {p[1].first} | done | {k1} |
| 2 | a page for the owning team when {s.name}'s request rate stays under its floor for 5 minutes while its pods are up, so a stop like this one pages inside 15 minutes | observability | open | {key} |
| 3 | CI check: traffic-split weights add up to 100 | platform | open | {k3} |

## Lucky

{lucky}
"""
    return f"""# {pm}: {s.name} lost its traffic during the LB failover drill

**Date:** {date} · **Severity:** SEV{sev} · **Status:** action items open
**Impact window:** {u0}-{u1} UTC ({dur} min)

### Summary

During the load-balancer failover drill, the weighted DNS record for `{host}` was moved to the new LB. The new LB
had no listener for that host, so clients got connection resets and nothing reached the ingress. {s.name}'s counters
went flat with every pod up.

### Customer impact

Roughly {round(lost / 1000)}k requests failed with a connection reset over {dur} minutes. Mobile clients showed their
generic network error.

### Detection

The {alert} page for {team} (fired {t(fire)}, from {up} flapping earlier) resolved at {t(res)} as the traffic drained
away. {p[0].first} closed it as recovered. We heard about the outage from support at {t(found)}, {gone} minutes in.

### Timeline ({tz})

- {t(err_on)}: errors on {s.name} go up, {up} is flapping
- {t(fire)}: {alert} pages {team}; {p[0].first} has it
- {t(drop - 1)}: drill step 3, DNS weight for `{host}` moved to the new LB
- {t(drop)}: no more requests at the ingress for {s.name}
- {t(res)}: {alert} resolves on its own
- {t(found)}: support asks in #{team}-alerts whether {s.name} is down
- {t(back)}: weight moved back, traffic recovers

### What went wrong

- Nothing checks that a service still receives requests. The error-ratio page needs traffic to say anything at all.
- The drill runbook moves DNS weight before it checks the target LB's listeners.

### Action items

1. **DONE** ({k1}, {p[1].first}): traffic floors in `slo/services.yaml` for the APIs, measured on each service's own request counter.
2. **TODO** ({key}, monitoring): {s.name} under its traffic floor for 5 minutes with its pods up should page the owning team. Target: paged no later than 15 minutes after traffic stops.
3. **TODO** ({k3}, network): drill runbook checks listeners before moving any weight.

### Where we got lucky

{lucky}
"""
