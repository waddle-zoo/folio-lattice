"""Exercise the public MCP contract and persistence against Docker Compose."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from typing import Any
from urllib.request import urlopen

from hyperset_consumer import exercise
from mcp import Client


async def read_artifact(base_url: str, artifact_id: str) -> dict[str, Any]:
    async with Client(f"{base_url}/mcp", raise_exceptions=True) as client:
        response = await client.call_tool("artifact_read", {"artifact_id": artifact_id})
        assert not response.is_error, response
        assert response.structured_content is not None
        return response.structured_content


def wait_ready(base_url: str) -> None:
    for _ in range(60):
        try:
            with urlopen(f"{base_url}/health", timeout=1) as response:
                if json.loads(response.read())["ready"]:
                    return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Folio Docker service did not become ready")


def main() -> None:
    base_url = os.environ.get("FOLIO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    wait_ready(base_url)
    created = asyncio.run(exercise(base_url))

    subprocess.run(["docker", "compose", "restart", "folio"], check=True, timeout=30)
    wait_ready(base_url)

    persisted = asyncio.run(read_artifact(base_url, created["artifact_id"]))
    assert persisted["version"]["id"] == created["version_id"]
    assert persisted["version"]["blob_hash"] == created["blob_hash"]
    assert persisted["text"] == "Hyperset revised evidence source graph"
    print(json.dumps({"status": "ok", "persistence": "verified"}, sort_keys=True))


if __name__ == "__main__":
    main()
