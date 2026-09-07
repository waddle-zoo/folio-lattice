# ADR 0011: Hosted identity and verified principal mapping

- Status: Accepted; implementation gated
- Date: 2026-09-07
- Supersedes: the development-only authentication allowance in ADR 0007

## Context

Hosted Folio Lattice needs a provider-neutral way to authenticate people and
service actors without allowing a caller to choose its tenant, actor, or role.
The current prototype may use coarse local authentication, but that path cannot
become the hosted trust model. Federated identity also introduces issuer
confusion, token substitution, stale membership, replay, and account-recovery
risks.

## Decision

Use an external, administrator-selected OIDC identity provider for hosted human
login through Authorization Code + PKCE. SAML may be adapted at the identity
boundary later, but the internal contract is the same verified principal
record. There is no Folio-local password store in the hosted baseline.

Represent a human principal by the immutable pair `(issuer, subject)` and map it
through a server-owned membership table to an internal `tenant_id`, `actor_id`,
status, roles, and assurance metadata. The request context is created only by
the authentication boundary and is not accepted from request JSON, query
parameters, MCP metadata, forwarded headers, or artifact messages.

Validate issuer against an administrator-configured exact allowlist, signature
against discovered keys, `aud`, `iss`, `exp`, `nbf`, nonce, state, and PKCE
transaction binding. Use exact registered redirect URIs. Access sessions are
short-lived, audience-bound, revocable, and carry only an internal actor
reference. Service actors use separately issued credentials with explicit tenant
membership and never impersonate a human actor.

Unmapped, disabled, ambiguous, expired, or assurance-insufficient principals
are denied before any tenant lookup. Tenant membership changes invalidate active
sessions and cached authorization decisions within the revocation bound in ADR
0012.

## Invariants

- Caller cannot select or override `tenant_id`, `actor_id`, role, issuer, or
  authentication assurance in an application request.
- `(issuer, subject)` is globally unique; email, display name, and domain are
  attributes, never identity keys or authorization grants.
- Every authenticated request has exactly one verified actor context or fails
  closed with no resource lookup that could reveal tenant existence.
- Human and service actors are distinct types; service credentials cannot grant
  human-admin semantics by convention.
- Production configuration has no authentication bypass, default tenant, or
  development signing key.
- Tokens are accepted only for their intended Folio audience and are never
  forwarded to an external MCP or Slack.

## Consequences

- Hosted deployments depend on an IdP and a server-owned membership lifecycle.
- Provider-neutral internal actor context survives IdP changes, but claims must
  be deliberately mapped and revoked.
- Local development keeps a separate, visibly non-production path.

## Non-goals

- Folio-owned identity proofing, password recovery, or authenticator issuance.
- Choosing a hosted IdP, assurance level, SCIM implementation, or SAML adapter.
- Treating IdP groups, email domains, or display names as authorization truth.

## Rejected alternatives

- Local passwords in Folio: duplicates identity lifecycle and recovery risk.
- Email-domain auto-join: domain control does not prove tenant membership or
  intended actor authorization.
- Trusting `tenant_id` or roles in a client claim: creates confused-deputy and
  stale-membership paths; the server-owned membership table is authoritative.
- Client IP, mTLS alone, or network location as user identity: insufficient for
  remote and multi-tenant access.
- Accepting any JWT with a valid signature: issuer, audience, nonce, lifetime,
  and membership checks remain mandatory.

## Migration

1. Keep the development authenticator behind an explicit non-production mode
   and label all sessions it creates as development principals.
2. Add principal, tenant-membership, actor, session, and revocation records;
   backfill the single development tenant with explicit memberships.
3. Configure one hosted IdP and exact redirect URIs in staging. Require a
   successful mapping before enabling hosted traffic.
4. Run dual-read diagnostics only; do not dual-authorize. Cut hosted traffic
   over, then remove the development authenticator from production images.
5. Rotate development keys and invalidate all pre-cutover sessions.

## Evidence gates

- Authentication tests reject wrong issuer, audience, signature key, nonce,
  state, PKCE verifier, expiry, and redirect URI; tests cover key rotation.
- Integration tests prove an authenticated principal cannot read or mutate an
  unmapped or differently mapped tenant, including via MCP and exports.
- Session revocation test shows disabled membership blocks an existing session
  within the documented bound.
- Service-actor tests prove human-only administration cannot be reached with a
  service credential.
- Staging exercise records IdP configuration, assurance choice, recovery owner,
  and a redacted authentication decision trace.
- Hosted readiness requires independent review of the IdP trust configuration
  and the negative tests in the threat model.

## Open blockers

- Hosted IdP, tenant bootstrap owner, and account-recovery process are not
  selected.
- Required identity and authentication assurance levels per deployment are not
  approved.
- Session and membership revocation SLO needs an operator decision.
- SAML adapter, SCIM lifecycle, and delegated support access remain future
  work; none may be inferred from OIDC claims.

## References

- [NIST SP 800-63-4](https://pages.nist.gov/800-63-4/)
- [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final)
- [OAuth 2.0 Security BCP (RFC 9700)](https://www.rfc-editor.org/rfc/rfc9700.html)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
