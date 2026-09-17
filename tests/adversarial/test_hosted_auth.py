"""Adversarial checks against the production hosted HTTP/MCP boundary."""

from __future__ import annotations

import base64
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.hosted_auth_negative_target import (
    ISSUER,
    PRINCIPALS,
    HostedAuthNegativeTarget,
    factory,
)
from tests.test_hosted_auth_negative import (
    ACTOR_A,
    PRIVATE_MARKER,
    TENANT_A,
    HttpResponse,
    PublicMcpHttpClient,
    _assert_same_public_error,
    _public_error_key,
    _seed_private_graph,
    _token,
)


@pytest.fixture
def target() -> HostedAuthNegativeTarget:
    value = factory()
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def mcp(target: HostedAuthNegativeTarget) -> PublicMcpHttpClient:
    return PublicMcpHttpClient(target.mcp_url)


def _request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes = b"",
) -> HttpResponse:
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return HttpResponse(response.status, dict(response.headers.items()), response.read())
    except HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers.items()), exc.read())


def _deny(response: HttpResponse, *secrets: str) -> None:
    _public_error_key(response, *secrets)


@pytest.mark.integration
def test_forged_signature_and_expired_tokens_are_rejected(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    valid = _token(target)
    header, payload, signature = valid.split(".")
    forged = ".".join((header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]))
    _deny(
        mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=forged),
        forged,
    )
    expired = _token(target, claims={"exp": int(time.time()) - 60})
    _deny(
        mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=expired),
        expired,
    )


@pytest.mark.integration
def test_wrong_issuer_audience_and_scope_are_rejected(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    for invalid in (
        _token(target, claims={"iss": "https://evil.example"}),
        _token(target, claims={"aud": "wrong-audience"}),
    ):
        _deny(
            mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=invalid),
            invalid,
        )

    limited = _token(target, "limited")
    response = mcp.call_tool(
        "artifact_create",
        {
            "name": "blocked.txt",
            "content_base64": base64.b64encode(b"blocked").decode(),
        },
        token=limited,
    )
    status, code, error = _public_error_key(response, limited)
    assert (status, code) == (403, "mcp_tool_denied")
    assert "operation not permitted" in error


@pytest.mark.integration
def test_authenticated_principal_cannot_cross_tenant_acl_boundary(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    token_a = _token(target)
    fixture = _seed_private_graph(mcp, token_a)
    token_b = _token(target, "member_b")
    other = mcp.call_tool(
        "artifact_read", {"artifact_id": fixture["artifact_id"]}, token=token_b
    )
    unknown = mcp.call_tool(
        "artifact_read", {"artifact_id": "artifact-that-does-not-exist-0001"}, token=token_b
    )
    _assert_same_public_error(other, unknown, token_b, PRIVATE_MARKER, TENANT_A, ACTOR_A)


@pytest.mark.integration
def test_existing_bearer_is_revoked_by_membership_status_change(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    token = _token(target)
    assert mcp.call_tool("artifact_list", {}, token=token).status < 400
    target.memberships.set_status(ISSUER, PRINCIPALS["member_a"][0], "revoked")
    _deny(mcp.call_tool("artifact_list", {}, token=token), token)


@pytest.mark.integration
def test_cookie_cannot_create_an_mcp_session(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    response = mcp.call_tool(
        "artifact_search",
        {"query": PRIVATE_MARKER},
        extra_headers={"Cookie": "folio_session=forged-session"},
    )
    _deny(response, "forged-session", PRIVATE_MARKER)


@pytest.mark.integration
def test_rotation_accepts_new_key_and_jwks_failure_fails_closed(
    target: HostedAuthNegativeTarget, mcp: PublicMcpHttpClient
) -> None:
    token = _token(target)
    _seed_private_graph(mcp, token)
    target.rotate_jwks()
    rotated = _token(target)
    assert mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=rotated).status < 400
    invalid = _token(target, signature="invalid-after-rotation")
    _deny(mcp.call_tool("artifact_search", {"query": PRIVATE_MARKER}, token=invalid), invalid)
    target.fail_jwks_fetch()
    _deny(
        mcp.call_tool("artifact_read", {"artifact_id": "unknown"}, token=rotated),
        rotated,
        PRIVATE_MARKER,
    )


@pytest.mark.integration
def test_auth_audit_log_excludes_bearer_cookie_and_request_content(
    target: HostedAuthNegativeTarget,
) -> None:
    token = _token(target)
    response = _request(
        target.mcp_url.removesuffix("/mcp") + "/auth/logout",
        method="POST",
        headers={
            "Origin": "https://attacker.example",
            "Cookie": f"folio_session={PRIVATE_MARKER}",
            "Authorization": f"Bearer {token}",
        },
    )
    status, code, _ = _public_error_key(response, token, PRIVATE_MARKER)
    assert (status, code) == (403, "csrf_failed")
    audit = target.audit_stream.getvalue()
    assert token not in audit
    assert PRIVATE_MARKER not in audit
    assert "attacker.example" not in audit
