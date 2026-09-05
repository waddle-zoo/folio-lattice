from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
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


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class FolioError(Exception):
    """Expected client-visible error."""


class FolioLattice:
    """Small transactional artifact and graph service for v0."""

    def __init__(self, db_path: str | Path, blob_root: str | Path):
        self.db_path = Path(db_path)
        self.blob_root = Path(blob_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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

    def _ensure_tenant(self, db: sqlite3.Connection, tenant_id: str) -> None:
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
        resolved_type = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        with self.connect() as db:
            self._ensure_tenant(db, tenant_id)
            db.execute(
                "INSERT INTO artifacts(id, tenant_id, name, media_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (artifact_id, tenant_id, name, resolved_type, utc_now()),
            )
        try:
            version = self.write_version(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                data=data,
                media_type=resolved_type,
                actor=actor,
                reason=reason,
                source_context=source_context or {},
                parent_version_id=None,
            )
        except Exception:
            with self.connect() as db:
                db.execute(
                    "DELETE FROM artifacts WHERE id = ? AND tenant_id = ?", (artifact_id, tenant_id)
                )
            raise
        return {"artifact": self.get_artifact(tenant_id, artifact_id), "version": version}

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict[str, Any]:
        with self.connect() as db:
            artifact = db.execute(
                "SELECT id, tenant_id, name, media_type, created_at FROM artifacts WHERE id = ? AND tenant_id = ?",
                (artifact_id, tenant_id),
            ).fetchone()
            if artifact is None:
                raise FolioError("artifact not found")
            latest = db.execute(
                "SELECT id, created_at FROM versions WHERE artifact_id = ? AND tenant_id = ? ORDER BY created_at DESC LIMIT 1",
                (artifact_id, tenant_id),
            ).fetchone()
        result = dict(artifact)
        result["current_version_id"] = latest["id"] if latest else None
        return result

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
        blob_hash = hashlib.sha256(data).hexdigest()
        version_id = new_id("ver")
        created_at = utc_now()
        text = self._text_for(data, media_type)
        with self.connect() as db:
            artifact = db.execute(
                "SELECT id FROM artifacts WHERE id = ? AND tenant_id = ?", (artifact_id, tenant_id)
            ).fetchone()
            if artifact is None:
                raise FolioError("artifact not found")
            current = db.execute(
                "SELECT id FROM versions WHERE artifact_id = ? AND tenant_id = ? ORDER BY created_at DESC LIMIT 1",
                (artifact_id, tenant_id),
            ).fetchone()
            expected_parent = current["id"] if current else None
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
                    json.dumps(source_context, sort_keys=True),
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
        return self.version_metadata(tenant_id, version_id)

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
        if version_id is None:
            version_id = self.get_artifact(tenant_id, artifact_id)["current_version_id"]
        metadata = self.version_metadata(tenant_id, version_id)
        if metadata["artifact_id"] != artifact_id:
            raise FolioError("version does not belong to artifact")
        data = self._version_bytes(tenant_id, version_id)
        result = {"version": metadata, "content_base64": base64.b64encode(data).decode("ascii")}
        text = self._text_for(data, metadata["media_type"])
        if text is not None:
            result["text"] = text
        return result

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
        return dict(row)

    def search(self, tenant_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            return []
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
        return [dict(row) for row in rows]

    def grep(self, tenant_id: str, pattern: str, limit: int = 100) -> list[dict[str, Any]]:
        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            raise FolioError(f"invalid grep pattern: {exc}") from exc
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, artifact_id, version_id, ordinal, start_offset, end_offset, content FROM chunks WHERE tenant_id = ? ORDER BY artifact_id, version_id, ordinal",
                (tenant_id,),
            ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            found = matcher.search(row["content"])
            if found:
                item = dict(row)
                item["match"] = found.group(0)
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
        with self.connect() as db:
            for artifact_id in (source_artifact_id, target_artifact_id):
                if (
                    db.execute(
                        "SELECT 1 FROM artifacts WHERE id = ? AND tenant_id = ?",
                        (artifact_id, tenant_id),
                    ).fetchone()
                    is None
                ):
                    raise FolioError("artifact not found")
            edge_id = new_id("edg")
            db.execute(
                """
                INSERT INTO edges(id, tenant_id, source_artifact_id, target_artifact_id, edge_type, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, source_artifact_id, target_artifact_id, edge_type) DO UPDATE SET metadata_json = excluded.metadata_json
                """,
                (
                    edge_id,
                    tenant_id,
                    source_artifact_id,
                    target_artifact_id,
                    edge_type,
                    json.dumps(metadata or {}, sort_keys=True),
                    utc_now(),
                ),
            )
            row = db.execute(
                """
                SELECT id, source_artifact_id, target_artifact_id, edge_type, metadata_json, created_at
                FROM edges WHERE tenant_id = ? AND source_artifact_id = ? AND target_artifact_id = ? AND edge_type = ?
                """,
                (tenant_id, source_artifact_id, target_artifact_id, edge_type),
            ).fetchone()
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
