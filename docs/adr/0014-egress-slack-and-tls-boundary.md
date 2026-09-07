# ADR 0014: Egress boundary, Slack connector, and TLS proxy

- Status: Accepted; implementation gated
- Date: 2026-09-07

## Context

Folio Lattice hosts untrusted artifacts and may call external MCPs or Slack.
Direct networking would allow SSRF, metadata-service access, data exfiltration,
credential theft, and uncontrolled spend. TLS termination and proxy identity
headers also create a boundary where a spoofed header or downgrade can bypass
authorization.

## Decision

All hosted ingress and external egress cross an explicit gateway boundary. The
gateway terminates client TLS only at a controlled edge, re-establishes TLS to
the service, and passes a cryptographically authenticated request context; the
service never trusts caller-controlled `X-Forwarded-*` or tenant headers.
External connector calls use an identity-aware egress proxy/gateway with
destination, method, path, DNS, IP, redirect, content, time, and rate policy.
TLS certificate chain and hostname validation are mandatory. TLS 1.3 is the
baseline; downgrade and invalid-certificate paths fail closed. Proxy access is
not a general HTTP proxy and is unavailable to sandbox code.

The gateway rejects loopback, link-local, private, multicast, Unix-socket,
numeric-obfuscated, and rebinding destinations unless an explicit operator
policy approves a private service identity. DNS is resolved and revalidated at
connection time; redirects are disabled by default and re-evaluated when
allowed. Connection policy is checked after resolution, not only on the input
hostname.

Slack is a named, separately approved outbound connector. It uses Slack OAuth
installation with granular scopes, workspace/team binding, token rotation and
revocation. The initial capability is a tenant-admin-approved, channel-allowlist
`chat.postMessage` operation. It does not accept arbitrary Slack webhook URLs,
arbitrary methods, user-selected destinations, remote attachments, or secrets
in message content. Message size, block count, URL policy, rate, spend, and
retry/idempotency behavior are bounded. Outbound audit records contain tenant,
actor, connector, workspace/channel identifiers, policy version, result, and a
keyed content digest, not message text or tokens.

## Invariants

- Sandboxed artifacts have no direct network, DNS, socket, or proxy credential.
- Every outbound request has a reviewed connector identity, destination policy,
  tenant/actor context, finite budget, and auditable outcome.
- A proxy or gateway cannot elevate a caller; it conveys verified context and
  the downstream service reauthorizes the operation.
- No caller-controlled forwarded header selects tenant, actor, scheme, or
  destination.
- TLS hostname/certificate validation is performed for every external hop; no
  plaintext fallback or silent TLS downgrade exists.
- Slack calls are limited to approved workspaces/channels/scopes and are
  independently revocable from Folio login.
- Redirects, DNS answers, IP ranges, request bodies, responses, and retries are
  bounded and rechecked against policy.

## Consequences

- External calls gain a policy and proxy hop, with added latency and failure
  modes that must be observable.
- Slack integrations require workspace/channel governance and narrow scopes.
- Some private-service integrations need an explicit operator-approved route.

## Non-goals

- General web browsing, arbitrary webhooks, or a user-controlled HTTP proxy.
- Inbound Slack event handling or Slack search/read capabilities in this ADR.
- Replacing application authorization with network allowlists or proxy headers.

## Rejected alternatives

- Direct artifact/browser egress: violates the sandbox boundary.
- Generic `fetch(url)` or user-supplied webhooks: SSRF and exfiltration path.
- Trusting `X-Forwarded-User`/`X-Tenant-ID`: headers are spoofable outside a
  tightly authenticated proxy channel.
- TLS passthrough with no service-side identity: loses policy and audit context.
- Disabling certificate verification or pinning one mutable public cert:
  either enables MITM or creates brittle rotation failures; use CA/hostname
  validation and explicit service identity where needed.
- Slack bot token in application configuration or per-message webhook URLs:
  broad, hard-to-revoke credentials and no tenant/channel policy.

## Migration

1. Make local Compose egress deny-by-default and document a loopback test
   exception; do not broaden artifact networking.
2. Add gateway/egress interfaces and policy decision records while keeping
   external connectors disabled.
3. Route hosted health, MCP, and storage traffic through TLS-validated service
   hops; remove direct service-to-internet paths.
4. Introduce Slack in a development workspace with a minimal scope and a
   channel allowlist; enable token rotation before staging use.
5. Remove legacy webhooks/static tokens and revoke them after migration evidence
   is archived without secrets.

## Evidence gates

- Network tests show sandbox processes cannot resolve or connect to public,
  private, loopback, link-local, or metadata destinations.
- Proxy tests reject DNS rebinding, redirects to private IPs, alternate numeric
  address forms, invalid certificates, TLS downgrade, spoofed forwarded
  headers, oversized payloads, and over-budget retries.
- End-to-end test proves gateway context cannot cross tenants and downstream
  service authorization still runs.
- Slack test covers OAuth install, exact workspace/channel policy, scope
  denial, rotation, revocation, rate/cost limit, content redaction, and retry
  duplication behavior.
- TLS configuration scan and certificate-expiry alert are clean; evidence
  names the supported TLS profile and proxy version.
- Independent pentest includes SSRF, proxy trust, DNS rebinding, TLS, Slack
  exfiltration, and abuse paths.

## Open blockers

- Hosted gateway/egress implementation and operating owner are not selected.
- Private-service destination policy and DNS architecture need approval.
- Slack app owner, approved scopes, workspace/channel governance, and data
  residency requirements are not selected.
- TLS certificate issuance/rotation and mTLS requirements between services are
  not finalized.

## References

- [TLS 1.3 (RFC 8446)](https://www.rfc-editor.org/rfc/rfc8446.html)
- [Recommendations for secure TLS (RFC 9325)](https://www.rfc-editor.org/rfc/rfc9325.html)
- [NIST SP 800-207A](https://csrc.nist.gov/pubs/sp/800/207/a/final)
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Slack authentication](https://docs.slack.dev/authentication/)
- [Slack token rotation](https://api.slack.com/authentication/rotation)
