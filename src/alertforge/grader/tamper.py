"""Expert-tier tamper checks on rule expressions: reading `ALERTS`, directly or through records.

A small PromQL lexer finds every vector selector (metric name plus matchers). Quoted strings are decoded the
way PromQL decodes them (Go escapes: `\\x41`, `\\u0041`, `\\101`, ...), and a quoted metric name inside the
braces (`{"ALERTS", ...}`) counts as the metric name. A selector reaches `ALERTS` or `ALERTS_FOR_STATE` if its
name is one of them, if a `__name__` matcher accepts either, or if it has no name and no `__name__` matcher at
all (a bare `{alertstate="pending"}` matches `ALERTS` series too). A recording rule is tainted if it reaches them
or reads a tainted record (to a fixed point), and an alert that does either is tamper. An expression the lexer
can't read is tamper. As a second opinion, an expression with a backslash or a quoted metric name is also
walked in the form `promtool promql format` prints, and is tamper if promtool can't format it.

One exemption: an alert whose only such selectors are `ALERTS{alertname="<itself>", alertstate="firing"}`, with
the name written out and equality matchers, and that reads no tainted record. That is the usual hysteresis
idiom; it can keep its own firing alert up but cannot turn pending into firing.
"""

from __future__ import annotations

import re

RESERVED = ("ALERTS", "ALERTS_FOR_STATE")
KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right", "bool", "and", "or", "unless",
            "offset", "atan2", "inf", "nan", "start", "end"}
_LEXEME = re.compile(r'\s+|#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`|[A-Za-z_:][A-Za-z0-9_:]*'
                     r'|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[a-z]*|=~|!~|!=|==|<=|>=|.', re.S)


class Unparsable(Exception):
    pass


def lex(expr: str) -> list[str]:
    toks = []
    for m in _LEXEME.finditer(str(expr)):
        t = m.group(0)
        if t.isspace() or t.startswith("#"):
            continue
        toks.append(t)
    return toks


_SIMPLE = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def go_unquote(tok: str) -> str:
    """A PromQL string literal decoded with Go's rules; Unparsable for an escape PromQL would reject."""
    q, body = tok[:1], tok[1:-1]
    if q == "`":
        return body
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = body[i + 1:i + 2]
        if e and (e in _SIMPLE or e in "'\""):
            out.append(_SIMPLE.get(e, e))
            i += 2
            continue
        width = {"x": 2, "u": 4, "U": 8}.get(e)
        digits = body[i + 2:i + 2 + width] if width else body[i + 1:i + 4]
        try:
            if width and re.fullmatch(f"[0-9a-fA-F]{{{width}}}", digits):
                out.append(chr(int(digits, 16)))
            elif not width and re.fullmatch("[0-7]{3}", digits) and int(digits, 8) <= 255:
                out.append(chr(int(digits, 8)))
            else:
                raise ValueError
        except (ValueError, OverflowError):
            raise Unparsable(f"bad escape in {tok!r}") from None
        i += 2 + width if width else 4
    return "".join(out)


def _unq(s: str) -> str:
    return go_unquote(s) if s[:1] in "\"'`" else s


def selectors(expr: str) -> list[tuple[str | None, list[tuple[str, str, str]]]]:
    """[(metric name or None, [(label, op, value)])] for every vector selector in expr."""
    toks = lex(expr)
    out = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t in ("by", "without", "on", "ignoring", "group_left", "group_right") and i + 1 < n and toks[i + 1] == "(":
            depth = 0
            while i < n:
                if toks[i] == "(":
                    depth += 1
                elif toks[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
            continue
        name = None
        if re.match(r"[A-Za-z_:]", t) and t.lower() not in KEYWORDS:
            if i + 1 < n and toks[i + 1] == "(":
                i += 1
                continue  # function or aggregation call
            name = t
            i += 1
            t = toks[i] if i < n else ""
        if t == "{":
            ms, inner, i = _matchers(toks, i)
            if inner is not None:
                if name is not None:
                    raise Unparsable("metric name given twice")
                out.append((inner, ms, False))
            else:
                out.append((name, ms, name is not None))
            continue
        if name is not None:
            out.append((name, [], True))
            continue
        i += 1
    return out


def _matchers(toks: list[str], i: int) -> tuple[list, str | None, int]:
    """Matchers inside one pair of braces, plus a quoted metric name written among them (or None)."""
    ms, inner, i = [], None, i + 1
    while i < len(toks) and toks[i] != "}":
        if toks[i] == ",":
            i += 1
            continue
        if toks[i][:1] in "\"'`" and i + 1 < len(toks) and toks[i + 1] in (",", "}"):
            if inner is not None:
                raise Unparsable("metric name given twice")
            inner = _unq(toks[i])
            i += 1
            continue
        if i + 2 >= len(toks) or toks[i + 1] not in ("=", "!=", "=~", "!~"):
            raise Unparsable("bad matcher")
        ms.append((_unq(toks[i]), toks[i + 1], _unq(toks[i + 2])))
        i += 3
    if i >= len(toks):
        raise Unparsable("unclosed selector")
    return ms, inner, i + 1


def _accepts(op: str, value: str, name: str) -> bool:
    if op == "=":
        return name == value
    if op == "!=":
        return name != value
    try:
        hit = re.fullmatch(value, name) is not None
    except re.error:
        return True  # unparsable regex: treat as reaching (fail closed)
    return hit if op == "=~" else not hit


def _names_reached(sel, candidates) -> set[str]:
    """The candidate metric names this selector can return. A selector with no name and no `__name__` matcher
    matches series of every name."""
    name, ms = sel[0], sel[1]
    got = set()
    for c in candidates:
        if name is not None and name != c:
            continue
        if all(_accepts(op, v, c) for lbl, op, v in ms if lbl == "__name__"):
            got.add(c)
    return got


_QUOTED_NAME = re.compile(r"""[{,]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`)\s*[,}]""")


def _parse(expr: str) -> list:
    """Selectors of expr. When it has a backslash or a quoted metric name, the form promtool prints is walked
    too (it decodes every escape and writes `{__name__="..."}`); if promtool can't format it, Unparsable."""
    sels = selectors(expr)
    if "\\" in expr or _QUOTED_NAME.search(expr):
        from .norm import fmt_expr
        f = fmt_expr(expr)
        if f is None:
            raise Unparsable("promtool can't format it")
        written = {(n, frozenset(ms)) for n, ms, lit in sels if lit}
        sels += [(n, ms, (n, frozenset(ms)) in written) for n, ms, _ in selectors(f)]
    return sels


def alerts_tamper(rules: list[dict]) -> list[str]:
    """Reasons for every alert that reads ALERTS/ALERTS_FOR_STATE, directly or via records."""
    records = {str(r["record"]): str(r.get("expr", "")) for r in rules if "record" in r}
    parsed: dict[int, list] = {}
    reasons = []
    for i, r in enumerate(rules):
        try:
            parsed[i] = _parse(str(r.get("expr", "")))
        except Unparsable:
            parsed[i] = None
            reasons.append(f"unparsable expression: {r.get('alert', r.get('record'))}")
    rec_idx = {str(r["record"]): i for i, r in enumerate(rules) if "record" in r}
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, i in rec_idx.items():
            if name in tainted or parsed[i] is None:
                continue
            for sel in parsed[i]:
                if _names_reached(sel, RESERVED) or _names_reached(sel, tainted):
                    tainted.add(name)
                    changed = True
                    break
    for i, r in enumerate(rules):
        if "alert" not in r or parsed[i] is None:
            continue
        own = str(r["alert"])
        for sel in parsed[i]:
            if _names_reached(sel, tainted & set(records)):
                reasons.append(f"alert {own} reads a record derived from ALERTS")
                break
            if _names_reached(sel, RESERVED):
                ms = {(lbl, op): v for lbl, op, v in sel[1]}
                exempt = (sel[0] == "ALERTS" and sel[2] and ms.get(("alertname", "=")) == own
                          and ms.get(("alertstate", "=")) == "firing"
                          and not any(lbl == "__name__" for lbl, _, _ in sel[1]))
                if not exempt:
                    reasons.append(f"alert {own} reads ALERTS")
                    break
    return reasons
