#!/usr/bin/env python3
"""Record scripted, LLM-free reference episodes through the OpenEnv episode layer, for the replay Space.

Each episode drives `alertforge.episode.Episode` (the class the OpenEnv server wraps) in parity reward mode,
so the per-step rewards sum to the Harbor reward. After every step the workspace is graded with the same
grader, and the trimmed grader details (per-check pass/fail) are stored with the step.

Policies (all read the conventions and the files they will change first):
  oracle    writes the oracle solution file by file, runs `run_checks` in between, then submits
  partial   writes the partial reference solution (half the requests, one plausible mistake)
  careless  oracle, with two slips that are caught and fixed: a hand edit that mis-indents one key of a
            rule file (caught by `run_checks`), and a page route sent to Slack (caught by `route_test`)

    export AF_PROMTOOL=/path/to/promtool AF_AMTOOL=/path/to/amtool
    python scripts/record_reference_runs.py --out dist/replay/traces [--tasks af-001,af-009] [-j 4]
    python scripts/export_runs.py --traces dist/replay/traces --out space/data/runs.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from alertforge.episode import Episode  # noqa: E402
from alertforge.solutions import break_indent  # noqa: E402

# one task per workflow, all three tiers
DEFAULT_TASKS = ["af-001", "af-009", "af-013", "af-017", "af-021", "af-029"]
POLICIES = ["oracle", "partial", "careless"]
TIERS = ("easy", "medium", "hard")


def heredoc_writes(script: str) -> list[tuple[str, str]]:
    """Parse `cat > 'path' <<'AF_EOF'` blocks of a reference solve.sh into (path, content) writes."""
    out, cur, buf = [], None, []
    for line in open(script).read().splitlines():
        if cur is None and line.startswith("cat > '") and line.endswith("<<'AF_EOF'"):
            cur, buf = line.split("'")[1], []
        elif cur is not None and line == "AF_EOF":
            out.append((cur, "\n".join(buf) + "\n"))
            cur = None
        elif cur is not None:
            buf.append(line)
    return out


def trim_details(details: dict) -> dict:
    """Keep what the checks panel needs from a grader details dict."""
    reqs = {}
    for rid, rd in (details.get("requirements") or {}).items():
        fams = rd.get("families") or {}
        reqs[rid] = {"s": rd.get("s"), "families": {"pos": fams["pos"]} if "pos" in fams else {}}
    return {"checks": details.get("checks") or {}, "requirements": reqs,
            "preservation_items": details.get("preservation_items") or [], "tamper": details.get("tamper") or [],
            "syntax_errors": details.get("syntax_errors") or {}}


def indent_slip(rules: list[tuple[str, str]], ws0: str) -> list[dict]:
    """A hand edit of an already-fixed, pre-existing rule file that breaks its YAML, then the fix."""
    pre = [(p, c) for p, c in rules if os.path.exists(os.path.join(ws0, p))] or rules
    if not pre:
        return []
    p, c = pre[0]
    bad = break_indent(c)
    bad_line = next((b for b, g in zip(bad.splitlines(), c.splitlines()) if b != g), None)
    acts = [{"think": f"Tidy up {p} by hand before moving on."},
            {"tool": "write_file", "path": p, "content": bad}, {"tool": "run_checks"},
            {"think": f"`run_checks` says {p} no longer loads: an `expr:` key is indented one space too far, "
                      "so every rule in the file is gone. Fix the indentation."}]
    if bad_line is not None and bad.count("\n" + bad_line + "\n") == 1:
        acts.append({"tool": "replace_in_file", "path": p, "old": "\n" + bad_line + "\n",
                     "new": "\n" + bad_line[1:] + "\n"})
    else:
        acts.append({"tool": "write_file", "path": p, "content": c})
    acts.append({"tool": "run_checks"})
    return acts


def bad_route(am_text: str, route_req: dict | None) -> str | None:
    """A plausible routing slip: the new team's page route sends to its Slack receiver instead of the pager."""
    if not route_req or not route_req["route"]["pos"]:
        return None
    expect = route_req["route"]["pos"][0]["expect"]
    pager = next((e for e in expect if e.startswith("pagerduty-")), None)
    chat = next((e for e in expect if e.startswith("slack-")), None)
    if not pager or not chat or f"receiver: {pager}" not in am_text:
        return None
    return am_text.replace(f"receiver: {pager}", f"receiver: {chat}", 1)


def plan(task_dir: str, policy: str) -> list[dict]:
    """The scripted action list (with `think` notes interleaved) for one policy."""
    tid = os.path.basename(task_dir)
    oracle = heredoc_writes(os.path.join(task_dir, "solution", "solve.sh"))
    writes = heredoc_writes(os.path.join(ROOT, "partial", tid, "solve.sh")) if policy == "partial" else oracle
    with open(os.path.join(task_dir, "tests", "spec.json")) as fh:
        spec = json.load(fh)
    route_req = next((r for r in spec["requirements"] if r["kind"] == "route"), None)
    probe_labels = route_req["route"]["pos"][0]["labels"] if route_req else None
    ws0 = os.path.join(task_dir, "environment", "workspace", "monitoring")

    acts: list[dict] = [{"think": "Start by listing the repo and reading the conventions in README.md."},
                        {"tool": "list_files"}, {"tool": "read_file", "path": "README.md"}]
    targets = [p for p, _ in writes if os.path.exists(os.path.join(ws0, p))]
    if targets:
        acts.append({"think": "Read every file the change requests touch before editing: " + ", ".join(targets) + "."})
        acts += [{"tool": "read_file", "path": p} for p in targets]

    rules = [(p, c) for p, c in writes if p.startswith("rules/")]
    am = [(p, c) for p, c in writes if p.startswith("alertmanager/")]
    for n, (p, c) in enumerate(rules):
        acts.append({"think": f"Update {p}."})
        acts.append({"tool": "write_file", "path": p, "content": c})
        if n % 2 == 1 or n == len(rules) - 1:
            acts.append({"tool": "run_checks"})
    if policy == "careless":
        acts += indent_slip(rules, ws0)
    for p, c in am:
        slip = bad_route(c, route_req) if policy == "careless" else None
        if slip:
            acts.append({"think": "Route the new team and add the inhibition in the Alertmanager config."})
            acts.append({"tool": "write_file", "path": p, "content": slip})
            acts.append({"tool": "route_test", "labels": probe_labels})
            acts.append({"think": "`route_test` shows the page alert only reaching Slack: the page route points at "
                                  "the wrong receiver. Rewrite the config."})
        else:
            acts.append({"think": "Route the new team and add the inhibition in the Alertmanager config."})
        acts.append({"tool": "write_file", "path": p, "content": c})
        if probe_labels:
            acts.append({"tool": "route_test", "labels": probe_labels})
    acts.append({"tool": "run_checks"})
    acts.append({"think": "Checks pass locally; submit."})
    acts.append({"tool": "submit"})
    return acts


def record(task_dir: str, policy: str) -> dict:
    tid = os.path.basename(task_dir)
    tier = next(t for t in TIERS if f"-{t}-" in tid)
    ep = Episode(task_dir, split="train", mode="parity", tier=tier)
    steps = []
    try:
        for a in plan(task_dir, policy):
            if "think" in a:
                steps.append({"think": a["think"]})
                continue
            res = ep.step(a)
            keys, details = ep.grader.grade(ep.ws)  # cached by workspace fingerprint: no second promtool run
            steps.append({"action": a, "output": res["output"], "ok": res["ok"], "reward": res["reward"],
                          "done": res["done"], "metadata": res["metadata"], "keys": keys,
                          "details": trim_details(details)})
            if res["done"]:
                break
    finally:
        ep.close()
    total = sum(s.get("reward", 0.0) for s in steps)
    last = steps[-1]
    return {"format": "3ambench-trace/1", "source": "openenv", "task_id": tid, "tier": tier,
            "agent": f"reference: {policy}", "model": "scripted (no LLM)", "reward_mode": "parity",
            "total_reward": round(total, 6), "harbor_reward": last["metadata"].get("harbor_reward"),
            "final_keys": last["keys"], "steps": steps}


def _job(args: tuple[str, str, str]) -> str:
    task_dir, policy, out = args
    tr = record(task_dir, policy)
    path = os.path.join(out, f"{tr['task_id']}__{policy}.json")
    with open(path, "w") as fh:
        json.dump(tr, fh)
    n = sum(1 for s in tr["steps"] if "action" in s)
    return f"{tr['task_id']:44s} {policy:9s} steps={n:3d} sum_r={tr['total_reward']:.4f} harbor={tr['harbor_reward']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "replay", "traces"))
    ap.add_argument("--tasks", default=",".join(DEFAULT_TASKS), help="comma-separated task ids or prefixes")
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("-j", "--jobs", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    all_tasks = sorted(os.listdir(os.path.join(ROOT, "tasks")))
    chosen = [t for pre in a.tasks.split(",") for t in all_tasks if t.startswith(pre.strip())]
    jobs = [(os.path.join(ROOT, "tasks", t), p, a.out) for t in chosen for p in a.policies.split(",")]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for line in pool.map(_job, jobs):
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
