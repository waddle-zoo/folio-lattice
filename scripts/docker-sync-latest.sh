#!/usr/bin/env bash

set -u

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
remote="${FOLIO_SYNC_REMOTE:-origin}"
branch="${FOLIO_SYNC_BRANCH:-main}"
interval="${FOLIO_SYNC_INTERVAL:-60}"
once=false

usage() {
  printf '%s\n' "Usage: $0 [--once]"
  printf '%s\n' "Fetch origin/main and rebuild the local Docker stack when the SHA changes."
}

while (($# > 0)); do
  case "$1" in
    --once)
      once=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! [[ "$interval" =~ ^[1-9][0-9]*$ ]]; then
  printf 'FOLIO_SYNC_INTERVAL must be a positive integer (seconds)\n' >&2
  exit 2
fi

control_port="${FOLIO_HOST_PORT:-8000}"
renderer_port="${FOLIO_RENDER_HOST_PORT:-8001}"
last_built=""

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

wait_for_health() {
  local url="$1"
  local attempts="${2:-30}"
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS --max-time 5 "$url" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

sync_once() {
  cd "$repo_root" || return 1

  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    log "worktree is not clean; refusing to pull or rebuild over local changes"
    return 1
  fi

  if ! git fetch --quiet "$remote" "$branch"; then
    log "git fetch failed; will retry"
    return 1
  fi

  target="$(git rev-parse --verify "$remote/$branch")" || {
    log "cannot resolve $remote/$branch; will retry"
    return 1
  }
  current="$(git rev-parse --verify HEAD)" || {
    log "cannot resolve current HEAD; will retry"
    return 1
  }

  if [[ "$current" != "$target" ]]; then
    if ! git merge-base --is-ancestor "$current" "$target"; then
      log "local HEAD diverged from $remote/$branch; refusing an automatic merge"
      return 1
    fi
    log "fast-forwarding to ${target:0:12}"
    if ! git merge --ff-only "$target"; then
      log "fast-forward failed; will retry"
      return 1
    fi
  fi

  if [[ "$last_built" == "$target" ]]; then
    log "already running ${target:0:12}"
    return 0
  fi

  log "building Docker services for ${target:0:12}"
  if ! docker compose up -d --build; then
    log "Docker build or startup failed; will retry"
    return 1
  fi

  if ! wait_for_health "http://127.0.0.1:${control_port}/health"; then
    log "control service health check failed; will retry"
    return 1
  fi
  if ! wait_for_health "http://127.0.0.1:${renderer_port}/health"; then
    log "renderer health check failed; will retry"
    return 1
  fi

  last_built="$target"
  log "Docker is healthy on ${target:0:12}"
  return 0
}

while true; do
  sync_once
  result=$?
  if [[ "$once" == true ]]; then
    exit "$result"
  fi
  sleep "$interval"
done
