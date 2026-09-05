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
| [0007](0007-private-tenancy-and-sharing.md) | Accepted | Private tenancy now, sharing in v2 |
| [0008](0008-hyperset-first-consumer.md) | Accepted | Hyperset is the first consumer through public contracts |

## Conventions

Each ADR has a stable number and records its status, context, decision,
consequences, non-goals, and open questions. A decision that changes should be
superseded by a new ADR rather than silently rewritten. Clarifications that do
not change the decision may be committed to the existing record.

Create or supersede an ADR before changing a boundary involving security,
persistence, provider neutrality, deployment, or privacy.
