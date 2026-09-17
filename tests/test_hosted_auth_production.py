"""Run AUTH-PROD-01 against the actual hosted Folio server process."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _issuer_handler(jwks: bytes) -> type[BaseHTTPRequestHandler]:
    class IssuerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/issuer/jwks.json":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(jwks)))
            self.end_headers()
            self.wfile.write(jwks)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return IssuerHandler


class _HttpsIssuer:
    def __init__(self, root: Path, jwks: bytes) -> None:
        self.cert_path = root / "issuer.pem"
        key_path = root / "issuer-key.pem"
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(signing_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(minutes=10))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(signing_key, hashes.SHA256())
        )
        key_path.write_bytes(
            signing_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        self.cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        self.signing_key = signing_key
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _issuer_handler(jwks))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert_path, key_path)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.issuer = f"https://127.0.0.1:{self.server.server_port}/issuer"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _write_memberships(path: Path, issuer: str) -> None:
    path.write_text(
        json.dumps(
            {
                "memberships": [
                    {
                        "issuer": issuer,
                        "subject": "production-subject",
                        "tenant_id": "production-tenant",
                        "actor_id": "membership-actor",
                        "scopes": ["artifact:read"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _request_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    try:
        with urlopen(Request(url, headers=headers or {}), timeout=1) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _stop(process: subprocess.Popen[bytes]) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return (stdout + stderr).decode(errors="replace")


class HostedAuthProductionTests(unittest.TestCase):
    def test_real_product_process_warms_oidc_and_binds_v1_me_to_membership(self) -> None:
        try:
            product_port = _free_port()
        except PermissionError as exc:
            self.skipTest(f"environment disallows loopback HTTP sockets: {exc}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
            jwk.update({"kid": "production-key", "alg": "RS256", "use": "sig"})
            try:
                issuer_server = _HttpsIssuer(root, json.dumps({"keys": [jwk]}).encode())
            except PermissionError as exc:
                self.skipTest(f"environment disallows loopback HTTPS sockets: {exc}")
            try:
                memberships_path = root / "memberships.json"
                _write_memberships(memberships_path, issuer_server.issuer)
                environment = dict(os.environ)
                for name in (
                    "FOLIO_OIDC_AUTHORIZATION_ENDPOINT",
                    "FOLIO_OIDC_TOKEN_ENDPOINT",
                    "FOLIO_OIDC_CLIENT_ID",
                    "FOLIO_OIDC_REDIRECT_URI",
                ):
                    environment.pop(name, None)
                environment.update(
                    {
                        "FOLIO_DEPLOYMENT_MODE": "hosted",
                        "FOLIO_DB_PATH": str(root / "folio.db"),
                        "FOLIO_BLOB_ROOT": str(root / "blobs"),
                        "FOLIO_OIDC_ISSUER": issuer_server.issuer,
                        "FOLIO_OIDC_AUDIENCE": "folio-api",
                        "FOLIO_OIDC_ALGORITHM": "RS256",
                        "FOLIO_OIDC_JWKS_URL": f"{issuer_server.issuer}/jwks.json",
                        "FOLIO_OIDC_MEMBERSHIPS_FILE": str(memberships_path),
                        "FOLIO_CONTROL_ORIGIN": "https://control.example",
                        "FOLIO_RENDER_ORIGIN": "https://render.example",
                        "SSL_CERT_FILE": str(issuer_server.cert_path),
                        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                    }
                )
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "folio_lattice.server",
                        "--transport",
                        "http",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(product_port),
                    ],
                    cwd=Path(__file__).parents[1],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    base_url = f"http://127.0.0.1:{product_port}"
                    deadline = time.monotonic() + 10
                    health: Any = None
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        try:
                            status, health = _request_json(f"{base_url}/health")
                            if status == 200 and isinstance(health, dict) and health.get("ready"):
                                break
                        except OSError:
                            pass
                        time.sleep(0.05)
                    else:
                        health = None
                    if not isinstance(health, dict) or not health.get("ready"):
                        logs = _stop(process)
                        raise AssertionError(f"hosted product did not become ready: {logs}")

                    now = int(time.time())
                    token = jwt.encode(
                        {
                            "iss": issuer_server.issuer,
                            "aud": "folio-api",
                            "sub": "production-subject",
                            "iat": now,
                            "nbf": now,
                            "exp": now + 300,
                        },
                        signing_key,
                        algorithm="RS256",
                        headers={"kid": "production-key"},
                    )
                    status, me = _request_json(
                        f"{base_url}/v1/me",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        me,
                        {
                            "authenticated": True,
                            "tenant_id": "production-tenant",
                            "actor_id": "membership-actor",
                        },
                    )
                    unauthorized_status, unauthorized = _request_json(f"{base_url}/v1/me")
                    self.assertEqual(unauthorized_status, 401)
                    self.assertEqual(unauthorized["code"], "authentication_required")
                    evidence = {
                        "status": "passed",
                        "product_process": True,
                        "hosted_startup": health["authentication"] == "oidc-bearer",
                        "membership_actor_binding": me["actor_id"] == "membership-actor",
                        "v1_me_status": status,
                        "v1_me_tenant_id": me["tenant_id"],
                        "v1_me_actor_id": me["actor_id"],
                    }
                    print(json.dumps(evidence, sort_keys=True))
                    evidence_path = os.environ.get("FOLIO_HOSTED_AUTH_PROD_EVIDENCE")
                    if evidence_path:
                        Path(evidence_path).write_text(
                            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                        )
                finally:
                    _stop(process)
            finally:
                issuer_server.close()
