#!/usr/bin/env bash
# Record real model runs on a task subset with Harbor (via scripts/harbor_local.sh), then re-export
# space/data/runs.json so the replay Space shows them. This spends model credits; nothing runs by default.
#
#   scripts/record_agent_runs.sh -a <agent> -m <model> [-k attempts] [-t af-001,af-009,...] [-n concurrent]
#
# Agents and auth (Harbor reads these from the environment):
#   codex        subscription: ~/.codex/auth.json (run `codex login` once); this script sets
#                CODEX_FORCE_AUTH_JSON=1. Use AUTH=apikey to use OPENAI_API_KEY instead.
#                  scripts/record_agent_runs.sh -a codex -m openai/<model> -k 2
#   claude-code  subscription token from `claude setup-token`, exported as CLAUDE_CODE_OAUTH_TOKEN; this script
#                sets CLAUDE_FORCE_OAUTH=1 so an API key in the environment is not used instead.
#                  CLAUDE_CODE_OAUTH_TOKEN=... scripts/record_agent_runs.sh -a claude-code -m <model> -k 2
#   terminus-2   any LiteLLM model id plus that provider's API key (OPENAI_API_KEY, OPENROUTER_API_KEY,
#                GEMINI_API_KEY, ...).
#                  OPENROUTER_API_KEY=... scripts/record_agent_runs.sh -a terminus-2 -m openrouter/<vendor>/<model>
#
# Default tasks: one per workflow across all tiers (af-001 easy, af-009 medium, af-013 easy, af-017 hard,
# af-021 medium, af-029 hard) - the same tasks as the reference runs, so the leaderboard compares like with like.
#
# harbor_local.sh drops the tasks' `no-network` override so it runs on Docker Desktop (macOS). With network
# access an agent could fetch the public solutions, so treat these as local, not leaderboard-grade, results;
# on a Linux Docker host run `harbor run -p tasks ...` directly to keep the restriction.
#
# Other env: HARBOR (default: harbor on PATH), AF_PROMTOOL / AF_AMTOOL (pinned tools for the export replay),
# PYTHON (a Python with PyYAML; default python3), JOBS_DIR (default dist/replay/agent-jobs).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT="" MODEL="" ATTEMPTS=1 CONCURRENT=2
TASKS="af-001,af-009,af-013,af-017,af-021,af-029"
usage() { sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while getopts "a:m:k:t:n:h" o; do
  case "$o" in
    a) AGENT="$OPTARG" ;; m) MODEL="$OPTARG" ;; k) ATTEMPTS="$OPTARG" ;;
    t) TASKS="$OPTARG" ;; n) CONCURRENT="$OPTARG" ;; h) usage 0 ;; *) usage 1 ;;
  esac
done
[ -n "$AGENT" ] && [ -n "$MODEL" ] || { echo "need -a <agent> and -m <model>" >&2; usage 1; }

case "$AGENT" in
  codex)
    if [ "${AUTH:-subscription}" = "apikey" ]; then
      : "${OPENAI_API_KEY:?AUTH=apikey needs OPENAI_API_KEY}"
    else
      [ -f "$HOME/.codex/auth.json" ] || { echo "no ~/.codex/auth.json; run 'codex login' first" >&2; exit 1; }
      export CODEX_FORCE_AUTH_JSON=1
    fi ;;
  claude-code)
    : "${CLAUDE_CODE_OAUTH_TOKEN:?export CLAUDE_CODE_OAUTH_TOKEN (from 'claude setup-token')}"
    export CLAUDE_FORCE_OAUTH=1 ;;
  terminus-2)
    env | grep -qE '^[A-Z_]*API_KEY=' || echo "warning: no *_API_KEY in the environment for $MODEL" >&2 ;;
  *) echo "unsupported agent '$AGENT' (codex | claude-code | terminus-2)" >&2; exit 1 ;;
esac

# stage the chosen tasks in one directory so they run as a single Harbor job
STAGE="$(mktemp -d)/tasks"; mkdir -p "$STAGE"
IFS=',' read -r -a PREFIXES <<< "$TASKS"
for p in "${PREFIXES[@]}"; do
  found=0
  for d in "$ROOT"/tasks/"$p"*/; do [ -f "$d/task.toml" ] && { cp -R "${d%/}" "$STAGE/"; found=1; }; done
  [ "$found" = 1 ] || { echo "no task matches '$p'" >&2; exit 1; }
done

JOBS_DIR="${JOBS_DIR:-$ROOT/dist/replay/agent-jobs}"
mkdir -p "$JOBS_DIR"
JOB="${AGENT}__$(echo "$MODEL" | tr '/:' '__')__$(date +%Y%m%d-%H%M%S)"
echo "job $JOB: $(ls "$STAGE" | wc -l | tr -d ' ') tasks x $ATTEMPTS attempts -> $JOBS_DIR/$JOB" >&2
"$ROOT/scripts/harbor_local.sh" "$STAGE" -- -a "$AGENT" -m "$MODEL" -k "$ATTEMPTS" -n "$CONCURRENT" \
  -o "$JOBS_DIR" --job-name "$JOB" -y

# re-export everything the Space shows: reference traces, local Harbor reference jobs, all agent jobs
SOURCES=(--harbor "$JOBS_DIR")
[ -d "$ROOT/dist/replay/harbor-jobs" ] && SOURCES+=("$ROOT/dist/replay/harbor-jobs")
[ -d "$ROOT/dist/replay/traces" ] && SOURCES=(--traces "$ROOT/dist/replay/traces" "${SOURCES[@]}")
"${PYTHON:-python3}" "$ROOT/scripts/export_runs.py" "${SOURCES[@]}" --out "$ROOT/space/data/runs.json"
