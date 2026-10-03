"""Alertmanager 0.28 matcher strings, parsed the way the config loader does.

A `matchers:` entry is one string that may hold several comma-separated matchers, with or without the
surrounding braces (`'alertname="X"'`, `'alertname="X",severity="page"'`, `'{alertname="X"}'`,
`'alertname=X'`). Alertmanager 0.28 runs in its default fallback mode: both the classic parser
(`pkg/labels.ParseMatchers`) and the UTF-8 parser (`matcher/parse`) read the string; when the classic parser
accepts it, its result is the one used (it wins any disagreement); otherwise the UTF-8 parser's result is used;
the string is rejected only when both reject it. Regexes are anchored (`^(?:re)$`).

`parse(s)` returns [(name, op, value)] or raises Unsupported for strings Alertmanager itself rejects.
"""

from __future__ import annotations

import re

from .amcheck import Unsupported  # noqa: F401  (the simulators catch this one)


# ---------------------------------------------------------------------------- classic parser (pkg/labels/parse.go)

_CLASSIC = re.compile(r"^\s*([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(=~|=|!=|!~)\s*(.*?)\s*$", re.S)


def _classic_tokens(s: str) -> list[str]:
    if s.startswith("{"):
        s = s[1:]
    if s.endswith("}"):
        s = s[:-1]
    tokens, tok, inside, escaped = [], [], False, False
    for ch in s:
        if ch == "," and not inside:
            tokens.append("".join(tok))
            tok = []
            continue
        if ch == '"':
            if not escaped:
                inside = not inside
            else:
                escaped = False
        elif ch == "\\":
            escaped = not escaped
        else:
            escaped = False
        tok.append(ch)
    last = "".join(tok).strip()
    if last:
        tokens.append(last)
    return tokens


def _classic_one(tok: str) -> tuple[str, str, str]:
    m = _CLASSIC.match(tok)
    if not m:
        raise Unsupported(f"bad matcher format: {tok!r}")
    raw, quoted = m.group(3), False
    if raw.startswith('"'):
        raw, quoted = raw[1:], True
    out, escaped, n = [], False, len(raw)
    for i, ch in enumerate(raw):
        if escaped:
            escaped = False
            if ch == "n":
                out.append("\n")
            elif ch in '"\\':
                out.append(ch)
            else:
                out.append("\\" + ch)   # a spurious escape keeps its backslash
            continue
        if ch == "\\":
            if i < n - 1:
                escaped = True
                continue
            out.append("\\")
        elif ch == '"':
            if not quoted or i < n - 1:
                raise Unsupported(f"unescaped double quote in {tok!r}")
            quoted = False
        else:
            out.append(ch)
    if quoted:
        raise Unsupported(f"missing trailing quote in {tok!r}")
    return m.group(1), m.group(2), "".join(out)


def parse_classic(s: str) -> list[tuple[str, str, str]]:
    return [_classic_one(t) for t in _classic_tokens(s)]


# ---------------------------------------------------------------------------- UTF-8 parser (matcher/parse)

_RESERVED = set("{}!=~,\\\"'`")
_OPS = ("=~", "!=", "!~", "=")
_ESC = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'"}


def _go_unquote(body: str) -> str:
    """strconv.Unquote of a double-quoted Go string body."""
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c == "\n":
            raise Unsupported("newline in quoted string")
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(body):
            raise Unsupported("trailing backslash")
        e = body[i + 1]
        if e in _ESC:
            out.append(_ESC[e])
            i += 2
        elif e in "xuU":
            k = {"x": 2, "u": 4, "U": 8}[e]
            h = body[i + 2:i + 2 + k]
            if len(h) != k or not all(x in "0123456789abcdefABCDEF" for x in h):
                raise Unsupported(f"bad \\{e} escape")
            out.append(chr(int(h, 16)))
            i += 2 + k
        elif e in "01234567":
            o = body[i + 1:i + 4]
            if len(o) != 3 or not all(x in "01234567" for x in o) or int(o, 8) > 255:
                raise Unsupported("bad octal escape")
            out.append(chr(int(o, 8)))
            i += 4
        else:
            raise Unsupported(f"unknown escape \\{e}")
    return "".join(out)


def _lex(s: str) -> list[tuple[str, str]]:
    toks, i = [], 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "{},":
            toks.append((c, c))
            i += 1
        elif c in "=!":
            op = next((o for o in _OPS if s.startswith(o, i)), None)
            if op is None:
                raise Unsupported(f"bad operator at {i}")
            toks.append(("op", op))
            i += len(op)
        elif c == '"':
            j, esc = i + 1, False
            while j < len(s) and (esc or s[j] != '"'):
                esc = (s[j] == "\\") and not esc
                j += 1
            if j >= len(s):
                raise Unsupported("unterminated quoted string")
            toks.append(("str", _go_unquote(s[i + 1:j])))
            i = j + 1
        elif c in _RESERVED:
            raise Unsupported(f"unexpected {c!r}")
        else:
            j = i
            while j < len(s) and not s[j].isspace() and s[j] not in _RESERVED:
                j += 1
            toks.append(("str", s[i:j]))
            i = j
    return toks


def parse_utf8(s: str) -> list[tuple[str, str, str]]:
    toks = _lex(s)
    if not toks:
        return []
    braced = toks[0][0] == "{"
    if braced:
        if toks[-1][0] != "}":
            raise Unsupported("missing closing brace")
        toks = toks[1:-1]
    elif any(t[0] in "{}" for t in toks):
        raise Unsupported("unbalanced braces")
    out, i = [], 0
    while i < len(toks):
        if len(toks) - i < 3:
            raise Unsupported("incomplete matcher")
        (k1, name), (k2, op), (k3, val) = toks[i], toks[i + 1], toks[i + 2]
        if k1 != "str" or k2 != "op" or k3 != "str" or name == "":
            raise Unsupported("expected name, operator, value")
        out.append((name, op, val))
        i += 3
        if i < len(toks):
            if toks[i][0] != ",":
                raise Unsupported("expected a comma")
            i += 1
    return out


# ---------------------------------------------------------------------------- the config loader's choice

def parse(s) -> list[tuple[str, str, str]]:
    if not isinstance(s, str):
        raise Unsupported(f"matcher {s!r} is not a string")
    try:
        out = parse_classic(s)
    except Unsupported:
        out = parse_utf8(s)
    for _, op, val in out:
        if op in ("=~", "!~"):
            compile_value(op, val)   # an invalid regex is a config error in Alertmanager too
    return out


def compile_value(op: str, value: str):
    if op in ("=~", "!~"):
        try:
            rx = re.compile(f"^(?:{value})$")
        except re.error as e:
            raise Unsupported(f"bad regex {value!r}") from e
        return (lambda v: bool(rx.match(v))) if op == "=~" else (lambda v: not rx.match(v))
    return (lambda v: v == value) if op == "=" else (lambda v: v != value)


def compile_list(ms) -> list[tuple[str, object]]:
    """A `matchers:` list (each entry may hold several matchers) as [(label, predicate)]."""
    if ms is None:
        return []
    if not isinstance(ms, list):
        raise Unsupported("matchers must be a list")
    out = []
    for m in ms:
        for name, op, val in parse(m):
            out.append((name, compile_value(op, val)))
    return out
