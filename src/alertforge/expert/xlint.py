"""Cross-task sameness lint (R2, SPEC 7.6 item 3), run over a build after its tasks are built.

Every piece of prose a task carries is filed under the text it came from: a card's ticket, thread, postmortem or
runbook under the card (`H04`, `R06`, `survey:H08`, ...), README, ADRs, service runbooks and the handover under
`house`, the split paragraph under `split`. Names, alerts, keys, numbers and times are masked (the way
`review-v02/scratch-realism/rep.py` does it). Two checks:

- no masked sentence appears in more than a third (rounded up) of the tasks that carry its text;
- word 6-grams of instruction plus incident prose: the largest pairwise Jaccard over the tasks (SPEC: <= 0.10 over
  the public 12; reported here for the whole build).

`render(family, seed)` builds a task's text without promtool, so tests and the CLI can lint a block quickly.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from collections import defaultdict

STRUCT = re.compile(r"^\s*(#|\|[-: |]+\|\s*$|---\s*$|```|alerts: \[|<!--)")
HANDLE_LINE = re.compile(r"^\S*\d\d:\d\d\s+[\w.@-]+:\s*")


def render(family: str, seed: int, master: int = 20260930) -> dict:
    """The workspace text, instruction and tickets of a task, without calibration or promtool."""
    from . import families
    from .build import materialize, static_files
    from .items_triage import Kept
    w, items = families.build(family, seed, master, f"afx-{family.lower()}-s{seed:02d}")
    trng = random.Random(w.tseed ^ 0x7E47)
    kept = []
    if family == "E5" and trng.random() < 0.7:
        k = Kept(w, trng)
        if k.ok:
            kept.append(k)
    w.notes["then"] = materialize(w, items, set())
    tickets = {}
    for it in items:
        t = it.ticket(w, trng)
        t.kind, t.symptom, t.code = it.kind, it.ticket_like, it.code
        tickets[it.rid] = t
    for it in items:
        if it.code == "R01" and getattr(it, "tier", "") == "slow":
            fast = next((o for o in items if o.code == "R01" and getattr(o, "tier", "") == "fast"
                         and o.p.get("svc") == it.p.get("svc")), None)
            tickets[it.rid].pair_of = tickets[fast.rid] if fast is not None else None
    st = static_files(w, items, tickets, kept, trng, "00000000-0000-0000-0000-000000000000")
    return {"task_id": w.task_id, "world": w, "items": items, "tickets": tickets, "static": st["workspace"],
            "instruction": st["instruction"], "kept": kept}


def _names(w) -> list[str]:
    out = {w.company.name, w.domain, w.namespace, w.company.key}
    out |= {s.name for s in w.services} | set(w.teams) | set(w.shared_teams)
    for p in w.people:
        out |= {p.first, p.last, p.full, p.handle}
    for f in w.files.values():
        out.add(f.stem)
    return sorted((x for x in out if x and len(x) > 1), key=len, reverse=True)


def normalise(text: str, names: list[str]) -> str:
    for n in names:
        text = text.replace(n, "<X>")
    text = re.sub(r"[A-Z][a-z]+[A-Z][A-Za-z]+", "<Alert>", text)
    text = re.sub(r"\b[A-Z]+-\d+\b", "<KEY>", text)
    text = re.sub(r"\d+(\.\d+)?", "<N>", text)
    text = re.sub(r"\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b|\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", "<DAY>", text)
    text = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", "<MON>", text)
    return " ".join(text.split())


def sentences(text: str, names: list[str]) -> set[str]:
    out, in_code = set(), False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or STRUCT.match(line):
            continue
        line = HANDLE_LINE.sub("", line)
        for part in re.split(r"(?<=[.!?])\s+", line):
            n = normalise(part, names)
            if len(n) >= 25 and len(n.split()) >= 5:
                out.add(n)
    return out


def units(m: dict) -> list[tuple[str, str]]:
    """(text key, text) for every piece of prose in a built (or rendered) task."""
    from .variety import item_keys
    w, ws = m["world"], m["static"]
    instr = m.get("instruction") or ""
    out: list[tuple[str, str]] = []
    by_packet, by_ticket = {}, {}
    for it in m["items"]:
        t = m["tickets"][it.rid]
        key = sorted(item_keys(w, it), key=lambda k: (not k.startswith("survey:"), k))[0]
        by_ticket[t.key] = key
        for path in t.files:
            by_packet[path.split("/")[1]] = it.code
    # the queue: one unit per ticket, the rest is house text
    parts = re.split(r"(?m)^### ", instr)
    out.append(("house", parts[0]))
    for p in parts[1:]:
        key = p.split(":", 1)[0]
        body, _, tail = p.partition("\n\n")
        lines = p.split("\n")
        main = "\n".join(x for x in lines if not x.startswith(("_Reported", "Reporter:", "`", ">")))
        out.append((by_ticket.get(key, "house"), main))
        out.append(("house", "\n".join(x for x in lines if x.startswith(("_Reported", "Reporter:", "`", ">")))))
    if w.trigger and w.trigger.get("moved"):
        tt = next((x for x in instr.split("\n\n") if w.trigger["date"] in x and w.trigger["new"] in x), "")
        out.append(("split", tt))
    rb_card = {f"runbooks/{rb['file']}": f"{rb['card']}:runbook" for rb in w.runbooks.values() if rb.get("card")}
    answers = {}
    for it in m["items"]:
        keys = item_keys(w, it)
        survey = next((k for k in keys if k.startswith("survey:")), None)
        if survey:
            answers[m["tickets"][it.rid].body.strip()] = survey
    for k in m.get("kept", []):
        answers.update({t.body.strip(): "kept" for t in m.get("kept_tickets", [])})
    for path, text in ws.items():
        if not path.endswith(".md"):
            continue
        if path.startswith("incidents/"):
            out.append((by_packet.get(path.split("/")[1], "house"), text))
        elif path.startswith("runbooks/"):
            out.append((rb_card.get(path, "house"), text))
        elif path.startswith("docs/slo/"):
            out.append(("H11", text))
        elif path.startswith("docs/oncall-survey"):
            head, _, rest = text.partition("**1.**")
            out.append(("survey", head))
            for ans in re.split(r"(?m)^\*\*\d+\.\*\*", "**1.**" + rest)[1:]:
                body = re.sub(r"^ \([\w-]+\)", "", ans).strip()
                key = answers.get(body)
                if key is None:
                    key = "kept" if "woke" in body or "Woken" in body or "laptop" in body else "survey-aside"
                out.append((key, body))
        else:
            out.append(("house", text))
    return out


def check(tasks: list[dict], frac: float = 1 / 3) -> tuple[list[str], dict]:
    """Violations of the per-key sentence rule, and stats (max pairwise 6-gram Jaccard)."""
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    carriers: dict[str, set[str]] = defaultdict(set)
    grams = {}
    for m in tasks:
        names = _names(m["world"])
        tid = m["task_id"]
        g = set()
        for key, text in units(m):
            carriers[key].add(tid)
            for snt in sentences(text, names):
                seen[key][snt].add(tid)
        prose = (m.get("instruction") or "") + "\n".join(v for k, v in m["static"].items() if k.endswith(".md") and (
            k.startswith("incidents/") or k.startswith("docs/oncall-survey")))
        words = normalise(prose, names).split()
        g = {tuple(words[i:i + 6]) for i in range(len(words) - 5)}
        grams[tid] = g
    bad = []
    for key, sents in seen.items():
        n = len(carriers[key])
        cap = max(1, math.ceil(n * frac - 1e-9))
        for snt, who in sents.items():
            if len(who) > cap:
                bad.append(f"[{key}] {len(who)}/{n} tasks (cap {cap}): {snt[:140]}")
    worst = (0.0, None)
    for a, b in itertools.combinations(sorted(grams), 2):
        u = len(grams[a] | grams[b])
        j = len(grams[a] & grams[b]) / u if u else 0.0
        if j > worst[0]:
            worst = (round(j, 3), (a, b))
    return sorted(bad), {"max_jaccard_6gram": worst[0], "pair": worst[1], "keys": {k: len(v) for k, v in carriers.items()}}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="cross-task sameness lint over a block of rendered tasks")
    ap.add_argument("--master", type=int, default=20260930)
    ap.add_argument("--seeds", default="1-9")
    ap.add_argument("--families", default="E1,E2,E3,E4,E5")
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seeds.split("-"))
    tasks = [render(f, s, a.master) for f in a.families.split(",") for s in range(lo, hi + 1)]
    bad, stats = check(tasks)
    print(json.dumps(stats, default=str))
    for b in bad:
        print(b)
    print(f"{len(bad)} sentences over the cap")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
