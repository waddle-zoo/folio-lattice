from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from .public_mcp import PublicMcpError, ToolCaller
from .sandbox import CapabilityBoundary

BRIDGE_ATTACHMENT = "folio-lattice"
BRIDGE_TOOLS = frozenset({"artifact_read", "artifact_search", "graph_traverse"})
MAX_BRIDGE_BODY_BYTES = 64 * 1024
MAX_BRIDGE_ARGUMENT_BYTES = 32 * 1024
MAX_BRIDGE_RESULT_BYTES = 1024 * 1024
MAX_BRIDGE_REQUEST_ID = 100
MAX_ID_LENGTH = 255


class BridgeRequestError(Exception):
    """A safe, categorized bridge denial or validation error."""

    def __init__(self, message: str, *, reason: str = "invalid_request"):
        super().__init__(message)
        self.reason = reason


def validate_bridge_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeRequestError("bridge request must be a JSON object")
    required = {"request_id", "artifact_id", "attachment", "tool", "arguments"}
    if set(value) != required:
        raise BridgeRequestError("bridge request has invalid fields")
    request_id = _bounded_string(value["request_id"], "request_id", MAX_BRIDGE_REQUEST_ID)
    artifact_id = _bounded_string(value["artifact_id"], "artifact_id", MAX_ID_LENGTH)
    attachment = _bounded_string(value["attachment"], "attachment", 100)
    tool = _bounded_string(value["tool"], "tool", 128)
    arguments = value["arguments"]
    if not isinstance(arguments, dict):
        raise BridgeRequestError("arguments must be a JSON object")
    try:
        encoded = json.dumps(arguments, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise BridgeRequestError("arguments must be valid JSON") from exc
    if len(encoded) > MAX_BRIDGE_ARGUMENT_BYTES:
        raise BridgeRequestError("bridge arguments are too large", reason="oversized_arguments")
    return {
        "request_id": request_id,
        "artifact_id": artifact_id,
        "attachment": attachment,
        "tool": tool,
        "arguments": _validate_tool_arguments(tool, arguments),
    }


def _bounded_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise BridgeRequestError(f"{field} must be a non-empty bounded string")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BridgeRequestError(f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_tool_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool == "artifact_read":
        allowed = {"artifact_id", "version_id"}
        if set(arguments) - allowed or "artifact_id" not in arguments:
            raise BridgeRequestError("artifact_read arguments have invalid fields")
        result: dict[str, Any] = {
            "artifact_id": _bounded_string(arguments["artifact_id"], "artifact_id", MAX_ID_LENGTH)
        }
        if "version_id" in arguments:
            version_id = arguments["version_id"]
            if version_id is not None:
                result["version_id"] = _bounded_string(version_id, "version_id", MAX_ID_LENGTH)
        return result
    if tool == "artifact_search":
        allowed = {"query", "limit"}
        if set(arguments) - allowed or "query" not in arguments:
            raise BridgeRequestError("artifact_search arguments have invalid fields")
        result = {"query": _bounded_string(arguments["query"], "query", 500)}
        if "limit" in arguments:
            result["limit"] = _integer(arguments["limit"], "limit", 1, 100)
        return result
    if tool == "graph_traverse":
        allowed = {"start_artifact_id", "max_depth", "limit"}
        if set(arguments) - allowed or "start_artifact_id" not in arguments:
            raise BridgeRequestError("graph_traverse arguments have invalid fields")
        result = {
            "start_artifact_id": _bounded_string(
                arguments["start_artifact_id"], "start_artifact_id", MAX_ID_LENGTH
            )
        }
        if "max_depth" in arguments:
            result["max_depth"] = _integer(arguments["max_depth"], "max_depth", 0, 10)
        if "limit" in arguments:
            result["limit"] = _integer(arguments["limit"], "limit", 1, 500)
        return result
    return arguments


class AttachedMcpBridge:
    """Authorize and audit one read-only artifact-to-MCP attachment."""

    def __init__(
        self,
        caller: ToolCaller,
        *,
        boundary: CapabilityBoundary | None = None,
        logger: logging.Logger | None = None,
        timeout_seconds: float = 5,
        max_result_bytes: int = MAX_BRIDGE_RESULT_BYTES,
    ):
        if timeout_seconds <= 0 or max_result_bytes < 1:
            raise ValueError("bridge limits must be positive")
        self.caller = caller
        self.boundary = boundary or CapabilityBoundary()
        self.logger = logger or logging.getLogger("folio_lattice.bridge")
        self.logger.setLevel(logging.INFO)
        self.timeout_seconds = timeout_seconds
        self.max_result_bytes = max_result_bytes

    async def call(self, request: Mapping[str, Any]) -> Any:
        started = time.monotonic()
        attachment = str(request["attachment"])
        tool = str(request["tool"])
        audit = {
            "event": "attached_mcp_decision",
            "request_id": str(request["request_id"]),
            "artifact_id": str(request["artifact_id"]),
            "attachment": attachment,
            "tool": tool,
        }
        if not self.boundary.authorize(attachment, tool):
            self._audit(audit, "deny", "capability_not_attached", started)
            raise BridgeRequestError(
                "attached MCP tool is not permitted", reason="capability_not_attached"
            )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self.caller.call(tool, request["arguments"])
        except TimeoutError as exc:
            self._audit(audit, "allow", "timeout", started)
            raise PublicMcpError("MCP call timed out") from exc
        except Exception:
            self._audit(audit, "allow", "tool_error", started)
            raise
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > self.max_result_bytes:
            self._audit(audit, "allow", "oversized_result", started)
            raise PublicMcpError("MCP result exceeds the bridge size limit")
        self._audit(audit, "allow", "completed", started)
        return result

    def audit_rejection(self, reason: str) -> None:
        self._audit(
            {
                "event": "attached_mcp_decision",
                "request_id": "unvalidated",
                "artifact_id": "unvalidated",
                "attachment": "unvalidated",
                "tool": "unvalidated",
            },
            "deny",
            reason,
            time.monotonic(),
        )

    def _audit(self, event: dict[str, Any], decision: str, reason: str, started: float) -> None:
        self.logger.info(
            json.dumps(
                {
                    **event,
                    "decision": decision,
                    "reason": reason,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
