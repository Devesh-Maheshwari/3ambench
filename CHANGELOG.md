# Changelog

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
