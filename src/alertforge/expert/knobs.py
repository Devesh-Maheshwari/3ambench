"""Generator knobs for the difficulty ladder (SPEC 9.2, benchmark review F1/BM-1).

  decoys          healthy rules and one route that use the same constructs the planted defects use (an average
                  of per-pod values, per-pod grouping on a channel), each with the reason it is right; changing
                  one costs preservation (on by default: it closes the "odd construct means bug" shortcut)
  e3-evidence     E3 tickets come with alert-channel and PagerDuty history whose explanation needs the route or
                  inhibition match (on by default: SPEC 2.2 wants 1-3 packets per task)
  strip-locators  the comments a planted defect leaves on its rule or route are dropped, except the TODOs a
                  ticket refers to (off by default: the next rung of the ladder if E1-E3 come in above the band)
  label-rule      README says outright that labels beyond the required ones are fine when they don't change where
                  an alert goes, as the expert grader reads it (off by default: it changes the workspace of every
                  task, so it waits for the next build)

The build workers read ACTIVE; `alertforge expert --knobs a,b` sets it for a run.
"""

from __future__ import annotations

ALL = ("decoys", "e3-evidence", "strip-locators", "label-rule")
DEFAULT = frozenset({"decoys", "e3-evidence"})
ACTIVE: set[str] = set(DEFAULT)


def set_active(names) -> None:
    bad = set(names) - set(ALL)
    if bad:
        raise ValueError(f"unknown knobs: {sorted(bad)}")
    ACTIVE.clear()
    ACTIVE.update(names)


def on(name: str) -> bool:
    return name in ACTIVE
