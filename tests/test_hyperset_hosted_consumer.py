import base64
import json
import os
import time
import unittest
from unittest.mock import patch

from hosted_auth_target import control_command
from hyperset_hosted_consumer import GateError, _decode_segment, _mcp_url, _validate_token


class McpUrlValidationTests(unittest.TestCase):
    def test_accepts_tenant_scoped_mcp_paths(self) -> None:
        for value in (
            "https://example.test/mcp",
            "https://example.test/tenant-a/mcp",
            "http://127.0.0.1:8787/nested/tenant/mcp",
        ):
            with self.subTest(value=value), patch.dict(os.environ, {"TEST_MCP_URL": value}):
                self.assertEqual(_mcp_url("TEST_MCP_URL"), value)

    def test_rejects_unsafe_or_non_mcp_urls(self) -> None:
        for value in (
            "ftp://example.test/tenant-a/mcp",
            "https://user@example.test/tenant-a/mcp",
            "https://example.test/tenant-a/mcp?token=secret",
            "https://example.test/tenant-a/mcp#fragment",
            "https://example.test/tenant-a/not-mcp",
            "https://example.test/tenant-a/mcp/",
        ):
            with self.subTest(value=value), patch.dict(os.environ, {"TEST_MCP_URL": value}):
                with self.assertRaises(GateError) as error:
                    _mcp_url("TEST_MCP_URL")
                self.assertEqual(error.exception.code, "invalid_configuration")

    def test_compact_jws_keeps_non_utf8_signature_bytes(self) -> None:
        def segment(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

        token = ".".join(
            (
                segment(b'{"alg":"RS256","kid":"test-key","typ":"JWT"}'),
                segment(
                    json.dumps(
                        {
                            "iss": "https://issuer.example",
                            "aud": "https://folio.example/mcp",
                            "sub": "subject-a",
                            "exp": time.time() + 60,
                        }
                    ).encode()
                ),
                segment(b"\x80\x81" + bytes(range(254))),
            )
        )

        parsed = _validate_token(
            token,
            "https://issuer.example",
            "https://folio.example/mcp",
        )

        self.assertEqual(parsed.algorithm, "RS256")
        self.assertEqual(_decode_segment(token.split(".")[2])[:2], b"\x80\x81")

    def test_restart_control_waits_for_a_new_ready_boot(self) -> None:
        health_responses = iter(
            [
                {"ready": True, "boot_id": "before"},
                {"ready": True, "boot_id": "before"},
                {"ready": True, "boot_id": "after"},
            ]
        )

        def request(url: str, **kwargs: object) -> object:
            if url.endswith("/health"):
                return next(health_responses)
            self.assertEqual(url, "https://target.test/__test/restart")
            self.assertEqual(kwargs, {"method": "POST", "payload": {}})
            return {"status": "restarting"}

        with patch("hosted_auth_target._url_json", side_effect=request) as mocked_request:
            control_command("https://target.test", "restart", None)

        self.assertEqual(mocked_request.call_count, 4)
