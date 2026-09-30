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

- **Leaderboard**: mean reward, solved rate and per-requirement means (alerts, repairs, recording, routing,
  inhibition) per agent and model.
- **Replay**: pick a task and a run, then play or step through it: the agent's tool calls, file diffs and tool
  output, the cumulative reward curve, and which checks pass after each step ("fires during a sustained outage",
  "stays silent during a short spike", "routes to pagerduty-payments", ...).

Everything is read from `data/runs.json`, produced by `scripts/export_runs.py` in the main repo:

- `reference: oracle / partial / careless` are scripted OpenEnv episodes (no model) from
  `scripts/record_reference_runs.py`. Their per-step rewards are the environment's own (parity mode: they sum to the
  Harbor reward).
- `harbor: …` runs come from Harbor job directories. Harbor reports a terminal reward; when a trial has an ATIF
  trajectory, the agent's file edits are replayed through the same grader to draw a per-step curve, and each run
  says whether that replay was exact.

Run the environment yourself:

```bash
docker run -p 8000:8000 -e ENABLE_WEB_INTERFACE=true ghcr.io/devesh-maheshwari/3ambench-env:0.1.1
# then open http://localhost:8000/web
```

Dataset: [openenvforge/3ambench](https://huggingface.co/datasets/openenvforge/3ambench).
