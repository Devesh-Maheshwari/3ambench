"""Incident packets for the E3 routing and inhibition tickets (knob `e3-evidence`).

What an on-call engineer attaches when routing goes wrong: an export of the alert channel the notifications
did land in, a PagerDuty export, a Grafana Explore pull of `ALERTS`, the routes test somebody ran. None of it
says which route or rule matched; working that out from the labels is the diagnosis. Every line is consistent
with the repo the queue starts from: notifications appear where the starting config sends them and nowhere else.
"""

from __future__ import annotations

import random

from . import evidence as ev

DAYS = 14
PAGES = ("page", "critical")


def _rng(w, item) -> random.Random:
    return random.Random(f"{w.tseed}:{item.rid}:e3-evidence")


def alerts_of(w, svc: str) -> list[tuple[str, str]]:
    """(alertname, severity) of every alert about the service as the starting repo has it: burn alerts this
    week's queue adds don't exist yet, and an alert whose severity the queue changes still has the old one."""
    from . import then
    files, _ = then.state(w)
    out = set()
    for f in files.values():
        for g in f.groups:
            for r in g["rules"]:
                if "alert" in r and f'service="{svc}"' in str(r.get("expr", "")):
                    lab = {**(g.get("labels") or {}), **(r.get("labels") or {})}
                    out.add((r["alert"], str(lab.get("severity", ""))))
    return sorted(out)


def _episodes(rng: random.Random, n: int, span: int, short=(4, 40)) -> list[tuple[int, int]]:
    """n firing episodes of one alert, none overlapping the one before (an alert fires once at a time)."""
    out, end = [], -10**9
    for s in sorted(rng.sample(range(0, span - 60), n)):
        d = rng.randint(*short)
        if s > end + 5:
            out.append((s, d))
            end = s + d
    return out


def _channel(clock: ev.Clock, events: list[tuple[float, dict, str]], group_by: list[str], header: str) -> str:
    """Slack lines in Alertmanager's default title format, firing and resolved, oldest first."""
    lines = []
    for m, labels, status in sorted(events, key=lambda e: e[0]):
        lines.append(f"{clock.local(m).strftime('%a %d %b %H:%M')} {ev.slack_title(status, [labels], group_by)}")
    return header + "\n\n" + ("\n".join(lines) if lines else "(no alert messages in this window)") + "\n"


def _fire(events, m, dur, labels):
    events.append((m, labels, "firing"))
    events.append((m + dur, labels, "resolved"))


def h25_packet(item, w, path: str, outage: tuple[str, int], dur: int = 30) -> dict[str, str]:
    """#alerts-default for two weeks: the new service's alerts, with no `team`, and nothing else (every other
    alert in the repo carries one)."""
    rng = _rng(w, item)
    s = w.svc(item.p["svc"])
    clock = ev.Clock(outage[0], "00:00", w.company.utc_offset, w.company.tz_label)
    span = DAYS * 1440
    events = []
    via = w.notes.get("pd_via")   # the SRE catch-all takes the pages first: only the rest reaches the root
    down = f"{s.camel}Down"
    for name, sev in alerts_of(w, s.name):
        if name not in item.p["alerts"] or name == down or (via and sev in PAGES):
            continue   # the Down page shows only the outage the ticket is about
        for m, d in _episodes(rng, rng.randint(2, 5), span):
            _fire(events, m - (DAYS - 1) * 1440, d, {"alertname": name, "service": s.name, "severity": sev})
    if down in item.p["alerts"] and not via:
        _fire(events, outage[1], dur, {"alertname": down, "service": s.name, "severity": "page"})
    return {f"{path}/alerts-default-channel.txt": _channel(clock, events, ["alertname", "service"],
                                                            f"#alerts-default, export of the last {DAYS} days")}


def h15_packet(item, w, path: str, date: str, alert: str) -> dict[str, str]:
    """The team's alert channel up to the day in the ticket: its pages are there next to the tickets and warnings
    (unless the SRE catch-all takes them first)."""
    rng = _rng(w, item)
    t = item.p["team"]
    clock = ev.Clock(date, "00:00", w.company.utc_offset, w.company.tz_label)
    span = DAYS * 1440
    events = []
    teamless = w.notes.get("teamless", set())   # alerts without `team` go to the root receiver, not this channel
    own = [x for x in w.services if x.team == t and x.name not in teamless]
    s0 = own[0] if own else None
    if s0 is not None and not w.notes.get("pd_via"):
        _fire(events, rng.randint(60, 1300), rng.randint(15, 50), {"alertname": alert, "service": s0.name,
                                                                    "severity": "page", "team": t})
    via = w.notes.get("pd_via")
    for s in own:
        for name, sev in alerts_of(w, s.name):
            if via and sev in PAGES:
                continue   # the SRE catch-all takes them before the team's block
            k = rng.randint(2, 4) if sev in PAGES else rng.randint(2, 8)
            for m, dur in _episodes(rng, k, span):
                _fire(events, m - (DAYS - 1) * 1440, dur, {"alertname": name, "service": s.name, "severity": sev, "team": t})
    return {f"{path}/slack-{t}-alerts.txt": _channel(clock, events, ["alertname", "service"],
                                                     f"#{t}-alerts, export of the last {DAYS} days")}


def h21_packet(item, w, path: str, date: str) -> tuple[dict[str, str], str]:
    """The team channel around a critical, and the Explore pull of the warning that never reached it."""
    rng = _rng(w, item)
    a0, a1, _ = item.p["alerts"]
    svc_of = {a: next(s for s in w.services if a.startswith(s.camel)) for a in (a0, a1)}
    s0, s1 = svc_of[a0], svc_of[a1]
    clock = ev.Clock(date, "08:00", w.company.utc_offset, w.company.tz_label)
    c0, c1 = rng.randint(100, 140), rng.randint(200, 240)   # the critical, firing for about an hour and a half
    events = []
    from . import then

    def base(a, s, sev):
        """The labels the alert carries in the starting repo (a moved service's alerts still say its old team)."""
        lab = dict(then.labels(w, a, s.name) or {"alertname": a, "service": s.name, "team": s.team})
        lab["severity"] = sev
        return {k: lab[k] for k in ("alertname", "service", "severity", "team") if k in lab}
    if not w.notes.get("pd_via"):   # with the SRE catch-all in place the critical goes to the SRE pager only
        _fire(events, c0, c1 - c0, base(a0, s0, "critical"))
    for name, sev in alerts_of(w, s1.name) + alerts_of(w, s0.name):
        if sev != "warning" or name in (a0, a1):
            continue
        for m, dur in _episodes(rng, 2, 360):
            d = min(dur, 30)
            if m + d < c0 - 5 or m > c1 + 5:   # warnings outside the critical's hour and a half get through
                _fire(events, m, d, base(name, s1 if name.startswith(s1.camel) else s0, sev))
    w1 = (c0 + rng.randint(10, 25), c1 - rng.randint(5, 15))   # a1's warning: firing, never posted
    # the export is of these teams' channels: an alert without `team` (or with a team that has no route yet) went
    # somewhere else
    chans = {then.team_word(w, s.name) for s in (s0, s1)}
    events = [e for e in events if e[1].get("team") in chans]
    posted = {e[1]["team"] for e in events}
    teams = sorted(t for t in chans if t in posted or t == then.team_word(w, s1.name))
    files = {f"{path}/slack-{'-and-'.join(teams)}-alerts.txt": _channel(
        clock, events, ["alertname", "service"], f"#{teams[0]}-alerts" + (f" and #{teams[1]}-alerts" if len(teams) > 1 else "")
        + f", {clock.day()} 08:00-14:00")}
    firing = [1 if w1[0] <= m <= w1[1] else None for m in range(361)]
    crit = [1 if c0 <= m <= c1 else None for m in range(361)]
    files[f"{path}/alerts-explore.csv"] = ev.explore_csv(clock, {
        ev.selector("ALERTS", {**base(a1, s1, "warning"), "alertstate": "firing"}): firing,
        ev.selector("ALERTS", {**base(a0, s0, "critical"), "alertstate": "firing"}): crit})
    return files, clock.hm(w1[0])


def h13_packet(item, w, path: str, rows_fn, clock: ev.Clock) -> dict[str, str]:
    """Two weeks of the SRE PagerDuty service: every team's pages landed there. Plus the routes test somebody ran
    after the rota change, which only tried warnings and tickets."""
    rng = _rng(w, item)
    teamless = w.notes.get("teamless", set())
    from . import then
    from .people import rota
    rows = []
    sre = rota(w, "sre")
    for s in w.services:
        for name, sev in alerts_of(w, s.name):
            if sev not in PAGES:
                continue
            lab = then.labels(w, name, s.name) or {"alertname": name, "service": s.name, "severity": sev}
            if s.name in teamless:
                lab.pop("team", None)
            for m, dur in _episodes(rng, rng.randint(1, 3), DAYS * 1440):
                rows.append({"created_on": clock.iso(m - DAYS * 1440), "service_name": "sre (prod)",
                             "title": then.title(w, lab), "urgency": "high",
                             "status": "resolved", "acknowledged_by": rng.choice(sre).full,
                             "resolved_on": clock.iso(m - DAYS * 1440 + dur)})
    # a team whose warnings and tickets still route the way README says in the starting config
    busy = set(w.notes.get("route_busy", set())) | {(w.trigger or {}).get("old"), (w.trigger or {}).get("new"),
                                                    w.notes.get("legacy_match_team")}
    t = next((x for x in sorted(w.teams) if x not in busy), w.teams[0])
    check = (f"$ amtool config routes test --config.file=alertmanager/alertmanager.yml team={t} severity=warning\n"
             f"slack-{t}\n"
             f"$ amtool config routes test --config.file=alertmanager/alertmanager.yml team={t} severity=ticket\n"
             f"jira-{t},slack-{t}\n")
    return {f"{path}/sre-pagerduty-last-{DAYS}-days.csv": ev.pagerduty_csv(rows_fn(rows)),
            f"{path}/routes-check-after-rota-change.txt": check}
