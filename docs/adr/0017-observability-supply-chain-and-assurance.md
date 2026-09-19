# ADR 0017: Observability, supply chain, and security assurance

- Status: Accepted; implementation gated
- Date: 2026-09-07

## Current release decision

As of 2026-09-19, enterprise promotion is **OPEN / BLOCKED**. Candidate
`8183d6603c4584673070bc29e9f4a6f823de114f` is a clean descendant of canonical
`eae08dd75f93bfa3b38215c495cd5e7ef481be` and passed independent focused review
for malformed external-MCP registration handling. It is landed on `main`, but
this narrow fix does not satisfy the ADR's full observability, supply-chain,
security-assurance, or independent-review gates. Promotion stays blocked until
the required exact-commit evidence and residual-risk decisions are recorded.

## Context

Security controls fail silently when authentication, tenant policy, sandbox
boundaries, bridges, egress, migrations, or backup jobs have no usable signal.
Hosted deployment also adds dependency, container, build, and release trust
that is not covered by application tests alone.

## Decision

Use structured, correlated logs, metrics, traces, health/readiness signals, and
security alerts across edge, identity, policy, storage, sandbox, MCP broker,
egress, Slack, migration, backup, and restore paths. Observability follows ADR
0015 minimization: no secrets, raw tenant content, prompts, tool payloads, or
high-cardinality identity data in labels. Alert on authentication failures and
anomalies, cross-tenant denials, privilege/grant changes, connector/secret
changes, token/audience failures, sandbox/bridge violations, egress blocks,
rate/abuse saturation, migration/backup failures, dependency vulnerabilities,
and unexpected data export.

Every release publishes an SBOM in a standard machine-readable format with
direct and transitive dependencies, versions, licenses, source references, and
known vulnerability status. Dependencies and base images are pinned by digest
where practical, reviewed, scanned, and updated through a documented process.
CI produces signed build provenance covering source revision, builder, inputs,
dependencies, and artifact digest; deployment verifies the expected artifact,
provenance, and image signature before promotion.

Security verification is layered and release-blocking:

- unit and contract tests for authentication, policy, tenant filters, ACLs,
  broker, egress, rate/size/time limits, audit, migration, and restore logic;
- adversarial integration tests for auth bypass, tenant/IDOR, sandbox escape,
  bridge spoofing, hostile MCP metadata, token passthrough, SSRF, Slack
  exfiltration, resource exhaustion, and log/secret leakage;
- fuzz/property tests for protocol, schemas, URLs, identifiers, graph queries,
  and artifact/bridge messages; and
- an independent, authorized penetration test before hosted general
  availability and after material changes to identity, authorization, sandbox,
  bridge, broker, or egress boundaries. Findings block release until risk is
  accepted by the security owner with evidence and expiry.

## Invariants

- A release is identifiable by source, dependency, build, image, SBOM,
  provenance, and deployment digests.
- Production deployment verifies the artifact it runs; CI success alone is not
  deployment evidence.
- Security events and control failures are observable without logging secrets or
  tenant content.
- Negative tests for auth, tenant, sandbox, and bridge boundaries run in CI and
  in a representative container/deployment smoke path.
- Independent testing has a defined scope, authorization, report, remediation
  owner, and retest; “no findings” is not assumed from internal tests.
- Vulnerability, dependency, certificate, backup, and pentest exceptions are
  time-bound, reviewed, and visible to release owners.

## Consequences

- CI and release processes gain evidence artifacts, verification steps, and
  external testing cost.
- Security signals are actionable without making logs a copy of tenant data.
- A hosted release may be delayed by an unresolved high-risk finding or missing
  provenance even when functional tests pass.

## Non-goals

- Claiming a compliance certification or eliminating all zero-day risk.
- Choosing a specific observability, SBOM, signing, or pentest vendor here.
- Replacing secure design and code review with scanners or pentest results.

## Rejected alternatives

- Application logs without metrics/alerts or trace correlation: failures become
  hard to detect and investigate.
- SBOM generated only after release or based on declared direct dependencies:
  omits transitive/runtime components and cannot prove deployed contents.
- Unsigned mutable tags and “latest” base images: deployment identity drifts.
- Happy-path unit tests as boundary evidence: do not exercise hostile origins,
  tenant swaps, crafted MCP metadata, or real transport behavior.
- Internal-only security review: independence and external attacker coverage
  are missing.

## Migration

1. Define event, metric, trace, alert, SBOM, provenance, and release-manifest
   schemas; inventory existing CI and deployment artifacts.
2. Add negative auth/tenant/sandbox/bridge tests to the current test suite and
   make them required before hosted changes.
3. Add SBOM/provenance generation and verification to the existing Docker/CI
   path; pin base image and dependency inputs.
4. Add representative container/e2e security smoke tests and restore/egress
   evidence links to release records.
5. Contract an independent pentest, remediate/retest findings, and publish the
   accepted residual-risk register before hosted general availability.

## Evidence gates

- `make check`, Docker build, Docker-to-MCP smoke, and security integration
  suites pass on the exact release commit.
- Auth, tenant, sandbox, and bridge adversarial suites include cross-tenant,
  malformed-message, hostile-origin, credential-exfiltration, and confused-
  deputy cases.
- Release contains SBOM, vulnerability scan, signed provenance, image digest,
  dependency lock evidence, and deployment verification result.
- Dashboards and alerts receive synthetic deny/allow, egress, backup, restore,
  and rate-limit events without sensitive payloads.
- Independent pentest report, remediation evidence, retest, and residual-risk
  acceptance are complete before hosted general availability.

## Open blockers

- Observability backend, alert ownership, and incident response rota are not
  selected.
- SBOM/provenance/signing tooling and deployment verifier are not selected.
- Pentest scope, provider, budget, and authorization window are not assigned.
- Release vulnerability thresholds and exception authority are not approved.

## References

- [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/)
- [NTIA SBOM minimum elements](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom)
- [SLSA provenance v1.2](https://slsa.dev/spec/v1.2/provenance)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
