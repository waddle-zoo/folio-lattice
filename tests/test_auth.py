from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.responses import JSONResponse

from folio_lattice.auth import (
    MAX_JWKS_BYTES,
    AuthenticationError,
    JwksClient,
    JwksError,
    MembershipError,
    MembershipStore,
    OidcAuthenticator,
    OidcVerifier,
    Principal,
    bearer_token,
    get_request_principal,
)
from folio_lattice.public_mcp import SignedPrincipalRelay
from folio_lattice.server import FolioHttpApp


class _DeterministicJwks:
    def __init__(self) -> None:
        self.jwks = b""

    def __call__(self, _request: Any, *, timeout: float) -> _DeterministicJwks:
        assert timeout > 0
        return self

    def __enter__(self) -> _DeterministicJwks:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.jwks


class OidcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.issuer = "https://deterministic-issuer.example/"
        cls.audience = "folio-test"
        cls.keys = {
            "key-1": rsa.generate_private_key(public_exponent=65537, key_size=2048),
            "key-2": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.jwks_source = _DeterministicJwks()
        self.memberships = MembershipStore(Path(self.temp.name) / "folio.db")
        self.memberships.add(
            issuer=self.issuer,
            subject="subject-1",
            tenant_id="tenant-a",
            actor_id="actor-a",
            scopes={"artifact:read", "artifact:write"},
        )
        self._serve_keys("key-1")
        self.verifier = OidcVerifier(
            issuer=self.issuer,
            audience=self.audience,
            jwks=JwksClient("https://deterministic-issuer.example/jwks", opener=self.jwks_source),
            memberships=self.memberships,
            now=lambda: 1_000_000,
        )
        self.verifier.warm_up()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _jwk(self, kid: str) -> dict[str, str]:
        value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[kid].public_key()))
        value.update({"kid": kid, "alg": "RS256", "use": "sig"})
        return value

    def _serve_keys(self, *kids: str) -> None:
        self.jwks_source.jwks = json.dumps({"keys": [self._jwk(kid) for kid in kids]}).encode()

    def _token(self, kid: str = "key-1", **claims: Any) -> str:
        payload = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "subject-1",
            "scope": "artifact:read artifact:write",
            "iat": 999_900,
            "nbf": 999_900,
            "exp": 1_000_100,
            **claims,
        }
        return jwt.encode(payload, self.keys[kid], algorithm="RS256", headers={"kid": kid})

    def test_verifies_exact_claims_and_server_mapping(self) -> None:
        self.assertEqual(
            self.verifier.verify(self._token()),
            Principal(
                "tenant-a",
                "actor-a",
                self.issuer,
                "subject-1",
                frozenset({"artifact:read", "artifact:write"}),
            ),
        )
        for claims in (
            {"iss": "https://wrong.example"},
            {"aud": "wrong-audience"},
            {"exp": 999_999},
            {"nbf": 1_000_001},
        ):
            with self.assertRaises(AuthenticationError):
                self.verifier.verify(self._token(**claims))
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(jwt.encode({"iss": self.issuer}, b"x" * 32, algorithm="HS256"))

    def test_server_scopes_ignore_untrusted_token_claims(self) -> None:
        self.assertEqual(
            self.verifier.verify(self._token(scope="artifact:read graph:read")).scopes,
            frozenset({"artifact:read", "artifact:write"}),
        )
        for scope in (
            None,
            "",
            " artifact:read",
            "artifact:read ",
            "artifact:\tread",
            "artifact:\\read",
        ):
            with self.subTest(scope=scope):
                self.assertEqual(
                    self.verifier.verify(self._token(scope=scope)).scopes,
                    frozenset({"artifact:read", "artifact:write"}),
                )
        missing_scope = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "subject-1",
            "iat": 999_900,
            "nbf": 999_900,
            "exp": 1_000_100,
        }
        self.assertEqual(
            self.verifier.verify(
                jwt.encode(
                    missing_scope,
                    self.keys["key-1"],
                    algorithm="RS256",
                    headers={"kid": "key-1"},
                )
            ).scopes,
            frozenset({"artifact:read", "artifact:write"}),
        )

    def test_signature_and_key_rotation_are_real_jwks_checks(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(self._token(kid="key-1", sub="unknown-subject"))
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(
                jwt.encode(
                    {
                        "iss": self.issuer,
                        "aud": self.audience,
                        "sub": "subject-1",
                        "exp": 1_000_100,
                    },
                    self.keys["key-2"],
                    algorithm="RS256",
                    headers={"kid": "key-1"},
                )
            )
        self._serve_keys("key-2")
        self.assertEqual(self.verifier.verify(self._token(kid="key-2")).actor_id, "actor-a")
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(self._token(kid="key-2", exp=999_999))

    def test_membership_mapping_is_immutable_and_status_is_authoritative(self) -> None:
        with self.assertRaises(MembershipError):
            self.memberships.add(
                issuer=self.issuer,
                subject="subject-1",
                tenant_id="tenant-b",
                actor_id="actor-b",
            )
        self.memberships.set_status(self.issuer, "subject-1", "disabled")
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(self._token())

    def test_membership_scopes_are_server_owned_and_seed_updates_them(self) -> None:
        self.memberships.set_scopes(self.issuer, "subject-1", {"graph:read"})
        self.assertEqual(self.memberships.lookup(self.issuer, "subject-1").scopes, {"graph:read"})
        seed = Path(self.temp.name) / "memberships.json"
        seed.write_text(
            json.dumps(
                {
                    "memberships": [
                        {
                            "issuer": self.issuer,
                            "subject": "subject-1",
                            "tenant_id": "tenant-a",
                            "actor_id": "actor-a",
                            "scopes": ["artifact:read"],
                        }
                    ]
                }
            )
        )
        self.memberships.seed_file(seed)
        self.assertEqual(
            self.memberships.lookup(self.issuer, "subject-1").scopes,
            {"artifact:read"},
        )

    def test_membership_seed_and_validation_fail_closed(self) -> None:
        self.assertEqual(Principal("t", "a", "i", "s").actor, "a")
        with self.assertRaisesRegex(MembershipError, "already exists"):
            self.memberships.add(
                issuer=self.issuer,
                subject="subject-1",
                tenant_id="tenant-a",
                actor_id="actor-a",
            )
        with self.assertRaisesRegex(MembershipError, "status"):
            self.memberships.set_status(self.issuer, "subject-1", "unknown")
        with self.assertRaisesRegex(MembershipError, "not found"):
            self.memberships.set_status(self.issuer, "missing", "revoked")
        seed = Path(self.temp.name) / "memberships.json"
        seed.write_text(
            json.dumps(
                {
                    "memberships": [
                        {
                            "issuer": self.issuer,
                            "subject": "subject-2",
                            "tenant_id": "tenant-b",
                            "actor_id": "actor-b",
                        }
                    ]
                }
            )
        )
        self.memberships.seed_file(seed)
        self.assertEqual(self.memberships.lookup(self.issuer, "subject-2").tenant_id, "tenant-b")
        seed.write_text("not-json")
        with self.assertRaisesRegex(MembershipError, "invalid"):
            self.memberships.seed_file(seed)

    def test_bearer_and_jwks_input_bounds(self) -> None:
        scope = {"headers": [(b"authorization", b"Bearer signed.token.value")]}
        self.assertEqual(bearer_token(scope), "signed.token.value")
        for headers in (
            [],
            [(b"authorization", b"Basic value")],
            [(b"authorization", b"Bearer bad token")],
            [(b"authorization", b"Bearer one"), (b"Authorization", b"Bearer two")],
        ):
            with self.assertRaises(AuthenticationError):
                bearer_token({"headers": headers})

        for body in (b"{}", b'{"keys":{}}', b'{"keys":[]}', b"x" * (MAX_JWKS_BYTES + 1)):
            source = _DeterministicJwks()
            source.jwks = body
            with self.assertRaises(JwksError):
                JwksClient("https://issuer.example/jwks", opener=source).refresh()
        with self.assertRaises(ValueError):
            JwksClient("https://issuer.example/jwks", timeout_seconds=0)

    def test_token_shapes_and_authenticator_are_bounded(self) -> None:
        for token in ("", "not-a-jwt"):
            with self.assertRaises(AuthenticationError):
                self.verifier.verify(token)
        self.assertEqual(
            self.verifier.verify(self._token(aud=["other", self.audience])).tenant_id,
            "tenant-a",
        )
        for claims in ({"iat": 1_000_001}, {"exp": True}, {"sub": ""}):
            with self.assertRaises(AuthenticationError):
                self.verifier.verify(self._token(**claims))
        authenticator = OidcAuthenticator(self.verifier)
        authenticator.warm_up()
        self.assertTrue(authenticator.ready())
        self.assertEqual(
            authenticator.authenticate(
                {"headers": [(b"authorization", f"Bearer {self._token()}".encode())]}
            ).actor_id,
            "actor-a",
        )
        self.memberships.set_status(self.issuer, "subject-1", "revoked")
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(self._token())


class HttpAuthBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_capability_rechecks_membership_status_and_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memberships = MembershipStore(Path(directory) / "folio.db")
            memberships.add(
                issuer="https://issuer.example",
                subject="subject-reader",
                tenant_id="tenant-a",
                actor_id="actor-a",
                scopes={"artifact:read"},
            )
            relay = SignedPrincipalRelay(
                "http://folio.internal:8000/mcp",
                "renderer-capability-secret-012345678901234567890123456789",
            )
            principal = Principal(
                "tenant-a",
                "actor-a",
                "https://issuer.example",
                "subject-reader",
                frozenset({"artifact:read"}),
            )
            app = FolioHttpApp(
                JSONResponse({"ok": True}),
                SimpleNamespace(health=lambda: {"ready": True, "status": "ok"}),
                JSONResponse({"ok": True}),
                deployment_mode="hosted",
                authenticator=object(),
                session_store=SimpleNamespace(membership_store=memberships),
                principal_relay=relay,
            )
            token = relay.issue(
                principal,
                tool="artifact_read",
                arguments={"artifact_id": "art_1", "version_id": "ver_1"},
            )
            scope = {
                "type": "http",
                "path": "/mcp",
                "headers": [(b"x-folio-internal-principal", token.encode())],
            }
            resolved, _ = app._authenticate_context(scope, allow_session=False)
            self.assertEqual(resolved.actor_id, "actor-a")
            memberships.set_scopes("https://issuer.example", "subject-reader", set())
            with self.assertRaises(AuthenticationError):
                app._authenticate_context(scope, allow_session=False)
            memberships.set_scopes("https://issuer.example", "subject-reader", {"artifact:read"})
            memberships.set_status("https://issuer.example", "subject-reader", "revoked")
            with self.assertRaises(AuthenticationError):
                app._authenticate_context(scope, allow_session=False)

    async def test_health_is_secret_free_and_mcp_is_request_scoped(self) -> None:
        principal = Principal("tenant-a", "actor-a", "https://issuer", "subject")

        class Authenticator:
            def authenticate(self, scope: Any) -> Principal:
                if not any(name.lower() == b"authorization" for name, _ in scope["headers"]):
                    raise AuthenticationError("authorization is required")
                return principal

            def ready(self) -> bool:
                return True

        async def downstream(scope: Any, receive: Any, send: Any) -> None:
            value = get_request_principal()
            await JSONResponse(
                {
                    "tenant_id": value.tenant_id if value else None,
                    "actor_id": value.actor_id if value else None,
                }
            )(scope, receive, send)

        app = FolioHttpApp(
            downstream,
            SimpleNamespace(health=lambda: {"status": "ok", "ready": True}),
            downstream,
            deployment_mode="hosted",
            authenticator=Authenticator(),
        )

        async def call(
            path: str, headers: list[tuple[bytes, bytes]] | None = None
        ) -> tuple[int, bytes]:
            sent: list[dict[str, Any]] = []

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await app(
                {
                    "type": "http",
                    "path": path,
                    "headers": headers or [],
                    "asgi": {"version": "3.0"},
                    "method": "GET",
                },
                receive,
                send,
            )
            start = next(item for item in sent if item["type"] == "http.response.start")
            body = b"".join(
                item.get("body", b"") for item in sent if item["type"] == "http.response.body"
            )
            return start["status"], body

        status, body = await call("/health")
        self.assertEqual(status, 200)
        self.assertNotIn(b"issuer", body)
        self.assertNotIn(b"subject", body)
        status, _ = await call("/sign-in")
        self.assertEqual(status, 200)
        status, _ = await call("/mcp")
        self.assertEqual(status, 401)
        status, body = await call("/mcp", [(b"authorization", b"Bearer signed-token")])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"tenant_id": "tenant-a", "actor_id": "actor-a"})
        self.assertIsNone(get_request_principal())


if __name__ == "__main__":
    unittest.main()
