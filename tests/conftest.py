"""Shared fixtures: locate promtool/amtool (AF_PROMTOOL/AF_AMTOOL, PATH, or ../../vendor/bin) and build tasks."""

from __future__ import annotations

import os
import shutil

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR_BIN = os.path.abspath(os.path.join(HERE, "..", "..", "..", "vendor", "bin"))

for _tool in ("promtool", "amtool"):
    key = f"AF_{_tool.upper()}"
    if not os.environ.get(key):
        cand = os.path.join(VENDOR_BIN, _tool)
        if os.path.exists(cand):
            os.environ[key] = cand

HAVE_TOOLS = all(shutil.which(os.environ.get(f"AF_{t.upper()}", t)) for t in ("promtool", "amtool"))
needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="promtool/amtool not available")


@pytest.fixture(scope="session")
def easy_task(tmp_path_factory):
    """slo-onboarding easy seed 1, built once per session."""
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge import render
    out = tmp_path_factory.mktemp("tasks")
    return render.build_task("slo-onboarding", "easy", 1, str(out), "af-test-easy", 20260928)


@pytest.fixture(scope="session")
def hard_task(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge import render
    out = tmp_path_factory.mktemp("tasks")
    return render.build_task("missed-page-postmortem", "hard", 1, str(out), "af-test-hard", 20260928)
