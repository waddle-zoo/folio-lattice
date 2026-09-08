"""Adversarial contract tests for the future hosted HTTP/MCP auth boundary.

These tests deliberately do not implement an IdP or production authentication.
Set ``FOLIO_HOSTED_AUTH_TEST_TARGET=module:factory`` to run integration cases.
The factory must return an object with:

* ``mcp_url``: public Streamable HTTP MCP endpoint;
* ``token(principal, claims=..., headers=..., signature=...)``: test-only token
  minting, signed by the fixture's configured OIDC test key;
* ``rotate_jwks()`` and ``fail_jwks_fetch()``: test-only JWKS controls; and
* ``close()``: cleanup.

Required principal names are ``member_a``, ``member_b``, ``unmapped``,
``ambiguous``, ``disabled``, and ``revoked``. The target owns its fixture
membership data and must expose one empty shared store per factory instance.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

TARGET_ENV = "FOLIO_HOSTED_AUTH_TEST_TARGET"
DENIAL_STATUSES = frozenset({401, 403, 404, 429, 503})
MAX_PUBLIC_ERROR_BYTES = 512
PRIVATE_MARKER = "hosted-auth-private-marker-7e1d"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
ACTOR_A = "actor-a"
ACTOR_B = "actor-b"


class HostedAuthTestTarget(Protocol):
    mcp_url: str

    def token(
        self,
        principal: str,
        *,
        claims: Mapping[str, object] | None = None,
        headers: Mapping[str, object] | None = None,
        signature: str | None = None,
    ) -> str: ...

    def rotate_jwks(self) -> None: ...

    def fail_jwks_fetch(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class PublicMcpHttpClient:
    """Small raw HTTP client for the public MCP contract, with bearer injection."""

    def __init__(self, mcp_url: str, *, timeout: float = 5) -> None:
        self.mcp_url = mcp_url
        self.timeout = timeout
        self._next_id = 1

    def call_tool(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        token: str | None = None,
        authorization: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        query: str = "",
        initialize_meta: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        if token is not None and authorization is not None:
            raise ValueError("choose token or authorization")
        headers = {"Accept": "application/json, text/event-stream"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if authorization is not None:
            headers["Authorization"] = authorization
        if any(key.lower() == "authorization" for key in (extra_headers or {})):
            raise ValueError("use authorization argument for Authorization header")
        headers.update(extra_headers or {})

        initialize_params: dict[str, Any] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "folio-hosted-auth-negative-tests", "version": "1"},
        }
        if initialize_meta is not None:
            initialize_params["_meta"] = dict(initialize_meta)
        initialize = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "initialize",
                "params": initialize_params,
            },
            headers,
            query=query,
        )
        if initialize.status not in {200, 201, 202}:
            return initialize

        session_id = next(
            (value for key, value in initialize.headers.items() if key.lower() == "mcp-session-id"),
            None,
        )
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        initialized = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, query=query
        )
        if initialized.status >= 400:
            return initialized
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments)},
            },
            headers,
            query=query,
        )

    def _id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _post(
        self, payload: Mapping[str, Any], headers: Mapping[str, str], *, query: str = ""
    ) -> HttpResponse:
        url = f"{self.mcp_url}{query}"
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return HttpResponse(
                    response.status, dict(response.headers.items()), response.read()
                )
        except HTTPError as exc:
            return HttpResponse(exc.code, dict(exc.headers.items()), exc.read())
        except URLError as exc:
            raise AssertionError(f"hosted auth target unavailable: {exc.reason}") from exc


def _load_target() -> HostedAuthTestTarget:
    spec = os.environ.get(TARGET_ENV)
    if not spec:
        pytest.skip(
            "hosted auth seam absent: set "
            f"{TARGET_ENV}=module:factory; expected mcp_url/token/rotate_jwks/fail_jwks_fetch"
        )
    try:
        module_name, factory_name = spec.split(":", 1)
        factory = getattr(import_module(module_name), factory_name)
        target = factory()
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {TARGET_ENV} factory {spec!r}: {exc}") from exc
    missing = [
        name
        for name in ("mcp_url", "token", "rotate_jwks", "fail_jwks_fetch", "close")
        if not hasattr(target, name)
    ]
    if missing:
        raise AssertionError(
            f"hosted auth test target missing required members: {', '.join(missing)}"
        )
    return target


@pytest.fixture
def target() -> HostedAuthTestTarget:
    value = _load_target()
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def mcp(target: HostedAuthTestTarget) -> PublicMcpHttpClient:
    return PublicMcpHttpClient(target.mcp_url)


def _json_payload(response: HttpResponse) -> dict[str, Any] | None:
    if not response.body:
        return None
    try:
        return json.loads(response.body)
    except json.JSONDecodeError:
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        for item in reversed(data):
            try:
                return json.loads(item)
            except json.JSONDecodeError:
                continue
    return None


def _result(response: HttpResponse) -> Any:
    assert response.status < 400, f"MCP HTTP failure: {response.status}"
    payload = _json_payload(response)
    assert payload is not None and "error" not in payload, "MCP returned JSON-RPC error"
    result = payload.get("result", {})
    assert not result.get("isError"), "MCP tool returned an error result"
    structured = result.get("structuredContent")
    assert structured is not None, "MCP result omitted structuredContent"
    return structured.get("result", structured)


def _public_error_key(response: HttpResponse, *secrets: str) -> tuple[int, str, str]:
    assert len(response.body) <= MAX_PUBLIC_ERROR_BYTES
    material = response.text + "\n" + "\n".join(response.headers.values())
    for secret in secrets:
        assert secret and secret not in material
    payload = _json_payload(response)
    assert payload is not None, "denial must use bounded JSON"
    tool_error = isinstance(payload.get("result"), dict) and payload["result"].get("isError")
    assert response.status in DENIAL_STATUSES or tool_error or "error" in payload
    if tool_error:
        result = payload["result"]
        content = result.get("content", [])
        messages = [item.get("text", "") for item in content if isinstance(item, dict)]
        error = " ".join(messages).strip()
        code = "mcp_tool_denied"
        public_status = 403
    else:
        assert set(payload) <= {"error", "code", "request_id"}
        error = payload.get("error", "")
        code = payload.get("code", "")
        public_status = response.status
    assert isinstance(error, str) and isinstance(code, str)
    assert len(error) <= 160 and len(code) <= 80
    assert "traceback" not in material.lower()
    assert "tenant" not in error.lower()
    assert "actor" not in error.lower()
    return public_status, code, error


def _assert_same_public_error(first: HttpResponse, second: HttpResponse, *secrets: str) -> None:
    assert _public_error_key(first, *secrets) == _public_error_key(second, *secrets)


def _token(
    target: HostedAuthTestTarget,
    principal: str = "member_a",
    *,
    claims: Mapping[str, object] | None = None,
    headers: Mapping[str, object] | None = None,
    signature: str | None = None,
) -> str:
    return target.token(principal, claims=claims or {}, headers=headers or {}, signature=signature)


def _seed_private_graph(mcp: PublicMcpHttpClient, token_a: str) -> dict[str, Any]:
    source = _result(
        mcp.call_tool(
            "artifact_create",
            {
                "name": "private.txt",
                "content_base64": base64.b64encode(PRIVATE_MARKER.encode()).decode(),
                "media_type": "text/plain",
                "reason": "hosted auth negative fixture",
            },
            token=token_a,
        )
    )
    target = _result(
        mcp.call_tool(
            "artifact_create",
            {
                "name": "target.txt",
                "content_base64": base64.b64encode(b"private target").decode(),
                "media_type": "text/plain",
                "reason": "hosted auth negative fixture",
            },
            token=token_a,
        )
    )
    _result(
        mcp.call_tool(
            "graph_link",
            {
                "source_artifact_id": source["artifact"]["id"],
                "target_artifact_id": target["artifact"]["id"],
                "edge_type": "private-edge",
            },
            token=token_a,
        )
    )
    return {
        "artifact_id": source["artifact"]["id"],
        "version_id": source["version"]["id"],
        "chunk_id": source["chunks"][0]["id"],
        "target_id": target["artifact"]["id"],
    }


@pytest.mark.integration
def test_missing_and_malformed_bearer_fails_closed(mcp: PublicMcpHttpClient) -> None:
    cases = [
        (None, None),
        (None, "Basic dXNlcjpwYXNz"),
        (None, "Bearer"),
        (None, "Bearer "),
        (None, "Bearer one.two"),
        (None, "Bearer one.two.three.four"),
    ]
    responses = [
        mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=token, authorization=auth)
        for token, auth in cases
    ]
    first = _public_error_key(responses[0])
    assert all(_public_error_key(response) == first for response in responses[1:])


@pytest.mark.integration
def test_jwt_issuer_audience_signature_algorithm_kid_and_time_confusion_denied(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    future = int(time.time()) + 3_600
    cases = [
        _token(target, claims={"iss": "https://evil.example"}),
        _token(target, claims={"aud": "wrong-audience"}),
        _token(target, signature="invalid-signature"),
        _token(target, headers={"alg": "none"}),
        _token(target, headers={"alg": "HS256"}),
        _token(target, headers={"kid": "unknown-key"}),
        _token(target, claims={"exp": 0}),
        _token(target, claims={"nbf": future}),
        _token(target, claims={"exp": "9999999999"}),
        _token(target, claims={"iat": future}),
    ]
    for invalid in cases:
        _public_error_key(
            mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=invalid), invalid
        )


@pytest.mark.integration
def test_tenant_actor_claims_headers_query_and_mcp_arguments_cannot_select_context(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    token_a = _token(target)
    token_b = _token(target, "member_b")
    fixture = _seed_private_graph(mcp, token_a)
    forged = {
        "X-Folio-Tenant-ID": TENANT_B,
        "X-Folio-Actor-ID": ACTOR_B,
        "X-Tenant-ID": TENANT_B,
        "X-Forwarded-User": ACTOR_B,
        "X-Forwarded-Email": "actor-b@example.test",
        "X-Forwarded-For": "203.0.113.9",
        "X-Forwarded-Proto": "https",
    }

    response = mcp.call_tool(
        "artifact_read",
        {"artifact_id": fixture["artifact_id"]},
        token=token_a,
        extra_headers=forged,
        query="?tenant_id=tenant-b&actor_id=actor-b",
        initialize_meta={"tenant_id": TENANT_B, "actor_id": ACTOR_B, "role": "platform-admin"},
    )
    assert PRIVATE_MARKER in response.text

    forged_claims = _token(
        target,
        claims={"tenant_id": TENANT_B, "actor_id": ACTOR_B, "role": "platform-admin"},
    )
    response = mcp.call_tool(
        "artifact_read",
        {"artifact_id": fixture["artifact_id"]},
        token=forged_claims,
        extra_headers=forged,
    )
    assert PRIVATE_MARKER in response.text

    response = mcp.call_tool(
        "artifact_read",
        {
            "artifact_id": fixture["artifact_id"],
            "tenant_id": TENANT_A,
            "actor_id": ACTOR_A,
        },
        token=token_b,
        extra_headers={"X-Folio-Tenant-ID": TENANT_A, "X-Folio-Actor-ID": ACTOR_A},
    )
    _public_error_key(response, token_b, PRIVATE_MARKER, TENANT_A, ACTOR_A)


@pytest.mark.integration
def test_unmapped_ambiguous_disabled_and_revoked_membership_are_indistinguishable(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    responses = [
        mcp.call_tool(
            "artifact_search",
            {"query": PRIVATE_MARKER},
            token=_token(target, principal),
        )
        for principal in ("unmapped", "ambiguous", "disabled", "revoked")
    ]
    first = _public_error_key(responses[0])
    assert all(_public_error_key(response) == first for response in responses[1:])


@pytest.mark.integration
def test_cross_tenant_artifact_chunk_version_search_grep_graph_and_write_ids_do_not_leak(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    token_a = _token(target)
    token_b = _token(target, "member_b")
    fixture = _seed_private_graph(mcp, token_a)
    unknown = {
        "artifact_id": "artifact-that-does-not-exist-0001",
        "version_id": "version-that-does-not-exist-0001",
        "chunk_id": "chunk-that-does-not-exist-0001",
        "target_id": "artifact-that-does-not-exist-0002",
    }

    read_other = mcp.call_tool(
        "artifact_read", {"artifact_id": fixture["artifact_id"]}, token=token_b
    )
    read_unknown = mcp.call_tool(
        "artifact_read", {"artifact_id": unknown["artifact_id"]}, token=token_b
    )
    _assert_same_public_error(read_other, read_unknown, token_b, PRIVATE_MARKER)

    chunk_other = mcp.call_tool(
        "artifact_read_chunk", {"chunk_id": fixture["chunk_id"]}, token=token_b
    )
    chunk_unknown = mcp.call_tool(
        "artifact_read_chunk", {"chunk_id": unknown["chunk_id"]}, token=token_b
    )
    _assert_same_public_error(chunk_other, chunk_unknown, token_b, PRIVATE_MARKER)

    version_other = mcp.call_tool(
        "artifact_read",
        {"artifact_id": fixture["artifact_id"], "version_id": fixture["version_id"]},
        token=token_b,
    )
    version_unknown = mcp.call_tool(
        "artifact_read",
        {"artifact_id": unknown["artifact_id"], "version_id": unknown["version_id"]},
        token=token_b,
    )
    _assert_same_public_error(version_other, version_unknown, token_b, PRIVATE_MARKER)

    versions_other = mcp.call_tool(
        "artifact_versions", {"artifact_id": fixture["artifact_id"]}, token=token_b
    )
    versions_unknown = mcp.call_tool(
        "artifact_versions", {"artifact_id": unknown["artifact_id"]}, token=token_b
    )
    _assert_same_public_error(versions_other, versions_unknown, token_b, PRIVATE_MARKER)

    for tool, arguments in (
        ("artifact_search", {"query": PRIVATE_MARKER}),
        ("artifact_grep", {"pattern": PRIVATE_MARKER}),
    ):
        response = mcp.call_tool(tool, arguments, token=token_b)
        assert response.status < 400
        assert PRIVATE_MARKER not in response.text

    graph_other = mcp.call_tool(
        "graph_traverse", {"start_artifact_id": fixture["artifact_id"]}, token=token_b
    )
    graph_unknown = mcp.call_tool(
        "graph_traverse", {"start_artifact_id": unknown["artifact_id"]}, token=token_b
    )
    _assert_same_public_error(graph_other, graph_unknown, token_b, PRIVATE_MARKER)

    write_other = mcp.call_tool(
        "artifact_write",
        {
            "artifact_id": fixture["artifact_id"],
            "parent_version_id": fixture["version_id"],
            "content_base64": base64.b64encode(b"attacker write").decode(),
        },
        token=token_b,
    )
    write_unknown = mcp.call_tool(
        "artifact_write",
        {
            "artifact_id": unknown["artifact_id"],
            "parent_version_id": unknown["version_id"],
            "content_base64": base64.b64encode(b"attacker write").decode(),
        },
        token=token_b,
    )
    _assert_same_public_error(write_other, write_unknown, token_b, PRIVATE_MARKER)

    link_other = mcp.call_tool(
        "graph_link",
        {
            "source_artifact_id": fixture["artifact_id"],
            "target_artifact_id": fixture["target_id"],
            "edge_type": "attacker-edge",
        },
        token=token_b,
    )
    link_unknown = mcp.call_tool(
        "graph_link",
        {
            "source_artifact_id": unknown["artifact_id"],
            "target_artifact_id": unknown["target_id"],
            "edge_type": "attacker-edge",
        },
        token=token_b,
    )
    _assert_same_public_error(link_other, link_unknown, token_b, PRIVATE_MARKER)


@pytest.mark.integration
def test_jwks_rotation_accepts_new_key_without_accepting_unknown_signature(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    token_a = _token(target)
    _seed_private_graph(mcp, token_a)
    target.rotate_jwks()
    rotated = _token(target)
    assert mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=rotated).status < 400
    unknown_signature = _token(target, signature="invalid-after-rotation")
    _public_error_key(
        mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=unknown_signature),
        unknown_signature,
    )


@pytest.mark.integration
def test_jwks_fetch_failure_fails_closed_without_content_or_token_leak(
    target: HostedAuthTestTarget, mcp: PublicMcpHttpClient
) -> None:
    token_a = _token(target)
    _seed_private_graph(mcp, token_a)
    target.fail_jwks_fetch()
    response = mcp.call_tool("artifact_read", {"artifact_id": "unknown"}, token=token_a)
    _public_error_key(response, token_a, PRIVATE_MARKER, TENANT_A, ACTOR_A)


def test_public_error_helper_rejects_unbounded_or_identity_enumerating_payloads() -> None:
    safe = HttpResponse(
        404, {"Content-Type": "application/json"}, b'{"code":"not_found","error":"not found"}'
    )
    assert _public_error_key(safe) == (404, "not_found", "not found")
    unsafe = HttpResponse(
        404,
        {"Content-Type": "application/json"},
        b'{"code":"not_found","error":"tenant tenant-a actor actor-a"}',
    )
    with pytest.raises(AssertionError):
        _public_error_key(unsafe, TENANT_A, ACTOR_A)


def test_public_mcp_request_does_not_put_bearer_in_json_arguments() -> None:
    token = "header.payload.signature"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "artifact_read", "arguments": {"artifact_id": "one"}},
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    assert token not in encoded
