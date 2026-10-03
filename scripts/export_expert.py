"""Expert-tier Harbor trials for the replay export (used by scripts/export_runs.py).

Expert tasks are graded per requirement (see replay_expert.py). Each run shows its timeline (tool calls, diffs,
output) and the grader's requirement breakdown. A per-step reward curve is drawn only when it is exact and cheap:
every file edit in the trajectory was recovered (structured edit tools, heredocs, apply_patch) and replaying them
reproduces the stored final workspace byte for byte. Then at most 9 intermediate states are graded with the task's
own grader; the 10th point is the final grade. Edits made through scripts or `sed -i` cannot be replayed exactly,
so those runs are terminal-only (`curve: "terminal"`) with the breakdown of the final state.

Re-grading (`--regrade-tasks DIR`): the stored final workspace (Harbor's artifact) is graded with
DIR/<task>/tests/grade.py, so runs can be re-scored after hidden-test fixes. The run keeps Harbor's numbers in
`reward_original` / `keys_original`.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import sys
import tempfile

from replay_atif import call_ops, iter_events, load_trajectory, renamed_tree
from replay_expert import EXPERT_KEYS, EXPERT_RE, ExpertCatalog, grade_with, instruction_seen, run_extras
from replay_ws import Workspace, args_summary, clip
from run_meta import input_mismatch

MAX_POINTS = 9   # intermediate grades per run; the final grade is the 10th point


def task_release(task_dir: str) -> str:
    """What a regrade used, as the Space shows it: "v0.2.0 tasks" (the version in the task's task.toml)."""
    try:
        with open(os.path.join(task_dir, "task.toml")) as fh:
            m = re.search(r'^version = "([^"]+)"', fh.read(), re.M)
    except OSError:
        m = None
    return f"v{m.group(1)} tasks" if m else "the current tasks"
LEVELS = [(600, 1600, 600), (400, 1000, 400), (250, 600, 250), (150, 300, 150)]   # output, diff, think
ORIGINAL_KEYS = ("reward", "solved", "progress", "diagnosis", "routine", "preservation")
SKIP = {"__pycache__", ".DS_Store"}


def tree(root: str) -> dict[str, bytes]:
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        for fn in fns:
            if fn not in SKIP:
                p = os.path.join(dp, fn)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = fh.read()
    return out


def overlay_of(base: dict[str, bytes], cur: dict[str, bytes]) -> dict[str, bytes | None]:
    """Files that differ from the starting workspace (None: deleted)."""
    ov: dict[str, bytes | None] = {k: v for k, v in cur.items() if base.get(k) != v}
    ov.update({k: None for k in base if k not in cur})
    return ov


def pick(n: int, k: int = MAX_POINTS) -> list[int]:
    """Indices of at most k of n points: the last index of each of k equal chunks."""
    return list(range(n)) if n <= k else sorted({-(-(c + 1) * n // k) - 1 for c in range(k)})


def render(run: dict, level: int) -> None:
    """(Re)write the clipped texts of an expert run's steps from the raw texts kept in run['_raw']."""
    out_n, diff_n, think_n = LEVELS[min(level, len(LEVELS) - 1)]
    for s, raw in zip(run["steps"], run["_raw"]):
        for key, n in (("text", think_n), ("output", out_n), ("diff", diff_n)):
            if raw.get(key):
                s[key] = clip(raw[key], n)


class ExpertRuns:
    def __init__(self, expert_dir: str, regrade_dirs: list[str], jobs: int = 4, curves: bool = True,
                 leaderboard=(), root: str = ""):
        self.expert_dir, self.regrade_dirs, self.jobs, self.curves = expert_dir, regrade_dirs, jobs, curves
        self.leaderboard, self.root = set(leaderboard), root
        self.catalogs: dict[str, ExpertCatalog] = {}
        self.bases: dict[str, dict] = {}
        self.plans: list[dict] = []

    # ------------------------------------------------------------------ tasks
    def regrade_dir(self, tid: str) -> str | None:
        return next((os.path.join(d, tid) for d in self.regrade_dirs
                     if os.path.isfile(os.path.join(d, tid, "tests", "spec.json"))), None)

    def task_dir(self, tid: str) -> str | None:
        p = self.regrade_dir(tid) or os.path.join(self.expert_dir, tid)
        return p if os.path.isfile(os.path.join(p, "tests", "spec.json")) else None

    def catalog(self, tid: str) -> ExpertCatalog:
        if tid not in self.catalogs:
            self.catalogs[tid] = ExpertCatalog(self.task_dir(tid), tid in self.leaderboard)
        return self.catalogs[tid]

    def base(self, tdir: str) -> dict[str, bytes]:
        if tdir not in self.bases:
            self.bases[tdir] = tree(os.path.join(tdir, "environment", "workspace", "monitoring"))
        return self.bases[tdir]

    def tasks_index(self) -> dict:
        return {tid: c.meta() for tid, c in sorted(self.catalogs.items())}

    # ------------------------------------------------------------------ trials
    def add(self, trial: str, res: dict, run: dict, keys: dict) -> bool:
        m = EXPERT_RE.search(res.get("task_name") or res.get("trial_name") or trial)
        tdir = self.task_dir(m.group(1)) if m else None
        if not tdir:
            return False
        tid = m.group(1)
        cat = self.catalog(tid)
        det = os.path.join(trial, "verifier", "details.json")
        art = os.path.join(trial, "artifacts", "workspace", "monitoring")
        art = renamed_tree(art) if os.path.isdir(art) else None   # read through --rename-map, if any
        plan = {"run": run, "tid": tid, "tdir": tdir, "keys": keys, "art": art,
                "details": json.load(open(det)) if os.path.isfile(det) else None, "steps": [], "raw": [],
                "points": [], "jobs": {}, "why": ""}
        run.update(task_id=tid, tier="expert", difficulty="expert", workflow=cat.meta()["workflow"])
        if self.regrade_dir(tid) and plan["art"]:
            plan["jobs"]["final"] = (tdir, plan["art"], None)
            mismatch = input_mismatch(tdir, plan["art"], os.path.join(trial, "agent", "trajectory.json"))
            if mismatch:
                run["invalid"] = mismatch
        elif self.regrade_dir(tid):
            run["invalid"] = "requested regrade unavailable: the saved workspace is missing"
        traj_p = os.path.join(trial, "agent", "trajectory.json")
        if os.path.isfile(traj_p):
            traj = load_trajectory(traj_p)
            if instruction_seen(traj, cat.instruction) is False and not run.get("invalid"):
                run["invalid"] = "the task instruction changed after this run; re-run it"
            self._timeline(plan, traj)
        self.plans.append(plan)
        return True

    def _timeline(self, plan: dict, traj: dict) -> None:
        """Steps with raw texts; with an exact replay, the overlays of the states worth grading."""
        ws = Workspace(plan["tdir"])
        base = self.base(plan["tdir"])
        steps, raw, snaps, pending, lossy, n_edits = plan["steps"], plan["raw"], [], {}, False, 0
        try:
            for ev in iter_events(traj):
                if ev[0] == "think":
                    steps.append({"kind": "think"})
                    raw.append({"text": ev[1]})
                elif ev[0] == "call":
                    _, cid, name, args = ev
                    ops, unknown = call_ops(name, args)
                    diffs, ok_all = [], True
                    for op in ops:
                        d, ok = ws.apply(op)
                        diffs.append(d)
                        ok_all = ok_all and ok
                    lossy = lossy or unknown or not ok_all
                    n_edits += len(ops)
                    steps.append({"kind": "tool_call", "tool": name, "args": args_summary(name, args)})
                    raw.append({"diff": "".join(diffs)})
                    pending[cid] = bool(ops)
                else:
                    _, cid, text = ev
                    tool = next((s["tool"] for s in reversed(steps) if s["kind"] == "tool_call"), "")
                    steps.append({"kind": "observation", "tool": tool})
                    raw.append({"output": text})
                    if pending.pop(cid, False) and not lossy:
                        snaps.append((len(steps) - 1, overlay_of(base, tree(ws.ws))))
            final = tree(ws.ws) if n_edits and not lossy and plan["art"] else None
        finally:
            ws.close()
        if not n_edits:
            plan["why"] = "no file edit could be recovered from the trajectory"
        elif lossy:
            plan["why"] = "some edits were made through shell commands or scripts that cannot be replayed exactly"
        elif final is None or final != tree(plan["art"]):
            plan["why"] = "replaying the recovered edits does not reproduce the stored final workspace"
        elif self.curves:
            plan["points"] = [snaps[i] for i in pick(len(snaps) - 1)] + [snaps[-1]]
            for k, (_, ov) in enumerate(plan["points"][:-1]):
                plan["jobs"][f"p{k}"] = (plan["tdir"], None, ov)
        else:
            plan["why"] = "per-step grading is off for this export (--expert-curves off)"

    # ------------------------------------------------------------------ grading
    def _grade(self, job: tuple) -> tuple[dict, dict]:
        tdir, ws, ov = job
        if ov is None:
            return grade_with(tdir, ws)
        tmp = tempfile.mkdtemp(prefix="af-xreplay-")
        try:
            dst = os.path.join(tmp, "monitoring")
            shutil.copytree(os.path.join(tdir, "environment", "workspace", "monitoring"), dst, symlinks=True)
            for rel, data in ov.items():
                p = os.path.join(dst, rel)
                if data is None:
                    if os.path.isfile(p):
                        os.remove(p)
                    continue
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(data)
            return grade_with(tdir, dst)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def finish(self) -> list[dict]:
        jobs = [(i, name, job) for i, p in enumerate(self.plans) for name, job in p["jobs"].items()]
        results: dict[tuple, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.jobs)) as pool:
            futs = {pool.submit(self._grade, job): (i, name) for i, name, job in jobs}
            for n, fut in enumerate(concurrent.futures.as_completed(futs), 1):
                try:
                    results[futs[fut]] = fut.result()
                except Exception as e:  # noqa: BLE001 - a failed grade keeps Harbor's own result
                    results[futs[fut]] = e
                if n % 25 == 0:
                    print(f"  graded {n}/{len(jobs)} expert states", file=sys.stderr)
        return [self._finalize(p, {name: results[(i, name)] for name in p["jobs"]}) for i, p in enumerate(self.plans)]

    def _finalize(self, plan: dict, res: dict) -> dict:
        run, keys, details = plan["run"], plan["keys"], plan["details"]
        order = self.catalog(plan["tid"]).order
        fin = res.get("final")
        note = ""
        if isinstance(fin, Exception):
            print(f"re-grade failed for {run.get('trial')}: {fin}", file=sys.stderr)
            note = " Re-grading failed; this is Harbor's own result."
            run["invalid"] = "requested regrade failed; the historical score is not eligible"
        elif fin is not None:
            run["reward_original"] = keys.get("reward", 0.0)
            run["keys_original"] = {k: keys[k] for k in ORIGINAL_KEYS if k in keys}
            run["regraded"] = task_release(plan["tdir"])
            keys, details = fin
        run["keys"] = {k: keys.get(k) for k in EXPERT_KEYS if k in keys}
        run["reward"] = keys.get("reward", 0.0)
        extras = run_extras(details, order)
        final_reqs = extras.pop("reqs")
        run.update(extras)
        steps, raw, cum, prev = plan["steps"], plan["raw"], 0.0, None
        graded = plan["points"] and all(isinstance(res.get(f"p{k}"), tuple) for k in range(len(plan["points"]) - 1))
        if graded:
            marks = {si: res[f"p{k}"] for k, (si, _) in enumerate(plan["points"][:-1])}
            marks[plan["points"][-1][0]] = (keys, details)
            for si, s in enumerate(steps):
                if si in marks:
                    k_, d_ = marks[si]
                    new = 0.4 * float(k_.get("progress", 0.0))
                    reqs = run_extras(d_, order)["reqs"]
                    s.update(r=round(new - cum, 6), phi=k_.get("progress"))
                    if reqs != prev:
                        s["reqs"], prev = reqs, reqs
                    cum = new
                if s["kind"] == "observation":
                    s["cum"] = round(cum, 6)
        if graded:
            run["curve"] = "replayed"
            run["curve_note"] = (f"Every edit was recovered and replays to the stored final workspace; "
                                 f"{len(plan['points'])} states graded with the task's grader (0.4 × progress per "
                                 "point, then the final reward)." + note)
        else:
            why = plan["why"] or ("intermediate grading failed" if plan["points"] else "no trajectory was recorded")
            run["curve"] = "terminal"
            run["curve_note"] = f"Final reward and requirement breakdown only: {why}.{note}"
        out = f"Harbor verifier: reward {run['reward']:.4f}"
        if "reward_original" in run:
            out = (f"Re-graded with the {run['regraded']} ({plan['tid']}): reward {run['reward']:.4f} "
                   f"(Harbor verifier: {run['reward_original']:.4f})")
        last = {"kind": "observation", "tool": "verifier", "output": out, "r": round(run["reward"] - cum, 6),
                "cum": round(run["reward"], 6)}
        if graded and final_reqs != prev:
            last["reqs"] = final_reqs
        steps.append(last)
        raw.append({})
        run["steps"], run["_raw"] = steps, raw
        render(run, 0)
        return run
