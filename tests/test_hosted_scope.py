import base64
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import TOOL_SCOPES, build_mcp_server
from folio_lattice.server import _bind_mcp_actor
from folio_lattice.service import FolioLattice


class HostedScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_enforces_least_privilege_scope_before_service_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service)
            principal = Principal(
                "tenant-a", "actor-a", "https://issuer.example", "subject-a", frozenset()
            )
            token = set_request_principal(principal)
            try:
                async with Client(server, raise_exceptions=True) as client:
                    tools = await client.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools}, set(TOOL_SCOPES))

                    denied = await client.call_tool(
                        "artifact_create",
                        {
                            "name": "blocked.txt",
                            "content_base64": base64.b64encode(b"blocked").decode(),
                        },
                    )
                    self.assertTrue(denied.is_error)
                    self.assertIn("operation not permitted", denied.content[0].text)
                    self.assertEqual(service.list_artifacts("tenant-a", actor=None), [])

                    reset_request_principal(token)
                    token = set_request_principal(
                        Principal(
                            "tenant-a",
                            "actor-a",
                            "https://issuer.example",
                            "subject-a",
                            frozenset({"artifact:write"}),
                        )
                    )
                    allowed = await client.call_tool(
                        "artifact_create",
                        {
                            "name": "allowed.txt",
                            "content_base64": base64.b64encode(b"allowed").decode(),
                        },
                    )
                    self.assertFalse(allowed.is_error, allowed)

                    read_denied = await client.call_tool("artifact_list", {})
                    self.assertTrue(read_denied.is_error)
                    self.assertIn("operation not permitted", read_denied.content[0].text)
            finally:
                reset_request_principal(token)

    async def test_acl_04_live_session_observes_share_then_immediate_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service)
            artifact = service.create_artifact(
                tenant_id="tenant-a", name="private.txt", data=b"private", actor="owner"
            )
            artifact_id = artifact["artifact"]["id"]

            member_token = set_request_principal(
                Principal(
                    "tenant-a",
                    "member",
                    "https://issuer.example",
                    "member-subject",
                    frozenset({"artifact:read", "artifact:share"}),
                )
            )
            try:
                async with Client(server, raise_exceptions=True) as member:
                    denied = await member.call_tool("artifact_read", {"artifact_id": artifact_id})
                    self.assertTrue(denied.is_error)

                    owner_token = set_request_principal(
                        Principal(
                            "tenant-a",
                            "owner",
                            "https://issuer.example",
                            "owner-subject",
                            frozenset({"artifact:share", "artifact:read"}),
                        )
                    )
                    try:
                        async with Client(server, raise_exceptions=True) as owner:
                            shared = await owner.call_tool(
                                "artifact_share",
                                {"artifact_id": artifact_id, "subject_actor_id": "member"},
                            )
                            self.assertFalse(shared.is_error, shared)
                    finally:
                        reset_request_principal(owner_token)

                    allowed = await member.call_tool("artifact_read", {"artifact_id": artifact_id})
                    self.assertFalse(allowed.is_error, allowed)

                    owner_token = set_request_principal(
                        Principal(
                            "tenant-a",
                            "owner",
                            "https://issuer.example",
                            "owner-subject",
                            frozenset({"artifact:share", "artifact:read"}),
                        )
                    )
                    try:
                        async with Client(server, raise_exceptions=True) as owner:
                            shared_result = shared.structured_content
                            assert shared_result is not None
                            grant_id = shared_result.get("result", shared_result)["id"]
                            revoked = await owner.call_tool(
                                "artifact_revoke",
                                {"artifact_id": artifact_id, "grant_id": grant_id},
                            )
                            self.assertFalse(revoked.is_error, revoked)
                    finally:
                        reset_request_principal(owner_token)

                    read_after_revoke = await member.call_tool(
                        "artifact_read", {"artifact_id": artifact_id}
                    )
                    history_after_revoke = await member.call_tool(
                        "artifact_versions", {"artifact_id": artifact_id}
                    )
                    self.assertTrue(read_after_revoke.is_error)
                    self.assertTrue(history_after_revoke.is_error)
            finally:
                reset_request_principal(member_token)

    async def test_ten_01_cross_tenant_live_session_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service)
            artifact = service.create_artifact(
                tenant_id="tenant-a", name="private.txt", data=b"private", actor="owner-a"
            )
            artifact_id = artifact["artifact"]["id"]
            token = set_request_principal(
                Principal(
                    "tenant-b",
                    "owner-b",
                    "https://issuer.example",
                    "owner-b-subject",
                    frozenset({"artifact:read"}),
                )
            )
            try:
                async with Client(server, raise_exceptions=True) as client:
                    for tool in ("artifact_read", "artifact_versions"):
                        denied = await client.call_tool(tool, {"artifact_id": artifact_id})
                        self.assertTrue(denied.is_error)
                        self.assertIn("artifact not found", denied.content[0].text)
                        self.assertNotIn(artifact_id, denied.content[0].text)
            finally:
                reset_request_principal(token)

    def test_hosted_mcp_scope_contains_sdk_session_identity(self) -> None:
        principal = Principal(
            "tenant-a",
            "actor-a",
            "https://issuer.example",
            "subject-a",
            frozenset({"artifact:read"}),
        )
        scope = {"path": "/mcp"}
        _bind_mcp_actor(scope, principal)
        self.assertEqual(set(scope["auth"].scopes), {"artifact:read"})
        self.assertEqual(scope["user"].access_token.subject, "subject-a")
        self.assertEqual(scope["user"].access_token.claims, {"iss": "https://issuer.example"})

    async def test_authenticated_principal_wins_over_fixed_policy_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            artifact = service.create_artifact(
                tenant_id="tenant-a", name="private.txt", data=b"private", actor="owner"
            )
            server = build_mcp_server(service, tenant_id="tenant-a", actor="fixed-owner")
            token = set_request_principal(
                Principal(
                    "tenant-a",
                    "member",
                    "https://issuer.example",
                    "member-subject",
                    frozenset({"artifact:read"}),
                )
            )
            try:
                async with Client(server, raise_exceptions=True) as client:
                    denied = await client.call_tool(
                        "artifact_read", {"artifact_id": artifact["artifact"]["id"]}
                    )
                    self.assertTrue(denied.is_error)
                    self.assertIn("artifact not found", denied.content[0].text)
            finally:
                reset_request_principal(token)
