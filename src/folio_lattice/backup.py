"""Bounded, local backup and restore primitives for the durable store.

The format deliberately keeps the metadata database and content-addressed
blobs together.  It is a portable recovery unit for the local/Compose
workflow; hosted deployments still need encrypted, immutable storage and a
separate key-management policy around this unit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .service import FolioLattice

BACKUP_SCHEMA_VERSION = "folio-backup-v1"
FOLIO_SCHEMA_VERSION = "folio-schema-v1"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "metadata.sqlite"
BLOBS_DIR_NAME = "blobs"
MAX_BACKUP_BLOBS = 100_000
MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_BLOB_HASH = re.compile(r"^[0-9a-f]{64}$")


class BackupError(ValueError):
    """A backup is invalid, incomplete, or unsafe to restore."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _schema_signature(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT type, name, COALESCE(sql, '') AS sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return hashlib.sha256(_canonical([tuple(row) for row in rows])).hexdigest()


def _check_sqlite(db: sqlite3.Connection) -> None:
    result = db.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise BackupError("metadata SQLite quick_check failed")
    foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise BackupError("metadata SQLite foreign_key_check failed")


def _table_counts(db: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: int(db.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]) for table in tables
    }


def _validate_blob_hash(value: object) -> str:
    if not isinstance(value, str) or _BLOB_HASH.fullmatch(value) is None:
        raise BackupError("invalid blob hash")
    return value


def _blob_path(root: Path, blob_hash: str) -> Path:
    return root / blob_hash[:2] / blob_hash


def _referenced_blobs(db: sqlite3.Connection) -> list[str]:
    rows = db.execute("SELECT DISTINCT blob_hash FROM versions ORDER BY blob_hash").fetchall()
    return [_validate_blob_hash(row[0]) for row in rows]


def _safe_output_dir(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise BackupError("backup output must be a new or empty directory")
    else:
        path.mkdir(parents=True)


def _remove_sqlite_sidecars(database_path: Path) -> None:
    """Remove only sidecars created by our temporary snapshot connection."""

    for suffix in ("-wal", "-shm"):
        sidecar = database_path.with_name(database_path.name + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass


def create_backup(db_path: str | Path, blob_root: str | Path, output: str | Path) -> dict[str, Any]:
    """Create a consistent database+blob snapshot in a new directory."""

    source_db_path = Path(db_path)
    source_blob_root = Path(blob_root)
    output_path = Path(output)
    if not source_db_path.is_file() or not source_blob_root.is_dir():
        raise BackupError("source artifact state is not initialized")
    _safe_output_dir(output_path)
    database_path = output_path / DATABASE_NAME
    temporary_database = output_path / f".{DATABASE_NAME}.tmp"
    started = time.monotonic()
    try:
        with sqlite3.connect(source_db_path) as source_db:
            source_db.execute("PRAGMA foreign_keys = ON")
            _check_sqlite(source_db)
            with sqlite3.connect(temporary_database) as destination_db:
                source_db.backup(destination_db)
                destination_db.execute("PRAGMA journal_mode = DELETE")
        _remove_sqlite_sidecars(temporary_database)
        os.replace(temporary_database, database_path)
        with sqlite3.connect(database_path) as snapshot_db:
            snapshot_db.execute("PRAGMA foreign_keys = ON")
            _check_sqlite(snapshot_db)
            blob_hashes = _referenced_blobs(snapshot_db)
            counts = _table_counts(snapshot_db)
            schema_signature = _schema_signature(snapshot_db)
            tenant_scope = sorted(row[0] for row in snapshot_db.execute("SELECT id FROM tenants"))
        _remove_sqlite_sidecars(database_path)

        if len(blob_hashes) > MAX_BACKUP_BLOBS:
            raise BackupError("backup exceeds the blob-count bound")
        blob_entries: list[dict[str, Any]] = []
        blob_bytes = 0
        for blob_hash in blob_hashes:
            source_blob = _blob_path(source_blob_root, blob_hash)
            if source_blob.is_symlink() or not source_blob.is_file():
                raise BackupError(f"referenced blob is missing or unsafe: {blob_hash}")
            size = source_blob.stat().st_size
            blob_bytes += size
            if blob_bytes > MAX_BACKUP_BYTES:
                raise BackupError("backup exceeds the byte bound")
            destination_blob = _blob_path(output_path / BLOBS_DIR_NAME, blob_hash)
            destination_blob.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_blob, destination_blob)
            blob_entries.append(
                {
                    "path": destination_blob.relative_to(output_path).as_posix(),
                    "sha256": _sha256(destination_blob),
                    "size": size,
                }
            )

        manifest: dict[str, Any] = {
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "folio_schema_version": FOLIO_SCHEMA_VERSION,
            "created_at": _utc_now(),
            "consistency_set": {
                "metadata": DATABASE_NAME,
                "blobs": BLOBS_DIR_NAME,
                "indexes": "included in metadata.sqlite; FTS rebuild inputs are included",
                "acl": "included in metadata.sqlite",
                "audit": "included in metadata.sqlite",
                "provenance": "included in metadata.sqlite",
                "secrets": "external references only; secret values are excluded",
            },
            "restore_order": [DATABASE_NAME, BLOBS_DIR_NAME, "post_restore_invariant_check"],
            "database": {
                "path": DATABASE_NAME,
                "size": database_path.stat().st_size,
                "sha256": _sha256(database_path),
                "schema_signature": schema_signature,
                "table_counts": counts,
                "tenant_scope": tenant_scope,
            },
            "blobs": {"count": len(blob_entries), "bytes": blob_bytes, "entries": blob_entries},
            "timing": {"elapsed_seconds": round(time.monotonic() - started, 6)},
            "integrity": {
                "algorithm": "sha256",
                "manifest_is_signed": False,
                "encryption_key_id": None,
            },
        }
        encoded = _canonical(manifest)
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise BackupError("backup manifest exceeds the byte bound")
        temporary_manifest = output_path / f".{MANIFEST_NAME}.tmp"
        temporary_manifest.write_bytes(encoded + b"\n")
        os.replace(temporary_manifest, output_path / MANIFEST_NAME)
        return manifest
    except (OSError, sqlite3.Error) as exc:
        for temporary in (temporary_database, output_path / f".{MANIFEST_NAME}.tmp"):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise BackupError(f"backup failed: {exc}") from exc


def _load_manifest(backup: Path) -> dict[str, Any]:
    manifest_path = backup / MANIFEST_NAME
    if not backup.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError("backup manifest is missing")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BackupError("backup manifest exceeds the byte bound")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("backup_schema_version") != BACKUP_SCHEMA_VERSION
    ):
        raise BackupError("unsupported backup schema version")
    if manifest.get("integrity", {}).get("algorithm") != "sha256":
        raise BackupError("backup integrity algorithm is unsupported")
    return manifest


def verify_backup(
    backup: str | Path,
    *,
    expected_key_id: str | None = None,
    expected_tenant_scope: set[str] | None = None,
) -> dict[str, Any]:
    """Verify checksums, SQLite invariants, references, and bounded layout."""

    backup_path = Path(backup)
    manifest = _load_manifest(backup_path)
    integrity = manifest["integrity"]
    if expected_key_id is not None and integrity.get("encryption_key_id") != expected_key_id:
        raise BackupError("backup encryption key identity does not match")
    allowed = {MANIFEST_NAME, DATABASE_NAME, BLOBS_DIR_NAME}
    actual = {entry.name for entry in backup_path.iterdir()}
    if actual != allowed:
        raise BackupError("backup has unexpected or missing top-level entries")
    database = manifest.get("database")
    if not isinstance(database, dict):
        raise BackupError("backup database manifest is missing")
    database_path = backup_path / DATABASE_NAME
    if database_path.is_symlink() or not database_path.is_file():
        raise BackupError("backup metadata database is missing or unsafe")
    if database.get("size") != database_path.stat().st_size or database.get("sha256") != _sha256(
        database_path
    ):
        raise BackupError("backup metadata checksum does not match")
    try:
        with sqlite3.connect(database_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            _check_sqlite(db)
            expected_signature = database.get("schema_signature")
            if expected_signature != _schema_signature(db):
                raise BackupError("backup metadata schema signature does not match")
            counts = _table_counts(db)
            if counts != database.get("table_counts"):
                raise BackupError("backup metadata table counts do not match")
            tenant_scope = sorted(row[0] for row in db.execute("SELECT id FROM tenants"))
            if tenant_scope != database.get("tenant_scope"):
                raise BackupError("backup tenant scope does not match metadata")
            if expected_tenant_scope is not None and set(tenant_scope) != expected_tenant_scope:
                raise BackupError("backup tenant scope does not match restore request")
            expected_blobs = set(_referenced_blobs(db))
    except sqlite3.Error as exc:
        raise BackupError(f"backup metadata cannot be opened: {exc}") from exc

    blob_manifest = manifest.get("blobs")
    if not isinstance(blob_manifest, dict) or not isinstance(blob_manifest.get("entries"), list):
        raise BackupError("backup blob manifest is missing")
    entries = blob_manifest["entries"]
    if len(entries) > MAX_BACKUP_BLOBS or blob_manifest.get("count") != len(entries):
        raise BackupError("backup blob count is out of bounds")
    listed: set[str] = set()
    total_bytes = 0
    blob_root = backup_path / BLOBS_DIR_NAME
    if blob_root.is_symlink() or not blob_root.is_dir():
        raise BackupError("backup blob directory is missing or unsafe")
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("backup blob entry is invalid")
        relative = entry.get("path")
        blob_hash = _validate_blob_hash(
            Path(str(relative)).name if isinstance(relative, str) else None
        )
        expected_path = f"{BLOBS_DIR_NAME}/{blob_hash[:2]}/{blob_hash}"
        if relative != expected_path or blob_hash in listed:
            raise BackupError("backup blob path is invalid or duplicated")
        listed.add(blob_hash)
        blob_path = backup_path / PurePosixPath(relative)
        if blob_path.is_symlink() or not blob_path.is_file():
            raise BackupError("backup blob is missing or unsafe")
        size = blob_path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_BACKUP_BYTES or entry.get("size") != size:
            raise BackupError("backup blob size is invalid")
        if entry.get("sha256") != _sha256(blob_path):
            raise BackupError("backup blob checksum does not match")
    if listed != expected_blobs or blob_manifest.get("bytes") != total_bytes:
        raise BackupError("backup blob references do not match metadata")
    for candidate in (backup_path / BLOBS_DIR_NAME).rglob("*"):
        if candidate.is_dir():
            continue
        if candidate.is_symlink() or candidate.relative_to(backup_path).as_posix() not in {
            entry["path"] for entry in entries
        }:
            raise BackupError("backup contains an unexpected blob file")
    return {**manifest, "verified": True}


def restore_backup(
    backup: str | Path,
    db_path: str | Path,
    blob_root: str | Path,
    *,
    expected_key_id: str | None = None,
    expected_tenant_scope: set[str] | None = None,
) -> dict[str, Any]:
    """Restore into absent targets after a complete verification pass."""

    started = time.monotonic()
    manifest = verify_backup(
        backup,
        expected_key_id=expected_key_id,
        expected_tenant_scope=expected_tenant_scope,
    )
    target_db = Path(db_path)
    target_blobs = Path(blob_root)
    if target_db.exists() or target_blobs.exists():
        raise BackupError("restore targets must not already exist")
    if target_db.resolve() == target_blobs.resolve():
        raise BackupError("restore database and blob targets must differ")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target_blobs.parent.mkdir(parents=True, exist_ok=True)
    temporary_db = Path(
        tempfile.mkstemp(prefix=".folio-restore-", suffix=".sqlite", dir=target_db.parent)[1]
    )
    temporary_blobs = Path(tempfile.mkdtemp(prefix=".folio-restore-", dir=target_blobs.parent))
    committed_db = False
    committed_blobs = False
    try:
        shutil.copyfile(Path(backup) / DATABASE_NAME, temporary_db)
        shutil.copytree(Path(backup) / BLOBS_DIR_NAME, temporary_blobs / BLOBS_DIR_NAME)
        restored = FolioLattice(
            temporary_db, temporary_blobs / BLOBS_DIR_NAME, read_only=True
        ).readiness()
        if not restored["ready"]:
            raise BackupError("restored state failed readiness checks")
        os.replace(temporary_db, target_db)
        committed_db = True
        os.replace(temporary_blobs / BLOBS_DIR_NAME, target_blobs)
        committed_blobs = True
    except BackupError:
        if committed_db and not committed_blobs:
            target_db.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        if committed_db and not committed_blobs:
            target_db.unlink(missing_ok=True)
        raise BackupError(f"restore failed: {exc}") from exc
    finally:
        temporary_db.unlink(missing_ok=True)
        shutil.rmtree(temporary_blobs, ignore_errors=True)
    return {
        "status": "restored",
        "backup_created_at": manifest["created_at"],
        "restored_at": _utc_now(),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "readiness": restored,
    }


def migration_check(db_path: str | Path, blob_root: str | Path) -> dict[str, Any]:
    """Run the current forward-compatible initializer twice and verify state."""

    started = time.monotonic()
    first = FolioLattice(db_path, blob_root)
    before = first.readiness()
    first.initialize()
    first.initialize()
    after = first.readiness()
    if not after["ready"]:
        raise BackupError("repeatable migration check left the service unready")
    return {
        "status": "ok",
        "migration": "forward-compatible, repeatable initializer",
        "destructive_down_migrations": False,
        "schema_version": FOLIO_SCHEMA_VERSION,
        "before": before,
        "after": after,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
