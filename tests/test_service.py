import base64
import json
import tempfile
import unittest
from pathlib import Path

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
            tenant_id="acme", name="one.md", data=b"alpha graph target", media_type="text/markdown", actor="codex", reason="seed"
        )
        second = self.service.create_artifact(
            tenant_id="acme", name="two.txt", data=b"beta linked document", media_type="text/plain", actor="codex", reason="seed"
        )
        artifact_id = first["artifact"]["id"]
        version_id = first["version"]["id"]
        update = self.service.write_version(
            tenant_id="acme", artifact_id=artifact_id, data=b"alpha graph target revised", media_type="text/markdown", actor="codex", reason="edit", source_context={"ticket": "v0"}, parent_version_id=version_id
        )
        versions = self.service.versions("acme", artifact_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(update["parent_version_id"], version_id)
        self.assertEqual(self.service.read_artifact("acme", artifact_id)["text"], "alpha graph target revised")
        self.assertTrue(self.service.search("acme", "revised"))
        self.assertTrue(self.service.grep("acme", r"graph\s+target"))
        self.service.link("acme", artifact_id, second["artifact"]["id"], "references")
        traversal = self.service.traverse("acme", artifact_id, max_depth=1)
        self.assertEqual(traversal[0]["target_artifact_id"], second["artifact"]["id"])

    def test_parent_mismatch_does_not_create_version(self):
        first = self.service.create_artifact(tenant_id="acme", name="one.txt", data=b"one", media_type="text/plain")
        with self.assertRaises(FolioError):
            self.service.write_version(
                tenant_id="acme", artifact_id=first["artifact"]["id"], data=b"two", media_type="text/plain", actor="dev", reason="bad", source_context={}, parent_version_id="ver_wrong"
            )
        self.assertEqual(len(self.service.versions("acme", first["artifact"]["id"])), 1)

    def test_binary_artifact_round_trips(self):
        data = b"\x00\x01\xff"
        created = self.service.create_artifact(tenant_id="acme", name="image.bin", data=data, media_type="application/octet-stream")
        read = self.service.read_artifact("acme", created["artifact"]["id"])
        self.assertEqual(base64.b64decode(read["content_base64"]), data)
        self.assertNotIn("text", read)


if __name__ == "__main__":
    unittest.main()
