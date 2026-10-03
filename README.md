---
license: apache-2.0
pretty_name: "3amBench (AlertForge)"
language: [en]
task_categories: [other]
size_categories: [n<1K]
tags: [harbor, openenv, rl-environment, agent, benchmark, dense-reward, prometheus, alertmanager, promql, sre, slo, observability]
configs:
  - config_name: default
    data_files: manifest.jsonl
---

# 3amBench: can your agent write alerts that page the right human at 3 a.m., and only then?

3amBench (package `alertforge`) is an RL environment and benchmark for a job SRE teams do every week:
owning **Prometheus alerting rules and Alertmanager routing as code**. Each task drops the agent into a realistic
`monitoring/` repo for a fictional company with a handful of change requests: onboard a service onto
multi-window burn-rate SLO alerts, fix the alert that paged 30 times last night, find out why nobody was paged
when a service was down for 47 minutes, route a newly formed team, add inhibition so an outage produces one page
instead of forty.

**Grading is behavioral.** Nothing is judged by an LLM, and nothing is compared as text. Hidden, seed-varied
outage replays run through Prometheus' own `promtool test rules`, and routing runs through Alertmanager's own
`amtool`. Every alert must **fire** during the outage, stay **silent** during normal traffic, short spikes,
recovery, low traffic and near-threshold traffic, fire **on time** (not before `for` elapses), fire **once per
service** (not per pod), carry the right **labels**, and reach the right **receivers**. A task has 59-189
atomic checks, so the reward is dense.

| | |
|---|---|
| Tasks | 30 core (5 workflows × 3 tiers × 2 seeds) and 45 expert (5 families × 9 seeds, see [Expert tier](#expert-tier-v02)), plus unlimited fresh ones from the generator / OpenEnv server |
| Checks per task | 59-189 (easy 59-67, medium 112-139, hard 167-189); expert 128-232 |
| Reward | `reward` in [0, 1] plus 15 diagnostic keys (core) or 26 (expert), identical within a tier |
| Grader | promtool 3.5.0 + amtool 0.28.1 + stdlib Python + PyYAML; deterministic; core 0.7-7 s, expert about 0.5-12 s per grade (one process) |
| Formats | Harbor tasks (this repo has the 30 core tasks; the Hugging Face dataset has all 75) and an OpenEnv server with per-step reward for the core tier (`openenv/alertforge_env`) |

## Quick start

```bash
# oracle (expect mean 1.0) and no-op (expect 0.0)
uvx harbor run --repo https://huggingface.co/datasets/openenvforge/3ambench@v0.2.0 -d 3ambench@0.2.0 -a oracle -n 4
uvx harbor run --repo https://huggingface.co/datasets/openenvforge/3ambench@v0.2.0 -d 3ambench@0.2.0 -a nop -n 4
# your model on the 5-task smoke set
uvx harbor run --repo https://huggingface.co/datasets/openenvforge/3ambench@v0.2.0 -d 3ambench-mini@0.2.0 -a terminus-2 -m <model>
# expert tier: the 15 leaderboard tasks (all 45: -d 3ambench-expert@0.2.0)
uvx harbor run --repo https://huggingface.co/datasets/openenvforge/3ambench@v0.2.0 -d 3ambench-expert-lb@0.2.0 -a terminus-2 -m <model>
```

The core tasks in `3ambench@0.2.0` are the `3ambench@0.1.1` tasks with their fictional company domains moved to
reserved `.example` names (and the version stamp); checks, scenarios and grader are unchanged, and every recorded
v0.1.1 run regrades to the same reward, so v0.1.1 numbers stay comparable.

Tasks set `[agent] network_mode = "no-network"`, because this repo ships the solutions (see Anti-hacking).
Harbor enforces that on Linux Docker hosts and sandboxed providers. **Docker Desktop on macOS cannot enforce it**
(its VM kernel lacks `CONFIG_NFT_FIB_INET`), and Harbor refuses the task. For local smoke runs on a Mac, use
`scripts/harbor_local.sh`, which runs a temp copy without the network override. Those runs are not leaderboard-safe.

**OpenEnv (multi-turn, per-step reward)**, against a local server started as in [Try it](#try-it). The client
needs `pip install "openenv-core==0.3.0"` and a clone of this repo on `PYTHONPATH=openenv`:
```python
from alertforge_env.client import AlertForgeEnv
with AlertForgeEnv(base_url="http://localhost:8000").sync() as env:
    obs = env.reset(seed=7, split="train", workflow="missed-page-postmortem", tier="medium")
    obs = env.step({"tool": "read_file", "path": "README.md"})
    obs = env.step({"tool": "write_file", "path": "rules/service-health.yml", "content": "..."})
    print(obs.reward, obs.done)   # parity mode: rewards sum to the Harbor reward
```

**Fresh tasks (training, private held-out sets):** `python generator/generate.py --master-seed <secret> --out <dir>`
(needs promtool and amtool; without `--out` it overwrites this repo's tasks).

## Try it

- **Replay runs in the browser:** [3amBench Replay](https://huggingface.co/spaces/openenvforge/3ambench-replay)
  steps through recorded runs (tool calls, file diffs, the reward curve, which checks pass after each step, and
  for expert runs which tickets were solved).
  Its data comes from `scripts/record_reference_runs.py`, `scripts/record_agent_runs.sh` and `scripts/export_runs.py`;
  the page itself is in `space/`.
- **Run the environment locally** and open the web interface at http://localhost:8000/web:
  ```bash
  docker run -p 8000:8000 -e ENABLE_WEB_INTERFACE=true ghcr.io/devesh-maheshwari/3ambench-env:0.2.0
  ```
  The image is built for linux/amd64 and linux/arm64. To build it yourself from a clone:
  `docker build -f openenv/alertforge_env/server/Dockerfile -t 3ambench-env .` (then run `3ambench-env`).

## Leaderboard (core, 6 tasks: af-001, af-009, af-013, af-017, af-021, af-029)

<!-- LEADERBOARD:core -->
| Agent | Model | Runs | Mean reward | Solved |
|---|---|---|---|---|
| claude-code | claude-opus-5-5 | 12 | 1.000 | 12/12 |
| codex | gpt-6-astra | 12 | 1.000 | 12/12 |
| terminus-2 | gpt-oss-120b | 13 | 0.436 | 3/13 |

37 runs on 6 tasks.
<!-- /LEADERBOARD -->

The runs were recorded on the v0.1.1 tasks; their saved workspaces, read with the renamed domains, regrade to the
same rewards on the 0.2.0 tasks. Local Harbor runs on Docker Desktop (agent network enabled, so not sealed). Frontier agents saturate this subset;
the dense reward separates weaker models (gpt-oss-120b scores between 0.03 and 0.37 on every unsolved run instead of 0).
v0.2 adds the expert tier below. Every run can be replayed step by step in the
[replay Space](https://huggingface.co/spaces/openenvforge/3ambench-replay).

## Expert tier (v0.2)

A core task is a short list of change requests on a small repo. An expert task is a week of a team's
observability queue on a large legacy one: 45 tasks, five families of nine seeds, each at a different fictional
company with its own services, teams and history. The starting repo has 62-85 alerting rules in 14-22 files, SLI
recording rules, an SLO catalog, an ownership file, ADRs, runbooks, a CI script, and an Alertmanager config shared
with two other Prometheus servers (41-53 receivers, legacy `match:` routes next to `matchers:`). The queue holds two
or three symptom tickets and two to seven routine requests.

| Family | The week |
|---|---|
| E1 quiet pager | Nobody was paged: an outage where the Down alert could not fire once the targets went away, an error-ratio page that never fired, a replica-lag alert stuck in pending, a postmortem action item asking for a traffic-floor page |
| E2 storm after a deploy | Too many pages: one per pod during a database failover, false pages from canaries and rollouts, a cluster outage that paged every service owner |
| E3 reorg + routing migration | A team split that routing never caught up with: alerts that still carry the old team, a team with no route, SRE still everyone's first pager, a channel being archived, warnings muted by unrelated criticals |
| E4 latency SLO rollout | A latency SLO going live: SLI recording rules that are wrong during a rollout, slow requests that never page, burn alerts to add |
| E5 legacy cleanup after an on-call survey | The work arrives as raw answers to an on-call survey: a consumer stall that never fired, a nightly batch that stopped without a ticket, a disk prediction that pages for the backup job, dead runbook links, a legacy service to remove. Some answers need nothing from this repo, and some alerts have to stay as they are |

What makes it realistic:

- Tickets read like tickets. A symptom ticket says what happened in the reporter's words ("17 pages for one
  database failover", "KafkaConsumerStalled has never fired") and points at an incident packet: PagerDuty and Slack
  exports, a postmortem, `query_range` pulls of the night that `bin/range2test` turns into promtool tests. It never
  says which rule is wrong.
- Policy lives in the repo, not in the ticket: SLOs and windows in the catalog, the paging policy in the README,
  ownership in `teams/ownership.yaml`, the burn-rate policy in an ADR. A new hire would have to look there too.
- The repo has a past: two routing syntaxes, a team split half done, rules that moved with their team, decoy alerts
  that look broken and aren't, alerts other teams own.
- No two tasks share a company, an org chart or a queue, so a fix can't be copied from a sibling task.

**Grading** is behavioral and dense, as in the core tier. Promtool replays every hidden scenario group once (plus a
re-run where a deadline window needs one) and the grader reads which alerts fire, minute by minute; routing goes through amtool and inhibition through the strict
Alertmanager simulator. A symptom ticket is an outcome check: when the incident replays, the right page reaches the
right team, and it stays quiet on normal traffic, deploys, flaps and the noise that started the ticket. Alerts are
counted after inhibition, so muting everything doesn't make a quiet pager look fixed. Routine requests (routes,
inhibitions, label moves, burn alerts, recording rules, annotations, legacy removals) are checked the same way.
Untouched rules, receivers and routes have to keep working; a rewritten but equivalent rule is replayed, not
compared as text. Each requirement scores s in [-0.25, 1] against the starting repo, and `reward` = 0.6 · solved +
0.4 · progress as in [Reward](#reward); expert progress also loses 0.75 weight units for each leave-as-is alert the
run changes, and `solved` requires all of them intact. A symptom ticket weighs 5-10 against 2-3 for a routine request, so the
tickets carry 55-82% of a task's weight. `diagnosis` is the weighted mean s over the symptom tickets and `routine`
the same over the rest: among runs that don't solve a task, they tell finding the cause apart from doing the easy
part of the queue. Expert tasks have 128-232 checks and the same anti-hacking defenses, plus tamper checks on reads
of `ALERTS`.

**Calibration, honestly:** frontier agents solve most expert tasks, in minutes (3-5 minutes per task in our pilot
runs). The tier is not built to rank frontier models against each other. It is aimed at separating mid-size and
open models, where the dense reward and the diagnosis/routine split show how far a run got, and at RL training on a
realistic repo. A harder frontier tier is in development; there is no frontier-hardness result yet.

### Leaderboard (v0.2.0, expert: the 15 tasks of `3ambench-expert-lb@0.2.0`, 3 per family)

<!-- LEADERBOARD:expert -->
| Agent | Model | Runs | Tasks | Mean reward | Solved | Diagnosis | Routine |
|---|---|---|---|---|---|---|---|
| claude-code | claude-opus-5-5 | 14 | 14/15 | 1.000 | 14/14 | 1.000 | 1.000 |
| codex | gpt-6-astra | 15 | 15/15 | 0.960 | 14/15 | 1.000 | 1.000 |
| claude-code | claude-fable-5-1 | 14 | 14/15 | 0.956 | 13/14 | 1.000 | 1.000 |
| codex | gpt-5.6-sol | 15 | 15/15 | 0.912 | 13/15 | 0.967 | 1.000 |
| claude-code | claude-sonnet-5 | 14 | 14/15 | 0.762 | 9/14 | 0.903 | 1.000 |
| claude-code | claude-haiku-4-5-20251001 | 14 | 14/15 | 0.346 | 2/14 | 0.473 | 0.927 |
| terminus-2 | gpt-oss-120b | 10 | 10/15 | 0.065 | 0/10 | 0.071 | 0.345 |

96 runs on 15 tasks; 2 runs flagged invalid are not counted.
<!-- /LEADERBOARD -->

The 15 tasks are `3ambench-expert-lb@0.2.0`: the development pilot set (3 per family,
`scripts/expert_calibrate.py --pilot`), not a calibrated or held-out selection. These are unbalanced local
development runs on Docker Desktop (agent network enabled, so not sealed), not a held-out model comparison, and the
rows cover different tasks (the Tasks column), so compare them with care. The two historical `afx-e5-s08` trials
(Opus and Astra) are excluded because their survey evidence differs from the current task. Astra and Sol were re-run
on the current task, so they cover all 15 tasks; Opus, Fable, Sonnet and Haiku have no valid `afx-e5-s08` run yet
and cover 14. gpt-oss-120b covers 10: the provider budget cap stopped it before afx-e2-s03, afx-e3-s04, afx-e4-s04
and afx-e5-s03, and it has no `afx-e5-s08` run. Every saved output was regraded with the released task's own
grader. Six runs score higher than Harbor's verifier gave them at the time, because of the grader fixes listed in
the CHANGELOG; the final 0.2.0 changes (reserved domains, ingress series, the two grader fixes) change no recorded
score. Infrastructure failures are excluded when present. Every run, with the
tickets it solved, can be replayed in the [replay Space](https://huggingface.co/spaces/openenvforge/3ambench-replay).

To build the 45 tasks yourself: `pip install .` from a clone, then `alertforge expert --out <dir> --seeds 1-9`
(promtool and amtool on the path, or in `AF_PROMTOOL` and `AF_AMTOOL`). It runs each task's acceptance gate as well,
and it reproduces the released tasks byte for byte.

See [calibration status](docs/calibration-status.md) for the calibration protocol and what is still open.

## What a task looks like

`af-001-slo-onboarding-easy-s1` (instruction excerpt; change requests are shuffled):

> - **P1.** `HighErrorRatio` is misbehaving; see `postmortems/PM-4043.md`. Fix the rule, keeping its purpose.
> - **I1.** While `ChatGwDown` is firing for a service, suppress `ChatGwErrorBudgetBurnFast`, `HighErrorRatio` for that same service only.
> - **A1.** Add `ChatGwErrorBudgetBurnFast` for `chat-gw` (SLO 0.995): fire when both `slo:sli_error:ratio_rate1h` and `slo:sli_error:ratio_rate5m` exceed 14.4 × (1 − 0.995), `for: 2m`, ...
> - **R1.** Team `payments` is now on call for `chat-gw`, but nothing routes to them yet. ...
> - **C1.** Add SLI recording rules `slo:sli_error:ratio_rate5m`, `slo:sli_error:ratio_rate1h` for the `grpc` SLI family ...

The postmortem describes a *symptom* ("pages at 4 a.m. when there are only a handful of requests"), never the
fix. At the `practitioner`/`expert` levels, requirements are stated as outcomes ("page payments when chat-gw is
burning its error budget fast") and the agent has to find names, windows, `for` values, labels and routing
policy in the repo's `README.md`, like a new hire would.

Workflows: `slo-onboarding`, `alert-storm-cleanup`, `missed-page-postmortem` (including total outages where
series disappear, so only `absent()` can page), `latency-slo` (histogram SLIs), `team-reorg-migration`.
Rules come as plain files or as Kubernetes `PrometheusRule` manifests.

## Reward

Each requirement r gets a score q_r from its check families (`mean(∅) = 1`):

```
Alert (new or repair): q = E · dup · mean(Fire ∪ Timing) · mean(Silent) · (0.7 + 0.3·mean(Label ∪ Annotation))
                       (burn alerts: q = ½·q_integrated + ½·q_with_reference_SLI_records)
Recording:             q = dup · mean(Value ∪ Cardinality)
Route:                 q = mean_positive Jaccard(expected, resolved receivers) · mean_negative [no forbidden receiver]
Inhibit:               q = mean(Suppressed) · mean(NotSuppressed)
s_r      = clip((q_r − q_r^pristine) / (1 − q_r^pristine), −0.25, 1)      # pristine → 0, oracle → 1, regressions < 0
progress = max(0, (0.5 + 0.5·preservation) · Σ w_r s_r / Σ w_r)        # weights: alert 3, repair 3, others 2
reward   = 0.6 · [every s_r = 1 ∧ preservation = 1 ∧ syntax_ok ∧ ¬tamper] + 0.4 · progress
```

Why products: an always-firing rule fails every silent check, and a renamed or never-firing rule fails every
fire check, so both score 0. A catch-all route passes positives and fails negatives. An inhibit-everything rule
fails the not-suppressed cases. Partial work still counts: a wrong severity costs the label family only, a
missing `continue` gives Jaccard ½, and a correct burn alert on top of broken SLI records keeps half its credit.

| Key | Meaning |
|---|---|
| `reward` | primary scalar |
| `solved` / `outcome` | 1 iff everything is fully correct (use for pass@k) |
| `progress` | the dense part (plot this separately; `reward` has a gap between 0.4 and 0.6 by construction) |
| `preservation` | untouched rules, receivers, routes and inhibitions still intact |
| `req_alerts`, `req_repairs`, `req_recording`, `req_routing`, `req_inhibit` | per-category scores |
| `fire_rate`, `silent_rate`, `label_rate`, `check_pass_rate` | raw diagnostic pass rates (non-zero for a no-op) |
| `syntax_ok`, `tamper` | all files load / a forbidden construct was used (tamper zeroes the reward) |
| `diagnosis`, `routine` | expert tier only: weighted mean `s` over the symptom tickets, and over the rest of the queue. Tickets carry at least 55% of an expert task's weight, so among runs that don't solve a task these say whether the progress came from diagnosing or from routine edits |

## Reward spread (measured on the v0.1.0 tasks, before the 0.1.1 fairness fixes)

| Policy | reward | notes |
|---|---|---|
| oracle (`solution/solve.sh`) | **1.0** on 30/30 | Harbor `-a oracle`: 1.0 on `af-001` (easy), `af-017` (hard, PrometheusRule), `af-023` (hard, latency), `af-029` (hard, reorg) |
| no-op (`-a nop`) | **0.0** on 30/30 | `preservation` = 1 |
| partial (`partial/<task>/solve.sh`: half the requirements plus one plausible mistake) | 0.17-0.29 | Harbor = local grader exactly (`af-017` 0.21644, `af-011` 0.208481) |
| P-mut: oracle + 1-3 agent-style mistakes (duplicate alert, missing `by`, missing `continue`, scratch file, YAML indent slip, wrong `for`), 120 episodes | mean 0.386, 0% at 0, 10% at 1, 94 distinct values | `progress` mean 0.81 |
| OpenEnv per step (read → write oracle files one at a time with one YAML slip → submit), 30 tasks | 54% of steps and 97% of writes change the reward | Σ step rewards = Harbor reward exactly (parity mode) |

Adversaries (all 30 tasks, local gate): always-fire, rename, catch-all route, inhibit-all → targeted
`req_*` ≤ 0.05. `ALERTS` injection, input-series shadowing, group `interval` changes → `tamper`, reward 0.
Receiver nulling, over-broad inhibition, band thresholds, per-pod alerts, `for`-only fixes, deleting untouched
rules → `outcome` 0 and reward ≤ 0.4.

Real-model results are in the two leaderboards above; a dense-vs-outcome GRPO training curve is not published yet.

## How it was built: Skill2Env, but procedural

This mirrors NVIDIA's [Skill2Env](https://github.com/NVlabs/Skill2Env) pipeline:
`skill/prometheus-alerting/SKILL.md` → `workflows/workflows.yaml` (the "planner output", same keys) → sampled
axes (archetype, verifier pattern, persona, tone, expertise, tier, rules format) → **a deterministic, seeded
creator** instead of an LLM → acceptance gate (oracle = 1, nop = 0, partial band, 14 adversaries, a
"missing `for` must fail Timing" mutant check, structural leak scan, determinism).

Checks are derived from the world spec through a **reference evaluator** (`promsim.py`: Prometheus `rate`
extrapolation, left-open windows, `histogram_quantile`, staleness, the `for` state machine, in exact fractions),
never from the oracle's rule text. The gate proves the oracle against real promtool, which is the structural
equivalent of Skill2Env's freeze boundary.

## Anti-hacking

| Threat | Defense |
|---|---|
| Downloading this repo's solutions | `[agent] network_mode = "no-network"`; no seeds in `task.toml`; hidden checks live only in the verifier image; evaluate headline numbers on a private-seed build |
| Editing the grader or pre-writing rewards | separate verifier container built from `tests/Dockerfile`; `test.sh` deletes old outputs and verifies `checksums.sha256` |
| Always-fire / never-fire / rename | fire × silent product; existence gate |
| Per-pod alerts, threshold bands, `for`-only fixes | `count(ALERTS{...}) == 1` per service, bracketing scenarios at 0.85T/1.15T, random spikes, long spikes that must fire |
| Faking `ALERTS`, shadowing input series, eval knobs | tamper: reserved record names, `label_replace` onto reserved labels, group `interval`/`query_offset`/`limit` |
| Null receivers, mute intervals, inhibit-everything | receivers, `global` hashed into preservation; time intervals are tamper; not-suppressed and bystander cases |
| Copying a healthy sibling | hard tier has no healthy sibling burn alerts; structural leak scan |
| Postmortem lookup tables | 3-4 paraphrases per symptom, decoy postmortems, defects with no postmortem on hard |
| Reward oracle in OpenEnv | `heldout`/`public` splits force outcome-only reward and hide `phi` |

## Related work

- [NVlabs/Skill2Env](https://github.com/NVlabs/Skill2Env): the SkillHub `sre-engineer` skill contains a literal
  14.4x burn-rate rule, and `slo-architect` covers burn-rate alerting. We downloaded ten SRE-adjacent Skill2Env
  tasks. The closest one (`task_slo-architect_lgre556n`) grades burn-rate policy by comparing YAML fields, and none
  run promtool or amtool.
- [camel-ai/seta-env](https://github.com/camel-ai/seta-env) task 1114: an Alertmanager routing and inhibition task. Its
  tests run amtool only for `check-config` and check routes and inhibitions by reading the YAML. 3amBench's routing
  checks are end-to-end: the chain case routes the labels the agent's own alert carries.
- Community rule sets: [samber/awesome-prometheus-alerts](https://github.com/samber/awesome-prometheus-alerts),
  [kubernetes-mixin](https://github.com/kubernetes-monitoring/kubernetes-mixin); Google SRE Workbook ch. 5.

## Limitations

- Traffic is synthetic (diurnal shape plus jitter), and services are fictional.
- Inhibition is graded by a strict simulator of Alertmanager's semantics, not by the Alertmanager process.
  Unsupported constructs fail closed.
- Seven alert templates host the defects, not a large upstream corpus.
- The routing chain case uses the agent's static labels, not labels observed in `ALERTS`.
- Frontier agents solved every run on the two easy tasks measured (af-001, af-013), so the easy tier likely
  saturates for them.
- **Contamination:** SkillHub skills (and any skill-augmented agent) already contain near-identical burn-rate
  recipes, and this repo ships solutions. Evaluate on a private-seed build, and report whether the agent had
  `SKILL.md`.
- English only.
- Expert tier (v0.2 candidates): hidden scenarios replay through promtool, whose samples sit exactly on the
  evaluation ticks. A target that leaves service discovery is marked stale on the next sample; a real
  Prometheus 3.5 server writes those stale markers about two scrape intervals after the last scrape, so an
  `absent()` Down alert pages 1-2 minutes later in production than in the replay. The graders accept
  `absent()` Down alerts with `for` up to 10m, which in production page 11-13 minutes after the targets went
  away, against the 10 minutes the task README asks for. Window corners that depend on the scrape and
  evaluation phases are graded as promtool sees them: `[90s]` on a 1m scrape passes in the replay, but on a
  real server it is empty for about half of all phase pairs.
- Expert tier: when a counter stops (consumer offsets, requests), a stated deadline is counted from the last
  sample that still increased, the earliest the stop can have happened. When a series goes away, it is counted
  from the minute its stale marker is written (see above for how that differs from a real server).
- Expert tier: a routing fix that sits below an unfixed catch-all route earns nothing on its own route checks,
  because the catch-all takes the alerts first. That is Alertmanager's first-match routing, kept on purpose: a
  run that misses the catch-all also loses the route tickets behind it.
- Expert tier: `cluster` is a target label on `up` only; request counters and histograms in the hidden data
  don't carry it.
- Expert tier: every hidden scenario holds what the world's targets export at healthy levels: request counters for
  every service whose pods are up; the histograms, pool gauges and cAdvisor series the repo's rules read about those
  services; the ingress controller's request counters (`nginx_ingress_controller_requests`) for every HTTP service;
  and the exporter series a ticket needs. Targets that go down or leave discovery get stale markers. The ingress
  counts the same requests and status codes as the app's own counters, and while a service has no target up it
  answers 502 (targets down) or 503 (none left) for the traffic clients keep sending. Not simulated: the ingress
  latency histogram and config-reload gauge, and fleet exporters (node, Postgres, Redis, Kafka, JVM) beyond those a
  ticket needs. The repo's fleet alerts and its ingress latency and reload alerts therefore never fire in the replay,
  and an alert that reads only those series cannot be graded on them. A scenario still starts without history: at
  its first evaluation `rate()` has no value yet, so a floor rule that reads a missing counter as zero traffic sees
  zero there, and its `for` is what keeps it quiet.
- Expert tier: an untouched alert counts as intact when it behaves the same. Its text may change (formatting,
  operand order, a static label equal to what its expression already yields, an identical second copy) as long as
  its `for`, thresholds, windows and functions stay and it fires with the same labels at every minute of every
  hidden scenario. An alert that fires in none of them can only be kept as written.
- Expert tier: an alert needs the labels the task's README names, with their values; it may carry others, which are
  judged by where the alert is delivered, not by their presence.

## Repository layout

`tasks/` (Harbor tasks: this repository holds the 30 core tasks; the Hugging Face dataset adds the 45 expert `afx-*`
tasks that `registry.json` also lists, so run those with `--repo https://huggingface.co/datasets/openenvforge/3ambench@v0.2.0`
or build them with `alertforge expert`) · `registry.json` (`3ambench`, `3ambench-mini`, `3ambench-expert`,
`3ambench-expert-lb`, all 0.2.0) · `manifest.jsonl` (one row per core task) · `partial/`, `null/` (reference
policies) · `skill/`, `workflows/` · `generator/generate.py` + `src/alertforge/` (the generator and grader source;
`src/alertforge/expert/` builds the expert tier) · `openenv/alertforge_env/` (OpenEnv server) · `space/` (static
replay viewer) · `scripts/` (`export_runs.py` and `leaderboard.py` produce the replay data and the leaderboard tables,
from a GitHub clone; the dataset does not ship `space/`) · `tests/` (pytest).

## License and citation

Apache-2.0; see `NOTICE.md` for attribution of adapted community rules (CC BY 4.0 / Apache-2.0).

```bibtex
@misc{3ambench2026,
  title  = {3amBench: a behaviorally graded, dense-reward RL environment for Prometheus alerting as code},
  year   = {2026},
  note   = {Harbor dataset and OpenEnv environment; generator package alertforge v0.2.0}
}
```
