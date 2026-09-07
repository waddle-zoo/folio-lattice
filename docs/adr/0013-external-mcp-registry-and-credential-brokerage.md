# ADR 0013: External MCP registry and credential brokerage

- Status: Accepted; implementation gated
- Date: 2026-09-07

## Context

External MCP servers can read data, perform side effects, and return untrusted
tool metadata. A URL in a user document or a discovered tool list cannot be an
authorization decision. The platform needs an admin-controlled registry that
limits tools and resources per connection and keeps third-party credentials out
of artifacts, models, logs, and ordinary application workers.

## Decision

Create a tenant-scoped external MCP connection registry. A connection record
contains endpoint and transport, TLS/identity requirements, protocol version,
server identity evidence, approved tool names, approved resource URI patterns,
read/write/destructive classification, input/output limits, data classification,
credential reference, owner, status, expiry, and policy version.

Only a tenant admin can create, approve, change, disable, or rotate a
connection. Discovery (`initialize`, `tools/list`, `resources/list`) is
quarantined metadata. It never enables a tool or resource. `listChanged` never
widens an allowlist automatically. Tool annotations, descriptions, resource
URIs, and returned content are untrusted input; schemas are validated and
outputs are bounded before crossing the bridge.

At invocation, the broker evaluates tenant, actor, artifact, connection,
tool/resource, action, schema, size, rate, time, and data-classification policy.
It sends a fresh, short-lived, audience-bound upstream credential. Inbound
Folio tokens are never passed through. OAuth Authorization Code + PKCE with
exact redirect registration and rotated refresh tokens is preferred. When an
upstream does not support OAuth, a secret-manager reference may hold a narrowly
scoped credential, but the broker alone may retrieve it and must inject it only
for the approved connection. Unscoped user tokens, ambient process secrets, and
credentials in artifact code are prohibited.

Secrets are encrypted and isolated in a secret manager or equivalent boundary,
with separate access identity from the application database. Rotation,
revocation, failed retrieval, scope change, admin change, and each use are
audited without recording secret values or raw authorization headers. Disabled
or expired connections fail closed and revoke cached upstream sessions.

## Invariants

- No discovered endpoint, tool, resource, annotation, or server instruction
  grants capability.
- Every invocation matches an approved tenant connection and exact tool or
  resource allowlist; wildcard access is not a default.
- Upstream credentials are audience-scoped, least-privilege, short-lived where
  supported, isolated from tenant content and models, and never logged.
- Folio bearer tokens are not accepted by or forwarded to an upstream MCP.
- Connection policy is checked at call time, not only at registration or list
  time.
- Tool results cannot cause a new outbound destination, credential lookup, or
  policy change without a separate approved action.
- Admin changes are attributable, reauthenticated, versioned, and reversible by
  disabling the connection.

## Consequences

- External integrations require administrator work and a brokered capability
  manifest before they can run.
- The broker becomes security-critical infrastructure and needs its own tests,
  availability budget, and audit trail.
- Some MCP servers will not be compatible until they support scoped credentials
  or a reviewed static-secret adapter.

## Non-goals

- A public MCP marketplace, user-created arbitrary connections, or automatic
  discovery-based enablement.
- Passing Folio sessions, tenant bearer tokens, or ambient credentials through
  to external servers.
- Promising safe behavior from an external MCP server beyond bounded access.

## Rejected alternatives

- Generic MCP proxy with all tools enabled: turns discovery into privilege
  escalation and makes blast radius unbounded.
- Token passthrough: creates audience confusion and a confused deputy.
- Credentials in environment variables available to sandbox/processes: leaks
  through code, crash dumps, logs, and prompt-visible context.
- User-controlled arbitrary MCP URLs: creates SSRF and unreviewed data egress.
- Auto-approval based on tool annotations or server descriptions: MCP metadata
  is untrusted unless independently trusted and reviewed.
- One global credential per provider: breaks tenant isolation and revocation.

## Migration

1. Keep the current platform MCP as an internal first-party connection with a
   static, explicit capability manifest.
2. Introduce registry records and an invocation policy seam without enabling
   third-party calls.
3. Move any development integrations to secret references; remove credentials
   from compose files, source, fixtures, and process-wide environment.
4. Add one test MCP server with hostile metadata and a fake secret manager.
5. Enable one reviewed read-only connection in staging, then separately gate
   writes, OAuth brokerage, and sandbox-origin calls.

## Evidence gates

- Registry tests prove tenant/admin scoping, explicit tool and resource
  allowlists, deny-by-default discovery, `listChanged` non-expansion, and
  schema/size/time enforcement.
- Hostile MCP tests cover tool poisoning, resource URI traversal, malicious
  redirects, oversized results, prompt-like output, token passthrough, and
  confused-deputy attempts.
- Secret tests prove application logs, traces, exceptions, artifacts, model
  context, and crash output contain no credential material.
- OAuth integration test proves PKCE, exact redirect, audience/resource
  binding, refresh rotation, revocation, and failure closed on broker outage.
- Static-credential test proves scope restriction, rotation, zeroization, and
  tenant-specific revocation.
- Egress, sandbox, and bridge tests prove a document cannot invoke an
  unapproved connection or change its policy.
- Independent pentest scope includes broker, registry, OAuth callback, SSRF,
  token audience, tool poisoning, and secret isolation.

## Open blockers

- Secret manager and hosted OAuth brokerage owner are not selected.
- Supported MCP transport/version matrix and endpoint identity policy are not
  approved.
- Per-tenant data classification and approval workflow are not defined.
- Provider-specific scope catalogs need review before any write-capable
  connector is enabled.

## References

- [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [MCP security principles](https://modelcontextprotocol.io/specification/2025-06-18/index)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [OAuth 2.0 Security BCP (RFC 9700)](https://www.rfc-editor.org/rfc/rfc9700.html)
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
