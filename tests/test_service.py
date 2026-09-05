import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from folio_lattice.service import FolioError, FolioLattice


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")

    def tearDown(self):
        self.temp.cleanup()

    def test_versions_search_chunks_and_graph(self):
        first = self.service.create_artifact(
            tenant_id="acme",
            name="one.md",
            data=b"alpha graph target",
            media_type="text/markdown",
            actor="codex",
            reason="seed",
        )
        second = self.service.create_artifact(
            tenant_id="acme",
            name="two.txt",
            data=b"beta linked document",
            media_type="text/plain",
            actor="codex",
            reason="seed",
        )
        artifact_id = first["artifact"]["id"]
        version_id = first["version"]["id"]
        update = self.service.write_version(
            tenant_id="acme",
            artifact_id=artifact_id,
            data=b"alpha graph target revised",
            media_type="text/markdown",
            actor="codex",
            reason="edit",
            source_context={"ticket": "v0"},
            parent_version_id=version_id,
        )
        versions = self.service.versions("acme", artifact_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(update["parent_version_id"], version_id)
        self.assertEqual(
            self.service.read_artifact("acme", artifact_id)["text"], "alpha graph target revised"
        )
        self.assertTrue(self.service.search("acme", "revised"))
        self.assertTrue(self.service.grep("acme", "graph target"))
        self.service.link("acme", artifact_id, second["artifact"]["id"], "references")
        traversal = self.service.traverse("acme", artifact_id, max_depth=1)
        self.assertEqual(traversal[0]["target_artifact_id"], second["artifact"]["id"])

    def test_parent_mismatch_does_not_create_version(self):
        first = self.service.create_artifact(
            tenant_id="acme", name="one.txt", data=b"one", media_type="text/plain"
        )
        with self.assertRaises(FolioError):
            self.service.write_version(
                tenant_id="acme",
                artifact_id=first["artifact"]["id"],
                data=b"two",
                media_type="text/plain",
                actor="dev",
                reason="bad",
                source_context={},
                parent_version_id="ver_wrong",
            )
        self.assertEqual(len(self.service.versions("acme", first["artifact"]["id"])), 1)

    def test_create_is_atomic_and_inputs_are_bounded(self):
        with patch.object(self.service, "_insert_version", side_effect=FolioError("failed")):
            with self.assertRaisesRegex(FolioError, "failed"):
                self.service.create_artifact(tenant_id="acme", name="partial.txt", data=b"partial")
        with self.service.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

        root = Path(self.temp.name)
        bounded = FolioLattice(root / "bounded.db", root / "bounded-blobs", max_artifact_bytes=2)
        with self.assertRaisesRegex(FolioError, "artifact exceeds"):
            bounded.create_artifact(tenant_id="acme", name="large.txt", data=b"123")

    def test_edges_are_idempotent_and_immutable(self):
        source = self.service.create_artifact(tenant_id="acme", name="a", data=b"a")
        target = self.service.create_artifact(tenant_id="acme", name="b", data=b"b")
        arguments = (
            "acme",
            source["artifact"]["id"],
            target["artifact"]["id"],
            "supports",
        )
        first = self.service.link(*arguments, {"claim": "one"})
        duplicate = self.service.link(*arguments, {"claim": "one"})
        self.assertEqual(first["id"], duplicate["id"])
        with self.assertRaisesRegex(FolioError, "immutable metadata"):
            self.service.link(*arguments, {"claim": "two"})

    def test_binary_artifact_round_trips(self):
        data = b"\x00\x01\xff"
        created = self.service.create_artifact(
            tenant_id="acme", name="image.bin", data=data, media_type="application/octet-stream"
        )
        read = self.service.read_artifact("acme", created["artifact"]["id"])
        self.assertEqual(base64.b64decode(read["content_base64"]), data)
        self.assertNotIn("text", read)

    def test_invalid_reads_and_queries_fail_closed(self):
        created = self.service.create_artifact(
            tenant_id="acme", name="empty.txt", data=b"", media_type="text/plain"
        )
        artifact_id = created["artifact"]["id"]
        with self.assertRaisesRegex(FolioError, "artifact not found"):
            self.service.get_artifact("acme", "art_missing")
        with self.assertRaisesRegex(FolioError, "version not found"):
            self.service.version_metadata("acme", "ver_missing")
        with self.assertRaisesRegex(FolioError, "chunk not found"):
            self.service.read_chunk("acme", "chk_missing")
        self.assertEqual(self.service.grep("acme", "["), [])
        with self.assertRaisesRegex(FolioError, "self-links"):
            self.service.link("acme", artifact_id, artifact_id, "related")
        with self.assertRaisesRegex(FolioError, "max_depth"):
            self.service.traverse("acme", artifact_id, max_depth=11)
        self.assertEqual(self.service.search("acme", "   "), [])
        self.assertEqual(self.service.read_artifact("acme", artifact_id)["text"], "")


if __name__ == "__main__":
    unittest.main()
