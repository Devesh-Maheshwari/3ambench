"""Acceptance gate for expert tasks: oracle 1.0 on three fresh graders (determinism), alternative solver 1.0,
nop 0.0 with progress 0, partial band, q_pristine checks, adversary bands, text lint, leak scan, grader time,
the scraped-world lint (scrapelint.py) and every accepted variant at 1.0 on the whole task."""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import tempfile
import time

from ..grader.xgrade import ExpertGrader
from . import solutions
from .build import grade_map, state_map, write_tests

SWEEP_SLACK = 0.05

# Words that give generated text away (spec §7.4). Kept rot13-encoded so this file does not carry them.
BANNED = [codecs.decode(w, "rot13") for w in (
    'qryir',
    'ebohfg',
    'frnzyrff',
    'yrirentr',
    'pbzcerurafvir',
    "vg'f jbegu abgvat",
    'rafher gung',
    'va beqre gb',
    'NV',
    'nffvfgnag',
    'YYZ',
    'Pynhqr',
    'Naguebcvp',
    'TCG',
    'ynathntr zbqry',
    '(?<!hfre-)ntrag')]
CASED = {codecs.decode(w, "rot13") for w in ("NV", "YYZ", "TCG")}


def lint(texts: dict[str, str]) -> list[str]:
    out = []
    for path, text in texts.items():
        for pat in BANNED:
            flags = 0 if pat in CASED else re.I
            if re.search(rf"\b{pat}\b", text, flags):
                out.append(f"banned word /{pat}/ in {path}")
    return out


def leak_scan(m: dict) -> list[str]:
    text = "\n".join(m["pristine_map"].values())
    found = []
    for bad in ("spec.json", "scenarios.json", "af_snap", "af_check", "q_pristine"):
        if bad in text:
            found.append(f"literal {bad!r} in the workspace")
    pods = set()
    for g in m["groups"]:
        for s in g["input_series"]:
            if len(s["values"]) > 40 and s["values"] in text:
                found.append(f"hidden series values visible ({g['name']})")
            for mm in re.finditer(r'pod="([^"]+)"', s["series"]):
                pods.add(mm.group(1))
    for p in pods:
        if p in text:
            found.append(f"hidden pod name visible: {p}")
    return found


_RULE_LINE = re.compile(r"\s*- (alert|record): ['\"]?([A-Za-z_:][A-Za-z0-9_:]*)")


def comment_share(pmap: dict, touch: set) -> tuple[int, int, int, int]:
    """(comments above touched rules, comments above any rule, touched rules, rules) in the rule files."""
    on_t = on_all = n_t = n_all = 0
    for path, text in pmap.items():
        if not path.startswith("rules/"):
            continue
        prev_comment = False
        for line in text.splitlines():
            if line.strip().startswith("#"):
                prev_comment = True
                continue
            mm = _RULE_LINE.match(line)
            if mm:
                hit = mm.group(2) in touch
                n_all += 1
                n_t += hit
                if prev_comment:
                    on_all += 1
                    on_t += hit
            prev_comment = False
    return on_t, on_all, n_t, n_all


def comment_lint(m: dict) -> list[str]:
    """Rationale comments must not cluster on the rules the queue is about (a grep for comments is no map)."""
    touch = set().union(*[it.touch for it in m["items"]]) if m["items"] else set()
    on_t, on_all, n_t, n_all = comment_share(m["pristine_map"], touch)
    if on_all and n_all and on_t / on_all > n_t / n_all + 0.20:
        return [f"comments cluster on touched rules: {on_t}/{on_all} vs {n_t}/{n_all} rules touched"]
    return []


def variant_sweep(m: dict, codes: set | None = None, slack: float = SWEEP_SLACK) -> list[str]:
    """RT-5: every declared-wrong variant of every item, applied on top of the oracle, keeps the item's `s` at or
    below the bound the variant declares (+ slack), and the item left untouched is never solved. Each item is
    graded on its own requirement and scenario groups (the other requirements cannot change its q), one grader
    per item."""
    out = []
    w = m["world"]
    for it in m["items"]:
        if codes is not None and it.code not in codes:
            continue
        wrong = [(n, fn, exp) for n, fn, exp in it.variants(w) if exp != "accept"]
        if not wrong:
            continue
        r = next(x for x in m["spec"]["requirements"] if x["id"] == it.rid)
        spec = {**m["spec"], "requirements": [r], "preserve": {}, "kept": []}
        d = tempfile.mkdtemp(prefix="af-sweep-")
        try:
            write_tests(d, spec, [g for g in m["groups"] if g["req"] == it.rid])
            grader = ExpertGrader(spec, os.path.join(d, "hidden"))
            for name, fn, bound in wrong:
                files, am, _ = solutions.variant_state(m, it, fn)
                _, det = grade_map(spec, d, {**m["static"], **state_map(files, am)}, grader)
                s = det["requirements"][it.rid]["s"]
                if name == "pristine":
                    # the item left as it starts, every other item fixed: other fixes can lift its cases (a fixed
                    # catch-all gives the new team's pages their FYI receiver), but it must never be solved
                    if s >= 1 - 1e-9:
                        out.append(f"{it.rid} ({it.code}) is solved without being touched")
                elif s > float(bound) + slack + 1e-9:
                    out.append(f"{it.rid} ({it.code}) wrong variant {name}: s={s:.3f} > bound {bound}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return out


def accepted_sweep(m: dict, grade) -> tuple[int, list[str]]:
    """Every accepted variant of every item, on top of the oracle, graded on the whole task: a fix the text allows
    must not be charged by another item's scenarios either (a floor rule that reads missing traffic as none, paged
    in another ticket's scenarios whose pods had no request series, afx-e1-s03). Returns (variants, failures)."""
    out, n = [], 0
    for it in m["items"]:
        for name, fn, exp in it.variants(m["world"]):
            if exp != "accept" or name == "oracle":
                continue
            n += 1
            k, det = grade(solutions.variant_state(m, it, fn))
            if k["reward"] < 1 - 1e-9:
                bad = {r: (v["q"], v["families"].get("noisy")) for r, v in det["requirements"].items() if v["s"] < 1}
                out.append(f"accepted {it.rid} ({it.code}) {name}: reward {k['reward']} {json.dumps(bad)[:300]}")
    return n, out


def _adversaries(m: dict, grade, partial_reward: float) -> tuple[dict, list[str]]:
    """Each adversary on top of the oracle (its own band), and each cheat again on the pristine repo (reward at or
    below the honest partial policy and PRISTINE_BOUND)."""
    out, reasons = {}, []
    cap = min(solutions.PRISTINE_BOUND, partial_reward)
    for name, (key, bound, applies) in solutions.ADVERSARIES.items():
        bases = (["pristine"] if name in solutions.PRISTINE_ONLY
                 else ["oracle"] + (["pristine"] if name in solutions.CHEATS else []))
        for base in bases:
            label = f"{name}@pristine" if base == "pristine" and name not in solutions.PRISTINE_ONLY else name
            st = solutions.adversary(m, name, base) if applies(m) else None
            if st is None:
                out[label] = "n/a"
                continue
            k, _ = grade(st)
            if label.endswith("@pristine"):
                ok, key2 = k["reward"] <= cap + 1e-9, "reward"
            elif key == "tamper":
                ok, key2 = k["tamper"] == 1.0 and k["reward"] == 0.0, key
            elif key == "keep":
                ok, key2 = k["reward"] >= bound - 1e-9, "reward"
            elif key == "below_partial":
                ok, key2 = k["reward"] <= partial_reward + 1e-9 and k["outcome"] == 0.0, "reward"
            else:
                ok, key2 = k[key] <= bound + 1e-9 and k["outcome"] == 0.0, key
            out[label] = {"reward": k["reward"], key2: k.get(key2), "ok": ok}
            if not ok:
                reasons.append(f"adversary {label} out of band: {key2}={k.get(key2)} outcome={k['outcome']}"
                               + (f" (cap {cap:.3f})" if label.endswith("@pristine") else ""))
    return out, reasons


def detect_counts(m: dict) -> dict[str, int]:
    """Per symptom ticket, how many distinct scenarios (or route/inhibit cases) can tell a fix from the pristine:
    the number that gives a partial diagnosis partial credit (BM-7 tracks this instead of the raw check count)."""
    out = {}
    for r in m["spec"]["requirements"]:
        if not r.get("ticket"):
            continue
        gs = [g for g in m["groups"] if g["req"] == r["id"]]
        if r["kind"] == "outcome":
            n = sum(1 for g in gs if any(p.get("family") in ("F_detect", "S_defect") for p in g["probes"]))
        elif r["kind"] in ("storm", "recording"):
            n = len(gs)
        elif r["kind"] == "route":
            n = len(r["route"].get("pos", [])) + len(r["route"].get("chain", []))
        elif r["kind"] == "inhibit":
            n = sum(1 for c in r["inhibit"]["cases"] if c["expect"])
        else:
            n = len(gs)
        out[r["id"]] = n
    return out


def family_checks(m: dict) -> tuple[dict, list[str]]:
    """SPEC 3.0 routine overlap with every earlier seed of the family, and the SPEC 2.2 alert-rule floor on the
    repo the queue starts from."""
    from . import families
    from .build import materialize
    from .repo import ALERT_FLOOR
    w = m["world"]
    ov, other = families.max_overlap(w.family, w.seed, w.notes.get("master", 20260930), m["items"])
    files, _ = materialize(w, m["items"], set())
    n = sum(1 for f in files.values() for g in f.groups for r in g["rules"] if "alert" in r)
    info = {"overlap": round(ov, 3), "overlap_seed": other, "alert_rules_pristine": n, "detect": detect_counts(m),
            "decoys": w.notes.get("decoys", []), "decoy_route": (w.notes.get("decoy_route") or {}).get("team")}
    bad = []
    if ov > families.MAX_OVERLAP + 1e-9:
        bad.append(f"routine kinds overlap seed {other} by {ov:.2f} (> {families.MAX_OVERLAP})")
    if n < ALERT_FLOOR:
        bad.append(f"{n} alert rules in the starting repo (< {ALERT_FLOOR})")
    return info, bad


def gate_task(m: dict, out_root: str | None = None, adversaries: bool = True, sweep: list[str] | None = None) -> dict:
    spec, hidden = m["spec"], m["hidden_dir"]
    tests_dir = os.path.dirname(hidden)
    static = m["static"]
    res: dict = {"task_id": m["task_id"], "reasons": [], "family": m["world"].family}

    def grade(state, grader=None):
        if state is None:
            return None, None
        files, am, extra = state
        mp = {**static, **state_map(files, am)}
        for k, v in extra.items():
            if v is None:
                mp.pop(k, None)
            else:
                mp[k] = v
        return grade_map(spec, tests_dir, mp, grader)

    times, oracle_keys = [], []
    for _ in range(3):
        t0 = time.time()
        k, det = grade(solutions.oracle(m), ExpertGrader(spec, hidden))
        times.append(round(time.time() - t0, 2))
        oracle_keys.append(k)
    res["grader_seconds"] = times
    ko = oracle_keys[0]
    res["oracle"] = ko["reward"]
    if any(k != ko for k in oracle_keys[1:]):
        res["reasons"].append("oracle keys differ between fresh graders")
    if ko["reward"] != 1.0:
        bad = {r: v for r, v in det["requirements"].items() if v["q"] < 1}
        pbad = [x for x in det.get("preservation_items", []) if not x["ok"]][:5]
        res["reasons"].append(f"oracle {ko['reward']}: {json.dumps(bad)[:1500]} P-fails {pbad} tamper {det.get('tamper')}")
    ka, deta = grade(solutions.alt(m))
    res["alt"] = ka["reward"]
    if ka["reward"] != 1.0:
        bad = {r: v for r, v in deta["requirements"].items() if v["q"] < 1}
        pbad = [x for x in deta.get("preservation_items", []) if not x["ok"]][:5]
        res["reasons"].append(f"alt {ka['reward']}: {json.dumps(bad)[:1500]} P-fails {pbad}")
    kn, _ = grade(solutions.null(m))
    res["null"] = kn["reward"]
    if kn["reward"] != 0.0 or kn["progress"] != 0.0 or kn["preservation"] != 1.0:
        res["reasons"].append(f"null not 0: reward {kn['reward']} progress {kn['progress']} P {kn['preservation']}")
    kp, _ = grade(solutions.partial(m))
    res["partial"] = kp["reward"]
    if not (kp["outcome"] == 0.0 and 0.05 < kp["reward"] < 0.4):
        res["reasons"].append(f"partial out of band (0.05, 0.4): {kp['reward']}")
    for r in spec["requirements"]:
        if r["q_pristine"] >= 1:
            res["reasons"].append(f"{r['id']} q_pristine {r['q_pristine']} >= 1")
        if r["kind"] in ("outcome", "storm") and r["q_pristine"] > 0.1:
            res["reasons"].append(f"{r['id']} ({r['defect']}) outcome q_pristine {r['q_pristine']} > 0.1")
    res["reasons"] += lint({**m["pristine_map"], "instruction.md": open(os.path.join(os.path.dirname(tests_dir), "instruction.md")).read()})
    res["reasons"] += leak_scan(m)
    res["reasons"] += comment_lint(m)
    from .scrapelint import lint as scrape_lint
    res["reasons"] += scrape_lint(m)
    oracle_grader = ExpertGrader(spec, hidden)
    res["accepted"], bad = accepted_sweep(m, lambda st: grade(st, oracle_grader))
    res["reasons"] += bad
    from .textlint import lint as text_lint
    res["reasons"] += text_lint(m)
    info, bad = family_checks(m)
    res.update(info)
    res["reasons"] += bad
    res["sweep"] = variant_sweep(m) if sweep is None else sweep
    res["reasons"] += res["sweep"]
    if adversaries:
        res["adversaries"], bad = _adversaries(m, grade, kp["reward"])
        res["reasons"] += bad
    if out_root:
        from ..render import patch_script
        pmap = {**static, **state_map(*solutions.partial(m)[:2])}
        for sub, target_map, hdr in (("partial", pmap, "partial: even-indexed requirements + one plausible mistake"),
                                     ("null", m["pristine_map"], "null: no-op (equivalent to harbor -a nop)")):
            d = os.path.join(out_root, sub, m["task_id"])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "solve.sh"), "w") as fh:
                fh.write(patch_script(m["pristine_map"], target_map, hdr) if sub == "partial" else f"#!/bin/bash\n# {hdr}\nexit 0\n")
            os.chmod(os.path.join(d, "solve.sh"), 0o755)
    res["pass"] = not res["reasons"]
    return res
