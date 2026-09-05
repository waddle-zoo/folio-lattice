# ADR 0002: MCP-first knowledge interface

- Status: Accepted
- Date: 2026-09-05

## Context

Agents need both discovery and precise retrieval. Returning every complete
document wastes context, while exposing only opaque search results prevents
clients from following relationships or verifying source material. A proprietary
client API would also undermine the platform-neutral product thesis.

## Decision

MCP is the first public read/write interface. The initial server will expose
small operations for:

- text search with stable artifact/version identifiers and ranked matches;
- grep-like literal or pattern matching with locations and bounded excerpts;
- graph traversal by direction, relationship type, and bounded depth;
- full-document reads by artifact and version;
- chunk reads using stable version-scoped offsets or chunk identifiers;
- artifact creation, relationship creation, and version-creating writes; and
- retrieval of metadata, version history, and provenance.

Every read result identifies the exact immutable version used. Pagination,
maximum traversal depth, chunk bounds, response size limits, and deterministic
ordering are part of the contract. Writes accept a base version or equivalent
precondition so concurrent changes produce a conflict rather than a lost update.

Search indexes may be asynchronous internally, but responses must make indexing
state visible so clients can distinguish "no match" from "not indexed yet."
Transport handlers call provider-neutral application services; MCP payloads do
not become the persistence model.

## Consequences

- MCP schema evolution and compatibility tests are release-critical.
- Version-scoped reads remain reproducible after later writes.
- Clients can control context consumption without giving up source identity.
- The internal service boundary can later support other transports without
  changing graph or artifact semantics.

## Non-goals

- A provider-specific tool vocabulary or response format.
- Arbitrary database queries through MCP.
- Silent last-write-wins mutation.
- User-specific credentials for calling third-party MCPs.

## Open questions

- Should chunks be byte-, character-, token-, or structure-addressed for each
  content type?
- Which search query features belong in v1 beyond literal grep and basic text
  ranking?
- How should clients negotiate optional server capabilities and schema versions?
