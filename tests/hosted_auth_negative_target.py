"""Deterministic public hosted-auth target for negative seam tests.

This is a test-only deployment. It uses the production OIDC verifier,
membership store, browser identity adapter, MCP server, and HTTP boundary over
one real loopback HTTP endpoint. The fixture owns an isolated database and
never logs bearer material.
"""

from __future__ import annotations

import io
import json
import logging
import socket
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.request import Request

import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.responses import JSONResponse

from folio_lattice.auth import (
    JwksClient,
    Membership,
    MembershipError,
    MembershipStore,
    OidcAuthenticator,
    OidcVerifier,
)
from folio_lattice.identity import HostedIdentityConfig, HttpHostedIdentityAdapter
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.server import FolioHttpApp
from folio_lattice.service import FolioLattice
from folio_lattice.sessions import SessionStore

ISSUER = "https://deterministic-hosted-auth.example"
JWKS_URL = f"{ISSUER}/jwks.json"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
CLIENT_ID = "folio-hosted-auth-negative"
AUDIENCE = "folio-hosted-auth-negative-mcp"
REDIRECT_URI = "https://folio.test/auth/callback"
OIDC_ALGORITHM = "RS256"
JWKS_CACHE_SECONDS = 300.0

_PRIVATE_KEYS = {
    "key-1": """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDhyCJC+w95pMAZ
kRBTF8FvqWWuz7y26NqXk6EV4ztAHP398D7eE/hd3ilkboLpzpPsZ7rud6tgsafa
FB0bKf6AHOEy108ZWS2ZPnGzYg4y8guJmoTk8Sx0Yl5jinN4iE+GoCrEPAsy2K8j
1kD6/MowGfssx8d/yDAlXL/36lkWZMixaRZazrHUrZqgThAZ7w4fXKaT0H5H/uDn
1zAIfSaKm3xBDZOKC8EDqepKvRrkJSlwKrDF/P2nr8+CT776ZVdkq27Gadnwyov2
0AKag+rCMwHsAkulMM+dr0j3cXk6Gxq2qsz7Nm1cGvd3nuStuTJ6d1p+US0BOU/f
zpucoRcXAgMBAAECggEAA4OV5Oz7ZK1HwB4d3u0zuYUta/cXvNEK9ej6TNjohyrs
WRNwJwywhOV8R2/g4bqxWrCSnDuIk0ywjBhcC5wvtda9OolXVdGpgAUUx/HyvqA9
xbp97wJB6t3NEkeJXIrikfnUMJGuqu/saeZfxIhCrVT6L8w8MqTu+uXipv0ivdJ0
QR3YWpq5ZqgFn2XkEKYRuBn23C0Si7i0dzrfyIrbfS3/nAX+9Z6paa5Ig6WTqybw
6+5NiOxVVCyz13yY/+/uZYsWOtxKYDJhERsBda+r4FLvTwvM5csdvE6513YSwH76
+JwqH6lJgToRkupErnSKo5iWB5/ny+ssliPxKq/jYQKBgQD0TJavoHNA/jG8aVEz
s1Y7Lho8+8Ls42QGWIjeiRSvwJ7yzeuihvsyv/52HPs6lVSH8ixDiroKRUZ6TaUv
X4K6oQWFTk1P076N27zDqil8pAwkb3ZV29HQK/cvMVpBUgQ3fputMi0AG8f6DGrX
JxhAKxiDyXMa4HiUwnFjFesmqwKBgQDsmH+17Mjd6zWGA+jq0nwesheBJqB2Fnyw
aVKU6Cg0hVizG2NznkP/nxpWlKLtLQfT+Kq7u0truj8p/VcdBhI2/4LU5uoIfDrc
Gg2VGjvrm/C+3yeVApdV7RXuDmT4A8OyIS+RRavE9yUcSx4RXCgzh8jYqVavyauF
k7G5X0sBRQKBgF6p6t4FK4Psu/MJUFjbTjfCZpJo8CCBAHphBjBNKAHufukRGBSz
f1UsBntYQZVy1f2TvskxUWO3clbkDXUs6mhNCumb7ONY5obrtdqP7mGI49eehVlB
w6yJzM+xrQqQsGecnNBhGATpvvTKqP/T+1aqHGa8weiRQMhMDEnb7XHfAoGAKYFj
Ph4I6u19WUJMVQ2R2qyxdOW61pyBSU1gwGCt1PDjq6ANZVYJZcmajD/NCUCSE/yW
rxJfW8mzlQEtjBjpjx5p7EGKIuzwQnaLlXGhu2aC6Gkrf3eR5vANndKGof+/D/vE
ZX7McGRO7VS8NJ1vLAMRF/k1DOebIHbVgciW1uUCgYEA0DHBY3exygKqORVE3ZCI
hB+dbJwe80pxB8zumhoUAu4Yz6HPkIDSYD2mtiQidhdmzR83B/Lx0QVHjwpm5fd9
Azn/TZmSPCu6fo7Ke7GSMw2YWZoMtnHaalp+wIxcxaPtUsC7MOFdGttytw0VjDpv
4qmev4E5o0edtoT+A9DJEkA=
-----END PRIVATE KEY-----""",
    "key-2": """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCzM6Bakl2kGH5g
wM5f0u44rix47LCMbyrTimOpWS6XuPx4+vB8I5QQLQgnOBvsYywF2H5sIGAZGuvW
L1PFMMQKoEE727+4ajOKDQbx4V8VS2p1w93MM8OUPLfIDAayq0vceLUtTWU4zaqi
7hkzMM2loh0G+lV1/HcVKVZS/euexfItJiSGBY1imlpnlLV/h6cW2sejNDBXnDyt
Q6IdhYn203Z7IwopZ3bN6WVyXA83ZsRKjPSBwbrU6CUfURLsAsa/TR6j1f4c+OnF
w1l+12SDIcK5OkHg6rzlCiuOKrZHYfu2mM/Im2+BpS9r0AB7epV7mPo1PFAOMQ20
AC81TMzjAgMBAAECggEARYmYBO1dBus2RycqLgb0x+vWeAoorCYLMVE3QSTJLrjq
x6tgnFtV/jzrOATO2RjWoWIUFLMvdy56K3/r+s3klNcA2VB5gf88BqrtcfjpY6MD
KbWaoL3JfAFfs3HvO2+7HU99xpmM2ND+EQFhp/qdIlPY/bcwaHtSXlTPGZ9MyiwR
P2+tezEGm1VI2PArY9d2+BzRUKvGd645TcnysCVVRq8RgeZNS1MyTIq0PV4n6jvj
fwXG5IjkZ5DIuz8Oz2tAO9DiDgiooSR1Hq3M8egmlkuJjDPU94/iDk6HfxTHuk5/
Al8tBlERPcfh/n9N7JD9YTZH1P9t0GRGPV2+BP0T8QKBgQDi6Hc2rJPOab1i/ZUI
u7jOLI7JK7JiNsdg/QlsRZbH9HZReZB2dO1sLYiCglgujhPwgR+6Pw36g/yHl/vL
gQYW4VlyIRUf7wZ36IKZUmwKXkaw6G+CEInPlnIDQ5C7YUf8mAqojBZwfkKc0h2E
fdf6BiV/a2/jM7p8Eu58hk6zqwKBgQDKLVmbTkfSPFb1wst09NKx0kU3CQ6X3Y96
9a7yOeYv4mjR2PTS2HWYjllzFqVuZzqrpxt9I5GbPy5u+JKiKYY354YcpE7JxEdS
jmSkMRRDIzvZngC+eJ7riI0WFcpCvgPBr2O6r1OVmU2ydPYyd3howxjZ1Ts9FwB+
OYQE0YeTqQKBgGKuoxoeF/IPPpRMoII50feonTiUTnI0TKW7plt2MEsp4EMy0UcT
NyZy7wmDUoJ8u+M+5OOFBLlMqYj26kTpChLtUo82IA/RTkjbz+CKXf5sXeYWUFiK
hMTJMzCEM++qMTqDjS8cLa4i2ymEn929NS7BeZFe0jxHhPTs7tctOhEdAoGBAMES
0fHordnt2bXVEutcKiG7BnJqac9JvQ3Vtf6IoHS2KRfNsu/v202XE4E+7Tkjx/nJ
Gg1FfHXfvn4nUBEgypZ0ubR8jOlOUjZa2W2bmRgMe8l0hI1hL5MK0oF2ybM3Nusu
jXTonk/NGVAFNmA3i3uwZPkSEwJiBwyD7LmVR34RAoGAK6Uhu2TUiWZ1g0hEe5UB
VfNHtgD2AaFNbMyLasuhy76NsoMIqXcQWbYz+PwjKsyraYJAlWNjMvZnvRRCZp6S
D7ON6BxeC+OdJzYRipJD3ESD7r7y2QmKDzx0DkH0bKpheYaYUjguxueFKaakELe0
ROZobUhEnN1Ie6yZVxZI70o=
-----END PRIVATE KEY-----""",
}

PRINCIPALS = {
    "member_a": ("member-a", "tenant-a", "actor-a"),
    "member_b": ("member-b", "tenant-b", "actor-b"),
    "limited": ("limited", "tenant-a", "limited"),
    "unmapped": ("unmapped", "tenant-a", "unmapped"),
    "ambiguous": ("ambiguous", "tenant-a", "ambiguous"),
    "disabled": ("disabled", "tenant-a", "disabled"),
    "revoked": ("revoked", "tenant-a", "revoked"),
}
ALL_SCOPES = {
    "artifact:read",
    "artifact:write",
    "artifact:search",
    "graph:read",
    "graph:write",
}
LIMITED_SCOPES = frozenset({"artifact:read", "artifact:search", "graph:read"})


class _BytesResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _BytesResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class _CacheClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def expire(self) -> None:
        self.value += JWKS_CACHE_SECONDS + 1


class _JwksProvider:
    def __init__(self) -> None:
        self.keys = {
            kid: serialization.load_pem_private_key(pem.encode(), password=None)
            for kid, pem in _PRIVATE_KEYS.items()
        }
        self.active_kid = "key-1"
        self.failed = False
        self.id_token = ""

    def __call__(self, request: Request, *, timeout: float) -> _BytesResponse:
        if timeout <= 0:
            raise OSError("invalid fixture timeout")
        if request.full_url == JWKS_URL:
            if self.failed:
                raise OSError("fixture JWKS failure")
            return _BytesResponse(json.dumps({"keys": [self.jwk()]}).encode())
        if request.full_url == TOKEN_ENDPOINT:
            return _BytesResponse(json.dumps({"id_token": self.id_token}).encode())
        raise OSError("unknown fixture provider URL")

    def jwk(self) -> dict[str, str]:
        value = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[self.active_kid].public_key())
        )
        value.update({"kid": self.active_kid, "alg": OIDC_ALGORITHM, "use": "sig"})
        return value


class _FixtureMembershipStore(MembershipStore):
    """Represent an ambiguous server mapping without weakening production lookup."""

    def lookup(self, issuer: str, subject: str) -> Membership | None:
        if issuer == ISSUER and subject == PRINCIPALS["ambiguous"][0]:
            raise MembershipError("ambiguous fixture membership")
        return super().lookup(issuer, subject)


class _HttpServer:
    def __init__(self, app: Any) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        self.port = port
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="on",
                log_level="error",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("hosted auth fixture HTTP server stopped")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)
        raise RuntimeError("hosted auth fixture HTTP server did not start")

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


class HostedAuthNegativeTarget:
    mcp_url: str

    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="folio-hosted-auth-negative-")
        root = Path(self.directory.name)
        db_path = root / "folio.db"
        self.provider = _JwksProvider()
        self.cache_clock = _CacheClock()
        self.jwks = JwksClient(
            JWKS_URL,
            cache_seconds=JWKS_CACHE_SECONDS,
            opener=self.provider,
            monotonic=self.cache_clock,
        )
        self.config = HostedIdentityConfig(
            issuer=ISSUER,
            jwks_url=JWKS_URL,
            authorization_endpoint=AUTHORIZATION_ENDPOINT,
            token_endpoint=TOKEN_ENDPOINT,
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
        )
        self.identity_adapter = HttpHostedIdentityAdapter(
            self.config,
            opener=self.provider,
            jwks=self.jwks,
        )
        self.identity_adapter.warm_up()
        self._seed_adapter_identity()
        memberships = _FixtureMembershipStore(db_path)
        for name in ("member_a", "member_b", "ambiguous", "disabled", "revoked"):
            subject, tenant, actor = PRINCIPALS[name]
            memberships.add(
                issuer=ISSUER,
                subject=subject,
                tenant_id=tenant,
                actor_id=actor,
                scopes=ALL_SCOPES,
            )
        subject, tenant, actor = PRINCIPALS["limited"]
        memberships.add(
            issuer=ISSUER,
            subject=subject,
            tenant_id=tenant,
            actor_id=actor,
            scopes=LIMITED_SCOPES,
        )
        memberships.set_status(ISSUER, PRINCIPALS["disabled"][0], "disabled")
        memberships.set_status(ISSUER, PRINCIPALS["revoked"][0], "revoked")
        self.memberships = memberships
        verifier = OidcVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks=self.jwks,
            memberships=memberships,
        )
        verifier.warm_up()
        self.verifier = verifier
        authenticator = OidcAuthenticator(verifier)
        service = FolioLattice(db_path, root / "blobs")
        mcp_server = build_mcp_server(service).streamable_http_app(
            json_response=True,
            host="127.0.0.1",
        )
        session_store = SessionStore(db_path, memberships)
        self.audit_stream = io.StringIO()
        self.audit_logger = logging.Logger("folio-hosted-auth-negative")
        self.audit_logger.addHandler(logging.StreamHandler(self.audit_stream))
        inner = FolioHttpApp(
            mcp_server,
            service,
            JSONResponse({"error": "not found"}, status_code=404),
            deployment_mode="hosted",
            authenticator=authenticator,
            session_store=session_store,
            identity_adapter=self.identity_adapter,
            auth_redirect_uri=REDIRECT_URI,
            control_origin="https://folio.test",
            audit_logger=self.audit_logger,
        )
        try:
            self.server = _HttpServer(inner)
        except PermissionError as exc:
            self.directory.cleanup()
            pytest.skip(f"environment disallows loopback HTTP sockets: {exc}")
        self.mcp_url = f"http://127.0.0.1:{self.server.port}/mcp"

    def _seed_adapter_identity(self) -> None:
        now = int(time.time())
        self.provider.id_token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": "adapter-subject",
                "aud": CLIENT_ID,
                "azp": CLIENT_ID,
                "nonce": "fixture-nonce",
                "iat": now,
                "nbf": now - 1,
                "exp": now + 600,
            },
            self.provider.keys["key-1"],
            algorithm=OIDC_ALGORITHM,
            headers={"kid": "key-1", "typ": "JWT"},
        )
        self.identity_adapter.complete_callback(
            code="fixture-code",
            nonce="fixture-nonce",
            code_verifier="fixture-verifier",
            redirect_uri=REDIRECT_URI,
        )

    def token(
        self,
        principal: str,
        *,
        claims: Mapping[str, object] | None = None,
        headers: Mapping[str, object] | None = None,
        signature: str | None = None,
    ) -> str:
        if principal not in PRINCIPALS:
            raise ValueError("unknown fixture principal")
        subject, _tenant, _actor = PRINCIPALS[principal]
        payload: dict[str, object] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": subject,
            "iat": int(time.time()),
            "nbf": int(time.time()) - 1,
            "exp": int(time.time()) + 600,
        }
        payload.update(claims or {})
        token_headers: dict[str, object] = {"kid": self.provider.active_kid, "typ": "JWT"}
        token_headers.update(headers or {})
        requested_algorithm = token_headers.get("alg", OIDC_ALGORITHM)
        signing_headers = {**token_headers, "alg": OIDC_ALGORITHM}
        token = jwt.encode(
            payload,
            self.provider.keys[self.provider.active_kid],
            algorithm=OIDC_ALGORITHM,
            headers=signing_headers,
        )
        if requested_algorithm != OIDC_ALGORITHM:
            parts = token.split(".")
            assert len(parts) == 3
            parts[0] = jwt.utils.base64url_encode(
                json.dumps(
                    {**token_headers, "alg": requested_algorithm},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).decode()
            token = ".".join(parts)
        if signature is None:
            return token
        parts = token.split(".")
        assert len(parts) == 3
        parts[2] = signature
        return ".".join(parts)

    def rotate_jwks(self) -> None:
        self.provider.active_kid = "key-2"

    def fail_jwks_fetch(self) -> None:
        self.provider.failed = True
        self.cache_clock.expire()

    def close(self) -> None:
        if hasattr(self, "server"):
            self.server.close()
        self.directory.cleanup()


def factory() -> HostedAuthNegativeTarget:
    return HostedAuthNegativeTarget()
