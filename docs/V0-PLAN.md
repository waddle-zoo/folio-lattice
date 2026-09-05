# Folio Lattice v0 implementation plan

Status: Phase 7 complete; full end-to-end ship gate remains open on the
mediated attached-MCP bridge in Phase 4

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
| Namespace seam | Namespace fields and fail-closed lookups exist at the service boundary without claiming a v0 authorization system. |
| Repeatability | The complete suite passes twice from fresh containers and a cleared artifact volume. |

## Phase 6: public contract and first-consumer proof

Research: [enterprise and Hyperset contract review](research/2026-09-05-enterprise-hyperset-contract.md).

This phase closes the gap between the working v0 core and a compact public
service boundary. Complete these in order:

1. Replace the hand-written MCP subset with the official MCP Python SDK and one
   shared operation implementation exposed over stdio and Streamable HTTP.
2. Derive the development namespace and provenance actor from process
   configuration. Remove tenant and actor overrides from public tool arguments,
   without treating this v0 default as an authorization system.
3. Make artifact creation and first-version metadata one transaction, track the
   current version explicitly, bound request/tool inputs, and reject graph-edge
   metadata rewrites that would erase history.
4. Add a Hyperset-shaped black-box consumer fixture. It may depend on the public
   MCP SDK and network endpoint only: no `folio_lattice` import, SQLite access,
   blob-volume mount, or special operation.
5. Prove immutable writes, search, grep, bounded chunks, graph traversal,
   namespace isolation, and restart persistence through Docker.

Phase acceptance:

| Area | Required evidence |
| --- | --- |
| Protocol | Official SDK client lists and calls typed tools over real Streamable HTTP. |
| Identity | Tool arguments cannot select the configured namespace or provenance actor. |
| Persistence | Create and first version commit together; restart preserves exact version bytes and provenance. |
| Graph | Duplicate identical link is idempotent; conflicting metadata cannot overwrite an edge. |
| Consumer | Hyperset fixture completes create, write, link, search, traversal, and read using public MCP/HTTP only. |
| Isolation | A second configured tenant/process cannot resolve the first tenant's identifiers. |
| Gates | `make check`, `make docker-build`, and `make docker-test` pass from the Mayor clone. |

Phase 6 does not close the full milestone sandbox gate. The existing CSP and
capability code remains policy scaffolding until the dedicated-origin renderer
and real-browser hostile suite in Phase 4 are implemented.

Implementation evidence, 2026-09-05:

- `make check`: 13 tests passed with 88.55% branch-aware coverage;
- `make docker-build`: official-SDK image built successfully;
- Docker MCP smoke: the public Hyperset fixture completed every contract
  operation, Compose restarted the service, and the same version identifier,
  SHA-256, and bytes were retrieved afterward;
- the image ran as UID `folio`, with a read-only root filesystem, all Linux
  capabilities dropped, and a healthy writable data volume; and
- a compatibility probe using Hyperset's pinned `mcp==1.28.1` client negotiated
  with the SDK 2.1.1 server, listed tools, and created an artifact.

### Scope correction

The official MCP SDK stays because it replaces the bespoke protocol parser,
generates the schemas for the core primitives, and provides both required
transports. V0 does not add an application credential system around it. The
temporary bearer wrapper is removed; local Docker binds to loopback, and
production authentication remains a deployment concern until the artifact and
graph loop earns a product-level credential design.

The public surface remains nine composable operations: create, write, read,
read chunk, search, grep, link, traverse, and version history. Provenance is
part of artifact writes rather than a separate subsystem. No consumer-specific
adapter, REST mirror, sharing model, vector service, or authorization framework
belongs in this phase.

## Phase 7: thin inspection UI and isolated renderer

Research: [thin inspection UI and renderer](research/2026-09-05-thin-ui-renderer.md).
Decision: [ADR 0009](adr/0009-thin-inspection-ui-and-isolated-renderer.md).

This phase adds the smallest browser loop over the existing state model:

1. A static page opens one artifact identifier and displays current text,
   immutable version metadata, and outgoing graph traversal.
2. Text edits call one private JSON operation with the current version as the
   optimistic parent. Each successful edit creates a normal immutable version.
3. A second process and origin opens the same state read-only and renders HTML,
   JavaScript, or CSS.
4. Both iframe and response CSP sandboxing allow scripts without same-origin,
   connection, form, popup, storage, or navigation privileges.
5. HTTP, real-browser, and Docker checks exercise the complete slice.

Implementation evidence, 2026-09-05:

- `make check`: 20 tests passed with 90.09% branch-aware coverage;
- headless Chrome ran stored HTML and standalone JavaScript/CSS artifacts while
  hostile network, storage, popup, form, navigation, and host-DOM attempts
  failed;
- `make docker-build` built the shared control/renderer image; and
- a fresh-volume Docker run completed the MCP graph loop, inspection read,
  restart-persistence check, and renderer check with `/data` read-only in the
  renderer container.

The public MCP surface remains unchanged. The UI routes are inspection mechanics,
not a general REST contract. Artifact listing, creation UI, graph editing,
sharing, credentials, auth frameworks, vector search, attached-MCP bridging, and
a frontend framework remain outside this phase.

## Ship gate

Nothing is called ready to ship until the implementation has a focused test
suite, the complete matrix above passes twice from fresh Docker state, the final
diff has been reviewed for scope and security, and the exact commands/results
are recorded in the commit or release notes.

## Deferred

Credentials, public or selective sharing, semantic/vector retrieval, a separate
graph database, marketplaces, and broad multi-agent orchestration remain after
this vertical slice. The next product layer is a thin UI over these same
artifact, graph, and renderer contracts—not a second application model.
