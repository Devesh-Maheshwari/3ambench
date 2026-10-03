#!/usr/bin/env python3
"""Print the README leaderboard table for one tier from space/data/runs.json (written by scripts/export_runs.py).

    python scripts/leaderboard.py --tier expert                     # markdown table on stdout
    python scripts/leaderboard.py --tier expert --readme README.md  # rewrite the <!-- LEADERBOARD:expert --> block

One row per agent and model (provider prefix dropped), over model runs only: scripted reference policies
(oracle, nop, partial, OpenEnv traces) and runs flagged `invalid` are left out. The expert tier counts the
leaderboard tasks only (`tasks[...].leaderboard`). Re-graded runs count with their re-graded reward. Columns:
runs, mean reward, solved/total and, for the expert tier, the leaderboard tasks the row covers and the mean
`diagnosis` and `routine` scores (rows that cover different tasks are not directly comparable).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect(data: dict, tier: str) -> tuple[list[dict], int, int]:
    """(rows, number of tasks with a counted run, number of invalid runs left out)."""
    tasks, groups, tids, invalid = data.get("tasks", {}), {}, set(), 0
    for r in data.get("runs", []):
        t = tasks.get(r["task_id"], {})
        own = r.get("tier") if r.get("tier") in ("core", "expert") else "expert" if r["task_id"].startswith("afx-") \
            else "core"   # runs.json v1 used `tier` for the difficulty
        if own != tier:
            continue
        if r.get("model_family") == "reference" or r.get("source") != "harbor" or not r.get("model"):
            continue
        if tier == "expert" and t.get("leaderboard") is False:
            continue
        if r.get("invalid"):
            invalid += 1
            continue
        tids.add(r["task_id"])
        groups.setdefault((r["agent"].split(": ", 1)[-1], r["model"].rsplit("/", 1)[-1]), []).append(r)
    rows = []
    for (agent, model), rs in groups.items():
        mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
        rows.append({"agent": agent, "model": model, "runs": len(rs), "tasks": len({r["task_id"] for r in rs}),
                     "reward": mean([r["reward"] for r in rs]),
                     "solved": sum(1 for r in rs if (r.get("keys") or {}).get("solved", 0) >= 1),
                     "diagnosis": mean([float((r.get("keys") or {}).get("diagnosis") or 0) for r in rs]),
                     "routine": mean([float((r.get("keys") or {}).get("routine") or 0) for r in rs])})
    rows.sort(key=lambda g: (-g["reward"], g["agent"], g["model"]))
    return rows, len(tids), invalid


def table(rows: list[dict], tier: str, n_lb: int = 0) -> str:
    expert = tier == "expert"
    head = ["Agent", "Model", "Runs"] + (["Tasks"] if expert else []) + ["Mean reward", "Solved"] + \
        (["Diagnosis", "Routine"] if expert else [])
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for g in rows:
        cells = [g["agent"], g["model"], str(g["runs"])] + ([f"{g['tasks']}/{n_lb}"] if expert else []) + \
            [f"{g['reward']:.3f}", f"{g['solved']}/{g['runs']}"]
        if expert:
            cells += [f"{g['diagnosis']:.3f}", f"{g['routine']:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def fill(readme: str, tier: str, block: str) -> str:
    rx = re.compile(rf"(<!-- LEADERBOARD:{re.escape(tier)} -->)(.*?)(<!-- /LEADERBOARD -->)", re.S)
    if not rx.search(readme):
        raise SystemExit(f"no <!-- LEADERBOARD:{tier} --> ... <!-- /LEADERBOARD --> block in the README")
    return rx.sub(lambda m: f"{m.group(1)}\n{block}\n{m.group(3)}", readme, count=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", choices=["core", "expert"], default="expert")
    ap.add_argument("--runs", default=os.path.join(ROOT, "space", "data", "runs.json"))
    ap.add_argument("--readme", help="rewrite this README's leaderboard block in place instead of printing")
    a = ap.parse_args()
    with open(a.runs) as fh:
        data = json.load(fh)
    rows, n_tasks, invalid = collect(data, a.tier)
    if not rows:
        print(f"no valid model runs for tier {a.tier} in {a.runs}", file=sys.stderr)
        return 1
    n_lb = sum(1 for t in data.get("tasks", {}).values() if t.get("leaderboard")) or n_tasks
    note = f"{sum(g['runs'] for g in rows)} runs on {n_tasks} tasks"
    if invalid:
        many = invalid != 1
        note += f"; {invalid} run{'s' if many else ''} flagged invalid {'are' if many else 'is'} not counted"
    block = f"{table(rows, a.tier, n_lb)}\n\n{note[0].upper()}{note[1:]}."
    if not a.readme:
        print(block)
        return 0
    with open(a.readme) as fh:
        text = fh.read()
    with open(a.readme, "w") as fh:
        fh.write(fill(text, a.tier, block))
    print(f"updated the {a.tier} leaderboard in {a.readme}: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
