#!/usr/bin/env bash
# Upload space/ (the static replay viewer + data/runs.json) to a Hugging Face Space.
#   SPACE_REPO=openenvforge/3ambench-replay scripts/push_space.sh
# Needs `hf auth login` with write access to the org. Regenerate data first with scripts/export_runs.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPACE_REPO="${SPACE_REPO:-openenvforge/3ambench-replay}"
[ -f "$ROOT/space/data/runs.json" ] || { echo "missing space/data/runs.json; run scripts/export_runs.py first"; exit 1; }
hf repo create "$SPACE_REPO" --repo-type space --space-sdk static --exist-ok
# upload_folder does not re-create the repo (hf upload does, defaulting to a paid Gradio SDK -> 402)
python3 -c 'import sys; from huggingface_hub import HfApi; HfApi().upload_folder(repo_id=sys.argv[1], folder_path=sys.argv[2], repo_type="space", commit_message=sys.argv[3])' \
  "$SPACE_REPO" "$ROOT/space" "${MSG:-Update replay data}"
echo "https://huggingface.co/spaces/$SPACE_REPO"
