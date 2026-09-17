# ADR 0016: Backup, migration, and disaster recovery

- Status: Accepted; implementation gated
- Date: 2026-09-07

## Context

Folio Lattice's security model depends on history, provenance, ACLs, graph
edges, indexes, audit, and connector policy surviving upgrades and failures
together. A database-only backup can restore data while losing authorization or
provenance. A migration that cannot be verified or recovered can silently
cross tenant boundaries.

## Decision

Treat metadata database, artifact/version blobs, graph/index rebuild inputs,
ACL/grant history, provenance, audit records, configuration, and connector
policy as a consistency set. Secret values remain in the external secret
manager; backups store only references and rotation/recovery metadata according
to that manager's policy.

Hosted deployments maintain encrypted, access-controlled, immutable backups in
at least two failure domains, with keys and backup-admin identities separated
from the production runtime. Backup manifests include schema/data version,
component digests, time range, tenant scope, encryption key ID, and integrity
checks. Backup access and restore are audited. Development Compose volumes are
not evidence of hosted backup durability.

Use forward-only expand/contract migrations with an explicit schema version,
preflight checks, checksums, bounded batches, resumability, and post-migration
invariant checks. Do not run destructive changes until a verified backup and
restore point exists. Rollback means restoring a known-good snapshot or running
the compatibility path; down-migrations are not required.

The baseline hosted recovery target is RPO <= 24 hours and RTO <= 8 hours until
a deployment-specific business impact analysis sets tighter targets. Restore
must rebuild or verify indexes, revalidate tenant ownership and ACLs, rotate
runtime/connector credentials as needed, and keep the recovered service
private until smoke and negative isolation tests pass. Restore exercises are
quarterly and after material storage/migration changes.

## Invariants

- No migration drops or rewrites version/provenance/ACL/audit data without an
  reviewed, restorable copy and an explicit retention decision.
- Restored data cannot become public or cross-tenant because of missing policy,
  stale indexes, default configuration, or regenerated identifiers.
- Backup and restore credentials cannot read live tenant content except through
  the audited recovery workflow and are not available to application workers.
- Migrations are deterministic, forward-only, resumable, checksum-verified,
  and safe to retry after interruption.
- Recovery preserves tenant ownership, actor/grant history, immutable versions,
  provenance, audit integrity, connector disablement, and deletion/hold policy.
- RPO/RTO, backup retention, restore test results, and unresolved failures are
  visible to the deployment owner.

## Consequences

- Immutable, separated backups increase storage and key-management cost.
- Forward-only migrations require compatibility code and restore-based rollback
  planning.
- Hosted readiness includes recurring recovery exercises, not only deployment
  automation.

## Non-goals

- Active-active multi-region service, zero data loss, or a provider selection.
- Treating backup replication as a substitute for incident response.
- Recovering secrets outside the secret manager's approved lifecycle.

## Rejected alternatives

- Copying only the primary database: loses blobs, indexes, policy, or audit
  context and can restore an unauthorized view.
- Mutable backup bucket with production credentials: vulnerable to deletion,
  ransomware, and privilege reuse.
- In-place destructive migrations with no restore test: failure is irreversible
  and can corrupt authorization state.
- Treating reindexing as a substitute for ACL verification: indexes can leak
  while being rebuilt or stale.
- “Multi-region” as the only DR evidence: replication is not a tested restore
  or recovery procedure.

## Migration

1. Inventory durable stores and add a manifest that identifies their consistency
   relationship and restore order.
2. Add schema versioning and expand/contract tooling; run migrations in staging
   against a production-shaped, tenant-isolated backup copy.
3. Introduce encrypted immutable backups and restore into an isolated recovery
   environment; run checksum, ACL, tenant, history, and audit tests.
4. Define deployment RPO/RTO, retention, failure domains, key custody, and
   recovery ownership; publish them with the runbook.
5. Gate hosted production writes on successful scheduled backup and last-known
   restore evidence.

## Evidence gates

- Backup job test proves all consistency-set components are present, encrypted,
  immutable, integrity-checked, and inaccessible to the application runtime.
- Restore test meets the declared RPO/RTO and passes cross-tenant, ACL,
  historical-version, provenance, audit, export, and connector-disablement
  checks before serving traffic.
- Migration test covers interruption, retry, checksum mismatch, partial batch,
  old-client compatibility, and rollback-by-restore.
- Quarterly exercise records operator, duration, recovered versions, missing
  data, key rotation, and remediation owner.
- Independent review checks backup retention/deletion, recovery permissions,
  secret references, and tenant isolation.

## Open blockers

- A provider-neutral source seam now defines versioned external key custody,
  immutable-store existence/integrity/WORM checks, and scheduler/RPO readiness
  metrics. It is deliberately not hosted evidence: no vendor adapter,
  deployment binding, or external restore witness is present.
- Hosted storage/backup provider, failure domains, key manager, and retention
  schedule are not selected.
- Business owners have not approved RPO/RTO targets beyond the baseline.
- Durable store inventory and restore order are incomplete.
- Recovery access, incident authority, and customer communication runbook are
  not assigned.

## References

- [NIST SP 800-34 Rev. 1](https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final)
- [NIST contingency planning](https://csrc.nist.gov/Topics/Security-and-Privacy/security-programs-and-operations/contingency-planning)
- [NIST SP 800-61 Rev. 3](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
