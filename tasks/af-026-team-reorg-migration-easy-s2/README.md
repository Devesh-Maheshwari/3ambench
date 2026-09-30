# af-026-team-reorg-migration-easy-s2

- Workflow: `team-reorg-migration` · Tier: `easy` · Rules format: `plain`
- Axes: archetype `operate_configure`, verifier `regression_suite`, persona `platform_engineer`, tone `urgent-pager`, expertise `novice`
- 5 requirements, 61 atomic checks, 13 preserved rules

| Requirement | Kind | Reward key | Weight |
|---|---|---|---|
| A1 | new_alert | req_alerts | 3 |
| P1 | repair | req_repairs | 3 |
| C1 | recording | req_recording | 2 |
| R1 | route | req_routing | 2 |
| I1 | inhibit | req_inhibit | 2 |

Graded by `tests/test.sh` (promtool + amtool replays of hidden scenarios). See the dataset card for the reward definition.
