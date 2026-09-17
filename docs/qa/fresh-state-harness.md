# Repeated fresh-state harness (`fl-urj.28`)

`scripts/repeat-fresh-state.sh` prepares repeated release-gate evidence but
refuses to run unless the exact candidate checkout is fixed, clean, and every
declared blocker is closed. It is intentionally not part of `make check` and
must not be used to close `fl-urj.28` while any prerequisite remains open.

The caller supplies a blocker manifest and a gate command. Each repetition
gets a new database/blob root, captures stdout and stderr separately, records
their hashes, and writes one JSON summary without embedding the logs. The
harness does not redact the separate log files, so the supplied gate command
must already exclude tokens, credentials, cookies, and sensitive content. A nonzero gate result
fails the harness after all repetitions have been captured.

Each run also receives a unique `COMPOSE_PROJECT_NAME` and
`FOLIO_COMPOSE_PROJECT`. Release/Compose mode requires Docker and Docker
Compose readiness before the gate command, bind-checks the caller-supplied
`FOLIO_FRESH_STATE_PORT_BASE`, and removes that exact project with volumes
after each run; it fails if labeled containers or volumes remain. Gate
commands must honor these variables. The documented `make docker-test` gate
builds and starts that project with `VCS_REF`, then consumes the harness's
`FOLIO_BASE_URL` and `FOLIO_RENDER_URL`; hard-coded Compose project names are
outside this harness contract. A non-Docker command may opt into
`FOLIO_FRESH_STATE_MODE=non-docker`; that mode is explicitly recorded and the
command must not create Docker resources. `non-release` is the only mode in
which the shared/default override is accepted, and it is not release evidence.

Example, only after the release candidate and blocker manifest are approved:

```sh
FOLIO_RELEASE_CANDIDATE_SHA="$EXACT_SHA" \
FOLIO_FRESH_STATE_BLOCKERS_PATH=evidence/fl-urj.28-blockers.json \
FOLIO_FRESH_STATE_EVIDENCE=evidence/fl-urj.28.json \
FOLIO_FRESH_STATE_RUNS=2 \
FOLIO_FRESH_STATE_MODE=release \
FOLIO_FRESH_STATE_PORT_BASE="$UNIQUE_PORT_BASE" \
FOLIO_FRESH_STATE_COMMAND_LABEL="Docker public MCP/UI/renderer gate" \
scripts/repeat-fresh-state.sh -- make docker-test
```

The harness itself is preparation evidence only. Execution, release-candidate
approval, and bead closure require the Mayor's exact SHA and blocker sign-off.
The blocker manifest is an input, not independent proof; the Mayor must
generate and retain it from the canonical Beads state for the exact candidate.
