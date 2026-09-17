# Backup, migration, and restore rehearsal

This repository has a bounded local recovery unit for the SQLite metadata
database and its content-addressed blobs. The local `folio-backup-v2` unit
encrypts the payload with AES-256-GCM and authenticates the manifest with a
configured manifest-authentication key. A `BackupKeyProvider` must resolve
distinct backup-wrapping and recovery key references; key bytes never enter
the manifest, logs, or evidence. The in-memory provider is test/local wiring
only. Hosted operations still need live external adapters and an independent
restore witness.

The provider-neutral hosted seam is `folio_lattice.backup_ops`:

- `DurableBackupKeyCustody.readiness()` returns only redacted provider posture,
  active key versions, and the set of versions still accepted during a
  rotation. It does not return key bytes. `BackupOperationsMonitor` rejects a
  local/in-memory adapter, unavailable custody, an invalid rotation state, or a
  backup whose recorded key version is no longer accepted.
- `ImmutableBackupStore.readiness()`, `.latest()`, and `.inspect(backup_id)`
  require the external adapter to prove existence, checksum verification,
  immutability, overwrite protection, and delete protection. The contract has
  no delete or overwrite operation; those controls belong to the provider's
  WORM/retention policy. A local directory or in-memory object is not hosted
  evidence.
- `BackupOperationsConfig` requires explicit schedule interval, RPO, maximum
  backup age, and adapter IDs. `BackupOperationsMonitor.status()` evaluates
  the latest observed backup and returns secret-free readiness, alert names,
  age/RPO status, and bounded metrics. When a hosted HTTP deployment injects
  the monitor, its `/readyz` dependency is required and fails closed until the
  real adapters are ready; `/metrics` exports the monitor's backup metrics.
  The legacy hosted process does not inject an adapter and therefore cannot
  claim hosted backup readiness or durability evidence.

The exact deployment configuration names are
`FOLIO_BACKUP_INTERVAL_SECONDS`, `FOLIO_BACKUP_RPO_SECONDS`,
`FOLIO_BACKUP_MAX_AGE_SECONDS`, `FOLIO_BACKUP_KEY_ADAPTER`,
`FOLIO_BACKUP_STORE_ADAPTER`, and optional expected active versions
`FOLIO_BACKUP_KEY_VERSION`/`FOLIO_RECOVERY_KEY_VERSION`. No vendor or cloud
SDK is selected here. `backup status` validates this configuration but uses
explicit unavailable adapters until deployment wiring supplies the real
custody/store objects; it therefore reports `not_ready` rather than claiming a
local pass.

## Commands

Run these from the exact image/source revision. The database and blob paths
default to `FOLIO_DB_PATH` and `FOLIO_BLOB_ROOT`:

```text
python -m folio_lattice.ops migrate check
python -m folio_lattice.ops backup create --output /evidence/backup \
  --backup-key-ref "$BACKUP_KEY_REF" \
  --recovery-key-ref "$RECOVERY_KEY_REF"
python -m folio_lattice.ops backup verify --input /evidence/backup \
  --key-id "$RECOVERY_KEY_REF"
python -m folio_lattice.ops backup verify --input /evidence/backup \
  --key-id "$RECOVERY_KEY_REF" --tenant-id "$EXPECTED_TENANT_ID"
python -m folio_lattice.ops dr restore \
  --input /evidence/backup \
  --db /evidence/recovered/folio.db \
  --blobs /evidence/recovered/blobs \
  --key-id "$RECOVERY_KEY_REF" --tenant-id "$EXPECTED_TENANT_ID"
```

The Python operations API requires the configured `BackupKeyProvider`; the
CLI remains fail-closed until a deployment wires that provider rather than
accepting key bytes or pretending a key reference is custody. Restore also
accepts an expected backup ID for replay control and refuses existing database
or blob targets. Restore into a new isolated location, keep the recovered
service private, and run the negative tenant/ACL matrix before serving
traffic.

## Consistency and failure behavior

The authenticated backup manifest records `folio-backup-v2`, a backup ID,
schema signature, tenant scope, SQLite quick/foreign-key checks, row counts,
SHA-256 for the metadata database and each referenced blob, and the restore
order. The encrypted payload contains only the metadata database and checked
content-addressed blobs; tar members are restricted to exact safe paths.
The key metadata includes manifest, backup, and recovery key versions; the
provider must match those versions on verify, so a key-reference swap or
rotation mismatch fails closed.
Metadata preserves versions and parents, provenance, graph edges, ACL/grant
history, external-connection policy and audit records. FTS/index definitions
and their rebuild inputs are part of the metadata database. Secret values are
not copied; connector credential fields remain references only.

`backup verify` authenticates the manifest before trusting key references,
tenant scope, or payload paths. It fails closed on unsigned/legacy manifests,
tampered key IDs or manifest fields, wrong recovery keys, ciphertext/tag
tampering, truncation, path traversal, extra files, checksum/size mismatch,
SQLite corruption, foreign-key failure, schema/count mismatch, missing
referenced blob, symlink, duplicate path, wrong recovery-key identity, replay
ID mismatch, or out-of-bound blob count/bytes. Restore refuses existing
targets and rolls back incomplete placement, which is the local rollback
guard: restore a verified snapshot into a new isolated target and switch over
only after post-restore ACL/tenant checks.

`migrate check` invokes the existing forward-compatible initializer twice and
checks readiness after each run. It performs no down-migration or destructive
rewrite. A migration that needs a destructive change remains blocked until a
verified backup and restore point exist.

## Evidence record

The focused rehearsal is `tests/test_backup_restore.py`. It creates two
artifacts, a graph edge, two immutable versions, an ACL share, and an audit
event; verifies the consistency set; restores into absent targets; checks
content, history, graph, audit, and readiness; and records backup/restore
timings against the 15-minute/60-minute rehearsal budgets. A second test
corrupts a copied blob and confirms verification, then restore into a
non-empty target, is refused.

The local test is not hosted durability evidence. G8 remains open for an
immutable hosted-store adapter, durable backup/recovery key custody and
rotation/retirement, a live scheduler/age/RPO alarm, and an independent
recovery witness. The source slice has contract/readiness/metric coverage in
`tests/test_backup_ops.py`, including unavailable adapters, missing/stale
backups, integrity metadata failure, rotation-version mismatch, and denied
overwrite/delete posture. `.23`/`.24` stay open until real hosted adapters,
clean restore/runtime rerun, scheduled evidence, and the independent witness
are independently verified.

## Fresh evidence isolation

Every release/demo/evidence run must use `scripts/repeat-fresh-state.sh` (or
an equivalent runner) with a unique Compose project, project-scoped volume,
caller-assigned control/renderer ports, `FOLIO_TENANT_ID`, and `FOLIO_ACTOR`.
Release/Compose mode requires Docker readiness before the gate command and
bind-checks the caller-supplied port base. The runner records those
identifiers, source SHA, database/blob roots, and cleanup status in the
evidence JSON, then removes only resources bearing that run's project label.
It refuses the default `8000`/`8001` ports and shared `dev`/`hyperset-v0`
tenant in release and non-Docker modes. The
`FOLIO_FRESH_STATE_ALLOW_SHARED_DEFAULTS=true` override is accepted only with
an explicit `FOLIO_FRESH_STATE_MODE=non-release`, which is not release
evidence. Never point a release fixture at the default volume or tenant;
intentional quickstart/Hyperset data is not disposable evidence state and must
not be deleted or mutated.
