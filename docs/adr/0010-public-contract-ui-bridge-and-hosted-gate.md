# ADR 0010: Public-contract UI, narrow artifact bridge, and hosted gate

- Status: Accepted
- Date: 2026-09-05
- Supersedes: the private UI and storage-access details of ADR 0009

## Context

The first inspector and isolated renderer proved the browser boundary, but both
reach artifact state through private Python/storage seams. That is insufficient
evidence for an MCP-first product. The milestone also requires a mediated
attached-MCP call, while the current capability class is not connected to a
browser or audit path. Finally, process-configured tenant and actor values make
a useful local namespace but provide no hosted identity guarantee.

## Decision

All end-to-end UI and renderer state access crosses Folio Lattice's public
Streamable HTTP MCP endpoint through the official SDK. One small same-origin UI
gateway exposes only the existing nine Folio tools and validates origin, content
type, body size, tool name, arguments, timeout, and result size. It is a UI
adapter, not a second public product contract.

The renderer mounts no database or blob volume. It performs only
`artifact_read` through the MCP adapter. The public read result gains additive
artifact metadata and deterministic chunk descriptors so the UI can identify
an artifact and exercise `artifact_read_chunk` without a private query.

Rendered artifacts may send one schema-defined message to their parent. The
parent accepts it only when `event.origin === "null"` and
`event.source === preview.contentWindow`, then submits it to a bridge endpoint
that independently requires the exact configured control origin. The bridge
has one default attachment named `folio-lattice`; only `artifact_read`,
`artifact_search`, and `graph_traverse` are permitted. Tool schemas, body,
argument, result, and time limits are enforced. Decisions are audit logged
without content or credentials. Artifact code receives no credential and cannot
select a tenant, actor, upstream URL, header, or attachment policy.

Deployment mode is explicit. `local` is the default and is labeled as
unauthenticated local development in the UI and health response. `hosted`
refuses startup because no authentication adapter exists. A future hosted slice
must supply a verified principal-to-tenant/actor context before request routing;
this ADR does not choose OAuth, SSO, SCIM, roles, or sharing policy.

## Consequences

- Browser and renderer claims are supported by the same public contract as
  external clients.
- The control service makes a loopback/internal HTTP MCP call to itself for UI
  operations; that modest overhead is deliberate contract evidence.
- The bridge is useful for read-oriented artifacts but cannot mutate data or
  reach arbitrary MCP servers.
- Local development remains simple and honest. Hosted deployment remains
  unavailable rather than silently unauthenticated.
- ADR 0009 remains valid for the thin UI, dedicated origin, and sandbox policy;
  its private JSON/service and read-only volume choices are superseded here.

## Non-goals

- Credentials, sessions, grants, ACLs, sharing, revocation, SSO, or SCIM.
- A generic MCP proxy, remote attachment administration, or user approvals UI.
- A general REST API, frontend platform, collaborative editor, or file manager.
- Vector search, regex grep, backlinks, incoming traversal, or path queries.
- A claim of enterprise readiness or production security certification.

## Release rule

Evidence must use real MCP clients, service processes, browser interactions, and
fresh Docker volumes. It must cover allow and deny paths, hostile rendering,
cross-tenant reads/search/traversal, stale writes, malformed/large/slow bridge
calls, understandable UI failures, and restart persistence. Any unproved or
failed gate remains an explicit blocker.
