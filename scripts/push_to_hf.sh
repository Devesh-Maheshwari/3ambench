#!/usr/bin/env bash
# Stage (and, only with CONFIRM=1, push) the 3amBench dataset and the OpenEnv Space to Hugging Face.
# NOT executed by the build. Usage:
#   HF_REPO=<you>/3ambench scripts/push_to_hf.sh                  # dry run: stages into dist/ (or $STAGE), uploads nothing
#   HF_REPO=<you>/3ambench CONFIRM=1 scripts/push_to_hf.sh        # upload + tag $VERSION (default v0.2.0)
#   SPACE_REPO=<you>/3ambench-env CONFIRM=1 scripts/push_to_hf.sh # also push the OpenEnv Space
# The expert tasks are staged from EXPERT_DIR (default dist/v02-candidates, written by `alertforge expert`) next to
# the core tasks, with their reference policies in partial/ and null/. The expert manifest and gate log are not
# staged, and the card ids in each expert task's tests/spec.json are stripped here, so the task files don't name the
# defect catalog (the ids are not secret: src/alertforge/expert/ defines them).
# Everything else is staged from what git would commit (tracked files plus untracked files that .gitignore and
# .git/info/exclude don't exclude), so local-only work in the tree is never uploaded.
set -euo pipefail
: "${HF_REPO:?set HF_REPO=<hf-user-or-org>/3ambench}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-v0.2.0}"
EXPERT_DIR="${EXPERT_DIR:-$ROOT/dist/v02-candidates}"
STAGE="${STAGE:-$ROOT/dist}"   # where the staged copies go (dist/ is gitignored)
DS="$STAGE/hf-dataset"
SP="$STAGE/hf-space"
[ -d "$EXPERT_DIR/tasks" ] || { echo "refusing: no expert tasks in $EXPERT_DIR/tasks (build them or set EXPERT_DIR)"; exit 1; }
git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 || { echo "refusing: $ROOT is not a git checkout (staging follows git)"; exit 1; }
rm -rf "$DS" "$SP"; mkdir -p "$DS" "$SP"

stage() {   # stage DEST PATH...: copy what git would commit under PATH... into DEST, keeping relative paths and modes
  local dest="$1"; shift
  local list; list="$(mktemp)"
  git -C "$ROOT" ls-files -z --cached --others --exclude-standard -- "$@" > "$list"
  [ -s "$list" ] || { rm -f "$list"; echo "refusing: nothing to stage for $*"; exit 1; }
  (cd "$ROOT" && tar --null -T "$list" -cf -) | tar -xf - -C "$dest"
  rm -f "$list"
}

# ---- dataset repo: tasks/ + registry.json + manifest + card (the layout `harbor run --repo` expects)
stage "$DS" README.md LICENSE NOTICE.md registry.json manifest.jsonl .gitignore
stage "$DS" tasks partial null skill workflows openenv scripts tests docs   # the card references all of these
# generator: same relative layout as the source tree (generate.py imports ../src, world.py reads ../../workflows)
stage "$DS" src pyproject.toml generator/generate.py
[ -d "$ROOT/assets" ] && stage "$DS" assets
# expert tier: tasks, partial and null policies, one afx-* directory each, next to the core ones
WANT="$(find "$EXPERT_DIR/tasks" -mindepth 1 -maxdepth 1 -type d -name 'afx-*' | wc -l | tr -d ' ')"
for kind in tasks partial null; do
  n=0
  for d in "$EXPERT_DIR/$kind"/afx-*/; do
    if [ -f "$d/solve.sh" ] || [ -f "$d/task.toml" ]; then cp -R "${d%/}" "$DS/$kind/"; n=$((n + 1)); fi
  done
  [ "$n" -gt 0 ] && [ "$n" -eq "$WANT" ] || { echo "refusing: $n of $WANT afx-* directories usable in $EXPERT_DIR/$kind"; exit 1; }
done
find "$DS" \( -name '__pycache__' -o -name '.pytest_cache' -o -name '*.egg-info' \) -type d -prune -exec rm -rf {} +
find "$DS" \( -name '.DS_Store' -o -name '*.pyc' \) -delete
printf '*.png filter=lfs diff=lfs merge=lfs -text\n*.parquet filter=lfs diff=lfs merge=lfs -text\n' > "$DS/.gitattributes"
if find "$DS" -type l | grep -q .; then echo "refusing: symlinks in staging (Harbor rejects them)"; exit 1; fi

# card ids: drop `defect` from each expert requirement, written as the generator writes spec.json, and re-sum it
python3 - "$DS/tasks" <<'PY' || { echo "refusing: could not strip the card ids"; exit 1; }
import glob, hashlib, json, os, sys
for spec_p in sorted(glob.glob(os.path.join(sys.argv[1], "afx-*", "tests", "spec.json"))):
    with open(spec_p) as fh:
        spec = json.load(fh)
    if not any("defect" in r for r in spec.get("requirements", [])):
        continue
    for r in spec["requirements"]:
        r.pop("defect", None)
    data = (json.dumps(spec, separators=(",", ":")) + "\n").encode()
    with open(spec_p, "wb") as fh:
        fh.write(data)
    sums_p = os.path.join(os.path.dirname(spec_p), "checksums.sha256")
    with open(sums_p) as fh:
        lines = fh.read().splitlines()
    hits = [i for i, ln in enumerate(lines) if ln.endswith("  spec.json")]
    if len(hits) != 1:
        sys.exit(f"{sums_p}: expected one spec.json line, found {len(hits)}")
    lines[hits[0]] = f"{hashlib.sha256(data).hexdigest()}  spec.json"
    with open(sums_p, "w") as fh:
        fh.write("\n".join(lines) + "\n")
PY
if grep -l '"defect"' "$DS"/tasks/afx-*/tests/spec.json; then echo "refusing: card ids left in the specs above"; exit 1; fi
if command -v sha256sum >/dev/null; then SHA=(sha256sum -c --quiet); else SHA=(shasum -a 256 -c --quiet); fi
for t in "$DS"/tasks/afx-*/; do
  (cd "$t/tests" && "${SHA[@]}" checksums.sha256 >/dev/null) || { echo "refusing: checksum mismatch in ${t}tests"; exit 1; }
done
# every task the registry names has to be staged
python3 - "$DS" <<'PY' || { echo "refusing: registry.json names tasks that are not staged"; exit 1; }
import json, os, sys
ds, missing = sys.argv[1], 0
for d in json.load(open(os.path.join(ds, "registry.json"))):
    gone = [t["path"] for t in d["tasks"] if not os.path.isfile(os.path.join(ds, t["path"], "task.toml"))]
    missing += len(gone)
    print(f"  {d['name']}@{d['version']}: {len(d['tasks'])} tasks" + (f", not staged: {gone[:3]}" if gone else ""))
sys.exit(1 if missing else 0)
PY
DEV="$( (grep -l '^version = ".*-dev"' "$DS"/tasks/afx-*/task.toml || true) | wc -l | tr -d ' ')"
[ "$DEV" = "0" ] || echo "warning: $DEV expert task.toml files still carry a -dev version; rebuild them with the release version"

# ---- OpenEnv Space: env package at the root, generator + released tasks vendored for the Docker build
mkdir -p "$SP/.env-src" "$SP/alertforge_src"
stage "$SP/.env-src" openenv/alertforge_env && cp -R "$SP/.env-src/openenv/alertforge_env/." "$SP"/ && rm -rf "$SP/.env-src"
stage "$SP/alertforge_src" src workflows pyproject.toml README.md
stage "$SP" tasks
sed -e 's#^COPY pyproject.toml README.md /app/alertforge/#COPY alertforge_src /app/alertforge#' \
    -e '/^COPY src \/app\/alertforge\/src/d' -e '/^COPY workflows \/app\/alertforge\/workflows/d' \
    -e 's#^COPY openenv/alertforge_env /app/env/alertforge_env#COPY . /app/env/alertforge_env#' \
    "$ROOT/openenv/alertforge_env/server/Dockerfile" > "$SP/Dockerfile"
find "$SP" \( -name '__pycache__' -o -name '*.egg-info' \) -type d -prune -exec rm -rf {} +

python3 "$ROOT/scripts/redact.py" --check "$DS" && python3 "$ROOT/scripts/redact.py" --check "$SP" || { echo "refusing: secrets or local paths in staging"; exit 1; }
CORE_N="$(find "$DS/tasks" -maxdepth 2 -path '*/af-[0-9]*/task.toml' | wc -l | tr -d ' ')"
EXPERT_N="$(find "$DS/tasks" -maxdepth 2 -path '*/afx-*/task.toml' | wc -l | tr -d ' ')"
echo "staged dataset at $DS ($CORE_N core + $EXPERT_N expert tasks) and Space at $SP"
if [ "${CONFIRM:-0}" != "1" ]; then
  echo "dry run only. Re-run with CONFIRM=1 to upload to https://huggingface.co/datasets/$HF_REPO"; exit 0
fi
[ "$DEV" = "0" ] || { echo "refusing to upload: $DEV expert tasks carry a -dev version"; exit 1; }
hf repo create "$HF_REPO" --repo-type dataset || true
hf upload "$HF_REPO" "$DS" . --repo-type dataset --commit-message "3amBench $VERSION"
hf repo tag create "$HF_REPO" "$VERSION" --repo-type dataset
# The Hub tag v0.1.1 was created on a revision that still held the v0.1.0 task files, and the core tasks change in
# 0.2.0 (reserved .example domains), so it is not re-pointed: delete it by hand once this upload is verified
# (CHANGELOG 0.2.0, "Hugging Face tags").
echo "then: hf repo tag delete $HF_REPO v0.1.1 --repo-type dataset -y"
if [ -n "${SPACE_REPO:-}" ]; then
  (cd "$SP" && openenv push --repo-id "$SPACE_REPO")
fi
# clean-cache verification, as a user would run it:
echo "verify: rm -rf ~/.cache/harbor/tasks && uvx harbor run --repo https://huggingface.co/datasets/$HF_REPO@$VERSION -d 3ambench@${VERSION#v} -a oracle -n 4"
echo "verify: uvx harbor run --repo https://huggingface.co/datasets/$HF_REPO@$VERSION -d 3ambench-expert-lb@${VERSION#v} -a oracle -n 4"
