#!/usr/bin/env python3
"""Pilot subset, keep/drop decisions and the public 12 for the expert tier (SPEC 9.2).

  --pilot     pick the pilot subset from the candidate manifest: 3 tasks per family, chosen greedily to cover the
              family's ticket cards, then the drawn routine kinds, never two tasks with the same item multiset
  --jobs DIR  read Harbor job directories (any depth), join every trial with the manifest and apply the rules:

  family   in band when the better of the two pilot models solves 2-6 of its 9 trials of the family. Above 6/9 for
           both models: the family generator takes the next knob (SPEC 9.2 ladder, `alertforge expert --knobs`);
           seeds are not dropped one at a time
  task     6/6 solved: drop (or knob and re-pilot). 0/6: fairness audit first, never dropped on that alone (a 0/6
           with one requirement behind 4+ of the failures is listed for the audit). 1-5/6: candidate
  length   SPEC 9.2: share of non-solves whose failing requirements are all routine (> 50%: shorten the queue)
  shortcut E4/E5 solves that never replayed a packet (no `range2test`, no `promtool test rules`): > 50% of a
           family's solves means the evidence is not doing its job
  public   E1 x3, E2 x3, E3 x2, E4 x2, E5 x2 from the kept tasks, closest to a 0.5 pooled solve rate first,
           never two tasks with the same item multiset, and never two whose instruction and incident prose share
           more than 10% of their word 6-grams (SPEC 7.6 item 3; names, keys, numbers masked)

    scripts/expert_calibrate.py --manifest dist/v02-candidates/manifest.jsonl --pilot
    scripts/expert_calibrate.py --manifest dist/v02-candidates/manifest.jsonl --jobs jobs/pilot \
        --tasks dist/v02-candidates/tasks --regrade --models harness/model-a harness/model-b --json out.json

Incomplete or invalid pilot cells produce collect; historical scores alone never
trigger hardening. Replay-command counts are approximate diagnostics, not a score.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import re
import sys

from run_meta import harbor_flags, input_mismatch, load_invalid

QUOTA = {"E1": 3, "E2": 3, "E3": 2, "E4": 2, "E5": 2}
TICKETS = {"H01", "H03", "H04", "H05", "H06", "H07", "H08", "H09", "H10", "H11", "H12", "H13", "H15", "H19", "H20",
           "H21", "H22", "H23", "H25"}
REPLAY = re.compile(r"range2test|promtool\s+test\s+rules")
EDIT = re.compile(r"apply_patch|\"(?:Write|Edit|MultiEdit|str_replace_editor)\"|sed\s+-i|cat\s*>|tee\s|>\s*rules/|"
                  r">\s*alertmanager/")


def load_manifest(path: str) -> dict[str, dict]:
    with open(path) as fh:
        return {r["task_id"]: r for r in map(json.loads, fh) if r.get("tier") == "expert"}


def cards(row: dict) -> list[str]:
    return [x.split(":")[1] for x in row["items"]]


def pilot(man: dict[str, dict], per_family: int = 3) -> dict[str, list[str]]:
    out = {}
    for fam in sorted({r["family"] for r in man.values()}):
        rows = sorted((r for r in man.values() if r["family"] == fam), key=lambda r: r["seed"])
        chosen, seen_t, seen_r, multisets = [], set(), collections.Counter(), set()
        for _ in range(per_family):
            best = None
            for r in rows:
                ms = tuple(sorted(cards(r)))
                if r["task_id"] in chosen or ms in multisets:
                    continue
                t = {c for c in cards(r) if c in TICKETS}
                rk = collections.Counter(c for c in cards(r) if c not in TICKETS)
                score = (len(t - seen_t), sum((rk - seen_r).values()), -r["seed"])
                if best is None or score > best[0]:
                    best = (score, r, ms, t, rk)
            if best is None:
                break
            _, r, ms, t, rk = best
            chosen.append(r["task_id"])
            multisets.add(ms)
            seen_t |= t
            seen_r |= rk
        out[fam] = chosen
    return out


def _trial_dirs(roots: list[str]) -> list[str]:
    out = []
    for root in roots:
        out += [os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", "result.json"), recursive=True)
                if os.path.isdir(os.path.join(os.path.dirname(p), "verifier"))]
    return sorted({os.path.realpath(p) for p in out})


def _commands(tdir: str) -> list[str]:
    """Every tool call's arguments in the trial's ATIF trajectory (Harbor writes it one directory down)."""
    paths = sorted(glob.glob(os.path.join(tdir, "*", "trajectory.json")))
    if not paths:
        return []
    with open(paths[0]) as fh:
        steps = json.load(fh).get("steps") or []
    return [json.dumps(tc.get("arguments", {})) for s in steps for tc in (s.get("tool_calls") or [])]


def trials(roots: list[str], man: dict[str, dict], tasks_dir: str | None, invalid=(), regrade=False) -> list[dict]:
    out, seen = [], set()
    for tdir in _trial_dirs(roots):
        with open(os.path.join(tdir, "result.json")) as fh:
            res = json.load(fh)
        tid = str(res.get("task_name", "")).split("/")[-1]
        if tid not in man:
            continue
        identity = res.get("id") or res.get("trial_name") or os.path.realpath(tdir)
        if identity in seen:
            continue
        seen.add(identity)
        info = res.get("agent_info") or {}
        who = f"{info.get('name', '?')}/{(info.get('model_info') or {}).get('name', '?')}"
        rewards = (res.get("verifier_result") or {}).get("rewards") or {}
        failing, ticket = [], {}
        det = os.path.join(tdir, "verifier", "details.json")
        spec = os.path.join(tasks_dir, tid, "tests", "spec.json") if tasks_dir else None
        if spec and os.path.exists(spec):
            with open(spec) as fh:
                ticket = {r["id"]: bool(r.get("ticket")) for r in json.load(fh)["requirements"]}
        if os.path.exists(det):
            with open(det) as fh:
                reqs = json.load(fh).get("requirements", {})
            failing = [(rid, ticket.get(rid, False)) for rid, v in reqs.items() if v.get("s", 0) < 1]
        cmds = _commands(tdir)
        first_edit = next((i for i, c in enumerate(cmds) if EDIT.search(c)), len(cmds))
        flags = harbor_flags(tdir, res, invalid)
        grade_source = "historical"
        if regrade and not flags.get("invalid"):
            from replay_expert import grade_with
            art = os.path.join(tdir, "artifacts", "workspace", "monitoring")
            try:
                if not tasks_dir or not os.path.isdir(art):
                    raise ValueError("the current task and saved workspace are required")
                task = os.path.join(tasks_dir, tid)
                mismatch = input_mismatch(task, art, os.path.join(tdir, "agent", "trajectory.json"))
                if mismatch:
                    flags["invalid"] = mismatch
                rewards, details = grade_with(task, art)
                failing = [(rid, ticket.get(rid, False)) for rid, v in details.get("requirements", {}).items()
                           if v.get("s", 0) < 1]
                grade_source = "current task regrade"
            except Exception as e:
                flags["invalid"] = f"requested regrade failed: {type(e).__name__}: {e}"
        if not all(isinstance(rewards.get(k), (int, float)) and math.isfinite(rewards[k])
                   and 0 <= rewards[k] <= 1 for k in ("reward", "solved")):
            flags["invalid"] = "missing or invalid verifier reward"
        elif not regrade and not flags.get("invalid"):
            flags["invalid"] = "historical grade is unverified; use --regrade with the current tasks"
        out.append({"task_id": tid, "family": man[tid]["family"], "who": who, "solved": rewards.get("solved", 0.0) == 1,
                    "reward": rewards.get("reward", 0.0), "diagnosis": rewards.get("diagnosis"),
                    "failing": failing, "replayed": any(REPLAY.search(c) for c in cmds), "calls": len(cmds),
                    "calls_before_edit": first_edit, "cost": (res.get("agent_result") or {}).get("cost_usd"),
                    "trial": tdir, "grade_source": grade_source, **flags})
    return out


def wilson(k: int, n: int) -> list[float] | None:
    """95% binomial interval; descriptive only (attempts on a task are correlated)."""
    if not n:
        return None
    z, p = 1.959963984540054, k / n
    denom = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0, mid - half), 4), round(min(1, mid + half), 4)]


def decide(rows: list[dict], man: dict[str, dict], tasks_dir: str | None = None,
           models: list[str] | None = None, attempts: int = 3, per_family: int = 3) -> dict:
    """Only a complete, balanced pilot can trigger a difficulty change or task selection.

    Counts are per task *and* model: nine runs of one task cannot stand in for three tasks.
    Invalid trials remain in the report, but never become model failures. Explicit model IDs
    are recommended; inference is allowed only when exactly two participants are present.
    """
    if attempts < 1 or per_family < 1:
        raise ValueError("attempts and per_family must be positive")
    models = sorted(set(models if models is not None else [r["who"] for r in rows]))
    selected = [r for r in rows if r["who"] in models]
    excluded = [{"trial": r.get("trial"), "task_id": r["task_id"], "reason": r["invalid"]}
                for r in selected if r.get("invalid")]
    valid = [r for r in selected if not r.get("invalid")]
    schedule = pilot(man, per_family)
    expected = {tid for tids in schedule.values() for tid in tids}
    by_task, by_fam = collections.defaultdict(list), collections.defaultdict(lambda: collections.defaultdict(list))
    for r in valid:
        by_task[r["task_id"]].append(r)
        if r["task_id"] in expected:
            by_fam[r["family"]][r["who"]].append(r)
    coverage = {}
    for tid in sorted(expected | set(by_task)):
        counts = collections.Counter(r["who"] for r in by_task[tid])
        coverage[tid] = {m: counts[m] for m in models}
    complete = lambda tid: len(models) == 2 and all(n == attempts for n in coverage[tid].values())
    fams = {}
    for fam, tids in schedule.items():
        per = by_fam[fam]
        rates = {who: (sum(x["solved"] for x in per[who]), len(per[who])) for who in models}
        ready = len(tids) == per_family and len(models) == 2 and all(complete(t) for t in tids)
        best = max((s / n for s, n in rates.values() if n), default=0)
        solves = [x for xs in per.values() for x in xs if x["solved"]]
        non = [x for xs in per.values() for x in xs if not x["solved"] and x["failing"]]
        easy = all(s / n > 6 / 9 for s, n in rates.values()) if ready else None
        band = 2 / 9 <= best <= 6 / 9 if ready else None
        fams[fam] = {"solves": rates, "complete": ready, "in_band": band, "too_easy": easy,
                     "decision": "collect" if not ready else "harden" if easy else "in_band" if band else "audit",
                     "solve_rate_ci95": {m: wilson(s, n) for m, (s, n) in rates.items()},
                     "missing": {t: {m: max(0, attempts - n) for m, n in coverage[t].items()}
                                 for t in tids if not complete(t)},
                     "length_share": round(sum(all(not t for _, t in x["failing"]) for x in non) / len(non), 3) if non else None,
                     "solves_without_replay": round(sum(not x["replayed"] for x in solves) / len(solves), 3) if solves else None}
    tasks = {}
    for tid in sorted(expected | set(by_task)):
        xs = by_task[tid]
        k, n = sum(x["solved"] for x in xs), len(xs)
        fails = collections.Counter(rid for x in xs for rid, _ in x["failing"])
        verdict = "incomplete" if not complete(tid) else "drop" if k == n else "audit" if k == 0 else "keep"
        tasks[tid] = {"solved": k, "trials": n, "rate": round(k / n, 3) if n else None, "verdict": verdict,
                      "by_model": coverage[tid], "solve_rate_ci95": wilson(k, n),
                      "top_failure": fails.most_common(1)[0] if fails else None,
                      "audit_reason": "one requirement behind 4+ failures" if fails and fails.most_common(1)[0][1] >= 4 else None}
    eligible = {t: v for t, v in tasks.items()
                if fams.get(man[t]["family"], {}).get("decision") == "in_band"}
    return {"families": fams, "tasks": tasks, "public": public(eligible, man, tasks_dir), "excluded": excluded,
            "protocol": {"models": models, "attempts_per_task_model": attempts, "tasks_per_family": per_family,
                         "pilot": schedule, "purpose": "development calibration; not held-out measurement",
                         "model_count_ok": len(models) == 2,
                         "replay_diagnostic": "approximate tool-name match; not a difficulty decision"},
            "valid_trials": len(valid)}


MAX_JACCARD = 0.10


def prose_grams(tasks_dir: str | None, tid: str) -> set:
    """Word 6-grams of a task's instruction and incident prose (survey included), names, keys and numbers masked."""
    if not tasks_dir or not os.path.isdir(os.path.join(tasks_dir, tid)):
        return set()
    root = os.path.join(tasks_dir, tid)
    ws = os.path.join(root, "environment", "workspace", "monitoring")
    files = [os.path.join(root, "instruction.md")] + glob.glob(os.path.join(ws, "incidents", "*", "*.md")) \
        + glob.glob(os.path.join(ws, "docs", "oncall-survey*.md"))
    text = "\n".join(open(f).read() for f in files if os.path.exists(f))
    names = set()
    own = os.path.join(ws, "teams", "ownership.yaml")
    if os.path.exists(own):
        names = {x for pair in re.findall(r"^  ([\w-]+):$|^    - ([\w-]+)", open(own).read(), re.M) for x in pair if x}
    for n in sorted(names, key=len, reverse=True):
        text = text.replace(n, "X")
    text = re.sub(r"[A-Z][a-z]+[A-Z][A-Za-z]+", "A", text)
    text = re.sub(r"\b[A-Z]+-\d+\b", "K", text)
    words = re.sub(r"\d+(\.\d+)?", "N", text).split()
    return {tuple(words[i:i + 6]) for i in range(len(words) - 5)}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def public(tasks: dict, man: dict[str, dict], tasks_dir: str | None = None) -> dict[str, list[str]]:
    out, grams, all_picked = {}, {}, []
    for fam, k in QUOTA.items():
        cands = sorted((t for t, v in tasks.items() if man[t]["family"] == fam and v["verdict"] == "keep"),
                       key=lambda t: (abs(tasks[t]["rate"] - 0.5), man[t]["seed"]))
        picked, seen = [], set()
        for t in cands:
            ms = tuple(sorted(cards(man[t])))
            g = grams.setdefault(t, prose_grams(tasks_dir, t))
            close = any(_jaccard(g, grams[o]) > MAX_JACCARD for o in all_picked)
            if ms not in seen and not close and len(picked) < k:
                picked.append(t)
                all_picked.append(t)
                seen.add(ms)
        out[fam] = picked
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--tasks", help="candidate tasks dir (default: tasks/ next to the manifest)")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--jobs", nargs="*", default=[])
    ap.add_argument("--json")
    ap.add_argument("--invalid", help="same invalid-trial list as export_runs.py")
    ap.add_argument("--regrade", action="store_true", help="grade saved workspaces with current tasks before decisions")
    ap.add_argument("--models", nargs=2, help="the two pilot participants, as agent/model IDs")
    ap.add_argument("--attempts", type=int, default=3, help="required valid runs per task and model (default: 3)")
    a = ap.parse_args(argv)
    man = load_manifest(a.manifest)
    if a.pilot:
        out = {"pilot": pilot(man)}
    else:
        tdir = a.tasks or os.path.join(os.path.dirname(os.path.abspath(a.manifest)), "tasks")
        rows = trials(a.jobs, man, tdir, load_invalid(a.invalid), regrade=a.regrade)
        out = decide(rows, man, tdir, models=a.models, attempts=a.attempts)
        cost = collections.defaultdict(float)
        for r in rows:
            cost[r["who"]] += r["cost"] or 0.0
        out["cost_usd"] = {k: round(v, 3) for k, v in sorted(cost.items())}
        out["trials"] = len(rows)
    text = json.dumps(out, indent=1, default=str)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
