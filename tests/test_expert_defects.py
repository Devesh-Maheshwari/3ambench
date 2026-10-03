"""Per-defect checks for the expert tier, graded with the real promtool and amtool.

For every defect card and routine kind the generator can plant, a world that hosts it is built, and every
variant the card lists is applied to the fixed repo and graded on that requirement alone:
- at least two distinct accepted fixes score q = 1;
- every plausible-but-wrong fix scores at or below its stated bound (the untouched repo included).
"""

import os
import shutil
import tempfile

import pytest

from conftest import HAVE_TOOLS

CODES = ["H01", "H03", "H04", "H05", "H06", "H07", "H08", "H09", "H10", "H11", "H12", "H13", "H15", "H17", "H19",
         "H20", "H21", "H22", "H23", "H25", "R01", "R03", "R05", "R06", "R08", "R10", "R11", "R12", "R13", "R18"]
MASTER = 20260930
# BM-4: a fix that gets the mechanism but not the whole defect passes the card's milder detect scenario and earns
# partial credit instead of nothing
PARTIAL = {"H04": "equals_zero", "H06": "printf_rounding", "H08": "avg_5m_for_10m", "H09": "window_10m",
           "H10": "guard_on_up", "H12": "over_2_percent", "H20": "packet_names_only"}


def _coverage() -> dict[str, tuple[str, int]]:
    from alertforge.expert import families
    want, out = set(CODES), {}
    for seed in range(1, 40):
        for fam in families.FAMILIES:
            try:
                _, items = families.build(fam, seed, MASTER)
            except RuntimeError:
                continue
            for it in items:
                if it.code in want and it.code not in out:
                    out[it.code] = (fam, seed)
        if set(out) >= want:
            break
    return out


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    return {"cov": _coverage(), "built": {}, "root": str(tmp_path_factory.mktemp("expert"))}


def _built(worlds, fam, seed):
    from alertforge.expert import build
    key = (fam, seed)
    if key not in worlds["built"]:
        worlds["built"][key] = build.build_task(fam, seed, worlds["root"], f"t-{fam}-{seed}", MASTER)
    return worlds["built"][key]


def _grade_variants(m, code):
    from alertforge.expert import solutions
    from alertforge.expert.build import state_map, write_tests
    from alertforge.grader.xgrade import ExpertGrader
    from alertforge.render import write_tree
    it = next(i for i in m["items"] if i.code == code)
    r = next(x for x in m["spec"]["requirements"] if x["id"] == it.rid)
    spec = {**m["spec"], "requirements": [r], "preserve": {}, "kept": []}
    d = tempfile.mkdtemp(prefix="af-def-")
    try:
        write_tests(d, spec, [g for g in m["groups"] if g["req"] == it.rid])
        grader = ExpertGrader(spec, os.path.join(d, "hidden"))
        out = []
        for name, fn, expect in it.variants(m["world"]):
            files, am, _ = solutions.variant_state(m, it, fn)
            ws = tempfile.mkdtemp(prefix="af-defws-")
            try:
                write_tree(ws, {**m["static"], **state_map(files, am)})
                keys, det = grader.grade(ws)
            finally:
                shutil.rmtree(ws, ignore_errors=True)
            out.append((name, expect, det["requirements"][it.rid]["q"], det["requirements"][it.rid]["families"]))
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.parametrize("code", CODES)
def test_defect_variants(worlds, code):
    if code not in worlds["cov"]:
        pytest.fail(f"no world in seeds 1-39 hosts {code}")
    fam, seed = worlds["cov"][code]
    res = _grade_variants(_built(worlds, fam, seed), code)
    accepted = [n for n, e, q, _ in res if e == "accept"]
    rejected = [n for n, e, q, _ in res if e != "accept"]
    single = code in ("R08", "R18")  # removals: every valid fix ends in the same repo state
    assert len(accepted) >= (1 if single else 2), f"{code}: fewer than two accepted fixes"
    assert rejected, f"{code}: no rejected variant"
    for name, expect, q, fams in res:
        if expect == "accept":
            assert q >= 1 - 1e-9, f"{code} {name}: accepted fix scored {q} ({fams})"
        else:
            assert q <= expect + 1e-9, f"{code} {name}: rejected fix scored {q} > {expect} ({fams})"
        if PARTIAL.get(code) == name:
            assert q >= 0.4, f"{code} {name}: a partial diagnosis earned {q} ({fams})"
    assert code not in PARTIAL or PARTIAL[code] in {n for n, _, _, _ in res}


def _r06_on_h01_service():
    from alertforge.expert import families
    for seed in range(1, 40):
        for fam in families.FAMILIES:
            try:
                w, items = families.build(fam, seed, MASTER)
            except RuntimeError:
                continue
            if any(i.code == "R06" and i.p["svc"] in w.notes.get("legacy_down", {}) for i in items):
                return fam, seed
    return None


@pytest.mark.slow
def test_r06_accepts_down_alert_kept_under_h01_legacy_name(worlds):
    """FA-1: H01 accepts a fix that keeps the legacy rule's name; R06 sourced from that alert is the same fix."""
    hit = _r06_on_h01_service()
    assert hit, "no world in seeds 1-39 puts R06 on an H01 service"
    m = _built(worlds, *hit)
    res = _grade_variants(m, "R06")
    by = {n: (e, q, f) for n, e, q, f in res}
    assert "h01_kept_legacy_name" in by
    for name, (expect, q, fams) in by.items():
        if expect == "accept":
            assert q >= 1 - 1e-9, f"R06 {name}: accepted fix scored {q} ({fams})"
        else:
            assert q <= expect + 1e-9, f"R06 {name}: rejected fix scored {q} > {expect} ({fams})"
    # the renamed Down alert is also H01's fix, so the whole task has to hold, not just R06
    from alertforge.expert import solutions
    from alertforge.expert.build import grade_map, state_map
    it = next(i for i in m["items"] if i.code == "R06")
    fn = next(f for n, f, _ in it.variants(m["world"]) if n == "h01_kept_legacy_name")
    files, am, _ = solutions.variant_state(m, it, fn)
    keys, det = grade_map(m["spec"], os.path.dirname(m["hidden_dir"]), {**m["static"], **state_map(files, am)})
    assert keys["reward"] == 1.0, {r: v.get("q") for r, v in det["requirements"].items()}
