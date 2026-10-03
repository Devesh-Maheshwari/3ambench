"""Tickets and incident packets for the service-alert defects (H01, H03, H06), and the helpers every packet uses.

Every narrative states what people saw and what the pager did, never what is wrong with a rule. Numbers the
checks rely on live in README, an ADR, a runbook or a postmortem action item, never only here. Packets show the
repo as the queue found it (`then.py`): pages carry the labels and reach the rotas the starting repo sends them to.
"""

from __future__ import annotations

import random

from . import evidence as ev
from . import then, variety
from .items import Ticket
from .people import by_trait, crew, rota
from .vocab import flavor, poss
from .xscen import http_series, ip, pods, series, up_values


# ---------------------------------------------------------------------------- shared helpers

def _day(w, rng: random.Random, lo: int = 2, hi: int = 20, weekday: bool = False) -> str:
    """A day `lo`..`hi` days before the queue's handover, as `YYYY-MM-DD`."""
    return w.cal.iso(w.cal.past(rng, lo, hi, weekday))


def unique_key(w, prefix: str, n: int) -> str:
    """A ticket key nobody else in this task's queue already has (two tickets in one queue never share a key)."""
    used = w.notes.setdefault("ticket_keys", set())
    while f"{prefix}-{n}" in used:
        n += 1
    used.add(f"{prefix}-{n}")
    return f"{prefix}-{n}"


def _key(w, rng) -> str:
    return unique_key(w, w.company.key, rng.randint(2200, 2990))


def handle_of(w, first: str) -> str:
    return next((x.handle for x in w.people if x.first == first), first.lower())


def person(w, first: str):
    return next((x for x in w.people if x.first == first), w.people[0])


def _up_raws(rng, w, svc, n, vanish=None, down=None, k=3, names=None):
    """`up` for a service's targets as the API returns it: target labels on every series (R8)."""
    names = names or pods(rng, svc, k)
    out = []
    for name in names[:k]:
        lab = {"service": svc, "job": svc, "namespace": w.namespace, "instance": f"{ip(rng)}:8080", "pod": name}
        out.append(series("up", lab, up_values(n, down, vanish)))
    return out


def ack(w, rng, pager: str | None) -> str:
    """Whoever acked a page on that PagerDuty service: someone on its rota, by full name (R7)."""
    team = "sre" if pager in (None, "sre") else pager
    return rng.choice(rota(w, team)).full


def page_row(w, rng, clock: ev.Clock, m: float, lab: dict, back: float, acked: bool = True) -> dict | None:
    """One PagerDuty row for a page with these labels at minute `m` of `clock`, as the pristine routing delivered it;
    None when that page reached no PagerDuty service."""
    pager = then.pager(w, lab)
    if pager is None:
        return None
    return {"created_on": clock.iso(m), "service_name": f"{pager} (prod)", "title": then.title(w, lab),
            "urgency": "high", "status": "resolved", "acknowledged_by": ack(w, rng, pager) if acked else "",
            "resolved_on": clock.iso(m + back)}


def _pd_rows(rng, w, clock: ev.Clock, extra: list[dict], skip: set = frozenset()) -> list[dict]:
    """A PagerDuty export: a few unrelated pages from the days around the incident plus `extra`. Every row is a
    page the starting repo really sends to PagerDuty (the alert exists and pages, its route reaches a PagerDuty
    service), titled the way Alertmanager titles it, acked by someone on that rota, numbered by creation time."""
    touch = set().union(*[it.touch for it in w.items]) if w.items else set()
    now = w.cal.now_utc()
    rows = []
    others = list(w.services)
    rng.shuffle(others)
    for s in others[:5]:
        names = then.page_alerts(w, s.name, touch | set(skip))
        if not names:
            continue
        name = rng.choice(names)
        lab = then.labels(w, name, s.name, cluster=w.clusters[0] if w.clusters else None)
        m = rng.randint(-600, 900) + rng.randint(-4, 2) * 1440
        if clock.iso(m) >= now:
            m -= 3 * 1440
        row = page_row(w, rng, clock, m, lab, rng.randint(8, 70))
        if row is not None:
            rows.append(row)
    rows += [dict(x) for x in extra]
    return numbered(w, rows)


def numbered(w, rows: list[dict]) -> list[dict]:
    """Rows in creation order with PagerDuty's incident numbers (one counter per task, growing with time)."""
    rows = w.cal.number_rows([dict(r) for r in rows])
    return [{"incident_number": r.pop("incident_number"), **r} for r in rows]


def paged_team(w, svc: str) -> str:
    """The rota a service's pages went to before this week's fixes (its alerts' `team` label then)."""
    return then.team_word(w, svc)


def _thread(header: str, lines: list[tuple[str, str, str]]) -> str:
    out = [header, ""]
    for hm, who, msg in lines:
        out.append(f"{hm} {who}: {msg}")
    return "\n".join(out) + "\n"


def alert_ref(w, item, alert: str, generic: str) -> str:
    """How a symptom ticket refers to the alert it is about. People do name alerts in tickets, but a queue where
    every ticket names what to change can be worked by grep (RT-7): only the first symptom ticket of a queue uses
    the name, the others say what paged (the packets name the alert either way)."""
    first = next((it for it in (w.items or []) if it.ticket_like), None)
    return alert if first is None or first is item else generic


def part_of_day(clock, m: float = 0) -> str:
    """`night`, `morning`, `afternoon` or `evening` for minute `m` of a packet, the way people refer back to it."""
    h = clock.local(m).hour
    return "night" if h < 6 or h >= 22 else "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def header(w, rng, inc: str, slug: str = "") -> str:
    """The channel name at the top of an exported thread; not every team writes the zone."""
    ch = rng.choice([f"#inc-{inc[4:]}{'-' + slug if slug else ''}", f"#incident-{inc[4:]}", f"#inc-{inc[4:]}"])
    return f"{ch}  (times {w.company.tz_label})" if rng.random() < 0.7 else ch


# ---------------------------------------------------------------------------- H01

def h01(item, w, rng) -> Ticket:
    a, b = item.p["a"], item.p["b"]
    team = item.p["team"]
    date = _day(w, rng, 2, 18)
    n = 100
    v = rng.randint(28, 36)
    fixed = rng.randint(80, 92)
    start = rng.choice(["03:30", "02:50", "13:10", "22:40", "01:15", "04:05", "19:20"])
    clock = ev.Clock(date, start, w.company.utc_offset, w.company.tz_label)
    noticed = v + rng.randint(22, 31)
    inc, key = w.cal.inc(date, start, noticed), _key(w, rng)
    p = crew(w, rng, team, 4)
    # the bad release replaced the pods; the fix-forward brought new ones, under the right selector
    old = _up_raws(rng, w, a, n, vanish=v)
    new = _up_raws(rng, w, a, n)
    for r in new:
        r.values = [None] * fixed + r.values[fixed:]
    ups_b = _up_raws(rng, w, b, n)
    rps = [rng.uniform(4, 9) for _ in range(n + 1)]
    dead_end = http_series(rng, w, b, n, rps, [0.001] * (n + 1), 2, post=rng.uniform(0.1, 0.4))
    path = f"incidents/{inc}"
    files = {f"{path}/query-range/up-{a}.json": ev.query_range(old + new, clock),
             f"{path}/query-range/up-{b}.json": ev.query_range(ups_b, clock),
             f"{path}/query-range/{b}-requests.json": ev.query_range(dead_end, clock)}
    how = rng.choice(["merchant", "dashboard", "support"])
    fl = flavor(w.company.industry)
    first = {"merchant": f"anyone else seeing {a} errors from the edge? {fl['report']}",
             "dashboard": f"the {a} panel on svc-overview has been empty for a while, is that just grafana?",
             "support": f"support has {rng.randint(9, 23)} tickets about {a} in the last 20 min"}[how]
    rel = clock.hm(v - 2)
    shape = variety.card(w, "H01:thread", [0, 1, 2])
    if shape == 0:
        lines = [(clock.hm(noticed), p[0].handle, first),
                 (clock.hm(noticed + 2), p[1].handle, f"yeah, LB logs show 502s since ~{clock.hm(v + rng.randint(0, 2))}"),
                 (clock.hm(noticed + 3), p[1].handle, by_trait(p[1], terse="pagerduty is quiet though??",
                                                              chatty="and pagerduty is completely quiet, which is weird",
                                                              asks="why is pagerduty quiet?")),
                 (clock.hm(noticed + 6), p[0].handle, f"rolled back the config push to {b} from earlier, not that"),
                 (clock.hm(noticed + 11), p[2].handle, f"helm history shows a {a} release at {rel}, service selector changed"),
                 (clock.hm(noticed + 12), p[2].handle, f"targets page doesn't list {a} at all"),
                 (clock.hm(fixed - 1), p[0].handle, f"fixed forward, {a} back. handing the writeup to {p[3].first}")]
        if rng.random() < 0.5:
            lines.insert(4, (clock.hm(noticed + 8), p[3].handle, f"{b} looks fine fwiw, it's only {a}"))
    elif shape == 1:
        lines = [(clock.hm(noticed), "incident-bot", f":rotating_light: {inc} declared by @{p[0].handle}: {a} unreachable"),
                 (clock.hm(noticed + 1), p[0].handle, first),
                 (clock.hm(noticed + 3), p[1].handle, f"nothing in pagerduty for {team}, checked twice"),
                 (clock.hm(noticed + 4), p[2].handle, f"the {b} config change went out an hour ago, reverting it to rule it out"),
                 (clock.hm(noticed + 9), p[2].handle, f"revert done, no difference. {b} itself is serving fine"),
                 (clock.hm(noticed + 13), p[1].handle, f"{a} has zero endpoints. the {rel} deploy changed the selector labels"),
                 (clock.hm(noticed + 15), p[0].handle, "fixing forward, the chart change is one line"),
                 (clock.hm(fixed - 1), p[1].handle, "endpoints back, 502s stopped"),
                 (clock.hm(fixed + 2), p[0].handle, f"resolving. {p[3].first} has the writeup")]
    else:
        lines = [(clock.hm(noticed), p[0].handle, f"{fl['reporter']} escalated: {a} errors. on it" if how == "merchant"
                  else f"looking at {a}, {'support escalation' if how == 'support' else 'dashboards look wrong'}"),
                 (clock.hm(noticed + 5), p[0].handle, f"LB has 502 for everything on {a} since ~{clock.hm(v + 1)}. no page at all"),
                 (clock.hm(noticed + 7), p[1].handle, "status page set to investigating"),
                 (clock.hm(noticed + 10), p[2].handle, by_trait(p[2], terse=f"not {b}, rolled its change back anyway",
                                                               chatty=f"rolled back the {b} change just in case, it wasn't that",
                                                               asks=f"could it be the {b} change? rolled it back, no change")),
                 (clock.hm(noticed + 14), p[0].handle, f"found it: the {rel} release has the wrong selector, the service points at no pods"),
                 (clock.hm(fixed - 1), p[0].handle, "fixed forward"),
                 (clock.hm(fixed + 1), p[1].handle, "status page resolved")]
    files[f"{path}/timeline.md"] = _thread(header(w, rng, inc, item.p["prefix"]), lines)
    manual = {"created_on": clock.iso(noticed + 1), "service_name": f"{team} (prod)",
              "title": f"Manually triggered: {a} errors reported by {fl['reporter'].split(' ', 1)[-1] if how == 'merchant' else 'support'}",
              "urgency": "high", "status": "resolved", "acknowledged_by": p[0].full if p[0].team == team else ack(w, rng, team),
              "resolved_on": clock.iso(fixed + 3)}
    files[f"{path}/pagerduty-incidents.csv"] = ev.pagerduty_csv(_pd_rows(rng, w, clock, [manual]))
    minutes = fixed - v
    who = ("An escalation from " + fl["reporter"]) if how == "merchant" else "Support" if how == "support" else \
        "Someone looking at a dashboard"
    title, body = variety.card(w, "H01", [
        (f"{a} down ~{round(minutes, -1)} min {clock.day()} night, no page" if int(start[:2]) < 6 or int(start[:2]) >= 22
         else f"{a} down ~{round(minutes, -1)} min on {clock.day()}, no page",
         f"{who} reached {team} on-call at {clock.hm(noticed)} {w.company.tz_label}; the edge was returning 502s for {a} "
         f"from about {clock.hm(v)} until the fix-forward at {clock.hm(fixed)}. Nothing paged. Packet: `{path}/`.\n\n"
         f"We want to be paged for this next time, by the {team} rota, not by a customer."),
        (f"no page for the {a} outage on {date[5:]}",
         f"{a} was hard down for about {minutes} minutes after a bad chart release ({clock.hm(v)}-{clock.hm(fixed)} "
         f"{w.company.tz_label}). The Down alert for it never went off. {p[3].first} has the thread and the "
         f"`up` pulls in `{path}/`.\n\nWhatever the fix is, it should page {team} when either of their two services "
         f"goes like this."),
        (f"{a} outage: pager silent for {minutes} min",
         f"From the IC notes: first signal was {fl['reporter'] if how == 'merchant' else 'a support ticket' if how == 'support' else 'a dashboard'} "
         f"at {clock.hm(noticed)}, not the pager. {b} was fine the whole time. See `{path}/timeline.md`; the "
         f"query_range pulls are next to it.")])
    return Ticket(key, title, body, p[0], files)


# ---------------------------------------------------------------------------- H03

def h03_times(w, rng, stopgap: bool) -> dict:
    """When the H03 incident happened (drawn when the item is set up: the stopgap's TODO carries its time)."""
    date = _day(w, rng, 3, 19)
    start = rng.choice(["00:40", "01:05", "23:50", "02:20"]) if stopgap else rng.choice(["00:40", "01:05", "15:20", "10:35"])
    onset, end = rng.randint(28, 34), rng.randint(62, 70)
    stop = onset + rng.randint(25, 35) if stopgap else None
    if stop is not None:
        end = max(end, stop + rng.randint(3, 8))
    clock = ev.Clock(date, start, w.company.utc_offset, w.company.tz_label)
    return {"date": date, "start": start, "onset": onset, "end": end, "stop": stop,
            "stop_hm": clock.hm(stop) if stop is not None else None, "inc": w.cal.inc(date, start, onset + 4)}


def _rule_path(f) -> str:
    """Where a rule file sits on the Prometheus pod: the operator writes PrometheusRules into its own config map."""
    if f.gen == 3:
        import hashlib
        uid = hashlib.sha256(f.stem.encode()).hexdigest()
        return (f"/etc/prometheus/rules/prometheus-k8s-rulefiles-0/monitoring-{f.stem}-"
                f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}.yaml")
    return f"/etc/prometheus/rules/{f.stem}.{f.ext}"


def h03(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    tm = item.p["times"]
    inc, key = tm["inc"], _key(w, rng)
    clock = ev.Clock(tm["date"], tm["start"], w.company.utc_offset, w.company.tz_label)
    n = 90
    onset, end, stop = tm["onset"], tm["end"], tm["stop"]
    T = float(s.err_thr)
    err = [T * rng.uniform(0.1, 0.3) if not onset <= m < end else T * rng.uniform(2.2, 4.0) for m in range(n + 1)]
    codes = {"500": [1.0 if not onset <= m < end else 0.25 for m in range(n + 1)],
             "503": [0.0 if not onset <= m < end else 0.75 for m in range(n + 1)]}
    raws = http_series(rng, w, s.name, n, [s.rps * rng.uniform(0.9, 1.1) for _ in range(n + 1)], err, s.pods, codes,
                       post=rng.uniform(0.1, 0.35))
    path = f"incidents/{inc}"
    files = {f"{path}/query-range/{s.name}-requests-by-code.json":
             ev.query_range(raws, clock, lambda r: r.labels.get("code", "").startswith("5") or r.labels.get("code") == "200")}
    # the rule as the server had it that night: the starting repo's rule, and before the stopgap went in, the same
    # rule on its old 5m window (S7, FA-6)
    f, g, rule = then.rules(w, item.p["alert"])[0]
    idx = g["rules"].index(rule)
    before = {**rule, "expr": item.broken_expr(s, "5m")}
    err_s = "multiple matches for labels: many-to-one matching must be explicit (group_left/group_right)"
    use_log = rng.random() < 0.6
    if use_log:
        mins = [m + 0.37 for m in range(onset + 1, end + 1)]
        files[f"{path}/prometheus.log"] = ev.prom_eval_error_log(
            clock, mins, _rule_path(f), g["name"], item.p["alert"],
            lambda m: ev.rule_yaml(before if stop is not None and m < stop else rule), err_s, idx)
    else:
        files[f"{path}/rules-api.json"] = ev.rules_api(clock, end - 1, _rule_path(f), g["name"], rule, err_s,
                                                       round(rng.uniform(0.0004, 0.0031), 9))
    team = paged_team(w, s.name)
    p = crew(w, rng, team, 4)
    stopper = person(w, item.p["person"])
    up = flavor(w.company.industry)["upstream"]
    shape = variety.card(w, "H03:thread", [0, 1, 2])
    if shape == 0:
        lines = [(clock.hm(onset + 4), p[0].handle, f"{s.name} 503s climbing on the LB dashboard, upstream timeouts?"),
                 (clock.hm(onset + 9), p[1].handle, f"nothing in pagerduty for {team}? I only noticed because I had grafana open"),
                 (clock.hm(onset + 15), p[0].handle, f"{up} status page says degraded"),
                 (clock.hm(onset + 22), p[2].handle, f"can someone check why {item.p['alert']} didn't go off"),
                 (clock.hm(end - 1), p[0].handle, "provider recovered, 503s gone")]
    elif shape == 1:
        lines = [(clock.hm(onset + 3), p[0].handle, f"503s on {s.name} from the LB, ~{round(100 * T * 3)}% of traffic"),
                 (clock.hm(onset + 5), p[0].handle, f"@{team}-oncall ?"),
                 (clock.hm(onset + 12), p[1].handle, "here. wasn't paged"),
                 (clock.hm(onset + 14), p[1].handle, f"{up} status page: degraded"),
                 (clock.hm(onset + 20), p[2].handle, f"{item.p['alert']} shows err on the rules page. not pending, err"),
                 (clock.hm(onset + 21), p[2].handle, by_trait(p[2], terse="no idea what that means at this hour, leaving it",
                                                             chatty="no idea what that means and it's the middle of the night, leaving it for the morning",
                                                             asks="anyone know what err means there? leaving it for now")),
                 (clock.hm(end - 1), p[0].handle, f"{up.replace('the ', '')} back, 503s gone")]
    else:
        lines = [(clock.hm(onset + 6), "incident-bot", f":rotating_light: {inc} declared by @{p[0].handle}: {s.name} 5xx"),
                 (clock.hm(onset + 7), p[0].handle, f"found it on the {s.name} board, not the pager. mostly 503"),
                 (clock.hm(onset + 10), p[1].handle, f"timeouts to {up}, their side"),
                 (clock.hm(onset + 18), p[1].handle, by_trait(p[1], terse="why no page though",
                                                             chatty="what I don't get is why nobody got paged for this",
                                                             asks=f"shouldn't {item.p['alert']} have paged by now?")),
                 (clock.hm(end - 1), p[0].handle, "recovered on their end. closing")]
    if stop is not None:
        said = variety.card(w, "H03:stopgap", [
            "widened the error ratio window to 15m so it stops missing the bursts. TODO to revert once someone looks properly",
            f"bumped the ratio window to 15m on {item.p['alert']} so it catches the bursts, will revert",
            "pushed a 15m window on the error ratio as a stopgap, left a TODO on the rule"])
        lines.append((clock.hm(stop), stopper.handle, said))
        lines.sort(key=lambda x: x[0] if x[0] >= tm["start"] else "~" + x[0])
    files[f"{path}/timeline.md"] = _thread(header(w, rng, inc), lines)
    files[f"{path}/pagerduty-incidents.csv"] = ev.pagerduty_csv(_pd_rows(rng, w, clock, []))
    src = f"the Prometheus log from that {part_of_day(clock, onset)}" if use_log else (
        "a paste of the rules API from the next morning" if part_of_day(clock, onset) in ("night", "evening")
        else "a paste of the rules API from later that day")
    share = round(100 * sum(err[onset:end]) / max(1, end - onset))
    dur = end - onset
    title, body = variety.card(w, "H03", [
        (f"{s.name} 503s {clock.hm(onset)}-{clock.hm(end)}, silent pager",
         f"Nobody got paged for {dur} min of 503s on {s.name}. "
         + (f"{stopper.first} widened the error-ratio window to 15m at {tm['stop_hm']} so it would \"stop missing the "
            f"bursts\" and left a TODO on it; that's still in the repo. " if stop is not None else "")
         + f"`{path}/` has {src} and the per-code counters."),
        (f"why didn't {alert_ref(w, item, item.p['alert'], 'the ' + s.name + ' error page')} fire on {clock.day()}?",
         f"{team} were not paged during the upstream timeouts ({clock.hm(onset)} to {clock.hm(end)} "
         f"{w.company.tz_label}), even though about {share}% of requests were failing for most of it. "
         + (f"There's a stopgap from {stopper.first} on the rule from that night. " if stop is not None else "")
         + f"Packet in `{path}/`."),
        (f"{s.name}: {dur} minutes of 5xx, no page",
         f"Found by {p[0].first} on a dashboard, not by the pager. Upstream timeouts made {s.name} return 503s. "
         + (f"{stopper.first}'s change from the incident is merged; please look at it with fresh eyes. " if stop is not None else "")
         + f"Everything we pulled is in `{path}/`.")])
    return Ticket(key, title, body, p[1], files)


# ---------------------------------------------------------------------------- H06

def replication_runbook(s, w) -> str:
    stale = flavor(w.company.industry)["stale"]
    return variety.card(w, "any:H06:runbook", [
        f"""---
alerts: [ReplicationLagHigh]
---
# ReplicationLagHigh

Pages when a {s.name} read replica has been more than 30 seconds behind the primary for 10 minutes.
Reads from a lagging replica serve {stale}, so this one pages.

1. Check `pg_stat_replication` on the primary: is the replica connected at all?
2. Long-running queries on the replica block replay; look for anything older than a few minutes.
3. If the replica is gone for good, take it out of the read pool (`{s.name}` config `READ_REPLICAS`).
""",
        f"""---
alerts: [ReplicationLagHigh]
---
# Replica lag ({s.name})

**When it pages:** a replica more than 30 seconds behind the primary, for 10 minutes straight. Anything reading
from it serves {stale} until it catches up.

- `SELECT client_addr, state, replay_lag FROM pg_stat_replication;` on the primary.
- A replica that is connected but not replaying is usually a long query holding it back: kill it.
- Out of the read pool: `READ_REPLICAS` in the {s.name} config, then a rolling restart.
""",
        f"""---
alerts: [ReplicationLagHigh]
---
# ReplicationLagHigh

{s.name} reads from its replicas. Once a replica falls 30s behind the primary and stays there for 10 minutes,
whoever is on call gets paged, because {stale} goes out to users while it lags.

What usually helps:
- replica disconnected (check `pg_stat_replication` on the primary): get database-reliability involved;
- replay blocked by a long-running query on the replica: cancel it;
- replica unrecoverable: drop it from `READ_REPLICAS` and redeploy.
"""])


def h06(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    key = _key(w, rng)
    date = _day(w, rng, 2, 16)
    start = rng.choice(["02:10", "09:20", "17:45", "06:35", "13:05", "21:40"])
    clock = ev.Clock(date, start, w.company.utc_offset, w.company.tz_label)
    inc = w.cal.inc(date, start, 70)
    n = 75
    t0 = rng.randint(6, 10)
    lag, v = [], rng.uniform(0.6, 2.5)
    for m in range(n + 1):
        v = rng.uniform(0.4, 3.9) if m < t0 else v + rng.uniform(2.4, 5.6)   # R8: a noisy climb, float seconds
        lag.append(round(v, 3))
    team = paged_team(w, s.name)
    raws = [series("pg_replication_lag_seconds", {"service": s.name, "instance": f"{ip(rng)}:9187",
                                                  "job": "postgres-exporter", "namespace": w.namespace}, lag)]
    path = f"incidents/{inc}"
    cols = {}
    for m, x in enumerate(lag):
        if x > 30:
            name = ev.selector("ALERTS", {"alertname": "ReplicationLagHigh", "alertstate": "pending",
                                         "current": f"{ev.humanize(x)}s", "service": s.name, "severity": "page",
                                         "team": team})
            col = [None] * (n + 1)
            col[m] = 1
            cols[name] = col
    files = {f"{path}/query-range/replication-lag.json": ev.query_range(raws, clock),
             f"{path}/alerts-explore.csv": ev.explore_csv(clock, cols)}
    p = crew(w, rng, team, 2)
    cross = next(m for m, x in enumerate(lag) if x > 30)
    found = rng.randint(n - 12, n)
    behind = max(1, round(lag[found] / 60))
    stale = flavor(w.company.industry)["stale"]
    if w.family == "E5":
        body = variety.card(w, "survey:H06", [
            f"ReplicationLagHigh is broken, I think. On {clock.day()} the {s.name} replica was {behind} minutes behind "
            f"and I only found out because {stale} showed up in a support ticket. The alerts page had it pending the "
            f"whole time. Explore export and the lag pull: `{path}/`.",
            f"I watched the replica lag alert sit in pending for an hour on {clock.day()} while the {s.name} replica fell "
            f"further behind. It never fired. What's the point of a page that can't page? `{path}/` has what I saw.",
            f"The replica lag alert doesn't work. {poss(s.name)} replica passed 30s at {clock.hm(cross)} and was "
            f"{behind} minutes behind when I looked at {clock.hm(found)}. Pending the whole time, never fired. `{path}/`."])
        return Ticket(key, "ReplicationLagHigh never fires", body, p[1], files)
    title, body = variety.card(w, "H06", [
        (f"{alert_ref(w, item, 'ReplicationLagHigh', 'replica lag alert')} sat in pending for an hour",
         f"{poss(s.name)} replica fell behind from about {clock.hm(t0)} and kept going. The alert showed pending on the "
         f"Prometheus alerts page the whole time and never fired; {p[0].first} found it at {clock.hm(found)} with the "
         f"replica {behind} minutes behind. Explore export and the lag pull are in `{path}/`."),
        (f"lag alert never pages ({s.name})",
         f"Replica lag on {s.name} passed 30s at {clock.hm(cross)} and was still climbing an hour later. No page. "
         f"The Explore CSV in `{path}/` is what the alerts page looked like."),
        (f"{s.name} served {stale} for an hour, no page",
         f"A customer report about {stale} took us to the {s.name} read replica at {clock.hm(found)}: about {behind} "
         f"minutes behind the primary. The lag alert had been pending since {clock.hm(cross)}. Pulls in `{path}/`.")])
    return Ticket(key, title, body, p[1], files)
