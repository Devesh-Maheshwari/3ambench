"""The three ADRs a task's README points at (R2/R9, editorial pass E8): Context / Decision / Consequences, an author
and a date, numbered the way that company numbered its decisions. Each comes in three wordings with the same
decision in it; the policy states general rules only (SPEC 6.2), never the trap a ticket is about."""

from __future__ import annotations

import random

from . import variety
from .people import rota
from .repo import FACTORS


def numbers(company) -> dict[str, str]:
    """ADR numbers per company: the scrape-interval decision came first, then SLO alerting, then disks."""
    rng = random.Random(f"adr:{company.name}")
    a = rng.randint(2, 5)
    b = a + rng.randint(2, 6)
    c = b + rng.randint(2, 7)
    return {"scrape": f"{a:04d}", "slo": f"{b:04d}", "disk": f"{c:04d}"}


def files(w) -> dict[str, str]:
    n = w.notes.get("adr") or numbers(w.company)
    return {f"docs/adr/{n['scrape']}-scrape-intervals.md": scrape(w, n["scrape"]),
            f"docs/adr/{n['slo']}-slo-alerting.md": slo(w, n["slo"]),
            f"docs/adr/{n['disk']}-disk-alerts.md": disk(w, n["disk"])}


def _meta(w, key: str, n: str, date: str) -> tuple[str, str]:  # noqa: ARG001
    """(author, date): someone in observability; dates in decision order, different at every company."""
    import datetime as dt
    obs = rota(w, "observability")
    who = obs[int(n) % len(obs)].full
    rng = random.Random(f"adr-dates:{w.company.name}")
    days = sorted(rng.sample(range(0, 800), 3))
    when = {k: (dt.date(2024, 3, 1) + dt.timedelta(days=d)).isoformat() for k, d in zip(("scrape", "slo", "disk"), days)}
    return who, when[key]


def scrape(w, n: str) -> str:
    who, date = _meta(w, "scrape", n, "2025-03-04")
    tables = ["""| job | interval | notes |
|---|---|---|
| app targets (`kubernetes-pods`) | 1m | |
| ingress | 1m | |
| `kafka-exporter` | 1m | was 15s until August; the exporter got too expensive on the big clusters |
| cAdvisor federation from platform | 1m | allowlist in README |
| node, postgres, redis exporters | 1m | |""", """| scrape job | every | |
|---|---|---|
| `kubernetes-pods` (our apps) | 1m | |
| ingress controller | 1m | |
| `kafka-exporter` | 1m | 15s before August, which cost too much on the large clusters |
| federated cAdvisor series (platform) | 1m | see the README allowlist |
| node / postgres / redis exporters | 1m | |""", """- `kubernetes-pods` (app targets): 1m
- ingress: 1m
- `kafka-exporter`: 1m. It ran at 15s until August, when the big clusters made that too expensive.
- cAdvisor, federated from platform: 1m (allowlist in README)
- node, postgres and redis exporters: 1m"""]
    table = variety.house(w, "adr:scrape-table", tables)
    return variety.house(w, "adr:scrape", [
        f"""# ADR {n}: scrape intervals

Status: accepted (last amended in August) · Author: {who} · {date}

## Context

Every job had its own interval and nobody could say which rate windows were safe.

## Decision

The global `scrape_interval` is 1m. Jobs and exceptions:

{table}

## Consequences

Changing an interval means checking the rate windows of every rule that reads the job.
""",
        f"""# {n}. Scrape intervals

* Status: accepted, amended in August
* Deciders: observability, platform
* Date: {date} ({who})

## Context

Prometheus memory doubled in a year, mostly from fast scrapes nobody needed.

## Decision

One interval for everything, 1m, set globally. Per job:

{table}

## Consequences

- A rule's rate window has to fit the interval of the job it reads. Whoever changes an interval checks those
  windows.
""",
        f"""# ADR {n}: one scrape interval

Accepted. Written by {who}, {date}; the kafka-exporter row was changed in August.

## Context

Mixed intervals (10s, 15s, 30s, 1m) made every new rule a guessing game about its window.

## Decision

Global `scrape_interval: 1m`. Current state per job:

{table}

## Consequences

Any change to a job's interval comes with a look at the windows of the rules reading that job.
"""])


def slo(w, n: str) -> str:
    who, date = _meta(w, "slo", n, "2025-06-17")
    f30, s30 = FACTORS[30]
    latency = [
        "A latency SLI counts requests slower than the objective. Use the bucket boundary nearest the objective "
        "without going over it; if the objective is a boundary, use it. Latency SLOs are the fraction of requests "
        "slower than the objective, never a quantile.",
        "For latency, the SLI is the share of requests slower than the objective, computed on the histogram bucket "
        "closest to the objective that doesn't exceed it (the objective's own boundary when there is one). A latency "
        "SLO is always that fraction; we don't write quantile SLOs.",
        "Latency SLOs are written as the fraction of requests slower than the objective, not as a quantile. Count the "
        "slow ones against the nearest bucket boundary at or below the objective; when the objective is itself a "
        "boundary, that boundary."]
    floor = [
        "A burn rate means nothing when there is no traffic: when a service stops getting requests its ratios go "
        "empty and the burn alerts resolve. Losing the traffic altogether is the floor alert's job "
        "(`traffic_floor_rps` in the catalog, measured on the service's own request counter).",
        "Burn alerts can't see a service that gets no requests at all: the ratios have nothing to divide and the "
        "alerts resolve. That case belongs to the traffic floor (`traffic_floor_rps` in the catalog, on the "
        "service's own request counter).",
        "Without traffic there is no burn rate; the ratios go empty and every burn alert resolves. Missing traffic "
        "is covered by the floor alert instead, against `traffic_floor_rps` in the catalog measured on the "
        "service's own request counter."]
    v = variety.house(w, "adr:slo", [0, 1, 2])
    body = [
        f"""# ADR {n}: SLO alerting

Status: accepted · {who}, {date}

## Context

Threshold alerts on error ratios paged for blips and stayed quiet through slow leaks.

## Decision

We page when a service has burned 2% of its error budget within an hour, and we don't page on a blip, so the
last 5 minutes have to agree. Slow burns (5% in 6 hours, confirmed over 30 minutes) open a ticket. The budget is
whatever `slo/services.yaml` says the window is: most services are on 30 days, batch and low-volume services
are on 7.

For a 30-day window that works out to {f30}x over 1h and 5m for the page and {s30}x over 6h and 30m for the
ticket. Other windows scale the same way (budget fraction × window hours / alert window hours).

## Consequences

{floor[0]}

{latency[0]}
""",
        f"""# {n}. Multi-window burn-rate alerts

* Status: accepted
* Author: {who}
* Date: {date}

## Context

Every team had its own error threshold, and most of them had been picked after an incident.

## Decision

- Page: 2% of the error budget gone within an hour, with the last 5 minutes agreeing (no paging on a blip).
- Ticket: 5% of the budget gone in 6 hours, confirmed over the last 30 minutes.
- The budget's window is the one in `slo/services.yaml`; 30 days for most services, 7 for batch and low-volume ones.
- On a 30-day window: {f30}x over 1h and 5m (page), {s30}x over 6h and 30m (ticket). Any other window scales the
  same way: budget fraction × window hours / alert window hours.

## Consequences

{floor[1]}

{latency[1]}
""",
        f"""# ADR {n}: alerting on SLOs

Accepted {date}. Author: {who}.

## Context

We want to be woken up for budget we are actually losing, not for every spike.

## Decision

A page means 2% of the error budget burned in the last hour, and the last 5 minutes still burning (so a blip
doesn't page). A ticket means 5% burned over 6 hours, still burning over the last 30 minutes. How much budget a
service has comes from its window in `slo/services.yaml`: 30 days for most, 7 for batch and low-volume services.
With 30 days that is {f30}x over 1h and 5m to page and {s30}x over 6h and 30m for the ticket; for other windows,
budget fraction × window hours / alert window hours.

## Consequences

{floor[2]}

{latency[2]}
"""][v]
    return body


def disk(w, n: str) -> str:
    who, date = _meta(w, "disk", n, "2025-10-08")
    return variety.house(w, "adr:disk", [
        f"""# ADR {n}: disk alerts

Status: accepted ({date}) · by {who}

## Context

Disk predictions paged for every backup and compaction.

## Decision

Open a ticket when a volume is under 15% free and on course to fill within 4 hours. Page when it's under 5% free
for 5 minutes.

## Consequences

A prediction alone is not a reason to page: backups, compactions and bulk loads all look like a volume filling up
for an hour.
""",
        f"""# {n}. When disks page

* Status: accepted
* Author: {who} ({date})

## Context

The old rule paged on a 4-hour fill prediction, which every nightly job set off.

## Decision

- Ticket: less than 15% free and predicted to fill within 4 hours.
- Page: less than 5% free, for 5 minutes.

## Consequences

Predictions never page on their own. Backups, compactions and bulk loads look like a filling volume for an hour
at a time, and that is not worth waking anyone.
""",
        f"""# ADR {n}: disk space

Accepted on {date}; {who}.

## Context

Too many disk pages at night, almost all of them for jobs that free the space again on their own.

## Decision

Below 15% free with the volume set to fill within 4 hours: ticket. Below 5% free for 5 minutes: page.

## Consequences

Nothing pages on a prediction alone, since a backup, a compaction or a bulk load makes a volume look like it is
filling for an hour.
"""])
