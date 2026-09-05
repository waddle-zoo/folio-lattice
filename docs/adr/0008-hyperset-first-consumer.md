# ADR 0008: Hyperset is the first consumer through public contracts

- Status: Accepted
- Date: 2026-09-05

## Context

Folio Lattice is intended to become the knowledge and artifact substrate for
multiple enterprise systems. Hyperset, the analytics context and governance
system in the same organization, will be the first user of that substrate and
will help exercise the knowledge flywheel with real ingestion, retrieval, and
versioned write patterns.

The first consumer relationship can either validate a durable public contract
or accidentally turn Folio Lattice into a Hyperset-specific internal module.
The latter would make the product harder to adopt, harder to secure, and less
useful to other agent clients.

## Decision

Hyperset integrates with Folio Lattice only through its public MCP and HTTP
contracts. Folio Lattice remains responsible for artifact identity, immutable
versions, graph relationships, provenance, capability enforcement, and tenant
boundaries.

The integration must not:

- share Folio Lattice's SQLite database or blob volume directly;
- import private Folio Lattice Python modules;
- bypass MCP/HTTP authorization or capability checks;
- make Folio Lattice depend on Hyperset's package layout; or
- add Hyperset-specific tool names to the provider-neutral core contract.

Hyperset-specific adapters, fixtures, and contract tests may live in Hyperset
or in a separately named integration surface. They must identify the public
contract they exercise and preserve the same behavior for other clients.

## Consequences

- Hyperset becomes the first realistic integration and an important end-to-end
  consumer for the knowledge flywheel.
- Folio Lattice must version and document its public MCP/HTTP schemas before
  Hyperset relies on them in production.
- Cross-repository contract tests become part of enterprise readiness.
- A failure in Hyperset should reveal a contract gap, not justify a private
  coupling shortcut.

## Non-goals

This decision does not define Hyperset's internal data model, its connector
architecture, or the future UI. It does not make Hyperset the only consumer or
grant it ownership of Folio Lattice's product direction.
