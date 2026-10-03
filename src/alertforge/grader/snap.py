"""promtool driver for the expert tier: ALERTS snapshots, window probes and mask-and-rerun.

Three probe types share one promtool run per scenario group:
- `count`: an expression with an expected value (as in the core tier), tagged with `af_check`.
- `snap`: `ALERTS{alertstate="firing"}` at one evaluation, with `exp_samples: []`. promtool prints every
  firing series with all of its labels when the assertion fails, so the failure text is the snapshot.
- `window`: `max_over_time(ALERTS{alertstate="firing",<sel>}[W])` at the end of an interval, so anything
  that fired at any evaluation inside the interval is printed, even for one minute.

A rule that fails to evaluate stops promtool's group before any assertion runs. Such a group is re-run with
every rule of that name replaced by `absent(vector(1))` (valid, returns nothing), up to MAX_MASKS times, and
the masked names are reported so the scorer can charge them.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import xml.etree.ElementTree as ET

import yaml

from .loader import RuleFile
from .promrun import tool, write_rule_files

SNAP_LABEL = "af_snap"
CHECK_LABEL = "af_check"
MAX_MASKS = 5
TIMEOUT = 400
MASK_EXPR = "absent(vector(1))"
EVAL_ERR = re.compile(r"rule: (?P<name>[^,\n]+), time: (?P<t>[^,\n]+), err: ")


# ---------------------------------------------------------------------------- expressions

def snap_expr(pid: str, sel: str = "") -> str:
    inner = 'alertstate="firing"' + (f",{sel}" if sel else "")
    return f'label_replace(ALERTS{{{inner}}}, "{SNAP_LABEL}", "{pid}", "", "")'


def window_expr(pid: str, minutes: int, sel: str = "") -> str:
    inner = 'alertstate="firing"' + (f",{sel}" if sel else "")
    return f'label_replace(max_over_time(ALERTS{{{inner}}}[{int(minutes)}m]), "{SNAP_LABEL}", "{pid}", "", "")'


def probe_expr(p: dict) -> str:
    if p["type"] == "snap":
        return snap_expr(p["id"], p.get("sel", ""))
    if p["type"] == "window":
        return window_expr(p["id"], p["t"] - p["t0"] + 1, p.get("sel", ""))
    return f'label_replace({p["expr"]}, "{CHECK_LABEL}", "{p["id"]}", "", "")'


# ---------------------------------------------------------------------------- got: parser

def _read_quoted(s: str, i: int) -> tuple[str, int]:
    """Go %q string starting at s[i] == '"'; returns (value, index after the closing quote)."""
    out, i = [], i + 1
    esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in esc:
                out.append(esc[n])
                i += 2
                continue
            if n == "x" and i + 3 < len(s):
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
                continue
            if n == "u" and i + 5 < len(s):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(n)
            i += 2
            continue
        if c == '"':
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise ValueError("unterminated string")


def _read_labels(s: str, i: int) -> tuple[dict[str, str], int]:
    """Label set starting at s[i] == '{'."""
    labels: dict[str, str] = {}
    i += 1
    while i < len(s):
        while i < len(s) and s[i] in " ,":
            i += 1
        if i < len(s) and s[i] == "}":
            return labels, i + 1
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(s, i)
        if not m:
            raise ValueError(f"label name expected at {i}")
        name = m.group(0)
        i = m.end()
        if s[i] != "=":
            raise ValueError("'=' expected")
        value, i = _read_quoted(s, i + 1)
        labels[name] = value
    raise ValueError("unterminated label set")


def parse_got(text: str) -> list[dict[str, str]]:
    """Every series printed after `got:` in a promtool failure text (tokenized, so values may contain
    `, `, braces or escaped quotes)."""
    out = []
    for m in re.finditer(r"got:\s*", text):
        i = m.end()
        while i < len(text) and text[i] == "{":
            try:
                labels, i = _read_labels(text, i)
            except (ValueError, IndexError):
                break
            out.append(labels)
            j = text.find(", {", i)
            nl = text.find("\n", i)
            if j == -1 or (nl != -1 and nl < j):
                break
            i = j + 2
    return out


# ---------------------------------------------------------------------------- running

def _masked(files: list[RuleFile], names: set[str]) -> list[RuleFile]:
    if not names:
        return files
    out = []
    for f in files:
        groups = copy.deepcopy(f.groups or [])
        for g in groups:
            for r in g.get("rules") or []:
                if str(r.get("alert", r.get("record"))) in names:
                    r["expr"] = MASK_EXPR
                    r.pop("for", None)
                    r.pop("keep_firing_for", None)
        out.append(RuleFile(f.path, groups))
    return out


def _test_doc(groups: list[dict], rule_paths: list[str], order: list[str]) -> dict:
    tests = []
    for g in groups:
        pe = []
        for p in g["probes"]:
            exp = [] if p["type"] in ("snap", "window") else [
                {"labels": f'{{{CHECK_LABEL}="{p["id"]}"}}', "value": p["expect"]}]
            pe.append({"expr": probe_expr(p), "eval_time": f'{p["t"]}m', "exp_samples": exp})
        tests.append({"name": g["name"], "interval": "1m", "input_series": g["input_series"],
                      "promql_expr_test": pe})
    doc = {"rule_files": rule_paths, "evaluation_interval": "1m", "tests": tests}
    if order:
        doc["group_eval_order"] = order
    return doc


def _run_once(groups: list[dict], files: list[RuleFile], workdir: str, tag: str) -> dict:
    """{group: ("err", rule name) | ("ok", {pid: bool | list[labels]}) | ("fail", None)}."""
    os.makedirs(workdir, exist_ok=True)
    order: list[str] = []
    rule_paths = write_rule_files(files, os.path.join(workdir, f"rules_{tag}"), order=order)
    if not rule_paths:
        empty = os.path.join(workdir, f"empty_{tag}.yml")
        with open(empty, "w") as fh:
            fh.write("groups: []\n")
        rule_paths = [empty]
    test_path = os.path.join(workdir, f"hidden_{tag}.test.yml")
    junit = os.path.join(workdir, f"junit_{tag}.xml")
    with open(test_path, "w") as fh:
        yaml.safe_dump(_test_doc(groups, rule_paths, order), fh, sort_keys=False, width=10**9)
    fail = {g["name"]: ("fail", None) for g in groups}
    try:
        subprocess.run([tool("promtool"), "test", "rules", f"--junit={junit}", test_path],
                       capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return fail
    if not os.path.exists(junit):
        return fail
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError:
        return fail
    by_name = {g["name"]: g for g in groups}
    out = dict(fail)
    for tc in root.iter("testcase"):
        g = by_name.get(tc.get("name", ""))
        if g is None:
            continue
        text = "\n".join(f.text or "" for f in tc.findall("failure"))
        err = EVAL_ERR.search(text)
        if err:
            out[g["name"]] = ("err", err.group("name").strip())
            continue
        if tc.findall("failure") and not re.search(r"expr: ", text):
            out[g["name"]] = ("fail", None)  # load or parse error of another kind
            continue
        res: dict = {}
        got = parse_got(text)
        for p in g["probes"]:
            if p["type"] in ("snap", "window"):
                res[p["id"]] = [{k: v for k, v in s.items() if k not in (SNAP_LABEL, "__name__")}
                                for s in got if s.get(SNAP_LABEL) == p["id"]]
            else:
                res[p["id"]] = not re.search(re.escape(p["id"]) + r'\\?"', text)
        out[g["name"]] = ("ok", res)
    return out


def run_groups(groups: list[dict], files: list[RuleFile], workdir: str, tag: str = "x",
               masks: dict[str, set[str]] | None = None) -> tuple[dict, dict[str, list[str]]]:
    """Run scenario groups with mask-and-rerun. Returns ({group: results | None}, {group: masked names}).

    `masks` pre-seeds names to mask per group (used by second passes, so they see the same rules)."""
    masks = {k: set(v) for k, v in (masks or {}).items()}
    results: dict[str, dict | None] = {}
    pending = list(groups)
    for it in range(MAX_MASKS + 1):
        if not pending:
            break
        buckets: dict[frozenset, list[dict]] = {}
        for g in pending:
            buckets.setdefault(frozenset(masks.get(g["name"], set())), []).append(g)
        pending = []
        for k, (mset, gs) in enumerate(sorted(buckets.items(), key=lambda kv: sorted(kv[0]))):
            out = _run_once(gs, _masked(files, set(mset)), workdir, f"{tag}{it}_{k}")
            for g in gs:
                state, val = out[g["name"]]
                if state == "ok":
                    results[g["name"]] = val
                elif state == "err" and it < MAX_MASKS and val not in masks.get(g["name"], set()):
                    masks.setdefault(g["name"], set()).add(val)
                    pending.append(g)
                else:
                    results[g["name"]] = None
    for g in pending:
        results[g["name"]] = None
    return results, {k: sorted(v) for k, v in masks.items() if v}
