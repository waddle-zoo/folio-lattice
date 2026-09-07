# ADR routing and enterprise-readiness index

This directory is the planning-facing ADR index requested by the
enterprise-readiness plan. The canonical decision records remain in
[`docs/adr/`](../adr/) and its [canonical index](../adr/README.md). This file
does not duplicate or replace those records.

## Rule

Do not change Folio Lattice's security, persistence, provider-neutrality,
deployment, privacy, identity, sharing, retention, or public-contract boundary
silently in an implementation issue. Create or supersede an ADR first, link it
from the canonical index, and update
[`docs/ENTERPRISE-READINESS-PLAN.md`](../ENTERPRISE-READINESS-PLAN.md) if the
dependency, owner, acceptance, or gate changes.

An ADR records a decision; this index records where that decision controls the
readiness plan. Proposed topics below are not product decisions until their ADR
is accepted.

## Accepted decisions

| ADR | Decision | Readiness coverage |
| --- | --- | --- |
| [0001](../adr/0001-product-boundary-and-first-milestone.md) | Product boundary and first milestone | E0, FL-URJ-R19 |
| [0002](../adr/0002-mcp-first-knowledge-interface.md) | MCP-first graph and artifact interface | E1, E3, FL-URJ-R04, FL-URJ-R06 |
| [0003](../adr/0003-artifacts-versions-and-provenance.md) | Arbitrary files, immutable versions, and provenance | E1, E7, FL-URJ-R01–R03 |
| [0004](../adr/0004-sandboxed-web-artifacts.md) | Sandboxed web artifacts and mediated MCP access | E2, FL-URJ-R08–R09 |
| [0005](../adr/0005-docker-first-deployment.md) | Docker-first development and enterprise deployment seams | E7, FL-URJ-R12–R14 |
| [0006](../adr/0006-provider-neutral-clients.md) | Provider-neutral client contracts | E3, E5, FL-URJ-R06–R07 |
| [0007](../adr/0007-private-tenancy-and-sharing.md) | Private tenancy now, sharing as a later capability | E1, E4, FL-URJ-R10–R11, R19 |
| [0008](../adr/0008-hyperset-first-consumer.md) | Hyperset uses public contracts only | E5, FL-URJ-R16, R19 |
| [0009](../adr/0009-thin-inspection-ui-and-isolated-renderer.md) | Thin inspection UI and isolated renderer | E2, E6, FL-URJ-R08, R15 |
| [0010](../adr/0010-public-contract-ui-bridge-and-hosted-gate.md) | Public-contract UI, narrow bridge, and hosted startup gate | E2, E4, E6, FL-URJ-R09–R15 |

ADRs 0008–0010 are listed here because the readiness plan must remain stable as
the public-consumer, UI, and hosted-gate work lands. If a checkout predates one
of them, the link becomes valid when that accepted record is merged; no local
copy belongs under `docs/adrs/`.

## Required future decision points

| Decision point | Before phase | Owner | Minimum decision content |
| --- | --- | --- | --- |
| Hosted identity and authentication adapter | E4.2 | Security + Mayor | Principal verification, tenant/actor binding, token/session handling, revocation, failure behavior, and deployment modes. |
| Authorization, grants, and sharing | Any sharing scope | Security + Mayor | Private defaults; artifact/version/graph/index/preview/attachment reachability; expiration, revocation, audit, and cache behavior. Sharing remains deferred without this ADR. |
| Retention, deletion, legal erasure, and backup/restore | E7.3 | Security + Refinery | In-scope copies, cryptographic key handling, RPO/RTO, purge evidence, and recovery authorization. |
| Public MCP compatibility and deprecation | E3.1 or contract change | Codex + QA + Mayor | Supported protocol/SDK versions, schema evolution, error compatibility, capability negotiation, and removal policy. |
| Hosted operations and security exceptions | E7.2 or exception | Security + Refinery | TLS, rate limits, audit export, observability, incident response, exception expiry, and residual risk. |
| Product-boundary expansion | Any deferred feature | Mayor | Why the feature serves generic Folio users, threat/persistence/privacy impact, dependencies, and replacement of affected non-goals. |

## ADR completion checklist

Every new or superseding record should contain:

- status, date, owner, and superseded record when applicable;
- context and concrete problem;
- decision and public contract impact;
- security, persistence, provider-neutrality, deployment, and privacy
  consequences;
- non-goals and deferred alternatives;
- migration/rollback and evidence requirements; and
- open questions, residual risks, and the readiness gates it can block.

Phase 0 creates no new product decision. It only makes existing decisions
traceable and prevents future implementation work from hiding a boundary change.
