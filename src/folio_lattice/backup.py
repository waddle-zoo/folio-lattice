"""Authenticated, encrypted local backup and restore primitives.

This module is deliberately a local recovery contract, not a hosted key or
immutability service. A deployment supplies a :class:`BackupKeyProvider`; the
provider keeps manifest-authentication, backup-wrapping, and recovery keys
outside the artifact and outside logs/evidence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .service import FolioLattice

BACKUP_SCHEMA_VERSION = "folio-backup-v2"
FOLIO_SCHEMA_VERSION = "folio-schema-v1"
MANIFEST_NAME = "manifest.json"
PAYLOAD_NAME = "payload.bin"
DATABASE_NAME = "metadata.sqlite"
BLOBS_DIR_NAME = "blobs"
MAX_BACKUP_BLOBS = 100_000
MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_BYTES = MAX_BACKUP_BYTES + (MAX_BACKUP_BLOBS * 2_048) + (16 * 1024 * 1024)
AES_KEY_BYTES = 32
AES_NONCE_BYTES = 12
AES_TAG_BYTES = 16
_BLOB_HASH = re.compile(r"^[0-9a-f]{64}$")
_KEY_REF = re.compile(r"^[A-Za-z0-9._:/-]{1,255}$")


class BackupError(ValueError):
    """A backup is invalid, incomplete, or unsafe to restore."""


class BackupKeyProvider(Protocol):
    """Resolve keys without exposing key bytes to backup metadata or callers."""

    def manifest_auth_key(self) -> bytes: ...

    def backup_key(self, key_ref: str) -> bytes: ...

    def recovery_key(self, key_ref: str) -> bytes: ...


class InMemoryBackupKeyProvider:
    """Small test/local adapter; production callers should use a secret manager."""

    def __init__(
        self,
        *,
        manifest_key: bytes,
        backup_keys: Mapping[str, bytes],
        recovery_keys: Mapping[str, bytes],
    ) -> None:
        self._manifest_key = manifest_key
        self._backup_keys = dict(backup_keys)
        self._recovery_keys = dict(recovery_keys)

    def manifest_auth_key(self) -> bytes:
        return self._manifest_key

    def backup_key(self, key_ref: str) -> bytes:
        return self._backup_keys[key_ref]

    def recovery_key(self, key_ref: str) -> bytes:
        return self._recovery_keys[key_ref]


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
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise BackupError("backup output must be a new or empty directory")
    else:
        path.mkdir(parents=True)


def _remove_sqlite_sidecars(database_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = database_path.with_name(database_path.name + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass


def _key_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or _KEY_REF.fullmatch(value) is None:
        raise BackupError(f"{field} is invalid")
    return value


def _key(provider: BackupKeyProvider | None, method: str, *args: str) -> bytes:
    if provider is None:
        raise BackupError("encrypted backup key provider is required")
    try:
        value = getattr(provider, method)(*args)
    except Exception:
        raise BackupError(f"{method.replace('_', ' ')} is unavailable") from None
    if not isinstance(value, bytes) or len(value) != AES_KEY_BYTES:
        raise BackupError(f"{method.replace('_', ' ')} is invalid")
    return value


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, field: str) -> bytes:
    if not isinstance(value, str) or len(value) > 512:
        raise BackupError(f"{field} is invalid")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise BackupError(f"{field} is invalid") from exc


def _unsigned_manifest(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    base = dict(manifest)
    authentication = base.pop("authentication", None)
    if not isinstance(authentication, dict) or set(authentication) != {"algorithm", "tag"}:
        raise BackupError("manifest authentication is missing")
    if authentication.get("algorithm") != "hmac-sha256":
        raise BackupError("manifest authentication algorithm is unsupported")
    tag = authentication.get("tag")
    if not isinstance(tag, str) or len(tag) != hashlib.sha256().digest_size * 2:
        raise BackupError("manifest authentication tag is invalid")
    try:
        bytes.fromhex(tag)
    except ValueError as exc:
        raise BackupError("manifest authentication tag is invalid") from exc
    return base, _canonical(base)


def _load_manifest(
    backup: Path,
    key_provider: BackupKeyProvider | None,
) -> tuple[dict[str, Any], bytes]:
    if backup.is_symlink() or not backup.is_dir():
        raise BackupError("backup manifest is missing")
    manifest_path = backup / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError("backup manifest is missing")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BackupError("backup manifest exceeds the byte bound")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("backup_schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupError("unsigned or legacy backup manifests are rejected")
    _, encoded = _unsigned_manifest(manifest)
    expected = hmac.new(_key(key_provider, "manifest_auth_key"), encoded, hashlib.sha256).hexdigest()
    actual = manifest["authentication"]["tag"]
    if not hmac.compare_digest(expected, actual):
        raise BackupError("manifest authentication failed")
    return manifest, encoded


class _GcmWriter:
    def __init__(self, target: BinaryIO, encryptor: Any) -> None:
        self.target = target
        self.encryptor = encryptor

    def write(self, value: bytes) -> int:
        encrypted = self.encryptor.update(value)
        self.target.write(encrypted)
        return len(value)

    def finalize(self) -> None:
        self.target.write(self.encryptor.finalize())
        self.target.write(self.encryptor.tag)


def _encrypt_payload(
    staged_db: Path,
    staged_blobs: list[tuple[Path, str]],
    output: Path,
    *,
    data_key: bytes,
    nonce: bytes,
    associated_data: bytes,
) -> None:
    encryptor = Cipher(algorithms.AES(data_key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(associated_data)
    with output.open("wb") as raw:
        raw.write(nonce)
        writer = _GcmWriter(raw, encryptor)
        with tarfile.open(fileobj=writer, mode="w|") as archive:  # type: ignore[call-overload]
            archive.add(staged_db, arcname=DATABASE_NAME, recursive=False)
            for source, relative in staged_blobs:
                archive.add(source, arcname=relative, recursive=False)
        writer.finalize()


def _unwrap_data_key(
    manifest: Mapping[str, Any],
    key_provider: BackupKeyProvider | None,
    *,
    expected_recovery_key_ref: str | None,
) -> bytes:
    keys = manifest.get("keys")
    if not isinstance(keys, dict):
        raise BackupError("backup key references are missing")
    if set(keys) != {"backup_key_ref", "recovery_key_ref", "wrapped_data_key"}:
        raise BackupError("backup key references are invalid")
    recovery_ref = _key_ref(keys.get("recovery_key_ref"), "recovery key reference")
    if expected_recovery_key_ref is not None and recovery_ref != expected_recovery_key_ref:
        raise BackupError("backup recovery-key identity does not match")
    wrapped = keys.get("wrapped_data_key")
    if not isinstance(wrapped, dict) or set(wrapped) != {"backup", "recovery"}:
        raise BackupError("recovery key wrapping is missing")
    item = wrapped["recovery"]
    if not isinstance(item, dict) or set(item) != {"nonce", "ciphertext"}:
        raise BackupError("recovery key wrapping is invalid")
    nonce = _unb64(item["nonce"], "recovery wrapping nonce")
    ciphertext = _unb64(item["ciphertext"], "recovery wrapped key")
    if len(nonce) != AES_NONCE_BYTES or len(ciphertext) != AES_KEY_BYTES + AES_TAG_BYTES:
        raise BackupError("recovery key wrapping is invalid")
    recovery_key = _key(key_provider, "recovery_key", recovery_ref)
    try:
        return AESGCM(recovery_key).decrypt(
            nonce,
            ciphertext,
            f"folio-recovery-key-v2:{recovery_ref}".encode(),
        )
    except (InvalidTag, ValueError, TypeError):
        raise BackupError("recovery key could not decrypt backup") from None


def _decrypt_payload(
    backup: Path,
    manifest: Mapping[str, Any],
    associated_data: bytes,
    data_key: bytes,
    output: Path,
) -> None:
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or set(payload) != {"path", "algorithm", "nonce"}:
        raise BackupError("backup payload metadata is invalid")
    if payload.get("path") != PAYLOAD_NAME or payload.get("algorithm") != "aes-256-gcm-tar-v1":
        raise BackupError("backup payload metadata is invalid")
    nonce = _unb64(payload.get("nonce"), "payload nonce")
    if len(nonce) != AES_NONCE_BYTES:
        raise BackupError("payload nonce is invalid")
    payload_path = backup / PAYLOAD_NAME
    if payload_path.is_symlink() or not payload_path.is_file():
        raise BackupError("backup payload is missing or unsafe")
    payload_size = payload_path.stat().st_size
    if payload_size > MAX_PAYLOAD_BYTES or payload_size < AES_NONCE_BYTES + AES_TAG_BYTES:
        raise BackupError("backup payload is out of bounds")
    decryptor = Cipher(algorithms.AES(data_key), modes.GCM(nonce)).decryptor()
    decryptor.authenticate_additional_data(associated_data)
    ciphertext_size = payload_size - AES_NONCE_BYTES - AES_TAG_BYTES
    with payload_path.open("rb") as source, output.open("wb") as target:
        if source.read(AES_NONCE_BYTES) != nonce:
            raise BackupError("backup payload nonce does not match manifest")
        remaining = ciphertext_size
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise BackupError("backup payload is truncated")
            target.write(decryptor.update(chunk))
            remaining -= len(chunk)
        tag = source.read(AES_TAG_BYTES)
        try:
            target.write(decryptor.finalize_with_tag(tag))
        except (InvalidTag, ValueError):
            raise BackupError("backup payload authentication failed") from None


def _safe_tar_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != name
    ):
        raise BackupError("backup payload contains an unsafe path")
    return name


def _extract_payload(tar_path: Path, destination: Path) -> None:
    destination.mkdir()
    seen: set[str] = set()
    blob_count = 0
    blob_bytes = 0
    try:
        with tarfile.open(tar_path, mode="r:") as archive:
            for member in archive:
                name = _safe_tar_name(member.name)
                if name in seen or not member.isreg() or member.size < 0:
                    raise BackupError("backup payload contains an invalid member")
                if name != DATABASE_NAME:
                    parts = name.split("/")
                    if (
                        len(parts) != 3
                        or parts[0] != BLOBS_DIR_NAME
                        or _BLOB_HASH.fullmatch(parts[2]) is None
                        or parts[1] != parts[2][:2]
                    ):
                        raise BackupError("backup payload contains an unsafe path")
                    blob_count += 1
                    blob_bytes += member.size
                    if blob_count > MAX_BACKUP_BLOBS or blob_bytes > MAX_BACKUP_BYTES:
                        raise BackupError("backup payload exceeds the byte bound")
                elif member.size > MAX_MANIFEST_BYTES:
                    raise BackupError("backup metadata exceeds the byte bound")
                target = destination / PurePosixPath(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError("backup payload member cannot be read")
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                seen.add(name)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("backup payload archive is invalid") from exc
    if DATABASE_NAME not in seen:
        raise BackupError("backup payload metadata is missing")


def _validate_blob_entries(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    blob_manifest = manifest.get("blobs")
    if not isinstance(blob_manifest, dict) or not isinstance(blob_manifest.get("entries"), list):
        raise BackupError("backup blob manifest is missing")
    entries = blob_manifest["entries"]
    if len(entries) > MAX_BACKUP_BLOBS or blob_manifest.get("count") != len(entries):
        raise BackupError("backup blob count is out of bounds")
    listed: set[str] = set()
    total = 0
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise BackupError("backup blob entry is invalid")
        relative = entry["path"]
        if not isinstance(relative, str):
            raise BackupError("backup blob path is invalid")
        parts = relative.split("/")
        blob_hash = _validate_blob_hash(parts[-1] if parts else None)
        if relative != f"{BLOBS_DIR_NAME}/{blob_hash[:2]}/{blob_hash}" or blob_hash in listed:
            raise BackupError("backup blob path is invalid or duplicated")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise BackupError("backup blob checksum is invalid")
        if not isinstance(entry["size"], int) or entry["size"] < 0:
            raise BackupError("backup blob size is invalid")
        total += entry["size"]
        if total > MAX_BACKUP_BYTES:
            raise BackupError("backup exceeds the byte bound")
        listed.add(blob_hash)
        normalized.append(dict(entry))
    if blob_manifest.get("bytes") != total:
        raise BackupError("backup blob byte count is invalid")
    return normalized


def _inspect_extracted(
    extracted: Path,
    manifest: Mapping[str, Any],
    *,
    expected_tenant_scope: set[str] | None,
) -> None:
    database = manifest.get("database")
    if not isinstance(database, dict):
        raise BackupError("backup database manifest is missing")
    database_path = extracted / DATABASE_NAME
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
            if database.get("schema_signature") != _schema_signature(db):
                raise BackupError("backup metadata schema signature does not match")
            if _table_counts(db) != database.get("table_counts"):
                raise BackupError("backup metadata table counts do not match")
            tenant_scope = sorted(row[0] for row in db.execute("SELECT id FROM tenants"))
            if tenant_scope != database.get("tenant_scope"):
                raise BackupError("backup tenant scope does not match metadata")
            if expected_tenant_scope is not None and set(tenant_scope) != expected_tenant_scope:
                raise BackupError("backup tenant scope does not match restore request")
            expected_blobs = set(_referenced_blobs(db))
    except sqlite3.Error as exc:
        raise BackupError(f"backup metadata cannot be opened: {exc}") from exc

    entries = _validate_blob_entries(manifest)
    listed = {entry["path"] for entry in entries}
    actual: set[str] = set()
    for candidate in extracted.rglob("*"):
        if candidate.is_dir():
            continue
        relative = candidate.relative_to(extracted).as_posix()
        if candidate.is_symlink() or relative not in listed | {DATABASE_NAME}:
            raise BackupError("backup payload contains an unexpected file")
        actual.add(relative)
    if {entry["path"].split("/")[-1] for entry in entries} != expected_blobs:
        raise BackupError("backup blob references do not match metadata")
    if actual != listed | {DATABASE_NAME}:
        raise BackupError("backup payload files do not match manifest")
    for entry in entries:
        path = extracted / PurePosixPath(entry["path"])
        if path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            raise BackupError("backup blob checksum does not match")


def _verified_extract(
    backup: Path,
    key_provider: BackupKeyProvider | None,
    *,
    expected_recovery_key_ref: str | None,
    expected_tenant_scope: set[str] | None,
    expected_backup_id: str | None,
) -> tuple[dict[str, Any], Path, Path]:
    manifest, encoded = _load_manifest(backup, key_provider)
    actual_top_level = {entry.name for entry in backup.iterdir()}
    if actual_top_level != {MANIFEST_NAME, PAYLOAD_NAME}:
        raise BackupError("backup has unexpected or missing top-level entries")
    backup_id = manifest.get("backup_id")
    if not isinstance(backup_id, str) or not re.fullmatch(r"backup_[0-9a-f]{32}", backup_id):
        raise BackupError("backup identity is invalid")
    if expected_backup_id is not None and backup_id != expected_backup_id:
        raise BackupError("backup identity does not match restore request")
    keys = manifest.get("keys")
    if not isinstance(keys, dict):
        raise BackupError("backup key references are missing")
    _key_ref(keys.get("backup_key_ref"), "backup key reference")
    data_key = _unwrap_data_key(
        manifest,
        key_provider,
        expected_recovery_key_ref=expected_recovery_key_ref,
    )
    temp_root = Path(tempfile.mkdtemp(prefix=".folio-backup-verify-"))
    tar_path = temp_root / "payload.tar"
    extracted = temp_root / "extracted"
    try:
        _decrypt_payload(backup, manifest, encoded, data_key, tar_path)
        _extract_payload(tar_path, extracted)
        _inspect_extracted(extracted, manifest, expected_tenant_scope=expected_tenant_scope)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return manifest, temp_root, extracted


def create_backup(
    db_path: str | Path,
    blob_root: str | Path,
    output: str | Path,
    *,
    key_provider: BackupKeyProvider | None = None,
    backup_key_ref: str,
    recovery_key_ref: str,
) -> dict[str, Any]:
    """Create a v2 encrypted backup with authenticated metadata."""

    source_db_path = Path(db_path)
    source_blob_root = Path(blob_root)
    output_path = Path(output)
    if not source_db_path.is_file() or not source_blob_root.is_dir():
        raise BackupError("source artifact state is not initialized")
    backup_key_ref = _key_ref(backup_key_ref, "backup key reference")
    recovery_key_ref = _key_ref(recovery_key_ref, "recovery key reference")
    if backup_key_ref == recovery_key_ref:
        raise BackupError("backup and recovery key references must be distinct")
    backup_key = _key(key_provider, "backup_key", backup_key_ref)
    recovery_key = _key(key_provider, "recovery_key", recovery_key_ref)
    manifest_key = _key(key_provider, "manifest_auth_key")
    _safe_output_dir(output_path)
    staging = Path(tempfile.mkdtemp(prefix=".folio-backup-stage-", dir=output_path.parent))
    database_path = staging / DATABASE_NAME
    staged_blob_root = staging / BLOBS_DIR_NAME
    payload_path = output_path / PAYLOAD_NAME
    temporary_payload = output_path / f".{PAYLOAD_NAME}.tmp"
    temporary_manifest = output_path / f".{MANIFEST_NAME}.tmp"
    try:
        with sqlite3.connect(source_db_path) as source_db:
            source_db.execute("PRAGMA foreign_keys = ON")
            _check_sqlite(source_db)
            with sqlite3.connect(database_path) as destination_db:
                source_db.backup(destination_db)
                destination_db.execute("PRAGMA journal_mode = DELETE")
        _remove_sqlite_sidecars(database_path)
        with sqlite3.connect(database_path) as snapshot_db:
            snapshot_db.execute("PRAGMA foreign_keys = ON")
            _check_sqlite(snapshot_db)
            blob_hashes = _referenced_blobs(snapshot_db)
            counts = _table_counts(snapshot_db)
            schema_signature = _schema_signature(snapshot_db)
            tenant_scope = sorted(row[0] for row in snapshot_db.execute("SELECT id FROM tenants"))
        if len(blob_hashes) > MAX_BACKUP_BLOBS:
            raise BackupError("backup exceeds the blob-count bound")
        blob_entries: list[dict[str, Any]] = []
        staged_blobs: list[tuple[Path, str]] = []
        blob_bytes = 0
        for blob_hash in blob_hashes:
            source_blob = _blob_path(source_blob_root, blob_hash)
            if source_blob.is_symlink() or not source_blob.is_file():
                raise BackupError(f"referenced blob is missing or unsafe: {blob_hash}")
            size = source_blob.stat().st_size
            blob_bytes += size
            if blob_bytes > MAX_BACKUP_BYTES:
                raise BackupError("backup exceeds the byte bound")
            staged_blob = _blob_path(staged_blob_root, blob_hash)
            staged_blob.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_blob, staged_blob)
            relative = f"{BLOBS_DIR_NAME}/{blob_hash[:2]}/{blob_hash}"
            staged_blobs.append((staged_blob, relative))
            blob_entries.append({"path": relative, "sha256": _sha256(staged_blob), "size": size})

        data_key = secrets.token_bytes(AES_KEY_BYTES)
        payload_nonce = secrets.token_bytes(AES_NONCE_BYTES)
        backup_wrap_nonce = secrets.token_bytes(AES_NONCE_BYTES)
        recovery_wrap_nonce = secrets.token_bytes(AES_NONCE_BYTES)
        wrapped_backup = AESGCM(backup_key).encrypt(
            backup_wrap_nonce, data_key, f"folio-backup-key-v2:{backup_key_ref}".encode()
        )
        wrapped_recovery = AESGCM(recovery_key).encrypt(
            recovery_wrap_nonce, data_key, f"folio-recovery-key-v2:{recovery_key_ref}".encode()
        )
        manifest_base: dict[str, Any] = {
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "folio_schema_version": FOLIO_SCHEMA_VERSION,
            "backup_id": f"backup_{secrets.token_hex(16)}",
            "created_at": _utc_now(),
            "consistency_set": {
                "payload": PAYLOAD_NAME,
                "indexes": "included in metadata.sqlite; FTS rebuild inputs are included",
                "acl": "included in metadata.sqlite",
                "audit": "included in metadata.sqlite",
                "provenance": "included in metadata.sqlite",
                "secrets": "external references only; secret values are excluded",
            },
            "restore_order": [PAYLOAD_NAME, "post_restore_invariant_check"],
            "database": {
                "path": DATABASE_NAME,
                "size": database_path.stat().st_size,
                "sha256": _sha256(database_path),
                "schema_signature": schema_signature,
                "table_counts": counts,
                "tenant_scope": tenant_scope,
            },
            "blobs": {"count": len(blob_entries), "bytes": blob_bytes, "entries": blob_entries},
            "payload": {
                "path": PAYLOAD_NAME,
                "algorithm": "aes-256-gcm-tar-v1",
                "nonce": _b64(payload_nonce),
            },
            "keys": {
                "backup_key_ref": backup_key_ref,
                "recovery_key_ref": recovery_key_ref,
                "wrapped_data_key": {
                    "backup": {"nonce": _b64(backup_wrap_nonce), "ciphertext": _b64(wrapped_backup)},
                    "recovery": {
                        "nonce": _b64(recovery_wrap_nonce),
                        "ciphertext": _b64(wrapped_recovery),
                    },
                },
            },
            "timing": {"elapsed_seconds": 0.0},
        }
        encoded = _canonical(manifest_base)
        manifest_auth = hmac.new(manifest_key, encoded, hashlib.sha256).hexdigest()
        manifest = {
            **manifest_base,
            "authentication": {"algorithm": "hmac-sha256", "tag": manifest_auth},
        }
        _encrypt_payload(
            database_path,
            staged_blobs,
            temporary_payload,
            data_key=data_key,
            nonce=payload_nonce,
            associated_data=encoded,
        )
        if temporary_payload.stat().st_size > MAX_PAYLOAD_BYTES:
            raise BackupError("backup payload exceeds the byte bound")
        temporary_manifest.write_bytes(_canonical(manifest) + b"\n")
        os.replace(temporary_payload, payload_path)
        os.replace(temporary_manifest, output_path / MANIFEST_NAME)
        return manifest
    except (BackupError, OSError, sqlite3.Error, tarfile.TarError) as exc:
        for partial in (temporary_payload, temporary_manifest, payload_path, output_path / MANIFEST_NAME):
            partial.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"backup failed: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def verify_backup(
    backup: str | Path,
    *,
    key_provider: BackupKeyProvider | None = None,
    expected_key_id: str | None = None,
    expected_recovery_key_ref: str | None = None,
    expected_tenant_scope: set[str] | None = None,
    expected_backup_id: str | None = None,
) -> dict[str, Any]:
    """Authenticate metadata, decrypt the payload, and verify all invariants."""

    if expected_key_id is not None:
        if expected_recovery_key_ref is not None and expected_key_id != expected_recovery_key_ref:
            raise BackupError("conflicting recovery-key identities")
        expected_recovery_key_ref = expected_key_id
    manifest, temp_root, _ = _verified_extract(
        Path(backup),
        key_provider,
        expected_recovery_key_ref=expected_recovery_key_ref,
        expected_tenant_scope=expected_tenant_scope,
        expected_backup_id=expected_backup_id,
    )
    shutil.rmtree(temp_root, ignore_errors=True)
    return {**manifest, "verified": True}


def restore_backup(
    backup: str | Path,
    db_path: str | Path,
    blob_root: str | Path,
    *,
    key_provider: BackupKeyProvider | None = None,
    expected_key_id: str | None = None,
    expected_recovery_key_ref: str | None = None,
    expected_tenant_scope: set[str] | None = None,
    expected_backup_id: str | None = None,
) -> dict[str, Any]:
    """Restore a verified encrypted snapshot into absent targets atomically."""

    if expected_key_id is not None:
        if expected_recovery_key_ref is not None and expected_key_id != expected_recovery_key_ref:
            raise BackupError("conflicting recovery-key identities")
        expected_recovery_key_ref = expected_key_id
    started = time.monotonic()
    target_db = Path(db_path)
    target_blobs = Path(blob_root)
    if target_db.exists() or target_blobs.exists():
        raise BackupError("restore targets must not already exist")
    if target_db.resolve() == target_blobs.resolve():
        raise BackupError("restore database and blob targets must differ")
    manifest, verified_root, extracted = _verified_extract(
        Path(backup),
        key_provider,
        expected_recovery_key_ref=expected_recovery_key_ref,
        expected_tenant_scope=expected_tenant_scope,
        expected_backup_id=expected_backup_id,
    )
    temporary_db: Path | None = None
    temporary_blobs: Path | None = None
    committed_db = False
    committed_blobs = False
    try:
        target_db.parent.mkdir(parents=True, exist_ok=True)
        target_blobs.parent.mkdir(parents=True, exist_ok=True)
        temporary_db = Path(
            tempfile.mkstemp(prefix=".folio-restore-", suffix=".sqlite", dir=target_db.parent)[1]
        )
        temporary_blobs = Path(tempfile.mkdtemp(prefix=".folio-restore-", dir=target_blobs.parent))
        shutil.copyfile(extracted / DATABASE_NAME, temporary_db)
        shutil.copytree(extracted / BLOBS_DIR_NAME, temporary_blobs / BLOBS_DIR_NAME)
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
        if temporary_db is not None:
            temporary_db.unlink(missing_ok=True)
        if temporary_blobs is not None:
            shutil.rmtree(temporary_blobs, ignore_errors=True)
        shutil.rmtree(verified_root, ignore_errors=True)
    return {
        "status": "restored",
        "backup_id": manifest["backup_id"],
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
