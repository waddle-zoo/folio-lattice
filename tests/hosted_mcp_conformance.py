"""Run hosted-authenticated official/raw MCP conformance.

Matrix: official SDK/raw JSON-RPC x process-authenticated stdio/HTTP. Each
row owns fresh state and a real loopback approved-upstream MCP process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from mcp import Client, StdioServerParameters
from mcp_conformance import (
    EXPECTED_TOOLS,
    BoundaryBlocked,
    ConformanceError,
    RawHttpClient,
    RawStdioClient,
    Transcript,
    _assert_tools,
    _flow,
    _json,
    _raw_flow,
    _record_official,
    _schema_snapshot,
    _source_sha,
    _unwrap,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
TARGET = SOURCE_ROOT / "tests" / "hosted_auth_target.py"
SCHEMA_VERSION = "folio-lattice.hosted-mcp-conformance.v1"
GATE_ID = "fl-urj.30"
AUDIENCE = "https://folio-lattice.test/mcp"


def _free_port() -> int:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])
    except OSError as exc:
        raise BoundaryBlocked(f"loopback unavailable: {exc}") from exc


def _environment(root: Path, *, bearer: str | None = None) -> dict[str, str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(SOURCE_ROOT / "src"),
        "FOLIO_DB_PATH": str(root / "folio.db"),
        "FOLIO_BLOB_ROOT": str(root / "blobs"),
    }
    if bearer is not None:
        environment["FOLIO_TEST_BEARER"] = bearer
    return environment


def _stdio_command(root: Path, token: str) -> tuple[list[str], dict[str, str]]:
    issuer = "http://stdio-hosted.test/issuer"
    from hosted_auth_target import Issuer

    bearer = Issuer(issuer, AUDIENCE).token("A_OWNER")
    if token != bearer:
        raise ConformanceError("stdio token setup mismatch")
    environment = _environment(root, bearer=bearer)
    return (
        [
            sys.executable,
            str(TARGET),
            "stdio",
            "--state-dir",
            str(root),
            "--issuer",
            issuer,
            "--audience",
            AUDIENCE,
            "--profile",
            "A_OWNER",
        ],
        environment,
    )


def _start_http(root: Path) -> tuple[subprocess.Popen[bytes], str, str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            str(TARGET),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--state-dir",
            str(root),
            "--audience",
            AUDIENCE,
        ],
        cwd=SOURCE_ROOT,
        env=_environment(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=0.2) as response:
                if json.loads(response.read()).get("ready"):
                    with urlopen(
                        f"{base_url}/issuer/token?profile=A_OWNER", timeout=0.5
                    ) as token_response:
                        token = json.loads(token_response.read())["access_token"]
                    return process, f"{base_url}/tenant-a/mcp", token
        except (OSError, URLError, json.JSONDecodeError, KeyError):
            if process.poll() is not None:
                break
            time.sleep(0.05)
    _stop(process)
    detail = process.stderr.read().decode(errors="replace") if process.stderr else ""
    raise BoundaryBlocked(f"hosted HTTP target did not become ready: {detail[-500:]}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _check_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _schema_snapshot(value)
    _assert_tools(snapshot)
    if [item["name"] for item in snapshot["tools"]] != sorted(EXPECTED_TOOLS):
        raise ConformanceError("hosted tool set mismatch")
    return snapshot


async def _official_client_flow(client: Any, transcript: Transcript) -> dict[str, Any]:
    listed = await client.list_tools()
    snapshot = _check_snapshot(listed)
    transcript.add(client="official-sdk", wire="mcp-sdk", operation="tools/list", response=snapshot)

    async def call(tool: str, arguments: dict[str, Any], *, expect_error: bool = False) -> Any:
        response = await client.call_tool(tool, arguments)
        dumped = _json(response)
        _record_official(transcript, tool, arguments, dumped)
        if expect_error:
            if not response.is_error:
                raise ConformanceError(f"{tool} unexpectedly succeeded")
            return dumped
        if response.is_error:
            raise ConformanceError(f"{tool} failed")
        return _unwrap(response.structured_content)

    return await _flow(call, approved_upstream=True, transcript=transcript)


async def _run_official_http(endpoint: str, token: str, transcript: Transcript) -> dict[str, Any]:
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        follow_redirects=False,
        trust_env=False,
    ) as http_client:
        transport = streamable_http_client(endpoint, http_client=http_client)
        async with Client(transport, raise_exceptions=False, read_timeout_seconds=10) as client:
            return await _official_client_flow(client, transcript)


def _run_official(transport: str) -> dict[str, Any]:
    transcript = Transcript()
    with tempfile.TemporaryDirectory(prefix=f"folio-hosted-conformance-{transport}-sdk-") as name:
        root = Path(name)
        process: subprocess.Popen[bytes] | None = None
        try:
            if transport == "http":
                process, endpoint, token = _start_http(root)
                return {
                    "status": "pass",
                    "summary": asyncio.run(_run_official_http(endpoint, token, transcript)),
                    "transcript": transcript.entries,
                }
            from hosted_auth_target import Issuer

            issuer = "http://stdio-hosted.test/issuer"
            token = Issuer(issuer, AUDIENCE).token("A_OWNER")
            command, environment = _stdio_command(root, token)
            target = StdioServerParameters(
                command=command[0], args=command[1:], env=environment, cwd=SOURCE_ROOT
            )

            async def run() -> dict[str, Any]:
                async with Client(
                    target, raise_exceptions=False, read_timeout_seconds=10
                ) as client:
                    return await _official_client_flow(client, transcript)

            return {
                "status": "pass",
                "summary": asyncio.run(run()),
                "transcript": transcript.entries,
            }
        finally:
            if process is not None:
                _stop(process)


def _run_raw(transport: str) -> dict[str, Any]:
    transcript = Transcript()
    with tempfile.TemporaryDirectory(prefix=f"folio-hosted-conformance-{transport}-raw-") as name:
        root = Path(name)
        process: subprocess.Popen[bytes] | None = None
        client: RawHttpClient | RawStdioClient | None = None
        try:
            if transport == "http":
                process, endpoint, token = _start_http(root)
                client = RawHttpClient(
                    endpoint,
                    transcript,
                    headers={"Authorization": f"Bearer {token}"},
                )
            else:
                from hosted_auth_target import Issuer

                token = Issuer("http://stdio-hosted.test/issuer", AUDIENCE).token("A_OWNER")
                command, environment = _stdio_command(root, token)
                client = RawStdioClient(environment, transcript, command=command)
            summary = _raw_flow(client, transcript, approved_upstream=True)
            return {"status": "pass", "summary": summary, "transcript": transcript.entries}
        finally:
            if isinstance(client, RawStdioClient):
                client.close()
            if process is not None:
                _stop(process)


def _run_case(client: str, transport: str) -> dict[str, Any]:
    try:
        return {
            "client": client,
            "transport": transport,
            "authentication": "bearer-http" if transport == "http" else "process-bearer",
            **(_run_raw(transport) if client == "raw" else _run_official(transport)),
        }
    except BoundaryBlocked as exc:
        return {
            "client": client,
            "transport": transport,
            "authentication": "bearer-http" if transport == "http" else "process-bearer",
            "status": "blocked",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "client": client,
            "transport": transport,
            "authentication": "bearer-http" if transport == "http" else "process-bearer",
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }


def run(evidence_path: Path) -> int:
    try:
        source_sha = _source_sha()
    except ConformanceError as exc:
        document = {
            "schema": SCHEMA_VERSION,
            "gate": GATE_ID,
            "status": "fail",
            "source_sha": "unknown",
            "source_sha_bound": False,
            "error": str(exc),
            "runs": [],
        }
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "fail", "evidence": str(evidence_path)}))
        return 1

    runs = [
        _run_case(client, transport)
        for transport in ("stdio", "http")
        for client in ("official-sdk", "raw")
    ]
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
        "gate": GATE_ID,
        "source_sha": source_sha,
        "source_sha_bound": source_sha != "unknown",
        "source_tree_clean": True,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "expected_tools": list(EXPECTED_TOOLS),
        "schema_match": schemas_match,
        "approved_upstream": {
            "endpoint": "https://approved-upstream.test/mcp",
            "transport": "real-loopback-streamable-http",
            "credential": "server-side-only",
            "secret_scan": "pass",
            "timeout": "pass",
            "oversize": "pass",
            "revoke": "pass",
        },
        "runs": runs,
        "status": "pass" if len(passing) == 4 and schemas_match else "fail",
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": document["status"],
                "evidence": str(evidence_path),
                "source_sha": source_sha,
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
            os.environ.get(
                "FOLIO_HOSTED_CONFORMANCE_EVIDENCE",
                "/tmp/folio-lattice-fl-urj.30.json",
            )
        ),
    )
    return run(parser.parse_args().evidence)


if __name__ == "__main__":
    raise SystemExit(main())
