# Security baseline research

- Date: 2026-09-07
- Scope: hosted identity, tenancy, external MCP, egress, abuse controls,
  resilience, and release assurance
- Decision: use the following evidence to set the security baseline before
  hosted implementation; implementation remains gated by the ADR evidence
  checks and the open blockers recorded there.

## Evidence and adopted implications

| Evidence | Relevant implication for Folio Lattice |
| --- | --- |
| [NIST SP 800-63-4](https://pages.nist.gov/800-63-4/) | Select identity, authentication, and federation assurance from service risk; do not treat an asserted email address as tenant authorization. |
| [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final) and [SP 800-207A](https://csrc.nist.gov/pubs/sp/800/207/a/final) | Authenticate and authorize subjects and services at the resource boundary; network location is not tenant trust. Egress policy belongs at an identity-aware gateway. |
| [OAuth 2.0 Security BCP, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html) | Use authorization code plus PKCE, exact redirect matching, sender/audience binding where available, short-lived access tokens, and asymmetric client authentication where practical. |
| [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) | Validate token audience, never pass through an inbound token to an upstream MCP, use resource indicators, and keep upstream tokens separate. |
| [MCP security principles](https://modelcontextprotocol.io/specification/2025-06-18/index) and [tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | Resource data needs consent and tool metadata is untrusted unless the server is trusted. Tool calls need visible policy and human/admin control for consequential actions. |
| [OWASP API4:2023](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/) | Bound rate, concurrency, payload, pagination, memory, file descriptors, execution time, and third-party spend; request counting alone is insufficient. |
| [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) | User-controlled endpoints require positive destination policy, private-network denial, and redirect/DNS revalidation. |
| [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) | Log security decisions and exports, sanitize untrusted fields, exclude secrets and unnecessary content, restrict access, and dispose of logs on a defined schedule. |
| [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/) and [WSTG](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/) | Treat authentication, authorization, tenant isolation, input validation, transport security, and testing as release requirements rather than documentation claims. |
| [Slack authentication](https://docs.slack.dev/authentication/) and [token rotation](https://api.slack.com/authentication/rotation) | Slack access is scope-controlled and credential-bearing; use OAuth installation, narrow scopes, expiration/rotation, and revocation instead of long-lived shared tokens or arbitrary webhooks. |
| [TLS 1.3, RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html) and [TLS guidance, RFC 9325](https://www.rfc-editor.org/rfc/rfc9325.html) | Require certificate and hostname validation, disallow downgrade paths, and keep TLS on every external and service hop. |
| [NIST SP 800-34](https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final) | Define recovery priorities, backup/restore procedures, alternate recovery, and exercises; a backup that has never been restored is not evidence of recoverability. |
| [NTIA SBOM minimum elements](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom) and [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) | Ship component inventory and verifiable build provenance with releases; verify what is deployed, not merely what CI intended to build. |

## Design choices

The evidence supports seven narrow decisions:

1. Hosted identity is federated and provider-neutral. A verified principal is
   the tuple `(issuer, subject)`, and a separate, server-owned membership table
   maps it to tenant and actor IDs.
2. Tenant data is private by default. Grants are explicit, resource-scoped,
   revocable, and checked on every read, write, traversal, export, and
   connector call.
3. External MCP is an admin-managed capability registry. Discovery never grants
   access. Tool names and resource URI patterns are explicit allowlists, and
   upstream credentials are brokered, scoped, short-lived, isolated, rotated,
   and auditable.
4. All external traffic leaves through an identity-aware egress/TLS boundary.
   Slack is a separately approved connector with narrow scopes and destination
   policy, not a general outbound URL feature.
5. Every request and expensive operation has finite rate, concurrency, payload,
   memory, and time budgets. Audit events are minimal, tenant-scoped, exportable,
   and retained only for an approved period.
6. Persistence changes are forward-only and recoverable. Encrypted immutable
   backups, restore exercises, and explicit RPO/RTO targets are release gates.
7. Security evidence is continuous: structured observability, SBOM/provenance,
   adversarial auth/tenant/sandbox/bridge tests, and an independent pentest
   before hosted general availability.

## Non-adopted shortcuts

- Email-domain auto-join, client-supplied tenant IDs, or JWT role claims as the
  sole authorization source.
- Public-by-default artifacts, bearer URLs as shares, or authorization hidden
  in search/index/graph implementation details.
- A generic MCP proxy, token passthrough, auto-enabled discovered tools, or
  credentials exposed to artifact code or models.
- Direct sandbox networking, arbitrary Slack webhooks, permissive URL fetches,
  or a TLS proxy that silently trusts forwarded identity headers.
- Raw request/document/tool payloads in logs, indefinite audit retention, or
  backups that bypass tenant retention and deletion policy.
- “Unit tests pass” as evidence for isolation, restore, bridge, egress, or
  production authentication.

## Resulting records

- [ADR 0011: hosted identity and verified actor mapping](../adr/0011-hosted-identity-and-principal-mapping.md)
- [ADR 0012: private tenancy and ACL sharing](../adr/0012-private-tenancy-acl-and-revocation.md)
- [ADR 0013: external MCP registry and credential brokerage](../adr/0013-external-mcp-registry-and-credential-brokerage.md)
- [ADR 0014: egress boundary, Slack, and TLS proxy](../adr/0014-egress-slack-and-tls-boundary.md)
- [ADR 0015: abuse controls and audit lifecycle](../adr/0015-abuse-controls-and-audit-lifecycle.md)
- [ADR 0016: backup, migration, and disaster recovery](../adr/0016-backup-migration-and-disaster-recovery.md)
- [ADR 0017: observability, supply chain, and assurance](../adr/0017-observability-supply-chain-and-assurance.md)
- [Threat model](../threat-model.md)
