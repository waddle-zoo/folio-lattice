"""Black-box hosted Hyperset gate for the public Streamable HTTP MCP contract.

This file intentionally has no ``folio_lattice`` imports. Token commands,
membership revocation, and service restart are deployment hooks; artifact and
graph access below uses only the public MCP endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

GATE_ID = "fl-urj.5.2"
SCHEMA_VERSION = "1.0"
PUBLIC_TOOLS = frozenset(
    {
        "artifact_create",
        "artifact_write",
        "artifact_read",
        "artifact_read_chunk",
        "artifact_search",
        "artifact_grep",
        "graph_link",
        "graph_traverse",
        "artifact_versions",
    }
)
SIGNED_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MCP_DENIAL_CODES = frozenset(
    {"mcp_tool_denied", "mcp_http_unauthorized", "mcp_http_forbidden", "mcp_http_not_found"}
)


class GateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class McpFailure(GateError):
    pass


@dataclass(frozen=True)
class Token:
    raw: str
    subject: str
    audience: tuple[str, ...]
    algorithm: str
    key_id: str
    fingerprint: str


@dataclass(frozen=True)
class GateConfig:
    issuer: str
    audience: str
    tenant_a_url: str
    tenant_b_url: str
    tenant_a: str
    tenant_b: str
    owner_a: Token
    member_a: Token
    token_b: Token
    revoke_command: str
    restart_command: str
    run_id: str
    revocation_bound_seconds: float
    request_timeout_seconds: float
    expected_actor_owner: str | None
    evidence_path: Path
    command: str

    @classmethod
    def from_env(cls, evidence_path: Path, command: str) -> GateConfig:
        issuer = _issuer_url("FOLIO_TEST_ISSUER_URL")
        audience = _required("FOLIO_EXPECTED_AUDIENCE")
        tenant_a = _required("FOLIO_EXPECTED_TENANT_A")
        tenant_b = _required("FOLIO_EXPECTED_TENANT_B")
        tenant_a_url = _mcp_url("FOLIO_TENANT_A_MCP_URL")
        tenant_b_url = _mcp_url("FOLIO_TENANT_B_MCP_URL")
        run_id = os.environ.get("FOLIO_E2E_RUN_ID", "fl-urj-5-2")
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise GateError("invalid_run_id", "FOLIO_E2E_RUN_ID has an unsafe format")
        bound = _positive_float("FOLIO_REVOCATION_BOUND_SECONDS", 10)
        timeout = _positive_float("FOLIO_MCP_TIMEOUT_SECONDS", 10)
        expected_actor = os.environ.get("FOLIO_EXPECTED_ACTOR_A_OWNER")
        tokens = {
            profile: _load_token(profile, issuer, audience, tenant)
            for profile, tenant in (
                ("A_OWNER", tenant_a),
                ("A_MEMBER", tenant_a),
                ("B", tenant_b),
            )
        }
        _check_issuer_metadata(issuer, tokens, audience)
        return cls(
            issuer=issuer,
            audience=audience,
            tenant_a_url=tenant_a_url,
            tenant_b_url=tenant_b_url,
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            owner_a=tokens["A_OWNER"],
            member_a=tokens["A_MEMBER"],
            token_b=tokens["B"],
            revoke_command=_required("FOLIO_REVOKE_COMMAND"),
            restart_command=_required("FOLIO_RESTART_COMMAND"),
            run_id=run_id,
            revocation_bound_seconds=bound,
            request_timeout_seconds=timeout,
            expected_actor_owner=expected_actor,
            evidence_path=evidence_path,
            command=command,
        )


@dataclass
class Evidence:
    started_at: str
    assertions: list[dict[str, Any]] = field(default_factory=list)
    artifact: dict[str, Any] | None = None
    failure: dict[str, str] | None = None

    def passed(self, assertion_id: str, details: dict[str, Any] | None = None) -> None:
        self.assertions.append({"id": assertion_id, "status": "pass", "details": details or {}})

    def failed(self, assertion_id: str, details: dict[str, Any]) -> None:
        self.assertions.append({"id": assertion_id, "status": "fail", "details": details})

    def document(
        self,
        *,
        status: str,
        config: GateConfig | None,
        finished_at: str,
    ) -> dict[str, Any]:
        tokens = []
        if config is not None:
            for profile, token, tenant in (
                ("A_OWNER", config.owner_a, config.tenant_a),
                ("A_MEMBER", config.member_a, config.tenant_a),
                ("B", config.token_b, config.tenant_b),
            ):
                tokens.append(
                    {
                        "profile": profile,
                        "tenant": tenant,
                        "subject": token.subject,
                        "algorithm": token.algorithm,
                        "key_id": token.key_id,
                        "audience": list(token.audience),
                        "fingerprint": token.fingerprint,
                    }
                )
        return {
            "schema_version": SCHEMA_VERSION,
            "gate": GATE_ID,
            "status": status,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "command": config.command if config else "make hosted-e2e",
            "deployment": {
                "tenant_a_mcp_url": config.tenant_a_url
                if config
                else _safe_env_url("FOLIO_TENANT_A_MCP_URL"),
                "tenant_b_mcp_url": config.tenant_b_url
                if config
                else _safe_env_url("FOLIO_TENANT_B_MCP_URL"),
                "tenant_a": config.tenant_a
                if config
                else os.environ.get("FOLIO_EXPECTED_TENANT_A"),
                "tenant_b": config.tenant_b
                if config
                else os.environ.get("FOLIO_EXPECTED_TENANT_B"),
                "source_sha": os.environ.get("FOLIO_SOURCE_SHA", "not-provided"),
                "image_digest": os.environ.get("FOLIO_IMAGE_DIGEST", "not-provided"),
                "revocation_bound_seconds": (config.revocation_bound_seconds if config else None),
            },
            "issuer": {
                "url": config.issuer if config else os.environ.get("FOLIO_TEST_ISSUER_URL"),
                "metadata_url": (
                    os.environ.get("FOLIO_TEST_ISSUER_METADATA_URL") if config else None
                ),
                "audience": config.audience
                if config
                else os.environ.get("FOLIO_EXPECTED_AUDIENCE"),
                "metadata_checked": bool(os.environ.get("FOLIO_TEST_ISSUER_METADATA_URL")),
                "tokens": tokens,
            },
            "artifact": self.artifact,
            "assertions": self.assertions,
            "failure": self.failure,
            "redaction": {
                "tokens": "excluded",
                "cookies": "excluded",
                "private_keys": "excluded",
                "content_bytes": "excluded; SHA-256 only",
                "command_output": "excluded",
            },
        }


class PublicMcpClient:
    def __init__(self, endpoint: str, token: Token, timeout_seconds: float):
        self.endpoint = endpoint
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.last_http_status: int | None = None
        self.last_response_leaked = False

    async def _capture_response(self, response: Any) -> None:
        self.last_http_status = response.status_code
        if response.status_code >= 400:
            body = await response.aread()
            self.last_response_leaked = any(
                value and value in body.decode(errors="replace") for value in _SENSITIVE_VALUES
            )

    async def tools(self) -> list[str]:
        try:
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client

            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {self.token.raw}"},
                timeout=self.timeout_seconds,
            ) as http_client:
                transport = streamable_http_client(self.endpoint, http_client=http_client)
                async with Client(transport) as client:
                    result = await client.list_tools()
                    return [tool.name for tool in result.tools]
        except Exception as exc:
            raise McpFailure("mcp_unavailable", "authenticated MCP tools/list failed") from exc

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.last_http_status = None
        self.last_response_leaked = False
        try:
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client

            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {self.token.raw}"},
                timeout=self.timeout_seconds,
                event_hooks={"response": [self._capture_response]},
            ) as http_client:
                transport = streamable_http_client(self.endpoint, http_client=http_client)
                async with Client(transport) as client:
                    response = await client.call_tool(tool, arguments)
        except McpFailure:
            raise
        except Exception as exc:
            if self.last_response_leaked:
                raise GateError("error_leak", "MCP denial response exposed fixture data") from exc
            code = {
                401: "mcp_http_unauthorized",
                403: "mcp_http_forbidden",
                404: "mcp_http_not_found",
            }.get(self.last_http_status, "mcp_transport_error")
            raise McpFailure(code, "authenticated MCP call failed") from exc
        if response.is_error:
            text = " ".join(
                item.text for item in response.content if getattr(item, "type", None) == "text"
            )
            leaked = any(value and value in text for value in _SENSITIVE_VALUES)
            if leaked:
                raise GateError("error_leak", "MCP denial response exposed fixture data")
            raise McpFailure("mcp_tool_denied", "MCP tool returned an expected-error response")
        value = response.structured_content
        if value is None:
            raise McpFailure("mcp_invalid_result", "MCP tool returned no structured result")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value


_SENSITIVE_VALUES: tuple[str, ...] = ()


async def run_gate(config: GateConfig, evidence: Evidence) -> None:
    global _SENSITIVE_VALUES
    marker = "hypersetgate" + re.sub(r"[^A-Za-z0-9]", "", config.run_id)
    source_text = f"Hyperset hosted auth gate marker {marker}"
    revised_text = f"{source_text} revised source graph"
    target_text = f"Hyperset hosted auth gate target {config.run_id}"
    source_context = {
        "consumer": "hyperset",
        "gate": GATE_ID,
        "fixture_version": "hosted-auth-v1",
        "phase": "create",
    }
    revised_context = {**source_context, "phase": "write"}
    source_hash = hashlib.sha256(revised_text.encode()).hexdigest()
    initial_hash = hashlib.sha256(source_text.encode()).hexdigest()
    source_b64 = base64.b64encode(source_text.encode()).decode()
    revised_b64 = base64.b64encode(revised_text.encode()).decode()
    target_b64 = base64.b64encode(target_text.encode()).decode()
    _SENSITIVE_VALUES = (source_text, revised_text, target_text)

    owner = PublicMcpClient(config.tenant_a_url, config.owner_a, config.request_timeout_seconds)
    member = PublicMcpClient(config.tenant_a_url, config.member_a, config.request_timeout_seconds)
    other = PublicMcpClient(config.tenant_b_url, config.token_b, config.request_timeout_seconds)

    names = await owner.tools()
    _record(
        evidence,
        "public_streamable_http_mcp",
        set(names) >= PUBLIC_TOOLS and not any("hyperset" in name.lower() for name in names),
        {"transport": "streamable-http", "public_tools_present": sorted(PUBLIC_TOOLS)},
    )

    source = await owner.call(
        "artifact_create",
        {
            "name": f"hyperset-{config.run_id}-source.md",
            "media_type": "text/markdown",
            "content_base64": source_b64,
            "reason": "hosted consumer gate seed",
            "source_context": source_context,
        },
    )
    target = await owner.call(
        "artifact_create",
        {
            "name": f"hyperset-{config.run_id}-target.md",
            "media_type": "text/markdown",
            "content_base64": target_b64,
            "reason": "hosted consumer gate target",
            "source_context": source_context,
        },
    )
    source_artifact = source["artifact"]
    first_version = source["version"]
    target_artifact = target["artifact"]
    source_id = source_artifact["id"]
    target_id = target_artifact["id"]
    first_version_id = first_version["id"]
    _SENSITIVE_VALUES = (
        source_text,
        revised_text,
        target_text,
        source_id,
        target_id,
        first_version_id,
    )
    _record(
        evidence,
        "tenant_a_create",
        source_artifact["tenant_id"] == config.tenant_a
        and target_artifact["tenant_id"] == config.tenant_a
        and first_version["blob_hash"] == initial_hash,
        {"artifact_id": source_id, "target_artifact_id": target_id, "tenant": config.tenant_a},
    )
    _record(
        evidence,
        "tenant_a_create_provenance",
        _provenance_matches(
            first_version,
            actor=config.expected_actor_owner,
            reason="hosted consumer gate seed",
            source_context=source_context,
            parent_version_id=None,
            blob_hash=initial_hash,
        ),
        {"version_id": first_version_id, "source_context": source_context},
    )

    before_write = await owner.call("artifact_read", {"artifact_id": source_id})
    _record(
        evidence, "tenant_a_read", _read_matches(before_write, source_text, first_version_id), {}
    )
    member_read = await member.call("artifact_read", {"artifact_id": source_id})
    _record(
        evidence,
        "tenant_a_member_read",
        _read_matches(member_read, source_text, first_version_id),
        {"subject_fingerprint": config.member_a.fingerprint},
    )

    updated = await owner.call(
        "artifact_write",
        {
            "artifact_id": source_id,
            "parent_version_id": first_version_id,
            "content_base64": revised_b64,
            "media_type": "text/markdown",
            "reason": "hosted consumer gate revision",
            "source_context": revised_context,
        },
    )
    updated_id = updated["id"]
    _record(
        evidence,
        "tenant_a_immutable_write",
        _provenance_matches(
            updated,
            actor=config.expected_actor_owner,
            reason="hosted consumer gate revision",
            source_context=revised_context,
            parent_version_id=first_version_id,
            blob_hash=source_hash,
        ),
        {"version_id": updated_id, "parent_version_id": first_version_id},
    )
    after_write = await owner.call("artifact_read", {"artifact_id": source_id})
    _SENSITIVE_VALUES = (*_SENSITIVE_VALUES, updated_id, after_write["chunks"][0]["id"])
    _record(
        evidence,
        "tenant_a_revised_read",
        _read_matches(after_write, revised_text, updated_id),
        {},
    )

    edge = await owner.call(
        "graph_link",
        {
            "source_artifact_id": source_id,
            "target_artifact_id": target_id,
            "edge_type": "supports",
            "metadata": {"claim": "hosted-consumer-gate"},
        },
    )
    traversal = await owner.call("graph_traverse", {"start_artifact_id": source_id})
    _record(
        evidence,
        "tenant_a_graph",
        edge["target_artifact_id"] == target_id
        and any(item.get("id") == edge["id"] for item in traversal),
        {"edge_id": edge["id"], "target_artifact_id": target_id},
    )
    versions = await owner.call("artifact_versions", {"artifact_id": source_id})
    chunk = await owner.call("artifact_read_chunk", {"chunk_id": after_write["chunks"][0]["id"]})
    search = await owner.call("artifact_search", {"query": marker})
    grep = await owner.call("artifact_grep", {"pattern": marker})
    _record(
        evidence,
        "tenant_a_retrieval_contract",
        len(versions) == 2
        and versions[0] == first_version
        and versions[1] == updated
        and chunk["content"] == revised_text
        and chunk["offset_unit"] == "unicode_code_points"
        and search
        and grep,
        {"version_count": len(versions), "search_results": len(search), "grep_results": len(grep)},
    )

    await _expect_denied(
        evidence,
        "tenant_b_read_denied",
        other,
        "artifact_read",
        {"artifact_id": source_id},
        allow_empty=False,
    )
    await _expect_denied(
        evidence,
        "tenant_b_chunk_denied",
        other,
        "artifact_read_chunk",
        {"chunk_id": after_write["chunks"][0]["id"]},
        allow_empty=False,
    )
    await _expect_denied(
        evidence,
        "tenant_b_versions_denied",
        other,
        "artifact_versions",
        {"artifact_id": source_id},
        allow_empty=True,
    )
    await _expect_denied(
        evidence,
        "tenant_b_traverse_denied",
        other,
        "graph_traverse",
        {"start_artifact_id": source_id},
        allow_empty=True,
    )
    await _expect_empty(
        evidence, "tenant_b_search_empty", other, "artifact_search", {"query": marker}
    )
    await _expect_empty(
        evidence, "tenant_b_grep_empty", other, "artifact_grep", {"pattern": marker}
    )

    _run_hook(config.revoke_command, "revoke", config.member_a.subject)
    evidence.passed("membership_revoked", {"subject": config.member_a.subject})
    started = time.monotonic()
    while True:
        try:
            await member.call("artifact_read", {"artifact_id": source_id})
        except McpFailure as failure:
            if failure.code not in MCP_DENIAL_CODES:
                raise GateError(
                    "target_unavailable", "revocation check could not prove authorization denial"
                ) from None
            elapsed = time.monotonic() - started
            evidence.passed(
                "revoked_existing_credential_denied",
                {
                    "elapsed_seconds": round(elapsed, 3),
                    "credential_fingerprint": config.member_a.fingerprint,
                },
            )
            break
        if time.monotonic() - started >= config.revocation_bound_seconds:
            evidence.failed(
                "revoked_existing_credential_denied",
                {"within_seconds": config.revocation_bound_seconds},
            )
            raise GateError("revocation_not_effective", "revoked credential remained usable")
        await asyncio.sleep(0.25)

    _run_hook(config.restart_command, "restart", "")
    evidence.passed("service_restarted", {})
    persisted = await owner.call("artifact_read", {"artifact_id": source_id})
    persisted_versions = await owner.call("artifact_versions", {"artifact_id": source_id})
    persisted_graph = await owner.call("graph_traverse", {"start_artifact_id": source_id})
    expected_snapshot = _stable_read(after_write)
    _record(
        evidence,
        "restart_exact_persistence",
        _stable_read(persisted) == expected_snapshot
        and persisted_versions == versions
        and persisted_graph == traversal,
        {"artifact_id": source_id, "version_id": updated_id, "blob_hash": source_hash},
    )
    _record(
        evidence,
        "restart_exact_provenance",
        persisted["version"] == updated
        and persisted["version"]["source_context"] == revised_context
        and persisted["version"]["parent_version_id"] == first_version_id,
        {
            "version_id": updated_id,
            "parent_version_id": first_version_id,
            "source_context": revised_context,
        },
    )
    evidence.artifact = {
        "artifact_id": source_id,
        "target_artifact_id": target_id,
        "tenant_id": config.tenant_a,
        "initial_version_id": first_version_id,
        "version_id": updated_id,
        "initial_blob_sha256": initial_hash,
        "blob_sha256": source_hash,
        "versions": versions,
        "edge": edge,
        "content_sha256": source_hash,
    }


async def _expect_denied(
    evidence: Evidence,
    assertion_id: str,
    client: PublicMcpClient,
    tool: str,
    arguments: dict[str, Any],
    *,
    allow_empty: bool,
) -> None:
    try:
        result = await client.call(tool, arguments)
    except McpFailure as failure:
        if failure.code not in MCP_DENIAL_CODES:
            evidence.failed(assertion_id, {"outcome": "unavailable", "mechanism": failure.code})
            raise GateError(
                "target_unavailable", f"{assertion_id} could not prove denial"
            ) from None
        details: dict[str, Any] = {"outcome": "denied", "mechanism": failure.code}
        if client.last_http_status is not None:
            details["http_status"] = client.last_http_status
        evidence.passed(assertion_id, details)
        return
    if allow_empty and result == []:
        evidence.passed(assertion_id, {"outcome": "empty"})
        return
    evidence.failed(assertion_id, {"outcome": "unexpected_success"})
    raise GateError("tenant_isolation_failure", f"{assertion_id} observed tenant A data")


async def _expect_empty(
    evidence: Evidence,
    assertion_id: str,
    client: PublicMcpClient,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    try:
        result = await client.call(tool, arguments)
    except McpFailure as failure:
        outcome = "denied" if failure.code in MCP_DENIAL_CODES else "unavailable"
        evidence.failed(assertion_id, {"outcome": outcome, "mechanism": failure.code})
        raise GateError(
            "tenant_isolation_failure" if outcome == "denied" else "target_unavailable",
            f"{assertion_id} did not return an empty result",
        ) from None
    if result != []:
        evidence.failed(assertion_id, {"outcome": "non_empty"})
        raise GateError("tenant_isolation_failure", f"{assertion_id} observed tenant A data")
    evidence.passed(assertion_id, {"outcome": "empty"})


def _record(
    evidence: Evidence, assertion_id: str, condition: bool, details: dict[str, Any]
) -> None:
    if condition:
        evidence.passed(assertion_id, details)
        return
    evidence.failed(assertion_id, details)
    raise GateError("assertion_failed", assertion_id)


def _read_matches(value: dict[str, Any], text: str, version_id: str) -> bool:
    return (
        value.get("text") == text
        and value.get("content_base64") == base64.b64encode(text.encode()).decode()
        and value.get("version", {}).get("id") == version_id
    )


def _stable_read(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in ("artifact", "version", "chunks", "content_base64", "text")
        if key in value
    }


def _provenance_matches(
    version: dict[str, Any],
    *,
    actor: str | None,
    reason: str,
    source_context: dict[str, Any],
    parent_version_id: str | None,
    blob_hash: str,
) -> bool:
    return (
        bool(version.get("actor"))
        and (actor is None or version.get("actor") == actor)
        and version.get("reason") == reason
        and version.get("source_context") == source_context
        and version.get("parent_version_id") == parent_version_id
        and version.get("blob_hash") == blob_hash
    )


def _run_hook(command: str, operation: str, subject: str) -> None:
    argv = shlex.split(command)
    if not argv:
        raise GateError("invalid_hook", f"{operation} hook is empty")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("TOKEN", "SECRET", "PASSWORD"))
    }
    environment["FOLIO_TEST_HOOK_OPERATION"] = operation
    if subject:
        environment["FOLIO_TEST_REVOKED_SUBJECT"] = subject
    result = subprocess.run(
        argv,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise GateError("hook_failed", f"{operation} hook exited with {result.returncode}")


def _load_token(profile: str, issuer: str, audience: str, tenant: str) -> Token:
    value = os.environ.get(f"FOLIO_TOKEN_{profile}")
    command = os.environ.get(f"FOLIO_TOKEN_COMMAND_{profile}")
    if value and command:
        raise GateError("duplicate_token_source", f"token source duplicated for {profile}")
    if command:
        argv = shlex.split(command)
        if not argv:
            raise GateError("invalid_token_command", f"token command is empty for {profile}")
        environment = {
            key: item
            for key, item in os.environ.items()
            if not any(secret in key.upper() for secret in ("TOKEN", "SECRET", "PASSWORD"))
        }
        environment["FOLIO_TEST_TOKEN_PROFILE"] = profile
        environment["FOLIO_TEST_TOKEN_TENANT"] = tenant
        result = subprocess.run(
            argv,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise GateError("token_command_failed", f"token command failed for {profile}")
        value = _token_from_output(result.stdout)
    if not value:
        raise GateError(
            "missing_token",
            f"set FOLIO_TOKEN_{profile} or FOLIO_TOKEN_COMMAND_{profile}",
        )
    return _validate_token(value.strip(), issuer, audience)


def _token_from_output(output: str) -> str:
    value = output.strip()
    if value.startswith("{"):
        try:
            value = json.loads(value)["access_token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise GateError(
                "invalid_token_output", "token command did not return an access token"
            ) from exc
    if not isinstance(value, str) or not value.strip():
        raise GateError("invalid_token_output", "token command did not return an access token")
    return value.strip()


def _validate_token(value: str, issuer: str, audience: str) -> Token:
    parts = value.split(".")
    if len(parts) != 3:
        raise GateError("invalid_signed_token", "test issuer token is not compact JWS")
    try:
        header = json.loads(_decode_segment(parts[0]))
        claims = json.loads(_decode_segment(parts[1]))
        signature = _decode_segment(parts[2])
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise GateError(
            "invalid_signed_token", "test issuer token is not valid compact JSON"
        ) from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise GateError("invalid_signed_token", "test issuer token header and claims are objects")
    algorithm = header.get("alg")
    key_id = header.get("kid")
    if (
        algorithm not in SIGNED_ALGORITHMS
        or not isinstance(key_id, str)
        or not key_id
        or len(signature) < 16
    ):
        raise GateError("invalid_signed_token", "test issuer token lacks an asymmetric signature")
    if header.get("typ") not in (None, "JWT", "at+jwt"):
        raise GateError("invalid_signed_token", "test issuer token has an unsupported type")
    if claims.get("iss") != issuer:
        raise GateError("invalid_token_issuer", "test issuer token has the wrong issuer")
    token_audience = claims.get("aud")
    if isinstance(token_audience, str):
        token_audience = [token_audience]
    if (
        not isinstance(token_audience, list)
        or not all(isinstance(item, str) for item in token_audience)
        or audience not in token_audience
    ):
        raise GateError("invalid_token_audience", "test issuer token is not audience-bound")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise GateError("invalid_token_subject", "test issuer token has no subject")
    now = time.time()
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= now:
        raise GateError("invalid_token_expiry", "test issuer token is expired")
    skew = _positive_float("FOLIO_TOKEN_CLOCK_SKEW_SECONDS", 30)
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + skew:
        raise GateError("invalid_token_nbf", "test issuer token is not active")
    if isinstance(claims.get("iat"), (int, float)) and claims["iat"] > now + skew:
        raise GateError("invalid_token_iat", "test issuer token is issued in the future")
    return Token(
        raw=value,
        subject=subject,
        audience=tuple(str(item) for item in token_audience),
        algorithm=algorithm,
        key_id=key_id,
        fingerprint=hashlib.sha256(value.encode()).hexdigest()[:16],
    )


def _decode_segment(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded.encode(), altchars=b"-_", validate=True).decode()


def _check_issuer_metadata(issuer: str, tokens: dict[str, Token], audience: str) -> None:
    metadata_url = _issuer_url("FOLIO_TEST_ISSUER_METADATA_URL")
    request = Request(metadata_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            metadata = json.loads(response.read())
    except Exception as exc:
        raise GateError("issuer_unavailable", "test issuer metadata could not be read") from exc
    if not isinstance(metadata, dict):
        raise GateError("invalid_issuer_metadata", "test issuer metadata is not an object")
    jwks_uri = metadata.get("jwks_uri")
    if metadata.get("issuer") != issuer or not isinstance(jwks_uri, str) or not jwks_uri:
        raise GateError("invalid_issuer_metadata", "test issuer metadata is not OIDC-compatible")
    try:
        import jwt

        key_client = jwt.PyJWKClient(jwks_uri)
        for token in tokens.values():
            signing_key = key_client.get_signing_key_from_jwt(token.raw)
            jwt.decode(
                token.raw,
                signing_key.key,
                algorithms=[token.algorithm],
                audience=audience,
                issuer=issuer,
                leeway=_positive_float("FOLIO_TOKEN_CLOCK_SKEW_SECONDS", 30),
                options={"require": ["iss", "sub", "aud", "exp"]},
            )
    except GateError:
        raise
    except Exception as exc:
        raise GateError(
            "invalid_signed_token", "test issuer token failed JWKS signature verification"
        ) from exc


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GateError("missing_configuration", f"missing {name}")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise GateError("invalid_configuration", f"{name} must be a positive number") from exc
    if value <= 0:
        raise GateError("invalid_configuration", f"{name} must be a positive number")
    return value


def _mcp_url(name: str) -> str:
    value = _required(name)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise GateError("invalid_configuration", f"{name} must be an HTTP URL ending in /mcp")
    return value


def _issuer_url(name: str) -> str:
    value = _required(name)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GateError("invalid_configuration", f"{name} must be an HTTP issuer URL")
    return value


def _safe_env_url(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return _mcp_url(name)
    except GateError:
        return "invalid"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _write_evidence(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        default=os.environ.get("FOLIO_EVIDENCE_PATH", "/tmp/folio-lattice-fl-urj-5.2.json"),
    )
    args = parser.parse_args()
    evidence = Evidence(started_at=_now())
    evidence_path = Path(args.evidence)
    config: GateConfig | None = None
    status = "pass"
    try:
        config = GateConfig.from_env(
            evidence_path, os.environ.get("FOLIO_GATE_COMMAND", "make hosted-e2e")
        )
        asyncio.run(run_gate(config, evidence))
    except GateError as exc:
        status = (
            "blocked"
            if exc.code in {"missing_configuration", "invalid_configuration", "invalid_run_id"}
            else "fail"
        )
        evidence.failure = {"code": exc.code, "message": exc.message}
    except Exception as exc:  # pragma: no cover - final evidence guard
        status = "fail"
        evidence.failure = {"code": "unexpected_error", "message": type(exc).__name__}
    document = evidence.document(status=status, config=config, finished_at=_now())
    _write_evidence(evidence_path, document)
    print(json.dumps({"evidence": str(evidence_path), "gate": GATE_ID, "status": status}))
    if status == "blocked":
        raise SystemExit(2)
    if status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
