"""Delivered notifications for the expert tier: inhibition, route walk with group_by, notification sets.

- `inhibited(am, alerts)`: Alertmanager 0.28 inhibition over the alerts firing together. A target matching
  a rule's target side is muted by a firing source that matches the source side and agrees on `equal`,
  except that when the target also matches the source side, sources that match the target side are skipped
  (Alertmanager's two-sided exclusion: two alerts that match both sides never mute each other). Inhibited
  alerts still act as sources.
- `walk(am, labels)`: the matching leaf routes (first match wins unless `continue`), with the effective
  receiver and group_by. A child's unset or empty `group_by` inherits the parent's; `'...'` groups on every
  label.
- `notifications(am, path, text, snapshots)`: distinct (receiver, route, group key) over snapshot times,
  after inhibition. Receivers come from amtool; a route walk that disagrees with amtool fails closed.

Timers (group_wait, group_interval, repeat_interval) are out of scope: a notification is a group that has
at least one delivered alert at some snapshot.
"""

from __future__ import annotations

from .amcheck import Unsupported, resolve
from .matchers import compile_list, compile_value

ALL = "..."


def _parse(ms, legacy: dict | None, legacy_re: dict | None) -> list:
    """`matchers` strings (Alertmanager's own grammar, see matchers.py) plus the legacy `match`/`match_re`."""
    out = compile_list(ms)
    for d, op in ((legacy, "="), (legacy_re, "=~")):
        if d is None:
            continue
        if not isinstance(d, dict):
            raise Unsupported("match must be a mapping")
        for k, v in d.items():
            out.append((str(k), compile_value(op, str(v))))
    return out


def _ok(ms: list, labels: dict) -> bool:
    return all(fn(labels.get(n, "")) for n, fn in ms)


# ---------------------------------------------------------------------------- inhibition

def _rules(am: dict) -> list:
    out = []
    for r in am.get("inhibit_rules") or []:
        if not isinstance(r, dict):
            raise Unsupported("inhibit rule is not a mapping")
        src = _parse(r.get("source_matchers"), r.get("source_match"), r.get("source_match_re"))
        tgt = _parse(r.get("target_matchers"), r.get("target_match"), r.get("target_match_re"))
        eq = r.get("equal") or []
        if not isinstance(eq, list):
            raise Unsupported("equal must be a list")
        out.append((src, tgt, [str(e) for e in eq]))
    return out


def inhibited(am: dict, alerts: list[dict]) -> list[bool] | None:
    """For each alert (label dict), whether the others firing with it mute it. None if unsupported."""
    try:
        rules = _rules(am)
    except (Unsupported, AttributeError):
        return None
    out = []
    for t in alerts:
        muted = False
        for src, tgt, eq in rules:
            if not _ok(tgt, t):
                continue
            two_sided = _ok(src, t)
            for s in alerts:
                if s is t or not _ok(src, s):
                    continue
                if any(s.get(e, "") != t.get(e, "") for e in eq):
                    continue
                if two_sided and _ok(tgt, s):
                    continue
                muted = True
                break
            if muted:
                break
        out.append(muted)
    return out


def delivered(am: dict, alerts: list[dict]) -> list[dict] | None:
    mask = inhibited(am, alerts)
    if mask is None:
        return None
    return [a for a, m in zip(alerts, mask) if not m]


# ---------------------------------------------------------------------------- routing

def walk(am: dict, labels: dict) -> list[tuple[str, object, str]] | None:
    """[(receiver, group_by list or ALL, route path)] for the matching leaves; None if unsupported."""
    root = am.get("route")
    if not isinstance(root, dict):
        return None
    try:
        return _walk(root, labels, root.get("receiver"), root.get("group_by") or [], "0", True)
    except (Unsupported, AttributeError, TypeError):
        return None


def _walk(node: dict, labels: dict, recv, gb, path: str, is_root: bool) -> list | None:
    if not is_root:
        ms = _parse(node.get("matchers"), node.get("match"), node.get("match_re"))
        if not _ok(ms, labels):
            return None
    recv = node.get("receiver") or recv
    if node.get("group_by"):
        gb = node["group_by"]
    gb = ALL if ALL in (gb if isinstance(gb, list) else [gb]) else list(gb)
    found: list = []
    for i, child in enumerate(node.get("routes") or []):
        if not isinstance(child, dict):
            raise Unsupported("route is not a mapping")
        got = _walk(child, labels, recv, gb, f"{path}.{i}", False)
        if got is not None:
            found += got
            if not child.get("continue"):
                break
    if not found:
        found = [(str(recv), gb, path)]
    return found


def group_key(gb, labels: dict) -> tuple:
    if gb == ALL:
        return tuple(sorted(labels.items()))
    return tuple((k, labels.get(k, "")) for k in sorted(gb))


def notifications(am: dict, am_path: str, am_text: str, snapshots: list[list[dict]]) -> set | None:
    """Distinct (receiver, route path, group key, frozenset(services)) over snapshots; None if unsupported."""
    groups: dict[tuple, set] = {}
    for alerts in snapshots:
        live = delivered(am, alerts)
        if live is None:
            return None
        for a in live:
            leaves = walk(am, a)
            want = resolve(am_path, am_text, a)
            if leaves is None or want is None or sorted({r for r, _, _ in leaves}) != sorted(set(want)):
                return None
            for recv, gb, path in leaves:
                key = (recv, path, group_key(gb, a))
                groups.setdefault(key, set()).add(a.get("service", ""))
    return {(r, p, k, frozenset(s)) for (r, p, k), s in groups.items()}
