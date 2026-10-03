"""Workspace text for expert tasks: ownership, catalog, runbooks, the handover note and queue (instruction.md), the
on-call survey (E5). README lives in `readme.py`, the ADRs in `adr.py`, service runbooks in `runbooks.py`.

README states general rules only; every number a check needs sits in one sentence there, in an ADR, a runbook or a
postmortem action item. Text every task carries comes in several wordings spread evenly over a build (R2).
"""

from __future__ import annotations

import random

import yaml

from . import variety
from .model import World
from .people import rota
from .vocab import poss, verb


def readme(w: World) -> str:
    from . import adr, readme as rd
    return rd.readme(w, w.notes.get("adr") or adr.numbers(w.company))


def ownership(w: World) -> str:
    """teams/ownership.yaml, written by hand: the one historical oddity (if any) carries its reason (R3)."""
    teams: dict[str, dict] = {}
    for s in w.services:
        teams.setdefault(s.team, {"services": [], "slack": f"#{s.team}-alerts", "pagerduty": f"pagerduty-{s.team}"})
        teams[s.team]["services"].append(s.name)
    for t, new in w.notes.get("slack_of", {}).items():
        if t in teams:
            teams[t]["slack"] = f"#{new[len('slack-'):]}"
    odd = w.notes.get("oddity") or {}
    head = variety.house(w, "ownership-head", [
        "# Source of truth for service ownership. Changes go through the eng-managers review.",
        "# Who owns what. Edited by eng managers only; everything else (routing, labels) follows this file.",
        "# Service ownership. Reviewed by the eng-managers group; ask in #eng-managers before moving a service."])
    out = [head, "teams:"]
    for t in sorted(teams):
        out += [f"  {t}:", "    services:"]
        for svc in teams[t]["services"]:
            out.append(f"    - {svc}" + (f"   # {odd['note']}" if odd.get("svc") == svc else ""))
        out += [f"    slack: '{teams[t]['slack']}'", f"    pagerduty: {teams[t]['pagerduty']}"]
    text = "\n".join(out) + "\n"
    assert yaml.safe_load(text) == {"teams": {t: teams[t] for t in sorted(teams)}}, "ownership.yaml round trip"
    return text


def catalog_yaml(w: World) -> str:
    from .repo import catalog
    doc = catalog(w)
    for s in w.services:
        if s.name in w.notes.get("latency_services", []):
            doc["services"].append({"name": s.name, "team": s.team, "slo_target": 0.99, "window": "7d",
                                    "sli_family": "latency", "latency_objective_seconds": 0.3,
                                    "metrics": ["http_request_duration_seconds_bucket", "http_request_duration_seconds_count"],
                                    "note": f"docs/slo/{s.name}.md"})
    head = variety.house(w, "catalog-head", [
        "# SLO catalog. Targets and windows are agreed with the owning team; floors come from PM action items.\n",
        "# SLO catalog: one entry per service with an SLO. Targets and windows are the owning team's call, agreed in\n"
        "# the SLO review; traffic floors were added after postmortems.\n",
        "# The catalog. Owning teams agree targets and windows with observability; floors come out of postmortem\n"
        "# action items.\n"])
    return head + yaml.safe_dump(doc, sort_keys=False)


def runbook_files(w: World, rng: random.Random | None = None) -> dict[str, str]:
    from .runbooks import service_runbooks
    out = {}
    for name, rb in sorted(w.runbooks.items()):
        body = rb["body"]
        out[f"runbooks/{rb['file']}"] = body() if callable(body) else body
    for k, v in service_runbooks(w, rng or random.Random(w.tseed ^ 0x2B00C)).items():
        out.setdefault(k, v)
    return out


HANDOVERS = [
    "Picking up the monitoring queue from {prev}, who's out this week. Repo is `/workspace/monitoring`. README and "
    "`docs/adr/` are the house rules, and `slo/` and `teams/ownership.yaml` win over anything a runbook says. We "
    "replay incident data through rule changes with `bin/range2test` before merge; `tests/` is just the smoke "
    "suite. CI is `bin/af-check`.",
    "You have the observability queue this week ({prev} is on leave). Everything is in `/workspace/monitoring`; "
    "house rules are README plus the ADRs. Packets for the incidents are under `incidents/`, and `bin/range2test` "
    "will turn the query_range pulls into promtool tests. `bin/af-check` is what CI runs.",
    "Queue for the week below, handed over from {prev}. Repo: `/workspace/monitoring`. Read README first, the "
    "catalog and ownership file are authoritative. Incident data is in `incidents/`; replay it with "
    "`bin/range2test` if you want to be sure before merging. CI is `bin/af-check`.",
    "{prev} is on the migration this week, so their queue is yours. Repo: `/workspace/monitoring`. House rules are "
    "README and `docs/adr/`; the SLO catalog and `teams/ownership.yaml` are the sources of truth. Incident packets "
    "live in `incidents/`, and `bin/range2test` turns their query_range pulls into promtool tests. CI runs "
    "`bin/af-check`.",
    "Handover from {prev}. The monitoring repo is at `/workspace/monitoring`; read README before touching anything, "
    "it has the conventions reviewers will hold you to. The catalog (`slo/services.yaml`) and `teams/ownership.yaml` "
    "beat anything older. `bin/range2test` replays the pulls under `incidents/`, `bin/af-check` is CI.",
    "Your turn on the obs queue; {prev} has it again next week. Everything lives in `/workspace/monitoring`. README "
    "and the ADRs under `docs/adr/` are how we do things here, `slo/services.yaml` and `teams/ownership.yaml` are "
    "the truth when something older disagrees. The tickets point at packets in `incidents/`; `bin/range2test` makes "
    "promtool tests out of their pulls. `bin/af-check` is what CI runs.",
    "{prev} is off until Monday, so this week's monitoring work is with you. The repo is `/workspace/monitoring`. "
    "Conventions: README, then `docs/adr/`. Ownership and SLO targets come from `teams/ownership.yaml` and "
    "`slo/services.yaml`, nowhere else. `bin/range2test` can replay any pull in `incidents/` against your branch; "
    "CI is `bin/af-check`.",
    "Here's the queue {prev} left. Working copy: `/workspace/monitoring`. Please skim README and the ADRs first; "
    "reviewers go by them. When a runbook and the catalog or ownership file disagree, the catalog and ownership "
    "file win. Packets are in `incidents/` (`bin/range2test` turns a query_range pull into a promtool test), and "
    "`bin/af-check` is the CI check.",
]

FOOTERS = [
    ["Usual rules: everything lands in the repo, don't tidy what nobody asked about, and it's done when "
     "`bin/af-check` is green. `promtool`, `amtool` and `bin/range2test` are on the path."],
    ["CI (`bin/af-check`) has to pass before anything merges, and done means every ticket above is handled. Please "
     "don't reformat or \"fix\" things outside the tickets, reviewers will bounce it. `promtool`, `amtool` and "
     "`bin/range2test` are installed."],
    ["Rules go under `rules/`, routing in `alertmanager/alertmanager.yml`. Leave the rest alone unless a ticket says "
     "so. The queue handled and a green `bin/af-check` means done; `promtool`, `amtool` and `bin/range2test` are on "
     "the path."],
    ["## Notes", "",
     "- Land everything in the repo; rules under `rules/`, routing in `alertmanager/alertmanager.yml`.",
     "- Leave alone what the queue doesn't ask for.",
     "- `promtool`, `amtool`, `bin/af-check` and `bin/range2test` are on the path. Done means the queue is "
     "handled and `bin/af-check` passes."],
    ["When you're through the list and `bin/af-check` is happy, that's the week. Keep the diff to what the tickets "
     "ask for; everything goes in the repo. The usual tools (`promtool`, `amtool`, `bin/range2test`) are there."],
    ["Same as always: changes live in the repo, nothing outside the tickets gets touched, and it isn't finished "
     "until every ticket is dealt with and `bin/af-check` passes. You'll find `promtool`, `amtool` and "
     "`bin/range2test` on the PATH."],
]

HEADER_FORMS = [
    lambda p, age, comp: f"_Reported by {p.full} · {age} · {comp}_",
    lambda p, age, comp: f"Reporter: {p.first} | opened {age} | component: {comp}",
    lambda p, age, comp: f"`{comp}` · from @{p.handle} · {age}",
]
COMPONENT = {"outcome": "alerting", "storm": "alerting", "new_alert": "slo", "repair": "alerting",
             "recording": "slo", "route": "routing", "inhibit": "routing", "migration": "ownership",
             "absence": "cleanup", "relabel": "alerting", "annotation": "alerting"}
SYMPTOM_COMMENTS = ["+1, this one woke me twice last week", "same here, we saw it on our side too",
                    "bumping this, it bit us again", "I can pair on it if that helps",
                    "linking the incident channel in case anyone wants the context"]
ROUTINE_COMMENTS = ["no rush from our side, but please don't drop it", "thanks for picking this up",
                    "we talked about this at the retro and nobody took it", "happy to review when it's up"]


def trigger_text(w: World, items) -> str:
    """The split paragraph, opened according to how much of the queue really comes from it (R11)."""
    tr = w.trigger
    moved = " and ".join(tr["moved"]) if len(tr["moved"]) <= 2 else ", ".join(tr["moved"][:-1]) + " and " + tr["moved"][-1]
    from_split = sum(1 for it in items if it.code in ("R03", "R04", "H17"))
    share = from_split / max(1, len(items))
    opener = ("Most of this week is fallout" if share >= 0.5 else
              "A couple of tickets this week are fallout" if from_split <= 2 else "Part of this week is fallout")
    return variety.card(w, "split", [
        f"{opener} from the {tr['old']} split on {tr['date']}: {moved} went to the new {tr['new']} team. The rest is "
        f"the usual.",
        f"Context: {tr['old']} split on {tr['date']}. {moved} now {verb(tr['moved'], 'belongs', 'belong')} to "
        f"{tr['new']} (see `teams/ownership.yaml`). "
        + ("A good part of the queue comes from that." if share >= 0.5 else "Two of the tickets below come from that."
           if from_split == 2 else "Some of the tickets below come from that."),
        f"Background: {tr['new']} was split off from {tr['old']} on {tr['date']}. {moved} {verb(tr['moved'], 'is', 'are')} "
        f"{poss(tr['new'])} now, per `teams/ownership.yaml`, and "
        + ("most tickets below come out of that." if share >= 0.5 else "a few tickets below come out of that.")])


def order_queue(queue: list) -> list:
    """The slow-burn ticket that says "and the slow-burn ticket for X" comes right after the fast-burn one (R6)."""
    q = list(queue)

    def at(x):
        return next((i for i, y in enumerate(q) if y is x), None)

    for t in list(q):
        fast = getattr(t, "pair_of", None)
        if fast is not None and at(fast) is not None and at(fast) > at(t):
            q.pop(at(t))
            q.insert(at(fast) + 1, t)
    return q


def instruction(w: World, tickets: list, rng: random.Random, canary: str, survey: bool, items=()) -> str:
    obs = rota(w, "observability")
    prev = rng.choice(obs).first
    lines = [f"<!-- harbor-canary GUID {canary} -->", variety.house(w, "handover", HANDOVERS).format(prev=prev)]
    if (w.trigger or {}).get("moved"):
        lines += ["", trigger_text(w, items)]
    lines += ["", "## Queue", ""]
    if survey:
        lines += [f"- **Q3 on-call survey.** `docs/oncall-survey-2026-q3.md`. Everything in it that needs a change in "
                  f"this repo is yours this week. Not all of it will.", ""]
    form = variety.house(w, "ticket-header", list(range(len(HEADER_FORMS))))
    commented = set(rng.sample(range(len(tickets)), min(len(tickets), rng.choice([1, 1, 2])))) if tickets else set()
    for i, t in enumerate(tickets):
        lines.append(f"### {t.key}: {t.title}")
        lines.append("")
        if t.reporter is not None and rng.random() < 0.5:
            age = rng.choice(["today", "yesterday", f"{rng.randint(2, 6)} days ago", "last week"])
            lines += [HEADER_FORMS[form](t.reporter, age, COMPONENT.get(getattr(t, "kind", ""), "alerting")), ""]
        lines.append(t.body)
        lines.append("")
        if i in commented:
            who = rng.choice([p for p in w.people if p is not t.reporter] or w.people)
            bank = SYMPTOM_COMMENTS if getattr(t, "symptom", False) else ROUTINE_COMMENTS
            lines += [f"> {who.handle}: {rng.choice(bank)}", ""]
    lines += variety.house(w, "footer", FOOTERS) + [""]
    return "\n".join(lines)


def _subject_team(w: World, text: str) -> str | None:
    """The team that owns the first service an answer talks about (by name or by alert-name prefix)."""
    hits = []
    for s in w.services:
        for pat in (s.name, s.camel):
            i = text.find(pat)
            if i >= 0:
                hits.append((i, -len(pat), s.team))
    return min(hits)[2] if hits else None


ASIDES = ["Handover notes are a mess. Half the time I find out what happened during the week from the PagerDuty "
          "history.",
          "The rota is too thin over the holidays. Two people covering three weeks is not a rota.",
          "My VPN drops every time I move from the phone hotspot to wifi, which is exactly when I'm paged.",
          "Please stop scheduling database maintenance at 2am without telling the rota.",
          "I'd like a proper shadow week before a first shift. Reading runbooks at 3am is not onboarding.",
          "The escalation policy still lists two people who left in the spring.",
          "Can the on-call allowance be paid monthly instead of quarterly?",
          "Most of my pages this quarter were real. Thanks to whoever cleaned up the flappy ones in July.",
          "Nothing to add. It was a quiet quarter for me."]


def survey_doc(w: World, answers: list, rng: random.Random) -> str:
    head = variety.card(w, "survey", [
        ["# On-call survey, Q3 2026", "", "Collected from the Q3 on-call retro. Unedited.", ""],
        ["# Q3 on-call survey (raw answers)", "", "From the form that went round after the retro. Team in brackets "
                                                  "where people filled it in.", ""],
        ["# On-call feedback, July to September", "", "Copied out of the survey form as it came in. Some answers are "
                                                      "about things outside this repo.", ""]])
    # two answers that need nothing from this repo, different ones in every survey of a build (R2)
    r, _ = variety.rank(w, "survey")
    asides = [ASIDES[r % len(ASIDES)], ASIDES[(r + 4) % len(ASIDES)]]
    pool = list(answers) + [None] * len(asides)
    rng.shuffle(pool)
    out = list(head)
    for i, t in enumerate(pool, 1):
        if t is None:
            out += [f"**{i}.** {asides.pop()}", ""]
            continue
        who = rng.choice(["", "", "x"])
        # A severity change is an owner's request, not an unattributed suggestion.
        # Keep the random draw so unrelated answers and evidence stay identical.
        if getattr(t, "code", "") == "R10":
            who = "x"
        if who:
            # whoever answered is on the rota of the service they complain about; fleet answers stay unsigned
            team = _subject_team(w, t.body)
            who = f" ({team})" if team else ""
        out += [f"**{i}.**{who} {t.body}", ""]
    return "\n".join(out)
