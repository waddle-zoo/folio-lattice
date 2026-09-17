from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Protocol

from mcp import Client

PUBLIC_TOOLS = frozenset(
    {
        "artifact_create",
        "artifact_write",
        "artifact_list",
        "artifact_read",
        "artifact_read_chunk",
        "artifact_search",
        "artifact_grep",
        "graph_link",
        "graph_traverse",
        "graph_component",
        "artifact_versions",
        "artifact_share",
        "artifact_revoke",
        "artifact_acl",
    }
)

ADMIN_TOOLS = frozenset(
    {
        "external_mcp_connection_register",
        "external_mcp_connection_list",
        "external_mcp_connection_status",
        "external_mcp_connection_revoke",
        "external_mcp_audit",
    }
)


class PublicMcpError(Exception):
    """Bounded, client-visible failure from the public MCP boundary."""


class ToolCaller(Protocol):
    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any: ...

    async def ready(self) -> bool: ...


class HttpMcpClient:
    """Small official-SDK adapter for one configured Streamable HTTP server."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 5,
        max_result_bytes: int = 13 * 1024 * 1024,
    ):
        if timeout_seconds <= 0 or max_result_bytes < 1:
            raise ValueError("MCP client limits must be positive")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_result_bytes = max_result_bytes

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        if tool not in PUBLIC_TOOLS:
            raise PublicMcpError("tool is not part of the Folio MCP contract")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(
                    self.endpoint,
                    raise_exceptions=False,
                    read_timeout_seconds=self.timeout_seconds,
                ) as client:
                    result = await client.call_tool(tool, dict(arguments))
        except TimeoutError as exc:
            raise PublicMcpError("MCP call timed out") from exc
        except PublicMcpError:
            raise
        except Exception as exc:
            raise PublicMcpError("MCP service unavailable") from exc

        if result.is_error:
            messages = [item.text for item in result.content if item.type == "text"]
            message = " ".join(messages).strip() or "MCP tool failed"
            raise PublicMcpError(message[:500])
        value = result.structured_content
        if value is None:
            raise PublicMcpError("MCP tool returned no structured result")
        try:
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise PublicMcpError("MCP tool returned an invalid structured result") from exc
        if len(encoded) > self.max_result_bytes:
            raise PublicMcpError("MCP result exceeds the allowed size")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(
                    self.endpoint,
                    raise_exceptions=False,
                    read_timeout_seconds=self.timeout_seconds,
                ) as client:
                    tools = await client.list_tools()
            return "artifact_read" in {tool.name for tool in tools.tools}
        except Exception:
            return False


class AdminMcpClient:
    """Call tenant-admin tools on the authenticated server context."""

    def __init__(self, server: Any, *, timeout_seconds: float = 5):
        if timeout_seconds <= 0:
            raise ValueError("MCP client timeout must be positive")
        self.server = server
        self.timeout_seconds = timeout_seconds

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        if tool not in ADMIN_TOOLS:
            raise PublicMcpError("tool is not part of the admin MCP contract")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(self.server, raise_exceptions=False) as client:
                    result = await client.call_tool(tool, dict(arguments))
        except TimeoutError as exc:
            raise PublicMcpError("admin MCP call timed out") from exc
        except PublicMcpError:
            raise
        except Exception as exc:
            raise PublicMcpError("admin MCP service unavailable") from exc
        if result.is_error:
            messages = [item.text for item in result.content if item.type == "text"]
            raise PublicMcpError(" ".join(messages).strip()[:500] or "Admin MCP tool failed")
        value = result.structured_content
        if value is None:
            raise PublicMcpError("admin MCP tool returned no structured result")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value

    async def ready(self) -> bool:
        return True
