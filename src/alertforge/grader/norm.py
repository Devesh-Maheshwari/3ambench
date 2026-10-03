"""Preservation fast path for the expert tier: a rule's effective, normalised form.

The effective rule is the rule merged with its group: rule labels over group labels (the rule wins), plus the
group's interval, query_offset and limit. The expression is compared after collapsing whitespace; when that
differs, both sides go through `promtool --experimental promql format` (which canonicalises spacing, quoting,
durations and matcher order) with the label lists of by/without/on/ignoring/group_* sorted afterwards,
because the formatter keeps their order.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

from .loader import collapse, dur_s
from .promrun import tool

_FMT_CACHE: dict[str, str | None] = {}
_LIST = re.compile(r"\b(by|without|on|ignoring|group_left|group_right)\s*\(([^()]*)\)")


def _sort_lists(expr: str) -> str:
    def fix(m):
        items = [x.strip() for x in m.group(2).split(",") if x.strip()]
        return f"{m.group(1)} ({', '.join(sorted(items))})"
    return _LIST.sub(fix, expr)


def fmt_expr(expr: str) -> str | None:
    key = collapse(expr)
    if key not in _FMT_CACHE:
        try:
            r = subprocess.run([tool("promtool"), "--experimental", "promql", "format", key],
                               capture_output=True, text=True, timeout=20)
            _FMT_CACHE[key] = _sort_lists(collapse(r.stdout)) if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            _FMT_CACHE[key] = None
    return _FMT_CACHE[key]


def effective(rule: dict, group: dict) -> dict:
    kind = "alert" if "alert" in rule else "record"
    glabels = group.get("labels") if isinstance(group.get("labels"), dict) else {}
    rlabels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
    labels = {str(k): str(v) for k, v in {**glabels, **rlabels}.items()}
    ann = rule.get("annotations") if isinstance(rule.get("annotations"), dict) else {}
    return {"k": kind, "n": str(rule.get(kind)), "expr": collapse(rule.get("expr", "")),
            "for": dur_s(rule.get("for")), "kff": dur_s(rule.get("keep_firing_for")), "labels": labels,
            "ann": {str(k): str(v) for k, v in ann.items()},
            "g": [dur_s(group.get("interval")) or 60, dur_s(group.get("query_offset")), group.get("limit") or 0]}


def digest(eff: dict, expr_form: str | None = None) -> str:
    d = dict(eff)
    d["expr"] = expr_form if expr_form is not None else eff["expr"]
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def same_rule(eff: dict, want: dict) -> bool:
    """want: {"raw": digest over the collapsed text, "fmt": digest over the formatted text}."""
    if digest(eff) == want["raw"]:
        return True
    f = fmt_expr(eff["expr"])
    return f is not None and digest(eff, f) == want.get("fmt")


def preserved(rule: dict, group: dict) -> dict:
    """The effective rule as preservation compares it. An alert's annotations are left out: they change what
    the notification says, not when it fires or where it goes (a description added to an untouched alert
    is not a regression)."""
    eff = effective(rule, group)
    if eff["k"] == "alert":
        eff["ann"] = {}
    return eff


def pristine_entry(rule: dict, group: dict) -> dict:
    eff = preserved(rule, group)
    f = fmt_expr(eff["expr"])
    out = {"kind": eff["k"], "name": eff["n"], "raw": digest(eff), "fmt": digest(eff, f) if f else None}
    if eff["k"] == "alert":   # the pristine form, replayed when the text changed (replay.py)
        out["form"] = {"expr": eff["expr"], "for": eff["for"], "kff": eff["kff"], "labels": eff["labels"]}
    return out


def core_eff(rule: dict, group: dict, keep_labels: tuple = ()) -> dict:
    """The effective rule reduced to its behaviour: expression, `for`, `keep_firing_for`, group fields and
    only the named labels (annotations and other labels may change without changing what it does)."""
    eff = effective(rule, group)
    eff["labels"] = {k: v for k, v in eff["labels"].items() if k in keep_labels}
    eff["ann"] = {}
    return eff


def core_entry(rule: dict, group: dict, keep_labels: tuple = ()) -> dict:
    eff = core_eff(rule, group, keep_labels)
    f = fmt_expr(eff["expr"])
    return {"raw": digest(eff), "fmt": digest(eff, f) if f else None}
