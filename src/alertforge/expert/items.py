"""Queue items: one class per defect card (H..) or routine kind (R..).

An item turns into one hidden requirement. Its life cycle inside a world:
  setup(w, rng)          pick hosts and put the *fixed* state into the oracle repo (w.files / w.am); False if
                         the world cannot host it
  break_(files, am, w)   turn the fixed state into the state the queue starts from (the pristine repo)
  spec(w, rng)           (requirement entry, scenario groups) for the hidden checks
  variants(w)            [(name, fn(files, am), expect)] alternative fixes applied to the oracle state:
                         expect "accept" (q = 1) or a float bound (q must stay at or below it)
  exporters(w)           exporter series the item adds to the world (healthy values in every other scenario)
  ticket(w, rng)         the queue text and the incident packet files
Text never names the mechanism; every number the checks use is stated somewhere in the workspace.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .model import CATEGORY, WEIGHTS, World
from .repo import policy
from .vocab import Person


@dataclass
class Ticket:
    key: str
    title: str
    body: str
    reporter: Person | None = None
    files: dict = field(default_factory=dict)     # packet files, rel path -> text
    bundle: str | None = None                     # "while you're in there" pairing with another item id
    kind: str = ""                                # the item's requirement kind (component line in the queue)
    symptom: bool = False                         # a symptom ticket rather than a routine request
    code: str = ""
    pair_of: object = None                        # the ticket this one continues ("and the slow-burn ticket ...")


class Item:
    code = "X"
    kind = "outcome"
    ticket_like = False       # a symptom ticket (vs a routine request)

    def __init__(self, rid: str):
        self.rid = rid
        self.names: set[str] = set()      # alert names this item creates or names (sibling exclusion)
        self.touch: set[str] = set()      # rules a valid fix may change (kept out of preservation)
        self.route_touch: set[str] = set()   # teams whose routing this item may change
        self.route_sev_touch: set[str] = set()   # severities whose routing it may change for every team
        self.receivers_touch: set[str] = set()
        # alerts whose own service a fix may set as a static label (the label an inhibition's `equal:` reads):
        # {alert: service}; the rest of such an alert is still preserved
        self.label_touch: dict[str, str] = {}
        self.exclude: set[str] = set()    # filled in by the composer
        self.p: dict = {}                 # chosen hosts and parameters

    # --------------------------------------------------------------- life cycle
    def setup(self, w: World, rng: random.Random) -> bool:
        return True

    def break_(self, files: dict, am: dict, w: World) -> None:
        pass

    def spec(self, w: World, rng: random.Random) -> tuple[dict, list[dict]]:
        raise NotImplementedError

    def variants(self, w: World) -> list:
        return []

    def exporters(self, w: World) -> list[dict]:
        """Exporter series this item adds to the world, scraped in every scenario where its service runs (scraped.py
        puts them into the groups that don't draw their own): [{service, metric, labels, key, values}], `key` the
        labels that identify the series, `values` ("const", v) | ("ints", lo, hi) | ("counter", lo, hi) per second."""
        return []

    def ticket(self, w: World, rng: random.Random) -> Ticket:
        raise NotImplementedError

    # --------------------------------------------------------------- helpers
    def entry(self, **kw) -> dict:
        d = {"id": self.rid, "kind": self.kind, "category": CATEGORY[self.kind], "weight": WEIGHTS[self.kind],
             "q_pristine": 0.0, "defect": self.code, "ticket": self.ticket_like}
        d.update(kw)
        return d

    def alt(self, files: dict, am: dict, w: World) -> None:
        """The alternative solver's fix: the second accepted variant (or the oracle when there is none)."""
        acc = [fn for _, fn, exp in self.variants(w) if exp == "accept"]
        if len(acc) >= 2:
            acc[1](files, am)
        elif acc:
            acc[0](files, am)


def wpolicy(w: World, team: str, sev: str) -> list[str]:
    """README routing policy, with a team's Slack receiver replaced when the queue retires it."""
    new = w.notes.get("slack_of", {}).get(team)
    out = policy(team, sev)
    return sorted(new if (new and x == f"slack-{team}") else x for x in out)


def svc_class(w: World, svc: str, cls: str = "page", team: str | None = None, sev: str | None = None) -> dict:
    team = team or w.svc(svc).team
    sev = sev or ("page" if cls == "page" else "ticket")
    return {"team": team, "class": cls, "expect": wpolicy(w, team, sev), "labels": {"severity": sev, "team": team}}


def outcome_entry(item: Item, w: World, services: dict, named: str | None = None, expected_count: int = 1) -> dict:
    return item.entry(outcome={"services": services, "alertname": named, "exclude": sorted(item.exclude - {named}),
                               "expected_count": expected_count, "touch": sorted(item.touch),
                               "runbook_prefix": f"https://runbooks.{w.domain}/alerts/"})


def fmt_dur(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"
