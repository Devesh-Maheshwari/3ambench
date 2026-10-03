"""Expert-tier world model: services, rule files (oracle state), Alertmanager config, queue items."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .vocab import Company, Person, camel

WEIGHTS = {"outcome": 4, "storm": 3, "new_alert": 3, "repair": 3, "recording": 2, "route": 2, "inhibit": 2,
           "migration": 2, "absence": 2, "relabel": 2, "annotation": 2}
CATEGORY = {"outcome": "req_outcome", "storm": "req_storm", "new_alert": "req_alerts", "repair": "req_repairs",
            "recording": "req_recording", "route": "req_routing", "inhibit": "req_inhibit",
            "migration": "req_migration", "absence": "req_absence", "relabel": "req_triage",
            "annotation": "req_triage"}


@dataclass
class Svc:
    name: str
    team: str
    kind: str                 # api | worker | grpc | consumer | indexer
    family: str               # http | grpc | latency
    target: str = "0.999"
    window_d: int = 30
    rps: int = 40
    floor: float | None = None
    pods: int = 3
    err_thr: str = "0.05"
    home: str = ""            # rule file stem that holds the service's own rules
    cluster: bool = False     # Down alerts carry `cluster`

    @property
    def camel(self) -> str:
        return camel(self.name)


@dataclass
class RuleFileSpec:
    stem: str
    gen: int                  # 1 legacy, 2 conventions, 3 PrometheusRule
    groups: list
    header: list = field(default_factory=list)
    ext: str = "yml"


@dataclass
class World:
    task_id: str
    family: str
    seed: int
    tseed: int
    company: Company
    people: list[Person]
    services: list[Svc]
    teams: list[str]                      # teams with rules in this repo
    shared_teams: list[str]               # teams only in the shared Alertmanager
    files: dict[str, RuleFileSpec] = field(default_factory=dict)   # oracle state
    am: dict = field(default_factory=dict)                         # oracle state
    items: list = field(default_factory=list)                      # queue items (see items.py)
    docs: dict = field(default_factory=dict)                       # extra workspace files (rel path -> text)
    catalog: dict = field(default_factory=dict)                    # slo/services.yaml content
    ownership: dict = field(default_factory=dict)                  # teams/ownership.yaml content
    runbooks: dict = field(default_factory=dict)                   # alert name -> runbook page stem
    trigger: dict = field(default_factory=dict)                    # the week's trigger event
    notes: dict = field(default_factory=dict)                      # free-form facts for texts (provenance)
    cluster_level: list[str] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    namespace: str = "prod"
    cal: object = None                                             # cal.Cal: today, incident dates and numbers
    am_extra_receivers: list = field(default_factory=list)          # receivers nothing routes to any more

    @property
    def domain(self) -> str:
        return self.company.domain

    def svc(self, name: str) -> Svc:
        return next(s for s in self.services if s.name == name)

    def runbook(self, alert: str) -> str:
        return f"https://runbooks.{self.domain}/alerts/{alert}"

    def state(self) -> tuple[dict, dict]:
        """A deep copy of the oracle state: ({stem: RuleFileSpec}, am)."""
        return copy.deepcopy(self.files), copy.deepcopy(self.am)


# ---------------------------------------------------------------------------- state helpers

def rules_named(files: dict, name: str, kind: str = "alert"):
    """Yield (file spec, group, rule) for every rule with that alert/record name."""
    for f in files.values():
        for g in f.groups:
            for r in g["rules"]:
                if r.get(kind) == name:
                    yield f, g, r


def first_rule(files: dict, name: str, kind: str = "alert") -> dict:
    for _, _, r in rules_named(files, name, kind):
        return r
    raise KeyError(name)


def drop_rules(files: dict, names: set[str]) -> None:
    for f in files.values():
        for g in f.groups:
            g["rules"] = [r for r in g["rules"] if r.get("alert", r.get("record")) not in names]


def replace_rule(files: dict, name: str, new: list[dict]) -> None:
    """Replace every rule with that alert name by the rules in `new` (at the first one's position)."""
    for f in files.values():
        for g in f.groups:
            out, done = [], False
            for r in g["rules"]:
                if r.get("alert") == name:
                    if not done:
                        out += copy.deepcopy(new)
                        done = True
                    continue
                out.append(r)
            g["rules"] = out


def add_rules(files: dict, stem: str, group: str, rules: list[dict], after: str | None = None) -> None:
    f = files[stem]
    g = next((x for x in f.groups if x["name"] == group), None)
    if g is None:
        g = {"name": group, "rules": []}
        f.groups.append(g)
    if after is None:
        g["rules"] += copy.deepcopy(rules)
        return
    idx = next((i for i, r in enumerate(g["rules"]) if r.get("alert", r.get("record")) == after), len(g["rules"]) - 1)
    g["rules"][idx + 1:idx + 1] = copy.deepcopy(rules)


def team_routes(am: dict) -> list[dict]:
    return am["route"]["routes"]


def find_route(am: dict, pred) -> dict | None:
    def walk(n):
        if pred(n):
            return n
        for c in n.get("routes") or []:
            hit = walk(c)
            if hit is not None:
                return hit
        return None
    for r in am["route"]["routes"]:
        hit = walk(r)
        if hit is not None:
            return hit
    return None


def route_for_team(am: dict, team: str) -> dict | None:
    def is_team(n):
        ms = n.get("matchers") or []
        return f'team="{team}"' in ms or (n.get("match") or {}).get("team") == team
    return find_route(am, is_team)


def home_group(w: World, s) -> dict:
    """The group named after the service in its home file, created when the service had no rules there yet."""
    f = w.files.get(s.home)
    if f is None:
        f = RuleFileSpec(s.home, 1 if s.home.startswith("legacy-") else 3, [], [], "yml" if s.home.startswith("legacy-") else "yaml")
        w.files[s.home] = f
    g = next((g for g in f.groups if g["name"] == s.name), None)
    if g is None:
        g = {"name": s.name, "rules": []}
        f.groups.append(g)
    return g
