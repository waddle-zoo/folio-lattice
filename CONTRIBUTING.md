# Contributing to Folio Lattice

Folio Lattice is enterprise infrastructure for durable artifacts, connected
knowledge, and constrained execution. A change should make the trust boundary
clearer, not merely make a demo work.

## Before opening a change

1. Read [`MANIFESTO.md`](MANIFESTO.md), the relevant ADRs, and
   [`docs/V0-PLAN.md`](docs/V0-PLAN.md).
2. State the public contract and the end-to-end behavior the change proves.
3. Add or update a focused test at the same boundary as the behavior.
4. Update an ADR when the change affects security, persistence, privacy,
   provider neutrality, or the Hyperset integration boundary.

## Local checks

Install the locked development environment and run the same checks used by CI:

```bash
make install
make check
make test-e2e
make docker-build
make docker-up
make docker-test
make docker-down
```

`make check` runs Ruff, mypy, and the coverage-gated pytest suite. The Docker
smoke workflow is required evidence for changes to the service boundary,
persistence, MCP transport, or container packaging.

## Design rules

- Keep the core contract provider-neutral and MCP-first.
- Preserve immutable versions, provenance, tenant boundaries, and explicit
  capability checks.
- Do not add direct network access to rendered artifacts.
- Do not make Folio Lattice depend on Hyperset internals. Hyperset is the first
  consumer of the public artifact and graph contract, not a privileged module.
- Avoid committing credentials, generated data, local databases, or runtime
  directories.
