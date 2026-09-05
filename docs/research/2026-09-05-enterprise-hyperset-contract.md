# Enterprise and Hyperset contract research

Date: 2026-09-05

## Question

What is the smallest hardening slice that makes Folio Lattice credible as an
enterprise service boundary and gives Hyperset a first consumer path without a
shared database, private imports, or a privileged bypass?

This review uses Folio Lattice `a707824` and Hyperset
`1f3464ceec795354f555cbf727b43c9f14ce92f6`.

## Existing evidence

The current Folio Lattice service proves its basic data loop. `make check`
passes 11 tests with 93% branch-aware coverage. The service creates immutable
artifact versions, stores content-addressed blobs, indexes text with SQLite
FTS5, traverses typed edges, supports stdio and HTTP-shaped JSON-RPC, and has a
Docker health smoke.

That is useful implementation evidence, but it is not yet an enterprise public
contract:

- `mcp_protocol.py` hand-rolls an older `2025-03-26` subset instead of using a
  supported MCP SDK and Streamable HTTP implementation.
- `tenant_id` and `actor` are caller-controlled tool arguments. A caller can
  select another tenant and assign provenance to another actor.
- `graph_link` updates edge metadata in place, which erases prior graph state.
- tool argument bounds and output schemas are descriptive dictionaries rather
  than SDK-enforced contracts.
- the Docker smoke does not prove authenticated access or persistence across a
  service restart.
- no black-box fixture demonstrates how Hyperset can use the contract without
  importing `folio_lattice` or opening its database and blob volume.

## MCP findings

The official Python SDK 2.x is the current stable line; PyPI reported `2.1.1`
during this review. It implements MCP revision `2026-07-28` and earlier
revisions. `MCPServer` derives JSON Schema from Python types, validates tool
arguments, provides structured tool results, and serves Streamable HTTP. Its
`Client` can exercise a server in memory or over the real HTTP transport.

This removes more code than it adds: Folio should delete its protocol parser and
keep artifact/graph policy in the service layer. Tool errors should be normal
MCP error results that clients can inspect, not ad hoc JSON-RPC failures.

Sources:

- [MCP Python SDK stable line](https://pypi.org/project/mcp/)
- [MCP Python SDK tools](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md)
- [MCP Python SDK testing](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/testing.md)
- [MCP Python SDK ASGI deployment](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md)
- [MCP 2026-07-28 Streamable HTTP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)

## Persistence and graph findings

SQLite remains sufficient for this slice. FTS5 supplies bounded lexical search,
ranking, and snippets. SQLite transactions give one atomic metadata commit, and
its recursive queries can support graph walks if the current explicit bounded
breadth-first implementation later becomes a bottleneck. A separate graph or
search service would add deployment failure modes without improving the first
consumer contract.

Content-addressed blob creation can happen before a metadata commit because an
unreferenced immutable blob is not visible product state. Metadata creation,
version insertion, current-version movement, chunks, and index rows must share
one `BEGIN IMMEDIATE` transaction. Duplicate graph writes may be idempotent only
when their complete immutable representation matches; conflicting metadata must
fail instead of overwriting history.

Sources:

- [SQLite atomic commit](https://sqlite.org/atomiccommit.html)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [SQLite recursive graph queries](https://sqlite.org/lang_with.html#queries_against_a_graph)

## Tenant and provenance findings

Hyperset scopes every connection, snapshot, resource, search hit, and bundle to
a workspace. Authorization is decided from metadata before content is opened,
and omitted workspace context never means every workspace. Folio needs the same
fail-closed rule at its smaller boundary.

V0 does not need end-user identity or OAuth. One configured service principal is
enough: HTTP requires a bearer token and derives tenant and actor from server
configuration; stdio derives them from the spawned process environment. Public
tool inputs cannot override either value. This is service authentication, not
the deferred user-specific credential broker.

Relevant Hyperset records:

- [Hyperset v0 foundation](https://github.com/waddle-zoo/hyperset/blob/1f3464ceec795354f555cbf727b43c9f14ce92f6/docs/v0-foundation.md)
- [Hyperset architecture](https://github.com/waddle-zoo/hyperset/blob/1f3464ceec795354f555cbf727b43c9f14ce92f6/docs/architecture.md)
- [Hyperset tenant isolation ADR](https://github.com/waddle-zoo/hyperset/blob/1f3464ceec795354f555cbf727b43c9f14ce92f6/docs/adr/0037-tenant-workspace-isolation.md)
- [Hyperset live MCP ADR](https://github.com/waddle-zoo/hyperset/blob/1f3464ceec795354f555cbf727b43c9f14ce92f6/docs/adr/0043-live-mcp-is-a-read-only-observed-source.md)

Hyperset's existing live-MCP connector is deliberately read-only and may call
only MCP resources. It must not be repurposed to invoke Folio mutation tools.
The first Folio consumer fixture therefore models a separate Hyperset service
client against Folio's public tools. It uses only the supported MCP SDK and the
network endpoint; it imports no Folio package and receives no storage path.

## Docker and browser findings

Compose health checks and named volumes remain the correct local proof. The
hardening slice should also run the container as non-root with all Linux
capabilities dropped, `no-new-privileges`, a read-only root filesystem, and a
writable named data volume. The Docker smoke must create data through MCP,
restart the service, then retrieve the same exact version through MCP.

The existing sandbox module is policy scaffolding, not a complete renderer.
HTML sandboxing requires an opaque origin unless `allow-same-origin` is granted,
and CSP must deny `connect-src` and explicitly constrain framing. No claim of a
complete web-artifact runtime should be made until a dedicated renderer and real
browser adversarial suite land.

Sources:

- [Docker Compose startup and health order](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker Compose volumes](https://docs.docker.com/reference/compose-file/volumes/)
- [HTML sandboxing flags](https://html.spec.whatwg.org/multipage/browsers.html#sandboxing)
- [Content Security Policy Level 3](https://www.w3.org/TR/CSP3/)

## Decision

Implement one hardening slice:

1. Replace the custom MCP parser with official MCP Python SDK 2.1.x
   `MCPServer`, stdio, and Streamable HTTP.
2. Derive one V0 tenant and actor from validated process configuration; require
   a bearer token on HTTP and remove caller-selected tenancy/provenance.
3. Make artifact creation one metadata transaction, add an explicit current
   version pointer, validate bounded inputs, and stop mutable edge overwrites.
4. Add a black-box Hyperset consumer fixture using only MCP over HTTP.
5. Extend Docker validation through authenticated create/read/search/traverse,
   a service restart, and exact immutable-version retrieval.

Do not add a second REST vocabulary, OAuth server, user credential broker,
client package, graph database, vector search, renderer placeholder, or Hyperset
adapter inside Folio. Those do not help prove this boundary.
