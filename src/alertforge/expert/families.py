"""Family composition: which tickets and routine items a seed draws, the week's trigger event, and the world
they live in. E1 quiet pager, E2 storm after a deploy, E3 reorg and routing, E4 latency SLO, E5 legacy cleanup
after an on-call survey."""

from __future__ import annotations

import hashlib
import random
from collections import Counter

from . import items_am as am
from . import items_cap as cap
from . import items_deploy as dep
from . import items_latency as lat
from . import items_routine as rt
from . import items_svc as svc
from . import items_triage as tri
from .model import World
from .repo import build_am, build_rules, pick_services
from .adr import numbers as adr_numbers
from .cal import Cal
from .people import people
from .vocab import AFFINITY, ODDITY_NOTES, SHARED_TEAMS, SPLITS, company_for

FAMILIES = ["E1", "E2", "E3", "E4", "E5"]


def task_seed(master: int, family: str, seed: int) -> int:
    return int(hashlib.sha256(f"{master}:expert:{family}:{seed}".encode()).hexdigest()[:16], 16)


# SPEC 3.0: one routine bank shared by every family, drawn per seed with the family's weights (a favoured kind is
# three times as likely), so no kind is guaranteed in every task of a family. At most two items of a kind (R01's
# fast and slow burn). E5's survey answers (TRIAGE) are drawn on their own, then 1-2 routine requests.
ROUTINE = {"R01f": lambda r: rt.BurnNew(r, "fast"), "R01s": lambda r: rt.BurnNew(r, "slow"), "R05": rt.ReceiverMove,
           "R06": rt.InhibitDown, "R08": tri.Decommission, "R10": lambda r: tri.SeverityChange(r, "up"),
           "R11": lambda r: tri.SeverityChange(r, "down"), "R12": tri.BlankAnnotation, "R13": tri.RunbookLink,
           "R18": tri.Duplicate}
TRIAGE = ("R10", "R11", "R12", "R13", "R18")
FAVOURED = {"E1": ("R01f", "R01s", "R05", "R06", "R08", "R12"), "E2": ("R06", "R10", "R12", "R01f"),
            "E3": ("R05", "R08", "R13", "R06"), "E4": ("R01f", "R01s", "R06", "R05"), "E5": ("R08", "R05", "R06")}
DRAWS = {"E1": (4, 6), "E2": (3, 5), "E3": (3, 5), "E4": (4, 5)}
TRIGGER = {"R03", "R04", "H17"}   # the split's label move and subtree: what an E3 week is about, never drawn
MAX_OVERLAP = 0.6                 # SPEC 3.0: two tasks of a family share at most this much of their routine kinds
ATTEMPTS = 200


def _draw(rng: random.Random, keys, k: int, favoured=()) -> list[str]:
    keys, out = list(keys), []
    for _ in range(min(k, len(keys))):
        x = rng.choices(keys, weights=[3 if y in favoured else 1 for y in keys])[0]
        keys.remove(x)
        out.append(x)
    return out


def _plan(family: str, rng: random.Random, seed: int = 0) -> tuple[list, list, dict]:
    """(ticket classes, routine factories, world needs)."""
    if family == "E1":
        tickets = rng.sample([svc.RegexAbsent, svc.ManyToOne, svc.TrafficVoid, svc.ValueInLabel], rng.choice([2, 3]))
        split = rng.random() < 0.75
    elif family == "E2":
        pool = [dep.StormGroupBy, dep.AvgOfRatios, dep.SumThenRate, dep.PerPodFor, dep.ClusterInhibit]
        tickets = [dep.StormGroupBy] + rng.sample(pool[1:], rng.choice([1, 2]))
        split = rng.random() < 0.4
    elif family == "E3":
        tickets = rng.sample([am.CatchAllFirst, am.MatchLiteral, am.SeverityInhibit, am.MissingTeam],
                             rng.choice([2, 3]))
        # the catch-all sits above every subtree, so it is the first ticket: the others' routes only work under it
        tickets.sort(key=lambda c: c is not am.CatchAllFirst)
        split = True
    elif family == "E4":
        # two of the three, each pair on a third of the seeds: the SLI on a changing bucket layout (H11), the
        # quantile capped at the top bucket (H12), the traffic floor (H04)
        pairs = [(lat.LatencyBuckets, svc.TrafficVoid), (lat.LatencyBuckets, lat.QuantileCap),
                 (lat.QuantileCap, svc.TrafficVoid)]
        tickets = rng.sample(pairs[seed % 3], 2)
        split = False
    else:
        pool = [svc.ValueInLabel, cap.MemorySawtooth, cap.KafkaStall, cap.OrVectorZero, cap.DiskPredict]
        sem = rng.sample(pool, 3)
        if cap.MemorySawtooth in sem and cap.DiskPredict in sem:
            # SPEC E5: about three semantics complaints, at most one of H08/H23
            sem.remove(cap.DiskPredict)
            sem.append(rng.choice([c for c in pool if c not in sem and c is not cap.DiskPredict]))
        tickets = sem
        split = False
    if family == "E5":
        keys = _draw(rng, TRIAGE, rng.randint(3, 5))
        keys += _draw(rng, [k for k in ROUTINE if k not in TRIAGE], rng.randint(1, 2), FAVOURED["E5"])
    else:
        keys = _draw(rng, ROUTINE, rng.randint(*DRAWS[family]), FAVOURED[family])
    routine = [ROUTINE[k] for k in keys]
    needs = {"split": split,
             "pair": svc.RegexAbsent in tickets, "floor": svc.TrafficVoid in tickets,
             "clusters": dep.ClusterInhibit in tickets,
             "indexer": cap.MemorySawtooth in tickets, "consumer": any(t in tickets for t in (cap.OrVectorZero,)),
             "latency": lat.LatencyBuckets in tickets}
    return tickets, routine, needs


def routine_kinds(items: list) -> list[str]:
    """The drawn routine kinds of a task, with multiplicity (the split's trigger items left out)."""
    return sorted(it.code for it in items if not it.ticket_like and it.code not in TRIGGER)


def overlap(a: list[str], b: list[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    m = max(sum(ca.values()), sum(cb.values()))
    return sum((ca & cb).values()) / m if m else 0.0


_KINDS: dict[tuple, list[str]] = {}


def kinds_of(family: str, seed: int, master: int) -> list[str]:
    key = (family, seed, master)
    if key not in _KINDS:
        _KINDS[key] = routine_kinds(build(family, seed, master)[1])
    return _KINDS[key]


def max_overlap(family: str, seed: int, master: int, items: list) -> tuple[float, int | None]:
    """The largest routine overlap with an earlier seed of the same family, and that seed."""
    mine = routine_kinds(items)
    best = (0.0, None)
    for s in range(1, seed):
        ov = overlap(mine, kinds_of(family, s, master))
        if ov > best[0]:
            best = (ov, s)
    return best


def exclude_siblings(items):
    """Exclude alerts another requirement creates, not ones it only references."""
    names = {n: it.rid for it in items if it.kind in ("outcome", "storm", "new_alert", "repair") for n in it.names}
    for it in items:
        it.exclude = {n for n, rid in names.items() if rid != it.rid}


def build(family: str, seed: int, master: int = 20260930, task_id: str = "") -> tuple[World, list]:
    """Compose a seed. A draw whose routine kinds overlap an earlier seed of the family by more than MAX_OVERLAP is
    redrawn (deterministic: the earlier seeds are composed the same way); after ATTEMPTS the least overlapping
    draw is kept and the gate reports it."""
    tseed = task_seed(master, family, seed)
    rng = random.Random(tseed)
    best = None
    for attempt in range(ATTEMPTS):
        w, its = _try(family, seed, tseed, random.Random(rng.random()), task_id, master)
        if w is None:
            continue
        ov, _ = max_overlap(family, seed, master, its)
        if ov <= MAX_OVERLAP + 1e-9:
            w.notes["master"] = master
            w.items = its
            return w, its
        if best is None or ov < best[0]:
            best = (ov, w, its)
    if best is None:
        raise RuntimeError(f"could not compose {family} seed {seed}")
    best[1].notes["master"] = master
    best[1].items = best[2]
    return best[1], best[2]


def _try(family, seed, tseed, rng, task_id, master=20260930):
    company = company_for(master, FAMILIES.index(family), seed)
    tickets, routine, needs = _plan(family, rng, seed)
    ind = company.industry
    aff = AFFINITY[ind]
    shared = rng.sample(SHARED_TEAMS, rng.randint(7, 10))
    if needs["clusters"] and "platform" not in shared:
        shared[0] = "platform"
    need_kinds = {"api": 3, "worker": 1}
    if needs["indexer"]:
        need_kinds["indexer"] = 1
    if needs["consumer"] or family == "E5":
        need_kinds["consumer"] = 1
    if needs["latency"]:
        need_kinds["latency"] = 1
    picked = pick_services(rng, ind, rng.randint(8, 10), need_kinds)
    split = None
    if needs["split"]:
        have = {aff[n] for n, _, _ in picked}
        opts = [sp for sp in SPLITS[ind] if sp[0] in have and sp[1] in have]
        if not opts:
            return None, None
        split = rng.choice(opts)
    w = World(task_id, family, seed, tseed, company, [], [], [], shared)
    w.notes["master"] = master
    w.notes["adr"] = adr_numbers(company)
    w.cal = Cal(rng, company.utc_offset)
    w.clusters = ["eu-1", "eu-2"] if needs["clusters"] else []
    w.namespace = rng.choice(["prod", "production", "apps", f"{ind}-prod"])
    from .model import Svc
    for name, kind, fam in picked:
        kind = "latency" if fam == "latency" else kind
        # R3: a service belongs to the team its work belongs to; before a split the parent owned the child's
        team = split[0] if split and aff[name] == split[1] else aff[name]
        s = Svc(name, team, kind, "http" if fam == "latency" else fam, cluster=bool(w.clusters))
        s.target = rng.choice(["0.999", "0.995", "0.9995", "0.99"]) if kind in ("api", "grpc") else "0.99"
        s.rps = rng.choice([30, 45, 60, 80, 120]) if kind in ("api", "grpc", "latency") else rng.choice([8, 12, 20])
        s.pods = rng.choice([3, 4, 6])
        s.err_thr = rng.choice(["0.02", "0.05", "0.1"])
        w.services.append(s)
    if needs["latency"]:
        w.notes["latency_services"] = [s.name for s in w.services if s.kind == "latency"]
    if needs["pair"]:
        base = next((s for s in w.services if s.kind == "api" and s.family == "http"
                     and sum(1 for x in w.services if x.name.split("-")[0] == s.name.split("-")[0]) == 1
                     and not (split and aff[s.name] in split)), None)
        if base is None:
            return None, None
        pre = base.name.split("-")[0]
        sib = f"{pre}-worker" if not base.name.endswith("-worker") else f"{pre}-api"
        if any(x.name == sib for x in w.services):
            return None, None
        w.services.append(Svc(sib, base.team, "worker", "http", target="0.99", rps=12, pods=3,
                              err_thr=base.err_thr, cluster=bool(w.clusters)))
        aff = {**aff, sib: base.team}
        w.notes["pair_base"] = base.name
    lowvol = [s for s in w.services if s.kind == "api" and s.family == "http"]
    if lowvol and (family in ("E1", "E4") or rng.random() < 0.3):
        s = rng.choice(lowvol)
        s.window_d, s.rps = 7, rng.choice([4, 5, 6])
        w.notes.setdefault("busy", set()).add(s.name)
    if needs["floor"]:
        if svc.TrafficVoid.prepare(w, rng) is None:
            return None, None
    _oddity(w, rng, aff, split)
    w.teams = list(dict.fromkeys(s.team for s in w.services))
    rng.shuffle(w.teams)
    if len(w.teams) < 3:
        return None, None
    if split:
        tr = rt.LabelMove.split(w, split[0], split[1], [s.name for s in w.services if aff.get(s.name) == split[1]])
        if tr is None:
            return None, None
        tr["day"] = w.cal.past(rng, 5, 12, weekday=True)
        tr["date"] = w.cal.ordinal(tr["day"])
        w.trigger = tr
    w.people = people(rng, company.region, w.teams + [t for t in ("platform", "database-reliability") if t in shared])
    build_rules(w, rng)
    build_am(w, rng)
    items = []
    k = {"T": 0, "Q": 0}

    def rid(prefix):
        k[prefix] += 1
        return f"{prefix}{k[prefix]}"

    if needs["split"]:
        routine = [rt.LabelMove, rt.TeamSubtree] + routine
    for cls in tickets:
        it = cls(rid("T"))
        if it.setup(w, rng):
            items.append(it)
    for fac in routine:
        it = fac(rid("Q"))
        if it.setup(w, rng):
            items.append(it)
    tick = [i for i in items if i.ticket_like]
    if len(tick) < (1 if family in ("E4",) else 2) or len(items) < 5:
        return None, None
    # sibling exclusion: alerts that another requirement creates or is about (not ones it only mentions, like
    # the targets of an inhibition or the alert whose Slack text is being fixed)
    exclude_siblings(items)
    w.cluster_level = ["ClusterUnreachable"] if w.clusters else []
    from . import decoys
    from .repo import fill_to_floor
    decoys.live(w, items)
    fill_to_floor(w, items)
    return w, items


def _oddity(w: World, rng: random.Random, aff: dict, split) -> None:
    """At most one historical oddity per world (R3): a service another team kept, with the reason written next to
    it in ownership.yaml. Never a service the split, the H01 pair or a latency SLO is about."""
    if rng.random() >= 0.35:
        return
    skip = set(split or ()) | {w.notes.get("pair_base")}
    cands = [s for s in w.services if s.kind != "latency" and aff.get(s.name) not in skip
             and s.name != w.notes.get("pair_base") and not s.name.startswith(f"{(w.notes.get('pair_base') or '#').split('-')[0]}-")]
    teams = sorted({s.team for s in w.services} - skip)
    if not cands or len(teams) < 3:
        return
    s = rng.choice(cands)
    other = rng.choice([t for t in teams if t != s.team])
    note = rng.choice(ODDITY_NOTES).format(svc=s.name, team=other, home=s.team, year=rng.choice([2023, 2024, 2025]))
    w.notes["oddity"] = {"svc": s.name, "team": other, "home": s.team, "note": note}
    s.team = other
