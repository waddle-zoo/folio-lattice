# ADR 0001: Product boundary and first milestone

- Status: Accepted
- Date: 2026-09-05

## Context

Folio Lattice could expand into authoring, publishing, collaboration, agent
orchestration, or an integration marketplace before its core data and security
contracts are proven. The first milestone needs a boundary that produces a
useful vertical slice and exposes the highest-risk assumptions early.

## Decision

Build the durable knowledge graph and safe artifact store first. The milestone
is a Docker-based local deployment with an MCP server that can:

- create an artifact and create immutable subsequent versions;
- search or grep indexed text;
- retrieve an entire document or a bounded chunk;
- add and traverse typed graph relationships;
- return version and provenance metadata; and
- host a static HTML/CSS/JavaScript artifact behind an explicit, tested sandbox
  policy.

Acceptance requires a fresh-clone startup path, persisted data across restarts,
contract-level MCP tests, write-conflict behavior, and adversarial tests for the
web sandbox. A minimal inspection or administration surface is allowed only
where it helps validate this vertical slice.

## Consequences

- Storage, identity, versioning, MCP contracts, and sandbox policy receive
  implementation priority over end-user interface work.
- The milestone is useful to agent clients before it is a polished standalone
  application.
- Features that do not exercise the core graph, artifact, or security lifecycle
  should not enter the first milestone.

## Non-goals

- A polished or full-featured authoring UI.
- Public or selective sharing, marketplaces, or social discovery.
- User-specific MCP credentials.
- Unrestricted browser networking.
- Polecat swarms or a large multi-agent operating model.

## Open questions

- Which persistent stores provide the simplest reference implementation without
  obscuring the storage interfaces?
- What performance envelope should the local milestone prove for artifact size,
  graph size, and concurrent clients?
- Which conformance fixture will demonstrate compatibility across MCP clients?
