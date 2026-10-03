"""Tickets and packets for deploy-time and storm defects (H05, H07, H22, H19, H20).

Deploys run on working days in office hours (an evening hotfix now and then), release tags carry the day of the
deploy, and every page in a packet is the page the starting repo sends: its labels, its title and the rota it
reached come from `then.py`, as they were before this week's queue."""

from __future__ import annotations

import datetime as dt

from . import evidence as ev
from . import then, variety
from .items import Ticket
from .people import by_trait, crew
from .prose_svc import _key, _pd_rows, _thread, ack, alert_ref, header, numbered, page_row, paged_team, person
from .vocab import and_list
from .xscen import http_series, ip, pod_name, pods, rs_hash


def _deploy_line(clock, m, svc, ver, msg) -> str:
    t = clock.local(m).strftime("%Y-%m-%dT%H:%M:%S")
    off = round(clock.off * 60)
    return f"{t}{'+' if off >= 0 else '-'}{abs(off) // 60:02d}:{abs(off) % 60:02d} rollouts {svc} {ver} {msg}"


def _clock(w, day: dt.date, hhmm: str) -> ev.Clock:
    return ev.Clock(day.strftime("%Y-%m-%d"), hhmm, w.company.utc_offset, w.company.tz_label)


# ---------------------------------------------------------------------------- H05

def h05(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    key, inc = _key(w, rng), item.p["inc"]
    alert = item.p["alert"]
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 3)
    lab = then.labels(w, alert, s.name)
    days = [d for d, _, _ in item.p["canaries"]]
    n_canary = len(days)
    deploys, pd, slack, clocks, canaries = [], [], [], [], []
    for k, (day, hhmm, late) in enumerate(item.p["canaries"]):
        clock = _clock(w, day, hhmm)
        rs = rs_hash(rng)
        canary = pod_name(rng, f"{s.name}-canary", rs)
        ver = w.cal.version(day, 1 + (k % 2 if not late else 2))
        deploys += [_deploy_line(clock, 0, s.name, ver, f"canary 1/{s.pods} started ({canary}, weight 1%)"
                                 + (" hotfix" if late else "")),
                    _deploy_line(clock, 25, s.name, ver, "canary promoted")]
        row = page_row(w, rng, clock, 11, lab, 15)
        if row is not None:
            pd.append(row)
        slack += [f"{clock.local(11):%a %H:%M} {then.title(w, lab, receiver_prefix='slack-')}",
                  f"{clock.local(26):%a %H:%M} {then.title(w, lab, 'resolved', receiver_prefix='slack-')}"]
        clocks.append(clock)
        canaries.append(canary)
    n = 40
    last = clocks[-1]
    stable = pods(rng, s.name, 2)
    raws = http_series(rng, w, s.name, n, [s.rps * 1.3] * (n + 1), [0.002] * (n + 1), 2, pod_names=stable,
                       post=rng.uniform(0.1, 0.3))
    raws += http_series(rng, w, s.name, n, [0.25] * (n + 1), [0.4] * (n + 1), 1, pod_names=canaries[-1:])   # FA-7
    path = f"incidents/{inc}"
    files = {f"{path}/deploys.log": "\n".join(deploys) + "\n",
             f"{path}/pagerduty-incidents.csv": ev.pagerduty_csv(_pd_rows(rng, w, clocks[0], pd)),
             f"{path}/slack-alerts.txt": "\n".join(slack) + "\n",
             f"{path}/query-range/{s.name}-requests-during-canary.json": ev.query_range(raws, last)}
    lb = rng.choice(["0.1", "0.2", "0.3"])
    stop = item.p["stopgap"]
    stopper = person(w, item.p["person"]).first
    was, now = f"{float(s.err_thr) * 100:g}%", f"{float(s.err_thr) * 400:g}%"
    when = w.cal.span(days)
    count = {3: "Three", 4: "Four", 5: "Five"}[n_canary]
    title, body = variety.card(w, "H05", [
        (f"{alert_ref(w, item, alert, 'the ' + s.name + ' error-ratio alert')} pages on every canary",
         f"{count} pages {when}, each one within a quarter of an hour of a canary going out, each one resolved by "
         f"itself when the canary was promoted. The load balancer shows {lb}% errors on {s.name} the whole time. "
         f"{p[0].first} has stopped trusting this alert, which is the real problem. "
         + (f"{stopper} raised the threshold from {was} to {now} as a stopgap after the last one ({inc}); see the TODO "
            f"on the rule. " if stop else "")
         + f"Deploy log, PD export and the pod-level pull are in `{path}/`."),
        (f"{s.name}: false pages at deploy time",
         f"Every {s.name} rollout {when} paged {team} for errors the edge never saw ({lb}% at the LB). Canary weight is "
         f"1%. `{path}/` has the deploy log lined up with the pages."
         + (f" {stopper} bumped the threshold from {was} to {now} on the rule afterwards; that was meant to be "
            f"temporary." if stop else "")),
        (f"canary deploys keep paging {team}",
         f"{n_canary} canaries of {s.name} {when}, {n_canary} pages, none of them real: the canary takes 1% of traffic "
         f"and the LB never went above {lb}% errors. "
         + (f"There's a raised threshold on the rule from {stopper} ({inc}), please don't keep it. " if stop else "")
         + f"Everything lined up in `{path}/`.")])
    return Ticket(key, title, body, p[1], files)


# ---------------------------------------------------------------------------- H07

def h07(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    day = w.cal.past(rng, 1, 12, weekday=True)
    clock = _clock(w, day, "09:00")
    key = _key(w, rng)
    alert = item.p["alert"]
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 3)
    times = sorted(rng.sample(range(40, 520), 3))
    inc = w.cal.inc(day, "09:00", times[-1] + 30)
    lab = then.labels(w, alert, s.name)
    deploys, pd = [], []
    for k, t in enumerate(times):
        ver = w.cal.version(day, k + 1)
        deploys.append(_deploy_line(clock, t - 11, s.name, ver, f"rolling update started ({s.pods} pods, maxSurge 1)"))
        deploys.append(_deploy_line(clock, t - 11 + 2 * s.pods, s.name, ver, "rolling update complete"))
        row = page_row(w, rng, clock, t, lab, rng.randint(3, 7))
        if row is not None:
            pd.append(row)
    path = f"incidents/{inc}"
    files = {f"{path}/deploys.log": "\n".join(deploys) + "\n",
             f"{path}/pagerduty-incidents.csv": ev.pagerduty_csv(_pd_rows(rng, w, clock, pd))}
    hm = ", ".join(clock.hm(t) for t in times)
    title, body = variety.card(w, "H07", [
        (f"{alert_ref(w, item, alert, s.name)} pages at deploy time",
         f"Paged at {hm} on {clock.day()}. Every one of them lines up with a rollout in `{path}/deploys.log`, "
         f"and every one resolved on its own a few minutes later. The error rate on the LB didn't move. "
         f"{p[0].first}: \"I'd rather not mute it, it did catch the bad migration in August.\""),
        (f"{s.name} deploys page the on-call",
         f"Three pages {w.cal.rel(day)} ({hm}), all during rollouts, none with real errors behind them. "
         f"PD export and the deploy log are in `{path}/`."),
        (f"three pages, three {s.name} rollouts",
         f"{w.cal.rel(day)[:1].upper() + w.cal.rel(day)[1:]} {s.name} shipped three times and {team} got paged three "
         f"times, a few minutes into each rollout. Nothing was wrong. `{path}/` has the deploy log and the PD export.")])
    return Ticket(key, title, body, p[1], files)


# ---------------------------------------------------------------------------- H22

def h22(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    day = w.cal.past(rng, 1, 14, weekday=True)
    start = w.cal.office_start(rng, rng.random() < 0.25)
    clock = _clock(w, day, start)
    key = _key(w, rng)
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 4)
    ver = item.p["version"]
    rs = rs_hash(rng)
    lines, t = [], 12
    lines.append(_deploy_line(clock, t, s.name, ver, f"rolling update started ({s.pods} pods)"))
    gens = rng.randint(6, 9)
    for g in range(gens):
        lines.append(_deploy_line(clock, t + 6 * g + 1, s.name, ver, f"pod {pod_name(rng, s.name, rs)} CrashLoopBackOff, replaced"))
    lines.append(_deploy_line(clock, t + 6 * gens + 3, s.name, ver, "rollback to previous revision"))
    dur = 6 * gens + 3
    inc = w.cal.inc(day, start, t + 14)
    path = f"incidents/{inc}"
    lb = rng.randint(25, 45)
    shape = variety.card(w, "H22:thread", [0, 1, 2])
    if shape == 2:
        thread = [(clock.hm(t + 12), "incident-bot", f":rotating_light: {inc} declared by @{p[1].handle}: {s.name} rollout"),
                  (clock.hm(t + 13), p[1].handle, f"{ver} pods crashing on start, {lb}% 5xx at the edge"),
                  (clock.hm(t + 18), p[3].handle, f"{item.p['alert']} hasn't fired. is it even looking at {s.name}?"),
                  (clock.hm(t + 22), p[1].handle, "letting it run a bit longer to get a stack trace"),
                  (clock.hm(t + dur), p[1].handle, "rollback done, recovered")]
    elif shape == 0:
        thread = [(clock.hm(t + 14), p[0].handle, f"{s.name} error rate on the LB is ~{lb}%, anyone deploying?"),
                  (clock.hm(t + 16), p[1].handle, f"{ver} is rolling, pods keep getting replaced"),
                  (clock.hm(t + 19), p[0].handle, f"no page from {item.p['alert']}, and it's clearly above the threshold"),
                  (clock.hm(t + dur), p[1].handle, "rolled back, errors gone")]
    else:
        thread = [(clock.hm(t + 13), p[2].handle, f"LB 5xx for {s.name} jumped to {lb}%"),
                  (clock.hm(t + 15), p[0].handle, by_trait(p[0], terse=f"{ver} crashlooping",
                                                          chatty=f"it's {ver}, the new pods crashloop and get replaced one after another",
                                                          asks=f"is that {ver}? pods look like they're crashlooping")),
                  (clock.hm(t + 21), p[2].handle, f"still nothing from pagerduty for {team}"),
                  (clock.hm(t + 24), p[1].handle, "holding the rollout, want to see the logs first"),
                  (clock.hm(t + dur), p[0].handle, "rolled back. 5xx back to normal")]
    files = {f"{path}/deploys.log": "\n".join(lines) + "\n",
             f"{path}/timeline.md": _thread(header(w, rng, inc), thread)}
    title, body = variety.card(w, "H22", [
        (f"bad {s.name} rollout never paged",
         f"{ver} failed for {dur} minutes {w.cal.rel(day)} and {alert_ref(w, item, item.p['alert'], 'the error page')} stayed quiet, while the LB showed "
         f"around {lb}% of requests failing. The rollout kept replacing pods the whole time (`{path}/deploys.log`)."),
        (f"{alert_ref(w, item, item.p['alert'], 'pager')} silent through a {dur}-minute bad {s.name} deploy",
         f"{p[0].first} noticed on the LB dashboard. Thread and deploy log in `{path}/`. We want this paged "
         f"next time; README says what the page is meant to look like."),
        (f"no page during the {ver} crashloop",
         f"{s.name} {ver} crashlooped for {dur} minutes {w.cal.rel(day)}, about {lb}% of requests failed at the LB, and "
         f"nobody was paged. Deploy log and thread: `{path}/`.")])
    return Ticket(key, title, body, p[2], files)


# ---------------------------------------------------------------------------- H19

def h19(item, w, rng) -> Ticket:
    pp = item.p
    day = w.cal.past(rng, 2, 14)
    start = rng.choice(["02:05", "03:40", "23:15", "01:30"])
    clock = _clock(w, day, start)
    key = _key(w, rng)
    inc = w.cal.inc(day, start, 4)
    team = paged_team(w, pp["a1"])
    p = crew(w, rng, team, 3)
    dba = crew(w, rng, "database-reliability", 1)[0]
    rows, slack, fyi, count = [], [], [], 0
    for svc, k in pp["pods"].items():
        base = then.labels(w, item.ALERT, svc) or {"alertname": item.ALERT, "service": svc, "severity": "page"}
        labs, times = [], []
        for pod in pods(rng, svc, k + rng.randint(1, 3)):
            labs.append({**base, "instance": f"{ip(rng)}:8080", "namespace": w.namespace, "pod": pod})
            times.append(3 + rng.random() * 2.5)
        per_pod = len(then.group_by(w, labs[0], "slack-")) >= len(labs[0])
        # #sre-fyi groups every page the root's way, one line per service
        fyi.append((min(times), f"{clock.hm(min(times))} {ev.slack_title('firing', labs, ['alertname', 'service'])}"))
        if base.get("team") == team:   # the team's own channel
            if per_pod:   # every pod is its own group: a line (and a page) each
                for m, lab in zip(times, labs):
                    slack.append((m, f"{clock.hm(m)} {then.title(w, lab, receiver_prefix='slack-')}"))
            else:
                gb = then.group_by(w, labs[0], "slack-")
                slack.append((min(times), f"{clock.hm(min(times))} {ev.slack_title('firing', labs, gb)}"))
        for i, (m, lab) in enumerate(zip(times, labs)):
            pager = then.pager(w, lab)
            if pager is not None and lab.get("team") == team and (per_pod or i == 0):
                rows.append({"created_on": clock.iso(m), "service_name": f"{pager} (prod)",
                             "title": then.title(w, lab) if per_pod else ev.slack_title("firing", labs, then.group_by(w, lab)),
                             "urgency": "high", "status": "resolved", "acknowledged_by": p[0].full if i == 0 else "",
                             "resolved_on": clock.iso(m + rng.randint(11, 19))})
                count += 1
    slack.sort()
    fyi.sort()
    path = f"incidents/{inc}"
    files = {f"{path}/pagerduty-incidents.csv": ev.pagerduty_csv(numbered(w, rows)),
             f"{path}/slack-{team}-alerts.txt": f"#{team}-alerts\n\n" + "\n".join(x for _, x in slack) + "\n",
             f"{path}/slack-sre-fyi.txt": "#sre-fyi\n\n" + "\n".join(x for _, x in fyi) + "\n"}
    shape = variety.card(w, "H19:thread", [0, 1, 2])
    db = rng.choice(["primary db", "orders-db", "main postgres", "pg-primary"])
    if shape == 0:
        files[f"{path}/timeline.md"] = _thread(header(w, rng, inc), [
            (clock.hm(0), dba.handle, f"starting the {db} failover now, should be ~5 min (announced in #maintenance yesterday)"),
            (clock.hm(3), p[0].handle, "phone's going off. a lot."),
            (clock.hm(4), p[0].handle, f"{count} pages. all {item.ALERT}, one per pod"),
            (clock.hm(5), dba.handle, "expected, connections drop during the promote. done in a sec"),
            (clock.hm(7), dba.handle, "promoted, pools reconnecting"),
            (clock.hm(10), p[0].handle, f"everything resolved by itself. {pp['a1']} and {pp['a2']} paged once per pod, the other teams once per service?"),
            (clock.hm(11), p[0].handle, "ticket for the morning")])
    elif shape == 1:
        files[f"{path}/timeline.md"] = _thread(header(w, rng, inc), [
            (clock.hm(3), p[0].handle, by_trait(p[0], terse=f"{count} pages in two minutes??",
                                               chatty=f"just got {count} pages in two minutes, all for {pp['a1']} and {pp['a2']}",
                                               asks=f"anyone know why I just got {count} pages?")),
            (clock.hm(5), p[1].handle, f"db connection errors on every {pp['a1']} pod. is the database down?"),
            (clock.hm(9), dba.handle, f"that's the {db} failover, the planned one"),
            (clock.hm(10), p[0].handle, "oh, the planned one? nobody told the rota"),
            (clock.hm(12), dba.handle, "done now, connections are back")])
    body_extra = ""
    if shape == 2:   # no thread: the on-call wrote it up in the ticket the next morning
        body_extra = (f" From the night: the {db} failover started at {clock.hm(0)} (planned, announced in "
                      f"#maintenance), and within three minutes I had {count} pages, all the same database alert, one per pod of "
                      f"{pp['a1']} and {pp['a2']}. The DBA had it promoted by {clock.hm(7)} and everything resolved on "
                      f"its own.")
    title, body = variety.card(w, "H19", [
        (f"{count} pages for one database failover",
         f"The planned failover {w.cal.rel(day)} paged {team} {count} times in about three minutes. Every page was the "
         f"same problem on a different pod. Other teams got one page per service. Packet: `{path}/`. "
         f"We still want to know when a service can't reach its database, just once per service."),
        (f"page storm during the db failover ({team})",
         f"{count} PagerDuty incidents in three minutes, one per pod of {pp['a1']} and {pp['a2']}. The PD export, our "
         f"alert channel and #sre-fyi from that night are in `{path}/`."),
        (f"{team}: one page per pod is too many",
         f"A database blip shouldn't page {count} times. {pp['a1']} and {pp['a2']} lost their connections for a few "
         f"minutes during the failover and every pod paged on its own; one page per service is what we want. "
         f"`{path}/`.")])
    return Ticket(key, title, body + body_extra, p[2] if shape != 2 else p[0], files)


# ---------------------------------------------------------------------------- H20

def h20(item, w, rng) -> Ticket:
    pp = item.p
    day = w.cal.past(rng, 3, 16)
    start = rng.choice(["04:10", "12:30", "21:50", "15:05"])
    clock = _clock(w, day, start)
    key = _key(w, rng)
    inc = w.cal.inc(day, start, 5)
    p = crew(w, rng, "platform", 3)
    cu = then.labels(w, "ClusterUnreachable", None, cluster=pp["c1"]) or {
        "alertname": "ClusterUnreachable", "cluster": pp["c1"], "severity": "page", "team": "platform"}
    rows = [{"created_on": clock.iso(3), "service_name": f"{then.pager(w, cu) or 'platform'} (prod)", "title": then.title(w, cu),
             "urgency": "high", "status": "resolved", "acknowledged_by": ack(w, rng, "platform"), "resolved_on": clock.iso(41)}]
    # every service that had pods in c1 that night paged its owner (one incident each: group key alertname, service)
    for i, svc in enumerate(pp["packet"]):
        s = w.svc(svc)
        lab = then.labels(w, f"{s.camel}Down", svc, cluster=pp["c1"])
        if lab is None:
            continue
        row = page_row(w, rng, clock, 4 + 0.2 * i, lab, 37, acked=False)
        if row is not None:
            rows.append(row)
    path = f"incidents/{inc}"
    late = pp["late"]
    dedupe = then.has_down_dedupe(w)
    shape = variety.card(w, "H20:thread", [0, 1, 2])
    if shape == 2:
        lines = [(clock.hm(4), "incident-bot", f":rotating_light: {inc} declared by @{p[0].handle}: {pp['c1']} unreachable"),
                 (clock.hm(6), p[0].handle, "cloud provider has a control plane incident for the region"),
                 (clock.hm(9), p[1].handle, f"{len(pp['packet'])} service pages on top of the cluster one. we can't do anything with those"),
                 (clock.hm(13), p[2].handle, f"{late} wasn't in it, it moves to {pp['c1']} next week"),
                 (clock.hm(41), p[0].handle, "provider says mitigated, cluster reachable")]
        if dedupe:
            lines.insert(3, (clock.hm(10), p[2].handle, "isn't there a Down inhibition for this?"))
    elif shape == 0:
        lines = [(clock.hm(5), p[0].handle, f"{pp['c1']} control plane unreachable, cloud provider incident open"),
                 (clock.hm(6), p[1].handle, "why is my phone going off for every service in there"),
                 (clock.hm(11), p[1].handle, f"{late} is the only one that kept quiet, it doesn't run in {pp['c1']} "
                                             f"until the move next week"),
                 (clock.hm(40), p[0].handle, f"{pp['c1']} back")]
        if dedupe:
            lines.insert(2, (clock.hm(8), p[2].handle, "the Down dedupe inhibition was supposed to stop exactly this"))
    else:
        lines = [(clock.hm(4), p[0].handle, f"ClusterUnreachable for {pp['c1']}, provider status page has an incident"),
                 (clock.hm(7), p[2].handle, by_trait(p[2], terse="and every service owner in it just got paged too",
                                                    chatty="also every team with something in that cluster just got woken up, my phone included",
                                                    asks="did everyone with pods there just get paged as well?")),
                 (clock.hm(12), p[1].handle, f"not {late} though, that one only lands in {pp['c1']} with next week's move"),
                 (clock.hm(39), p[0].handle, f"provider recovered, {pp['c1']} reachable again")]
        if dedupe:
            lines.insert(2, (clock.hm(9), p[0].handle, "thought we had an inhibition for exactly this"))
    files = {f"{path}/pagerduty-incidents.csv": ev.pagerduty_csv(numbered(w, rows)),
             f"{path}/timeline.md": _thread(header(w, rng, inc), lines)}
    teams = and_list(sorted({paged_team(w, x) for x in pp["packet"]}))
    title, body = variety.card(w, "H20", [
        (f"{len(rows)} pages when {pp['c1']} dropped",
         f"When {pp['c1']} became unreachable {w.cal.rel(day)}, platform got `ClusterUnreachable`, which is right, "
         f"and every service owner with pods there got paged as well, which is what README says shouldn't happen. "
         f"{late} has moved into {pp['c1']} since, so next time it would be one more. PD export in `{path}/`."),
        (f"service pages during the {pp['c1']} outage",
         f"Platform was on it within a minute, but {teams} were woken up for a cluster they can't do anything "
         f"about. See `{path}/`."),
        (f"cluster outage paged every team",
         f"{pp['c1']} was unreachable for about 40 minutes {w.cal.rel(day)}. Only platform can do anything about that, "
         f"but every team with something running there got its own page. {late} moved into {pp['c1']} after that "
         f"night. PD export: `{path}/`.")])
    return Ticket(key, title, body, p[1], files)
