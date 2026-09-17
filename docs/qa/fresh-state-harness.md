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
`FOLIO_COMPOSE_PROJECT`. When Docker is available, the harness removes that
exact project with volumes after the run and fails if labeled containers or
volumes remain. Gate commands must honor these variables; hard-coded Compose
project names are outside this harness contract.

Example, only after the release candidate and blocker manifest are approved:

```sh
FOLIO_RELEASE_CANDIDATE_SHA="$EXACT_SHA" \
FOLIO_FRESH_STATE_BLOCKERS_PATH=evidence/fl-urj.28-blockers.json \
FOLIO_FRESH_STATE_EVIDENCE=evidence/fl-urj.28.json \
FOLIO_FRESH_STATE_RUNS=2 \
FOLIO_FRESH_STATE_COMMAND_LABEL="Docker public MCP/UI/renderer gate" \
scripts/repeat-fresh-state.sh -- make docker-test
```

The harness itself is preparation evidence only. Execution, release-candidate
approval, and bead closure require the Mayor's exact SHA and blocker sign-off.
The blocker manifest is an input, not independent proof; the Mayor must
generate and retain it from the canonical Beads state for the exact candidate.
