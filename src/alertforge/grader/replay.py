"""Behavioural preservation, second step (G8): an untouched alert whose text changed is replayed.

The fast path (norm.py) proves identity up to promtool formatting. When it fails for an untouched alert, the
pristine form and the current form run side by side over every hidden scenario and the task's exercise scenarios
(each service down, gone, erroring, saturated), in one extra promtool call. The alert is compared as a whole:
every pristine definition (a warning and a critical copy are two) against every current one. It passes when:
- both sides have the same set of `for`, `keep_firing_for` and expression signature: the expressions keep the
  pristine's strings, numbers, durations and functions (operands may be reordered, a comparison flipped, a `by`
  list written as `without`, an identical copy added); a changed threshold, window, aggregation or debounce is
  never replayed into a pass;
- the two forms produce the same firing `ALERTS` series, labels included, at every minute of every scenario, and
  the pristine form fires somewhere (a replay where nothing fires proves nothing). Labels are compared on what
  fires: a static label equal to what the expression already yields on every series it returns (`service` on a
  `sum by (service)` alert) changes nothing; one that changes or adds a value does.
"""

from __future__ import annotations

import re

from .loader import RuleFile
from .norm import fmt_expr

_STR = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_NUM = re.compile(r"(?<![\w.])(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:ms|[smhdwy])?|\.\d+)(?![\w.])")
_LISTS = re.compile(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)")
_IDENT = re.compile(r"[A-Za-z_:][\w:]*")
_FREE = {"by", "without", "on", "ignoring", "group_left", "group_right", "bool", "and", "or", "unless"}


def signature(expr: str) -> tuple | None:
    """What an equivalent rewrite keeps: string literals, numbers and durations, metric and function names.
    Label lists of by/without/on/ignoring and the set operators are left out (their rewrites are replayed)."""
    f = fmt_expr(expr)
    if f is None:
        return None
    strings = sorted(_STR.findall(f))
    rest = _LISTS.sub(" ", _STR.sub(" ", f))
    rest = re.sub(r"\{[^{}]*\}", " ", rest)
    nums = sorted(_NUM.findall(rest))
    idents = sorted(x for x in _IDENT.findall(_NUM.sub(" ", rest)) if x not in _FREE)
    return strings, nums, idents


def _dur(sec) -> str | None:
    return f"{int(sec)}s" if isinstance(sec, (int, float)) and sec else (str(sec) if sec else None)


def _rule(name: str, form: dict) -> dict:
    r = {"alert": name, "expr": form["expr"]}
    if _dur(form.get("for")):
        r["for"] = _dur(form["for"])
    if _dur(form.get("kff")):
        r["keep_firing_for"] = _dur(form["kff"])
    if form.get("labels"):
        r["labels"] = dict(form["labels"])
    return r


def _sig(form: dict):
    sig = signature(form["expr"])
    return form.get("for"), form.get("kff"), None if sig is None else tuple(tuple(x) for x in sig)


def candidates(pairs: list[tuple[object, list, list]]) -> list[tuple[object, list, list]]:
    """(item, pristine forms, current forms) worth replaying: an alert as a whole, every definition of it on each
    side. The two sides have the same set of (`for`, `keep_firing_for`, expression signature): a copy may be added
    (two identical definitions fire as one alert), dropped or rewritten, but no threshold, window, function or
    debounce may change. The labels are compared by the replay, on the series that fire."""
    out = []
    for item, ps, cs in pairs:
        sp, sc = {_sig(p) for p in ps}, {_sig(c) for c in cs}
        if not ps or not cs or any(x[2] is None for x in sp | sc) or sp != sc:
            continue
        out.append((item, ps, cs))
    return out


def _end(g: dict) -> int:
    return int(g.get("minutes") or max([p["t"] for p in g.get("probes", [])] or [60]))


def equivalent(pairs: list[tuple[object, list, list]], groups: list[dict], files: list[RuleFile], workdir: str,
               masks: dict[str, list[str]]) -> set:
    """Items whose pristine and current definitions behave the same on every scenario (see the module doc)."""
    from . import snap
    pairs = candidates(pairs)
    if not pairs or not groups:
        return set()
    rules = []
    for i, (_, ps, cs) in enumerate(pairs):
        rules += [_rule(f"AfReplayP{i}", p) for p in ps] + [_rule(f"AfReplayC{i}", c) for c in cs]
    extra = RuleFile("rules/zz-af-replay.yml", [{"name": "af-replay", "rules": rules}])
    run, pre = [], {}
    for k, g in enumerate(groups):
        t = _end(g)
        probes = []
        for i in range(len(pairs)):
            pa = f'ALERTS{{alertname="AfReplayP{i}",alertstate="firing"}}'
            ca = f'ALERTS{{alertname="AfReplayC{i}",alertstate="firing"}}'
            diff = f"(count(({pa} unless ignoring (alertname) {ca}) or ({ca} unless ignoring (alertname) {pa})) or vector(0))"
            fired = f"(count({pa}) or vector(0))"
            probes += [{"type": "count", "id": f"rp{k}.d{i}", "expr": f"max_over_time({diff}[{t + 1}m:1m])", "t": t, "expect": 0},
                       {"type": "count", "id": f"rp{k}.f{i}", "expr": f"max_over_time({fired}[{t + 1}m:1m])", "t": t, "expect": 0}]
        name = f"rp{k}|{g['name']}"
        run.append({"name": name, "input_series": g["input_series"], "probes": probes})
        if masks.get(g["name"]):
            pre[name] = masks[g["name"]]
    res, got_masks = snap.run_groups(run, list(files) + [extra], workdir, "rp", masks=pre)
    ok = set()
    for i, (item, _, _) in enumerate(pairs):
        same, fired = True, False
        for k, g in enumerate(run):
            r = res.get(g["name"])
            if r is None or {f"AfReplayP{i}", f"AfReplayC{i}"} & set(got_masks.get(g["name"], [])):
                same = False
                break
            same = same and r.get(f"rp{k}.d{i}") is True
            fired = fired or r.get(f"rp{k}.f{i}") is False
        if same and fired:
            ok.add(item)
    return ok
