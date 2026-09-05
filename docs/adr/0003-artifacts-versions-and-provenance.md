# ADR 0003: Arbitrary-file artifacts, immutable versions, and provenance

- Status: Accepted
- Date: 2026-09-05

## Context

Folio Lattice must store documents, source files, media, archives, and future
types that cannot be predicted now. Treating a file as a mutable row would make
agent writes difficult to audit, reproduce, compare, or recover.

## Decision

An artifact is a stable logical identity. Its content lives in immutable version
records. Every create, replace, edit, metadata mutation that affects the artifact
representation, or generated output creates a new version; no API updates
version content in place.

The core model separates:

- artifact identity and tenant ownership;
- immutable version identity, parent version, content digest, media type, size,
  storage locator, and creation time;
- provenance including actor, client, operation, supplied reason, source
  references, and correlation/request identifier; and
- typed graph edges whose endpoints state whether they follow an artifact or pin
  a specific version.

Arbitrary bytes are accepted. Text extraction, chunking, previews, and indexing
are derived data associated with the exact source version and extractor version.
Unknown media types remain retrievable even when no renderer or indexer exists.
Content-addressed blob storage and metadata storage may be separate, but a write
becomes visible only after both are durably associated. Identical bytes may be
deduplicated without merging artifact or provenance identities.

## Consequences

- History, rollback, comparison, caching, and reproducible reads have a reliable
  primitive.
- Storage grows monotonically until an explicit retention policy is adopted.
- Writes require transactional coordination or a recoverable commit protocol
  across metadata and blob storage.
- Deletion and legal erasure need explicit tombstone and purge semantics; they
  cannot be implemented as ordinary mutation.

## Non-goals

- Requiring every file type to be searchable or renderable.
- In-place content mutation or mutable version identifiers.
- Inferring complete provenance from chat history.
- A collaborative real-time editing protocol in the first milestone.

## Open questions

- Which provenance fields are mandatory for human, service, and agent actors?
- What retention and tenant-controlled purge policies are required for hosted
  deployment?
- Should graph-edge changes share the artifact version stream or use a separate
  immutable event history?
