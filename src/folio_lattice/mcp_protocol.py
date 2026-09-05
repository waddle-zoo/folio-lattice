from __future__ import annotations

import json
from typing import Any, Callable

from .service import FolioError, FolioLattice


TOOLS: list[dict[str, Any]] = [
    {"name": "artifact_create", "description": "Create an artifact and its first immutable version.", "inputSchema": {"type": "object", "required": ["name", "content_base64"], "properties": {"tenant_id": {"type": "string"}, "name": {"type": "string"}, "content_base64": {"type": "string"}, "media_type": {"type": "string"}, "actor": {"type": "string"}, "reason": {"type": "string"}, "source_context": {"type": "object"}}}},
    {"name": "artifact_write", "description": "Write a new immutable version with an optimistic parent check.", "inputSchema": {"type": "object", "required": ["artifact_id", "content_base64"], "properties": {"tenant_id": {"type": "string"}, "artifact_id": {"type": "string"}, "content_base64": {"type": "string"}, "media_type": {"type": "string"}, "parent_version_id": {"type": ["string", "null"]}, "actor": {"type": "string"}, "reason": {"type": "string"}, "source_context": {"type": "object"}}}},
    {"name": "artifact_read", "description": "Read a complete artifact version.", "inputSchema": {"type": "object", "required": ["artifact_id"], "properties": {"tenant_id": {"type": "string"}, "artifact_id": {"type": "string"}, "version_id": {"type": "string"}}}},
    {"name": "artifact_read_chunk", "description": "Read one bounded document chunk.", "inputSchema": {"type": "object", "required": ["chunk_id"], "properties": {"tenant_id": {"type": "string"}, "chunk_id": {"type": "string"}}}},
    {"name": "artifact_search", "description": "Search indexed text with SQLite FTS5.", "inputSchema": {"type": "object", "required": ["query"], "properties": {"tenant_id": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "artifact_grep", "description": "Run a bounded regular-expression search over indexed chunks.", "inputSchema": {"type": "object", "required": ["pattern"], "properties": {"tenant_id": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "graph_link", "description": "Create or update a typed edge between artifacts.", "inputSchema": {"type": "object", "required": ["source_artifact_id", "target_artifact_id", "edge_type"], "properties": {"tenant_id": {"type": "string"}, "source_artifact_id": {"type": "string"}, "target_artifact_id": {"type": "string"}, "edge_type": {"type": "string"}, "metadata": {"type": "object"}}}},
    {"name": "graph_traverse", "description": "Traverse outgoing graph edges with explicit bounds.", "inputSchema": {"type": "object", "required": ["start_artifact_id"], "properties": {"tenant_id": {"type": "string"}, "start_artifact_id": {"type": "string"}, "max_depth": {"type": "integer"}, "limit": {"type": "integer"}}}},
    {"name": "artifact_versions", "description": "List immutable version history.", "inputSchema": {"type": "object", "required": ["artifact_id"], "properties": {"tenant_id": {"type": "string"}, "artifact_id": {"type": "string"}, "limit": {"type": "integer"}}}},
]


def _tenant(args: dict[str, Any]) -> str:
    return str(args.get("tenant_id") or "dev")


class McpProtocol:
    def __init__(self, service: FolioLattice):
        self.service = service

    def _call(self, name: str, args: dict[str, Any]) -> Any:
        tenant_id = _tenant(args)
        if name == "artifact_create":
            import base64
            return self.service.create_artifact(tenant_id=tenant_id, name=str(args["name"]), data=base64.b64decode(args["content_base64"], validate=True), media_type=args.get("media_type"), actor=str(args.get("actor") or "dev"), reason=str(args.get("reason") or "initial artifact"), source_context=args.get("source_context") or {})
        if name == "artifact_write":
            import base64
            artifact = self.service.get_artifact(tenant_id, str(args["artifact_id"]))
            return self.service.write_version(tenant_id=tenant_id, artifact_id=artifact["id"], data=base64.b64decode(args["content_base64"], validate=True), media_type=str(args.get("media_type") or artifact["media_type"]), actor=str(args.get("actor") or "dev"), reason=str(args.get("reason") or "update"), source_context=args.get("source_context") or {}, parent_version_id=args.get("parent_version_id"))
        if name == "artifact_read":
            return self.service.read_artifact(tenant_id, str(args["artifact_id"]), args.get("version_id"))
        if name == "artifact_read_chunk":
            return self.service.read_chunk(tenant_id, str(args["chunk_id"]))
        if name == "artifact_search":
            return self.service.search(tenant_id, str(args["query"]), int(args.get("limit") or 20))
        if name == "artifact_grep":
            return self.service.grep(tenant_id, str(args["pattern"]), int(args.get("limit") or 100))
        if name == "graph_link":
            return self.service.link(tenant_id, str(args["source_artifact_id"]), str(args["target_artifact_id"]), str(args["edge_type"]), args.get("metadata") or {})
        if name == "graph_traverse":
            return self.service.traverse(tenant_id, str(args["start_artifact_id"]), int(args.get("max_depth") or 2), int(args.get("limit") or 100))
        if name == "artifact_versions":
            return self.service.versions(tenant_id, str(args["artifact_id"]), int(args.get("limit") or 100))
        raise FolioError(f"unknown tool: {name}")

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "folio-lattice", "version": "0.1.0"}}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = request.get("params") or {}
            try:
                result = self._call(str(params["name"]), params.get("arguments") or {})
                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}], "structuredContent": result}}
            except (KeyError, ValueError, FolioError) as exc:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
