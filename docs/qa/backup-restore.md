# Backup, migration, and restore rehearsal

This repository now has a bounded local recovery unit for the SQLite metadata
database and its content-addressed blobs. It is intentionally explicit about
what it does not provide: `manifest.json` is checksum-verified but not signed
or encrypted. Hosted operations must place the unit in an encrypted,
immutable, access-controlled store with keys separated from the application
runtime.

## Commands

Run these from the exact image/source revision. The database and blob paths
default to `FOLIO_DB_PATH` and `FOLIO_BLOB_ROOT`:

```text
python -m folio_lattice.ops migrate check
python -m folio_lattice.ops backup create --output /evidence/backup
python -m folio_lattice.ops backup verify --input /evidence/backup
python -m folio_lattice.ops backup verify --input /evidence/backup \
  --key-id "$RECOVERY_KEY_ID" --tenant-id "$EXPECTED_TENANT_ID"
python -m folio_lattice.ops dr restore \
  --input /evidence/backup \
  --db /evidence/recovered/folio.db \
  --blobs /evidence/recovered/blobs \
  --key-id "$RECOVERY_KEY_ID" --tenant-id "$EXPECTED_TENANT_ID"
```

The restore command refuses existing database or blob targets. Restore into a
new isolated location, keep the recovered service private, and run the
negative tenant/ACL matrix before serving traffic.

## Consistency and failure behavior

The backup manifest records `folio-backup-v1`, a schema signature, tenant
scope, SQLite quick/foreign-key checks, row counts, SHA-256 for the metadata database and
each referenced blob, and the restore order. Metadata preserves versions and
parents, provenance, graph edges, ACL/grant history, external-connection
policy and audit records. FTS/index definitions and their rebuild inputs are
part of the metadata database. Secret values are not copied; connector
credential fields remain references only.

`backup verify` fails closed on a missing or unexpected top-level file,
checksum/size mismatch, SQLite corruption, foreign-key failure, schema/count
mismatch, missing referenced blob, symlink, duplicate path, wrong recovery-key
identity, or out-of-bound blob count/bytes. A changed manifest can only be
detected by an external signature or immutable-store policy; the local format
does not pretend that a checksum alone is an authenticity proof. Restore
refuses existing targets, which is the rollback guard: restore a verified
snapshot into a new isolated target and switch over only after post-restore
ACL/tenant checks.

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

The local test is not hosted durability evidence. `.23` stays open until
encrypted immutable storage, separated recovery credentials, a scheduled
backup age/RPO target, and an independent recovery witness are attached to the
release ledger.

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
