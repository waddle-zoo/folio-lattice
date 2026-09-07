# Folio Lattice enterprise-readiness plan

- Status: Phase 0 planning only
- Date: 2026-09-07
- Scope: private, company-hosted Folio Lattice service with MCP as its first
  public interface

## Claim rule

**Folio Lattice is not enterprise-ready until every gate in this document has
passed with recorded evidence.** A missing, stale, disputed, or indirectly
inferred result is a failed gate. A passing unit test, a private service test,
or a local prototype cannot substitute for public MCP, UI, Hyperset, browser,
tenant, persistence, Docker, or operational evidence.

The hosted claim is separate from the bounded local milestone. Local mode may
remain useful for trusted development, but it must be labeled unauthenticated
local development. Hosted mode must refuse to start until a verified identity
and tenant context exist. No document, demo, README, release note, or sales
material may call local mode enterprise-ready.

This commit records requirements, dependencies, owners, acceptance checks, and
evidence targets only. It does not begin implementation.

## Product boundary

Folio Lattice stays small and generic:

- Artifacts are stable logical identities backed by arbitrary bytes,
  immutable versions, provenance, and typed graph relationships.
- MCP is the provider-neutral contract for create, write, read, chunk, search,
  grep, link, traversal, and version history. HTTP and stdio are transports,
  not separate product models.
- HTML, JavaScript, and CSS remain untrusted artifacts in a dedicated,
  opaque-origin sandbox. They have no direct external egress or host-origin
  access. Mediated calls reach only explicitly attached MCP capabilities.
- The UI is a thin inspection and core-loop client over public MCP. It is not a
  second persistence model, general REST API, frontend platform, or full
  authoring suite.
- Hyperset is the first black-box consumer, not a Folio dependency. It uses
  public MCP/HTTP only and receives no database, blob mount, private import, or
  special tool.
- Private tenancy and verified hosted identity are readiness requirements.
  Selective/public sharing, user-specific MCP credentials, marketplaces,
  vector search, a separate graph database, and broad multi-agent orchestration
  remain deferred unless a new ADR changes the boundary.

## Traceability map

The following IDs normalize every `fl-urj` requirement into a delivery and
evidence obligation. A phase is complete only when all requirements mapped to it
have passed their public acceptance and evidence checks.

| ID | Requirement | Risk | Planned coverage |
| --- | --- | --- | --- |
| FL-URJ-R01 | Arbitrary files are first-class artifacts with stable identity, media type, size, and content hash. | Data loss / lock-in | E1.2, E1.3 |
| FL-URJ-R02 | Every write creates an immutable version with parent, actor, reason, timestamp, source context, and provenance history. | Audit failure / lost work | E1.2, E1.4 |
| FL-URJ-R03 | Metadata, blobs, versions, graph changes, chunks, and indexes do not become partially visible after a failed mutation. | Corruption | E1.2, E1.4, E7.3 |
| FL-URJ-R04 | Search and literal grep, full-document reads, bounded chunk reads, graph links, bounded traversal, and version history have stable provider-neutral semantics. | Unusable contract | E1.3, E3.1, E5.1 |
| FL-URJ-R05 | Graph edges are typed, tenant-scoped, bounded, and history-safe; conflicting metadata cannot silently overwrite prior state. | Knowledge corruption / disclosure | E1.3, E1.4 |
| FL-URJ-R06 | MCP is the public contract with typed schemas, bounded inputs/outputs, stable errors, discoverable capabilities, and supported stdio plus Streamable HTTP transports. | Client incompatibility | E3.1, E3.2, E3.3 |
| FL-URJ-R07 | Claude Code, Codex, Cursor, Hyperset, and future clients use the same domain identifiers and semantics; no provider-specific privileged path exists. | Provider lock-in | E3.3, E5.1 |
| FL-URJ-R08 | Web artifacts render from a separate origin with opaque sandboxing, restrictive CSP, no network egress, no host DOM/state access, and no XSS-capable path. | Credential/data exfiltration | E2.1, E2.2, E8.2 |
| FL-URJ-R09 | Sandboxed artifacts can call only attached, allowlisted MCP capabilities through a narrow validated bridge; calls are bounded, auditable, and credential-free. | Confused deputy / escalation | E2.3, E8.2 |
| FL-URJ-R10 | Tenant and actor context is explicit, fail-closed, private by default, and not caller-selectable; cross-tenant reads, search, and traversal reveal nothing. | Cross-tenant disclosure | E1.1, E1.4, E4.1, E4.2 |
| FL-URJ-R11 | Hosted mode has verified authentication, principal-to-tenant/actor binding, authorization, revocation, and an explicit deployment posture. | False trust boundary | E4.1, E4.2 |
| FL-URJ-R12 | Docker is the reproducible local path with durable volumes, repeatable migrations, health/readiness, safe configuration, and production deployment seams. | Non-repeatable deployment | E7.1, E7.3 |
| FL-URJ-R13 | Runtime hardening, structured observability, audit records/export, rate limits, incident signals, and operational runbooks exist for hosted operation. | Undetectable failure / abuse | E7.1, E7.2 |
| FL-URJ-R14 | Backup/restore, retention, deletion/legal erasure, key management, TLS, and disaster-recovery objectives are defined and exercised. | Irrecoverable data / compliance gap | E7.3, E8.1 |
| FL-URJ-R15 | The thin UI supports the core artifact loop through public MCP: create/upload, read/chunk, search/grep, graph navigation, history, optimistic write, preview, and recoverable failures. | Unverifiable human path | E6.1, E6.2, E6.3 |
| FL-URJ-R16 | Hyperset proves first-consumer interoperability as an external black-box client using public MCP/HTTP, isolated storage, and restart persistence. | Integration coupling | E5.1, E5.2, E5.3 |
| FL-URJ-R17 | Repository quality and supply-chain controls are reproducible: pinned dependencies, lint/format/type/test/coverage checks, CI, dependency updates, security reporting, and release records. | Unreviewable change risk | E8.1, E8.3 |
| FL-URJ-R18 | Readiness claims use repeated fresh-checkout/fresh-volume evidence, adversarial deny tests, independent review, explicit residual risks, and the all-gates rule. | Misleading assurance | E8.2, E8.3 |
| FL-URJ-R19 | Folio remains generic and bounded: no Hyperset-specific core behavior, private coupling, broad artifact network, premature infrastructure, or unapproved scope expansion. | Product drift / security debt | E0, every phase, ADR review |

## Owners and decision rights

Owners are roles so work survives crew rotation. A named assignee may be added
to the corresponding bead without changing this plan.

| Role | Owns | Decision right |
| --- | --- | --- |
| Mayor / product owner | Scope, sequencing, ADR approval, final readiness claim, and coordination across crews. | Only role that may approve an enterprise-ready claim. |
| Codex crew | Core artifact, version, graph, search, MCP adapter, and minimal deployment implementation. | May propose contract changes; may not silently change product boundaries. |
| Security crew | Threat model, tenancy/authentication, sandbox/CSP/bridge controls, security review, and residual-risk register. | May block a phase or claim on a security failure. |
| QA crew | Public MCP conformance, Hyperset black-box fixture, Docker/e2e repeatability, coverage, and release evidence. | Owns pass/fail reproduction records. |
| UX crew | Thin UI workflows, browser acceptance, accessibility, and human usability evidence. | May block UI readiness on task failure or misleading state. |
| Refinery / release owner | Rebase, merge integrity, artifact provenance, and release packaging. | Verifies committed evidence matches shipped source. |

## Dependency graph

Phase completion is a hard dependency, not a suggested order:

```text
E0 planning
  |
  v
E1 durable contract and tenant boundary
  |\
  | \
  v  v
E3 public MCP contract  E2 sandbox and capability boundary
  |\         /
  |        /
  v       v
E4 hosted identity and privacy
  |
  v
E5 Hyperset black-box consumer
  |
  +------------------+
  |                  |
  v                  v
E6 public-contract UI E7 Docker, operations, and data lifecycle
  \                  /
   \                /
    v              v
      E8 assurance and release gate
```

More precisely:

- E1 depends on the current product contract and ADR set. E1 must establish
  the domain and tenant seams before a consumer or UI can be trusted.
- E2 depends on E1's artifact-read semantics. Its end-to-end proof also uses
  E3's public MCP path; unit-only renderer work does not close the phase.
- E3 depends on E1 and must preserve the nine composable operations rather than
  introducing a second vocabulary.
- E4 depends on E1's explicit context seam and E3's request boundary. Hosted
  mode cannot be enabled as a configuration shortcut.
- E5 depends on E3 and E4. Hyperset may use a configured local principal while
  hosted identity is being built, but it cannot bypass identity or tenancy.
- E6 depends on E2 and E3; its public-contract claim also requires E5's
  consumer shape to remain valid.
- E7 depends on E1's durable storage, E3's service boundary, and E4's deployment
  identity posture.
- E8 depends on E1 through E7. No earlier pass can waive a later gate.

## Risk-ordered phases and vertical slices

### E0 — Planning, traceability, and claim discipline

Status: this phase is the current documentation-only task.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E0.1 | Inventory current contract, boundaries, gaps, and deferred work. | Existing manifesto, product brief, V0 plan, ADRs. | Mayor + Codex | Plan names the public MCP/UI/Hyperset paths and does not claim completion. | This plan; repository inspection record. |
| E0.2 | Convert requirements into IDs, phases, dependencies, owners, gates, and evidence targets. | E0.1 | Mayor | Every requirement has a phase and a pass condition. | Traceability map and dependency graph in this document. |
| E0.3 | Establish ADR routing and all-gates release rule. | E0.1 | Mayor + Refinery | Future boundary changes point to an ADR before implementation. | [`docs/adrs/README.md`](adrs/README.md). |

Exit: both Phase 0 documents exist, links resolve, no source or runtime files
changed, and the commit is visibly documentation-only.

### E1 — Durable artifact, graph, and tenant contract

Risk focus: persistence corruption, lost history, and cross-tenant disclosure.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E1.1 | Explicit request context carries configured tenant and actor; public inputs cannot override them. | E0 | Codex + Security | Public MCP calls use configured context; omitted or cross-tenant context fails closed. | Contract tests; tenant threat-model notes. |
| E1.2 | Create and write atomically persist artifact/version/blob/provenance with parent checks and immutable pointers. | E1.1 | Codex | MCP create/write returns stable IDs; stale parent conflicts without changing current content or history. | Migration/schema diff; transaction tests; version fixtures. |
| E1.3 | Read, chunk, search, grep, link, traverse, and history share stable identifiers and bounded semantics. | E1.2 | Codex | One client can create, retrieve exact bytes, read a bounded chunk, find text, link, traverse, and list history. | Public contract examples; service tests. |
| E1.4 | Failure safety and namespace isolation cover malformed payloads, invalid parents, missing blobs, interrupted writes, and two tenants. | E1.1–E1.3 | QA + Security | Failed public calls leave no partial visible state; tenant B cannot read, search, grep, or traverse tenant A. | Before/after assertions; two-process isolation run. |

Exit: E1 passes public MCP acceptance and its failures are observable without
opening the database or blob volume.

### E2 — Dedicated-origin renderer and narrow artifact bridge

Risk focus: untrusted code escaping into the control plane or using Folio as a
network/credential proxy.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E2.1 | Renderer serves HTML/CSS/JavaScript from a separate origin with opaque iframe sandboxing and no storage mount. | E1.3; E3 public read path for final proof | Security + Codex | Browser loads supported artifacts while control DOM, cookies, storage, host origin, and top navigation remain unavailable. | Renderer policy; response/header assertions. |
| E2.2 | Hostile browser fixtures cover egress, CSP weakening, DOM escape, popups, forms, downloads, frames, workers, and navigation. | E2.1 | Security + QA | Real browser observes deny behavior and unchanged control-page URL/state. | `docs/SECURITY-TEST-MATRIX.md`; browser run artifacts. |
| E2.3 | One schema-defined bridge permits only named attached read/search/traverse capabilities with exact source/origin checks, bounds, timeouts, and audit decisions. | E2.1–E2.2; E3 | Security + Codex | Allowed attached call succeeds; unknown attachment, write, forged frame, wrong origin, oversized, slow, or credential-bearing call is rejected and logged. | Bridge schema/tests; redacted audit samples. |

Exit: normal rendering and mediated read-only use work; every adversarial
control has an executed deny result. Browser isolation is not treated as proof
against browser-engine vulnerabilities.

### E3 — Public MCP contract and provider neutrality

Risk focus: clients silently depending on private or provider-specific behavior.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E3.1 | Official MCP SDK defines typed tool schemas/results around one shared service implementation. | E1 | Codex + QA | Official SDK client lists tools and receives structured results/errors without private imports. | Pinned dependency; schema snapshot; protocol tests. |
| E3.2 | Stdio and Streamable HTTP expose the same nine operations with bounded inputs/outputs, capability discovery, and stable error categories. | E3.1 | Codex | The same scenario succeeds over both transports; malformed, oversized, unknown, and unauthorized requests fail safely. | Transport tests; public contract reference. |
| E3.3 | At least two provider-neutral client stacks exercise the same identifiers and semantics. | E3.2 | QA | Client behavior does not branch on Claude Code, Codex, Cursor, or Hyperset brand names. | Conformance matrix; client logs. |

Exit: public MCP behavior is documented and tested as the product contract.

### E4 — Hosted identity, tenancy, and privacy

Risk focus: a development principal being mistaken for enterprise
authorization.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E4.1 | Deployment mode is explicit; local mode is visibly unauthenticated; hosted mode refuses startup without an authentication adapter. | E1.1; E3.2 | Security + Codex | Health/UI and subprocess/container checks show local posture; hosted startup fails before listening when identity is absent. | Startup tests; health payload; configuration runbook. |
| E4.2 | Verified principal resolves to tenant and actor before MCP operation routing; authorization and revocation are enforced at the service boundary. | E4.1 | Security | Public MCP cannot select tenant/actor through arguments, headers, URLs, or bridge messages; revoked identity loses access. | Auth adapter contract; two-tenant e2e; revocation log. |
| E4.3 | Private-by-default policy covers artifacts, versions, graph edges, indexes, provenance, attachments, audit, caches, and exports. | E4.2 | Security + Mayor | Cross-tenant reads/search/traversal and error/timing shortcuts reveal no data. | Privacy matrix; policy tests; ADR for grants/sharing if scope expands. |

Exit: a hosted trust boundary is real and tested. Selective/public sharing is
not silently added; it requires a superseding ADR covering versions, graph
reachability, derived data, attachment access, expiration, revocation, and
auditability.

### E5 — Hyperset first-consumer proof

Risk focus: integration that passes only because Folio and Hyperset share
implementation details.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E5.1 | Hyperset-shaped external fixture uses only public MCP/HTTP and the supported client SDK. | E3; E4 local or hosted identity | QA + Hyperset owner | Fixture creates artifacts, writes a new version, links, searches/greps, traverses, reads full/chunk content, and lists history. | `tests/hyperset_consumer.py`; isolated fixture logs. |
| E5.2 | Consumer flow proves exact persistence across service restart and clean volume lifecycle. | E5.1; E7.1 | QA | Same version ID, hash, provenance, and bytes return after restart; no storage path is opened by the consumer. | Docker e2e record; image/volume metadata. |
| E5.3 | Consumer contract remains reusable by future clients and exposes no Hyperset-specific Folio operation. | E5.1–E5.2 | QA + Mayor | Removing Hyperset brand-specific code leaves the same public operation set and semantics. | Contract review; compatibility notes. |

Exit: Hyperset is a black-box consumer and a failed test identifies a public
contract gap rather than justifying a private shortcut.

### E6 — Public-contract inspection UI

Risk focus: human claims being supported by private service/database paths or
misleading security and failure states.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E6.1 | Static UI gateway uses the public MCP endpoint with bounded, allowlisted operations; no second REST vocabulary or private service import. | E2, E3, E5 | UX + Codex | Browser network trace shows UI artifact state crossing the public MCP path. | UI route contract; browser network evidence. |
| E6.2 | One compact page supports create/upload, read/full and chunk, search/grep, graph link/navigation, history, optimistic write, and HTML/CSS/JS preview. | E6.1 | UX | A person completes the core loop without seeing base64, internal IDs as required input, database details, or transport internals. | `docs/UX-TEST-PLAN.md`; real-browser flow. |
| E6.3 | Loading, success, empty, conflict, error, unsupported-file, local-mode, and denied-bridge states are understandable and keyboard/screen-reader operable. | E6.2 | UX + QA | Native keyboard path, focus, labels, live status, alerts, and accessible preview title pass; stale edits are preserved. | Accessibility tree/session record; browser tests. |

Exit: UI behavior is evidence of the same MCP contract, not a privileged
demonstration path.

### E7 — Docker, operations, and data lifecycle

Risk focus: an application that is locally functional but cannot be operated,
recovered, or safely exposed.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E7.1 | Fresh Compose startup has repeatable migrations, named durable volume, non-root runtime, read-only root filesystem where compatible, dropped capabilities, no-new-privileges, health/readiness, and explicit config validation. | E1, E3, E4.1 | Codex + Refinery | Fresh Docker flow starts, serves public MCP/UI/renderer, rejects unsafe hosted config, and survives restart. | `Dockerfile`; Compose config; `make docker-build` and e2e logs. |
| E7.2 | Structured logs, metrics, traces, audit decisions, bounded errors, rate limits, alerts, incident response, and security-reporting paths are defined without logging content or secrets. | E4.2; E7.1 | Security + Refinery | Operators can identify request, tenant, actor, capability, latency, and decision without exposing bytes, credentials, or raw arguments. | Observability schema; dashboards/alerts; runbooks; `SECURITY.md`. |
| E7.3 | Backup/restore, disaster recovery, retention, deletion/legal erasure, key management, TLS, upgrade, and rollback procedures are exercised against durable state. | E1.2; E4.3; E7.1 | Refinery + Security | Restore reproduces authorized artifact/version/graph state; erasure removes all in-scope derived/exported copies per policy. | Restore transcript; RPO/RTO record; lifecycle policy; key/TLS profile. |

Exit: operations are reproducible and recovery evidence covers the same public
state users and agents depend on.

### E8 — Assurance, evidence, and release decision

Risk focus: declaring readiness from partial or non-reproducible evidence.

| Slice | Small vertical result | Dependencies | Owner | Public acceptance | Evidence artifact |
| --- | --- | --- | --- | --- | --- |
| E8.1 | CI and local checks enforce dependency lock, format/lint, typing, focused tests, coverage floor, migration checks, Docker build, and public e2e. | E1–E7 | QA + Refinery | A clean checkout runs the documented commands; failures block release. | `Makefile`; `pyproject.toml`; lockfile; CI artifacts. |
| E8.2 | Full adversarial matrix, real-browser/UI/accessibility review, tenant isolation, Hyperset, restart, fresh-volume, and hosted-startup tests pass. | E2, E4, E5, E6, E7 | Security + QA + UX | MCP, UI, and Hyperset paths each pass allow and deny scenarios through public interfaces. | `docs/SECURITY-TEST-MATRIX.md`; `docs/UX-TEST-PLAN.md`; browser/Docker logs. |
| E8.3 | Release evidence records exact commit, environment, commands, outputs, image digest, test counts, coverage, reviewer decisions, residual risks, and expiry/retest dates. | E8.1–E8.2 | Mayor + Refinery | All gates are green twice where required; any unknown remains a blocker. | `docs/RELEASE-EVIDENCE.md`; signed review/approval record. |

Exit: Mayor may make an enterprise-ready claim only after E8.3 and every prior
gate pass. This plan itself never marks a gate passed.

## Public acceptance contract

These three paths are mandatory evidence sources. Private service calls,
database fixtures, and direct renderer storage access may support unit tests but
cannot close the corresponding gate.

| Path | Minimum passing behavior | Required deny/recovery behavior |
| --- | --- | --- |
| Public MCP | A real client creates text, JSON, binary, HTML, CSS, and JavaScript artifacts; reads exact full content and bounded chunks; searches/greps; links/traverses; writes a new version; and lists history. | Invalid IDs, malformed/large input, stale parent, unknown operation, cross-tenant access, and capability escalation fail with stable bounded errors and no partial mutation. |
| Public UI | A person uploads/creates, opens, searches/greps, reads a chunk, links/navigates, edits with an optimistic parent, sees history, previews web artifacts, and recovers from empty/error/conflict/service-unavailable states. | UI never opens storage directly; loading ends; unsaved content/query survives recoverable errors; local mode is visibly unauthenticated; denied bridge and hostile preview behavior are understandable. |
| Hyperset | An isolated external process uses the public MCP/HTTP endpoint only to create, write, link, search/grep, traverse, read full/chunk content, and verify restart persistence. | No Folio import, SQLite access, blob-volume mount, shared database, private endpoint, Hyperset-specific tool, or special auth bypass. |

## Gate checklist

The release record must show a pass for every row. “Not applicable” is not a
pass unless Mayor records an approved scope decision in an ADR and the claim is
narrowed accordingly.

| Gate | Pass condition | Evidence |
| --- | --- | --- |
| G0 Scope and ADR | Boundary, deferred work, owner, and change decisions are explicit; no unapproved expansion. | ADR index; reviewed plan; final diff. |
| G1 Data correctness | Immutable versions, provenance, graph history, bounded reads/search, transaction safety, and failure atomicity pass through public MCP. | Public MCP/e2e tests; before/after state assertions. |
| G2 Tenant privacy | Configured/verified context controls all lookups; cross-tenant reads/search/traversal/errors do not disclose data; private defaults hold. | Two-tenant process tests; policy matrix. |
| G3 MCP compatibility | Official SDK, typed schemas/results, stdio and Streamable HTTP, stable errors/limits, capability discovery, and provider-neutral conformance pass. | Schema snapshots; two-client logs; CI. |
| G4 Sandbox | Dedicated origin, opaque sandbox, CSP, no direct egress, no host state/XSS path, and real-browser hostile deny tests pass. | Security matrix; Chrome/browser artifacts; headers. |
| G5 Bridge | Exact frame source/origin and server origin checks, fixed allowlist, schema/size/time limits, no credentials, allow/deny audit all pass. | Bridge tests; redacted audit records. |
| G6 Hyperset | External black-box consumer completes core operations and exact restart persistence through public MCP/HTTP only. | Fixture source; isolated Docker run; logs. |
| G7 UI and accessibility | Core UI loop works over public MCP; browser failure states, keyboard operation, assistive technology, and security disclosure pass. | UX plan/session records; browser e2e. |
| G8 Docker and operations | Fresh build/start/migrate/restart, runtime hardening, readiness, observability, rate limiting, backup/restore, lifecycle, TLS, and recovery evidence pass. | Build/e2e output; runbooks; restore transcript. |
| G9 Assurance and release | Quality/supply-chain checks, independent security/UX review, repeated fresh-state evidence, residual-risk sign-off, and exact release record pass. | CI artifacts; review record; release evidence. |

## Evidence ledger

Each future implementation bead must name the rows it closes and attach the
result to one or more of these durable artifacts:

| Evidence | What it proves |
| --- | --- |
| `docs/V0-PLAN.md` | Bounded local artifact/graph/MCP milestone and its non-goals. |
| `docs/adr/` and [`docs/adrs/README.md`](adrs/README.md) | Canonical decisions, planned decision points, and scope changes. |
| `docs/SECURITY-TEST-MATRIX.md` | Executed hostile browser, bridge, tenant, auth, and leakage checks. |
| `docs/UX-TEST-PLAN.md` | Human/browser core-loop, accessibility, and failure-state acceptance. |
| `tests/hyperset_consumer.py` and public e2e tests | Black-box consumer and public MCP behavior. |
| `Dockerfile`, `docker-compose.yml`, `Makefile`, CI, lockfile | Reproducible build, runtime, and repository gates. |
| `SECURITY.md`, runbooks, lifecycle/restore records | Reporting, operations, recovery, deletion, and residual-risk handling. |
| `docs/RELEASE-EVIDENCE.md` | Exact commands, outputs, environment, image identity, reviewer decision, and blockers. |

Evidence must be generated from the source commit under review. Copying an old
green result onto a new commit is not evidence for the new commit.

## Work rules

1. Start each phase with a small implementation issue that names one vertical
   slice, its dependencies, public acceptance, and evidence artifact.
2. Keep the nine MCP operations composable. Add no Hyperset-specific operation,
   REST mirror, client package, graph database, vector service, or credential
   broker merely to make one test easier.
3. Record changes to security, persistence, provider neutrality, deployment,
   privacy, identity, sharing, retention, or public contract in a new or
   superseding ADR before implementation.
4. Security and UX acceptance require deny/failure paths as well as successful
   paths. Logs and evidence omit artifact content, secrets, credentials, and
   raw untrusted arguments.
5. Run focused checks during each slice. Before any readiness discussion, run
   the full gate suite twice from a clean checkout and fresh isolated Docker
   state, then record exact output.
6. If any gate fails, remove enterprise-ready language from the release claim,
   preserve the failure as a blocker, and fix or explicitly narrow the claim via
   ADR. Never waive a gate by renaming it.
