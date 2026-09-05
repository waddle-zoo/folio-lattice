from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .service import FolioError, FolioLattice

MAX_ID_LENGTH = 255
MAX_BASE64_LENGTH = 12 * 1024 * 1024


def _tool_errors[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (binascii.Error, ValueError, FolioError) as exc:
        raise ToolError(str(exc)) from exc


def _decode(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("content_base64 is not valid base64") from exc


def build_mcp_server(
    service: FolioLattice,
    *,
    tenant_id: str,
    actor: str,
) -> MCPServer:
    """Bind one configured namespace and actor to the public MCP surface."""
    server = MCPServer(
        "folio-lattice",
        version="0.1.0",
        instructions=(
            "Provider-neutral immutable artifact storage and graph traversal. "
            "Tenant and actor identity are derived by the server."
        ),
    )

    @server.tool(description="Create an artifact and its first immutable version.")
    def artifact_create(
        name: Annotated[str, Field(min_length=1, max_length=255)],
        content_base64: Annotated[str, Field(max_length=MAX_BASE64_LENGTH)],
        media_type: Annotated[str | None, Field(max_length=255)] = None,
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = "initial artifact",
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _tool_errors(
            lambda: service.create_artifact(
                tenant_id=tenant_id,
                name=name,
                data=_decode(content_base64),
                media_type=media_type,
                actor=actor,
                reason=reason,
                source_context=source_context or {},
            )
        )

    @server.tool(description="Write an immutable version after an optimistic parent check.")
    def artifact_write(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        parent_version_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        content_base64: Annotated[str, Field(max_length=MAX_BASE64_LENGTH)],
        media_type: Annotated[str | None, Field(max_length=255)] = None,
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = "update",
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            artifact = service.get_artifact(tenant_id, artifact_id)
            return service.write_version(
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                data=_decode(content_base64),
                media_type=media_type or artifact["media_type"],
                actor=actor,
                reason=reason,
                source_context=source_context or {},
                parent_version_id=parent_version_id,
            )

        return _tool_errors(operation)

    @server.tool(description="Read a complete immutable artifact version.")
    def artifact_read(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        version_id: Annotated[str | None, Field(max_length=MAX_ID_LENGTH)] = None,
    ) -> dict[str, Any]:
        return _tool_errors(lambda: service.read_artifact(tenant_id, artifact_id, version_id))

    @server.tool(description="Read one bounded text chunk with explicit offset units.")
    def artifact_read_chunk(
        chunk_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
    ) -> dict[str, Any]:
        return _tool_errors(lambda: service.read_chunk(tenant_id, chunk_id))

    @server.tool(description="Search indexed text with bounded SQLite FTS5 syntax.")
    def artifact_search(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        return _tool_errors(lambda: service.search(tenant_id, query, limit))

    @server.tool(description="Find a bounded literal string within indexed text chunks.")
    def artifact_grep(
        pattern: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return _tool_errors(lambda: service.grep(tenant_id, pattern, limit))

    @server.tool(description="Create an immutable, idempotent typed edge between artifacts.")
    def graph_link(
        source_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        target_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        edge_type: Annotated[str, Field(min_length=1, max_length=100)],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _tool_errors(
            lambda: service.link(
                tenant_id,
                source_artifact_id,
                target_artifact_id,
                edge_type,
                metadata or {},
            )
        )

    @server.tool(description="Traverse outgoing graph edges with explicit bounds.")
    def graph_traverse(
        start_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        max_depth: Annotated[int, Field(ge=0, le=10)] = 2,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return _tool_errors(
            lambda: service.traverse(tenant_id, start_artifact_id, max_depth, limit)
        )

    @server.tool(description="List an artifact's immutable version history.")
    def artifact_versions(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return _tool_errors(lambda: service.versions(tenant_id, artifact_id, limit))

    return server
