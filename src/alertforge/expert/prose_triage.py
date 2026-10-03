"""Text for triage items (R08, R10, R11, R12, R13, R18) and for the complaint about an alert that was right.

In E5 the triage items are answers in the on-call survey: first person, written by whoever was annoyed, not
tickets (R2). Everywhere else they are queue tickets."""

from __future__ import annotations

import datetime as dt

from . import evidence as ev
from . import variety
from .items import Ticket
from .people import crew
from .prose_svc import _day, _key, paged_team
from .xscen import ip, pods, series


def _survey(w, item) -> bool:
    return w.family == "E5" and item.code in variety.SURVEY_CODES


def generic_runbook(alert: str, w=None) -> str:
    opts = [f"""---
alerts: [{alert}]
---
# {alert}

1. Check the service dashboard (svc-overview) for the affected service.
2. Look at recent deploys (`#deploys`) before digging further.
3. If the database is involved, loop in database-reliability.
""", f"""---
alerts: [{alert}]
---
# {alert}

Start from svc-overview filtered to the service. Most of the time something was just deployed: check #deploys
and the rollout history. Database trouble goes to database-reliability, they have their own rota.
""", f"""---
alerts: [{alert}]
---
# {alert}

- service dashboard first (svc-overview, pick the service)
- anything in #deploys in the last hour? roll it back before investigating
- DB involved: page database-reliability
"""]
    return variety.card(w, "any:R13:runbook", opts) if w is not None else opts[0]


def r08(item, w, rng) -> Ticket:
    z, s = item.p["gone"], item.p["svc"]
    title, body = variety.card(w, "R08", [
        (f"{z} is gone", f"`{z}` was switched off on {item.p['date']}; its alerts and its route block are still in "
                         f"here. Please clean them out. `{s}` stays, obviously."),
        (f"remove {z}", f"Decommissioned on {item.p['date']} ({item.p['team']} confirmed). Whatever still refers to "
                        f"it in rules and routing can go. Don't touch `{s}`."),
        (f"{z} leftovers", f"{z} has had zero replicas since {item.p['date']} and the deployment is being deleted. "
                           f"Its rules and routing are still in this repo. {s} is the replacement and stays.")])
    return Ticket(_key(w, rng), title, body, crew(w, rng, item.p["team"], 1)[0])


def severity(item, w, rng) -> Ticket:
    a = item.p["alert"]
    who = crew(w, rng, paged_team(w, item.p["svc"]), 1)[0]
    if item.direction == "up":
        other = rng.choice([s.name for s in w.services if s.kind == "api"] or ["the API"])
        if _survey(w, item):
            text = variety.card(w, "survey:R10", [
                f"{a} should page. Twice now it was the first sign of trouble and both times it sat in Slack for twenty "
                f"minutes before anyone looked. Customers were unable to complete requests while it was firing.",
                f"I keep seeing {a} in Slack hours after the fact. It warned us before the backlog took {other} down "
                f"last month and nobody was paged. Customers could not complete requests. Make it a page.",
                f"Can we finally make {a} page? Customers cannot complete requests while it is firing. "
                f"We said so in the ops review and it still only posts to Slack."])
            return Ticket(_key(w, rng), f"make {a} page", text, who)
        title, body = variety.card(w, "R10", [
            (f"make {a} page", f"{a}: make it page. It caught the last two incidents before customers did, and both "
                               f"times it sat in Slack for twenty minutes before anyone looked."),
            (f"{a} should wake someone up", f"Can {a} page please? It's the only thing that warned us before the "
                                            f"backlog took {other} down last month."),
            (f"{a} to page", f"We agreed in the ops review that {a} should wake someone up. Right now it only posts to "
                             f"Slack.")])
        return Ticket(_key(w, rng), title, body, who)
    if _survey(w, item):
        text = variety.card(w, "survey:R11", [
            f"{a} pages whoever's on call at 3 a.m. and there is nothing I can do about it until the upstream team is "
            f"in. It should be a ticket for the owning team.",
            f"Third night this month {a} paged me and the answer was \"wait for business hours\". That's a ticket, "
            f"not a page.",
            f"Nobody can act on {a} at night. I ack it and go back to sleep. Put it in the team's queue instead."])
        return Ticket(_key(w, rng), f"{a} shouldn't page", text, who)
    title, body = variety.card(w, "R11", [
        (f"{a} shouldn't page", f"{a} pages whoever's on call at 3 a.m. and there is nothing they can do about it until "
                                f"the upstream team is in. It should go to the team's queue instead."),
        (f"{a}: ticket, not page", f"Nobody can act on {a} at night. It should be a ticket for the owning team, not a "
                                   f"page."),
        (f"stop paging for {a}", f"Third night this month {a} paged and the answer was \"wait for business hours\". "
                                 f"Make it a ticket.")])
    return Ticket(_key(w, rng), title, body, who)


def r12(item, w, rng) -> Ticket:
    a = item.p["alert"]
    who = crew(w, rng, paged_team(w, item.p["svc"]), 1)[0]
    if _survey(w, item):
        text = variety.card(w, "survey:R12", [
            f"The Slack message for {a} just says \"p99 latency above 500ms on\" and then nothing. Which pod? Which "
            f"service? I have to open Prometheus to find out.",
            f"Small one: {a} notifications end mid-sentence (\"... on\"). I'd like to know what they're about without "
            f"clicking through.",
            f"Got {a} in Slack at night: \"p99 latency above 500ms on\". On what? I spent five minutes finding out."])
        return Ticket(_key(w, rng), f"{a} message is cut off", text, who)
    title, body = variety.card(w, "R12", [
        (f"{a} message is cut off", f"The Slack message for {a} just says \"p99 latency above 500ms on\" and then "
                                    f"nothing. Which pod? Which service? I have to open Prometheus to find out."),
        (f"{a} notifications end mid-sentence", f"{a} notifications end mid-sentence (\"... on\"). Please make them say "
                                                f"what they're about."),
        (f"{a}: summary doesn't say what's slow", f"Got {a} in Slack last night: \"p99 latency above 500ms on\". On "
                                                  f"what? Summary needs to name the thing that's slow.")])
    return Ticket(_key(w, rng), title, body, who)


def r13(item, w, rng) -> Ticket:
    a = item.p["alert"]
    who = crew(w, rng, "sre", 1)[0]
    if _survey(w, item):
        text = variety.card(w, "survey:R13", [
            f"The runbook link on {a} goes to the old wiki, which 404s since the migration. There's a runbook for it "
            f"in the repo, I checked.",
            f"Clicked the runbook on {a} mid-incident and got the old Confluence 404. Fun.",
            f"Please go through the runbook links. {a} still points at the old wiki, which is gone."])
        return Ticket(_key(w, rng), f"{a} runbook link 404s", text, who)
    title, body = variety.card(w, "R13", [
        (f"{a} runbook link 404s", f"The runbook link on {a} goes to the old wiki, which 404s since the migration. "
                                   f"There is a runbook for it in the repo."),
        (f"dead runbook link on {a}", f"{a}'s runbook link is dead (old wiki). Point it where the rest of them point."),
        (f"{a}: runbook goes to the old wiki", f"Clicked the runbook on {a} mid-incident and got the old Confluence "
                                               f"404. Please fix the link.")])
    return Ticket(_key(w, rng), title, body, who)


def r18(item, w, rng) -> Ticket:
    who = crew(w, rng, "sre", 1)[0]
    if _survey(w, item):
        text = variety.card(w, "survey:R18", [
            f"Why do I get {item.NEW} and {item.OLD} for the same disk, every single time? {item.OLD} is the old one.",
            f"{item.OLD} and {item.NEW} always fire together. One of them has to go, and {item.OLD} is the older one.",
            f"Two alerts for one full disk ({item.OLD} and {item.NEW}). I'd keep {item.NEW}."])
        return Ticket(_key(w, rng), "duplicate disk alerts", text, who)
    title, body = variety.card(w, "R18", [
        ("duplicate disk alerts", f"We get {item.NEW} and {item.OLD} for the same thing, every time. {item.OLD} is the "
                                  f"old one."),
        (f"drop {item.OLD}", f"{item.OLD} and {item.NEW} always fire together. Drop {item.OLD}, it's the older of the two."),
        (f"{item.OLD} duplicates {item.NEW}", f"Every disk alert arrives twice, once as {item.OLD} and once as "
                                              f"{item.NEW}. {item.NEW} is the newer one; keep that and drop the other.")])
    return Ticket(_key(w, rng), title, body, who)


def kept(k, w, rng) -> Ticket:
    s = k.svc
    date = _day(w, rng, 2, 16)
    clock = ev.Clock(date, "02:40", w.company.utc_offset, w.company.tz_label)
    n = 60
    act = [int(v) for v in [60 + (m * 0.4 if m < 20 else 8 + (m - 20) * 2.2) for m in range(n + 1)]]
    act = [min(v, 97) if m < 45 else 30 for m, v in enumerate(act)]
    pod = pods(rng, s.name, 1)[0]
    lab = {"service": s.name, "pod": pod, "instance": f"{ip(rng)}:8080", "namespace": w.namespace}
    raws = [series("db_pool_connections_active", lab, act), series("db_pool_connections_max", lab, [100] * (n + 1))]
    path = f"incidents/{w.cal.inc(date, '02:40', 30)}"
    peak = next((m for m, v in enumerate(act) if v >= 96), 30)
    files = {f"{path}/query-range/{s.name}-db-pool.json": ev.query_range(raws, clock),
             f"{path}/app.log": app_log(rng, clock, s.name, pod, peak)}
    text = variety.card(w, "kept", [
        f"{k.alert} paged me at {clock.hm(peak - 5)} for nothing. Nobody complained, it resolved by itself. "
        f"Can we make it less jumpy? Pool numbers and the app log from that night are in `{path}/`.",
        f"Woken up by {k.alert} at {clock.hm(peak - 5)}. I don't think it was real, the service looked fine by the "
        f"time I had a laptop open. Stuff from that night: `{path}/`.",
        f"I got {k.alert} at {clock.hm(peak - 5)} and by the time my laptop was open {s.name} was fine. Was it ever "
        f"real? Pool graph and logs: `{path}/`."])
    return Ticket(_key(w, rng), f"{k.alert} woke me up", text, crew(w, rng, paged_team(w, s.name), 1)[0], files)


def app_log(rng, clock, svc: str, pod: str, peak: int) -> str:  # noqa: ARG001
    """The app's log around the pool running dry (R8): bursts of failed requests with request ids, warnings about
    slow checkouts before it, ordinary lines around it."""
    lines = []
    t = (peak - 6) * 60.0
    while t < (peak + 6) * 60:
        sec = rng.uniform(0.2, 9.0) if peak * 60 - 30 < t < peak * 60 + 120 else rng.uniform(4, 40)
        t += sec
        stamp = (clock.local(0) + dt.timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        rid = "".join(rng.choice("0123456789abcdef") for _ in range(16))
        m = t / 60
        if peak - 1 <= m < peak + 3 and rng.random() < 0.7:
            lines.append(f"{stamp} ERROR [{svc},{rid}] pool: FATAL: sorry, too many clients already")
        elif peak - 4 <= m < peak + 3 and rng.random() < 0.5:
            lines.append(f"{stamp} WARN  [{svc},{rid}] pool: connection checkout took {rng.uniform(1.2, 4.8):.1f}s "
                         f"(active={rng.randint(88, 99)}, max=100)")
        else:
            lines.append(f"{stamp} INFO  [{svc},{rid}] {rng.choice(['GET', 'GET', 'POST'])} /v1/{rng.choice(['items', 'orders', 'search', 'accounts'])} "
                         f"{rng.choice([200, 200, 200, 204, 404])} {rng.randint(8, 240)}ms")
    return "\n".join(lines) + "\n"
