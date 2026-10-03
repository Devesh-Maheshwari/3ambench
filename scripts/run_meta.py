"""Per-run metadata for the replay export: model family, invalid-run flags, and the size budget of runs.json.

Model family groups the leaderboard: the agent harness by name ("my-agent" -> "My Agent"), except that
open-weight models form their own group whatever harness ran them, and scripted policies are "reference".

A run is flagged `invalid` (and left out of every leaderboard) when the result says nothing about the model:
the provider or the sandbox failed (rate limits, API or network errors, environment or verifier timeouts), the
grader crashed, the run's prompt is not the task's current instruction, or the owner listed it in an
`--invalid` file. A run that ended on the model's own account (agent timeout, context window, a refusal) stays
valid and carries the exception name in `exception`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

OPEN_WEIGHTS = re.compile(r"gpt-oss|llama|qwen|deepseek|mistral|mixtral|gemma|glm-|kimi|olmo|nemotron|phi-\d", re.I)
INFRA = re.compile(r"RateLimit|UsageLimit|InternalServer|Overloaded|ConnectionClosed|ResponseStalled|UnknownApi|"
                   r"ResourceNotFound|Authentication|ModelNotFound|NetworkConnection|APIConnection|APIError|"
                   r"ServiceUnavailable|SetupTimeout|EnvironmentStart|VerifierTimeout|BuildFailed|Healthcheck|"
                   r"^Timeout$")


def model_family(agent: str, model: str, source: str) -> str:
    name = agent.split(": ", 1)[-1]
    if source != "harbor" or not model or name.startswith(("oracle", "nop")):
        return "reference"
    if OPEN_WEIGHTS.search(model):
        return "open weights"
    return " ".join(w.capitalize() for w in re.split(r"[-_ ]+", name) if w) or "other"


def load_invalid(path: str | None) -> list[tuple[str, str]]:
    """`--invalid` file: one `<substring of the trial path>  <reason>` per line, # comments."""
    out = []
    if path:
        with open(path) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    pat, _, reason = line.partition(" ")
                    out.append((pat, reason.strip() or "listed as invalid by the owner"))
    return out


def harbor_flags(trial: str, res: dict, listed: list[tuple[str, str]]) -> dict:
    """{'invalid': reason} and/or {'exception': type} for one Harbor trial (empty when the run is clean)."""
    out: dict[str, str] = {}
    exc = (res.get("exception_info") or {}).get("exception_type")
    if exc:
        out["exception"] = exc
        if INFRA.search(exc):
            out["invalid"] = f"infrastructure error during the run ({exc})"
    if os.path.isfile(os.path.join(trial, "verifier", "grader_error.json")):
        out["invalid"] = "the grader crashed on this run"
    log = os.path.join(trial, "verifier", "checksum.log")
    if os.path.isfile(log) and os.path.getsize(log) > 0:
        out["invalid"] = "the verifier's checksum check failed (broken task image)"
    for pat, reason in listed:
        if pat in trial:
            out["invalid"] = reason
    return out


def public(run: dict) -> dict:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def input_mismatch(task_dir: str, workspace: str, trajectory: str) -> str | None:
    """Regrading an output cannot repair different inputs seen by the solver.

    Compare the saved instruction and evidence/policy files that tasks do not ask
    solvers to edit. Runbooks are mutable task outputs and cannot establish input
    provenance from the final workspace. A mismatch is excluded from calibration.
    """
    from replay_atif import call_ops, iter_events, load_trajectory
    from replay_expert import instruction_seen, strip_comments
    task = Path(task_dir)
    if not os.path.isfile(trajectory):
        return "the task instruction cannot be verified: trajectory missing"
    traj = load_trajectory(trajectory)
    if instruction_seen(traj, strip_comments((task / "instruction.md").read_text())) is not True:
        return "the saved instruction differs or cannot be verified; re-run the current task"
    base = task / "environment" / "workspace" / "monitoring"
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if str(rel) != "README.md" and rel.parts[0] not in ("docs", "incidents", "slo", "teams", "bin"):
            continue
        saved = Path(workspace) / rel
        if saved.is_file() and saved.read_bytes() != path.read_bytes():
            # A final workspace is an output, not an initial snapshot. Preserve
            # model edits when anchored replacements reproduce it exactly. Do
            # not execute shell commands or infer provenance from full rewrites.
            text = path.read_text()
            edited = False
            for event in iter_events(traj):
                if event[0] != "call":
                    continue
                ops, _ = call_ops(event[2], event[3])
                for op in ops:
                    if op[0] == "replace" and op[1] == str(rel) and op[2] and op[2] in text:
                        text = text.replace(op[2], op[3]) if op[4] else text.replace(op[2], op[3], 1)
                        edited = True
            if edited and text == saved.read_text():
                continue
        if not saved.is_file() or saved.read_bytes() != path.read_bytes():
            return f"task evidence differs from the saved workspace ({rel}); re-run the current task"
    return None


def fit(data: dict, runs: list[dict], budget: float, render, levels: int) -> int:
    """Shorten the texts of `runs` (largest first, one level at a time) until runs.json fits `budget` bytes."""
    def size() -> int:
        return len(json.dumps({**data, "runs": [public(r) for r in data["runs"]]}, separators=(",", ":")))

    level = {id(r): 0 for r in runs}
    total = size()
    while total > budget:
        cands = sorted((r for r in runs if level[id(r)] < levels - 1),
                       key=lambda r: -len(json.dumps(r["steps"], separators=(",", ":"))))
        if not cands:
            break
        for r in cands[:max(1, len(cands) // 4)]:
            level[id(r)] += 1
            render(r, level[id(r)])
        total = size()
    return total
