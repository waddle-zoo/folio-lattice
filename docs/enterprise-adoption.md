# Enterprise adoption notes

Folio Lattice should be easy to evaluate before it is trusted with corporate
knowledge. This document lists the evidence an adopter should expect from each
layer.

## Repository evidence

- A small `src/` package with no runtime dependencies in v0.
- Locked development dependencies in `uv.lock`.
- Ruff lint and format checks over product code and tests.
- Pytest with a visible coverage floor.
- Unit, transport, and Docker boundary tests.
- CI on supported Python versions and a Docker health smoke test.
- Dependabot for Python and GitHub Actions updates.
- A documented security reporting path and explicit non-goals.

## Runtime evidence

- Configuration is supplied through environment variables or deployment
  configuration, not source edits.
- Metadata and content are persisted in explicit volumes.
- Health checks distinguish process availability from product readiness.
- MCP errors are structured and stable enough for clients to recover.
- Writes are transactional, immutable, provenance-bearing, and protected by an
  optimistic parent check.
- Tenant and capability boundaries are enforced at the service boundary.

## Integration evidence

Hyperset is the first consumer. Its integration should be built as a public
contract test: a clean Folio Lattice service, a real MCP/HTTP client, and an
isolated test tenant. The test should prove artifact creation, graph linking,
retrieval, versioned writes, and restart persistence without opening Folio's
database or importing private modules.

That same test shape should be reusable by future enterprise consumers. A
consumer-specific shortcut is a failed adoption signal, not a successful
integration.

## Current limitations

The v0 repository is not yet a production security certification. The full
dedicated-origin renderer, hosted authorization, operational observability,
backup/restore procedures, and formal schema compatibility policy remain ship
gates. The first adopter relationship is a reason to close those gates with
evidence, not a reason to bypass them.
