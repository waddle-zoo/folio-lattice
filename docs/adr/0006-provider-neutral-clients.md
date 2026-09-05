# ADR 0006: Provider-neutral client contracts

- Status: Accepted
- Date: 2026-09-05

## Context

Folio Lattice is intended to serve Claude Code, Codex, Cursor, and other agents.
If persisted data or tool semantics mirror one provider's messages, identities,
or orchestration model, other clients will become second-class and migration will
be expensive.

## Decision

Define the public contract in provider-neutral MCP schemas and durable domain
types. Artifact IDs, version IDs, graph relationships, chunks, actor records,
provenance, errors, pagination, and concurrency preconditions must have meanings
independent of any model or agent client.

Client identity and provenance may record a provider or product name as metadata,
but behavior cannot depend on that value. Provider-specific adapters are thin
edge components: they translate authentication, configuration, or presentation
without changing stored semantics. No provider transcript is required to read,
verify, or continue work on an artifact.

The conformance suite will exercise the same create, write, search, grep, read,
chunk, and traversal scenarios through at least two distinct MCP client stacks.
Capabilities and protocol/schema versions are discoverable so clients can degrade
gracefully rather than branching on brand names.

## Consequences

- New clients integrate against one documented contract.
- Provider-exclusive features may require lossy adapters or remain unsupported
  until they have a general representation.
- Cross-client conformance tests are necessary to catch accidental coupling.
- Provenance can identify the originating client without making that client the
  owner of the resulting knowledge.

## Non-goals

- Identical user interfaces or configuration for every client.
- Provider-specific graph semantics or privileged write paths.
- Reproducing every vendor's artifact UI.
- A universal agent orchestration protocol or polecat swarm.

## Open questions

- What is the minimum MCP protocol and capability baseline for supported clients?
- How should extensions be namespaced and promoted into the core contract?
- Which client stacks should run in continuous conformance testing?
