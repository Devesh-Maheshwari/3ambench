"""Shared helpers for the replay exporters: text clipping, tool-call summaries, and a scratch copy of a task's
workspace that recovered edits are applied to (scripts/export_runs.py for the core tier, scripts/export_expert.py
for the expert tier).
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import tempfile

from replay_atif import apply_v4a

MAX_OUT, MAX_DIFF, MAX_THINK, MAX_ARGS = 1200, 2400, 1200, 300


def clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n} more chars]"


def args_summary(tool: str, a: dict) -> str:
    if tool in ("write_file", "Write", "create_file"):
        return f"{a.get('path') or a.get('file_path')} ({len(str(a.get('content', '')))} bytes)"
    for k in ("path", "file_path", "command", "cmd", "keystrokes", "labels", "input"):
        if k in a and a[k] not in (None, ""):
            v = a[k]
            head = f"{a.get('path') or a.get('file_path')}  " if k not in ("path", "file_path") and (
                a.get("path") or a.get("file_path")) else ""
            return clip(head + (json.dumps(v) if isinstance(v, (dict, list)) else str(v)), MAX_ARGS)
    return clip(json.dumps(a), MAX_ARGS) if a else ""


class Workspace:
    """A scratch copy of a task's initial workspace: applies edits, returns diffs, grades on demand."""

    def __init__(self, task_dir: str, grader=None):
        self.root = tempfile.mkdtemp(prefix="af-replay-")
        self.ws = os.path.join(self.root, "monitoring")
        shutil.copytree(os.path.join(task_dir, "environment", "workspace", "monitoring"), self.ws, symlinks=True)
        self.grader = grader

    def read(self, rel: str) -> str | None:
        p = os.path.join(self.ws, rel)
        return open(p).read() if os.path.isfile(p) else None

    def files(self) -> dict:
        out = {}
        for dp, _, fns in os.walk(self.ws):
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), self.ws)
                out[rel] = self.read(rel)
        return out

    def apply(self, op: tuple) -> tuple[str, bool]:
        """Apply one op; returns (unified diff, ok)."""
        if op[0] == "patch":
            ops, ok = apply_v4a(self.files(), op[1])
            diffs = [self.apply(o) for o in ops]
            return "".join(d for d, _ in diffs), ok and all(k for _, k in diffs)
        rel = op[1]
        old = self.read(rel)
        if op[0] == "write":
            new = op[2]
        elif op[0] == "replace":
            _, _, a, b, every = op
            if old is None or (a not in old):
                return "", False
            new = old.replace(a, b) if every else old.replace(a, b, 1)
        elif op[0] == "delete":
            if old is not None:
                os.remove(os.path.join(self.ws, rel))
            return self._diff(rel, old, ""), True
        else:
            return "", False
        p = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(new)
        return self._diff(rel, old, new), True

    @staticmethod
    def _diff(rel: str, old: str | None, new: str) -> str:
        return "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True),
                                            f"a/{rel}" if old is not None else "/dev/null", f"b/{rel}", n=2))

    def grade(self) -> tuple[dict, dict]:
        return self.grader.grade(self.ws)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
