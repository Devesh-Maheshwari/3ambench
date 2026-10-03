"""A small YAML emitter for rule files and alertmanager.yml that keeps comments and per-generation style.

Dict keys named "_comment" (a string or a list of lines) are written as `# ...` lines above the mapping they
belong to and dropped from the data. `strip()` returns the data without them; `emit()` output is checked to
load back to exactly that data.
"""

from __future__ import annotations

import json
import re

import yaml

_PLAIN_BAD = re.compile(r"^[\s\-?:,\[\]{}#&*!|>'\"%@`]|: |\s#|[\s]$|^$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n"}


def strip(obj):
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k != "_comment"}
    if isinstance(obj, list):
        return [strip(v) for v in obj]
    return obj


def _scalar(v, quote_dur: bool = False) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    if quote_dur and re.fullmatch(r"\d+[smhdw]", s):
        return json.dumps(s)
    if (_PLAIN_BAD.search(s) or s.lower() in _RESERVED or re.fullmatch(r"[-+.\d][\d_.eE+-]*", s)
            or "\n" in s or "\t" in s or "\\" in s or not s.isascii()):
        return json.dumps(s, ensure_ascii=False)
    return s


class Style:
    """quote_dur: durations as "5m"; block_expr: `expr: |` for expressions longer than `block_at` chars;
    squote: single-quoted strings where that reads naturally (older files)."""

    def __init__(self, quote_dur=False, block_expr=False, block_at=70, flow_labels=False):
        self.quote_dur, self.block_expr, self.block_at, self.flow_labels = quote_dur, block_expr, block_at, flow_labels


def _comments(v, pad: str) -> list[str]:
    if not v:
        return []
    lines = [v] if isinstance(v, str) else list(v)
    return [f"{pad}# {ln}".rstrip() for ln in lines]


def _emit(obj, indent: int, style: Style, out: list[str], key_ctx: str = "") -> None:
    pad = " " * indent
    if isinstance(obj, dict):
        out += _comments(obj.get("_comment"), pad)
        for k, v in obj.items():
            if k == "_comment":
                continue
            _emit_kv(k, v, indent, style, out)
        return
    raise TypeError("top level must be a mapping")


def _flow_map(d: dict) -> str:
    return "{" + ", ".join(f"{k}: {_scalar(v)}" for k, v in d.items()) + "}"


def _emit_kv(k, v, indent: int, style: Style, out: list[str]) -> None:
    pad = " " * indent
    if isinstance(v, dict):
        if not v:
            out.append(f"{pad}{k}: {{}}")
            return
        if style.flow_labels and k in ("labels",) and all(not isinstance(x, (dict, list)) for x in v.values()):
            out.append(f"{pad}{k}: {_flow_map(v)}")
            return
        out.append(f"{pad}{k}:")
        _emit(v, indent + 2, style, out)
        return
    if isinstance(v, list):
        if not v:
            out.append(f"{pad}{k}: []")
            return
        if all(not isinstance(x, (dict, list)) for x in v) and k in ("group_by", "equal"):
            out.append(f"{pad}{k}: [{', '.join(_scalar(x) for x in v)}]")
            return
        out.append(f"{pad}{k}:")
        for item in v:
            if isinstance(item, dict):
                out += _comments(item.get("_comment"), pad)
                keys = [x for x in item if x != "_comment"]
                if not keys:
                    out.append(f"{pad}- {{}}")
                    continue
                first = []
                _emit_kv(keys[0], item[keys[0]], indent + 2, style, first)
                first[0] = f"{pad}- " + first[0][indent + 2:]
                out += first
                for kk in keys[1:]:
                    _emit_kv(kk, item[kk], indent + 2, style, out)
            else:
                out.append(f"{pad}- {_scalar(item)}")
        return
    if k == "expr" and isinstance(v, str) and style.block_expr and len(v) > style.block_at:
        out.append(f"{pad}{k}: |")
        for ln in _wrap_expr(v):
            out.append(f"{pad}  {ln}")
        return
    out.append(f"{pad}{k}: {_scalar(v, quote_dur=style.quote_dur and k in ('for', 'keep_firing_for', 'interval'))}")


def _wrap_expr(expr: str) -> list[str]:
    """Break a long binary expression before its top-level operators, the way people format PromQL."""
    if "\n" in expr:
        return expr.splitlines()
    parts, depth, cur, i = [], 0, "", 0
    ops = (" / ", " and ", " or ", " unless ", " > ", " < ")
    while i < len(expr):
        c = expr[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        hit = next((o for o in ops if depth == 0 and expr.startswith(o, i)), None)
        if hit in (" / ", " and ", " or ", " unless ") and cur.strip():
            parts.append(cur.rstrip())
            cur = hit.strip() + " "
            i += len(hit)
            continue
        cur += c
        i += 1
    parts.append(cur.rstrip())
    out = []
    for p in parts:
        if out and p.startswith(("> ", "< ")):
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def emit(doc: dict, style: Style | None = None, header: list[str] | None = None) -> str:
    style = style or Style()
    out = [f"# {h}".rstrip() for h in (header or [])]
    _emit(doc, 0, style, out)
    text = "\n".join(out) + "\n"
    back = yaml.safe_load(text)
    want = strip(doc)
    if style.block_expr:
        back = _norm_block(back)
        want = _norm_block(want)
    if back != want:
        raise AssertionError("YAML emitter round trip failed")
    return text


def _norm_block(obj):
    if isinstance(obj, dict):
        return {k: (re.sub(r"\s+", " ", v).strip() if k == "expr" and isinstance(v, str) else _norm_block(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_block(x) for x in obj]
    return obj
