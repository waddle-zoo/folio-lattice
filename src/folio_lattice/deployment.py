"""Fail-closed, versioned state upgrade and rollback workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .backup import migration_check
from .service import FolioLattice

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DeploymentError(RuntimeError):
    """Deployment state cannot be verified or safely switched."""


def _version(value: str, field: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise DeploymentError(f"{field} is invalid")
    return value


def _state_path(db_path: Path, state_path: str | Path | None) -> Path:
    return (
        Path(state_path)
        if state_path is not None
        else db_path.with_name(f"{db_path.name}.deployment.json")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise DeploymentError("blob root must be a real directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DeploymentError("blob root contains an unsafe entry")
        if path.is_dir():
            continue
        if not path.is_file():
            raise DeploymentError("blob root contains an unsafe entry")
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(_file_digest(path).encode())
    return digest.hexdigest()


def _inspect(db_path: Path, blob_root: Path) -> dict[str, Any]:
    if not db_path.is_file():
        raise DeploymentError("deployment database is missing")
    if not blob_root.is_dir():
        raise DeploymentError("deployment blob root is missing")
    try:
        readiness = FolioLattice(db_path, blob_root, read_only=True).readiness()
    except Exception as exc:
        raise DeploymentError("deployment state readiness check failed") from exc
    if readiness.get("ready") is not True:
        raise DeploymentError("deployment state is not ready")
    return {
        "ready": True,
        "database_sha256": _file_digest(db_path),
        "blobs_sha256": _tree_digest(blob_root),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(path: Path, expected_version: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise DeploymentError("deployment version state is unavailable") from exc
    if not isinstance(value, dict) or not isinstance(value.get("active_version"), str):
        raise DeploymentError("deployment version state is invalid")
    _version(value["active_version"], "active deployment version")
    if expected_version is not None and value["active_version"] != expected_version:
        raise DeploymentError("deployment version state does not match expected version")
    return value


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise DeploymentError("rollback snapshot manifest is unavailable") from exc
    if not isinstance(value, dict) or not isinstance(value.get("version"), str):
        raise DeploymentError("rollback snapshot manifest is invalid")
    _version(value["version"], "rollback snapshot version")
    if not isinstance(value.get("state"), dict):
        raise DeploymentError("rollback snapshot state is invalid")
    return value


def _rollback_parent(db_path: Path) -> Path:
    parent = db_path.parent / ".folio-rollback"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _copy_database(source: Path, target: Path) -> None:
    try:
        with sqlite3.connect(source) as source_db:
            with sqlite3.connect(target) as target_db:
                source_db.backup(target_db)
                target_db.execute("PRAGMA journal_mode = DELETE")
                target_db.commit()
    except (OSError, sqlite3.Error) as exc:
        raise DeploymentError("database snapshot failed") from exc


def _snapshot(db_path: Path, blob_root: Path, version: str) -> Path:
    snapshot_path = _rollback_parent(db_path) / f"{version}-{uuid.uuid4().hex}"
    temporary = snapshot_path.with_name(f".{snapshot_path.name}.tmp")
    try:
        temporary.mkdir()
        _copy_database(db_path, temporary / "folio.db")
        shutil.copytree(blob_root, temporary / "blobs")
        state = _inspect(temporary / "folio.db", temporary / "blobs")
        _write_json(temporary / "manifest.json", {"version": version, "state": state})
        os.replace(temporary, snapshot_path)
        return snapshot_path
    except (DeploymentError, OSError, shutil.Error) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(exc, DeploymentError):
            raise
        raise DeploymentError("rollback snapshot failed") from exc


def _stage_snapshot(snapshot_path: Path, db_path: Path, blob_root: Path) -> tuple[Path, Path]:
    try:
        manifest = _read_manifest(snapshot_path / "manifest.json")
        expected = manifest.get("state")
        if not isinstance(expected, dict):
            raise DeploymentError("rollback snapshot state is invalid")
        staged_db = Path(tempfile.mkstemp(prefix=".folio-stage-", dir=db_path.parent)[1])
        staged_blobs = Path(tempfile.mkdtemp(prefix=".folio-stage-", dir=blob_root.parent))
        shutil.copyfile(snapshot_path / "folio.db", staged_db)
        shutil.copytree(snapshot_path / "blobs", staged_blobs / "blobs")
        actual = _inspect(staged_db, staged_blobs / "blobs")
        if actual != expected:
            raise DeploymentError("rollback snapshot verification failed")
        return staged_db, staged_blobs / "blobs"
    except DeploymentError:
        if "staged_db" in locals():
            staged_db.unlink(missing_ok=True)
        if "staged_blobs" in locals():
            shutil.rmtree(staged_blobs, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error, shutil.Error) as exc:
        if "staged_db" in locals():
            staged_db.unlink(missing_ok=True)
        if "staged_blobs" in locals():
            shutil.rmtree(staged_blobs, ignore_errors=True)
        raise DeploymentError("rollback snapshot staging failed") from exc


def _replace_live(db_path: Path, blob_root: Path, staged_db: Path, staged_blobs: Path) -> None:
    old_db = Path(tempfile.mkstemp(prefix=".folio-old-", dir=db_path.parent)[1])
    old_db.unlink()
    old_blobs = Path(tempfile.mkdtemp(prefix=".folio-old-", dir=blob_root.parent))
    old_blobs.rmdir()
    old_sidecars: dict[Path, Path] = {}
    moved_db = moved_blobs = new_db = new_blobs = False
    try:
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                old_sidecar = Path(tempfile.mkstemp(prefix=".folio-old-", dir=db_path.parent)[1])
                old_sidecar.unlink()
                os.replace(sidecar, old_sidecar)
                old_sidecars[sidecar] = old_sidecar
        os.replace(db_path, old_db)
        moved_db = True
        os.replace(staged_db, db_path)
        new_db = True
        os.replace(blob_root, old_blobs)
        moved_blobs = True
        os.replace(staged_blobs, blob_root)
        new_blobs = True
    except (OSError, sqlite3.Error) as exc:
        if new_blobs and blob_root.exists():
            shutil.rmtree(blob_root, ignore_errors=True)
        if moved_blobs and old_blobs.exists():
            os.replace(old_blobs, blob_root)
        if new_db and db_path.exists():
            db_path.unlink(missing_ok=True)
        if moved_db and old_db.exists():
            os.replace(old_db, db_path)
        for sidecar, old_sidecar in old_sidecars.items():
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)
            if old_sidecar.exists():
                os.replace(old_sidecar, sidecar)
        raise DeploymentError("state switch failed and was rolled back") from exc
    finally:
        old_db.unlink(missing_ok=True)
        shutil.rmtree(old_blobs, ignore_errors=True)
        staged_db.unlink(missing_ok=True)
        shutil.rmtree(staged_blobs, ignore_errors=True)
        for old_sidecar in old_sidecars.values():
            old_sidecar.unlink(missing_ok=True)


def initialize_deployment_state(
    db_path: str | Path,
    blob_root: str | Path,
    version: str,
    *,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create initial version state only after readiness verification."""

    version = _version(version, "deployment version")
    database = Path(db_path)
    blobs = Path(blob_root)
    state = _inspect(database, blobs)
    target = _state_path(database, state_path)
    _write_json(target, {"active_version": version, "state": state, "updated_at": _utc_now()})
    return {"status": "initialized", "active_version": version, "state": state}


def transactional_upgrade(
    db_path: str | Path,
    blob_root: str | Path,
    *,
    current_version: str,
    target_version: str,
    state_path: str | Path | None = None,
    migrate: Callable[[str | Path, str | Path], Any] = migration_check,
) -> dict[str, Any]:
    """Upgrade live state, retaining a verified snapshot for explicit rollback."""

    current_version = _version(current_version, "current deployment version")
    target_version = _version(target_version, "target deployment version")
    if current_version == target_version:
        raise DeploymentError("target deployment version must change")
    database = Path(db_path)
    blobs = Path(blob_root)
    target_state = _state_path(database, state_path)
    _read_state(target_state, current_version)
    before = _inspect(database, blobs)
    snapshot = _snapshot(database, blobs, current_version)
    try:
        migrate(database, blobs)
        after = _inspect(database, blobs)
        _write_json(
            target_state,
            {
                "active_version": target_version,
                "previous_version": current_version,
                "rollback_snapshot": str(snapshot),
                "before": before,
                "after": after,
                "updated_at": _utc_now(),
            },
        )
    except Exception as exc:
        try:
            _restore_snapshot(snapshot, database, blobs)
            _inspect(database, blobs)
        except Exception as rollback_exc:
            raise DeploymentError(
                "upgrade failed and rollback verification failed"
            ) from rollback_exc
        raise DeploymentError("upgrade failed; previous state restored") from exc
    return {
        "status": "upgraded",
        "from_version": current_version,
        "to_version": target_version,
        "rollback_snapshot": str(snapshot),
        "before": before,
        "after": after,
    }


def _restore_snapshot(snapshot_path: Path, db_path: Path, blob_root: Path) -> None:
    staged_db, staged_blobs = _stage_snapshot(snapshot_path, db_path, blob_root)
    _replace_live(db_path, blob_root, staged_db, staged_blobs)
    _inspect(db_path, blob_root)


def rollback_deployment(
    db_path: str | Path,
    blob_root: str | Path,
    *,
    snapshot_path: str | Path,
    target_version: str,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Switch to a named verified snapshot and fail closed on any mismatch."""

    target_version = _version(target_version, "rollback deployment version")
    database = Path(db_path)
    blobs = Path(blob_root)
    target_state = _state_path(database, state_path)
    current = _read_state(target_state)
    snapshot = Path(snapshot_path)
    try:
        manifest = _read_manifest(snapshot / "manifest.json")
    except DeploymentError:
        raise
    if manifest.get("version") != target_version:
        raise DeploymentError("rollback snapshot version does not match target")
    staged_db, staged_blobs = _stage_snapshot(snapshot, database, blobs)
    current_snapshot = _snapshot(database, blobs, current["active_version"])
    try:
        _replace_live(database, blobs, staged_db, staged_blobs)
        after = _inspect(database, blobs)
        _write_json(
            target_state,
            {
                "active_version": target_version,
                "previous_version": current["active_version"],
                "rollback_snapshot": str(current_snapshot),
                "after": after,
                "updated_at": _utc_now(),
            },
        )
    except Exception as exc:
        try:
            _restore_snapshot(current_snapshot, database, blobs)
        except Exception as rollback_exc:
            raise DeploymentError(
                "rollback failed and recovery verification failed"
            ) from rollback_exc
        raise DeploymentError("rollback failed; previous state restored") from exc
    return {
        "status": "rolled_back",
        "from_version": current["active_version"],
        "to_version": target_version,
        "after": after,
    }
