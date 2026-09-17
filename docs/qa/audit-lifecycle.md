# Audit lifecycle evidence

The local and hosted MCP surfaces expose one bounded `audit_export` tool for
the `tenant:admin` scope. It returns versioned `audit-v1` events in ascending
`occurred_at, id` order, a continuation cursor, a 24-hour export expiry, and a
SHA-256 checksum over the canonical NDJSON representation. Every export is
itself recorded as an audit event.

Events contain tenant and actor identifiers, request and correlation IDs,
action, resource type and opaque ID, outcome, reason, policy/source metadata,
retention class, expiry, legal-hold state, and an integrity hash. A bounded
details allowlist accepts status/reason/policy/connection/tool/resource IDs and
transport only. Content, prompts, arguments/results, credentials, bearer
tokens, and raw URLs are rejected before persistence.

Readers and exporters recompute each event's canonical integrity hash and fail
closed on tampering. Export windows must be timezone-aware ISO-8601 values with
`from_time <= to_time`; windows are normalized to UTC before indexed lookup.
Pagination is capped at 10,000 events and the NDJSON accumulator stops at
1 MiB, returning a cursor when more rows remain. Export metadata and its
`audit_export` event are committed in one transaction, so an audit-write
failure cannot leave an orphan export record.

The default retention classes are security 365 days, request/abuse 90 days,
and debug 30 days. `set_audit_legal_hold` protects selected events from the
retention purge hook; the executable operator hook is:

```text
python -m folio_lattice.ops audit purge --before 2026-01-01T00:00:00+00:00
```

It removes expired, non-held events and records a minimized purge summary.
Readiness includes the audit schema/index dependency,
and `/metrics` reports aggregate audit event, denial, export, and purge
counters without tenant labels.

Focused evidence:

```text
pytest -q tests/test_audit.py                         # 4 passed
pytest -q tests/test_service.py tests/test_mcp.py \
  tests/test_deployment.py tests/test_external_mcp.py tests/test_web.py
                                                       # 56 passed, 18 subtests
```

The export is tenant-scoped by the authenticated MCP principal. A principal
without `tenant:admin` receives a uniform operation-not-permitted error.
