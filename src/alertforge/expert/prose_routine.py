"""Queue text for routine requests (R01, R03, R04/H17, R05, R06). Several phrasings each, spread evenly over the
tasks that carry the kind (`variety.card`); the request states the outcome and where the facts live, never the
mechanism."""

from __future__ import annotations

from . import then, variety
from .items import Ticket
from .people import crew
from .prose_svc import _key
from .vocab import poss


def adr(w, which: str) -> str:
    return w.notes.get("adr", {}).get(which, {"scrape": "0003", "slo": "0007", "disk": "0012"}[which])


def r01(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    a = adr(w, "slo")
    fast = item.tier == "fast"
    both = {(s.name, "fast"), (s.name, "slow")} <= set(w.notes.get("burn_new", set()))
    if fast:
        title, body = variety.card(w, "R01f", [
            (f"{s.name}: fast burn page",
             f"{s.name} got its own SLO in the catalog this sprint (`slo/services.yaml`). It needs the fast-burn page "
             f"per ADR {a}, named the usual way."),
            (f"burn alerts for {s.name}",
             f"Could you add the {s.name} fast-burn page? SLO and window are in the catalog; the policy is ADR {a}. "
             f"{s.team} signed off on paging for it."),
            (f"page {s.team} when {s.name} burns budget",
             f"{s.team} agreed at the SLO review that {s.name} should page on fast budget burn. Catalog entry is in, "
             f"nothing alerts on it yet. ADR {a} has the policy."),
            (f"{s.name} has an SLO and no burn page",
             f"New catalog entry for {s.name} (merged last week). The page for a fast burn is missing; {s.team} want it "
             f"like every other API's, per ADR {a}.")])
    else:
        opts = [(f"{s.name} slow burn into the {s.team} queue",
                 f"Slow budget burn on {s.name} should land in {poss(s.team)} Jira queue, not wake anyone. ADR {a} has "
                 f"the policy; mind the catalog window."),
                (f"slow burn for {s.name}",
                 f"{s.team} want the slow-burn ticket for {s.name} as well (ADR {a}, catalog entry is already merged)."),
                (f"{s.name}: slow-burn ticket",
                 f"{s.name} is on the catalog now. Please add the ticket for slow budget burn (ADR {a}). Low-volume "
                 f"API, so the catalog gives it its own window; don't copy the numbers from another service.")]
        if both:   # the fast-burn ticket sits right before this one in the queue (texts.order_queue)
            opts[1] = (f"{s.name}: slow burn ticket",
                       f"And the slow-burn ticket for {s.name}, same ADR. It's a low-volume API, check which window the "
                       f"catalog puts it on before copying numbers from another service.")
        title, body = variety.card(w, "R01s", opts)
    return Ticket(_key(w, rng), title, body, crew(w, rng, s.team, 1)[0])


def _r06_audience(w, svc: str, source: str) -> tuple[str, str]:
    """FA-5: who actually got those pages in the starting repo. ('team', <team>) when they reached the owner's
    pager, ('sre', 'sre') when the SRE pager took them, ('slack', <channel>) when nothing paged at all."""
    lab = then.labels(w, source, svc)
    if lab is None:
        names = then.page_alerts(w, svc)
        lab = then.labels(w, names[0], svc) if names else None
    if lab is None:
        return "slack", "#alerts-default"
    pager = then.pager(w, lab)
    team = lab.get("team")
    if pager and pager != "sre" and pager == team:
        return "team", team
    if pager:
        return "sre", pager
    rcv = [r for r, _ in then.receivers(w, lab) if r.startswith("slack-") and r != "slack-sre-fyi"]
    _, am = then.state(w)
    conf = next((x for x in am.get("receivers", []) if rcv and x.get("name") == rcv[0]), {})
    chan = ((conf.get("slack_configs") or [{}])[0]).get("channel") or "#alerts-default"
    return "slack", chan


def r06(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    burn_new = (s.name, "fast") in w.notes.get("burn_new", set())
    who, where = _r06_audience(w, s.name, item.p["source"])
    team = where if who == "team" else s.team
    # the fast-burn page for this service is on this week's queue: the story can't have it paging yet
    extra = " and the fast-burn page going in this week would make it three" if burn_new else ""
    pages = "its Down page and then its error-ratio page on top" if burn_new else \
        "its Down page and then its burn and error-ratio pages on top"
    count = "twice" if burn_new else "three times"
    if who == "sre":
        opts = [(f"quieter {s.name} outages",
                 f"When {s.name} is down the SRE pager gets {pages}{extra}. While the Down alert is firing, hold the "
                 f"burn and error-ratio pages back for {s.name} only."),
                (f"{s.name}: one page when it's down",
                 f"SRE, from the last {s.name} outage: paged {count} for the same thing{extra}. While {poss(s.name)} "
                 f"Down alert fires, its burn alerts and its error-ratio alert shouldn't page. Nothing else should be "
                 f"affected."),
                (f"{s.name} down = 3 pages",
                 f"SRE on-call, from the retro: \"When {s.name} dies the pager gives me Down, then the error ratio a "
                 f"minute later{', and soon the burn page too' if burn_new else ', then the burn page'}. I only "
                 f"needed the first one.\" Other services' pages shouldn't change.")]
    elif who == "slack":
        opts = [(f"quieter {s.name} outages",
                 f"When {s.name} is down, {where} gets {pages.replace('page', 'alert')}{extra}. While the Down alert is "
                 f"firing, hold the burn and error-ratio alerts back for {s.name} only."),
                (f"{s.name}: one alert when it's down",
                 f"Last {s.name} outage put the same thing into {where} {count}. While {poss(s.name)} Down alert fires, "
                 f"its burn alerts and its error-ratio alert should stay quiet. Nothing else should be affected."),
                (f"{s.name} down = a wall of alerts",
                 f"From the retro: \"When {s.name} dies {where} fills up with Down, then the error ratio"
                 f"{', and the new burn alert' if burn_new else ', then the burn alert'}. The first one was enough.\" "
                 f"Other services shouldn't change.")]
    else:
        opts = [(f"quieter {s.name} outages",
                 f"When {s.name} is down, {team} get {pages}{extra}. While the Down alert is firing, hold "
                 f"{'the burn and error-ratio pages' if burn_new else 'those'} back for {s.name} only."),
                (f"{s.name}: one page when it's down",
                 f"Last outage paged {team} {count} for the same thing{extra}. While {poss(s.name)} Down alert fires, "
                 f"its burn alerts and its error-ratio alert shouldn't page. Nothing else should be affected."),
                (f"{s.name} down = 3 pages",
                 f"{team} on-call, from the retro: \"When {s.name} dies I get Down, then "
                 f"{'the error ratio, a minute apart. I only needed the first one. And with the burn page you are adding it will be three.' if burn_new else 'the burn page, then the error ratio, a minute apart. I only needed the first one.'}\" "
                 f"Other services' pages shouldn't change.")]
    title, body = variety.card(w, "R06", opts)
    return Ticket(_key(w, rng), title, body, crew(w, rng, team if who == "team" else "sre", 1)[0])


def r03(item, w, rng) -> Ticket:
    new, old = item.p["new"], item.p["old"]
    moved = item.p["moved"]
    date = w.trigger.get("date", "the 14th")
    one = len(moved) == 1
    what = moved[0] if one else f"the {new} services"
    those = f"{moved[0]}" if one else "those services"
    title, body = variety.card(w, "R03", [
        (f"alerts still carry {old} for {what}",
         f"Since the split on {date}, `teams/ownership.yaml` has {', '.join(moved)} under {new}. Every alert "
         f"for {those} still says `team: {old}`. Please move them over; ownership.yaml is the source."),
        (f"{new}: team labels",
         f"Fallout from the {old} split: {poss(what) if one else what + chr(39)} alerts need `team: {new}` everywhere "
         f"they're defined. {old} keep whatever is still theirs in ownership.yaml."),
        (f"{new} tickets land in {poss(old)} Jira",
         f"The {' and '.join(moved)} alerts still say `team: {old}`, so {poss(new)} tickets end up in {poss(old)} "
         f"project. ownership.yaml was updated on {date}; the rules weren't.")])
    return Ticket(_key(w, rng), title, body, crew(w, rng, new, 1)[0])


def r04(item, w, rng) -> Ticket:
    new, old = item.p["new"], item.p["old"]
    when = w.cal.next_weekday("Monday")
    title, body = variety.card(w, "R04" if item.code == "R04" else "H17", [
        (f"route {new}",
         f"{new} have their PagerDuty service and Slack channel now (receivers `pagerduty-{new}`, `slack-{new}`, "
         f"`jira-{new}` are there already). Route their alerts per the routing policy in README."),
        (f"{new} pages go to {old}",
         (f"{item.p['moved'][0]} still pages the {old} rota." if len(item.p.get("moved", [])) == 1 else
          f"{poss(new)} services still page the {old} rota.")
         + f" Route {new} like every other team. {old} keep their remaining services as they are."),
        (f"{new} rota starts {when.replace('on ', '')}",
         f"{new} go live on their own rota {when}; receivers `pagerduty-{new}`, `slack-{new}` and `jira-{new}` "
         f"exist. Their alerts should reach them the way README says every team's do, and stop reaching {old}.")])
    return Ticket(_key(w, rng), title, body, crew(w, rng, new, 1)[0])


R05_TEXTS, R05_LITERAL = 3, 1   # the second text says "nothing may route to <old>": literal about the old receiver


def r05_literal(w) -> bool:
    """Whether this task's R05 ticket is the literal text (the grader then checks that no route names the old
    receiver; otherwise only that no alert can still end up there)."""
    return variety.index(w, "R05", R05_TEXTS) == R05_LITERAL


def r05(item, w, rng) -> Ticket:
    t, ch = item.p["team"], item.p["channel"]
    fri = w.cal.next_weekday("Friday")
    title, body = variety.card(w, "R05", [
        (f"#{t}-alerts is archived {fri.replace('on ', '')}",
         f"{t} moved to `{ch}`. `#{t}-alerts` gets archived {fri}; everything that goes to it today should go "
         f"to the new channel. Add a receiver `{item.p['new']}` for it."),
        (f"{t}: new alerts channel",
         f"{poss(t)} alerts channel is `{ch}` from now on (`{item.p['new']}`). The old one is being archived, so nothing "
         f"may route to `{item.p['old']}` after this."),
        (f"move {t} Slack alerts to {ch}",
         f"Workspace admins archive `#{t}-alerts` at the end of the week. {t} asked for everything that posts there "
         f"to go to `{ch}` instead; receiver name `{item.p['new']}`, please.")])
    return Ticket(_key(w, rng), title, body, crew(w, rng, t, 1)[0])
