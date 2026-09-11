import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from folio_lattice.auth import MembershipStore
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioError, FolioLattice


class AclTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "folio.db"
        self.service = FolioLattice(self.db, root / "blobs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_private_by_default_share_revoke_and_durability(self) -> None:
        created = self.service.create_artifact(
            tenant_id="tenant-a",
            name="private.txt",
            data=b"private marker",
            actor="owner",
        )
        artifact_id = created["artifact"]["id"]

        with self.assertRaisesRegex(FolioError, "artifact not found"):
            self.service.read_artifact("tenant-a", artifact_id, actor="member")
        self.assertEqual(self.service.search("tenant-a", "private", actor="member"), [])
        self.assertEqual(self.service.grep("tenant-a", "marker", actor="member"), [])

        grant = self.service.share_artifact(
            "tenant-a",
            artifact_id,
            actor="owner",
            subject_actor_id="member",
            reason="review",
        )
        self.assertEqual(grant["status"], "active")
        self.assertEqual(
            self.service.read_artifact("tenant-a", artifact_id, actor="member")["text"],
            "private marker",
        )
        self.assertEqual(len(self.service.search("tenant-a", "private", actor="member")), 1)
        writer_grant = self.service.share_artifact(
            "tenant-a",
            artifact_id,
            actor="owner",
            subject_actor_id="writer",
            action="write",
        )
        written = self.service.write_version(
            tenant_id="tenant-a",
            artifact_id=artifact_id,
            data=b"writer update",
            media_type="text/plain",
            actor="writer",
            reason="edit",
            source_context={},
            parent_version_id=created["version"]["id"],
        )
        self.assertEqual(written["actor"], "writer")
        self.assertEqual(writer_grant["action"], "write")
        with self.assertRaisesRegex(FolioError, "artifact not found"):
            self.service.write_version(
                tenant_id="tenant-a",
                artifact_id=artifact_id,
                data=b"changed",
                media_type="text/plain",
                actor="member",
                reason="edit",
                source_context={},
                parent_version_id=created["version"]["id"],
            )

        revoked = self.service.revoke_share(
            "tenant-a", artifact_id, actor="owner", grant_id=grant["id"], reason="done"
        )
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked["reason"], "review")
        self.assertEqual(revoked["revocation_reason"], "done")

        reopened = FolioLattice(self.db, Path(self.temp.name) / "blobs")
        with self.assertRaisesRegex(FolioError, "artifact not found"):
            reopened.read_artifact("tenant-a", artifact_id, actor="member")
        reshared = reopened.share_artifact(
            "tenant-a", artifact_id, actor="owner", subject_actor_id="member"
        )
        self.assertNotEqual(reshared["id"], grant["id"])

    def test_hosted_membership_blocks_cross_tenant_share(self) -> None:
        memberships = MembershipStore(self.db)
        memberships.add(
            issuer="https://issuer.example/",
            subject="owner-subject",
            tenant_id="tenant-a",
            actor_id="owner",
        )
        memberships.add(
            issuer="https://issuer.example/",
            subject="other-subject",
            tenant_id="tenant-b",
            actor_id="member",
        )
        created = self.service.create_artifact(
            tenant_id="tenant-a", name="x.txt", data=b"x", actor="owner"
        )
        with self.assertRaisesRegex(FolioError, "active tenant member"):
            self.service.share_artifact(
                "tenant-a",
                created["artifact"]["id"],
                actor="owner",
                subject_actor_id="member",
            )

    def test_owner_reason_is_reserved_and_legacy_spoof_is_revokeable(self) -> None:
        created = self.service.create_artifact(
            tenant_id="tenant-a", name="x.txt", data=b"x", actor="owner"
        )
        artifact_id = created["artifact"]["id"]
        with self.assertRaisesRegex(FolioError, "share reason is reserved"):
            self.service.share_artifact(
                "tenant-a",
                artifact_id,
                actor="owner",
                subject_actor_id="member",
                reason="  ARtifact OWNER  ",
            )

        with self.assertRaisesRegex(FolioError, "cannot be revoked"):
            self.service.revoke_share(
                "tenant-a",
                artifact_id,
                actor="owner",
                grant_id=f"owner_{artifact_id}_read",
            )

        grant = self.service.share_artifact(
            "tenant-a", artifact_id, actor="owner", subject_actor_id="member", reason="review"
        )
        with self.service.connect() as db:
            db.execute(
                "UPDATE acl_grants SET reason = ? WHERE id = ?",
                ("artifact owner", grant["id"]),
            )
        revoked = self.service.revoke_share(
            "tenant-a", artifact_id, actor="owner", grant_id=grant["id"]
        )
        self.assertEqual(revoked["status"], "revoked")

    def test_existing_artifact_migrates_to_owner_grants(self) -> None:
        legacy_db = Path(self.temp.name) / "legacy.db"
        legacy_blobs = Path(self.temp.name) / "legacy-blobs"
        with sqlite3.connect(legacy_db) as db:
            db.executescript(
                """
                CREATE TABLE tenants (id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE artifacts (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE versions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    parent_version_id TEXT,
                    blob_hash TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_context TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO tenants VALUES ('tenant-a', 'now');
                INSERT INTO artifacts VALUES ('art_old', 'tenant-a', 'old.txt', 'text/plain', 'now');
                INSERT INTO versions VALUES (
                    'ver_old', 'tenant-a', 'art_old', NULL, 'missing', 'text/plain',
                    3, 'legacy-owner', 'seed', '{}', 'now'
                );
                """
            )
        migrated = FolioLattice(legacy_db, legacy_blobs)
        with self.assertRaisesRegex(FolioError, "version blob missing"):
            # Initialization performed the migration before this read reaches the blob.
            migrated.read_artifact("tenant-a", "art_old", actor="legacy-owner")
        with migrated.connect() as db:
            owner = db.execute(
                "SELECT owner_actor_id, current_version_id FROM artifacts WHERE id = 'art_old'"
            ).fetchone()
            grants = db.execute(
                "SELECT COUNT(*) FROM acl_grants WHERE artifact_id = 'art_old' AND status = 'active'"
            ).fetchone()[0]
        self.assertEqual(
            dict(owner), {"owner_actor_id": "legacy-owner", "current_version_id": "ver_old"}
        )
        self.assertEqual(grants, 3)


class McpAclTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_rejects_reserved_owner_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service, tenant_id="tenant-a", actor="owner")
            async with Client(server) as client:
                created = await client.call_tool(
                    "artifact_create",
                    {
                        "name": "shared.txt",
                        "content_base64": base64.b64encode(b"shared").decode(),
                    },
                )
                self.assertFalse(created.is_error, created)
                structured = created.structured_content
                assert structured is not None
                artifact_id = structured.get("result", structured)["artifact"]["id"]
                response = await client.call_tool(
                    "artifact_share",
                    {
                        "artifact_id": artifact_id,
                        "subject_actor_id": "member",
                        "reason": "  ARtifact OWNER  ",
                    },
                )
                self.assertTrue(response.is_error)
                self.assertIn("share reason is reserved", response.content[0].text)

    async def test_mcp_share_and_revoke_have_no_caller_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service, tenant_id="tenant-a", actor="owner")
            async with Client(server, raise_exceptions=True) as client:
                tools = await client.list_tools()
                by_name = {tool.name: tool for tool in tools.tools}
                for name in ("artifact_share", "artifact_revoke", "artifact_acl"):
                    properties = by_name[name].input_schema.get("properties", {})
                    self.assertNotIn("tenant_id", properties)
                    self.assertNotIn("actor", properties)
                created = await client.call_tool(
                    "artifact_create",
                    {
                        "name": "shared.txt",
                        "content_base64": base64.b64encode(b"shared").decode(),
                    },
                )
                structured = created.structured_content
                assert structured is not None
                created_result = structured.get("result", structured)
                artifact_id = created_result["artifact"]["id"]
                shared = await client.call_tool(
                    "artifact_share",
                    {"artifact_id": artifact_id, "subject_actor_id": "member"},
                )
                shared_content = shared.structured_content
                assert shared_content is not None
                shared_result = shared_content.get("result", shared_content)
                self.assertEqual(shared_result["status"], "active")
                revoked = await client.call_tool(
                    "artifact_revoke",
                    {"artifact_id": artifact_id, "grant_id": shared_result["id"]},
                )
                revoked_content = revoked.structured_content
                assert revoked_content is not None
                revoked_result = revoked_content.get("result", revoked_content)
                self.assertEqual(revoked_result["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
