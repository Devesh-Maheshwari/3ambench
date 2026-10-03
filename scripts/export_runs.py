#!/usr/bin/env python3
"""Export Harbor jobs and OpenEnv step traces into space/data/runs.json for the 3amBench Replay Space.

    python scripts/export_runs.py --traces dist/replay/traces --harbor jobs/ --out space/data/runs.json

--traces  trace JSON files (or dirs of them) written by scripts/record_reference_runs.py; any trace with
          {"task_id", "agent", "steps": [{"action", "output", "reward", ...}]} works, per-step grader
          details are optional
--harbor  Harbor job dirs (or parents of job dirs); every trial with a verifier result is exported.
          Harbor only reports a terminal reward. When a trial has agent/trajectory.json (ATIF), the agent's
          file edits are replayed on a copy of the task workspace and graded after each edit with the same
          grader, which gives a per-step curve; the last step is always Harbor's own verifier reward.
          Edits that cannot be recovered (for example `sed -i` in a shell) make the curve approximate, and
          the run says so in `curve_note`.
          Expert-tier trials (afx-*, tasks from --expert-tasks-dir) carry the grader's per-requirement breakdown;
          their curve is drawn only when the replay is exact (scripts/export_expert.py), else `curve: terminal`.
--regrade-tasks DIR
          grade every stored workspace (Harbor's artifacts/workspace/monitoring) whose task is in DIR with
          DIR/<task>/tests/grade.py, e.g. after hidden-test fixes; runs keep Harbor's numbers in
          `reward_original` / `keys_original`.
--invalid FILE
          extra runs to leave out of leaderboards, one `<substring of the trial path>  <reason>` per line.
          Infrastructure failures and runs whose prompt is not the task's current instruction are flagged
          automatically (scripts/run_meta.py).
--rename-map FILE
          {"map": {old: new}}: host names the tasks renamed after the runs were recorded (0.2.0 moved the fictional
          company domains to reserved `.example` names). Trajectories, traces and stored workspaces are read
          through it before they are replayed, compared with the task or graded.
Needs the pinned tools for replays: AF_PROMTOOL=/path/to/promtool AF_AMTOOL=/path/to/amtool.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import redact  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from export_expert import LEVELS, ExpertRuns, render, task_release  # noqa: E402
from replay_atif import call_ops, iter_events, load_renames, load_trajectory, renamed, renamed_tree  # noqa: E402
from replay_checks import Catalog  # noqa: E402
from replay_expert import EXPERT_RE, grade_with  # noqa: E402
from replay_ws import MAX_DIFF, MAX_OUT, MAX_THINK, Workspace, args_summary, clip  # noqa: E402
from run_meta import fit, harbor_flags, load_invalid, model_family  # noqa: E402

KEYS = ["reward", "solved", "outcome", "progress", "preservation", "req_alerts", "req_repairs", "req_recording",
        "req_routing", "req_inhibit", "fire_rate", "silent_rate", "label_rate", "check_pass_rate", "syntax_ok", "tamper"]
TASK_RE = re.compile(r"(af-\d{3})")
TIERS = {"core": {"label": "Standard", "release": "v0.1.1"}, "expert": {"label": "Expert", "release": "v0.2.0"}}
CURVES = ("Expert runs get a per-step curve only when every edit replays exactly to the stored final workspace "
          "(at most 10 graded states); the others show the final reward and requirement breakdown.")


def meta_of(task_id: str) -> tuple[str, str]:
    m = re.match(r"af-\d+-(.+)-(easy|medium|hard)-s\d+$", task_id)
    return (m.group(1), m.group(2)) if m else ("", "")


def leaderboard_tasks(registry: str, name: str = "3ambench-expert-lb") -> list[str]:
    try:
        with open(registry) as fh:
            rows = [d for d in json.load(fh) if d.get("name") == name]
    except (OSError, ValueError):
        return []
    return [t["name"] for t in rows[-1]["tasks"]] if rows else []


class Exporter:
    def __init__(self, tasks_dir: str, replay: bool = True, expert: ExpertRuns | None = None,
                 regrade_dirs: list[str] | None = None, invalid: list[tuple[str, str]] | None = None):
        self.tasks_dir = tasks_dir
        self.replay = replay
        self.expert = expert
        self.regrade_dirs = regrade_dirs or []
        self.invalid = invalid or []
        self.catalogs: dict[str, Catalog] = {}
        self.graders: dict = {}
        self.runs: list[dict] = []

    def task_dir(self, ref: str) -> str | None:
        m = TASK_RE.search(ref or "")
        if not m:
            return None
        hits = sorted(glob.glob(os.path.join(self.tasks_dir, m.group(1) + "-*")))
        return hits[0] if hits else None

    def catalog(self, task_id: str) -> Catalog:
        if task_id not in self.catalogs:
            self.catalogs[task_id] = Catalog(os.path.join(self.tasks_dir, task_id))
        return self.catalogs[task_id]

    def grader(self, task_dir: str):
        if task_dir not in self.graders:
            from alertforge.grader.grade import Grader
            with open(os.path.join(task_dir, "tests", "spec.json")) as fh:
                self.graders[task_dir] = Grader(json.load(fh), os.path.join(task_dir, "tests", "hidden"))
        return self.graders[task_dir]

    def _finish(self, run: dict, keys: dict, details: dict | None) -> None:
        cat = self.catalog(run["task_id"])
        run["keys"] = {k: keys.get(k) for k in KEYS if k in keys}
        run["reward"] = keys.get("reward", 0.0)
        run["req_scores"] = {rid: rd.get("s") for rid, rd in ((details or {}).get("requirements") or {}).items()}
        run["_final"] = cat.states(details) if details else {}
        wf, tier = meta_of(run["task_id"])
        run.setdefault("workflow", wf)
        run.update(tier="core", difficulty=tier, model_family=model_family(run["agent"], run["model"], run["source"]))
        self.runs.append(run)

    # ------------------------------------------------------------------ OpenEnv traces
    def add_trace(self, path: str) -> None:
        with open(path) as fh:
            tr = json.loads(renamed(fh.read()))
        tdir = self.task_dir(tr["task_id"])
        tid = os.path.basename(tdir) if tdir else tr["task_id"]
        cat = self.catalog(tid)
        ws = Workspace(tdir)
        steps, cum, last_keys, last_det = [], 0.0, None, None
        try:
            for s in tr["steps"]:
                if "think" in s:
                    steps.append({"kind": "think", "text": clip(s["think"], MAX_THINK)})
                    continue
                a = dict(s["action"])
                tool = a.pop("tool", "")
                diff = ""
                if tool == "write_file" and a.get("path"):
                    diff, _ = ws.apply(("write", a["path"], a.get("content") or ""))
                elif tool == "replace_in_file" and a.get("path") and s.get("ok", True):
                    diff, _ = ws.apply(("replace", a["path"], a.get("old") or "", a.get("new") or "", False))
                steps.append({"kind": "tool_call", "tool": tool, "args": args_summary(tool, a),
                              **({"diff": clip(diff, MAX_DIFF)} if diff else {})})
                r = float(s.get("reward") or 0.0)
                cum += r
                obs = {"kind": "observation", "tool": tool, "output": clip(s.get("output"), MAX_OUT),
                       "ok": s.get("ok", True), "r": round(r, 6), "cum": round(cum, 6)}
                phi = (s.get("metadata") or {}).get("phi")
                if phi is not None:
                    obs["phi"] = round(phi, 6)
                if s.get("details"):
                    obs["_checks"] = cat.states(s["details"])
                    last_det = s["details"]
                last_keys = s.get("keys") or last_keys
                steps.append(obs)
        finally:
            ws.close()
        keys = tr.get("final_keys") or last_keys or {"reward": cum}
        run = {"task_id": tid, "agent": tr.get("agent", "unknown"), "model": tr.get("model", ""),
               "source": tr.get("source", "openenv"), "steps": steps,
               "curve": "per-step", "curve_note": f"OpenEnv episode, {tr.get('reward_mode', 'parity')} reward mode: "
                                                   "step rewards are the environment's own and sum to the Harbor reward."}
        self._finish(run, keys, tr.get("final_details") or last_det)

    # ------------------------------------------------------------------ Harbor trials
    def add_harbor(self, path: str) -> None:
        trials = [os.path.dirname(p) for p in glob.glob(os.path.join(path, "**", "result.json"), recursive=True)
                  if os.path.isfile(os.path.join(os.path.dirname(p), "verifier", "reward.json"))]
        for t in sorted(set(trials)):
            try:
                self._harbor_trial(t)
            except Exception as e:  # noqa: BLE001 - one broken trial should not stop the export
                print(f"skip {t}: {e}", file=sys.stderr)

    def _harbor_trial(self, tdir_: str) -> None:
        with open(os.path.join(tdir_, "result.json")) as fh:
            res = json.load(fh)
        rewards = (res.get("verifier_result") or {}).get("rewards")
        if not rewards:
            return
        ref = res.get("task_name") or res.get("trial_name") or tdir_
        task_dir = None if EXPERT_RE.search(ref) else self.task_dir(ref)
        if not task_dir and not (self.expert and EXPERT_RE.search(ref)):
            return
        info = res.get("agent_info") or {}
        agent = info.get("name") or "agent"
        if agent == "oracle" and re.search(r"partial", tdir_):
            agent = "oracle (partial solution)"
        model_info = info.get("model_info") or {}
        model = model_info.get("name") or ((res.get("config") or {}).get("agent") or {}).get("model_name") or ""
        run = {"agent": f"harbor: {agent}", "model": model, "source": "harbor",
               "trial": res.get("trial_name", os.path.basename(tdir_)),
               "cost_usd": (res.get("agent_result") or {}).get("cost_usd")}
        flags = harbor_flags(tdir_, res, self.invalid)
        if not task_dir:
            run.update(model_family=model_family(run["agent"], model, "harbor"), **flags)
            if not self.expert.add(tdir_, res, run, rewards):
                print(f"skip {tdir_}: expert task not found in {self.expert.expert_dir}", file=sys.stderr)
            return
        tid = os.path.basename(task_dir)
        run = {"task_id": tid, **run}
        det_p = os.path.join(tdir_, "verifier", "details.json")
        details = json.load(open(det_p)) if os.path.isfile(det_p) else None
        rewards, details = self._regrade(tdir_, tid, run, rewards, details)
        traj = os.path.join(tdir_, "agent", "trajectory.json")
        if os.path.isfile(traj):
            run["steps"], run["curve"], run["curve_note"] = self._replay(task_dir, load_trajectory(traj), rewards["reward"])
        else:
            steps = []
            orc = os.path.join(tdir_, "agent", "oracle.txt")
            if os.path.isfile(orc):
                steps.append({"kind": "tool_call", "tool": "solution/solve.sh", "args": "bash solution/solve.sh"})
                steps.append({"kind": "observation", "tool": "solution/solve.sh", "output": clip(open(orc).read(), MAX_OUT)})
            steps.append(self._verifier_step(rewards["reward"], 0.0))
            run.update(steps=steps, curve="terminal", curve_note="Harbor reports a terminal reward only, and this "
                                                                 "trial has no agent trajectory to replay.")
        run.update(flags)
        self._finish(run, rewards, details)

    def _regrade(self, trial: str, tid: str, run: dict, rewards: dict, details: dict | None) -> tuple:
        """Core trial whose task is in a --regrade-tasks dir: grade the stored workspace with that task's grader."""
        rdir = next((os.path.join(d, tid) for d in self.regrade_dirs if os.path.isdir(os.path.join(d, tid))), None)
        art = os.path.join(trial, "artifacts", "workspace", "monitoring")
        if not rdir or not os.path.isdir(art):
            return rewards, details
        try:
            keys, det = grade_with(rdir, renamed_tree(art))
        except Exception as e:  # noqa: BLE001 - keep Harbor's result
            print(f"re-grade failed for {trial}: {e}", file=sys.stderr)
            return rewards, details
        kept = ("reward", "solved", "progress", "preservation")
        run.update(reward_original=rewards.get("reward", 0.0), regraded=task_release(rdir),
                   keys_original={k: rewards[k] for k in kept if k in rewards})
        return keys, det

    @staticmethod
    def _verifier_step(reward: float, cum: float) -> dict:
        return {"kind": "observation", "tool": "verifier", "output": f"Harbor verifier: reward {reward:.4f}",
                "r": round(reward - cum, 6), "cum": round(reward, 6)}

    def _replay(self, task_dir: str, traj: dict, harbor_reward: float) -> tuple[list, str, str]:
        tid = os.path.basename(task_dir)
        cat = self.catalog(tid)
        ws = Workspace(task_dir, self.grader(task_dir) if self.replay else None)
        steps, cum, n_edits, lossy, pending = [], 0.0, 0, False, {}
        keys = {"progress": 0.0}
        try:
            if self.replay:
                keys, _ = ws.grade()
            phi0 = keys.get("progress", 0.0)
            for ev in iter_events(traj):
                if ev[0] == "think":
                    steps.append({"kind": "think", "text": clip(ev[1], MAX_THINK)})
                    continue
                if ev[0] == "call":
                    _, cid, name, args = ev
                    ops, unknown = call_ops(name, args)
                    diffs, ok_all = [], True
                    for op in ops:
                        d, ok = ws.apply(op)
                        diffs.append(d)
                        ok_all = ok_all and ok
                    lossy = lossy or unknown or not ok_all
                    n_edits += len(ops)
                    call = {"kind": "tool_call", "tool": name, "args": args_summary(name, args)}
                    if any(diffs):
                        call["diff"] = clip("".join(diffs), MAX_DIFF)
                    steps.append(call)
                    pending[cid] = bool(ops)
                    continue
                _, cid, text = ev
                obs = {"kind": "observation", "tool": next((s["tool"] for s in reversed(steps)
                                                            if s["kind"] == "tool_call"), ""),
                       "output": clip(text, MAX_OUT)}
                if pending.pop(cid, False) and self.replay:
                    keys, det = ws.grade()
                    new = 0.4 * (keys.get("progress", 0.0) - phi0)
                    obs.update(r=round(new - cum, 6), phi=keys.get("progress"), _checks=cat.states(det))
                    cum = new
                obs["cum"] = round(cum, 6)
                steps.append(obs)
        finally:
            ws.close()
        steps.append(self._verifier_step(harbor_reward, cum))
        if not self.replay or n_edits == 0:
            return steps, "terminal", ("Harbor reports a terminal reward only; no file edits could be recovered from "
                                       "the trajectory, so there is no per-step curve.")
        final = 0.4 * keys.get("progress", 0.0) + 0.6 * keys.get("outcome", 0.0)
        if lossy or abs(final - harbor_reward) > 1e-4:
            return steps, "replayed-approx", (
                f"Per-step curve replays {n_edits} recovered file edits through the grader (0.4 × progress); some "
                f"edits could not be recovered (replayed end state {final:.3f} vs Harbor {harbor_reward:.3f}). "
                "The last step is Harbor's verifier reward.")
        return steps, "replayed", (f"Per-step curve replays {n_edits} file edits through the grader "
                                   "(0.4 × progress per step, +0.6 × outcome at the end); the replayed end state "
                                   "matches Harbor's verifier reward.")

    # ------------------------------------------------------------------ output
    def tasks_index(self) -> dict:
        out = {}
        for tid in sorted({r["task_id"] for r in self.runs if r["tier"] == "core"}):
            tdir = os.path.join(self.tasks_dir, tid)
            with open(os.path.join(tdir, "instruction.md")) as fh:
                instr = re.sub(r"<!--.*?-->\s*", "", fh.read(), flags=re.S).strip()
            cat = self.catalog(tid)
            wf, tier = meta_of(tid)
            out[tid] = {"workflow": wf, "tier": "core", "difficulty": tier, "instruction": instr,
                        "requirements": cat.requirements(), "checks": cat.export()}
        if self.expert:
            out.update(self.expert.tasks_index())
        return out

    def dump(self, out_path: str, budget: float = 4.5e6) -> dict:
        if self.expert:
            self.runs += self.expert.finish()
        tasks = self.tasks_index()
        for r in self.runs:
            if r["tier"] != "core":
                continue
            order = [c["id"] for c in tasks[r["task_id"]]["checks"]]
            enc = lambda st: "".join("1" if st.get(c) else ("0" if c in st else "-") for c in order)  # noqa: E731
            prev = None
            for s in r["steps"]:
                st = s.pop("_checks", None)
                if st is not None:
                    e = enc(st)
                    if e != prev:
                        s["checks"] = e
                        prev = e
            final = r.pop("_final")
            r["final_checks"] = enc(final) if final else prev
        for r in self.runs:
            for i, s in enumerate(r["steps"]):
                s["i"] = i
        self.runs.sort(key=lambda r: (r["task_id"], r["source"], r["agent"], r.get("trial", "")))
        for i, r in enumerate(self.runs):
            r["id"] = f"r{i}"
        tiers = {k: dict(v) for k, v in TIERS.items()}
        tiers["expert"].update(leaderboard_tasks=leaderboard_tasks(os.path.join(ROOT, "registry.json")), curves=CURVES)
        data = {"version": 2, "tiers": tiers, "tasks": tasks, "runs": self.runs}
        expert = [r for r in self.runs if r["tier"] == "expert"]
        fit(data, expert, budget, render, len(LEVELS))
        for r in expert:
            r.pop("_raw", None)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w") as fh:
            fh.write(redact(json.dumps(data, separators=(",", ":"))))  # never publish keys or local paths
        return data


def expand(paths: list[str], pattern: str) -> list[str]:
    out = []
    for p in paths:
        out += sorted(glob.glob(os.path.join(p, pattern))) if os.path.isdir(p) else [p]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", nargs="*", default=[], help="trace JSON files or dirs")
    ap.add_argument("--harbor", nargs="*", default=[], help="Harbor job dirs (searched recursively for trials)")
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--expert-tasks-dir", default=os.path.join(ROOT, "dist", "v02-candidates", "tasks"))
    ap.add_argument("--regrade-tasks", nargs="+", default=[], metavar="DIR",
                    help="re-grade stored workspaces of tasks found in DIR with DIR/<task>/tests/grade.py")
    ap.add_argument("--invalid", help="file of `<trial path substring> <reason>` lines to flag as invalid")
    ap.add_argument("--rename-map", help="JSON {\"map\": {old: new}} of host names renamed in the tasks since the runs")
    ap.add_argument("--expert-curves", choices=["auto", "off"], default="auto",
                    help="auto: grade up to 9 intermediate states of exactly replayable expert runs; off: final only")
    ap.add_argument("--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) // 2)),
                    help="parallel expert grades")
    ap.add_argument("--budget-mb", type=float, default=4.5, help="shorten expert step texts until runs.json fits")
    ap.add_argument("--out", default=os.path.join(ROOT, "space", "data", "runs.json"))
    ap.add_argument("--no-replay", action="store_true", help="do not grade Harbor trajectories step by step")
    a = ap.parse_args()
    t0 = time.time()
    if a.rename_map:
        print(f"reading stored runs through {load_renames(a.rename_map)} renamed host names", file=sys.stderr)
    expert = ExpertRuns(a.expert_tasks_dir, a.regrade_tasks, a.jobs, a.expert_curves == "auto",
                        leaderboard_tasks(os.path.join(ROOT, "registry.json")), ROOT)
    ex = Exporter(a.tasks_dir, replay=not a.no_replay, expert=expert, regrade_dirs=a.regrade_tasks,
                  invalid=load_invalid(a.invalid))
    for p in expand(a.traces, "*.json"):
        ex.add_trace(p)
    for p in a.harbor:
        ex.add_harbor(p)
    data = ex.dump(a.out, a.budget_mb * 1e6)
    size = os.path.getsize(a.out)
    print(f"wrote {a.out}: {len(data['runs'])} runs over {len(data['tasks'])} tasks, {size / 1e6:.2f} MB, "
          f"{time.time() - t0:.0f} s")
    for r in data["runs"]:
        extra = (f" (Harbor {r['reward_original']:.4f})" if "reward_original" in r else "") + \
            (f"  INVALID: {r['invalid']}" if r.get("invalid") else "")
        print(f"  {r['task_id']:40s} {r['agent']:32s} {r['curve']:16s} reward={r['reward']:.4f}{extra}")
    if size > 5e6:
        print("warning: runs.json is over 5 MB; export fewer runs or lower the clip limits", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
