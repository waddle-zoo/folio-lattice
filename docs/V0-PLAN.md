# Folio Lattice v0 implementation plan

Status: implementation in progress; full end-to-end ship gate is still open

The v0 target is a small, testable vertical slice. It proves the durable graph
and artifact lifecycle before adding a polished UI, public sharing, semantic
search, or user-specific credentials.

## Phase 1: foundation

1. Add a Docker Compose development topology with an API/MCP service and a
   persistent artifact volume.
2. Define SQLite migrations for tenants, artifacts, versions, blobs, chunks,
   nodes, edges, and provenance.
3. Add a content-addressed blob store with media type, size, and SHA-256
   metadata.
4. Add an indexing pipeline for text-like artifacts and bounded deterministic
   chunks. Binary files remain valid artifacts even when they are not indexed.

## Phase 2: graph and artifact contract

Implement the smallest provider-neutral service operations:

- create an artifact;
- write a new version with an explicit parent and provenance;
- read artifact metadata and a complete version;
- read a bounded chunk by stable chunk identifier or offset contract;
- grep/search indexed text with stable result identifiers and snippets;
- create typed graph edges;
- traverse edges with explicit depth and result limits; and
- list version history.

Every mutation must be transactional. A failed write must not create a partial
version, edge, blob reference, or index row.

## Phase 3: MCP adapter

Expose the service contract through MCP tools/resources. Keep tool names,
schemas, error categories, identifiers, and pagination independent of the model
provider. Provide a stdio server for local clients and a Docker HTTP transport
for integration tests. Use a deterministic development principal until user
credentials are designed.

## Phase 4: safe web artifacts

1. Store HTML, CSS, and JavaScript as ordinary versioned artifacts.
2. Render them from a dedicated origin inside a sandboxed iframe.
3. Apply a restrictive CSP with no direct external network connection.
4. Do not pass host cookies, local storage, privileged headers, or platform
   origin access into the artifact.
5. Provide a platform bridge that accepts only explicitly attached MCP
   capabilities, validates the requested operation, and records the call.
6. Add hostile fixtures for `fetch`, XHR, WebSocket, top-navigation, DOM
   escape attempts, and same-origin reads.

## Phase 5: end-to-end acceptance

Run the matrix from a fresh checkout with Docker and a clean artifact volume.

| Area | Acceptance check |
| --- | --- |
| Startup | Compose starts from empty state; migrations are repeatable; health checks pass. |
| Create | MCP client creates text, JSON, image/binary, HTML, CSS, and JavaScript artifacts. |
| Write | Each write creates a new immutable version with parent, actor, reason, timestamp, and content hash. |
| Retrieval | Full-document and bounded-chunk reads return stable identifiers and exact content. |
| Search | Grep/full-text search returns matching artifact/version/chunk identifiers and snippets. |
| Graph | Typed links can be created and bounded traversal returns the expected connected subgraph. |
| Failure safety | Invalid parent, malformed payload, missing blob, and interrupted write leave no partial mutation. |
| MCP | A provider-neutral client can create, search, traverse, read, and write through the same contract. |
| Sandbox | HTML/CSS/JS renders, but direct external egress, host-origin reads, cookie access, and top-level escape attempts fail. |
| Capabilities | An attached MCP capability works through the bridge; an unattached capability is rejected and logged. |
| Privacy seam | Tenant/owner fields and authorization checks exist at the service boundary even with a development principal. |
| Repeatability | The complete suite passes twice from fresh containers and a cleared artifact volume. |

## Phase 6: enterprise and first-consumer hardening

Research: [enterprise and Hyperset contract review](research/2026-09-05-enterprise-hyperset-contract.md).

This phase closes the gap between the working v0 core and a supportable public
service boundary. Complete these in order:

1. Replace the hand-written MCP subset with the official MCP Python SDK and one
   shared operation implementation exposed over stdio and Streamable HTTP.
2. Validate startup configuration. HTTP derives tenant and actor from a
   configured bearer-authenticated service principal; stdio derives the same
   values from its process environment. Remove tenant and actor overrides from
   public tool arguments.
3. Make artifact creation and first-version metadata one transaction, track the
   current version explicitly, bound request/tool inputs, and reject graph-edge
   metadata rewrites that would erase history.
4. Add a Hyperset-shaped black-box consumer fixture. It may depend on the public
   MCP SDK and network endpoint only: no `folio_lattice` import, SQLite access,
   blob-volume mount, or special operation.
5. Prove authenticated MCP behavior, immutable writes, search, grep, bounded
   chunks, graph traversal, cross-tenant denial, and restart persistence through
   Docker.

Phase acceptance:

| Area | Required evidence |
| --- | --- |
| Protocol | Official SDK client lists and calls typed tools over real Streamable HTTP. |
| Identity | Missing/wrong HTTP bearer tokens fail; tool arguments cannot select tenant or actor. |
| Persistence | Create and first version commit together; restart preserves exact version bytes and provenance. |
| Graph | Duplicate identical link is idempotent; conflicting metadata cannot overwrite an edge. |
| Consumer | Hyperset fixture completes create, write, link, search, traversal, and read using public MCP/HTTP only. |
| Isolation | A second configured tenant/process cannot resolve the first tenant's identifiers. |
| Gates | `make check`, `make docker-build`, and `make docker-test` pass from the Mayor clone. |

Phase 6 does not close the full milestone sandbox gate. The existing CSP and
capability code remains policy scaffolding until the dedicated-origin renderer
and real-browser hostile suite in Phase 4 are implemented.

## Ship gate

Nothing is called ready to ship until the implementation has a focused test
suite, the complete matrix above passes twice from fresh Docker state, the final
diff has been reviewed for scope and security, and the exact commands/results
are recorded in the commit or release notes.

## Deferred

User-specific MCP credentials, a full authoring UI, public or selective sharing,
semantic/vector retrieval, a separate graph database, marketplaces, and broad
multi-agent orchestration remain after this vertical slice.
