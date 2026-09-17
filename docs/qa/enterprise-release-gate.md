# Enterprise release gate and evidence architecture

Status: QA plan for `fl-urj.3`; planning-only. No hosted or UI behavior is
implemented by this document.

This plan turns Folio Lattice's product boundary and ADRs into a release
decision. It distinguishes executable checks that exist on `main` from
planned public contracts. A planned check is not evidence until its command
exists, runs against the stated boundary, and produces the required artifact.

## Decision

An enterprise release is promotable only when every required gate is `PASS`.
`NOT RUN`, missing evidence, an unreviewed flaky retry, or a result against a
different image/configuration is `FAIL`. Security, tenancy, credential,
sandbox, audit, migration/restore, readiness, accessibility, and supply-chain
gates are release-blocking controls, not advisory test coverage.

The current prototype can pass only its local MCP, service, sandbox-header,
and Docker smoke gates. It cannot be described as hosted-enterprise-ready:
authentication, tenant-derived identity, sharing, external credential
mediation, UI, browser isolation, audit, backup/DR, readiness, and release
attestations are not implemented on `main`.

This plan preserves the existing boundary in ADRs 0001-0008. Planned hosted
features require their own implementation work and, where behavior changes
security, privacy, persistence, or deployment, a superseding or clarifying ADR
before the gate can move to `PASS`.

## Research basis

Repository sources are the [MCP-first contract](../adr/0002-mcp-first-knowledge-interface.md),
[immutable versions and provenance](../adr/0003-artifacts-versions-and-provenance.md),
[sandbox policy](../adr/0004-sandboxed-web-artifacts.md),
[Docker deployment seams](../adr/0005-docker-first-deployment.md),
[provider-neutral clients](../adr/0006-provider-neutral-clients.md),
[private tenancy and sharing](../adr/0007-private-tenancy-and-sharing.md),
[Hyperset public-contract rule](../adr/0008-hyperset-first-consumer.md), and
the [enterprise adoption evidence notes](../enterprise-adoption.md).

External verification references:

- [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/)
  supplies testable web-application security requirements and stable
  requirement identifiers.
- [NIST SP 800-218 SSDF](https://csrc.nist.gov/pubs/sp/800/218/final) supplies
  secure-development and vulnerability-management practices.
- [SLSA 1.2](https://slsa.dev/spec/v1.2/) supplies provenance and artifact
  verification expectations.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) supplies the accessibility baseline;
  automated checks do not replace manual evaluation.
- [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) supplies HTTP
  semantics used here, including `401`, `403`, `404`, `429`, and `Retry-After`.
- [OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/)
  supplies the logs, metrics, and traces vocabulary.
- [WHATWG iframe sandbox](https://html.spec.whatwg.org/multipage/iframe-embed-object.html)
  supplies browser sandbox semantics; CSP and network policy remain separate
  controls.

## Command and surface contract

Commands below use `BASE_URL`, `API_URL`, `UI_URL`, `TENANT_A_URL`, and
`TENANT_B_URL` as environment variables. CI must set them to the exact
deployment under test and record their resolved values without credentials.
Commands marked **current** run against this repository at the plan's source
commit. Commands marked **planned** are the names and interfaces future
implementation must provide; until then their gate is `FAIL`, never `N/A`.

### Current commands on `main`

```sh
# Product checks
make check
make test-unit
make test-e2e

# Docker boundary and public HTTP/MCP smoke path
make docker-build
make docker-up
FOLIO_BASE_URL=http://127.0.0.1:8000 python tests/test_docker_e2e.py
make docker-down

# Direct public endpoints
curl --fail-with-body "$BASE_URL/health"
curl --fail-with-body "$BASE_URL/mcp" \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# Provider-neutral stdio transport
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | PYTHONPATH=src python -m folio_lattice.server --transport stdio

# Existing sandbox policy checks
uv run pytest -q tests/test_sandbox.py
```

The current public MCP contract is `POST /mcp` with JSON-RPC methods
`initialize`, `notifications/initialized`, `tools/list`, `tools/call`, and
`resources/list`. Current tools are `artifact_create`, `artifact_write`,
`artifact_read`, `artifact_read_chunk`, `artifact_search`, `artifact_grep`,
`graph_link`, `graph_traverse`, and `artifact_versions`. Current HTTP exposes
only `GET /health` and `POST /mcp`; it has no authentication or tenant routing.

Current bounds that the release gate must keep visible are search limit 100,
grep/traversal limit 500, traversal depth 10, and 1,200-character text chunks.
The current server has no request-body, response-size, timeout, rate, or
concurrency limit; those are planned gates below.

### Planned stable hosted commands

The hosted contract uses an authenticated control-plane origin and a tenant
API origin. A tenant identifier supplied in an MCP argument is metadata only;
the authenticated subject and routed host are authoritative.

```sh
# Identity and tenant routing
curl --fail-with-body "$UI_URL/login"
curl --fail-with-body "$API_URL/v1/me" -H "Authorization: Bearer $TOKEN_A"
curl --fail-with-body "$TENANT_A_URL/v1/tenants/current" \
  -H "Authorization: Bearer $TOKEN_A"
curl --fail-with-body "$TENANT_A_URL/mcp" \
  -H "Authorization: Bearer $TOKEN_A" \
  -H 'Content-Type: application/json' --data @evidence/fixtures/initialize.json

# Hosted management API; all writes return a request/audit identifier
curl --fail-with-body -X POST "$TENANT_A_URL/v1/artifacts/$ARTIFACT_ID/grants" \
  -H "Authorization: Bearer $ADMIN_TOKEN_A" \
  -H 'Content-Type: application/json' \
  --data '{"subject":"user-b","role":"reader","expires_at":"2030-01-01T00:00:00Z"}'
curl --fail-with-body -X DELETE "$TENANT_A_URL/v1/artifacts/$ARTIFACT_ID/grants/$GRANT_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN_A"
curl --fail-with-body "$TENANT_A_URL/v1/audit?from=...&to=...&cursor=..." \
  -H "Authorization: Bearer $AUDITOR_TOKEN_A"

# External MCP attachment and capability mediation
curl --fail-with-body -X POST "$TENANT_A_URL/v1/mcp-attachments" \
  -H "Authorization: Bearer $ADMIN_TOKEN_A" \
  -H 'Content-Type: application/json' \
  --data @evidence/fixtures/attachment-slack.json
curl --fail-with-body "$TENANT_A_URL/v1/mcp-attachments/$ATTACHMENT_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN_A"

# Operations
curl --fail-with-body "$API_URL/health"
curl --fail-with-body "$API_URL/readyz"
curl --fail-with-body "$API_URL/metrics"
docker compose -f deploy/compose/hosted.yml config --quiet
docker compose -f deploy/compose/hosted.yml run --rm folio migrate check
docker compose -f deploy/compose/hosted.yml run --rm folio backup create \
  --output /evidence/backup \
  --backup-key-ref "$BACKUP_KEY_REF" \
  --recovery-key-ref "$RECOVERY_KEY_REF"
docker compose -f deploy/compose/hosted.yml run --rm folio backup verify \
  --input /evidence/backup \
  --key-id "$RECOVERY_KEY_REF"
docker compose -f deploy/compose/hosted.yml run --rm folio dr restore \
  --input /evidence/backup \
  --key-id "$RECOVERY_KEY_REF"
```

### Hyperset hosted-auth consumer gate (`fl-urj.5.2`)

The black-box consumer is executable once a hosted deployment and
standards-conformant test issuer are supplied:

```sh
make hosted-e2e
```

Configure the two public `/mcp` URLs, issuer discovery/JWKS URL, three token
sources, membership revoke hook, restart hook, revocation bound, source SHA,
and image digest as documented in
[`hyperset-hosted-e2e.md`](hyperset-hosted-e2e.md). The consumer is independent
of the in-process implementation. A run without that external deployment and
issuer must record `blocked`, never a local-mode pass. Redacted output follows
[`hyperset-hosted-evidence.schema.json`](hyperset-hosted-evidence.schema.json).

Planned browser commands use a pinned Playwright image/toolchain. The browser
must exercise the UI and the dedicated sandbox origin, not a mocked DOM.

```sh
pnpm exec playwright test tests/browser/auth-tenant.spec.ts
pnpm exec playwright test tests/browser/sharing-admin.spec.ts
pnpm exec playwright test tests/browser/slack-slice.spec.ts
pnpm exec playwright test tests/browser/accessibility.spec.ts
pnpm exec playwright test tests/browser/sandbox/*.spec.ts
pnpm exec playwright test tests/browser/bridge.spec.ts
```

Each planned browser test must emit JUnit, a trace on failure, screenshots for
manual checkpoints, and a network record with headers/cookies/secrets
redacted. A real browser is required for origin, cookie, CSP, navigation,
keyboard, and assistive-technology claims.

## Evidence package

Every gate run writes an immutable package at
`evidence/<release>/<gate>/<run-id>/`:

```text
manifest.json       # gate, source SHA, image digest, command, actor, times, result
command.txt         # exact command after safe environment expansion
stdout.log
stderr.log
results.json        # structured assertions and status codes
junit.xml           # when test runner supports it
screenshots/        # browser/manual evidence only
network.jsonl       # redacted request/response metadata, never bodies by default
attestations/       # SBOM, provenance, signatures, restore checksums
```

`manifest.json` records gate ID, release Git SHA, dependency lock hash, Docker
image digest, deployment/configuration fingerprint, environment class,
command, test fixture version, actor, independent reviewer (if any),
start/end time, result, failure IDs, evidence file SHA-256 values, and
redaction status. It never records bearer tokens, cookies, private keys,
credential values, full sensitive artifact content, or unbounded request data.

Evidence storage is encrypted, access-controlled, append-only for released
packages, and exportable by release ID. Re-running a failed command creates a
new run; it cannot overwrite the failed package. Production audit records use
the tenant's approved retention/legal-hold policy. Proposed defaults:

| Evidence class | Minimum retention | Custodian |
| --- | --- | --- |
| PR lint, type, unit, contract | 90 days | Build CI |
| Release functional, Docker, hosted smoke | 2 years | Release engineering |
| Adversarial security and vulnerability reports | 2 years | Security |
| Backup/restore and DR game-day | 2 years, including quarterly runs | SRE/DBA |
| Independent pentest and retest | 3 years | Security/compliance |
| Production audit events/exports | 7 years by default, or tenant/legal schedule if stricter | Compliance |

Retention values are policy defaults, not a claim about a legal requirement.
Legal hold, contractual tenant policy, and data minimization override them.
Expired evidence is deleted through a recorded retention job; deletion itself
must not delete held evidence.

## Gate register

Stage codes: `PR-S` static; `PR-U` unit; `PR-C` protocol/contract;
`PR-D` Docker; `NIGHTLY-A` adversarial; `PREPROD-E` hosted end-to-end;
`REL-O` operational/release; `MANUAL` human acceptance; `EXT` independent.

Owner codes: `QA` owns execution and evidence; `DEV` owns implementation;
`SEC` owns security review; `SRE` owns operations; `REL` owns build/release;
`UX` owns accessibility/usability; `DBA` owns persistence recovery;
`INT` owns Slack/external MCP fixtures; `PENTEST` is an independent vendor;
`PRODUCT` accepts user-task outcomes. “Independent” means reviewer is not the
author of the control or test under review.

| ID / requirement | Exact surface and command | Pass/fail criteria | Evidence and retention | Stage / ownership | Blocking semantics |
| --- | --- | --- | --- | --- | --- |
| `FL-URJ-01` Core artifact, graph, MCP, version, provenance | **Current:** `make check`; `make test-e2e`; Docker sequence above. MCP `initialize`, `tools/list`, then `tools/call` for all nine current tools. **Planned:** run same fixture through two independent MCP client stacks. | Pass only when create/read/chunk/search/grep/link/traverse/version/write round-trip; writes create immutable versions with parent precondition and provenance; ordering and limits are stable; malformed input fails closed. Any lost update, tenant argument bypass, or contract drift fails. | JUnit, redacted JSON-RPC transcripts, artifact/version IDs, image digest; PR 90d, release 2y. | `PR-U`, `PR-C`, `PR-D`; `QA` runs, `DEV` fixes, second-client reviewer independent. | Required check. Any core contract failure blocks merge/release. |
| `FL-URJ-02` Hosted authentication and tenant routing | **Planned:** `curl "$API_URL/v1/me"` with valid/expired/wrong-issuer/wrong-audience/malformed tokens; authenticated `POST "$TENANT_A_URL/mcp"`; browser `auth-tenant.spec.ts`. | Valid subject gets only assigned tenant; token signature, issuer, audience, expiry, nonce/session policy, secure cookie and logout work; host and subject agree; supplied `tenant_id` cannot switch context; unauthenticated requests return `401` with no data. | Redacted status/headers, subject-to-tenant mapping, audit IDs, browser trace; security 2y. | `PREPROD-E`, `NIGHTLY-A`; `SEC` and `QA`, identity owner manual review. | Hard block on auth bypass, confused deputy, tenant mismatch, token/cookie leakage, or missing evidence. |
| `FL-URJ-03` ACL, share, revoke, admin | **Planned:** grant/revoke `curl` commands; browser `sharing-admin.spec.ts`; authenticated MCP reads/writes before and after revoke. | Private by default; reader cannot write/admin; writer cannot grant/admin unless policy grants it; admin actions are scoped and audited; shares cover current/pinned version, graph reachability, derived indexes/previews, and attached MCP access; revoke takes effect within stated propagation bound and invalidates cached/share URLs. | Grant policy snapshot, before/after decisions, cache/revocation timestamps, audit export; release 2y, audit 7y. | `PREPROD-E`, `MANUAL`; `QA` executes, `SEC` reviews policy, `PRODUCT` accepts UX, independent authorization reviewer. | No release with default-public data, privilege escalation, stale access after revocation, or unaudited admin action. |
| `FL-URJ-04` External MCP credentials and allowlists | **Planned:** attachment API commands; browser `sandbox/*.spec.ts` and `bridge.spec.ts`; bridge request `{type:"mcp.call",server,method,name,arguments}` against a fixture. | Credentials remain server-side and absent from artifact code, URLs, browser storage, responses, logs, and evidence; only tenant/admin-approved server origin, tool/resource, schema, method, size, and time are allowed; no general proxy; revoke denies new calls and audit records decision. | Attachment policy, redacted bridge transcript, secret scanner result, deny/allow audit events; security 2y. | `PR-C`, `NIGHTLY-A`, `PREPROD-E`; `SEC` independent of `DEV`, `INT` owns fixture. | Hard block on credential disclosure, unallowlisted egress/call, schema bypass, or bridge call without audit. |
| `FL-URJ-05` Slack slice | **Planned:** `FOLIO_SLACK_MCP_URL=... pnpm exec playwright test tests/browser/slack-slice.spec.ts`; `FOLIO_SLACK_MCP_URL=... uv run pytest -q tests/hosted/test_slack_slice.py`. Fixture uses seeded channels/messages and fake credentials. | Admin attaches least-privilege Slack search; nontechnical user can select tenant, search an approved channel/time range, inspect source, and save an immutable artifact with actor/source/version provenance; no message or credential crosses tenant; revoke stops search. | Seed manifest, attachment/grant decisions, redacted search/result transcript, saved artifact/version IDs, screenshots; release 2y. | `PREPROD-E`, `MANUAL`; `INT` fixture owner, `QA` execution, `SEC` independent, `PRODUCT` task acceptance. | Blocks release slice if integration is in release scope; never waive cross-tenant or secret leakage. |
| `FL-URJ-06` TLS and secure transport | **Planned:** `openssl s_client -connect "$API_HOST:443" -servername "$TENANT_HOST" -verify_return_error </dev/null`; `curl --proto '=https' --tlsv1.2 --fail-with-body "$API_URL/readyz"`. | Valid chain/hostname; TLS 1.2+ policy; no plaintext except controlled redirect; HSTS, secure/HttpOnly/SameSite cookies, and no sensitive query values; certificate expiry/rotation alert works. | TLS transcript without session secrets, certificate fingerprint/expiry, header capture; release 2y. | `REL-O`, `PREPROD-E`; `SRE` executes, `SEC` independently reviews. | Hard block on invalid TLS, downgrade, insecure cookie, sensitive URL leakage, or expired cert. |
| `FL-URJ-07` Rates, timeouts, and resource bounds | **Current:** unit tests assert search/grep/traverse bounds. **Planned:** `uv run pytest -q tests/security/test_http_limits.py`; load fixture sends concurrent requests and oversized bodies to `/mcp`; browser verifies bounded error state. | Enforce request bytes, response bytes, per-tool arguments, chunk/read size, regex cost, traversal depth/results, call timeout, concurrency, per-subject/tenant/IP rate; reject before expensive work; `429` includes valid `Retry-After`; `413`/`408`/`422` are consistent and do not leak data. | Limit configuration fingerprint, load summary, status/header samples, resource metrics; security 2y. | `PR-U`, `NIGHTLY-A`, `PREPROD-E`; `SRE` defines budgets, `QA` runs, `SEC` reviews abuse cases. | Hard block on unbounded input/work, missing rate isolation, process exhaustion, or retry-unfriendly `429`. |
| `FL-URJ-08` Audit leakage, retention, and export | **Planned:** `GET /v1/audit`; export API `POST /v1/audit/exports`; `sha256sum` exported JSONL; fixture submits secrets, cross-tenant IDs, failed auth, grant/revoke, MCP, sandbox, admin, and backup actions. | Every security-relevant event has tenant, actor, action, target, decision, request/correlation ID, timestamp, and outcome; raw artifact content, tokens, credentials, and unnecessary PII never enter logs/audit; reads and exports are tenant/admin scoped; export is complete, ordered, integrity-checkable, retained, and access logged. | Redacted audit JSONL, export manifest/checksum, retention-job result, leakage scanner; audit 7y, security evidence 2y. | `PR-C`, `NIGHTLY-A`, `REL-O`; `SEC` and `Compliance` independent, `QA` runs. | Hard block on any sensitive leakage, missing event, unauthorized export, tamper gap, or retention failure. |
| `FL-URJ-09` Backup, migration, and disaster recovery | **Planned:** Docker `migrate check`, `backup create`, `backup verify`, `dr restore` commands above; run migration twice and restore into a clean isolated tenant. | Migrations are forward-only, ordered, repeatable, checksum-verified, and compatible with supported clients; backup includes metadata, blobs, indexes/derivation rebuild instructions, ACL, audit, and provenance; restore verifies hashes/counts/version parents/tenant isolation; hosted RPO <=15m and RTO <=60m or approved service target. | Migration report, backup manifest/checksums, restore diff, timed game-day log, owner sign-off; DR 2y, release 2y. | `PR-D`, `REL-O`, quarterly `MANUAL`; `DBA`/`SRE` execute, independent recovery witness verifies. | Hard block on failed restore, untested backup, destructive migration, RPO/RTO miss, or incomplete evidence. |
| `FL-URJ-10` Observability and readiness | **Current:** `curl "$BASE_URL/health"` and Docker healthcheck. **Planned:** `curl "$API_URL/readyz"`; `curl "$API_URL/metrics"`; inspect structured logs and trace for one MCP request. | `/health` proves process liveness only; `/readyz` fails when DB/blob/migration/dependency state is not safe; logs are structured and correlated; metrics cover latency, errors, saturation, auth denials, rate limits, bridge calls, queue/index lag, backup age; traces do not contain secrets; alerts fire and recover. | Health/readiness responses, metrics snapshot, redacted log/trace bundle, alert/recovery timestamps; release 2y. | `PR-D`, `PREPROD-E`, `REL-O`; `SRE` owns, `QA` verifies, independent on-call reviewer. | Hard block if readiness lies, observability is absent for a critical path, or alert/recovery test fails. |
| `FL-URJ-11` SBOM and software supply chain | **Current:** `make docker-build` and locked `uv.lock`. **Planned:** `docker buildx build --sbom=true --provenance=mode=max --tag "$IMAGE" --push .`; `syft "$IMAGE" -o cyclonedx-json`; `grype "$IMAGE"`; `cosign verify-attestation --type slsaprovenance "$IMAGE"`. | SBOM covers final image and runtime dependencies; lockfile and base-image provenance are recorded; no unapproved critical/high vulnerability; image is signed and digest-pinned; provenance identifies source SHA, builder, inputs, and release checks; build is reproducible enough to verify. | SBOM, vulnerability report, signature/provenance attestations, image digest, dependency/license policy result; release 2y. | `PR-S`, `REL-O`; `REL` owns build, `SEC` independently reviews. | Hard block on missing SBOM/provenance/signature, unapproved critical/high issue, mutable release tag, or unverifiable source. |
| `FL-URJ-12` Accessibility and nontechnical UX | **Planned:** `pnpm exec playwright test tests/browser/accessibility.spec.ts`; pinned axe/HTML checks; manual keyboard and screen-reader run on the same hosted build. | WCAG 2.2 AA baseline for product UI; keyboard-only completion; visible focus and meaningful labels/errors; no drag-only or color-only action; accessible auth; nontechnical participant can sign in, choose tenant, create/read/search/version an artifact, share/revoke, inspect Slack source, export audit, and understand loading/empty/error states without developer terminology. | JUnit/axe report, keyboard checklist, screen-reader/browser/OS matrix, screenshots/video, task notes and defects; release 2y. | `MANUAL`, `PREPROD-E`; `UX` executes, independent accessibility reviewer verifies, `PRODUCT` accepts task success. | Hard block on critical accessibility failure, blocked core task, misleading permission/error state, or unreviewed manual result. |
| `FL-URJ-13` Adversarial authentication | **Planned:** `uv run pytest -q tests/adversarial/test_auth.py`; browser token/session cases; HTTP replay/concurrency fixture. | Reject forged, expired, wrong-issuer/audience, missing-scope, fixation/replay, logout, CSRF, and session-confusion cases; return uniform safe errors; do not reveal account/tenant existence; audit decisions without secrets. | Test report, redacted tokens with fingerprints only, response matrix, audit correlation; security 2y. | `NIGHTLY-A`, `EXT`; `SEC` authors threat cases, independent security tester reviews. | Any bypass or material enumeration/session flaw blocks release. |
| `FL-URJ-14` Adversarial tenant isolation | **Planned:** `uv run pytest -q tests/adversarial/test_tenant_isolation.py`; run every MCP read/search/grep/chunk/version/graph/write and every API/UI route using tenants A/B. | Tenant B cannot read, infer, search, traverse, mutate, export, receive timing-sensitive existence, or obtain cached/indexed/backup/share data from A; IDs in errors, logs, metrics, traces, and exports are scoped/redacted. | Cross-tenant response matrix, timing/size summary, database/backup fixture checks, redacted logs; security 2y. | `NIGHTLY-A`, `PREPROD-E`, `EXT`; `SEC` independent of service owner. | Zero-tolerance hard block for cross-tenant data or authorization leakage. |
| `FL-URJ-15` Adversarial sandbox and browser isolation | **Current:** `uv run pytest -q tests/test_sandbox.py` checks CSP headers/capability defaults only. **Planned:** `pnpm exec playwright test tests/browser/sandbox/*.spec.ts --project=chromium`; Docker network inspection. | Dedicated origin differs from control plane; iframe/process policy denies top navigation, popups, downloads, storage inheritance, host DOM/cookies, arbitrary frames/workers/forms; CSP and egress deny direct network/XSS paths; packaged local assets still load; secrets absent. | Browser trace/screenshots, response headers, blocked-request list, Docker network evidence, secret scan; security 2y. | `PR-U`, `NIGHTLY-A`, `PREPROD-E`, `EXT`; `SEC` owns threat model, browser QA executes, independent pentest verifies. | Hard block on any DOM escape, credential access, external egress, CSP bypass, or unsafe default. |
| `FL-URJ-16` Adversarial MCP bridge | **Planned:** `pnpm exec playwright test tests/browser/bridge.spec.ts`; bridge fixture sends spoofed source/origin, unknown schema, oversized/slow/replayed/duplicate messages, unauthorized server/tool/resource, and malformed result. | Accept only exact origin/source and schema; bind request to tenant/artifact/attachment; enforce allowlist, scopes, size/time/rate limits, one response correlation, safe error, no credential forwarding, and audit for allow/deny; no confused deputy. | Redacted message corpus, allow/deny matrix, timeout/size results, audit IDs; security 2y. | `NIGHTLY-A`, `EXT`; `SEC` independent, `QA` runs fixture. | Zero-tolerance hard block for spoofed call, cross-tenant call, credential exposure, or missing audit. |
| `FL-URJ-17` Independent penetration test | **Planned deployment:** `docker compose -f deploy/compose/hosted.yml up -d`; independent vendor runs authenticated/unauthenticated web, API, MCP, tenant, sandbox, bridge, rate, and operational tests and returns signed report; vendor retest follows fixes. | Independent tester has production-like scope, test accounts for two tenants and admin/auditor roles, source/image/config fingerprints, and no developer-only bypass; no unresolved Critical/High; Medium findings have owner/date/mitigation or approved exception; retest confirms closure. | Signed scope, report, evidence references, findings register, management response, retest; pentest 3y. | `EXT`, before first enterprise release and annually/material auth/sandbox changes; `PENTEST` independent, `SEC` accepts. | Hard block on missing/expired pentest, unresolved Critical/High, or scope gap affecting a release-critical control. |

## CI and promotion policy

CI publishes one check per stage and attaches its evidence manifest. Required
branch checks are `PR-S`, `PR-U`, `PR-C`, and `PR-D` for every change. Changes
touching auth, tenant, persistence, sandbox, transport, Docker, dependencies,
or public schemas additionally require the relevant `NIGHTLY-A` or
`PREPROD-E` evidence before merge. `REL-O`, `MANUAL`, and `EXT` are required
before enterprise promotion, not after deployment.

Promotion order is:

1. Freeze source SHA, lockfile, configuration, fixtures, and image digest.
2. Run PR gates and publish immutable manifests.
3. Build, sign, attest, and deploy the exact image to isolated pre-production.
4. Run hosted auth/tenant, ACL, MCP, Slack, rate/bounds, audit, readiness,
   browser, sandbox, and bridge gates.
5. Complete manual nontechnical UX/accessibility review and operational
   backup/restore/DR evidence.
6. Obtain independent security review/pentest and remediation sign-off.
7. Release only the attested digest; run post-deploy `/readyz`, MCP smoke, and
   audit-correlation checks. Retain rollback evidence.

An exception is a recorded risk acceptance containing affected gate, exact
scope, evidence, severity, compensating control, owner, approver, expiry, and
remediation issue. Exceptions never permit a cross-tenant leak, credential
disclosure, sandbox escape/egress, forged authentication, failed restore,
missing release provenance, or an unresolved Critical/High pentest finding.
Flaky tests are quarantined only with an issue, owner, expiry, replacement
check, and independent approval; quarantine does not turn missing evidence into
`PASS`.

## First implementation order

The first risk-ordered vertical slice must be explicitly assigned after this
plan is reviewed. Recommended order is:

1. authenticated tenant-derived MCP request context and fail-closed policy;
2. hosted API/UI shell with readiness, structured audit, rate/size/time bounds;
3. browser sandbox and schema-validated MCP bridge with a fake external MCP;
4. ACL/share/revoke/admin and Slack fixture slice;
5. browser accessibility/nontechnical UX acceptance;
6. backup/migration/DR, SBOM/provenance, and independent pentest.

No later slice can claim enterprise readiness while an earlier release-blocking
gate remains planned, unimplemented, or unevidenced.
