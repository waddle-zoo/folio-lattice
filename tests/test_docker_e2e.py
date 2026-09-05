"""Exercise the public MCP contract against an already-running Docker service."""

from __future__ import annotations

import base64
import json
import os
from urllib.request import Request, urlopen


def call(base_url: str, request_id: int, method: str, params: dict) -> dict:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    ).encode("utf-8")
    request = Request(
        f"{base_url}/mcp",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def tool(base_url: str, request_id: int, name: str, arguments: dict) -> dict:
    response = call(
        base_url,
        request_id,
        "tools/call",
        {"name": name, "arguments": arguments},
    )
    assert "error" not in response, response
    return response["result"]["structuredContent"]


def main() -> None:
    base_url = os.environ.get("FOLIO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    with urlopen(f"{base_url}/health", timeout=5) as response:
        assert json.loads(response.read()) == {"status": "ok"}

    initialized = call(base_url, 1, "initialize", {"capabilities": {}})
    assert initialized["result"]["serverInfo"]["name"] == "folio-lattice"

    source = tool(
        base_url,
        2,
        "artifact_create",
        {
            "tenant_id": "docker-smoke",
            "name": "source.md",
            "media_type": "text/markdown",
            "content_base64": base64.b64encode(b"source graph content").decode("ascii"),
            "actor": "ci",
            "reason": "docker smoke test",
        },
    )
    target = tool(
        base_url,
        3,
        "artifact_create",
        {
            "tenant_id": "docker-smoke",
            "name": "target.md",
            "media_type": "text/markdown",
            "content_base64": base64.b64encode(b"target knowledge").decode("ascii"),
            "actor": "ci",
            "reason": "docker smoke test",
        },
    )
    source_id = source["artifact"]["id"]
    target_id = target["artifact"]["id"]
    first_version_id = source["version"]["id"]

    updated = tool(
        base_url,
        4,
        "artifact_write",
        {
            "tenant_id": "docker-smoke",
            "artifact_id": source_id,
            "parent_version_id": first_version_id,
            "content_base64": base64.b64encode(b"updated graph content").decode("ascii"),
            "actor": "ci",
            "reason": "docker smoke update",
        },
    )
    assert updated["parent_version_id"] == first_version_id
    assert (
        tool(
            base_url,
            5,
            "artifact_read",
            {"tenant_id": "docker-smoke", "artifact_id": source_id},
        )["text"]
        == "updated graph content"
    )
    assert tool(
        base_url,
        6,
        "artifact_search",
        {"tenant_id": "docker-smoke", "query": "updated"},
    )
    tool(
        base_url,
        7,
        "graph_link",
        {
            "tenant_id": "docker-smoke",
            "source_artifact_id": source_id,
            "target_artifact_id": target_id,
            "edge_type": "references",
        },
    )
    traversal = tool(
        base_url,
        8,
        "graph_traverse",
        {"tenant_id": "docker-smoke", "start_artifact_id": source_id},
    )
    assert traversal[0]["target_artifact_id"] == target_id
    assert (
        len(
            tool(
                base_url,
                9,
                "artifact_versions",
                {"tenant_id": "docker-smoke", "artifact_id": source_id},
            )
        )
        == 2
    )


if __name__ == "__main__":
    main()
