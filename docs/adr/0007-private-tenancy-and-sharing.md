# ADR 0007: Private tenancy now, sharing in v2

- Status: Accepted
- Date: 2026-09-05

## Context

Company knowledge and generated artifacts may contain sensitive data. An early
prototype may be reachable with weak or development-only access controls, but
that expedience must not define ownership or make public access the default.
Selective and public sharing introduce policy, identity, revocation, indexing,
and data-leakage questions that are not required to validate the first milestone.

## Decision

Model tenant ownership on every artifact, version, graph edge, derived index,
provenance record, MCP attachment, and audit event. All repository and service
queries are tenant-scoped, and newly created artifacts are private by default.
Cross-tenant identifiers must not be resolvable through reads, search results,
errors, timing-sensitive shortcuts, or graph traversal.

The first milestone may use a simple development tenant and coarse local
authentication, but interfaces must carry an explicit tenant and actor context.
Development bypasses are configuration-gated, visually and operationally clear,
and forbidden in production mode.

Selective sharing with teammates and public sharing are deferred to v2. The core
model should reserve room for explicit grants and policy evaluation, but v1 does
not expose sharing APIs or pretend that URL secrecy is authorization. Future
sharing must cover versions, graph reachability, derived previews/indexes,
attached MCP access, auditability, expiration, and revocation in a superseding
ADR.

## Consequences

- Tenant scope is a required parameter and test dimension throughout the stack.
- Local setup has a little more ceremony even before full identity support.
- Private defaults reduce accidental exposure and allow sharing to be added as
  an explicit policy transition.
- Prototype deployments without production authentication must be labeled and
  isolated accordingly.

## Non-goals

- Selective teammate sharing or public sharing in v1.
- Public galleries, marketplaces, or social discovery.
- Enterprise SSO, SCIM, or a complete role-management UI in the first milestone.
- Treating an early technically public prototype as the intended access model.

## Open questions

- What subject, group, role, and service-principal model should v2 grants use?
- How should sharing a moving artifact differ from sharing a pinned version?
- What deletion and revocation guarantees apply to caches, indexes, exports, and
  previously rendered artifacts?
