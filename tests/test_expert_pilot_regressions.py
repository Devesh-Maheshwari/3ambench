"""The stored pilot workspaces behind three grader fixes, graded on a fresh build of their task (skipped where the
replay store `dist/replay/agent-jobs-v02` isn't present, e.g. a clean checkout).

- afx-e1-s03, two runs: a traffic-floor rule that reads a missing request counter as zero traffic, charged by
  scenarios whose pods were up without request series (0.339 -> 1.0).
- afx-e3-s08: a static `service` equal to the one the Down alert's expression yields, on R06's inhibition source
  (preservation, 0.399 -> 1.0).
- afx-e4-s02: a floor alert with one label more than the README names (T2 L = 0, s 0.85 -> 1.0), and a second,
  identical definition of an untouched burn alert (preservation, 0.399 -> 1.0).
"""

import glob
import json
import os
import re
import shutil

import pytest

from conftest import HAVE_TOOLS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "dist", "replay", "agent-jobs-v02")
MASTER = 20260930
CASES = [("E1", 3, "claude-code__anthropic_claude-opus-5-5__afx-e1-s03", None),
         ("E1", 3, "codex__openai_gpt-6-astra__afx-e1-s03", None),
         ("E3", 8, "codex__openai_gpt-5.6-sol__afx-e3-s08", None),
         ("E4", 2, "claude-code__anthropic_claude-sonnet-5__afx-e4-s02", "T2"),
         ("E4", 2, "claude-code__anthropic_claude-haiku-4-5-20251001__afx-e4-s02", None)]
_BUILT: dict = {}


def _workspace(job: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(STORE, f"{job}*", "*__*", "artifacts", "workspace", "monitoring")))
    return hits[0] if hits else None


def _predates_rename(ws: str) -> bool:
    """True when a stored workspace still carries the company host names the 0.2.0 release renamed to `.example`."""
    texts = []
    for pattern in ("README.md", os.path.join("rules", "*.y*ml")):
        for path in glob.glob(os.path.join(ws, pattern)):
            try:
                with open(path, encoding="utf-8") as fh:
                    texts.append(fh.read())
            except (OSError, UnicodeDecodeError):
                pass
    hosts = re.findall(r"https://runbooks\.([A-Za-z0-9.-]+)", "\n".join(texts))
    return any(not h.rstrip(".").endswith(".example") for h in hosts)


def _renamed_copy(ws: str, tmp: str) -> str | None:
    """A copy of `ws` read through the private rename map named by AF_RENAME_MAP ({"map": {old: new}}), or None."""
    path = os.environ.get("AF_RENAME_MAP")
    if not path:
        return None
    with open(path) as fh:
        names = json.load(fh)["map"]
    keys = sorted(names, key=lambda k: (-len(k), k))
    rx = re.compile(r"(?<![A-Za-z0-9-])(?:" + "|".join(map(re.escape, keys)) + r")(?![A-Za-z0-9])")
    sub = lambda text: rx.sub(lambda mo: names[mo.group(0)], text)  # noqa: E731
    dst = os.path.join(tmp, "monitoring")
    for dp, dns, fns in os.walk(ws):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            src = os.path.join(dp, fn)
            out = os.path.join(dst, sub(os.path.relpath(src, ws)))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                with open(src, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                shutil.copy2(src, out)
                continue
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(sub(text))
    return dst


@pytest.mark.slow
@pytest.mark.parametrize("family,seed,job,req", CASES, ids=[c[2] for c in CASES])
def test_pilot_workspace_regrades_to_full_reward(tmp_path_factory, family, seed, job, req):
    ws = _workspace(job)
    if ws is None:
        pytest.skip(f"{job} not in the replay store")
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    if _predates_rename(ws):
        renamed = _renamed_copy(ws, str(tmp_path_factory.mktemp("renamed")))
        if renamed is None:
            pytest.skip(f"{job} predates the 0.2.0 domain rename; set AF_RENAME_MAP to regrade it")
        ws = renamed
    from alertforge.expert import build
    from alertforge.grader.xgrade import ExpertGrader
    key = (family, seed)
    if key not in _BUILT:
        _BUILT[key] = build.build_task(family, seed, str(tmp_path_factory.mktemp(f"{family}{seed}")),
                                       f"afx-{family.lower()}-s{seed:02d}", MASTER)
    m = _BUILT[key]
    keys, det = ExpertGrader(m["spec"], m["hidden_dir"]).grade(ws)
    assert keys["reward"] == 1.0, {r: (v["s"], v["families"].get("noisy")) for r, v in det["requirements"].items()
                                   if v["s"] < 1}
    if req:
        assert det["requirements"][req]["s"] == 1.0
