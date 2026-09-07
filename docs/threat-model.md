# Folio Lattice hosted security threat model

- Version: 1.0 baseline
- Date: 2026-09-07
- Scope: hosted control plane, MCP interface, artifact store, sandbox/bridge,
  external MCP registry/broker, egress/TLS boundary, Slack connector,
  observability, backup, and release pipeline
- Decision status: implementation is blocked until the ADR evidence gates and
  the open blockers below have owners

This is a decision-oriented baseline, not a claim that the prototype already
implements these controls. The current product remains provider-neutral and
Docker-first; hosted services must preserve these boundaries.

## Security objectives

1. Only a verified principal mapped by Folio may act for a tenant.
2. Tenant content, versions, graph reachability, derived data, connectors,
   audit, and exports remain private unless an explicit, revocable grant allows
   access.
3. Untrusted artifacts, MCP servers, and tool metadata cannot obtain ambient
   credentials, arbitrary network access, or unreviewed side effects.
4. External calls are bounded, destination-controlled, TLS-protected,
   tenant-scoped, and attributable.
5. Security decisions are observable and minimally recorded without creating a
   shadow content store.
6. Recovery and releases preserve authorization, provenance, history, and
   deployment identity.

## Assets

| ID | Asset | Security property |
| --- | --- | --- |
| A1 | Artifact content, versions, blobs, previews, indexes | Tenant confidentiality, integrity, deletion policy |
| A2 | Graph edges, provenance, and metadata | Tenant confidentiality, history, integrity |
| A3 | Tenants, principals, actors, memberships, ACLs | Authorization integrity and availability |
| A4 | Sessions, access tokens, OAuth codes, refresh tokens | Confidentiality, audience binding, revocation |
| A5 | External MCP registry, allowlists, connector policy | Least privilege and change integrity |
| A6 | Secret references and brokered credentials | Confidentiality, scope, rotation, revocation |
| A7 | Slack messages and connector actions | Destination integrity, confidentiality, abuse resistance |
| A8 | Audit events, logs, metrics, traces, exports | Accountability without excess disclosure |
| A9 | Backups, migration state, restore images | Recoverability, integrity, tenant isolation |
| A10 | Source, dependencies, images, SBOM, provenance | Release integrity and traceability |

## Actors

- Internet attacker without credentials.
- Authenticated tenant member or compromised tenant account.
- Tenant admin who can change grants, connectors, or exports.
- Malicious artifact author or agent controlling HTML/JavaScript/CSS and bridge
  messages.
- Malicious or compromised external MCP server returning hostile metadata,
  resources, or tool results.
- Compromised identity provider account or misconfigured federation.
- Platform operator or time-bound break-glass operator.
- Network attacker able to observe, alter, replay, or redirect traffic.
- Malicious dependency, build input, registry artifact, or CI identity.

## Trust boundaries and flows

```text
Internet / browser / MCP client
          | TLS + verified identity
          v
Edge / TLS gateway -----> identity provider
          |
          v
Control plane + policy engine -----> metadata / blobs / indexes / audit
          |
          +----> sandbox origin -- narrow bridge --> policy engine
          |
          +----> MCP broker ---- approved connection ----> external MCP
          |
          +----> egress/TLS gateway ---- approved connector ----> Slack

CI source + locked dependencies --> signed build/provenance --> deployed image
                                                   |
                                      encrypted backup / restore boundary
```

The gateway, identity provider, data stores, sandbox origin, MCP broker,
external MCP, Slack, secret manager, observability system, backup system, and
CI/deployment system are separate trust decisions. Network reachability is not
authorization. Every flow is rechecked at its resource boundary.

## Threat register

| ID | Threat / abuse case | Impact | Baseline mitigation | Evidence |
| --- | --- | --- | --- | --- |
| T01 | Issuer, audience, key, nonce, state, PKCE, or redirect confusion | Account takeover or wrong tenant | Verified principal mapping; exact issuer/audience/redirect; PKCE; revocation | ADR 0011 auth-negative suite |
| T02 | Caller swaps tenant, actor, artifact, version, edge, or export ID | Cross-tenant disclosure or mutation | Tenant-first policy checks on every path; deny by default | ADR 0012 IDOR matrix |
| T03 | Stale grant/cache or revoked capability remains usable | Post-revocation access | Bounded invalidation; versioned policy; no stale fail-open | ADR 0012/0015 revocation exercise |
| T04 | Tenant admin escalates, locks out owner, or administers another tenant | Authorization integrity | Tenant-scoped admin, reauth, last-admin guard, break-glass separation | ADR 0012 admin tests |
| T05 | Tool description/resource URI/tool list poisons model or policy | Unauthorized side effect or data disclosure | Discovery quarantine, explicit allowlists, untrusted metadata, human/admin control | ADR 0013 hostile MCP tests |
| T06 | Inbound Folio token is replayed to external MCP | Confused deputy / privilege escalation | Audience validation; separate brokered upstream token; no passthrough | ADR 0013 token tests |
| T07 | Secret appears in env, artifact, prompt, trace, exception, or log | Third-party compromise and tenant impact | Secret-manager isolation, scoped injection, redaction, rotation | ADR 0013/0015 leak tests |
| T08 | User-controlled endpoint reaches metadata, loopback, private service, or rebinding IP | SSRF and credential/data theft | Egress allowlist, DNS/IP checks, redirect denial, TLS validation | ADR 0014 SSRF suite |
| T09 | Slack connector sends to wrong channel, leaks content, or spams | External disclosure, reputational/cost harm | Named connector, workspace/channel allowlist, narrow scopes, rate/cost/content policy | ADR 0014 Slack tests |
| T10 | TLS downgrade, invalid cert, spoofed proxy header, or proxy confused deputy | Traffic interception or auth bypass | TLS profile, authenticated gateway context, downstream reauthorization | ADR 0014 TLS/proxy tests |
| T11 | Large, slow, batched, recursive, or retried requests exhaust resources | DoS and cost amplification | Rate/concurrency/payload/time/memory/process/descriptor limits, circuit breakers | ADR 0015 load/fuzz suite |
| T12 | Audit injection, raw payload capture, or log access leaks data | Privacy loss and weak forensics | Minimal schema, sanitization, access control, tamper evidence, retention | ADR 0015 audit tests |
| T13 | Backup deletion, secret co-location, or restore without ACLs | Data loss or cross-tenant exposure | Encrypted immutable copies, separated keys, restore isolation tests | ADR 0016 restore exercise |
| T14 | Migration partial failure rewrites ownership/history/policy | Silent data corruption | Forward-only expand/contract, checksums, restore point, post-checks | ADR 0016 migration tests |
| T15 | Malicious artifact escapes sandbox or spoofs bridge/origin | Host or tenant compromise | Separate origin, restrictive sandbox/CSP, exact source/origin/schema checks | ADR 0004 + ADR 0017 adversarial suite |
| T16 | Malicious dependency/build input or unsigned image reaches production | Persistent supply-chain compromise | Locked inputs, SBOM, signed provenance/image, verification, scans | ADR 0017 release gate |
| T17 | Insider or break-glass operator reads/export data without accountability | Confidentiality and trust loss | Time/ticket-bound delegation, step-up, minimum scope, audited export | ADR 0011/0012/0015 |
| T18 | Cache, index, rendered artifact, export, or downloaded copy survives deletion | Data remanence | Derived-store purge, expiry, revoke checks, explicit residual-risk record | ADR 0012/0015/0016 |

## Assumptions

- The hosted identity provider, secret manager, backup store, and deployment
  system are operated under separate administrative controls and provide their
  documented security primitives.
- Tenant administrators are trusted to grant access inside their tenant but may
  be compromised; platform controls must limit blast radius.
- External MCP servers and all returned data are untrusted by default.
- Browser-origin isolation is necessary but not sufficient; bridge policy and
  server-side authorization remain mandatory.
- A downloaded copy cannot be recalled. The system can prevent future access,
  invalidate platform copies, and record the residual risk.
- Legal retention, data residency, and contractual requirements may require
  deployment-specific values and can supersede defaults only through reviewed
  policy.

## Release and operation gates

Hosted implementation cannot be called ready until:

1. ADR 0011-0017 open blockers have owners, selected values/providers, or an
   explicit time-bound risk acceptance.
2. Auth, tenant/ACL, sandbox, bridge, MCP, egress/Slack, rate/abuse, audit,
   migration, backup/restore, and supply-chain suites pass on the release
   artifact and representative Docker path.
3. `make check`, Docker build, Docker-to-MCP smoke, SBOM/provenance verification,
   restore evidence, and observability alert exercises pass.
4. An authorized independent pentest covers T01-T18's highest-risk boundaries;
   findings are remediated or accepted with owner, expiry, and compensating
   controls.
5. Incident response, revocation, secret rotation, backup restore, and
   customer communication runbooks are rehearsed.

## Residual risks and blockers

- Hosted IdP, secret manager, egress/gateway, observability, backup, and signing
  providers are not selected in the v0 repository.
- Tenant lifecycle, groups, legal retention/holds, data residency, and support
  access require product and legal decisions.
- Current prototype is not evidence of hosted authentication, ACL, proxy,
  secret, DR, or pentest readiness.
- External MCP and Slack behavior varies by provider; each connector needs a
  reviewed capability and scope catalog before enablement.

## References

See [security baseline research](research/2026-09-07-security-baseline.md) for
the source list and adopted implications. Existing product boundaries remain in
[ADR 0004](adr/0004-sandboxed-web-artifacts.md), [ADR 0005](adr/0005-docker-first-deployment.md),
and [ADR 0008](adr/0008-hyperset-first-consumer.md).
