# ADR 0012: Private tenancy, ACL grants, and revocation

- Status: Accepted; implementation gated
- Date: 2026-09-07
- Supersedes: ADR 0007

## Context

The original v0 boundary reserved sharing for later. Hosted use now requires a
minimal, explicit sharing model without weakening private-by-default storage.
Authorization must cover artifacts, immutable versions, graph edges, derived
indexes/previews, MCP attachments, exports, and administrative actions. A grant
that works only on the primary artifact is not a tenant boundary.

## Decision

Every protected row and blob carries a tenant owner. Every read, write, search,
grep, graph traversal, version read, derived-data read, export, sandbox bridge
call, and external-MCP call evaluates a server-side policy with the verified
actor context from ADR 0011. Missing policy is deny.

Use explicit ACL grants with:

- subject: internal actor, tenant group, or service actor;
- resource: tenant, artifact, version, graph edge, attachment, export, or
  administrative capability;
- action: read, write, delete, share, export, administer, or invoke;
- optional conditions: pinned version, channel/resource scope, expiry, and
  purpose; and
- provenance: grant creator, creation time, reason, policy version, and
  revocation time.

Artifacts, history, graph reachability, indexes, and previews are private by
default. A tenant admin may grant to a named member or tenant-managed group;
anonymous/public links are not part of this baseline. Tenant administration is
tenant-scoped. A platform break-glass operator, if later introduced, receives a
time-bound, ticket-bound, read-minimized delegation and is never a normal tenant
admin.

Granting an artifact grants the artifact's versions and derived representations
only when the policy explicitly says so; otherwise each resource needs its own
grant. Revocation blocks new authorization decisions immediately and invalidates
cached decisions and issued download/bridge capabilities within the documented
bound. Revoke also queues purge/invalidation for caches, indexes, exports, and
rendered artifacts; previously downloaded copies cannot be recalled and are
recorded as residual risk.

Admin safeguards prevent removing the last active tenant admin, require
reauthentication for membership, grant, revoke, export, and connector changes,
and record the before/after policy hash.

## Invariants

- No tenant-owned identifier is resolvable across tenants through content,
  search, errors, graph edges, timing-sensitive shortcuts, or export metadata.
- Ownership and ACL checks occur before data fetch, index lookup, blob stream,
  tool invocation, or side effect.
- A valid grant never widens the tenant boundary; cross-tenant sharing is
  rejected unless a future ADR defines a separate, explicit federation model.
- Deny is the default for new resources, new subjects, unknown actions, and
  stale policy caches.
- ACL changes are versioned, attributable, auditable, and effective on all
  access paths, including historical versions and derived data.
- Tenant admins cannot administer another tenant or create an unbounded
  platform-admin path.

## Consequences

- Every protected representation needs tenant metadata and policy coverage.
- Sharing is explicit and reviewable, but policy evaluation and invalidation add
  latency and operational work.
- Revocation stops platform access; downloaded copies remain a stated residual
  risk.

## Non-goals

- Public links, anonymous sharing, or cross-tenant collaboration.
- A universal relationship-based authorization language or policy marketplace.
- Legal deletion guarantees beyond the approved retention and hold policy.

## Rejected alternatives

- Public-by-default artifacts or secret URLs: discoverability and revocation
  failures are unacceptable for company knowledge.
- Role-only authorization: too coarse for artifact, version, connector, and
  export boundaries.
- ACL checks only at the API controller: search, graph, cache, and bridge paths
  would bypass the policy.
- Copying tenant membership from IdP groups into tokens: stale groups and
  provider-specific claims become authorization truth.
- Granting an entire tenant for convenience: violates least privilege and
  makes revocation hard to reason about.

## Migration

1. Add tenant ownership to every existing artifact, version, edge, index,
   attachment, provenance, and audit record; fail migration if any row is
   ambiguous.
2. Create an owner grant for each existing artifact in the development tenant.
3. Add a central policy evaluation seam and route every read/write/search/
   traversal/export/bridge path through it before exposing sharing controls.
4. Backfill the development actor as tenant admin only in non-production mode.
5. Introduce named-member grants behind an admin-only feature flag; invalidate
   all authorization caches when enabled and on each grant change.

## Evidence gates

- Matrix tests cover owner, member, group, service actor, expired grant,
  revoked grant, disabled actor, unknown resource, cross-tenant IDOR, and
  historical-version access.
- Search, grep, graph traversal, indexes, previews, exports, sandbox bridge,
  and MCP attachment tests all prove tenant filtering before data access.
- Revocation exercise proves new reads and cached capabilities fail within the
  published bound and that purge jobs cover all derived stores.
- Admin tests prevent last-admin lockout, unauthorized role escalation,
  cross-tenant administration, and unreauthenticated exports.
- A restore test (ADR 0016) reruns the cross-tenant negative matrix after
  recovery.
- Independent pentest scope includes IDOR, privilege escalation, cache
  invalidation, graph leakage, and export paths.

## Open blockers

- Tenant group source and lifecycle are not selected.
- Revocation bound and cache technology are not selected.
- Legal requirements for immutable audit and deleted-data purge need a policy
  owner.
- Cross-tenant collaboration and public sharing remain out of scope.

## References

- [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final)
- [OWASP Web Security Testing Guide: authorization](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/05-Authorization_Testing/)
- [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/)
