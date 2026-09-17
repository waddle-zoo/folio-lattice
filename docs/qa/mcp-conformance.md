# MCP conformance runner

`tests/mcp_conformance.py` is executable evidence for the public MCP
contract. It starts a fresh database and blob directory for each matrix row,
then runs the same artifact and graph flow through:

- the official `mcp.Client` SDK;
- an independent raw client using only newline JSON-RPC, `urllib`, and
  `subprocess`.

Both clients run over stdio and Streamable HTTP. The flow exercises every
current tool: artifact create, list, read, chunk, search, grep, write,
versions, share, revoke, ACL, graph link, traverse, component, and external MCP
blocked-destination registration, empty-registry, audit, and denial cases.
Each generic row asserts the blocked-address rejection, verifies that the
registry stays empty, and exercises fail-closed unregistered-connection calls
without contacting an upstream. Successful registry calls and exact-allowlist
tool and resource checks remain part of the separate hosted approved-upstream
fixture gate. It also
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

## Hosted-authenticated gate (`fl-urj.30`)

`tests/hosted_mcp_conformance.py` runs the same flow as a hosted-authenticated
two-client matrix: official SDK and independent raw JSON-RPC, each over
process-authenticated stdio and bearer-authenticated Streamable HTTP. Every
row starts fresh state. The stdio target verifies a process-scoped bearer
before exposing the stdio server; production hosted mode remains HTTP-only.

The external connection row reaches a real loopback Streamable HTTP MCP
upstream through `HttpExternalMcpTransport`, then proves exact approval,
server-side credential use, secret-free evidence, timeout, oversized-result
rejection, and immediate revoke denial. Oversized transcripts retain only
byte count and SHA-256.

Run after committing the checkout under test:

```sh
FOLIO_CONFORMANCE_SOURCE_SHA="$(git rev-parse HEAD)" \
FOLIO_HOSTED_CONFORMANCE_EVIDENCE=/tmp/folio-lattice-fl-urj.30.json \
  make hosted-conformance
```

The runner fails if the configured source SHA differs from `HEAD` or the
checkout is dirty. Evidence records the exact source SHA, four matrix rows,
matching tool schemas, and redacted upstream checks. It does not claim a
deployment or external internet service: the upstream is an isolated live
loopback MCP process.
