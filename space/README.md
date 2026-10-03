---
title: 3amBench Replay
emoji: 🚨
colorFrom: indigo
colorTo: red
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
short_description: Step through agent runs on 3amBench, check by check
tags:
  - rl-environment
  - harbor
  - openenv
  - prometheus
  - alertmanager
  - sre
---

# 3amBench Replay

A static viewer for runs on [3amBench](https://github.com/Devesh-Maheshwari/3ambench), an RL environment where an
agent owns Prometheus alerting rules and Alertmanager routing as code, graded by replaying hidden outages through
`promtool` and `amtool`.

- **Two tiers**, switched at the top of the page: **Standard** (the 30 v0.1.1 tasks: five workflows at easy, medium
  and hard) and **Expert** (v0.2: a week of a team's observability queue on a large legacy repo, in five families).
- **Leaderboard** per tier, grouped by model family, one row per agent and model: runs, tasks, mean reward and solved
  runs with a small solve-rate bar. Standard rows add the per-category means (alerts, repairs, recording, routing,
  inhibition); Expert rows add *diagnosis* and *routine*, the weighted mean requirement scores over the symptom
  tickets and over the rest of the queue. The Expert board counts only the leaderboard tasks.
- **Replay**: pick a task and a run, then play or step through it: the agent's tool calls, file diffs and tool
  output, and the cumulative reward curve when the run has one. Standard runs show which checks pass after each step
  ("fires during a sustained outage", "stays silent during a short spike", "routes to pagerduty-payments", ...).
  Expert runs show the queue instead: every ticket and routine request with its score, solved, partly solved or
  not, with a short reason for the ones that are not, plus what the run broke among the rules and routes it should
  have left alone.

Everything is read from `data/runs.json`, produced by `scripts/export_runs.py` in the main repo:

- `reference: oracle / partial / careless` are scripted OpenEnv episodes (no model) from
  `scripts/record_reference_runs.py`. Their per-step rewards are the environment's own (parity mode: they sum to the
  Harbor reward).
- `harbor: …` runs come from Harbor job directories. Harbor reports a terminal reward; when a trial has an ATIF
  trajectory, the agent's file edits are replayed through the same grader to draw a per-step curve, and each run
  says whether that replay was exact. Expert runs get a curve only when every edit replays exactly; the rest show
  the final grade with its per-requirement breakdown.
- **Re-graded** runs were scored again from their saved workspace with the task's current hidden tests; the page
  shows the new reward next to the one Harbor reported at the time.
- **Invalid** runs (for example a provider rate limit that ended the run early) stay viewable, are marked as such,
  and are left out of the leaderboard.

Run the environment yourself:

```bash
docker run -p 8000:8000 -e ENABLE_WEB_INTERFACE=true ghcr.io/devesh-maheshwari/3ambench-env:0.2.0
# then open http://localhost:8000/web
```

Dataset: [openenvforge/3ambench](https://huggingface.co/datasets/openenvforge/3ambench).
