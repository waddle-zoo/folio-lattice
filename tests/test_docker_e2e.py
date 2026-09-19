"""Exercise the public MCP contract and persistence against Docker Compose."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from hyperset_consumer import exercise
from mcp import Client


async def read_artifact(base_url: str, artifact_id: str) -> dict[str, Any]:
    async with Client(f"{base_url}/mcp", raise_exceptions=True) as client:
        response = await client.call_tool("artifact_read", {"artifact_id": artifact_id})
        assert not response.is_error, response
        assert response.structured_content is not None
        return response.structured_content


async def create_html(base_url: str) -> dict[str, Any]:
    async with Client(f"{base_url}/mcp", raise_exceptions=True) as client:
        response = await client.call_tool(
            "artifact_create",
            {
                "name": "docker-render.html",
                "media_type": "text/html",
                "content_base64": base64.b64encode(
                    b"<h1>Docker renderer</h1><script>document.body.dataset.ran='yes'</script>"
                ).decode(),
            },
        )
        assert not response.is_error, response
        assert response.structured_content is not None
        return response.structured_content


def post_json(url: str, payload: dict[str, Any], origin: str) -> Any:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def wait_ready(base_url: str) -> dict[str, Any]:
    for _ in range(60):
        try:
            with urlopen(f"{base_url}/readyz", timeout=1) as response:
                readiness = json.loads(response.read())
                if readiness["ready"]:
                    return readiness
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Folio Docker service did not become ready")


def assert_oversized_human_gateway_recovers(base_url: str) -> None:
    parsed = urlsplit(base_url)
    assert parsed.hostname is not None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    maximum = 13 * 1024 * 1024
    with socket.create_connection((parsed.hostname, port), timeout=10) as connection:
        connection.settimeout(10)
        connection.sendall(
            (
                f"POST /api/mcp HTTP/1.1\r\n"
                f"Host: {parsed.netloc}\r\n"
                "Content-Type: application/json\r\n"
                f"Origin: {base_url}\r\n"
                f"Content-Length: {maximum + 1}\r\n\r\n"
            ).encode()
            + b"{"
        )
        raw = bytearray()
        while chunk := connection.recv(65536):
            raw.extend(chunk)
    header_bytes, body = bytes(raw).split(b"\r\n\r\n", 1)
    lines = header_bytes.splitlines()
    assert lines[0].startswith(b"HTTP/1.1 413 "), lines[0]
    headers = {
        line.split(b": ", 1)[0].lower(): line.split(b": ", 1)[1]
        for line in lines[1:]
        if b": " in line
    }
    assert headers[b"retry-after"] == b"0"
    assert headers[b"cache-control"] == b"no-store"
    assert len(body) < 512
    response = json.loads(body)
    assert response["code"] == "request_too_large"
    assert response["error"] == "request body too large"
    assert response["request_id"]
    assert wait_ready(base_url)["ready"] is True


def main() -> None:
    base_url = os.environ.get("FOLIO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    render_url = os.environ.get("FOLIO_RENDER_URL", "http://127.0.0.1:8001").rstrip("/")
    readiness = wait_ready(base_url)
    renderer_readiness = wait_ready(render_url)
    assert_oversized_human_gateway_recovers(base_url)
    assert readiness["dependencies"]["database"]["ready"] is True
    assert readiness["dependencies"]["blob"]["ready"] is True
    assert readiness["dependencies"]["migration"]["ready"] is True
    assert readiness["dependencies"]["acl"]["ready"] is True
    assert readiness["dependencies"]["external_mcp"]["ready"] is True
    created = asyncio.run(exercise(base_url))
    html = asyncio.run(create_html(base_url))

    compose = ["docker", "compose"]
    project = os.environ.get("FOLIO_COMPOSE_PROJECT")
    if project:
        compose.extend(["-p", project])

    def inspect_service(name: str) -> dict[str, Any]:
        container_id = subprocess.run(
            [*compose, "ps", "-q", name], check=True, text=True, capture_output=True
        ).stdout.strip()
        return json.loads(
            subprocess.run(
                ["docker", "inspect", container_id], check=True, text=True, capture_output=True
            ).stdout
        )[0]

    for name in ("folio", "renderer"):
        inspected = inspect_service(name)
        assert inspected["Config"]["User"] == "10001:10001"
        assert inspected["HostConfig"]["ReadonlyRootfs"] is True
        assert inspected["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in inspected["HostConfig"]["SecurityOpt"]
    assert inspect_service("renderer")["Mounts"] == []

    subprocess.run([*compose, "restart", "folio", "renderer"], check=True, timeout=90)
    readiness_after_restart = wait_ready(base_url)
    renderer_readiness_after_restart = wait_ready(render_url)

    persisted = asyncio.run(read_artifact(base_url, created["artifact_id"]))
    assert persisted["version"]["id"] == created["version_id"]
    assert persisted["version"]["blob_hash"] == created["blob_hash"]
    assert persisted["text"] == f"Hyperset revised evidence source graph {created['marker']}"
    inspected = post_json(
        f"{base_url}/api/mcp",
        {"tool": "artifact_read", "arguments": {"artifact_id": created["artifact_id"]}},
        base_url,
    )
    assert inspected["version"]["id"] == created["version_id"]
    assert inspected["chunks"]
    graph = post_json(
        f"{base_url}/api/mcp",
        {
            "tool": "graph_traverse",
            "arguments": {"start_artifact_id": created["artifact_id"]},
        },
        base_url,
    )
    assert graph
    bridged = post_json(
        f"{base_url}/api/bridge",
        {
            "request_id": "docker-bridge",
            "artifact_id": created["artifact_id"],
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": {"query": created["marker"]},
        },
        base_url,
    )
    assert bridged["result"]

    html_id = html["artifact"]["id"]
    with urlopen(
        Request(f"{render_url}/render/{html_id}", headers={"Sec-Fetch-Dest": "iframe"})
    ) as response:
        rendered = response.read().decode()
        policy = response.headers["Content-Security-Policy"]
    assert "Docker renderer" in rendered
    assert "sandbox allow-scripts" in policy
    assert "connect-src 'none'" in policy
    try:
        urlopen(Request(f"{render_url}/render/{html_id}", data=b"", method="POST"))
        raise AssertionError("renderer accepted POST")
    except HTTPError as error:
        assert error.code == 405
        assert "sandbox allow-scripts" in error.headers["Content-Security-Policy"]
    print(
        json.dumps(
            {
                "status": "ok",
                "readiness": {
                    "folio": readiness,
                    "renderer": renderer_readiness,
                    "folio_after_restart": readiness_after_restart,
                    "renderer_after_restart": renderer_readiness_after_restart,
                },
                "persistence": "verified",
                "public_ui_gateway": "verified",
                "attached_bridge": "verified",
                "renderer": "verified_without_storage_mount",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
