from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .auth import get_request_principal
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
    tenant_id: str | None = None,
    actor: str | None = None,
) -> MCPServer:
    """Bind local identity or resolve one authenticated principal per request."""

    fixed_local_identity = tenant_id is not None and actor is not None

    def identity() -> tuple[str, str]:
        principal = get_request_principal()
        if principal is not None:
            return principal.tenant_id, principal.actor_id
        if tenant_id is None or actor is None:
            raise FolioError("authentication required")
        return tenant_id, actor

    def policy_actor(request_actor: str) -> str | None:
        return None if fixed_local_identity else request_actor

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
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.create_artifact(
                tenant_id=request_tenant,
                name=name,
                data=_decode(content_base64),
                media_type=media_type,
                actor=request_actor,
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
            request_tenant, request_actor = identity()
            artifact = service.get_artifact(
                request_tenant,
                artifact_id,
                actor=policy_actor(request_actor),
                action="write",
            )
            return service.write_version(
                tenant_id=request_tenant,
                artifact_id=artifact_id,
                data=_decode(content_base64),
                media_type=media_type or artifact["media_type"],
                actor=request_actor,
                reason=reason,
                source_context=source_context or {},
                parent_version_id=parent_version_id,
                authorization_actor=policy_actor(request_actor),
            )

        return _tool_errors(operation)

    @server.tool(description="List recent artifacts available to the current actor.")
    def artifact_list(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.list_artifacts(request_tenant, limit, actor=policy_actor(request_actor))
        )

    @server.tool(description="Read a complete immutable artifact version.")
    def artifact_read(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        version_id: Annotated[str | None, Field(max_length=MAX_ID_LENGTH)] = None,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.read_artifact(
                request_tenant,
                artifact_id,
                version_id,
                actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="Read one bounded text chunk with explicit offset units.")
    def artifact_read_chunk(
        chunk_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.read_chunk(request_tenant, chunk_id, actor=policy_actor(request_actor))
        )

    @server.tool(description="Search indexed text for a bounded natural-language phrase.")
    def artifact_search(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.search(request_tenant, query, limit, actor=policy_actor(request_actor))
        )

    @server.tool(description="Find a bounded literal string within indexed text chunks.")
    def artifact_grep(
        pattern: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.grep(request_tenant, pattern, limit, actor=policy_actor(request_actor))
        )

    @server.tool(description="Create an immutable, idempotent typed edge between artifacts.")
    def graph_link(
        source_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        target_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        edge_type: Annotated[str, Field(min_length=1, max_length=100)],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.link(
                request_tenant,
                source_artifact_id,
                target_artifact_id,
                edge_type,
                metadata or {},
                actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="Traverse outgoing graph edges with explicit bounds.")
    def graph_traverse(
        start_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        max_depth: Annotated[int, Field(ge=0, le=10)] = 2,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.traverse(
                request_tenant,
                start_artifact_id,
                max_depth,
                limit,
                actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="List an artifact's immutable version history.")
    def artifact_versions(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.versions(
                request_tenant, artifact_id, limit, actor=policy_actor(request_actor)
            )
        )

    @server.tool(description="Grant an actor bounded access to an artifact.")
    def artifact_share(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        subject_actor_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        action: Annotated[str, Field(pattern="^(read|write|share)$")] = "read",
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = "shared artifact",
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.share_artifact(
                request_tenant,
                artifact_id,
                actor=request_actor,
                subject_actor_id=subject_actor_id,
                action=action,
                reason=reason,
                authorization_actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="Revoke an existing artifact grant immediately.")
    def artifact_revoke(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        grant_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = "revoked share",
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.revoke_share(
                request_tenant,
                artifact_id,
                actor=request_actor,
                grant_id=grant_id,
                reason=reason,
                authorization_actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="List an artifact's server-owned access grants.")
    def artifact_acl(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity()
        return _tool_errors(
            lambda: service.artifact_acl(
                request_tenant,
                artifact_id,
                actor=request_actor,
                authorization_actor=policy_actor(request_actor),
            )
        )

    return server
