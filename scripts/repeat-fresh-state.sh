#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "fresh-state harness blocked: $1" >&2
  exit 2
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

: "${FOLIO_RELEASE_CANDIDATE_SHA:?FOLIO_RELEASE_CANDIDATE_SHA is required}"
: "${FOLIO_FRESH_STATE_BLOCKERS_PATH:?FOLIO_FRESH_STATE_BLOCKERS_PATH is required}"

candidate_sha="$(git rev-parse HEAD)"
[[ "$FOLIO_RELEASE_CANDIDATE_SHA" == "$candidate_sha" ]] \
  || fail "release candidate SHA does not match checkout"
[[ -z "$(git status --porcelain)" ]] || fail "checkout is dirty"
[[ -s "$FOLIO_FRESH_STATE_BLOCKERS_PATH" ]] || fail "blocker manifest is missing"
jq -e 'type == "array" and length > 0 and all(.[]; .status == "closed")' \
  "$FOLIO_FRESH_STATE_BLOCKERS_PATH" >/dev/null \
  || fail "declared blocker is not closed"

runs="${FOLIO_FRESH_STATE_RUNS:-2}"
[[ "$runs" =~ ^[2-9][0-9]*$ ]] || fail "FOLIO_FRESH_STATE_RUNS must be an integer >= 2"

mode="${FOLIO_FRESH_STATE_MODE:-release}"
[[ "$mode" == release || "$mode" == non-release || "$mode" == non-docker ]] \
  || fail "FOLIO_FRESH_STATE_MODE must be release, non-release, or non-docker"

allow_shared_defaults="${FOLIO_FRESH_STATE_ALLOW_SHARED_DEFAULTS:-false}"
[[ "$allow_shared_defaults" == true || "$allow_shared_defaults" == false ]] \
  || fail "FOLIO_FRESH_STATE_ALLOW_SHARED_DEFAULTS must be true or false"
[[ "$allow_shared_defaults" != true || "$mode" == non-release ]] \
  || fail "shared/default overrides require FOLIO_FRESH_STATE_MODE=non-release"
[[ -n "${FOLIO_FRESH_STATE_PORT_BASE:-}" ]] \
  || fail "FOLIO_FRESH_STATE_PORT_BASE is required and must be unique to this invocation"
port_base="$FOLIO_FRESH_STATE_PORT_BASE"
[[ "$port_base" =~ ^[1-9][0-9]*$ ]] || fail "FOLIO_FRESH_STATE_PORT_BASE must be a positive integer"
(( port_base + 100 + runs <= 65535 )) || fail "fresh-state port range exceeds 65535"

port_is_free() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required for port preflight"
  python3 - "$1" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

if [[ "$mode" != non-docker ]]; then
  command -v docker >/dev/null 2>&1 || fail "Docker is required for this execution mode"
  docker info >/dev/null 2>&1 || fail "Docker daemon is not ready for this execution mode"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose is not available for this execution mode"
fi

evidence="${FOLIO_FRESH_STATE_EVIDENCE:-/tmp/folio-lattice-fl-urj.28.json}"
mkdir -p "$(dirname "$evidence")"
[[ "${1:-}" == "--" ]] || fail "usage: $0 [options] -- command [args...]"
shift
[[ "$#" -gt 0 ]] || fail "a gate command is required"
command_label="${FOLIO_FRESH_STATE_COMMAND_LABEL:-$1}"

roots=()
fresh_prefix="${TMPDIR:-/tmp}/folio-lattice-fresh-state."
cleanup() {
  for root in "${roots[@]}"; do
    [[ "$root" == "$fresh_prefix"* && "$root" != "$fresh_prefix" ]] || continue
    rm -rf -- "$root"
  done
}
trap cleanup EXIT

run_results=()
assigned_ports=()
overall_status=pass
for run in $(seq 1 "$runs"); do
  root="$(mktemp -d "${fresh_prefix}XXXXXX")"
  roots+=("$root")
  compose_project="folio-fresh-${candidate_sha:0:12}-${run}-$(basename "$root" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | tail -c 9)"
  tenant_id="fresh-${candidate_sha:0:12}-${run}"
  actor_id="fresh-runner-${candidate_sha:0:12}-${run}"
  control_port=$((port_base + run))
  renderer_port=$((port_base + 100 + run))
  if [[ "$allow_shared_defaults" == true ]]; then
    tenant_id="${FOLIO_TENANT_ID:-$tenant_id}"
    actor_id="${FOLIO_ACTOR:-$actor_id}"
    control_port="${FOLIO_HOST_PORT:-$control_port}"
    renderer_port="${FOLIO_RENDER_HOST_PORT:-$renderer_port}"
  fi
  [[ "$tenant_id" != hyperset-v0 && "$tenant_id" != dev ]] \
    || [[ "$allow_shared_defaults" == true && "$mode" == non-release ]] \
    || fail "refusing shared/default tenant; use explicit non-release mode only for non-release data"
  [[ "$control_port" != 8000 && "$renderer_port" != 8001 ]] \
    || [[ "$allow_shared_defaults" == true && "$mode" == non-release ]] \
    || fail "refusing default ports; use explicit non-release mode only for non-release data"
  [[ "$control_port" =~ ^[1-9][0-9]*$ && "$renderer_port" =~ ^[1-9][0-9]*$ ]] \
    || fail "fresh-state ports must be positive integers"
  (( control_port <= 65535 && renderer_port <= 65535 )) \
    || fail "fresh-state port exceeds 65535"
  for assigned_port in "$control_port" "$renderer_port"; do
    if [[ "${#assigned_ports[@]}" -gt 0 ]]; then
      for previous_port in "${assigned_ports[@]}"; do
        [[ "$assigned_port" != "$previous_port" ]] \
          || fail "duplicate fresh-state port $assigned_port; choose a unique port base"
      done
    fi
    assigned_ports+=("$assigned_port")
  done
  port_is_free "$control_port" \
    || fail "control port $control_port is not free; choose a unique FOLIO_FRESH_STATE_PORT_BASE"
  port_is_free "$renderer_port" \
    || fail "renderer port $renderer_port is not free; choose a unique FOLIO_FRESH_STATE_PORT_BASE"
  run_evidence="${evidence%.json}.run-${run}"
  mkdir -p "$run_evidence"

  set +e
  (
    export FOLIO_FRESH_STATE_ROOT="$root"
    export FOLIO_DB_PATH="$root/folio.db"
    export FOLIO_BLOB_ROOT="$root/blobs"
    export FOLIO_FRESH_STATE_RUN="$run"
    export FOLIO_COMPOSE_PROJECT="$compose_project"
    export COMPOSE_PROJECT_NAME="$compose_project"
    export FOLIO_TENANT_ID="$tenant_id"
    export FOLIO_ACTOR="$actor_id"
    export FOLIO_HOST_PORT="$control_port"
    export FOLIO_RENDER_HOST_PORT="$renderer_port"
    "$@"
  ) >"$run_evidence/stdout.log" 2>"$run_evidence/stderr.log"
  exit_code=$?
  set -e

  cleanup_status="not_applicable"
  if [[ "$mode" != non-docker ]]; then
    cleanup_status="pass"
    if ! docker compose -p "$compose_project" down -v --remove-orphans \
      >"$run_evidence/compose-cleanup.log" 2>&1; then
      cleanup_status="fail"
    fi
    if [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$compose_project")" \
      || -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$compose_project")" ]]; then
      cleanup_status="fail"
    fi
    if [[ "$cleanup_status" == "fail" ]]; then
      exit_code=1
    fi
  fi

  stdout_sha=$(sha256_file "$run_evidence/stdout.log")
  stderr_sha=$(sha256_file "$run_evidence/stderr.log")
  run_results+=("$(jq -n \
    --argjson run "$run" \
    --argjson exit_code "$exit_code" \
    --arg stdout_sha "$stdout_sha" \
    --arg stderr_sha "$stderr_sha" \
    --arg evidence "$run_evidence" \
    --arg compose_project "$compose_project" \
    --arg tenant_id "$tenant_id" \
    --arg actor_id "$actor_id" \
    --arg control_port "$control_port" \
    --arg renderer_port "$renderer_port" \
    --arg db_path "$root/folio.db" \
    --arg blob_root "$root/blobs" \
    --arg candidate_sha "$candidate_sha" \
    --arg compose_cleanup "$cleanup_status" \
    --arg mode "$mode" \
    '{run:$run, exit_code:$exit_code, stdout_sha256:$stdout_sha,
      stderr_sha256:$stderr_sha, evidence_dir:$evidence,
      compose_project:$compose_project, volume_scope:"project-scoped",
      tenant_id:$tenant_id, actor_id:$actor_id, control_port:$control_port,
      renderer_port:$renderer_port, db_path:$db_path, blob_root:$blob_root,
      candidate_sha:$candidate_sha, compose_cleanup:$compose_cleanup, mode:$mode,
      port_preflight:"pass"}')")
  if [[ "$exit_code" -ne 0 ]]; then
    overall_status=fail
  fi
done

jq -n \
  --arg status "$overall_status" \
  --arg candidate_sha "$candidate_sha" \
  --arg command "$command_label" \
  --arg mode "$mode" \
  --arg project_policy "unique project/volume/ports/tenant/actor per run" \
  --argjson runs "[$(IFS=,; echo "${run_results[*]}")]" \
  '{status:$status, candidate_sha:$candidate_sha, command:$command, mode:$mode, runs:$runs,
    fresh_state:"isolated db/blob root per run", project_policy:$project_policy,
    logs_redacted:false,
    log_policy:"gate command must not emit secrets; logs are hashed but not transformed"}' \
  > "$evidence"

[[ "$overall_status" == pass ]] || exit 1
