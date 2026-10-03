#!/usr/bin/env bash
# Run Harbor on a host that cannot enforce `no-network` (for example Docker Desktop on macOS, whose VM
# kernel lacks CONFIG_NFT_FIB_INET, so Harbor refuses the task). Copies the tasks to a temp dir with
# the `[agent] network_mode = "no-network"` override removed, then runs `harbor run` on the copy.
#
# NOT leaderboard-safe: with network access an agent can download the public solutions. Use it for
# oracle / nop / partial smoke runs and local development only.
#
#   scripts/harbor_local.sh <task-dir | tasks-dir> [--partial] -- <harbor run args>
#   scripts/harbor_local.sh tasks/af-001-slo-onboarding-easy-s1 -- -a oracle -o /abs/jobs --job-name x
#   --partial swaps solution/solve.sh for $PARTIAL_DIR/<task>/solve.sh (run with -a oracle). PARTIAL_DIR defaults
#   to the partial/ next to the tasks directory: partial/ for the core tier, dist/v02-candidates/partial for the
#   expert candidates.
set -euo pipefail
HARBOR="${HARBOR:-harbor}"
SRC="${1:?task or tasks dir}"; shift
PARTIAL=0
if [ "${1:-}" = "--partial" ]; then PARTIAL=1; shift; fi
[ "${1:-}" = "--" ] && shift
TMP="$(mktemp -d)/tasks"
mkdir -p "$TMP"
if [ -f "$SRC/task.toml" ]; then TASKS=("$SRC"); TDIR="$(cd "$SRC/.." && pwd)"; else TASKS=("$SRC"/*/); TDIR="$(cd "$SRC" && pwd)"; fi
PARTIAL_DIR="${PARTIAL_DIR:-$(dirname "$TDIR")/partial}"
for t in "${TASKS[@]}"; do
  name="$(basename "$t")"
  cp -R "$t" "$TMP/$name"
  sed -i.bak '/^network_mode = "no-network"$/d' "$TMP/$name/task.toml" && rm -f "$TMP/$name/task.toml.bak"
  if [ "$PARTIAL" = 1 ]; then
    [ -f "$PARTIAL_DIR/$name/solve.sh" ] || { echo "no partial policy at $PARTIAL_DIR/$name/solve.sh" >&2; exit 2; }
    cp "$PARTIAL_DIR/$name/solve.sh" "$TMP/$name/solution/solve.sh"
  fi
done
TARGET="$TMP"
[ "${#TASKS[@]}" = 1 ] && TARGET="$TMP/$(basename "${TASKS[0]}")"
echo "running on local copy: $TARGET" >&2
exec "$HARBOR" run -p "$TARGET" "$@"
