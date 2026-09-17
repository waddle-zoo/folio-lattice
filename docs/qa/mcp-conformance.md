# MCP conformance runner

`tests/mcp_conformance.py` is executable evidence for the public MCP
contract. It starts a fresh database and blob directory for each matrix row,
then runs the same artifact and graph flow through:

- the official `mcp.Client` SDK;
- an independent raw client using only newline JSON-RPC, `urllib`, and
  `subprocess`.

Both clients run over stdio and Streamable HTTP. The flow exercises every
current tool: artifact create, list, read, chunk, search, grep, write,
versions, share, revoke, ACL, graph link, traverse, component, and approved
external MCP connection register/list/status/audit/revoke. External tool and
resource calls are exercised through exact-allowlist fail-closed cases; a
successful real-upstream call remains part of the separate approved-connection
integration gate. It also
checks malformed arguments, oversized fields, unknown tools, and an
inaccessible artifact. A separate public hosted-auth fixture checks missing
bearer access returns `401` for both clients.

Run from a clean checkout:

```sh
FOLIO_CONFORMANCE_EVIDENCE=/tmp/folio-lattice-mcp-conformance.json \
FOLIO_CONFORMANCE_SOURCE_SHA="$(git rev-parse HEAD)" \
  uv run python tests/mcp_conformance.py
```

The runner exits non-zero unless all four client/transport rows pass, schemas
match, and the missing-bearer probe passes. JSON evidence contains source SHA,
schema snapshots, operation summaries, status/headers, and exact bounded
transcripts. Oversized bodies are represented by byte count and SHA-256 rather
than copied into evidence.

This is protocol and regression evidence only. It does not certify hosted or
enterprise readiness.
