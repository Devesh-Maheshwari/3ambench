# Calibration status (v0.2.0)

The expert tier separates mid-size and open models clearly, but it does not separate frontier agents: they solve
almost every expert task, usually in a few minutes. v0.2.0 makes no frontier-difficulty claim.

## What the v0.2.0 leaderboard is

- One attempt per task and model on the 15 tasks of `3ambench-expert-lb@0.2.0` (3 per family), run locally through
  Harbor on Docker Desktop with the agent's network enabled. These are development runs, not a sealed, held-out
  comparison.
- Every stored run was graded with the release grader. The fictional company domains were renamed to reserved
  `.example` names after the runs; stored workspaces and trajectories are read through that rename, and all 171
  stored runs (96 expert, 73 core, plus 2 excluded) regrade to the same scores they had before it.
- Coverage differs by model and the table says so: two `afx-e5-s08` attempts (Opus and Astra) are excluded because
  that task's survey wording was corrected after they ran; Astra and Sol were rerun on the corrected task, the other
  models were not. gpt-oss-120b ran on 10 tasks because its hosted API had a spending cap.
- Every failure in these runs was triaged before it was counted. Failures caused by the task or the grader
  (an unstated requirement, scenario data that punished a valid fix, ambiguous wording) were fixed in the
  generator or grader and the stored runs regraded; only genuine model mistakes count as failures.

## Rules for counting results

- Mechanical acceptance gates come before any model measurement: the reference solution scores 1, an alternative
  valid solution scores 1, doing nothing scores 0, a partial solution lands in its band, and every scripted cheat
  stays in its band.
- A lower score caused by ambiguity, grader errors, missing evidence or infrastructure is not a model failure and not
  a hardening result.
- `scripts/expert_calibrate.py` requires a balanced pilot before any difficulty verdict: two declared models, three
  preselected tasks per family and three valid attempts per model and task. Missing cells produce `collect`, not a
  verdict. Duplicate and invalid runs never count as extra attempts, and historical results are regraded against
  the current task artifacts before use. Report models separately: a pooled average can hide saturation by the
  stronger model.
- Before fresh measurements, freeze task and grader hashes, seeds, model and harness settings, reasoning and
  sampling settings, resource, tool and network limits, attempt counts and the retry and exclusion rules, and keep
  every raw trajectory, final workspace, result and exclusion.

## Frontier tier

A harder frontier tier is in development. It is held out of this release so that it can be measured on tasks no
model has seen; it will ship as its own version once a calibrated measurement supports it.
