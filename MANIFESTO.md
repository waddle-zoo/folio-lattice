# Folio Lattice manifesto

Folio Lattice is company-hosted infrastructure for knowledge that people and AI
agents can use without handing control of that knowledge to a model vendor. It
combines a durable, connected artifact store with a constrained execution
environment. The first interface is MCP, not a polished application UI.

This document states the product contract. The architecture decision records in
[`docs/adr`](docs/adr/README.md) turn that contract into implementation choices.

## What we are building

Folio Lattice has two inseparable parts:

1. A knowledge graph in which documents and arbitrary files are addressable
   artifacts connected by typed relationships.
2. A safe artifact runtime for HTML, JavaScript, and CSS that cannot reach the
   public network or escape into the hosting application.

Agents connect through MCP. They can search and grep indexed content, traverse
relationships, retrieve a complete document or a bounded chunk, create an
artifact, and write a new version. Every write creates an immutable version. The
system records who or what made it, when it happened, which prior version it
followed, and the client-supplied reason or source context.

The graph and artifact lifecycle are the product. Any early web interface exists
to inspect, test, and administer that lifecycle; it is not the primary authoring
experience.

## The first milestone

The first milestone is a reproducible Docker-based local deployment containing:

- persistent metadata, graph, blob, version, and provenance storage;
- an MCP server exposing artifact creation, immutable writes, graph traversal,
  text search, grep-like matching, full-document reads, and bounded chunk reads;
- a renderer for static HTML/CSS/JavaScript artifacts with a testable isolation
  boundary, no external egress, and no XSS-capable path into the host;
- a mediated bridge through which sandboxed artifacts may call only MCP servers
  attached and authorized by the Folio Lattice platform; and
- integration tests that exercise the same public contracts from more than one
  provider-neutral MCP client.

The milestone is successful when a fresh clone can start locally with Docker,
an MCP client can create and connect artifacts, every mutation produces a new
auditable version, search and retrieval return stable results, and a hostile web
artifact cannot violate the sandbox policy.

## Product principles

### Durable knowledge over transient output

Artifacts are not chat attachments. They have stable identities, immutable
versions, explicit relationships, and queryable provenance. Content bytes and
metadata may use different storage systems, but both belong to one lifecycle.

### Small, provider-neutral contracts

Claude Code, Codex, Cursor, and other agents should see the same MCP tools and
resource semantics. Core identifiers, graph edges, version records, and
provenance must not depend on a provider's message format or agent model.

### Read precisely, write explicitly

Clients may retrieve a full document when needed, but they can also search,
grep, traverse, and read bounded chunks to control context use. Writes are
explicit mutations with concurrency checks; they never silently replace history.

### Treat isolation as product behavior

Web artifacts are untrusted code. Their inability to contact arbitrary origins,
read host state, or execute in a privileged origin is a user-visible guarantee.
Access to attached MCPs is mediated by the platform, allowlisted, authorized,
logged, and revocable.

### Local first, deployable by design

Docker Compose is the reference development environment. Service boundaries,
configuration, migrations, storage adapters, observability, and stateless API
processes must leave a clear path to a hosted enterprise deployment without
forking the product contract.

### Private by default

Tenants are isolated and newly created artifacts are private. The data model must
have room for explicit grants, but selective teammate sharing and public sharing
are v2 product capabilities. An early prototype that is reachable without full
authorization is development scaffolding, never the intended security posture.

## Boundaries for the first phase

The first phase deliberately excludes:

- user-specific credentials for attached MCP servers;
- a polished or full-featured authoring UI;
- public sharing, selective teammate sharing, marketplaces, and social discovery;
- unrestricted browser networking from rendered artifacts;
- provider-specific graph or artifact behavior; and
- polecat swarms or a large multi-agent operating model.

These omissions keep the first milestone centered on durable data, precise MCP
access, immutable history, and an enforceable runtime boundary. Adding an
excluded capability requires an ADR that explains its effect on security,
persistence, provider neutrality, and privacy.
