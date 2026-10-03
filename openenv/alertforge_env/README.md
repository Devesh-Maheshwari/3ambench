---
title: 3amBench AlertForge Env
emoji: 📟
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 8000
base_path: /web
pinned: false
license: apache-2.0
tags: [openenv, rl-environment, harbor, prometheus, alertmanager, sre, dense-reward]
---

# 3amBench / AlertForge: OpenEnv server

Multi-turn version of the [3amBench Harbor dataset](https://huggingface.co/datasets/openenvforge/3ambench): the agent edits a Prometheus/Alertmanager
repo with tools, and every step is graded by the same `grade.py` Harbor uses (promtool + amtool replays of hidden
outage scenarios).

## Actions
`{"tool": ...}` with one of `list_files`, `read_file{path}`, `write_file{path, content}`,
`replace_in_file{path, old, new}`, `run_checks`, `route_test{labels}`, `submit`.

## Reset
`reset(seed, split="train"|"heldout"|"public", index=None, workflow=None, tier=None)`
- `train`: a fresh task generated in-process (unlimited instances; use this for training).
- `heldout`: generated from the server-side secret `AF_HELDOUT_SEED`.
- `public`: the 30 released Harbor tasks (evaluation only).

## Reward (`AF_REWARD_MODE`)
| mode | per step | terminal | sum |
|---|---|---|---|
| `parity` (default) | `0.4·ΔΦ` | `+0.6·outcome` | exactly the Harbor `reward` |
| `shaped` | `0.3·ΔΦ − 0.002 − 0.01·invalid` (cost cap 0.1) | `+0.6·outcome` | |
| `outcome` | 0 | Harbor `reward` | Harbor `reward` |

On `heldout` and `public`, the mode is forced to `outcome` and `metadata.phi` is `null`, so the reward stream
cannot be used to probe the hidden checks. (openenv-core 0.3 does not send `metadata` over the wire at all;
`phi`/`delta_phi` are available in-process for trainer logging.)

## Run locally
```bash
docker build -t alertforge-env -f openenv/alertforge_env/server/Dockerfile .   # from env/alertforge
docker run -p 8000:8000 alertforge-env
```
```python
from alertforge_env.client import AlertForgeEnv
with AlertForgeEnv(base_url="http://localhost:8000").sync() as env:
    obs = env.reset(seed=1, split="train", workflow="slo-onboarding", tier="easy")
    obs = env.step({"tool": "read_file", "path": "README.md"})
```
