from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
import uuid
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
# A search page is small, but the SQL candidate scan must also be bounded when
# a tenant has many matching chunks. Cursor predicates are applied before this
# cap so continuation remains complete for ordinary pages.
MAX_SEARCH_CANDIDATES = 10_000
ARTIFACT_LIST_CURSOR_SEPARATOR = "|"
MAX_ARTIFACT_LIST_CURSOR_LENGTH = 512
MAX_EDGE_TYPE_LENGTH = 100
MAX_GRAPH_COMPONENT_NODES = 500
MAX_EXTERNAL_ENDPOINT_LENGTH = 4_096
MAX_EXTERNAL_CONNECTION_NAME_LENGTH = 255
MAX_EXTERNAL_LIST_ITEMS = 128
MAX_EXTERNAL_ITEM_LENGTH = 2_048
MAX_EXTERNAL_ARGUMENT_BYTES = 64 * 1024
EXTERNAL_SQLITE_BUSY_TIMEOUT_SECONDS = 35.0
_EXTERNAL_POLICY_KEYS = frozenset({"slack"})
_EXTERNAL_POLICY_PUBLIC_KEYS = frozenset({"channels", "max_time_range_seconds"})
_EXTERNAL_PUBLIC_FIELDS = (
    "id",
    "tenant_id",
    "name",
    "endpoint",
    "origin",
    "transport",
    "approved_tools",
    "approved_resources",
    "allowed_origins",
    "status",
    "health_status",
    "last_health_at",
    "created_by",
    "created_at",
    "revoked_by",
    "revoked_at",
    "policy_version",
)
EXTERNAL_MCP_POLICY_VERSION = "external-mcp-v1"
ACL_ACTIONS = frozenset({"read", "write", "share"})
ACL_SUBJECT_TYPE = "actor"
OWNER_GRANT_REASON = "artifact owner"
REQUIRED_SCHEMA_OBJECTS = frozenset(
    {
        "tenants",
        "artifacts",
        "versions",
        "chunks",
        "chunk_fts",
        "edges",
        "acl_grants",
        "external_mcp_connections",
        "external_mcp_audit",
    }
)
REQUIRED_SCHEMA_INDEXES = frozenset(
    {
        "versions_artifact_idx",
        "edges_source_idx",
        "edges_target_idx",
        "acl_grants_lookup_idx",
        "acl_grants_active_idx",
        "external_mcp_connections_name_idx",
        "external_mcp_connections_tenant_idx",
        "external_mcp_audit_lookup_idx",
    }
)
REQUIRED_SCHEMA_COLUMNS = {
    "acl_grants": frozenset(
        {
            "tenant_id",
            "artifact_id",
            "subject_type",
            "subject_id",
            "action",
            "status",
        }
    ),
    "external_mcp_connections": frozenset(
        {
            "tenant_id",
            "endpoint",
            "origin",
            "status",
            "health_status",
            "policy_json",
            "policy_version",
        }
    ),
    "external_mcp_audit": frozenset(
        {"tenant_id", "connection_id", "actor_id", "action", "outcome"}
    ),
}


class _UseCallerActor:
    pass


_USE_CALLER_ACTOR = _UseCallerActor()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _parse_artifact_list_cursor(cursor: object) -> tuple[str, str]:
    if not isinstance(cursor, str) or len(cursor) > MAX_ARTIFACT_LIST_CURSOR_LENGTH:
        raise FolioError("invalid artifact_list cursor")
    timestamp, separator, artifact_id = cursor.partition(ARTIFACT_LIST_CURSOR_SEPARATOR)
    if (
        not separator
        or not timestamp
        or not artifact_id
        or ARTIFACT_LIST_CURSOR_SEPARATOR in artifact_id
    ):
        raise FolioError("invalid artifact_list cursor")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise FolioError("invalid artifact_list cursor") from exc
    if parsed.tzinfo is None:
        raise FolioError("invalid artifact_list cursor")
    if not artifact_id.strip() or len(artifact_id) > MAX_NAME_LENGTH:
        raise FolioError("invalid artifact_list cursor")
    return timestamp, artifact_id


def _search_snippet(value: str, query: str, *, radius: int = 72) -> str:
    """Return a short, case-preserving excerpt with the match marked."""

    folded_value = value.casefold()
    folded_query = query.casefold()
    offset = folded_value.find(folded_query)
    matched_text = query
    if offset < 0:
        # Keep the old FTS-friendly behavior for punctuation-separated phrases
        # (for example, ``graph-target`` matching ``graph target``) while the
        # primary contract remains a case-insensitive substring search.
        normalized_query = re.sub(r"[\W_]+", " ", folded_query).strip()
        normalized_value = re.sub(r"[\W_]+", " ", folded_value).strip()
        normalized_offset = normalized_value.find(normalized_query)
        if normalized_offset >= 0 and normalized_query:
            first_word = normalized_query.split(" ", 1)[0]
            offset = folded_value.find(first_word)
            matched_text = value[offset : offset + len(first_word)]
    if offset < 0:
        return value[: radius * 2]
    end = offset + len(matched_text)
    start = max(0, offset - radius)
    finish = min(len(value), end + radius)
    prefix = "…" if start else ""
    suffix = "…" if finish < len(value) else ""
    return f"{prefix}{value[start:offset]}[{value[offset:end]}]{value[end:finish]}{suffix}"


def _search_match_offset(value: str, query: str) -> int:
    """Find a case-insensitive substring, retaining tokenized phrase compatibility."""

    offset = value.casefold().find(query.casefold())
    if offset >= 0:
        return offset
    normalized_query = re.sub(r"[\W_]+", " ", query.casefold()).strip()
    normalized_value = re.sub(r"[\W_]+", " ", value.casefold()).strip()
    if not normalized_query or normalized_query not in normalized_value:
        return -1
    first_word = normalized_query.split(" ", 1)[0]
    return value.casefold().find(first_word)


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
            connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=EXTERNAL_SQLITE_BUSY_TIMEOUT_SECONDS,
            )
        else:
            connection = sqlite3.connect(self.db_path, timeout=EXTERNAL_SQLITE_BUSY_TIMEOUT_SECONDS)
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
                    owner_actor_id TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS acl_grants (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    subject_type TEXT NOT NULL CHECK(subject_type = 'actor'),
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('read', 'write', 'share')),
                    status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    revoked_by TEXT,
                    revoked_at TEXT,
                    revocation_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS acl_grants_lookup_idx
                    ON acl_grants(tenant_id, artifact_id, subject_type, subject_id, action, status);
                CREATE UNIQUE INDEX IF NOT EXISTS acl_grants_active_idx
                    ON acl_grants(tenant_id, artifact_id, subject_type, subject_id, action)
                    WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS external_mcp_connections (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    name TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    transport TEXT NOT NULL CHECK(transport = 'streamable_http'),
                    approved_tools TEXT NOT NULL,
                    approved_resources TEXT NOT NULL,
                    allowed_origins TEXT NOT NULL,
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    credential_ref TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
                    health_status TEXT NOT NULL CHECK(health_status IN ('unknown', 'healthy', 'unhealthy')),
                    last_health_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_by TEXT,
                    revoked_at TEXT,
                    policy_version TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS external_mcp_connections_name_idx
                    ON external_mcp_connections(tenant_id, name);
                CREATE INDEX IF NOT EXISTS external_mcp_connections_tenant_idx
                    ON external_mcp_connections(tenant_id, status, created_at);
                CREATE TABLE IF NOT EXISTS external_mcp_audit (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    connection_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    tool_name TEXT,
                    resource_uri TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS external_mcp_audit_lookup_idx
                    ON external_mcp_audit(tenant_id, connection_id, created_at, id);
                """
            )
            grant_columns = {row["name"] for row in db.execute("PRAGMA table_info(acl_grants)")}
            if "revocation_reason" not in grant_columns:
                db.execute("ALTER TABLE acl_grants ADD COLUMN revocation_reason TEXT")
            external_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(external_mcp_connections)")
            }
            if "policy_json" not in external_columns:
                db.execute(
                    "ALTER TABLE external_mcp_connections ADD COLUMN policy_json TEXT NOT NULL DEFAULT '{}'"
                )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(artifacts)")}
            if "owner_actor_id" not in columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN owner_actor_id TEXT")
            rows = db.execute(
                "SELECT id, tenant_id FROM artifacts WHERE owner_actor_id IS NULL"
            ).fetchall()
            for row in rows:
                owner = db.execute(
                    """
                    SELECT actor FROM versions
                    WHERE artifact_id = ? AND tenant_id = ?
                    ORDER BY created_at, id
                    LIMIT 1
                    """,
                    (row["id"], row["tenant_id"]),
                ).fetchone()
                if owner is None or not owner["actor"]:
                    raise FolioError("cannot migrate artifact without an owner actor")
                db.execute(
                    "UPDATE artifacts SET owner_actor_id = ? WHERE id = ?",
                    (owner["actor"], row["id"]),
                )
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
            for row in db.execute(
                "SELECT id, tenant_id, owner_actor_id FROM artifacts WHERE owner_actor_id IS NOT NULL"
            ):
                for action in ACL_ACTIONS:
                    db.execute(
                        """
                        INSERT OR IGNORE INTO acl_grants(
                            id, tenant_id, artifact_id, subject_type, subject_id,
                            action, status, created_by, created_at, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                        """,
                        (
                            f"owner_{row['id']}_{action}",
                            row["tenant_id"],
                            row["id"],
                            ACL_SUBJECT_TYPE,
                            row["owner_actor_id"],
                            action,
                            row["owner_actor_id"],
                            utc_now(),
                            OWNER_GRANT_REASON,
                        ),
                    )

    def _ensure_tenant(self, db: sqlite3.Connection, tenant_id: str) -> None:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        db.execute(
            "INSERT OR IGNORE INTO tenants(id, created_at) VALUES (?, ?)",
            (tenant_id, utc_now()),
        )

    def _authorize(
        self,
        db: sqlite3.Connection,
        tenant_id: str,
        artifact_id: str,
        actor: str | None,
        action: str,
    ) -> None:
        if action not in ACL_ACTIONS:
            raise FolioError("unknown authorization action")
        artifact = db.execute(
            "SELECT owner_actor_id FROM artifacts WHERE id = ? AND tenant_id = ?",
            (artifact_id, tenant_id),
        ).fetchone()
        if artifact is None:
            raise FolioError("artifact not found")
        if actor is None:
            return
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        if artifact["owner_actor_id"] == actor:
            return
        grant = db.execute(
            """
            SELECT 1 FROM acl_grants
            WHERE tenant_id = ? AND artifact_id = ?
              AND subject_type = ? AND subject_id = ?
              AND action = ? AND status = 'active'
            LIMIT 1
            """,
            (tenant_id, artifact_id, ACL_SUBJECT_TYPE, actor, action),
        ).fetchone()
        if grant is None:
            raise FolioError("artifact not found")

    def _access_clause(
        self, actor: str | None, action: str, *, artifact_alias: str = "a"
    ) -> tuple[str, tuple[str, ...]]:
        if actor is None:
            return "1 = 1", ()
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        if action not in ACL_ACTIONS:
            raise FolioError("unknown authorization action")
        return (
            f"({artifact_alias}.owner_actor_id = ? OR EXISTS ("
            "SELECT 1 FROM acl_grants g "
            f"WHERE g.tenant_id = {artifact_alias}.tenant_id "
            f"AND g.artifact_id = {artifact_alias}.id "
            "AND g.subject_type = ? AND g.subject_id = ? "
            "AND g.action = ? AND g.status = 'active'))",
            (actor, ACL_SUBJECT_TYPE, actor, action),
        )

    @staticmethod
    def _grant_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

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
                "INSERT INTO artifacts(id, tenant_id, owner_actor_id, name, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, tenant_id, actor, name, resolved_type, utc_now()),
            )
            for action in ACL_ACTIONS:
                db.execute(
                    """
                    INSERT INTO acl_grants(
                        id, tenant_id, artifact_id, subject_type, subject_id,
                        action, status, created_by, created_at, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        f"owner_{artifact_id}_{action}",
                        tenant_id,
                        artifact_id,
                        ACL_SUBJECT_TYPE,
                        actor,
                        action,
                        actor,
                        utc_now(),
                        OWNER_GRANT_REASON,
                    ),
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
            "artifact": self.get_artifact(tenant_id, artifact_id, actor=actor),
            "version": self.version_metadata(tenant_id, version_id, actor=actor),
        }

    def get_artifact(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        actor: str | None = None,
        action: str = "read",
    ) -> dict[str, Any]:
        with self.connect() as db:
            artifact = db.execute(
                "SELECT id, tenant_id, owner_actor_id, name, media_type, current_version_id, created_at FROM artifacts WHERE id = ? AND tenant_id = ?",
                (artifact_id, tenant_id),
            ).fetchone()
            if artifact is None:
                raise FolioError("artifact not found")
            self._authorize(db, tenant_id, artifact_id, actor, action)
        result = dict(artifact)
        result.pop("owner_actor_id", None)
        return result

    def list_artifacts(
        self,
        tenant_id: str,
        limit: int = 20,
        *,
        actor: str | None = None,
        name: str | None = None,
        media_type: str | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        if name is not None:
            self._validate_text("name", name, MAX_NAME_LENGTH)
        if media_type is not None:
            self._validate_text("media_type", media_type, MAX_MEDIA_TYPE_LENGTH)
        cursor_params: tuple[str, ...] = ()
        cursor_sql = ""
        if cursor is not None:
            cursor_timestamp, cursor_artifact_id = _parse_artifact_list_cursor(cursor)
            cursor_sql = """
                      AND (
                          COALESCE(v.created_at, a.created_at) < ?
                          OR (
                              COALESCE(v.created_at, a.created_at) = ?
                              AND a.id < ?
                          )
                      )
            """
            cursor_params = (cursor_timestamp, cursor_timestamp, cursor_artifact_id)
        with self.connect() as db:
            access_sql, access_params = self._access_clause(actor, "read")
            rows = db.execute(
                """
                SELECT a.id, a.name, a.media_type, a.created_at,
                       a.current_version_id, v.created_at AS updated_at,
                       (SELECT COUNT(*) FROM edges e
                        WHERE e.tenant_id = a.tenant_id
                          AND (e.source_artifact_id = a.id OR e.target_artifact_id = a.id)) AS graph_edges
                FROM artifacts a
                LEFT JOIN versions v
                  ON v.id = a.current_version_id AND v.tenant_id = a.tenant_id
                WHERE a.tenant_id = ? AND """
                + access_sql
                + cursor_sql
                + """
                  AND (? IS NULL OR a.name = ?)
                  AND (? IS NULL OR a.media_type = ?)
                ORDER BY COALESCE(v.created_at, a.created_at) DESC, a.id DESC
                LIMIT ?
                """,
                (
                    tenant_id,
                    *access_params,
                    *cursor_params,
                    name,
                    name,
                    media_type,
                    media_type,
                    max(1, min(limit, 100)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

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
        authorization_actor: str | None | _UseCallerActor = _USE_CALLER_ACTOR,
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
            request_actor = (
                actor if isinstance(authorization_actor, _UseCallerActor) else authorization_actor
            )
            self._authorize(db, tenant_id, artifact_id, request_actor, "write")
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
        return self.version_metadata(tenant_id, version_id, actor=request_actor, action="write")

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

    def version_metadata(
        self,
        tenant_id: str,
        version_id: str,
        *,
        actor: str | None = None,
        action: str = "read",
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT id, artifact_id, parent_version_id, blob_hash, media_type,
                       byte_size, actor, reason, source_context, created_at
                FROM versions
                WHERE id = ? AND tenant_id = ?
                  AND (? IS NULL OR artifact_id = ?)
                """,
                (version_id, tenant_id, artifact_id, artifact_id),
            ).fetchone()
            if row is None:
                raise FolioError("version not found")
            try:
                self._authorize(db, tenant_id, row["artifact_id"], actor, action)
            except FolioError as exc:
                if str(exc) == "artifact not found":
                    raise FolioError("version not found") from None
                raise
        result = dict(row)
        result["source_context"] = json.loads(result["source_context"])
        return result

    def _version_bytes(
        self,
        tenant_id: str,
        version_id: str,
        *,
        actor: str | None = None,
        artifact_id: str | None = None,
    ) -> bytes:
        metadata = self.version_metadata(
            tenant_id, version_id, actor=actor, artifact_id=artifact_id
        )
        path = self._blob_path(metadata["blob_hash"])
        if not path.exists():
            raise FolioError("version blob missing")
        return path.read_bytes()

    def read_artifact(
        self,
        tenant_id: str,
        artifact_id: str,
        version_id: str | None = None,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        artifact = self.get_artifact(tenant_id, artifact_id, actor=actor)
        if version_id is None:
            version_id = artifact["current_version_id"]
        metadata = self.version_metadata(
            tenant_id, version_id, actor=actor, artifact_id=artifact_id
        )
        data = self._version_bytes(tenant_id, version_id, actor=actor, artifact_id=artifact_id)
        result = {
            "artifact": artifact,
            "version": metadata,
            "chunks": self.chunk_descriptors(tenant_id, artifact_id, version_id, actor=actor),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
        text = self._text_for(data, metadata["media_type"])
        if text is not None:
            result["text"] = text
        return result

    def chunk_descriptors(
        self,
        tenant_id: str,
        artifact_id: str,
        version_id: str,
        *,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._authorize(db, tenant_id, artifact_id, actor, "read")
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

    def read_chunk(
        self, tenant_id: str, chunk_id: str, *, actor: str | None = None
    ) -> dict[str, Any]:
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
            try:
                self._authorize(db, tenant_id, row["artifact_id"], actor, "read")
            except FolioError as exc:
                if str(exc) == "artifact not found":
                    raise FolioError("chunk not found") from None
                raise
        result = dict(row)
        result["offset_unit"] = "unicode_code_points"
        return result

    def _component_ids(
        self,
        db: sqlite3.Connection,
        tenant_id: str,
        start_artifact_id: str,
        limit: int,
        *,
        actor: str | None,
    ) -> list[str]:
        self._authorize(db, tenant_id, start_artifact_id, actor, "read")
        limit = max(1, min(limit, MAX_GRAPH_COMPONENT_NODES))
        seen = {start_artifact_id}
        ordered = [start_artifact_id]
        queue = deque([start_artifact_id])
        access_sql, access_params = self._access_clause(actor, "read")
        while queue and len(seen) < limit:
            current = queue.popleft()
            rows = db.execute(
                """
                SELECT a.id AS artifact_id
                FROM edges e
                JOIN artifacts a
                  ON a.tenant_id = e.tenant_id
                 AND a.id <> ?
                 AND (a.id = e.source_artifact_id OR a.id = e.target_artifact_id)
                WHERE e.tenant_id = ?
                  AND (e.source_artifact_id = ? OR e.target_artifact_id = ?)
                  AND """
                + access_sql
                + " ORDER BY e.created_at, e.id",
                (current, tenant_id, current, current, *access_params),
            ).fetchall()
            for row in rows:
                artifact_id = row["artifact_id"]
                if artifact_id in seen:
                    continue
                seen.add(artifact_id)
                ordered.append(artifact_id)
                queue.append(artifact_id)
                if len(seen) >= limit:
                    break
        return ordered

    def graph_component(
        self,
        tenant_id: str,
        start_artifact_id: str,
        limit: int = 100,
        *,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            artifact_ids = self._component_ids(
                db,
                tenant_id,
                start_artifact_id,
                limit,
                actor=actor,
            )
            placeholders = ",".join("?" for _ in artifact_ids)
            access_sql, access_params = self._access_clause(actor, "read")
            rows = db.execute(
                """
                SELECT a.id, a.name, a.media_type, a.created_at,
                       a.current_version_id, v.created_at AS updated_at
                FROM artifacts a
                LEFT JOIN versions v
                  ON v.id = a.current_version_id AND v.tenant_id = a.tenant_id
                WHERE a.tenant_id = ?
                  AND a.id IN ("""
                + placeholders
                + ") AND "
                + access_sql,
                (tenant_id, *artifact_ids, *access_params),
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[artifact_id] for artifact_id in artifact_ids if artifact_id in by_id]

    def search(
        self,
        tenant_id: str,
        query: str,
        limit: int = 20,
        *,
        actor: str | None = None,
        graph_root_artifact_id: str | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            if graph_root_artifact_id is not None:
                with self.connect() as db:
                    self._authorize(db, tenant_id, graph_root_artifact_id, actor, "read")
            return []
        if len(query) > MAX_QUERY_LENGTH:
            raise FolioError(f"query exceeds {MAX_QUERY_LENGTH} characters")
        cursor_timestamp: str | None = None
        cursor_artifact_id: str | None = None
        if cursor is not None:
            try:
                cursor_timestamp, cursor_artifact_id = _parse_artifact_list_cursor(cursor)
            except FolioError as exc:
                raise FolioError("invalid artifact_search cursor") from exc

        with self.connect() as db:
            component_ids: list[str] | None = None
            if graph_root_artifact_id is not None:
                # Resolving the component first intentionally authorizes the root and
                # only follows readable, same-tenant edges.  A hidden root therefore
                # fails closed instead of silently returning a global search.
                component_ids = self._component_ids(
                    db,
                    tenant_id,
                    graph_root_artifact_id,
                    MAX_GRAPH_COMPONENT_NODES,
                    actor=actor,
                )
            access_sql, access_params = self._access_clause(actor, "read")
            component_sql = ""
            component_params: tuple[str, ...] = ()
            if component_ids is not None:
                placeholders = ",".join("?" for _ in component_ids)
                component_sql = f" AND a.id IN ({placeholders})"
                component_params = tuple(component_ids)
            cursor_sql = ""
            cursor_params: tuple[str, ...] = ()
            if cursor_timestamp is not None and cursor_artifact_id is not None:
                cursor_sql = """
                      WHERE (
                          updated_at < ?
                          OR (
                              updated_at = ?
                              AND artifact_id < ?
                          )
                      )
                """
                cursor_params = (cursor_timestamp, cursor_timestamp, cursor_artifact_id)
            readable_sql = (
                """
                WITH visible_base AS (
                    SELECT a.tenant_id, a.id AS artifact_id, a.name AS artifact_name,
                           a.media_type, a.created_at,
                           a.current_version_id AS version_id,
                           v.created_at AS updated_at,
                           v.source_context
                    FROM artifacts a
                    JOIN versions v
                      ON v.id = a.current_version_id AND v.tenant_id = a.tenant_id
                    WHERE a.tenant_id = ?
                """
                + component_sql
                + " AND "
                + access_sql
                + """
                ), readable_base AS (
                    SELECT * FROM visible_base
                """
                + cursor_sql
                + """
                ), readable AS (
                    SELECT rb.artifact_id, rb.artifact_name, rb.media_type,
                           rb.created_at, rb.version_id, rb.updated_at,
                           rb.source_context,
                           (SELECT COUNT(*) FROM edges e
                            JOIN visible_base other
                              ON other.artifact_id = CASE
                                  WHEN e.source_artifact_id = rb.artifact_id
                                  THEN e.target_artifact_id
                                  ELSE e.source_artifact_id
                              END
                            WHERE e.tenant_id = rb.tenant_id
                              AND (e.source_artifact_id = rb.artifact_id
                                   OR e.target_artifact_id = rb.artifact_id)) AS graph_edges
                    FROM readable_base rb
                ), metadata_matches AS (
                    SELECT r.*, NULL AS chunk_id, NULL AS content
                    FROM readable r
                    WHERE instr(lower(r.artifact_name), lower(?)) > 0
                       OR instr(lower(r.media_type), lower(?)) > 0
                ), body_matches AS (
                    SELECT r.*, c.id AS chunk_id, c.content
                    FROM readable r
                    JOIN chunks c
                      ON c.tenant_id = ?
                     AND c.artifact_id = r.artifact_id
                     AND c.version_id = r.version_id
                    WHERE instr(lower(c.content), lower(?)) > 0
                       OR c.id IN (
                           SELECT chunk_id FROM chunk_fts
                           WHERE chunk_fts MATCH ? AND tenant_id = ?
                       )
                )
                SELECT * FROM metadata_matches
                UNION ALL
                SELECT * FROM body_matches
                ORDER BY updated_at DESC, artifact_id DESC
                LIMIT ?
                """
            )
            match_query = '"' + query.replace('"', '""') + '"'
            params = (
                tenant_id,
                *component_params,
                *access_params,
                *cursor_params,
                query,
                query,
                tenant_id,
                query,
                match_query,
                tenant_id,
                MAX_SEARCH_CANDIDATES,
            )
            try:
                rows = db.execute(readable_sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                # FTS tokenization is an optimization for legacy phrase queries;
                # arbitrary punctuation must still work as a body substring search.
                if "fts" not in str(exc).lower() and "syntax" not in str(exc).lower():
                    raise FolioError("invalid search query") from exc
                fallback_sql = readable_sql.replace(
                    "                       OR c.id IN (\n"
                    "                           SELECT chunk_id FROM chunk_fts\n"
                    "                           WHERE chunk_fts MATCH ? AND tenant_id = ?\n"
                    "                       )\n",
                    "",
                )
                fallback_params = (
                    tenant_id,
                    *component_params,
                    *access_params,
                    *cursor_params,
                    query,
                    query,
                    tenant_id,
                    query,
                    MAX_SEARCH_CANDIDATES,
                )
                rows = db.execute(fallback_sql, fallback_params).fetchall()

        by_artifact: dict[str, dict[str, Any]] = {}
        for row in rows:
            artifact_id = row["artifact_id"]
            name = row["artifact_name"]
            media_type = row["media_type"]
            body = row["content"] or ""
            match_kinds: list[str] = []
            if _search_match_offset(name, query) >= 0:
                match_kinds.append("name")
            if _search_match_offset(media_type, query) >= 0:
                match_kinds.append("media_type")
            body_offset = _search_match_offset(body, query) if body else -1
            if body_offset >= 0:
                match_kinds.append("body")
            if not match_kinds:
                continue

            item = by_artifact.get(artifact_id)
            if item is None:
                try:
                    source_context = json.loads(row["source_context"])
                except (TypeError, json.JSONDecodeError):
                    source_context = {}
                path = (
                    source_context.get("path")
                    or source_context.get("file_path")
                    or source_context.get("filename")
                    or name
                    if isinstance(source_context, dict)
                    else name
                )
                item = {
                    "artifact_id": artifact_id,
                    "version_id": row["version_id"],
                    "artifact_name": name,
                    "media_type": media_type,
                    "match_kinds": [],
                    "match_kind": "",
                    "snippet": "",
                    "chunk_id": None,
                    "score": None,
                    "path": path,
                    "graph_context": {
                        "root_artifact_id": graph_root_artifact_id,
                        "scoped": graph_root_artifact_id is not None,
                        "edge_count": row["graph_edges"],
                    },
                    "graph_edges": row["graph_edges"],
                    "graph_root_artifact_id": graph_root_artifact_id,
                    "updated_at": row["updated_at"] or row["created_at"],
                }
                by_artifact[artifact_id] = item

            had_body_match = "body" in item["match_kinds"]
            for kind in match_kinds:
                if kind not in item["match_kinds"]:
                    item["match_kinds"].append(kind)
            item["match_kind"] = (
                item["match_kinds"][0] if len(item["match_kinds"]) == 1 else "multiple"
            )
            if body_offset >= 0 and not had_body_match:
                item["snippet"] = _search_snippet(body, query)
                item["chunk_id"] = row["chunk_id"]
                item["score"] = 0.0
            elif not item["snippet"]:
                item["snippet"] = _search_snippet(
                    name if "name" in match_kinds else media_type, query
                )

        ordered = sorted(
            by_artifact.values(),
            key=lambda item: (item["updated_at"], item["artifact_id"]),
            reverse=True,
        )
        if cursor_timestamp is not None and cursor_artifact_id is not None:
            ordered = [
                item
                for item in ordered
                if item["updated_at"] < cursor_timestamp
                or (
                    item["updated_at"] == cursor_timestamp
                    and item["artifact_id"] < cursor_artifact_id
                )
            ]
        return ordered[: max(1, min(limit, 100))]

    def grep(
        self, tenant_id: str, pattern: str, limit: int = 100, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        if not pattern:
            return []
        if len(pattern) > MAX_QUERY_LENGTH:
            raise FolioError(f"pattern exceeds {MAX_QUERY_LENGTH} characters")
        with self.connect() as db:
            access_sql, access_params = self._access_clause(actor, "read")
            rows = db.execute(
                """
                SELECT c.id, c.artifact_id, c.version_id, c.ordinal,
                       c.start_offset, c.end_offset, c.content,
                       a.name AS artifact_name
                FROM chunks c
                JOIN artifacts a ON a.id = c.artifact_id
                 AND a.tenant_id = c.tenant_id
                WHERE c.tenant_id = ?
                  AND a.current_version_id = c.version_id
                  AND """
                + access_sql
                + " ORDER BY c.artifact_id, c.version_id, c.ordinal",
                (tenant_id, *access_params),
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
        *,
        actor: str | None = None,
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
                self._authorize(db, tenant_id, artifact_id, actor, "write")
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
        self,
        tenant_id: str,
        start_artifact_id: str,
        max_depth: int = 2,
        limit: int = 100,
        *,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        if max_depth < 0 or max_depth > 10:
            raise FolioError("max_depth must be between 0 and 10")
        limit = max(1, min(limit, 500))
        self.get_artifact(tenant_id, start_artifact_id, actor=actor)
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
                    SELECT e.id, e.source_artifact_id, e.target_artifact_id,
                           e.edge_type, e.metadata_json,
                           a.name AS target_artifact_name
                    FROM edges e
                    JOIN artifacts a ON a.id = e.target_artifact_id
                     AND a.tenant_id = e.tenant_id
                    WHERE e.tenant_id = ? AND e.source_artifact_id = ?
                      AND """
                    + self._access_clause(actor, "read")[0]
                    + """
                    ORDER BY e.created_at, e.id
                    """,
                    (
                        tenant_id,
                        current,
                        *self._access_clause(actor, "read")[1],
                    ),
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

    def versions(
        self,
        tenant_id: str,
        artifact_id: str,
        limit: int = 100,
        *,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        self.get_artifact(tenant_id, artifact_id, actor=actor)
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

    def share_artifact(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        actor: str,
        subject_actor_id: str,
        action: str = "read",
        reason: str = "shared artifact",
        authorization_actor: str | None | _UseCallerActor = _USE_CALLER_ACTOR,
    ) -> dict[str, Any]:
        self._validate_text("subject_actor_id", subject_actor_id, MAX_NAME_LENGTH)
        self._validate_text("reason", reason, MAX_REASON_LENGTH)
        if reason.strip().casefold() == OWNER_GRANT_REASON.casefold():
            raise FolioError("share reason is reserved")
        if action not in ACL_ACTIONS:
            raise FolioError("share action is invalid")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request_actor = (
                actor if isinstance(authorization_actor, _UseCallerActor) else authorization_actor
            )
            self._authorize(db, tenant_id, artifact_id, request_actor, "share")
            membership_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'oidc_memberships'"
            ).fetchone()
            if (
                membership_table is not None
                and db.execute(
                    """
                SELECT 1 FROM oidc_memberships
                WHERE tenant_id = ? AND actor_id = ? AND status = 'active'
                LIMIT 1
                """,
                    (tenant_id, subject_actor_id),
                ).fetchone()
                is None
            ):
                raise FolioError("share target is not an active tenant member")
            existing = db.execute(
                """
                SELECT id, tenant_id, artifact_id, subject_type, subject_id,
                       action, status, created_by, created_at, reason,
                       revoked_by, revoked_at, revocation_reason
                FROM acl_grants
                WHERE tenant_id = ? AND artifact_id = ?
                  AND subject_type = ? AND subject_id = ?
                  AND action = ? AND status = 'active'
                """,
                (
                    tenant_id,
                    artifact_id,
                    ACL_SUBJECT_TYPE,
                    subject_actor_id,
                    action,
                ),
            ).fetchone()
            if existing is not None:
                return self._grant_dict(existing)
            grant_id = new_id("grt")
            db.execute(
                """
                INSERT INTO acl_grants(
                    id, tenant_id, artifact_id, subject_type, subject_id,
                           action, status, created_by, created_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    grant_id,
                    tenant_id,
                    artifact_id,
                    ACL_SUBJECT_TYPE,
                    subject_actor_id,
                    action,
                    actor,
                    utc_now(),
                    reason,
                ),
            )
            row = db.execute(
                """
                SELECT id, tenant_id, artifact_id, subject_type, subject_id,
                       action, status, created_by, created_at, reason,
                       revoked_by, revoked_at, revocation_reason
                FROM acl_grants WHERE id = ?
                """,
                (grant_id,),
            ).fetchone()
        assert row is not None
        return self._grant_dict(row)

    def revoke_share(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        actor: str,
        grant_id: str,
        reason: str = "revoked share",
        authorization_actor: str | None | _UseCallerActor = _USE_CALLER_ACTOR,
    ) -> dict[str, Any]:
        self._validate_text("grant_id", grant_id, MAX_NAME_LENGTH)
        self._validate_text("reason", reason, MAX_REASON_LENGTH)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request_actor = (
                actor if isinstance(authorization_actor, _UseCallerActor) else authorization_actor
            )
            self._authorize(db, tenant_id, artifact_id, request_actor, "share")
            row = db.execute(
                """
                SELECT id, tenant_id, artifact_id, subject_type, subject_id,
                       action, status, created_by, created_at, reason,
                       revoked_by, revoked_at, revocation_reason
                FROM acl_grants
                WHERE id = ? AND tenant_id = ? AND artifact_id = ?
                """,
                (grant_id, tenant_id, artifact_id),
            ).fetchone()
            if row is None:
                raise FolioError("grant not found")
            if row["id"] == f"owner_{artifact_id}_{row['action']}":
                raise FolioError("artifact owner grant cannot be revoked")
            if row["status"] == "active":
                db.execute(
                    """
                    UPDATE acl_grants
                    SET status = 'revoked', revoked_by = ?, revoked_at = ?, revocation_reason = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (actor, utc_now(), reason, grant_id),
                )
            row = db.execute(
                """
                SELECT id, tenant_id, artifact_id, subject_type, subject_id,
                       action, status, created_by, created_at, reason,
                       revoked_by, revoked_at, revocation_reason
                FROM acl_grants WHERE id = ?
                """,
                (grant_id,),
            ).fetchone()
        assert row is not None
        return self._grant_dict(row)

    def artifact_acl(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        actor: str,
        authorization_actor: str | None | _UseCallerActor = _USE_CALLER_ACTOR,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            request_actor = (
                actor if isinstance(authorization_actor, _UseCallerActor) else authorization_actor
            )
            self._authorize(db, tenant_id, artifact_id, request_actor, "share")
            rows = db.execute(
                """
                SELECT id, artifact_id, subject_type, subject_id, action, status,
                       created_by, created_at, reason, revoked_by, revoked_at
                       , revocation_reason
                FROM acl_grants
                WHERE tenant_id = ? AND artifact_id = ?
                ORDER BY created_at, id
                """,
                (tenant_id, artifact_id),
            ).fetchall()
        return [self._grant_dict(row) for row in rows]

    @staticmethod
    def _external_origin(value: str, *, endpoint: bool = False) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_EXTERNAL_ENDPOINT_LENGTH
            or value != value.strip()
            or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
            or "*" in value
        ):
            raise FolioError("external MCP origin is invalid")
        try:
            parsed = urlsplit(value)
            port = parsed.port
            host = parsed.hostname
        except ValueError:
            parsed = None
            port = None
            host = None
        if parsed is None:
            raise FolioError("external MCP origin is invalid")
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (endpoint and not parsed.path)
            or (not endpoint and parsed.path)
        ):
            raise FolioError("external MCP origin is invalid")
        if host is None:
            raise FolioError("external MCP origin is invalid")
        if endpoint:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise FolioError("external MCP endpoint host is not public")
        bracketed_host = f"[{host.lower()}]" if ":" in host else host.lower()
        return f"https://{bracketed_host}{f':{port}' if port is not None else ''}"

    @staticmethod
    def _external_items(field: str, values: object) -> list[str]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            raise FolioError(f"{field} must be a list of exact strings")
        items = list(values)
        if len(items) > MAX_EXTERNAL_LIST_ITEMS:
            raise FolioError(f"{field} has too many entries")
        normalized: list[str] = []
        for value in items:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > MAX_EXTERNAL_ITEM_LENGTH
                or value != value.strip()
                or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
                or "*" in value
            ):
                raise FolioError(f"{field} must contain exact, non-wildcard strings")
            normalized.append(value)
        if len(set(normalized)) != len(normalized):
            raise FolioError(f"{field} must not contain duplicates")
        return sorted(normalized)

    @staticmethod
    def _external_credential_ref(value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_EXTERNAL_ITEM_LENGTH
            or value != value.strip()
            or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
        ):
            raise FolioError("credential_ref must be an opaque secret:// reference")
        try:
            parsed = urlsplit(value)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme != "secret"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise FolioError("credential_ref must be an opaque secret:// reference")
        return value

    @staticmethod
    def _external_json_list(value: str) -> list[str]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise FolioError("external MCP policy is invalid")
        return parsed

    @staticmethod
    def _external_policy(value: Mapping[str, Any] | None) -> str:
        if value is None:
            policy: dict[str, Any] = {}
        elif not isinstance(value, Mapping):
            raise FolioError("external MCP policy is invalid")
        else:
            policy = dict(value)
        if any(not isinstance(key, str) for key in policy) or set(policy) - _EXTERNAL_POLICY_KEYS:
            raise FolioError("external MCP policy is invalid")
        if any(not isinstance(value, Mapping) for value in policy.values()):
            raise FolioError("external MCP policy is invalid")
        try:
            encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            encoded = None
        if encoded is None:
            raise FolioError("external MCP policy is invalid")
        if len(encoded.encode()) > MAX_EXTERNAL_ARGUMENT_BYTES:
            raise FolioError("external MCP policy exceeds the allowed size")
        return encoded

    @staticmethod
    def _external_policy_dict(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if (
            not isinstance(parsed, dict)
            or any(not isinstance(key, str) for key in parsed)
            or set(parsed) - _EXTERNAL_POLICY_KEYS
        ):
            raise FolioError("external MCP policy is invalid")
        return parsed

    @staticmethod
    def _external_public(row: dict[str, Any]) -> dict[str, Any]:
        public = {key: row[key] for key in _EXTERNAL_PUBLIC_FIELDS if key in row}
        policy_value = row.get("policy_json")
        if isinstance(policy_value, str):
            try:
                policy = json.loads(policy_value)
            except (TypeError, ValueError):
                policy = {}
            if isinstance(policy, dict):
                slack = policy.get("slack")
                if isinstance(slack, dict):
                    safe_slack = {
                        key: slack[key] for key in _EXTERNAL_POLICY_PUBLIC_KEYS if key in slack
                    }
                    public["policy"] = {"slack": safe_slack}
        return public | {"credential_configured": row.get("credential_ref") is not None}

    @staticmethod
    def _write_external_audit(
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        connection_id: str,
        actor: str,
        action: str,
        outcome: str,
        tool_name: str | None = None,
        resource_uri: str | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO external_mcp_audit(
                id, tenant_id, connection_id, actor_id, action, outcome,
                tool_name, resource_uri, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("extaudit"),
                tenant_id,
                connection_id,
                actor,
                action,
                outcome,
                tool_name,
                resource_uri,
                utc_now(),
            ),
        )

    def _external_row(
        self, db: sqlite3.Connection, tenant_id: str, connection_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            """
            SELECT id, tenant_id, name, endpoint, origin, transport,
                   approved_tools, approved_resources, allowed_origins,
                   policy_json, credential_ref, status, health_status, last_health_at,
                   created_by, created_at, revoked_by, revoked_at, policy_version
            FROM external_mcp_connections
            WHERE id = ? AND tenant_id = ?
            """,
            (connection_id, tenant_id),
        ).fetchone()
        if row is None:
            raise FolioError("external MCP connection not found")
        values = dict(row)
        for field in ("approved_tools", "approved_resources", "allowed_origins"):
            values[field] = self._external_json_list(values[field])
        values["policy"] = self._external_policy_dict(values["policy_json"])
        return values

    def register_external_connection(
        self,
        *,
        tenant_id: str,
        actor: str,
        name: str,
        endpoint: str,
        approved_tools: list[str],
        approved_resources: list[str],
        allowed_origins: list[str],
        policy: Mapping[str, Any] | None = None,
        credential_ref: str | None = None,
        reason: str = "approved external MCP connection",
    ) -> dict[str, Any]:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        self._validate_text("name", name, MAX_EXTERNAL_CONNECTION_NAME_LENGTH)
        self._validate_text("reason", reason, MAX_REASON_LENGTH)
        endpoint_origin = self._external_origin(endpoint, endpoint=True)
        tools = self._external_items("approved_tools", approved_tools)
        resources = self._external_items("approved_resources", approved_resources)
        origins = [self._external_origin(value) for value in allowed_origins]
        if not tools and not resources:
            raise FolioError("external MCP connection needs an explicit tool or resource allowlist")
        if endpoint_origin not in origins:
            raise FolioError("external MCP endpoint origin must be explicitly allowed")
        policy_json = self._external_policy(policy)
        credential = self._external_credential_ref(credential_ref)
        connection_id = new_id("extconn")
        now = utc_now()
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._ensure_tenant(db, tenant_id)
                db.execute(
                    """
                    INSERT INTO external_mcp_connections(
                        id, tenant_id, name, endpoint, origin, transport,
                        approved_tools, approved_resources, allowed_origins,
                        policy_json, credential_ref, status, health_status, last_health_at,
                        created_by, created_at, revoked_by, revoked_at, policy_version
                    ) VALUES (?, ?, ?, ?, ?, 'streamable_http', ?, ?, ?, ?, ?, 'active',
                              'unknown', NULL, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        connection_id,
                        tenant_id,
                        name,
                        endpoint,
                        endpoint_origin,
                        json.dumps(tools, separators=(",", ":")),
                        json.dumps(resources, separators=(",", ":")),
                        json.dumps(sorted(origins), separators=(",", ":")),
                        policy_json,
                        credential,
                        actor,
                        now,
                        EXTERNAL_MCP_POLICY_VERSION,
                    ),
                )
                audit_failed = False
                try:
                    self._write_external_audit(
                        db,
                        tenant_id=tenant_id,
                        connection_id=connection_id,
                        actor=actor,
                        action="register",
                        outcome="allowed",
                    )
                except Exception:
                    audit_failed = True
                if audit_failed:
                    raise FolioError("external MCP registration failed") from None
        except sqlite3.IntegrityError:
            raise FolioError("external MCP connection name already exists") from None
        return self.external_connection_status(tenant_id, connection_id, actor=actor)

    def list_external_connections(
        self, tenant_id: str, *, actor: str, limit: int = MAX_EXTERNAL_LIST_ITEMS
    ) -> list[dict[str, Any]]:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, tenant_id, name, endpoint, origin, transport,
                       approved_tools, approved_resources, allowed_origins,
                       policy_json, credential_ref, status, health_status, last_health_at,
                       created_by, created_at, revoked_by, revoked_at, policy_version
                FROM external_mcp_connections
                WHERE tenant_id = ?
                ORDER BY created_at, id
                LIMIT ?
                """,
                (tenant_id, max(1, min(limit, MAX_EXTERNAL_LIST_ITEMS))),
            ).fetchall()
            values = []
            for row in rows:
                item = dict(row)
                for field in ("approved_tools", "approved_resources", "allowed_origins"):
                    item[field] = self._external_json_list(item[field])
                values.append(self._external_public(item))
        return values

    def external_connection_for_broker(
        self, tenant_id: str, connection_id: str, *, actor: str
    ) -> dict[str, Any]:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        with self.connect() as db:
            return self._external_row(db, tenant_id, connection_id)

    def external_connection_status(
        self, tenant_id: str, connection_id: str, *, actor: str
    ) -> dict[str, Any]:
        connection = self.external_connection_for_broker(tenant_id, connection_id, actor=actor)
        return self._external_public(connection)

    @contextmanager
    def external_call_fence(
        self, tenant_id: str, connection_id: str, *, actor: str
    ) -> Iterator[dict[str, Any]]:
        """Hold the registry write fence through upstream transport start."""

        del actor
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            connection = self._external_row(db, tenant_id, connection_id)
            if connection["status"] != "active":
                raise FolioError("external MCP connection is revoked")
            yield connection
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_external_health(
        self, tenant_id: str, connection_id: str, *, actor: str, status: str
    ) -> None:
        if status not in {"healthy", "unhealthy"}:
            raise FolioError("external MCP health status is invalid")
        with self.connect() as db:
            connection = self._external_row(db, tenant_id, connection_id)
            if connection["status"] == "revoked":
                raise FolioError("external MCP connection is revoked")
            db.execute(
                "UPDATE external_mcp_connections SET health_status = ?, last_health_at = ? "
                "WHERE id = ? AND tenant_id = ?",
                (status, utc_now(), connection_id, tenant_id),
            )
            self._write_external_audit(
                db,
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="health_check",
                outcome=status,
            )

    def revoke_external_connection(
        self, tenant_id: str, connection_id: str, *, actor: str, reason: str
    ) -> dict[str, Any]:
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        self._validate_text("reason", reason, MAX_REASON_LENGTH)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            connection = self._external_row(db, tenant_id, connection_id)
            outcome = "already_revoked" if connection["status"] == "revoked" else "revoked"
            if connection["status"] != "revoked":
                db.execute(
                    "UPDATE external_mcp_connections SET status = 'revoked', revoked_by = ?, "
                    "revoked_at = ? WHERE id = ? AND tenant_id = ?",
                    (actor, utc_now(), connection_id, tenant_id),
                )
            self._write_external_audit(
                db,
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="revoke",
                outcome=outcome,
            )
            connection = self._external_row(db, tenant_id, connection_id)
        return self._external_public(connection)

    def _record_external_denial(
        self,
        *,
        tenant_id: str,
        connection_id: str,
        actor: str,
        action: str,
        tool_name: str | None = None,
        resource_uri: str | None = None,
    ) -> None:
        try:
            with self.connect() as db:
                tenant = db.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                if tenant is not None:
                    self._write_external_audit(
                        db,
                        tenant_id=tenant_id,
                        connection_id=connection_id,
                        actor=actor,
                        action=action,
                        outcome="denied",
                        tool_name=tool_name,
                        resource_uri=resource_uri,
                    )
        except Exception:
            return

    def authorize_external_tool(
        self,
        tenant_id: str,
        connection_id: str,
        tool_name: str,
        *,
        actor: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            connection = self.external_connection_for_broker(tenant_id, connection_id, actor=actor)
        except FolioError:
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="tool_call",
                tool_name=tool_name,
            )
            raise
        if connection["status"] != "active":
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="tool_call",
                tool_name=tool_name,
            )
            raise FolioError("external MCP connection is revoked")
        if tool_name not in connection["approved_tools"]:
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="tool_call",
                tool_name=tool_name,
            )
            raise FolioError("external MCP tool is not approved")
        try:
            encoded = json.dumps(dict(arguments), separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise FolioError("external MCP tool arguments are invalid") from None
        if len(encoded) > MAX_EXTERNAL_ARGUMENT_BYTES:
            raise FolioError("external MCP tool arguments exceed the allowed size")
        return connection

    def authorize_external_resource(
        self, tenant_id: str, connection_id: str, resource_uri: str, *, actor: str
    ) -> dict[str, Any]:
        try:
            connection = self.external_connection_for_broker(tenant_id, connection_id, actor=actor)
        except FolioError:
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="resource_read",
                resource_uri=resource_uri,
            )
            raise
        if connection["status"] != "active":
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="resource_read",
                resource_uri=resource_uri,
            )
            raise FolioError("external MCP connection is revoked")
        if resource_uri not in connection["approved_resources"]:
            self._record_external_denial(
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action="resource_read",
                resource_uri=resource_uri,
            )
            raise FolioError("external MCP resource is not approved")
        return connection

    def record_external_call(
        self,
        tenant_id: str,
        connection_id: str,
        *,
        actor: str,
        action: str,
        outcome: str,
        tool_name: str | None = None,
        resource_uri: str | None = None,
    ) -> None:
        with self.connect() as db:
            self._write_external_audit(
                db,
                tenant_id=tenant_id,
                connection_id=connection_id,
                actor=actor,
                action=action,
                outcome=outcome,
                tool_name=tool_name,
                resource_uri=resource_uri,
            )

    def external_mcp_audit(
        self,
        tenant_id: str,
        *,
        actor: str,
        connection_id: str | None = None,
        limit: int = MAX_EXTERNAL_LIST_ITEMS,
    ) -> list[dict[str, Any]]:
        self._validate_text("tenant_id", tenant_id, MAX_NAME_LENGTH)
        self._validate_text("actor", actor, MAX_NAME_LENGTH)
        with self.connect() as db:
            if connection_id is None:
                rows = db.execute(
                    "SELECT id, connection_id, actor_id, action, outcome, tool_name, "
                    "resource_uri, created_at FROM external_mcp_audit "
                    "WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (tenant_id, max(1, min(limit, MAX_EXTERNAL_LIST_ITEMS))),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id, connection_id, actor_id, action, outcome, tool_name, "
                    "resource_uri, created_at FROM external_mcp_audit "
                    "WHERE tenant_id = ? AND connection_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (tenant_id, connection_id, max(1, min(limit, MAX_EXTERNAL_LIST_ITEMS))),
                ).fetchall()
        return [dict(row) for row in rows]

    def readiness(self) -> dict[str, Any]:
        """Check durable dependencies without exposing tenant data."""
        database_ready = False
        migration_ready = False
        acl_ready = False
        external_mcp_ready = False
        try:
            with self.connect() as db:
                objects = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE name IS NOT NULL")
                }
                indexes = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
                    )
                }
                columns_ready = all(
                    required <= {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                    for table, required in REQUIRED_SCHEMA_COLUMNS.items()
                )
                quick_check = db.execute("PRAGMA quick_check").fetchone()
                database_ready = quick_check is not None and quick_check[0] == "ok"
                migration_ready = (
                    REQUIRED_SCHEMA_OBJECTS <= objects
                    and REQUIRED_SCHEMA_INDEXES <= indexes
                    and columns_ready
                )
                acl_ready = (
                    "acl_grants" in objects
                    and "acl_grants_lookup_idx" in indexes
                    and "acl_grants_active_idx" in indexes
                    and REQUIRED_SCHEMA_COLUMNS["acl_grants"]
                    <= {row[1] for row in db.execute("PRAGMA table_info(acl_grants)")}
                )
                external_mcp_ready = {
                    "external_mcp_connections",
                    "external_mcp_audit",
                } <= objects and {
                    "external_mcp_connections_name_idx",
                    "external_mcp_connections_tenant_idx",
                    "external_mcp_audit_lookup_idx",
                } <= indexes
        except (OSError, sqlite3.Error):
            pass

        database_parent = self.db_path.parent
        if self.read_only:
            database_access = (
                self.db_path.is_file()
                and os.access(self.db_path, os.R_OK)
                and os.access(database_parent, os.R_OK | os.X_OK)
            )
        else:
            database_access = (
                self.db_path.is_file()
                and os.access(self.db_path, os.R_OK | os.W_OK)
                and os.access(database_parent, os.R_OK | os.W_OK | os.X_OK)
            )
        database_ready = database_ready and database_access

        blob_access = os.R_OK | os.X_OK if self.read_only else os.R_OK | os.W_OK | os.X_OK
        blob_ready = self.blob_root.is_dir() and os.access(self.blob_root, blob_access)
        ready = (
            database_ready and blob_ready and migration_ready and acl_ready and external_mcp_ready
        )
        return {
            "status": "ok" if ready else "not_ready",
            "ready": ready,
            "dependencies": {
                "database": {"ready": database_ready},
                "blob": {"ready": blob_ready},
                "migration": {"ready": migration_ready},
                "acl": {"ready": acl_ready},
                "external_mcp": {"ready": external_mcp_ready},
            },
        }

    def health(self) -> dict[str, Any]:
        """Backward-compatible durable-state response for direct callers."""
        return self.readiness()
