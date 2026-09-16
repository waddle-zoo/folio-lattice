from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from folio_lattice.auth import AuthenticationError
from folio_lattice.identity import (
    HostedIdentityConfig,
    HttpHostedIdentityAdapter,
    IdentityClaims,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class _Provider:
    def __init__(self, jwks_url: str, token_endpoint: str, jwks: bytes) -> None:
        self.jwks_url = jwks_url
        self.token_endpoint = token_endpoint
        self.jwks = jwks
        self.id_token = ""
        self.requests: list[tuple[Request, float]] = []

    def __call__(self, request: Request, *, timeout: float) -> _Response:
        self.requests.append((request, timeout))
        url = request.full_url
        if url == self.jwks_url:
            return _Response(self.jwks)
        if url == self.token_endpoint:
            return _Response(json.dumps({"id_token": self.id_token}).encode())
        raise OSError("unexpected provider endpoint")


class HostedIdentityAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = "https://issuer.example"
        self.jwks_url = f"{self.issuer}/jwks.json"
        self.authorization_endpoint = f"{self.issuer}/authorize"
        self.token_endpoint = f"{self.issuer}/token"
        self.client_id = "folio-browser"
        self.redirect_uri = "https://folio.example/auth/callback"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": "idp-key", "alg": "RS256", "use": "sig"})
        self.provider = _Provider(
            self.jwks_url,
            self.token_endpoint,
            json.dumps({"keys": [jwk]}).encode(),
        )
        self.config = HostedIdentityConfig(
            issuer=self.issuer,
            jwks_url=self.jwks_url,
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
        )
        self.adapter = HttpHostedIdentityAdapter(
            self.config,
            opener=self.provider,
            now=lambda: 1_000,
        )
        self.transaction = SimpleNamespace(
            state="state-value",
            nonce="nonce-value",
            code_verifier="verifier-value",
        )

    def _token(self, **overrides: object) -> str:
        claims: dict[str, object] = {
            "iss": self.issuer,
            "sub": "subject-a",
            "aud": self.client_id,
            "azp": self.client_id,
            "nonce": self.transaction.nonce,
            "iat": 1_000,
            "nbf": 999,
            "exp": 1_100,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.key,
            algorithm="RS256",
            headers={"kid": "idp-key", "typ": "JWT"},
        )

    def _complete(self, *, redirect_uri: str | None = None) -> IdentityClaims:
        self.provider.id_token = self._token()
        return self.adapter.complete_callback(
            code="authorization-code",
            nonce=self.transaction.nonce,
            code_verifier=self.transaction.code_verifier,
            redirect_uri=redirect_uri or self.redirect_uri,
        )

    def test_authorization_and_pkce_exchange_use_exact_configured_endpoints(self) -> None:
        self.assertFalse(self.adapter.ready())
        self.assertIsInstance(
            self.adapter.authorization_url(
                state=self.transaction.state,
                nonce=self.transaction.nonce,
                code_challenge="challenge-value",
                redirect_uri=self.redirect_uri,
                return_to="/workspace/safe",
            ),
            str,
        )
        authorization_url = self.adapter.authorization_url(
            state=self.transaction.state,
            nonce=self.transaction.nonce,
            code_challenge="challenge-value",
            redirect_uri=self.redirect_uri,
            return_to="/workspace/safe",
        )
        parsed = urlsplit(authorization_url)
        self.assertEqual(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}", self.authorization_endpoint
        )
        query = parse_qs(parsed.query)
        self.assertEqual(query["client_id"], [self.client_id])
        self.assertEqual(query["redirect_uri"], [self.redirect_uri])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], ["challenge-value"])

        self.assertTrue(self.adapter.ready() is False)
        self.adapter.warm_up()
        identity = self._complete()
        self.assertEqual(identity, IdentityClaims(self.issuer, "subject-a"))
        request, timeout = self.provider.requests[-1]
        self.assertEqual(request.full_url, self.token_endpoint)
        self.assertEqual(timeout, 5)
        assert request.data is not None
        form = parse_qs(request.data.decode("ascii"))
        self.assertEqual(form["redirect_uri"], [self.redirect_uri])
        self.assertEqual(form["client_id"], [self.client_id])
        self.assertEqual(form["code_verifier"], [self.transaction.code_verifier])
        self.assertFalse(hasattr(identity, "scopes"))

    def test_callback_rejects_wrong_redirect(self) -> None:
        with self.assertRaises(AuthenticationError):
            self._complete(redirect_uri="https://evil.example/auth/callback")
        self.assertEqual(len(self.provider.requests), 0)

    def test_callback_rejects_adversarial_signed_claims(self) -> None:
        variants = (
            {"iss": "https://wrong.example"},
            {"aud": "wrong-client"},
            {"azp": "wrong-client"},
            {"nonce": "wrong-nonce"},
            {"exp": 999},
            {"iat": 1_001},
            {"nbf": 1_001},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.provider.id_token = self._token(**variant)
                with self.assertRaises(AuthenticationError):
                    self.adapter.complete_callback(
                        code="authorization-code",
                        nonce=self.transaction.nonce,
                        code_verifier=self.transaction.code_verifier,
                        redirect_uri=self.redirect_uri,
                    )

    def test_callback_rejects_wrong_signature_and_algorithm(self) -> None:
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.provider.id_token = jwt.encode(
            {
                "iss": self.issuer,
                "sub": "subject-a",
                "aud": self.client_id,
                "azp": self.client_id,
                "nonce": self.transaction.nonce,
                "iat": 1_000,
                "exp": 1_100,
            },
            other_key,
            algorithm="RS256",
            headers={"kid": "idp-key"},
        )
        with self.assertRaises(AuthenticationError):
            self.adapter.complete_callback(
                code="authorization-code",
                nonce=self.transaction.nonce,
                code_verifier=self.transaction.code_verifier,
                redirect_uri=self.redirect_uri,
            )

        self.provider.id_token = jwt.encode(
            self._token_claims(),
            "not-a-signing-key-that-is-long-enough",
            algorithm="HS256",
            headers={"kid": "idp-key"},
        )
        with self.assertRaises(AuthenticationError):
            self.adapter.complete_callback(
                code="authorization-code",
                nonce=self.transaction.nonce,
                code_verifier=self.transaction.code_verifier,
                redirect_uri=self.redirect_uri,
            )

    def _token_claims(self) -> dict[str, object]:
        return {
            "iss": self.issuer,
            "sub": "subject-a",
            "aud": self.client_id,
            "azp": self.client_id,
            "nonce": self.transaction.nonce,
            "iat": 1_000,
            "exp": 1_100,
        }

    def test_config_is_https_exact_and_bounded(self) -> None:
        invalid = (
            {"issuer": "http://issuer.example"},
            {"authorization_endpoint": f"{self.authorization_endpoint}?x=1"},
            {"token_endpoint": "https://user:pass@issuer.example/token"},
            {"redirect_uri": "https://folio.example/other"},
            {"timeout_seconds": 31},
            {"max_response_bytes": 4 * 1024 * 1024 + 1},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(self.config, **changes)
