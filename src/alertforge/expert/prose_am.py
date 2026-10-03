"""Tickets for the E3 routing and inhibition defects (H13, H15, H21, H25)."""

from __future__ import annotations

from . import evidence as ev
from . import knobs, then, variety
from . import prose_e3 as e3
from .items import Ticket
from .people import crew
from .prose_svc import _day, _key, _pd_rows, ack, paged_team
from .vocab import poss


def h13(item, w, rng) -> Ticket:
    quiet = w.notes.get("pd_quiet", set())  # a service whose alerts carry no team can't be this story
    s = rng.choice([x for x in w.services if x.kind == "api" and x.name not in quiet]
                   or [x for x in w.services if x.kind == "api"])
    date = _day(w, rng, 2, 10)
    clock = ev.Clock(date, rng.choice(["03:20", "05:05", "22:30", "01:50"]), w.company.utc_offset, w.company.tz_label)
    lab = then.labels(w, f"{s.camel}Down", s.name) or {"alertname": f"{s.camel}Down", "service": s.name,
                                                       "severity": "page", "team": paged_team(w, s.name)}
    rows = [{"created_on": clock.iso(0), "service_name": f"{then.pager(w, lab) or 'sre'} (prod)", "title": then.title(w, lab),
             "urgency": "high", "status": "resolved", "acknowledged_by": ack(w, rng, "sre"), "resolved_on": clock.iso(34)}]
    path = f"incidents/{w.cal.inc(date, clock.hm(0), 2)}"
    files = {f"{path}/pagerduty-incidents.csv": ev.pagerduty_csv(_pd_rows(rng, w, clock, rows))}
    team = paged_team(w, s.name)
    title, body = variety.card(w, "H13", [
        (f"{team} on-call never got {s.camel}Down on {clock.day()}; SRE did",
         f"SRE got paged at {clock.hm(0)}, called around for twenty minutes to find who owns {s.name}, and only then "
         f"woke {team}. Every team has had its own rota since {item.p['quarter']}. SRE still wants to see every "
         f"page, just not be the one paged for it. PD export: `{path}/`."),
        (f"pages still go to the SRE pager",
         f"{team} haven't been paged directly once this month; everything goes through the SRE pager first. "
         f"That was meant to end when the team rotas started. SRE keep watching pages in #sre-fyi. See `{path}/`."),
        (f"SRE is still everyone's first pager",
         f"{w.cal.rel(clock.local(0).date())[:1].upper() + w.cal.rel(clock.local(0).date())[1:]} at {clock.hm(0)} the SRE "
         f"phone rang for {s.camel}Down, and SRE had to find and wake {team}. Team rotas have been live since "
         f"{item.p['quarter']}; pages should go to them directly, and SRE only wants the copy in #sre-fyi. `{path}/`.")])
    if knobs.on("e3-evidence"):
        files.update(e3.h13_packet(item, w, path, lambda rows: _pd_rows(rng, w, clock, rows), clock))
        body += variety.card(w, "H13:extra", [
            " The SRE PagerDuty export for the last two weeks and the routes check someone ran after the rota change "
            "are there too.",
            " Also in there: two weeks of the SRE service's PagerDuty history and the routes test from after the rota "
            "change.",
            " Next to it are the SRE PagerDuty export (last 14 days) and the output of the routes test somebody ran "
            "when the rotas changed."])
    return Ticket(_key(w, rng), title, body, crew(w, rng, "sre", 1)[0], files)


def h15(item, w, rng) -> Ticket:
    t = item.p["team"]
    teamless = w.notes.get("teamless", set())   # a service whose alerts carry no team is another ticket's story
    s = next((x for x in w.services if x.team == t and x.name not in teamless),
             next((x for x in w.services if x.team == t), None))
    alert = f"{s.camel}Down" if s else "their Down alert"
    date = _day(w, rng, 2, 12)
    if w.notes.get("pd_via"):
        # the SRE catch-all takes every page first, so the team's own page route has never been exercised
        opts = [(f"{t} haven't been paged in weeks",
                 f"{alert} fired on {date[5:]}. SRE got it and called {t}; the {t} rota itself got nothing, and "
                 f"neither did #{t}-alerts. Tickets for them do arrive in Jira. Their routing is the oldest block in "
                 f"the file and nobody has looked at it since the rota started."),
                (f"{t}: their own pager never rings",
                 f"{poss(t)} on-call has had PagerDuty set up for months and it has never paged them; everything comes "
                 f"through SRE. Their routing block is the oldest in the file."),
                (f"{t} pager silent since the rota started",
                 f"Not one page has reached the {t} rota directly; SRE forwards them by phone. Their Jira tickets "
                 f"arrive fine. The {t} block in the Alertmanager config predates everyone else's.")]
    else:
        opts = [(f"{t} haven't been paged in weeks",
                 f"{alert} fired on {date[5:]} and only ever showed up in #{t}-alerts. Nobody on the {t} rota got a "
                 f"page. Tickets for them do arrive in Jira. Their routing is the oldest block in the file."),
                (f"{t}: pages end up in Slack only",
                 f"{poss(t)} on-call says they get Slack messages for pages but PagerDuty stays quiet, since forever as far as "
                 f"they can tell."),
                (f"no PagerDuty for {t}",
                 f"{t} see their pages as Slack messages in #{t}-alerts, and their phones never ring. Tickets and "
                 f"warnings are fine. Somebody on {t} found out the hard way on {date[5:]}.")]
    title, body = variety.card(w, "H15", opts)
    files = {}
    if knobs.on("e3-evidence"):
        path = f"incidents/{w.cal.inc(date, '12:00')}"
        files = e3.h15_packet(item, w, path, date, alert)
        body += variety.card(w, "H15:extra", [f" Their channel export for the last two weeks is in `{path}/`.",
                                              f" `{path}/` has the last two weeks of #{t}-alerts.",
                                              f" I exported #{t}-alerts for the past fortnight: `{path}/`."])
    return Ticket(_key(w, rng), title, body, crew(w, rng, t, 1)[0], files)


def h21(item, w, rng) -> Ticket:
    a = item.p["alerts"]
    date = _day(w, rng, 2, 12)
    title, body = variety.card(w, "H21", [
        (f"warnings disappear whenever anything is critical",
         f"While {a[0]} was critical on {date[5:]}, the {a[1]} warning never reached Slack, and neither did "
         f"any other warning in the namespace. We only want a critical to hold back its own warning copy."),
        (f"critical alerts mute unrelated warnings",
         f"A critical {a[0]} silenced every warning in the namespace for an hour, including one we needed. "
         f"README says what that inhibition is supposed to do."),
        (f"{a[1]} never showed up in Slack",
         f"{a[1]} was firing on {date[5:]} (it's in the Explore pull) and never posted. At the same time {a[0]} was "
         f"critical. Those two have nothing to do with each other.")])
    files = {}
    if knobs.on("e3-evidence"):
        path = f"incidents/{w.cal.inc(date, '08:00', 100)}"
        files, at = e3.h21_packet(item, w, path, date)
        body += variety.card(w, "H21:extra", [
            f" Slack export and an Explore pull of `ALERTS` from the morning it happened are in `{path}/`; the "
            f"{a[1]} warning starts firing at {at} and never shows up in the channel.",
            f" In `{path}/`: the channel from that morning and an Explore pull of `ALERTS`. {a[1]} goes to firing at "
            f"{at}; the channel never gets it.",
            f" The Explore pull of `ALERTS` in `{path}/` has {a[1]} firing from {at}, and the channel export next to "
            f"it has nothing for it."])
    team = paged_team(w, next(x.name for x in w.services if a[1].startswith(x.camel)))
    return Ticket(_key(w, rng), title, body, crew(w, rng, team, 1)[0], files)


def h25(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    date = _day(w, rng, 2, 10)
    clock = ev.Clock(date, rng.choice(["02:10", "04:40", "23:35"]), w.company.utc_offset, w.company.tz_label)
    dur = rng.randint(24, 40)   # R6: an outage nobody wrote up stays short
    if w.notes.get("pd_via"):
        # the SRE catch-all (H13) takes every page, team or not, so the page did reach a pager: SRE's
        opts = [(f"{s.name} was down from {clock.hm(0)} to {clock.hm(dur)} and {s.team} never heard",
                 f"The Down page went to the SRE pager with no team on it, and SRE couldn't tell whose it was; the "
                 f"rest of its alerts sit in #alerts-default. {s.name} moved into this Prometheus last sprint. "
                 f"{s.team} own it (ownership.yaml)."),
                (f"{s.name} alerts reach nobody who owns it",
                 f"{s.name} pages arrive on the SRE pager without a team, and everything else it sends lands in "
                 f"#alerts-default, which nobody reads. They should reach {s.team} like everything else they own."),
                (f"who owns {s.name} pages?",
                 f"SRE was paged for {s.name} {w.cal.rel(clock.local(0).date())} with no team on the page and spent "
                 f"{dur} minutes on something {s.team} could have fixed in five. {s.name} came over to this Prometheus "
                 f"last sprint.")]
    else:
        opts = [(f"{s.name} was down from {clock.hm(0)} to {clock.hm(dur)} and nobody was paged",
                 f"The alert did fire; it's sitting in #alerts-default. {s.name} moved into this Prometheus last sprint. "
                 f"{s.team} own it (ownership.yaml)."),
                (f"{s.name} alerts go nowhere",
                 f"{s.name} alerts fire into #alerts-default and nobody reads that channel. They should reach {s.team} "
                 f"like everything else they own."),
                (f"{s.name}: alerts land in #alerts-default",
                 f"Since {s.name} moved into this Prometheus last sprint, its alerts post to #alerts-default and page "
                 f"nobody. {w.cal.rel(clock.local(0).date())[:1].upper() + w.cal.rel(clock.local(0).date())[1:]} it was "
                 f"down for {dur} minutes and {s.team} heard about it the next morning.")]
    title, body = variety.card(w, "H25", opts)
    files = {}
    if knobs.on("e3-evidence"):
        path = f"incidents/{w.cal.inc(date, clock.hm(0), 1)}"
        h, mi = (int(x) for x in clock.hm(0).split(":"))
        files = e3.h25_packet(item, w, path, (clock.local(0).strftime("%Y-%m-%d"), h * 60 + mi), dur)
        body += variety.card(w, "H25:extra", [f" Export of #alerts-default for the last two weeks: `{path}/`.",
                                              f" `{path}/` has #alerts-default for the past 14 days.",
                                              f" Two weeks of #alerts-default are in `{path}/`."])
    return Ticket(_key(w, rng), title, body, crew(w, rng, s.team, 1)[0], files)
