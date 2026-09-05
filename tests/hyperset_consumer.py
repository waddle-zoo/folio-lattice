"""Black-box Hyperset fixture using only Folio's public MCP/HTTP contract."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any

from mcp import Client


async def _tool(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    response = await client.call_tool(name, arguments)
    if response.is_error:
        raise RuntimeError(response.content[0].text)
    structured = response.structured_content
    if structured is None:
        raise RuntimeError(f"{name} returned no structured content")
    return structured.get("result", structured)


async def exercise(base_url: str) -> dict[str, Any]:
    async with Client(f"{base_url.rstrip('/')}/mcp", raise_exceptions=True) as client:
        source = await _tool(
            client,
            "artifact_create",
            {
                "name": "hyperset-evidence.md",
                "media_type": "text/markdown",
                "content_base64": base64.b64encode(b"Hyperset evidence source graph").decode(),
                "reason": "capture evaluation evidence",
                "source_context": {"system": "hyperset", "evaluation": "fixture-v0"},
            },
        )
        target = await _tool(
            client,
            "artifact_create",
            {
                "name": "hyperset-report.md",
                "media_type": "text/markdown",
                "content_base64": base64.b64encode(b"Hyperset report target").decode(),
                "reason": "capture evaluation report",
                "source_context": {"system": "hyperset", "evaluation": "fixture-v0"},
            },
        )
        source_id = source["artifact"]["id"]
        first_version_id = source["version"]["id"]
        updated = await _tool(
            client,
            "artifact_write",
            {
                "artifact_id": source_id,
                "parent_version_id": first_version_id,
                "content_base64": base64.b64encode(
                    b"Hyperset revised evidence source graph"
                ).decode(),
                "reason": "evaluation evidence revised",
                "source_context": {"system": "hyperset", "run_id": "fixture-run"},
            },
        )
        edge = await _tool(
            client,
            "graph_link",
            {
                "source_artifact_id": source_id,
                "target_artifact_id": target["artifact"]["id"],
                "edge_type": "supports",
                "metadata": {"claim": "fixture-claim"},
            },
        )
        search = await _tool(client, "artifact_search", {"query": "revised"})
        traversal = await _tool(client, "graph_traverse", {"start_artifact_id": source_id})
        versions = await _tool(client, "artifact_versions", {"artifact_id": source_id})
        read = await _tool(client, "artifact_read", {"artifact_id": source_id})
        chunk = await _tool(client, "artifact_read_chunk", {"chunk_id": search[0]["chunk_id"]})
        grep = await _tool(client, "artifact_grep", {"pattern": "source graph"})

        assert updated["parent_version_id"] == first_version_id
        assert updated["actor"] == "hyperset"
        assert edge["target_artifact_id"] == target["artifact"]["id"]
        assert traversal[0]["target_artifact_id"] == target["artifact"]["id"]
        assert len(versions) == 2
        assert read["text"] == "Hyperset revised evidence source graph"
        assert chunk["offset_unit"] == "unicode_code_points"
        assert grep[0]["offset_unit"] == "unicode_code_points"
        return {
            "artifact_id": source_id,
            "version_id": updated["id"],
            "blob_hash": updated["blob_hash"],
            "tenant_id": source["artifact"]["tenant_id"],
        }


def main() -> None:
    base_url = os.environ.get("FOLIO_BASE_URL", "http://127.0.0.1:8000")
    print(json.dumps(asyncio.run(exercise(base_url)), sort_keys=True))


if __name__ == "__main__":
    main()
