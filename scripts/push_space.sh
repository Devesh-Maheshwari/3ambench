#!/usr/bin/env bash
# Upload space/ (the static replay viewer + data/runs.json) to a Hugging Face Space.
#   SPACE_REPO=openenvforge/3ambench-replay scripts/push_space.sh             # dry run: checks, uploads nothing
#   SPACE_REPO=openenvforge/3ambench-replay CONFIRM=1 scripts/push_space.sh   # upload
# Needs `hf auth login` with write access to the org. Regenerate data first with scripts/export_runs.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPACE_REPO="${SPACE_REPO:-openenvforge/3ambench-replay}"
[ -f "$ROOT/space/data/runs.json" ] || { echo "missing space/data/runs.json; run scripts/export_runs.py first"; exit 1; }
python3 "$ROOT/scripts/redact.py" --check "$ROOT/space" || { echo "refusing to upload: fix the findings above"; exit 1; }
if [ "${CONFIRM:-0}" != "1" ]; then
  echo "dry run: would upload $(find "$ROOT/space" -type f ! -name .DS_Store | wc -l | tr -d ' ') files from $ROOT/space to https://huggingface.co/spaces/$SPACE_REPO; re-run with CONFIRM=1"
  exit 0
fi
hf repo create "$SPACE_REPO" --repo-type space --space-sdk static --exist-ok
# upload_folder does not re-create the repo (hf upload does, defaulting to a paid Gradio SDK -> 402)
python3 -c 'import sys; from huggingface_hub import HfApi; HfApi().upload_folder(repo_id=sys.argv[1], folder_path=sys.argv[2], repo_type="space", commit_message=sys.argv[3], ignore_patterns=[".DS_Store", "**/.DS_Store"])' \
  "$SPACE_REPO" "$ROOT/space" "${MSG:-Update replay data}"
echo "https://huggingface.co/spaces/$SPACE_REPO"
