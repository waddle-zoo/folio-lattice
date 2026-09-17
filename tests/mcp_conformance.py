"""Run two-client MCP conformance and write exact redacted transcripts.

The official client is ``mcp.Client``. The independent client uses only JSON-RPC,
``subprocess``, and ``urllib``; it never imports Folio implementation modules.
Each client/transport run starts with a new database and blob directory.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp import Client, StdioServerParameters

PROTOCOL_VERSION = "2025-06-18"
SCHEMA_VERSION = "folio-lattice.mcp-conformance.v1"
EXPECTED_TOOLS = (
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
    "external_mcp_connection_register",
    "external_mcp_connection_list",
    "external_mcp_connection_status",
    "external_mcp_connection_revoke",
    "external_mcp_audit",
    "external_mcp_tool_call",
    "external_mcp_resource_read",
)
SOURCE_ROOT = Path(os.environ.get("FOLIO_SOURCE_ROOT", Path(__file__).resolve().parents[1]))


class ConformanceError(Exception):
    """Protocol or contract mismatch."""


class BoundaryBlocked(ConformanceError):
    """The host could not provide a required process or network boundary."""


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SOURCE_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    actual = result.stdout.strip()
    configured = os.environ.get("FOLIO_CONFORMANCE_SOURCE_SHA")
    if configured and configured != actual:
        raise ConformanceError(
            f"source SHA mismatch: configured {configured!r}, checkout {actual!r}"
        )
    if os.environ.get("FOLIO_CONFORMANCE_REQUIRE_CLEAN", "1") == "1":
        try:
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=SOURCE_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ConformanceError("could not verify source tree status") from exc
        if dirty:
            raise ConformanceError("source tree is dirty")
    return actual


def _schema_snapshot(value: Any) -> dict[str, Any]:
    tools = _json(value)
    if isinstance(tools, Mapping) and isinstance(tools.get("result"), Mapping):
        tools = tools["result"]
    if isinstance(tools, Mapping) and "tools" in tools:
        tools = tools["tools"]
    if not isinstance(tools, list):
        raise ConformanceError("tools/list response omitted tools")
    snapshot: list[dict[str, Any]] = []
    for tool in tools:
        item = _json(tool)
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise ConformanceError("tools/list returned an invalid tool")
        snapshot.append(
            {
                key: item[key]
                for key in ("name", "title", "description", "inputSchema", "outputSchema")
                if key in item and item[key] is not None
            }
        )
    return {"tools": sorted(snapshot, key=lambda item: item["name"])}


def _unwrap(value: Any) -> Any:
    value = _json(value)
    if isinstance(value, Mapping) and set(value) == {"result"}:
        return value["result"]
    return value


def _error_text(value: Any) -> str:
    value = _json(value)
    if isinstance(value, Mapping):
        if isinstance(value.get("error"), Mapping):
            error = value["error"]
            return str(error.get("message") or error.get("code") or "JSON-RPC error")
        content = value.get("content")
        if isinstance(content, list):
            return " ".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, Mapping) and item.get("type") == "text"
            ).strip()
    return ""


def _redacted_exception(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:500]}


def _assert_tools(snapshot: dict[str, Any]) -> None:
    names = [tool["name"] for tool in snapshot["tools"]]
    if names != sorted(EXPECTED_TOOLS):
        raise ConformanceError(f"tool set mismatch: {names!r}")
    for tool in snapshot["tools"]:
        if not isinstance(tool.get("inputSchema"), Mapping):
            raise ConformanceError(f"{tool['name']} omitted inputSchema")


@dataclass
class Transcript:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, **entry: Any) -> None:
        self.entries.append({"sequence": len(self.entries) + 1, **entry})


@dataclass
class RawResponse:
    status: int | None
    headers: dict[str, str]
    body: bytes
    payload: Any


class RawHttpClient:
    """Independent public Streamable HTTP MCP client."""

    def __init__(
        self,
        endpoint: str,
        transcript: Transcript,
        headers: Mapping[str, str] | None = None,
    ):
        self.endpoint = endpoint
        self.transcript = transcript
        self.next_id = 1
        self.headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        self.headers.update(headers or {})

    def request(self, payload: Mapping[str, Any], *, expect_response: bool = True) -> RawResponse:
        request_id = payload.get("id")
        body = _json_bytes(payload)
        request = Request(self.endpoint, data=body, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                response_body = response.read()
                result = RawResponse(
                    response.status,
                    dict(response.headers.items()),
                    response_body,
                    _decode_http_body(response_body, response.headers.get("Content-Type", "")),
                )
        except HTTPError as exc:
            response_body = exc.read()
            result = RawResponse(
                exc.code,
                dict(exc.headers.items()),
                response_body,
                _decode_http_body(response_body, exc.headers.get("Content-Type", "")),
            )
        except URLError as exc:
            raise BoundaryBlocked(f"raw HTTP unavailable: {exc.reason}") from exc
        self.transcript.add(
            client="raw",
            wire="http",
            request=_transcript_payload(payload),
            response={
                "status": result.status,
                "headers": result.headers,
                "body": _transcript_body(result.body, result.payload),
            },
            request_id=request_id,
        )
        if expect_response and result.status is not None and result.status >= 500:
            raise ConformanceError(f"HTTP server failure: {result.status}")
        session_id = next(
            (value for key, value in result.headers.items() if key.lower() == "mcp-session-id"),
            None,
        )
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        return result

    def initialize(self) -> dict[str, Any]:
        response = self.request(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "folio-lattice-raw-conformance", "version": "1"},
                },
            }
        )
        _require_success(response, "initialize")
        self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = self.request(
            {"jsonrpc": "2.0", "id": self._id(), "method": "tools/list", "params": {}}
        )
        _require_success(listed, "tools/list")
        return listed.payload

    def call_tool(self, tool: str, arguments: Mapping[str, Any]) -> RawResponse:
        return self.request(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments)},
            }
        )

    def _id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value


class RawStdioClient:
    """Independent newline-delimited JSON-RPC client for public stdio."""

    def __init__(
        self,
        environment: Mapping[str, str],
        transcript: Transcript,
        command: list[str] | None = None,
    ):
        self.transcript = transcript
        self.next_id = 1
        try:
            self.process = subprocess.Popen(
                command or [sys.executable, "-m", "folio_lattice.server", "--transport", "stdio"],
                cwd=SOURCE_ROOT,
                env=dict(environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise BoundaryBlocked(f"stdio unavailable: {exc}") from exc

    def request(self, payload: Mapping[str, Any], *, expect_response: bool = True) -> Any:
        if self.process.stdin is None or self.process.stdout is None:
            raise BoundaryBlocked("stdio pipes unavailable")
        body = _json_bytes(payload) + b"\n"
        try:
            self.process.stdin.write(body)
            self.process.stdin.flush()
        except OSError as exc:
            raise BoundaryBlocked(f"stdio write failed: {exc}") from exc
        response: Any = None
        if expect_response:
            ready, _, _ = select.select([self.process.stdout], [], [], 10)
            if not ready:
                raise BoundaryBlocked("stdio response timed out")
            line = self.process.stdout.readline()
            if not line:
                detail = self.process.stderr.read().decode(errors="replace")
                raise BoundaryBlocked(f"stdio closed before response: {detail[-500:]}")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ConformanceError(f"stdio returned non-JSON: {line!r}") from exc
        self.transcript.add(
            client="raw",
            wire="stdio",
            request=_transcript_payload(payload),
            response=_transcript_payload(response),
            request_id=payload.get("id"),
        )
        return response

    def initialize(self) -> dict[str, Any]:
        response = self.request(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "folio-lattice-raw-conformance", "version": "1"},
                },
            }
        )
        _require_success(response, "initialize")
        self.request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False
        )
        listed = self.request(
            {"jsonrpc": "2.0", "id": self._id(), "method": "tools/list", "params": {}}
        )
        _require_success(listed, "tools/list")
        return listed

    def call_tool(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        return self.request(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments)},
            }
        )

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def _id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value


def _decode_http_body(body: bytes, content_type: str) -> Any:
    if "text/event-stream" in content_type:
        data = [
            line[5:].strip()
            for line in body.decode(errors="replace").splitlines()
            if line.startswith("data:")
        ]
        if not data:
            return None
        body = data[-1].encode()
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _transcript_body(body: bytes, payload: Any) -> Any:
    if len(body) <= 32 * 1024:
        return payload if payload is not None else body.decode(errors="replace")
    return {"bytes": len(body), "sha256": _sha256(body)}


def _transcript_payload(value: Any) -> Any:
    if value is None:
        return None
    encoded = _json_bytes(value)
    if len(encoded) > 32 * 1024:
        return {"bytes": len(encoded), "sha256": _sha256(encoded)}
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"authorization", "access_token", "credential_ref"}:
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _transcript_payload(item)
        return redacted
    if isinstance(value, list):
        return [_transcript_payload(item) for item in value]
    return value


def _require_success(response: Any, operation: str) -> Any:
    payload = response.payload if isinstance(response, RawResponse) else response
    if not isinstance(payload, Mapping) or "error" in payload:
        raise ConformanceError(f"{operation} failed: {_error_text(payload)}")
    return payload


def _record_official(transcript: Transcript, operation: str, arguments: Any, result: Any) -> None:
    transcript.add(
        client="official-sdk",
        wire="mcp-sdk",
        operation=operation,
        arguments=_transcript_payload(arguments),
        response=_transcript_payload(result),
    )


async def _official_flow(
    transport: str,
    root: Path,
    transcript: Transcript,
    endpoint: str | None,
) -> dict[str, Any]:
    environment = _server_environment(root)
    target: Any
    if transport == "stdio":
        target = StdioServerParameters(
            command=sys.executable,
            args=["-m", "folio_lattice.server", "--transport", "stdio"],
            env=environment,
            cwd=SOURCE_ROOT,
        )
    else:
        assert endpoint is not None
        target = endpoint
    async with Client(target, raise_exceptions=False, read_timeout_seconds=10) as client:
        listed = await client.list_tools()
        snapshot = _schema_snapshot(listed)
        transcript.add(
            client="official-sdk", wire="mcp-sdk", operation="tools/list", response=snapshot
        )
        _assert_tools(snapshot)

        async def call(
            tool: str, arguments: Mapping[str, Any], *, expect_error: bool = False
        ) -> Any:
            response = await client.call_tool(tool, dict(arguments))
            dumped = _json(response)
            _record_official(transcript, tool, arguments, dumped)
            if expect_error:
                if not response.is_error:
                    raise ConformanceError(f"{tool} unexpectedly succeeded")
                return dumped
            if response.is_error:
                raise ConformanceError(f"{tool} failed: {_error_text(dumped)}")
            return _unwrap(response.structured_content)

        return await _flow(call)


def _raw_flow(
    client: RawHttpClient | RawStdioClient,
    transcript: Transcript,
    *,
    approved_upstream: bool = False,
) -> dict[str, Any]:
    listed = client.initialize()
    snapshot = _schema_snapshot(listed)
    transcript.add(
        client="raw", wire=type(client).__name__, operation="tools/list", response=snapshot
    )
    _assert_tools(snapshot)

    def raw_call(tool: str, arguments: Mapping[str, Any], *, expect_error: bool = False) -> Any:
        response = client.call_tool(tool, arguments)
        payload = response.payload if isinstance(response, RawResponse) else response
        failed = (
            not isinstance(payload, Mapping)
            or "error" in payload
            or bool(isinstance(payload.get("result"), Mapping) and payload["result"].get("isError"))
        )
        if expect_error:
            if not failed:
                raise ConformanceError(f"{tool} unexpectedly succeeded")
            return payload
        if failed:
            raise ConformanceError(f"{tool} failed: {_error_text(payload)}")
        if isinstance(payload, Mapping):
            result = payload.get("result", payload)
            if isinstance(result, Mapping) and "structuredContent" in result:
                result = result["structuredContent"]
            return _unwrap(result)
        return payload

    async def call(tool: str, arguments: Mapping[str, Any], *, expect_error: bool = False) -> Any:
        return raw_call(tool, arguments, expect_error=expect_error)

    return asyncio.run(_flow(call, approved_upstream=approved_upstream, transcript=transcript))


async def _flow(
    call: Any, *, approved_upstream: bool = False, transcript: Transcript | None = None
) -> dict[str, Any]:
    content = base64.b64encode(b"conformance source marker").decode()
    target_content = base64.b64encode(b"conformance target marker").decode()
    source = await call(
        "artifact_create",
        {"name": "source.md", "content_base64": content, "media_type": "text/markdown"},
    )
    target = await call(
        "artifact_create",
        {"name": "target.md", "content_base64": target_content, "media_type": "text/markdown"},
    )
    web_asset = await call(
        "artifact_create",
        {
            "name": "agent-launch-board.html",
            "content_base64": base64.b64encode(b"launch controls").decode(),
            "media_type": "text/html",
        },
    )
    source_id = source["artifact"]["id"]
    target_id = target["artifact"]["id"]
    version_id = source["version"]["id"]
    read = await call("artifact_read", {"artifact_id": source_id})
    chunk = await call("artifact_read_chunk", {"chunk_id": read["chunks"][0]["id"]})
    listed = await call("artifact_list", {})
    searched = await call("artifact_search", {"query": "conformance source"})
    grepped = await call("artifact_grep", {"pattern": "conformance source"})
    filename_search = await call("artifact_search", {"query": "agent-launch-board.html"})
    filename_grep = await call("artifact_grep", {"pattern": "agent-launch-board.html"})
    named_assets = await call("artifact_list", {"name": "agent-launch-board.html", "limit": 1})
    typed_assets = await call("artifact_list", {"media_type": "text/html", "limit": 1})
    edge = await call(
        "graph_link",
        {
            "source_artifact_id": source_id,
            "target_artifact_id": target_id,
            "edge_type": "references",
        },
    )
    traversed = await call("graph_traverse", {"start_artifact_id": source_id})
    component = await call("graph_component", {"start_artifact_id": source_id})
    written = await call(
        "artifact_write",
        {
            "artifact_id": source_id,
            "parent_version_id": version_id,
            "content_base64": base64.b64encode(b"conformance updated marker").decode(),
            "media_type": "text/markdown",
            "reason": "conformance write",
        },
    )
    versions = await call("artifact_versions", {"artifact_id": source_id})
    grant = await call(
        "artifact_share",
        {
            "artifact_id": source_id,
            "subject_actor_id": "conformance-reader",
            "reason": "conformance share",
        },
    )
    acl = await call("artifact_acl", {"artifact_id": source_id})
    revoked = await call(
        "artifact_revoke",
        {"artifact_id": source_id, "grant_id": grant["id"], "reason": "conformance revoke"},
    )
    acl_after = await call("artifact_acl", {"artifact_id": source_id})
    approved_tools = ["calendar.events.list"]
    if approved_upstream:
        approved_tools.extend(["calendar.events.slow", "calendar.events.oversize"])
    connection = await call(
        "external_mcp_connection_register",
        {
            "name": "conformance-calendar",
            "endpoint": "https://approved-upstream.test/mcp"
            if approved_upstream
            else "https://calendar.example/mcp",
            "approved_tools": approved_tools,
            "approved_resources": ["calendar://events/today"],
            "allowed_origins": [
                "https://approved-upstream.test"
                if approved_upstream
                else "https://calendar.example"
            ],
            **({"credential_ref": "secret://conformance/upstream"} if approved_upstream else {}),
            "reason": "conformance registry flow",
        },
    )
    connection_id = connection["id"]
    connections = await call("external_mcp_connection_list", {})
    connection_status = await call(
        "external_mcp_connection_status", {"connection_id": connection_id}
    )
    external_tool_allowed = None
    external_resource_allowed = None
    external_timeout = None
    external_oversized = None
    if approved_upstream:
        connection_health = await call(
            "external_mcp_connection_status", {"connection_id": connection_id, "probe": True}
        )
        external_tool_allowed = await call(
            "external_mcp_tool_call",
            {
                "connection_id": connection_id,
                "tool_name": "calendar.events.list",
                "arguments": {"limit": 5},
            },
        )
        external_resource_allowed = await call(
            "external_mcp_resource_read",
            {
                "connection_id": connection_id,
                "resource_uri": "calendar://events/today",
            },
        )
        external_timeout = await call(
            "external_mcp_tool_call",
            {
                "connection_id": connection_id,
                "tool_name": "calendar.events.slow",
                "arguments": {},
            },
            expect_error=True,
        )
        external_oversized = await call(
            "external_mcp_tool_call",
            {
                "connection_id": connection_id,
                "tool_name": "calendar.events.oversize",
                "arguments": {},
            },
            expect_error=True,
        )
        if connection_health.get("health_status") != "healthy":
            raise ConformanceError("approved upstream health mismatch")
        if external_tool_allowed.get("upstream") != "approved-conformance":
            raise ConformanceError("approved upstream tool call mismatch")
        if "calendar://events/today" not in json.dumps(external_resource_allowed):
            raise ConformanceError("approved upstream resource read mismatch")
    external_tool_denied = await call(
        "external_mcp_tool_call",
        {
            "connection_id": connection_id,
            "tool_name": "calendar.events.delete",
            "arguments": {},
        },
        expect_error=True,
    )
    external_resource_denied = await call(
        "external_mcp_resource_read",
        {
            "connection_id": connection_id,
            "resource_uri": "calendar://events/private",
        },
        expect_error=True,
    )
    external_audit = await call("external_mcp_audit", {"connection_id": connection_id, "limit": 20})
    connection_revoked = await call(
        "external_mcp_connection_revoke",
        {"connection_id": connection_id, "reason": "conformance revoke"},
    )
    external_post_revoke = None
    if approved_upstream:
        external_post_revoke = await call(
            "external_mcp_tool_call",
            {
                "connection_id": connection_id,
                "tool_name": "calendar.events.list",
                "arguments": {},
            },
            expect_error=True,
        )
    malformed = await call("artifact_read", {"artifact_id": 7}, expect_error=True)
    oversized = await call(
        "artifact_create", {"name": "x" * 256, "content_base64": content}, expect_error=True
    )
    unknown = await call("unknown_tool", {}, expect_error=True)
    unauthorized = await call(
        "artifact_read", {"artifact_id": "art_not_visible"}, expect_error=True
    )
    for failure in (
        malformed,
        oversized,
        unknown,
        unauthorized,
        external_tool_denied,
        external_resource_denied,
        *(item for item in (external_timeout, external_oversized, external_post_revoke) if item),
    ):
        if any(
            secret in json.dumps(failure)
            for secret in (
                "sqlite",
                "Traceback",
                "conformance source marker",
                "conformance/upstream",
                "upstream-conformance-secret",
            )
        ):
            raise ConformanceError("failure response leaked internal or fixture content")
    if approved_upstream:
        encoded_transcript = json.dumps(transcript.entries if transcript else {}, sort_keys=True)
        if any(
            secret in encoded_transcript
            for secret in ("secret://conformance/upstream", "upstream-conformance-secret")
        ):
            raise ConformanceError("approved upstream transcript leaked secret material")
    if len(listed) != 3 or chunk["content"] != "conformance source marker":
        raise ConformanceError("artifact create/read/chunk/list mismatch")
    if filename_search or filename_grep:
        raise ConformanceError("body search unexpectedly indexed artifact metadata")
    if [item["id"] for item in named_assets] != [web_asset["artifact"]["id"]] or [
        item["id"] for item in typed_assets
    ] != [web_asset["artifact"]["id"]]:
        raise ConformanceError("artifact filename/media-type discovery mismatch")
    if not searched or not grepped or edge["target_artifact_id"] != target_id:
        raise ConformanceError("search/grep/link mismatch")
    if not traversed or {item["id"] for item in component} != {source_id, target_id}:
        raise ConformanceError("graph traversal/component mismatch")
    if written["parent_version_id"] != version_id or len(versions) != 2:
        raise ConformanceError("write/version mismatch")
    if not any(item["id"] == grant["id"] and item["status"] == "active" for item in acl):
        raise ConformanceError("share/ACL mismatch")
    if revoked["status"] != "revoked" or any(
        item["id"] == grant["id"] and item["status"] == "active" for item in acl_after
    ):
        raise ConformanceError("revoke/ACL mismatch")
    if (
        len(connections) != 1
        or connection_status["id"] != connection_id
        or connection_status["credential_configured"] != approved_upstream
        or connection_revoked["status"] != "revoked"
        or not any(item["outcome"] == "denied" for item in external_audit)
        or (
            approved_upstream
            and (
                not any(item["outcome"] == "allowed" for item in external_audit)
                or external_post_revoke is None
            )
        )
    ):
        raise ConformanceError("external MCP registry/revoke/audit mismatch")
    return {
        "artifact_count": len(listed),
        "search_count": len(searched),
        "grep_count": len(grepped),
        "filename_discovery": True,
        "media_type_discovery": True,
        "traverse_count": len(traversed),
        "component_count": len(component),
        "version_count": len(versions),
        "grant_status": grant["status"],
        "revoke_status": revoked["status"],
        "external_connection_status": connection_revoked["status"],
        "approved_upstream": approved_upstream,
        "approved_upstream_tool": external_tool_allowed is not None,
        "approved_upstream_resource": external_resource_allowed is not None,
        "approved_upstream_timeout": external_timeout is not None,
        "approved_upstream_oversize": external_oversized is not None,
        "approved_upstream_post_revoke_denied": external_post_revoke is not None,
        "negative_cases": [
            "malformed",
            "oversized",
            "unknown",
            "unauthorized",
            "external_tool_not_approved",
            "external_resource_not_approved",
        ],
    }


def _server_environment(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "FOLIO_DB_PATH": str(root / "folio.db"),
        "FOLIO_BLOB_ROOT": str(root / "blobs"),
        "FOLIO_TENANT_ID": "conformance-tenant",
        "FOLIO_ACTOR": "conformance-owner",
        "FOLIO_CONTROL_ORIGIN": "http://127.0.0.1:1",
        "PYTHONPATH": str(SOURCE_ROOT / "src"),
    }


def _free_port() -> int:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])
    except OSError as exc:
        raise BoundaryBlocked(f"loopback unavailable: {exc}") from exc


def _start_http(root: Path) -> tuple[subprocess.Popen[bytes], str]:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "folio_lattice.server",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=SOURCE_ROOT,
        env={**_server_environment(root), "FOLIO_CONTROL_ORIGIN": f"http://127.0.0.1:{port}"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                if json.loads(response.read()).get("ready"):
                    return process, endpoint
        except (OSError, URLError, json.JSONDecodeError):
            if process.poll() is not None:
                break
            time.sleep(0.05)
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    _stop(process)
    raise BoundaryBlocked(f"HTTP server did not become ready: {stderr[-500:]}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_raw(transport: str) -> dict[str, Any]:
    transcript = Transcript()
    with tempfile.TemporaryDirectory(prefix=f"folio-conformance-{transport}-raw-") as directory:
        root = Path(directory)
        client: RawHttpClient | RawStdioClient | None = None
        process: subprocess.Popen[bytes] | None = None
        try:
            if transport == "stdio":
                client = RawStdioClient(_server_environment(root), transcript)
            else:
                process, endpoint = _start_http(root)
                client = RawHttpClient(endpoint, transcript)
            summary = _raw_flow(client, transcript)
            return {"status": "pass", "summary": summary, "transcript": transcript.entries}
        finally:
            if isinstance(client, RawStdioClient):
                client.close()
            if process is not None:
                _stop(process)


def _run_official(transport: str) -> dict[str, Any]:
    transcript = Transcript()
    with tempfile.TemporaryDirectory(prefix=f"folio-conformance-{transport}-sdk-") as directory:
        root = Path(directory)
        process: subprocess.Popen[bytes] | None = None
        endpoint: str | None = None
        try:
            if transport == "http":
                process, endpoint = _start_http(root)
            summary = asyncio.run(_official_flow(transport, root, transcript, endpoint))
            return {"status": "pass", "summary": summary, "transcript": transcript.entries}
        finally:
            if process is not None:
                _stop(process)


def _run_case(client: str, transport: str) -> dict[str, Any]:
    try:
        return {
            "client": client,
            "transport": transport,
            **(_run_raw(transport) if client == "raw" else _run_official(transport)),
        }
    except BoundaryBlocked as exc:
        return {"client": client, "transport": transport, "status": "blocked", "error": str(exc)}
    except Exception as exc:  # evidence must retain first failure without hiding other matrix rows
        return {
            "client": client,
            "transport": transport,
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _unauthorized_probe() -> dict[str, Any]:
    """Probe missing bearer through the deterministic public hosted test target."""
    if os.environ.get("FOLIO_CONFORMANCE_SKIP_AUTH_PROBE") == "1":
        return {"status": "skipped", "reason": "FOLIO_CONFORMANCE_SKIP_AUTH_PROBE=1"}
    transcript = Transcript()
    process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="folio-conformance-auth-") as directory:
        try:
            port = _free_port()
            base_url = f"http://127.0.0.1:{port}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "tests" / "hosted_auth_target.py"),
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--state-dir",
                    directory,
                ],
                cwd=SOURCE_ROOT,
                env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT / "src")},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with urlopen(f"{base_url}/health", timeout=0.2) as response:
                        if json.loads(response.read()).get("ready"):
                            break
                except (OSError, URLError, json.JSONDecodeError):
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
            else:
                raise BoundaryBlocked("hosted auth target did not become ready")

            endpoint = f"{base_url}/tenant-a/mcp"
            raw = RawHttpClient(endpoint, transcript)
            raw_result = raw.request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "folio-lattice-raw-conformance", "version": "1"},
                    },
                }
            )
            if raw_result.status != 401 or "unauthorized" not in raw_result.body.decode(
                errors="replace"
            ):
                raise ConformanceError(
                    f"missing bearer returned unexpected HTTP response: {raw_result.status}"
                )

            official: dict[str, str]
            try:

                async def official_probe() -> None:
                    async with Client(
                        endpoint, raise_exceptions=True, read_timeout_seconds=10
                    ) as client:
                        await client.list_tools()

                asyncio.run(official_probe())
            except Exception as exc:
                official = _redacted_exception(exc)
            else:
                raise ConformanceError("official SDK unexpectedly authenticated without bearer")
            return {
                "status": "pass",
                "expected_status": 401,
                "official_sdk": official,
                "transcript": transcript.entries,
            }
        except BoundaryBlocked:
            raise
        finally:
            if process is not None:
                _stop(process)


def run(evidence_path: Path) -> int:
    runs = [
        _run_case(client, transport)
        for transport in ("stdio", "http")
        for client in ("official-sdk", "raw")
    ]
    try:
        authorization_probe = _unauthorized_probe()
    except BoundaryBlocked as exc:
        authorization_probe = {"status": "blocked", "error": str(exc)}
    except Exception as exc:
        authorization_probe = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
    passing = [item for item in runs if item["status"] == "pass"]
    snapshots = {
        f"{item['transport']}/{item['client']}": next(
            (
                entry["response"]
                for entry in item.get("transcript", [])
                if entry.get("operation") == "tools/list"
            ),
            None,
        )
        for item in passing
    }
    comparable = [value for value in snapshots.values() if value is not None]
    schemas_match = bool(comparable) and all(value == comparable[0] for value in comparable[1:])
    document = {
        "schema": SCHEMA_VERSION,
        "source_sha": _source_sha(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "expected_tools": list(EXPECTED_TOOLS),
        "schema_match": schemas_match,
        "authorization_probe": authorization_probe,
        "runs": runs,
        "status": (
            "pass"
            if len(passing) == 4 and schemas_match and authorization_probe["status"] == "pass"
            else "blocked"
            if any(item["status"] == "blocked" for item in runs)
            or authorization_probe["status"] == "blocked"
            else "fail"
        ),
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": document["status"],
                "evidence": str(evidence_path),
                "source_sha": document["source_sha"],
            }
        )
    )
    return 0 if document["status"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(
            os.environ.get("FOLIO_CONFORMANCE_EVIDENCE", "/tmp/folio-lattice-mcp-conformance.json")
        ),
    )
    return run(parser.parse_args().evidence)


if __name__ == "__main__":
    raise SystemExit(main())
