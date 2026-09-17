from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .auth import get_request_principal
from .external_mcp import ExternalMcpBroker
from .service import FolioError, FolioLattice

MAX_ID_LENGTH = 255
MAX_BASE64_LENGTH = 12 * 1024 * 1024
MAX_EXTERNAL_LIST_ITEMS = 128
EXTERNAL_MCP_ADMIN_SCOPE = "tenant:admin"

TOOL_SCOPES = {
    "artifact_create": "artifact:write",
    "artifact_write": "artifact:write",
    "artifact_list": "artifact:read",
    "artifact_read": "artifact:read",
    "artifact_read_chunk": "artifact:read",
    "artifact_search": "artifact:search",
    "artifact_grep": "artifact:search",
    "graph_link": "graph:write",
    "graph_traverse": "graph:read",
    "graph_component": "graph:read",
    "artifact_versions": "artifact:read",
    "artifact_share": "artifact:share",
    "artifact_revoke": "artifact:share",
    "artifact_acl": "artifact:share",
    "external_mcp_connection_register": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_connection_list": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_connection_status": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_connection_revoke": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_audit": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_tool_call": EXTERNAL_MCP_ADMIN_SCOPE,
    "external_mcp_resource_read": EXTERNAL_MCP_ADMIN_SCOPE,
    "audit_export": EXTERNAL_MCP_ADMIN_SCOPE,
}


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
    external_broker: ExternalMcpBroker | None = None,
    external_rate_limits: Mapping[str, int] | None = None,
    external_rate_window_seconds: float = 60.0,
    external_concurrency_limit: int = 32,
    external_concurrency_per_key: int = 4,
) -> MCPServer:
    """Bind local identity or resolve one authenticated principal per request."""

    fixed_local_identity = tenant_id is not None and actor is not None

    def identity(*required_scopes: str) -> tuple[str, str]:
        principal = get_request_principal()
        if principal is not None:
            if any(scope not in principal.scopes for scope in required_scopes):
                raise ToolError("operation not permitted")
            return principal.tenant_id, principal.actor_id
        if tenant_id is None or actor is None:
            raise FolioError("authentication required")
        return tenant_id, actor

    def policy_actor(request_actor: str) -> str | None:
        # A verified request principal always wins, even when a caller embeds
        # this server with legacy fixed defaults.  Never turn an authenticated
        # actor into the local-policy bypass path.
        if get_request_principal() is not None:
            return request_actor
        return None if fixed_local_identity else request_actor

    broker = external_broker or ExternalMcpBroker(
        service,
        rate_limits=external_rate_limits,
        rate_window_seconds=external_rate_window_seconds,
        concurrency_limit=external_concurrency_limit,
        concurrency_per_key=external_concurrency_per_key,
    )

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
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_create"])
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
            request_tenant, request_actor = identity(TOOL_SCOPES["artifact_write"])
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

    @server.tool(
        description=(
            "List recent artifacts available to the current actor, optionally filtered by "
            "exact filename and media type. Continue after a prior page with its final "
            "item's '<updated_at>|<id>' cursor."
        )
    )
    def artifact_list(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        name: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
        media_type: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
        cursor: Annotated[str | None, Field(max_length=512)] = None,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_list"])
        return _tool_errors(
            lambda: service.list_artifacts(
                request_tenant,
                limit,
                actor=policy_actor(request_actor),
                name=name,
                media_type=media_type,
                cursor=cursor,
            )
        )

    @server.tool(description="Read a complete immutable artifact version.")
    def artifact_read(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        version_id: Annotated[str | None, Field(max_length=MAX_ID_LENGTH)] = None,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_read"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_read_chunk"])
        return _tool_errors(
            lambda: service.read_chunk(request_tenant, chunk_id, actor=policy_actor(request_actor))
        )

    @server.tool(
        description=(
            "Discover readable artifacts with one case-insensitive substring search across "
            "artifact name, media type, and current body. Results are deduplicated by stable "
            "artifact_id and include match_kinds, snippet, path, and graph context. "
            "Use graph_root_artifact_id to search only its readable connected component; "
            "continue a page with the returned updated_at|artifact_id cursor."
        )
    )
    def artifact_search(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        graph_root_artifact_id: Annotated[
            str | None, Field(min_length=1, max_length=MAX_ID_LENGTH)
        ] = None,
        cursor: Annotated[str | None, Field(max_length=512)] = None,
    ) -> list[dict[str, Any]]:
        required_scopes = [TOOL_SCOPES["artifact_search"]]
        if graph_root_artifact_id is not None:
            required_scopes.append(TOOL_SCOPES["graph_component"])
        request_tenant, request_actor = identity(*required_scopes)
        return _tool_errors(
            lambda: service.search(
                request_tenant,
                query,
                limit,
                actor=policy_actor(request_actor),
                graph_root_artifact_id=graph_root_artifact_id,
                cursor=cursor,
            )
        )

    @server.tool(description="Find a bounded literal string within indexed text chunks.")
    def artifact_grep(
        pattern: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_grep"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["graph_link"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["graph_traverse"])
        return _tool_errors(
            lambda: service.traverse(
                request_tenant,
                start_artifact_id,
                max_depth,
                limit,
                actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="List readable artifacts in a bounded undirected graph component.")
    def graph_component(
        start_artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["graph_component"])
        return _tool_errors(
            lambda: service.graph_component(
                request_tenant,
                start_artifact_id,
                limit,
                actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="List an artifact's immutable version history.")
    def artifact_versions(
        artifact_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_versions"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_share"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_revoke"])
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
        request_tenant, request_actor = identity(TOOL_SCOPES["artifact_acl"])
        return _tool_errors(
            lambda: service.artifact_acl(
                request_tenant,
                artifact_id,
                actor=request_actor,
                authorization_actor=policy_actor(request_actor),
            )
        )

    @server.tool(description="Register one tenant-admin-approved external MCP connection.")
    def external_mcp_connection_register(
        name: Annotated[str, Field(min_length=1, max_length=255)],
        endpoint: Annotated[str, Field(min_length=1, max_length=4096)],
        approved_tools: list[str],
        approved_resources: list[str],
        allowed_origins: list[str],
        credential_ref: Annotated[str | None, Field(max_length=2048)] = None,
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = (
            "approved external MCP connection"
        ),
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_connection_register"])
        return _tool_errors(
            lambda: broker.register(
                tenant_id=request_tenant,
                actor=request_actor,
                name=name,
                endpoint=endpoint,
                approved_tools=approved_tools,
                approved_resources=approved_resources,
                allowed_origins=allowed_origins,
                credential_ref=credential_ref,
                reason=reason,
            )
        )

    @server.tool(description="List tenant-scoped external MCP connection records.")
    def external_mcp_connection_list(
        limit: Annotated[int, Field(ge=1, le=MAX_EXTERNAL_LIST_ITEMS)] = 128,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_connection_list"])
        return _tool_errors(
            lambda: broker.list_connections(
                tenant_id=request_tenant, actor=request_actor, limit=limit
            )
        )

    @server.tool(description="Read or probe one tenant-scoped external MCP connection status.")
    def external_mcp_connection_status(
        connection_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        probe: bool = False,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_connection_status"])
        return _tool_errors(
            lambda: broker.health(
                tenant_id=request_tenant,
                actor=request_actor,
                connection_id=connection_id,
                probe=probe,
            )
        )

    @server.tool(description="Revoke one external MCP connection immediately.")
    def external_mcp_connection_revoke(
        connection_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        reason: Annotated[str, Field(min_length=1, max_length=2_000)] = "revoked connection",
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_connection_revoke"])
        return _tool_errors(
            lambda: broker.revoke(
                tenant_id=request_tenant,
                actor=request_actor,
                connection_id=connection_id,
                reason=reason,
            )
        )

    @server.tool(description="List secret-free audit decisions for external MCP connections.")
    def external_mcp_audit(
        connection_id: Annotated[str | None, Field(max_length=MAX_ID_LENGTH)] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_EXTERNAL_LIST_ITEMS)] = 128,
    ) -> list[dict[str, Any]]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_audit"])
        return _tool_errors(
            lambda: broker.audit(
                tenant_id=request_tenant,
                actor=request_actor,
                connection_id=connection_id,
                limit=limit,
            )
        )

    @server.tool(
        description=(
            "Export bounded, tenant-scoped security audit events as an integrity-checkable "
            "audit-v1 result. Events contain opaque IDs and reason codes only; content, "
            "credentials, arguments, and raw URLs are excluded. Continue with next_cursor."
        )
    )
    def audit_export(
        from_time: Annotated[str | None, Field(max_length=64)] = None,
        to_time: Annotated[str | None, Field(max_length=64)] = None,
        limit: Annotated[int, Field(ge=1, le=10_000)] = 10_000,
        cursor: Annotated[str | None, Field(max_length=512)] = None,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["audit_export"])
        return _tool_errors(
            lambda: service.export_audit_events(
                request_tenant,
                actor=request_actor,
                from_time=from_time,
                to_time=to_time,
                limit=limit,
                cursor=cursor,
            )
        )

    @server.tool(description="Call one exact tool on one approved external MCP connection.")
    def external_mcp_tool_call(
        connection_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        tool_name: Annotated[str, Field(min_length=1, max_length=2_048)],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_tool_call"])
        return _tool_errors(
            lambda: {
                "result": broker.call_tool(
                    tenant_id=request_tenant,
                    actor=request_actor,
                    connection_id=connection_id,
                    tool_name=tool_name,
                    arguments=arguments or {},
                )
            }
        )

    @server.tool(description="Read one exact resource on one approved external MCP connection.")
    def external_mcp_resource_read(
        connection_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH)],
        resource_uri: Annotated[str, Field(min_length=1, max_length=2_048)],
    ) -> dict[str, Any]:
        request_tenant, request_actor = identity(TOOL_SCOPES["external_mcp_resource_read"])
        return _tool_errors(
            lambda: {
                "result": broker.read_resource(
                    tenant_id=request_tenant,
                    actor=request_actor,
                    connection_id=connection_id,
                    resource_uri=resource_uri,
                )
            }
        )

    return server
