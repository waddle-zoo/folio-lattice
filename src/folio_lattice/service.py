from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import sqlite3
import uuid
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TEXT_MEDIA_TYPES = {
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/css",
    "text/csv",
    "text/html",
    "text/javascript",
    "text/markdown",
    "text/plain",
    "text/xml",
}

DEFAULT_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_NAME_LENGTH = 255
MAX_MEDIA_TYPE_LENGTH = 255
MAX_REASON_LENGTH = 2_000
MAX_CONTEXT_BYTES = 32 * 1024
MAX_QUERY_LENGTH = 500
MAX_EDGE_TYPE_LENGTH = 100


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class FolioError(Exception):
    """Expected client-visible error."""


class FolioLattice:
    """Small transactional artifact and graph service for v0."""

    def __init__(
        self,
        db_path: str | Path,
        blob_root: str | Path,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        read_only: bool = False,
    ):
        self.db_path = Path(db_path)
        self.blob_root = Path(blob_root)
        self.read_only = read_only
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        self.max_artifact_bytes = max_artifact_bytes
        if read_only:
            if not self.db_path.is_file() or not self.blob_root.is_dir():
                raise FileNotFoundError("artifact state is not initialized")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.blob_root.mkdir(parents=True, exist_ok=True)
            self.initialize()

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not self.read_only:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    current_version_id TEXT REFERENCES versions(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    parent_version_id TEXT REFERENCES versions(id),
                    blob_hash TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_context TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS versions_artifact_idx
                    ON versions(tenant_id, artifact_id, created_at);
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    version_id TEXT NOT NULL REFERENCES versions(id),
                    ordinal INTEGER NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(version_id, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    chunk_id UNINDEXED,
                    tenant_id UNINDEXED,
                    artifact_id UNINDEXED,
                    version_id UNINDEXED,
                    content,
                    tokenize = 'unicode61'
                );
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    source_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    target_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    edge_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, source_artifact_id, target_artifact_id, edge_type)
                );
                CREATE INDEX IF NOT EXISTS edges_source_idx
                    ON edges(tenant_id, source_artifact_id, edge_type);
                CREATE INDEX IF NOT EXISTS edges_target_idx
                    ON edges(tenant_id, target_artifact_id, edge_type);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(artifacts)")}
            if "current_version_id" not in columns:
                db.execute(
                    "ALTER TABLE artifacts ADD COLUMN current_version_id TEXT REFERENCES versions(id)"
                )
                db.execute(
                    """
                    UPDATE artifacts
                    SET current_version_id = (
                        SELECT id FROM versions
                        WHERE versions.artifact_id = artifacts.id
                          AND versions.tenant_id = artifacts.tenant_id
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    )
                    """
                )

    def _ensure_tenant(self, db: sqlite3.Connection, tenant_id: str) -> None:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        db.execute(
            "INSERT OR IGNORE INTO tenants(id, created_at) VALUES (?, ?)",
            (tenant_id, utc_now()),
        )

    def _blob_path(self, blob_hash: str) -> Path:
        return self.blob_root / blob_hash[:2] / blob_hash

    def _store_blob(self, data: bytes, blob_hash: str | None = None) -> str:
        blob_hash = blob_hash or hashlib.sha256(data).hexdigest()
        destination = self._blob_path(blob_hash)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        return blob_hash

    @staticmethod
    def _validate_text(field: str, value: str, maximum: int) -> str:
        if not value or not value.strip():
            raise FolioError(f"{field} must not be empty")
        if len(value) > maximum:
            raise FolioError(f"{field} exceeds {maximum} characters")
        return value

    @staticmethod
    def _json(value: dict[str, Any], field: str) -> str:
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise FolioError(f"{field} must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise FolioError(f"{field} exceeds {MAX_CONTEXT_BYTES} bytes")
        return encoded

    def _validate_version_input(
        self,
        *,
        data: bytes,
        media_type: str,
        actor: str,
        reason: str,
        source_context: dict[str, Any],
    ) -> str:
        if len(data) > self.max_artifact_bytes:
            raise FolioError(f"artifact exceeds {self.max_artifact_bytes} bytes")
        self._validate_text("media_type", media_type, MAX_MEDIA_TYPE_LENGTH)
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        self._validate_text("reason", reason, MAX_REASON_LENGTH)
        return self._json(source_context, "source_context")

    @staticmethod
    def _text_for(data: bytes, media_type: str) -> str | None:
        if media_type not in TEXT_MEDIA_TYPES and not media_type.startswith("text/"):
            return None
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _chunks(text: str, chunk_size: int = 1200) -> Iterable[tuple[int, int, int, str]]:
        if not text:
            return
        for ordinal, start in enumerate(range(0, len(text), chunk_size)):
            end = min(start + chunk_size, len(text))
            yield ordinal, start, end, text[start:end]

    def create_artifact(
        self,
        *,
        tenant_id: str,
        name: str,
        data: bytes,
        media_type: str | None = None,
        actor: str = "dev",
        reason: str = "initial artifact",
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_id = new_id("art")
        self._validate_text("name", name, MAX_NAME_LENGTH)
        resolved_type = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        source_context_json = self._validate_version_input(
            data=data,
            media_type=resolved_type,
            actor=actor,
            reason=reason,
            source_context=source_context or {},
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_tenant(db, tenant_id)
            db.execute(
                "INSERT INTO artifacts(id, tenant_id, name, media_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (artifact_id, tenant_id, name, resolved_type, utc_now()),
            )
            version_id = self._insert_version(
                db,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                data=data,
                media_type=resolved_type,
                actor=actor,
                reason=reason,
                source_context_json=source_context_json,
                parent_version_id=None,
            )
        return {
            "artifact": self.get_artifact(tenant_id, artifact_id),
            "version": self.version_metadata(tenant_id, version_id),
        }

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict[str, Any]:
        with self.connect() as db:
            artifact = db.execute(
                "SELECT id, tenant_id, name, media_type, current_version_id, created_at FROM artifacts WHERE id = ? AND tenant_id = ?",
                (artifact_id, tenant_id),
            ).fetchone()
            if artifact is None:
                raise FolioError("artifact not found")
        return dict(artifact)

    def write_version(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        data: bytes,
        media_type: str,
        actor: str,
        reason: str,
        source_context: dict[str, Any],
        parent_version_id: str | None,
    ) -> dict[str, Any]:
        source_context_json = self._validate_version_input(
            data=data,
            media_type=media_type,
            actor=actor,
            reason=reason,
            source_context=source_context,
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version_id = self._insert_version(
                db,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                data=data,
                media_type=media_type,
                actor=actor,
                reason=reason,
                source_context_json=source_context_json,
                parent_version_id=parent_version_id,
            )
        return self.version_metadata(tenant_id, version_id)

    def _insert_version(
        self,
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        artifact_id: str,
        data: bytes,
        media_type: str,
        actor: str,
        reason: str,
        source_context_json: str,
        parent_version_id: str | None,
    ) -> str:
        blob_hash = hashlib.sha256(data).hexdigest()
        version_id = new_id("ver")
        created_at = utc_now()
        text = self._text_for(data, media_type)
        artifact = db.execute(
            "SELECT current_version_id FROM artifacts WHERE id = ? AND tenant_id = ?",
            (artifact_id, tenant_id),
        ).fetchone()
        if artifact is None:
            raise FolioError("artifact not found")
        expected_parent = artifact["current_version_id"]
        if parent_version_id != expected_parent:
            raise FolioError(f"parent version mismatch; expected {expected_parent}")
        self._store_blob(data, blob_hash)
        db.execute(
            """
            INSERT INTO versions(
                id, tenant_id, artifact_id, parent_version_id, blob_hash,
                media_type, byte_size, actor, reason, source_context, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                tenant_id,
                artifact_id,
                parent_version_id,
                blob_hash,
                media_type,
                len(data),
                actor,
                reason,
                source_context_json,
                created_at,
            ),
        )
        if text is not None:
            for ordinal, start, end, content in self._chunks(text):
                chunk_id = new_id("chk")
                db.execute(
                    """
                    INSERT INTO chunks(
                        id, tenant_id, artifact_id, version_id, ordinal,
                        start_offset, end_offset, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        tenant_id,
                        artifact_id,
                        version_id,
                        ordinal,
                        start,
                        end,
                        content,
                    ),
                )
                db.execute(
                    "INSERT INTO chunk_fts(chunk_id, tenant_id, artifact_id, version_id, content) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, tenant_id, artifact_id, version_id, content),
                )
        db.execute(
            """
            UPDATE artifacts SET current_version_id = ?, media_type = ?
            WHERE id = ? AND tenant_id = ?
            """,
            (version_id, media_type, artifact_id, tenant_id),
        )
        return version_id

    def version_metadata(self, tenant_id: str, version_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT id, artifact_id, parent_version_id, blob_hash, media_type,
                       byte_size, actor, reason, source_context, created_at
                FROM versions WHERE id = ? AND tenant_id = ?
                """,
                (version_id, tenant_id),
            ).fetchone()
        if row is None:
            raise FolioError("version not found")
        result = dict(row)
        result["source_context"] = json.loads(result["source_context"])
        return result

    def _version_bytes(self, tenant_id: str, version_id: str) -> bytes:
        metadata = self.version_metadata(tenant_id, version_id)
        path = self._blob_path(metadata["blob_hash"])
        if not path.exists():
            raise FolioError("version blob missing")
        return path.read_bytes()

    def read_artifact(
        self, tenant_id: str, artifact_id: str, version_id: str | None = None
    ) -> dict[str, Any]:
        artifact = self.get_artifact(tenant_id, artifact_id)
        if version_id is None:
            version_id = artifact["current_version_id"]
        metadata = self.version_metadata(tenant_id, version_id)
        if metadata["artifact_id"] != artifact_id:
            raise FolioError("version does not belong to artifact")
        data = self._version_bytes(tenant_id, version_id)
        result = {
            "artifact": artifact,
            "version": metadata,
            "chunks": self.chunk_descriptors(tenant_id, artifact_id, version_id),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
        text = self._text_for(data, metadata["media_type"])
        if text is not None:
            result["text"] = text
        return result

    def chunk_descriptors(
        self, tenant_id: str, artifact_id: str, version_id: str
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, artifact_id, version_id, ordinal, start_offset, end_offset
                FROM chunks
                WHERE tenant_id = ? AND artifact_id = ? AND version_id = ?
                ORDER BY ordinal
                """,
                (tenant_id, artifact_id, version_id),
            ).fetchall()
        return [{**dict(row), "offset_unit": "unicode_code_points"} for row in rows]

    def read_chunk(self, tenant_id: str, chunk_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT id, artifact_id, version_id, ordinal, start_offset, end_offset, content
                FROM chunks WHERE id = ? AND tenant_id = ?
                """,
                (chunk_id, tenant_id),
            ).fetchone()
        if row is None:
            raise FolioError("chunk not found")
        result = dict(row)
        result["offset_unit"] = "unicode_code_points"
        return result

    def search(self, tenant_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if len(query) > MAX_QUERY_LENGTH:
            raise FolioError(f"query exceeds {MAX_QUERY_LENGTH} characters")
        try:
            with self.connect() as db:
                rows = db.execute(
                    """
                    SELECT chunk_id, artifact_id, version_id, snippet(chunk_fts, 4, '[', ']', '…', 18) AS snippet,
                           bm25(chunk_fts) AS score
                    FROM chunk_fts
                    WHERE tenant_id = ? AND chunk_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (tenant_id, query, max(1, min(limit, 100))),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise FolioError("invalid search query") from exc
        return [dict(row) for row in rows]

    def grep(self, tenant_id: str, pattern: str, limit: int = 100) -> list[dict[str, Any]]:
        if not pattern:
            return []
        if len(pattern) > MAX_QUERY_LENGTH:
            raise FolioError(f"pattern exceeds {MAX_QUERY_LENGTH} characters")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, artifact_id, version_id, ordinal, start_offset, end_offset, content FROM chunks WHERE tenant_id = ? ORDER BY artifact_id, version_id, ordinal",
                (tenant_id,),
            ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            offset = row["content"].find(pattern)
            if offset >= 0:
                item = dict(row)
                item["match"] = pattern
                item["match_offset"] = offset
                item["offset_unit"] = "unicode_code_points"
                matches.append(item)
                if len(matches) >= max(1, min(limit, 500)):
                    break
        return matches

    def link(
        self,
        tenant_id: str,
        source_artifact_id: str,
        target_artifact_id: str,
        edge_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_artifact_id == target_artifact_id:
            raise FolioError("self-links are not allowed")
        self._validate_text("edge_type", edge_type, MAX_EDGE_TYPE_LENGTH)
        metadata_json = self._json(metadata or {}, "metadata")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for artifact_id in (source_artifact_id, target_artifact_id):
                if (
                    db.execute(
                        "SELECT 1 FROM artifacts WHERE id = ? AND tenant_id = ?",
                        (artifact_id, tenant_id),
                    ).fetchone()
                    is None
                ):
                    raise FolioError("artifact not found")
            existing = db.execute(
                """
                SELECT id, source_artifact_id, target_artifact_id, edge_type,
                       metadata_json, created_at
                FROM edges
                WHERE tenant_id = ? AND source_artifact_id = ?
                  AND target_artifact_id = ? AND edge_type = ?
                """,
                (tenant_id, source_artifact_id, target_artifact_id, edge_type),
            ).fetchone()
            if existing is not None:
                if existing["metadata_json"] != metadata_json:
                    raise FolioError("edge already exists with different immutable metadata")
                row = existing
            else:
                edge_id = new_id("edg")
                db.execute(
                    """
                    INSERT INTO edges(
                        id, tenant_id, source_artifact_id, target_artifact_id,
                        edge_type, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        tenant_id,
                        source_artifact_id,
                        target_artifact_id,
                        edge_type,
                        metadata_json,
                        utc_now(),
                    ),
                )
                row = db.execute(
                    """
                    SELECT id, source_artifact_id, target_artifact_id, edge_type,
                           metadata_json, created_at
                    FROM edges WHERE id = ? AND tenant_id = ?
                    """,
                    (edge_id, tenant_id),
                ).fetchone()
        assert row is not None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def traverse(
        self, tenant_id: str, start_artifact_id: str, max_depth: int = 2, limit: int = 100
    ) -> list[dict[str, Any]]:
        if max_depth < 0 or max_depth > 10:
            raise FolioError("max_depth must be between 0 and 10")
        limit = max(1, min(limit, 500))
        self.get_artifact(tenant_id, start_artifact_id)
        seen = {start_artifact_id}
        queue = deque([(start_artifact_id, 0)])
        results: list[dict[str, Any]] = []
        with self.connect() as db:
            while queue and len(results) < limit:
                current, depth = queue.popleft()
                if depth >= max_depth:
                    continue
                rows = db.execute(
                    """
                    SELECT id, source_artifact_id, target_artifact_id, edge_type, metadata_json
                    FROM edges WHERE tenant_id = ? AND source_artifact_id = ?
                    ORDER BY created_at, id
                    """,
                    (tenant_id, current),
                ).fetchall()
                for row in rows:
                    target = row["target_artifact_id"]
                    item = dict(row)
                    item["metadata"] = json.loads(item.pop("metadata_json"))
                    item["depth"] = depth + 1
                    results.append(item)
                    if target not in seen:
                        seen.add(target)
                        queue.append((target, depth + 1))
                    if len(results) >= limit:
                        break
        return results

    def versions(self, tenant_id: str, artifact_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.get_artifact(tenant_id, artifact_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, artifact_id, parent_version_id, blob_hash, media_type, byte_size, actor, reason, source_context, created_at FROM versions WHERE tenant_id = ? AND artifact_id = ? ORDER BY created_at, id LIMIT ?",
                (tenant_id, artifact_id, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_context"] = json.loads(item["source_context"])
            result.append(item)
        return result

    def health(self) -> dict[str, Any]:
        """Check persistent state without exposing tenant data."""
        try:
            with self.connect() as db:
                db.execute("SELECT 1").fetchone()
            access = os.R_OK if self.read_only else os.W_OK
            ready = os.access(self.blob_root, access)
        except sqlite3.Error:
            ready = False
        return {"status": "ok" if ready else "not_ready", "ready": ready}
