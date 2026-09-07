# ADR 0015: Abuse controls and audit lifecycle

- Status: Accepted; implementation gated
- Date: 2026-09-07

## Context

Authenticated clients can still exhaust service resources, amplify third-party
costs, brute-force administration, or use large/slow MCP and artifact payloads
to bypass request-count limits. Audit data is needed for accountability but can
itself become a tenant data leak or a second secret store.

## Decision

Apply layered limits at the edge, service, tenant, actor, connection, tool,
operation, and sandbox boundaries. Each limit is finite, named, observable, and
fails closed for security-sensitive operations when its enforcement dependency
is unavailable. The baseline policy includes:

- token-bucket rate limits and concurrency ceilings per tenant, actor, IP for
  unauthenticated endpoints, connection, and expensive operation;
- maximum headers, JSON depth/array/string size, MCP message/tool argument and
  result size, artifact upload, export page, graph traversal breadth/depth,
  and pagination window;
- connect, DNS, TLS handshake, read, total, queue, and sandbox execution
  deadlines with cancellation propagation;
- bounded retries with jitter, circuit breakers, idempotency keys for writes
  and outbound side effects, per-tenant storage/compute budgets, and third-party
  spend alerts; and
- bounded memory, CPU, file descriptors, processes, output, and log volume for
  service and sandbox workers.

Deployments must publish concrete values before enabling production traffic.
The initial conformance profile is 1 MiB control-plane JSON, 10 MiB MCP
arguments/results, 30-second synchronous external-call deadline, 5-minute
maximum asynchronous job age, bounded pagination (10,000 items per export
job), and explicit artifact-size/worker quotas chosen per deployment. No zero,
null, or unlimited value means “use a safe default.”

Record a minimal, structured audit event for authentication decisions, policy
allow/deny, grants/revocations, admin changes, connector and secret lifecycle,
external calls, Slack sends, exports, rate-limit/abuse decisions, sandbox
violations, migrations, restores, and break-glass access. Events contain event
ID/time, tenant ID, actor ID/type, request/correlation ID, action, resource
type/opaque ID, outcome/reason, policy/version, connector, and source. Do not
store access tokens, secrets, raw documents, prompts, tool arguments/results,
Slack message text, full URLs with credentials, or unbounded user-agent/IP
data. Use keyed digests or coarse network metadata where correlation needs it.

Audit retention classes are explicit: security/admin/credential events default
to 365 days, request/abuse decision events default to 90 days, and debug
telemetry default to 30 days. A tenant or legal policy may extend a class with
an owner and expiry; no class is indefinite by default. Deletion removes hot
copies, exports, and derived copies subject to a recorded legal hold and backup
expiry policy. Audit access is tenant-scoped, read-only by default, and itself
audited.

Tenant admins can export their authorized audit events through an authenticated,
step-up-protected, asynchronous export. Export format is versioned NDJSON with
schema manifest, filters, time range, policy version, integrity digest, and
expiry. Export creation, download, failure, and deletion are events. Platform
break-glass export is separate, time-bound, and ticket-bound.

## Invariants

- No API, tool, export, or worker path has an unbounded request, payload,
  response, retry, memory, process, descriptor, cost, or execution-time budget.
- Limits are applied after actor/tenant identification where possible and cannot
  be bypassed by changing resource IDs, pagination, batching, or transport.
- Limit and audit failures do not disclose tenant existence or secrets.
- Security audit events are attributable, tamper-evident, tenant-scoped, and
  complete enough to reconstruct authorization decisions without content logs.
- Secrets and sensitive content never enter logs, traces, metrics labels, audit
  fields, error bodies, or exported audit files.
- Retention and purge are policy-driven, testable, and applied to copies and
  exports as well as primary log storage.

## Consequences

- Legitimate large or high-throughput tenants need an approved quota profile;
  limits may reject work instead of allowing unbounded degradation.
- Minimal audit data improves privacy but requires correlation IDs, reason
  codes, and a practiced incident process.
- Retention and export become product policy with storage and deletion costs.

## Non-goals

- Providing a complete DDoS scrubbing service or replacing provider billing
  controls.
- Capturing raw tenant content as a forensic archive.
- Selecting legal retention periods for every customer or jurisdiction.

## Rejected alternatives

- Per-IP rate limiting only: authenticated abuse and NAT bypass remain.
- Request-count-only limits: batch, large-payload, slow, and expensive calls
  still amplify resource use.
- Logging every payload for forensics: creates a high-value shadow data store.
- Indefinite logs “for compliance”: violates minimization and creates unclear
  disposal obligations.
- User-downloadable raw log files or bearer export URLs: bypasses live ACL and
  revocation checks.
- Fail-open rate limiter/audit pipeline for side-effecting calls: outage turns
  into an abuse or accountability bypass.

## Migration

1. Inventory every synchronous, asynchronous, bridge, connector, export, and
   storage operation and assign a finite budget.
2. Add shared limit and audit interfaces; migrate security-sensitive paths
   before tuning ordinary read throughput.
3. Redact existing logs and remove credentials/content fields before retention
   enforcement; preserve only approved event fields.
4. Add retention jobs, tenant-scoped export, legal-hold state, and purge
   verification across hot/cold/export stores.
5. Publish deployment-specific limits and retention schedule before hosted
   traffic; load-test the profile and record false-positive tuning.

## Evidence gates

- Fuzz and boundary tests cover oversized/deep/batched payloads, pagination,
  slow clients, retries, cancellation, concurrency, memory, process, and log
  volume.
- Load tests show per-tenant fairness, bounded third-party spend, circuit
  breaking, and no limiter bypass through alternate transports or IDs.
- Audit tests verify required event coverage, sanitization/log injection
  resistance, secret absence, tenant isolation, tamper detection, retention,
  purge, export filtering, export expiry, and audited log access.
- Incident exercise demonstrates reconstructing a cross-tenant attempt and
  connector action without reading raw tenant content.
- Independent pentest includes rate bypass, resource exhaustion, export/retention
  escape, log injection, and sensitive-data disclosure.

## Open blockers

- Deployment-specific quotas, retention legal basis, legal-hold owner, and
  tenant export authorization are not approved.
- Log/metric/trace backend and tamper-evidence mechanism are not selected.
- Cost budgets and alert recipients for external providers are not defined.
- Privacy review must approve network metadata and keyed-digest handling.

## References

- [OWASP API4:2023](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [NIST SP 800-92 log management](https://csrc.nist.gov/Projects/log-management/publications)
- [NIST SP 800-61 Rev. 3](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
