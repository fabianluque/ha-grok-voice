#!/usr/bin/env bash
# Decide the add-on version, test it, then commit the bump on main.
# Image publish and the GitHub Release run only after this exits 0 with
# released=true. Tag pushes are not done here: a tag created with
# GITHUB_TOKEN would not start a follow-up workflow.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

: "${EVENT_NAME:?}"
: "${RELEASE_BUMP:?}"
: "${GITHUB_OUTPUT:?}"
: "${GITHUB_SHA:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_REF:?}"
: "${RUNNER_TEMP:?}"

if [ "$GITHUB_REF" != "refs/heads/main" ]; then
  echo "Run Release from the main branch (selected ref: ${GITHUB_REF})." >&2
  exit 1
fi

# Actions may leave HEAD detached. Point main at this commit without moving
# backward to an older SHA, then fetch tags for the previous release.
if [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
  git switch -C main
fi
git fetch origin --tags --force

set_output() {
  printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
}

parent_args=()
if [ "$EVENT_NAME" = "push" ] && [ -n "${PUSH_BEFORE:-}" ] && [[ "$PUSH_BEFORE" != 00000000* ]]; then
  if git cat-file -e "${PUSH_BEFORE}:grok_voice_agent/config.yaml" 2>/dev/null; then
    git show "${PUSH_BEFORE}:grok_voice_agent/config.yaml" > "$RUNNER_TEMP/parent-config.yaml"
    parent_args=(--parent-config "$RUNNER_TEMP/parent-config.yaml")
  fi
fi

git tag -l 'v*' > "$RUNNER_TEMP/tags.txt"

decision="$(
  python3 .github/scripts/release_version.py decide \
    --event "$EVENT_NAME" \
    --bump "$RELEASE_BUMP" \
    --config grok_voice_agent/config.yaml \
    --tags-file "$RUNNER_TEMP/tags.txt" \
    "${parent_args[@]}"
)"
release_flag="$(printf '%s\n' "$decision" | sed -n '1p')"
version="$(printf '%s\n' "$decision" | sed -n '2p')"
reason="$(printf '%s\n' "$decision" | sed -n '3p')"
echo "$reason"

if [ "$release_flag" != "true" ]; then
  # current + existing tag, but the GitHub Release was never created (image
  # push succeeded, release step failed). Finish that publish.
  if [ "$EVENT_NAME" = "workflow_dispatch" ] && [ "$RELEASE_BUMP" = "current" ] \
    && [[ "$reason" == *"already exists"* ]] \
    && ! gh release view "v${version}" >/dev/null 2>&1; then
    echo "Tag v${version} exists without a GitHub Release. Finishing publish."
    release_flag=true
  else
    set_output released false
    set_output version "$version"
    set_output sha "$(git rev-parse HEAD)"
    exit 0
  fi
fi

python3 .github/scripts/release_version.py set-version \
  --config grok_voice_agent/config.yaml \
  --version "$version"

previous_tag="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n 1 || true)"
note_args=(-f "tag_name=v${version}" -f "target_commitish=${GITHUB_SHA}")
since=""
if [ -n "$previous_tag" ]; then
  note_args+=(-f "previous_tag_name=${previous_tag}")
  since="$(git log -1 --format=%cI "$previous_tag")"
fi

gh api --method POST \
  -H "Accept: application/vnd.github+json" \
  "/repos/${GITHUB_REPOSITORY}/releases/generate-notes" \
  "${note_args[@]}" \
  --jq .body > "$RUNNER_TEMP/notes.md"

if ! gh issue list --state closed --limit 100 --json number,title,closedAt > "$RUNNER_TEMP/issues.json"; then
  echo '[]' > "$RUNNER_TEMP/issues.json"
fi

python3 .github/scripts/release_version.py changelog \
  --version "$version" \
  --notes "$RUNNER_TEMP/notes.md" \
  --issues "$RUNNER_TEMP/issues.json" \
  --since "$since" \
  --date "$(date -u +%F)" \
  --changelog CHANGELOG.md

python3 -m pip install --upgrade pip
python3 -m pip install -r grok_voice_agent/requirements.txt pytest aiohttp
python3 -m pytest
npm --prefix client ci
npm --prefix client test
npm --prefix client run build

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add grok_voice_agent/config.yaml CHANGELOG.md
if git diff --cached --quiet; then
  sha="$(git rev-parse HEAD)"
else
  git commit -m "Release v${version}"
  git push origin HEAD:main
  sha="$(git rev-parse HEAD)"
fi

set_output released true
set_output version "$version"
set_output sha "$sha"
