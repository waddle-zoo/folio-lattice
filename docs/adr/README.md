# Architecture decision records

ADRs are the canonical record of consequential Folio Lattice product and
architecture decisions. The [manifesto](../../MANIFESTO.md) is the product
contract; these records define how the implementation will honor it. The
[product brief](../PRODUCT-BRIEF.md) is retained as source context.

## Decisions

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-product-boundary-and-first-milestone.md) | Accepted | Product boundary and first milestone |
| [0002](0002-mcp-first-knowledge-interface.md) | Accepted | MCP-first graph and artifact interface |
| [0003](0003-artifacts-versions-and-provenance.md) | Accepted | Arbitrary-file artifacts, immutable versions, and provenance |
| [0004](0004-sandboxed-web-artifacts.md) | Accepted | Sandboxed web artifacts and mediated MCP access |
| [0005](0005-docker-first-deployment.md) | Accepted | Docker-first development and enterprise deployment seams |
| [0006](0006-provider-neutral-clients.md) | Accepted | Provider-neutral client contracts |
| [0007](0007-private-tenancy-and-sharing.md) | Superseded by 0012 | Private tenancy now, sharing in v2 |
| [0008](0008-hyperset-first-consumer.md) | Accepted | Hyperset is the first consumer through public contracts |
| [0009](0009-thin-inspection-ui-and-isolated-renderer.md) | Accepted | Thin inspection UI and isolated renderer |
| [0010](0010-public-contract-ui-bridge-and-hosted-gate.md) | Accepted | Public-contract UI, narrow artifact bridge, and hosted gate |
| [0011](0011-hosted-identity-and-principal-mapping.md) | Accepted; gated | Hosted identity and verified principal-to-tenant/actor mapping |
| [0012](0012-private-tenancy-acl-and-revocation.md) | Accepted; gated | Private tenancy, ACL grants, admin, sharing, and revocation |
| [0013](0013-external-mcp-registry-and-credential-brokerage.md) | Accepted; gated | Admin-managed external MCP registry and credential brokerage |
| [0014](0014-egress-slack-and-tls-boundary.md) | Accepted; gated | Egress, Slack outbound, and TLS proxy boundary |
| [0015](0015-abuse-controls-and-audit-lifecycle.md) | Accepted; gated | Rate, abuse, payload, time, audit minimization, retention, and export |
| [0016](0016-backup-migration-and-disaster-recovery.md) | Accepted; gated | Backup, migration, restore, and disaster recovery |
| [0017](0017-observability-supply-chain-and-assurance.md) | Accepted; gated | Observability, SBOM, provenance, adversarial tests, and pentest |

## Conventions

Each ADR has a stable number and records its status, context, decision,
consequences, non-goals, and open questions. A decision that changes should be
superseded by a new ADR rather than silently rewritten. Clarifications that do
not change the decision may be committed to the existing record.

Create or supersede an ADR before changing a boundary involving security,
persistence, provider neutrality, deployment, or privacy.

The hosted security baseline is summarized in the [threat model](../threat-model.md)
and [security research](../research/2026-09-07-security-baseline.md).
