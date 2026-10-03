# Changelog

## 0.2.0

The expert tier, plus reserved names for every fictional company. The core tier's grader and the OpenEnv server
are unchanged; the 30 core tasks change only in their host names and version stamp (see below), and the OpenEnv
image is rebuilt as 0.2.0 so it bundles the renamed tasks.

- **Expert tier: 45 tasks, 5 families × 9 seeds** (`3ambench-expert@0.2.0`; the 15 leaderboard tasks, 3 per
  family, are `3ambench-expert-lb@0.2.0`). Each task is a week of a team's observability queue on a large legacy
  repo at its own fictional company: quiet pager, storm after a deploy, reorg and routing migration, latency SLO
  rollout, and legacy cleanup after an on-call survey. Symptom tickets come with incident packets (PagerDuty and
  Slack exports, postmortems, `query_range` pulls that `bin/range2test` replays); routine requests cover routing,
  inhibition, label moves, burn alerts, SLI recording rules, annotations and legacy removals. Built and gated by
  `alertforge expert` (`src/alertforge/expert/`).
- **Expert grader** (`src/alertforge/grader/xgrade.py` and the modules next to it). One promtool pass over every
  hidden scenario group; outcome checks count delivered alerts, after inhibition; untouched rules that were
  rewritten are replayed rather than compared as text. Same reward shape as the core tier, plus the keys
  `diagnosis` and `routine` (weighted mean score over the symptom tickets and over the rest of the queue) and
  per-kind scores (`req_outcome`, `req_storm`, `req_migration`, `req_absence`, `req_triage`, ...).
- **Grader fairness fixes** found while reviewing the expert candidates, before release:
  - Alertmanager matchers are read with Alertmanager's own grammar, so brace and comma spellings of the same
    matcher score the same.
  - A static `service` label equal to the alert's own service is accepted on moved and relabelled alerts.
  - An inhibition fix also counts when the Down alert it hangs off was fixed under its old name.
  - An untouched alert counts as intact when it behaves the same in every hidden scenario (formatting, operand
    order and redundant static labels may change).
  - When a counter stops, a stated deadline runs from the last sample that still increased.
  - Hidden scenarios carry the target labels and the healthy series of every target and exporter, so a rule that
    reads another service's counters sees real traffic.
  - Symptom tickets have more than one detect scenario, so a fix that catches part of the problem (a floor rule
    that only sees traffic going to zero) earns partial credit instead of none; tickets carry at least 55% of a
    task's weight.
  - Hidden scenarios carry the ingress controller's request counters for every HTTP service, matching the app's own
    counts, so an error-ratio or error-count fix that reads the load-balancer view gets full credit.
  - A retired receiver counts as still in use only where some alert can still reach it, unless the ticket says in
    so many words that nothing may route to it.
  - An alert the queue says to keep may be written with either spelling of the paging severity (`critical` or
    `page`), as every README says they route and page the same.
- **Anti-tamper hardening.** Rules that read `ALERTS` (bare selectors, `__name__` matchers, escaped spellings) count
  as tamper and zero the reward. An inhibition that reaches past the one service a ticket names fails the ticket.
  A label move has to move every definition: a copy left with the old team fails it. Fixes that page on a short
  blip fail where the task's text says such a blip shouldn't page. Shared routing subtrees are part of
  preservation, so deleting or flattening another team's routes costs the run. Repo comments no longer point at the
  planted problems, public task metadata no longer names defect classes, and the published `tests/spec.json` drops
  the internal defect ids.
- **Replay viewer.** A tier switch (Standard: the core tier; Expert: v0.2), a leaderboard per tier grouped by model
  family with a solve-rate bar per model, a requirement view for expert runs (which tickets each run solved, with
  the ticket titles and the reason a ticket lost credit), a light/dark switch and a phone layout.
  `scripts/export_runs.py` writes `runs.json` version 2: `tier` and `model_family` per run, the per-requirement
  breakdown of expert runs, a per-step curve for expert runs whose edits replay exactly (otherwise the final grade
  only), `--regrade-tasks DIR` to re-score stored workspaces with a task's own grader while keeping Harbor's
  original numbers, and `invalid` flags (provider or sandbox failures, a changed instruction, an `--invalid` list)
  that keep a run out of every leaderboard. `scripts/leaderboard.py` prints the README tables from it.
- `registry.json` adds `3ambench@0.2.0`, `3ambench-mini@0.2.0`, `3ambench-expert@0.2.0` and
  `3ambench-expert-lb@0.2.0`; `scripts/push_to_hf.sh` stages the expert tasks and their `partial/` and `null/`
  policies next to the core ones. `scripts/redact.py --check` no longer flags shell defaults, code or its own mask.
- **Calibration and experimental probes** (not part of the released task sets):
  - Regraded saved expert outputs with the corrected verifier; excluded two E5 seed
    8 attempts whose survey evidence changed. The current local development table
    reports saturation by the strongest tested models.
  - Calibration now requires balanced per-model/task attempts and verified current
    grades. Invalid or duplicate runs cannot trigger hardening or task selection.
  - A harder frontier tier is in development. It is not part of this release and no frontier-difficulty claim
    is made.
  - Added deadline windows that apply inhibition at each evaluation, preserving
    background-alert exclusions. Existing point-in-time probes retain their behavior.
  - Fixed failed-build cleanup and packaged the replay/check helper scripts in wheels.
- **Reserved company domains.** Several generated company names were registered domains (runbook URLs, Alertmanager
  e-mail addresses, ingress hosts and incident exports all derive from them). Every fictional company now uses a
  reserved `.example` name, in both tiers. Nothing else in the tasks changed: after mapping the old names to the new
  ones, every agent-visible file is byte-identical, and every stored run regrades to the same score (171 runs, 0
  changes). Core task stamps move to 0.2.0.
- **Hugging Face tag note:** the dataset's `v0.1.1` tag had been created on a revision that still held the v0.1.0
  task files (only the README had been uploaded), so it never served the 0.1.1 fixes. It is removed; use `v0.2.0`,
  which contains the fixed core tasks (renamed as above).


## 0.1.1

Grader fairness fixes. Task seeds, services, rules, postmortems and instructions are unchanged. Each task's
`README.md` (in the workspace) gets the wording changes listed below, and the hidden checks change only
for the requirements named here.

- **Accept any reasonable `for` when the task does not specify one.** A repair of a fleet alert that was
  planted without `for` (defect B02) has no duration in the README, and the postmortem only describes the
  symptom (pages from spikes of a minute or two). The onset checks used to require the oracle's exact
  duration, so a correct fix with another value (e.g. `for: 5m` instead of `10m`) scored like the unfixed
  rule. Such a repair now accepts every `for` from a floor to twice the conventional value: `crashloop` and
  `mem_high` 3m to 2x, `error_ratio` and `latency_p99` 5m to 2x (one full `5m` rate window, so a short spike
  cannot page). Every scenario of the requirement is checked against both ends of that range. Durations the
  README fixes (burn-rate alerts, `<Service>Down`) are still checked exactly. Affected tasks: af-003, af-005,
  af-006, af-009, af-024, af-029.
- **README: recording rules are not per service.** The SLI recording section now says recording rules cover
  every service that emits the family's metrics and must not be filtered by `service`. The check is unchanged.
- **README: fleet alert windows and threshold units.** The fleet section now says error-ratio and latency
  alerts use `5m` rate windows and that thresholds are fractions. A repair of a mis-scaled threshold or a
  broken rate window used to depend on unstated conventions.
- `task.toml` names are generated as `openenvforge/<task>`, matching the published tasks.
