#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "release input verification failed: $1" >&2
  exit 1
}

: "${SOURCE_SHA:?SOURCE_SHA is required}"
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "SOURCE_SHA is not a 40-character commit SHA"
[[ "$(git rev-parse HEAD)" == "$SOURCE_SHA" ]] || fail "source SHA does not match checkout"
git diff --quiet || fail "tracked checkout has unstaged changes"
git diff --cached --quiet || fail "tracked checkout has staged changes"
untracked_inputs="$(git ls-files --others --exclude-standard)"
[[ -z "$untracked_inputs" ]] || fail "checkout has untracked source or build inputs: $untracked_inputs"

action_count=0
while IFS= read -r action_ref; do
  [[ "$action_ref" =~ @[0-9a-f]{40}$ ]] || fail "workflow action is not pinned: $action_ref"
  action_count=$((action_count + 1))
done < <(
  find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) -exec \
    awk '
      /^[[:space:]-]*uses:[[:space:]]*[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+@[A-Za-z0-9._-]+([[:space:]]|$)/ {
        sub(/^[[:space:]-]*uses:[[:space:]]*/, "")
        sub(/[[:space:]].*$/, "")
        print
      }
    ' {} +
)
(( action_count > 0 )) || fail "no workflow actions found"

base_image_count=0
while IFS= read -r image_ref; do
  [[ "$image_ref" =~ @sha256:[0-9a-f]{64}$ ]] || fail "Docker base image is not digest-pinned: $image_ref"
  base_image_count=$((base_image_count + 1))
done < <(
  awk '$1 == "FROM" { for (i = 2; i <= NF; i++) if ($i !~ /^--/) { print $i; break } }' Dockerfile
)
(( base_image_count > 0 )) || fail "no Docker base images found"

for required in Dockerfile pyproject.toml uv.lock; do
  [[ -f "$required" ]] || fail "required release input is missing: $required"
done

jq -n \
  --arg source_sha "$SOURCE_SHA" \
  --argjson action_count "$action_count" \
  --argjson base_image_count "$base_image_count" \
  '{status: "pass", source_sha: $source_sha, action_count: $action_count,
    base_image_count: $base_image_count, immutable_refs: true,
    tracked_checkout: "clean", untracked_inputs: "clean"}'
