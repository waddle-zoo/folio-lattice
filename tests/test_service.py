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
            data=b"alpha legacy-marker graph target",
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
        self.assertTrue(self.service.search("acme", "graph-target"))
        self.assertTrue(self.service.grep("acme", "graph target"))
        self.assertEqual(self.service.search("acme", "legacy-marker"), [])
        self.assertEqual(self.service.grep("acme", "legacy-marker"), [])
        self.service.link("acme", artifact_id, second["artifact"]["id"], "references")
        traversal = self.service.traverse("acme", artifact_id, max_depth=1)
        self.assertEqual(traversal[0]["target_artifact_id"], second["artifact"]["id"])

    def test_graph_component_is_undirected_and_search_can_scope_to_it(self):
        incoming = self.service.create_artifact(
            tenant_id="acme", name="duplicate.txt", data=b"component marker"
        )
        root = self.service.create_artifact(tenant_id="acme", name="root.txt", data=b"root node")
        outgoing = self.service.create_artifact(
            tenant_id="acme", name="outgoing.txt", data=b"component marker"
        )
        disconnected = self.service.create_artifact(
            tenant_id="acme", name="duplicate.txt", data=b"component marker"
        )
        self.service.link("acme", incoming["artifact"]["id"], root["artifact"]["id"], "references")
        self.service.link("acme", root["artifact"]["id"], outgoing["artifact"]["id"], "references")
        self.service.link(
            "acme", outgoing["artifact"]["id"], incoming["artifact"]["id"], "references"
        )

        component = self.service.graph_component("acme", root["artifact"]["id"])
        self.assertEqual(
            {item["id"] for item in component},
            {incoming["artifact"]["id"], root["artifact"]["id"], outgoing["artifact"]["id"]},
        )
        leaf_component = self.service.graph_component("acme", outgoing["artifact"]["id"])
        self.assertEqual(
            {item["id"] for item in leaf_component}, {item["id"] for item in component}
        )
        bounded = self.service.graph_component("acme", outgoing["artifact"]["id"], limit=2)
        self.assertEqual(len(bounded), 2)
        self.assertEqual(len({item["id"] for item in bounded}), 2)
        scoped = self.service.search(
            "acme", "component", graph_root_artifact_id=root["artifact"]["id"]
        )
        self.assertEqual(
            {item["artifact_id"] for item in scoped},
            {incoming["artifact"]["id"], outgoing["artifact"]["id"]},
        )
        self.assertNotIn(disconnected["artifact"]["id"], {item["artifact_id"] for item in scoped})
        outgoing_result = next(
            item for item in scoped if item["artifact_id"] == outgoing["artifact"]["id"]
        )
        self.assertEqual(
            [node["artifact_id"] for node in outgoing_result["graph_path"]],
            [root["artifact"]["id"], outgoing["artifact"]["id"]],
        )
        self.assertNotIn("graph_edges", outgoing_result)
        self.assertNotIn("edge_count", outgoing_result["graph_context"])

    def test_search_unifies_name_type_body_with_context_and_dedupes(self):
        root = self.service.create_artifact(
            tenant_id="acme", name="root.md", data=b"root body", actor="reader"
        )
        linked = self.service.create_artifact(
            tenant_id="acme",
            name="docker-render.html",
            data=b"docker body marker",
            media_type="text/html",
            actor="reader",
            source_context={"path": "assets/docker-render.html"},
        )
        duplicate = self.service.create_artifact(
            tenant_id="acme",
            name="docker-render.html",
            data=b"other body marker",
            media_type="text/plain",
            actor="reader",
        )
        private = self.service.create_artifact(
            tenant_id="acme",
            name="docker-private.html",
            data=b"docker private marker",
            media_type="text/html",
            actor="owner",
        )
        self.service.link("acme", root["artifact"]["id"], linked["artifact"]["id"], "renders")

        by_name = self.service.search("acme", "DOCKER-RENDER.HTML", actor="reader")
        self.assertEqual(
            {item["artifact_id"] for item in by_name},
            {linked["artifact"]["id"], duplicate["artifact"]["id"]},
        )
        linked_result = next(
            item for item in by_name if item["artifact_id"] == linked["artifact"]["id"]
        )
        self.assertEqual(linked_result["version_id"], linked["version"]["id"])
        self.assertEqual(linked_result["artifact_name"], "docker-render.html")
        self.assertEqual(linked_result["media_type"], "text/html")
        self.assertEqual(linked_result["match_kind"], "name")
        self.assertEqual(linked_result["match_kinds"], ["name"])
        self.assertIn("[docker-render.html]", linked_result["snippet"])
        self.assertEqual(linked_result["path"], "assets/docker-render.html")
        self.assertNotIn("graph_edges", linked_result)
        self.assertNotIn("edge_count", linked_result["graph_context"])

        by_type = self.service.search("acme", "TEXT/HTML", actor="reader")
        self.assertEqual([item["artifact_id"] for item in by_type], [linked["artifact"]["id"]])
        self.assertEqual(by_type[0]["match_kind"], "media_type")

        combined = self.service.search("acme", "docker", actor="reader")
        combined_linked = next(
            item for item in combined if item["artifact_id"] == linked["artifact"]["id"]
        )
        self.assertEqual(combined_linked["match_kind"], "multiple")
        self.assertEqual(combined_linked["match_kinds"], ["name", "body"])
        self.assertIn("[docker]", combined_linked["snippet"])

        by_body = self.service.search("acme", "BODY MARKER", actor="reader")
        self.assertEqual(
            {item["artifact_id"] for item in by_body},
            {linked["artifact"]["id"], duplicate["artifact"]["id"]},
        )
        self.assertTrue(all("body" in item["match_kinds"] for item in by_body))
        self.assertTrue(all(item["chunk_id"].startswith("chk_") for item in by_body))
        self.assertNotIn(private["artifact"]["id"], {item["artifact_id"] for item in by_body})

        scoped = self.service.search(
            "acme",
            "docker-render.html",
            actor="reader",
            graph_root_artifact_id=root["artifact"]["id"],
        )
        self.assertEqual([item["artifact_id"] for item in scoped], [linked["artifact"]["id"]])
        self.assertEqual(scoped[0]["graph_context"]["root_artifact_id"], root["artifact"]["id"])

    def test_search_paginates_stably_by_updated_at_and_artifact_id(self):
        created = [
            self.service.create_artifact(
                tenant_id="acme", name=f"page-{index}.md", data=b"page marker", actor="reader"
            )
            for index in range(3)
        ]
        first = self.service.search("acme", "page", limit=2, actor="reader")
        self.assertEqual(len(first), 2)
        cursor = f"{first[-1]['updated_at']}|{first[-1]['artifact_id']}"
        second = self.service.search("acme", "page", limit=2, actor="reader", cursor=cursor)
        self.assertEqual(len(second), 1)
        self.assertTrue(
            {item["artifact_id"] for item in first}.isdisjoint(
                item["artifact_id"] for item in second
            )
        )
        self.assertEqual(
            {item["artifact_id"] for item in [*first, *second]},
            {item["artifact"]["id"] for item in created},
        )
        with self.assertRaisesRegex(FolioError, "invalid artifact_search cursor"):
            self.service.search("acme", "page", cursor="bad", actor="reader")

    def test_search_candidate_scan_is_bounded_before_python_deduplication(self):
        for index in range(4):
            self.service.create_artifact(
                tenant_id="acme",
                name=f"bounded-{index}.md",
                data=b"marker",
                actor="reader",
            )
        # Keep this small in the unit test to prove the SQL LIMIT is enforced;
        # production uses the explicit MAX_SEARCH_CANDIDATES bound of 10,000.
        with patch("folio_lattice.service.MAX_SEARCH_CANDIDATES", 2):
            results = self.service.search("acme", "bounded", limit=100, actor="reader")
        self.assertEqual(len(results), 2)

    def test_search_uses_ranked_fts_and_indexed_substring_discovery(self):
        ranked = self.service.create_artifact(
            tenant_id="acme",
            name="ranked.md",
            data=b"needle needle context",
            actor="reader",
        )
        substring = self.service.create_artifact(
            tenant_id="acme",
            name="substring.md",
            data=b"the docker-renderer payload",
            actor="reader",
        )

        ranked_result = self.service.search("acme", "needle", actor="reader")
        self.assertEqual(ranked_result[0]["artifact_id"], ranked["artifact"]["id"])
        self.assertIsNotNone(ranked_result[0]["score"])
        self.assertIn("[needle]", ranked_result[0]["snippet"])

        substring_result = self.service.search("acme", "ocker-rend", actor="reader")
        self.assertEqual(
            [item["artifact_id"] for item in substring_result], [substring["artifact"]["id"]]
        )
        self.assertEqual(substring_result[0]["match_kinds"], ["body"])
        self.assertIsNotNone(substring_result[0]["score"])

    def test_short_punctuation_fallback_is_explicitly_bounded(self):
        for index in range(4):
            self.service.create_artifact(
                tenant_id="acme",
                name=f"punctuation-{index}.md",
                data=f"body-{index} @".encode(),
                actor="reader",
            )
        with patch("folio_lattice.service.MAX_SEARCH_CANDIDATES", 2):
            results = self.service.search("acme", "@", limit=100, actor="reader")
        self.assertLessEqual(len(results), 2)
        self.assertTrue(all("body" in item["match_kinds"] for item in results))

    def test_search_does_not_cross_tenant_body_index(self):
        foreign = self.service.create_artifact(
            tenant_id="other",
            name="foreign.md",
            data=b"tenant-isolation-marker",
            actor="owner",
        )
        self.service.create_artifact(
            tenant_id="acme", name="local.md", data=b"local body", actor="reader"
        )
        self.assertEqual(self.service.search("acme", "tenant-isolation-marker"), [])
        self.assertEqual(
            self.service.search("other", "tenant-isolation-marker")[0]["artifact_id"],
            foreign["artifact"]["id"],
        )

    def test_search_graph_path_is_readable_and_fail_closed_after_revoke(self):
        root = self.service.create_artifact(
            tenant_id="acme", name="reader-root.md", data=b"root marker", actor="reader"
        )
        private = self.service.create_artifact(
            tenant_id="acme", name="private-neighbor.md", data=b"private marker", actor="owner"
        )
        # Direct service setup bypasses write ACL only to model an existing edge
        # created by the owning workflow; reads must still enforce ACL visibility.
        self.service.link("acme", root["artifact"]["id"], private["artifact"]["id"], "references")
        self.service.share_artifact(
            "acme", root["artifact"]["id"], actor="reader", subject_actor_id="owner"
        )

        reader_root = self.service.search("acme", "reader-root", actor="reader")
        self.assertEqual(len(reader_root), 1)
        self.assertEqual(
            [node["artifact_id"] for node in reader_root[0]["graph_path"]],
            [],
        )
        self.assertNotIn("graph_edges", reader_root[0])
        self.assertNotIn("edge_count", reader_root[0]["graph_context"])
        self.assertNotIn(private["artifact"]["id"], str(reader_root[0]))
        self.assertEqual(self.service.search("acme", "private marker", actor="reader"), [])

        owner_root = self.service.search("acme", "reader-root", actor="owner")
        self.assertEqual(len(owner_root), 1)
        self.assertNotIn("graph_edges", owner_root[0])
        owner_private = self.service.search(
            "acme",
            "private marker",
            actor="owner",
            graph_root_artifact_id=root["artifact"]["id"],
        )
        self.assertEqual(
            [node["artifact_id"] for node in owner_private[0]["graph_path"]],
            [root["artifact"]["id"], private["artifact"]["id"]],
        )

        private_grant = self.service.share_artifact(
            "acme", private["artifact"]["id"], actor="owner", subject_actor_id="reader"
        )
        visible_private = self.service.search(
            "acme",
            "private marker",
            actor="reader",
            graph_root_artifact_id=root["artifact"]["id"],
        )
        self.assertEqual(len(visible_private), 1)
        self.assertEqual(
            [node["artifact_id"] for node in visible_private[0]["graph_path"]],
            [root["artifact"]["id"], private["artifact"]["id"]],
        )

        self.service.revoke_share(
            "acme",
            private["artifact"]["id"],
            actor="owner",
            grant_id=private_grant["id"],
        )
        self.assertEqual(self.service.search("acme", "private marker", actor="reader"), [])
        self.assertEqual(
            self.service.search(
                "acme",
                "private marker",
                actor="reader",
                graph_root_artifact_id=root["artifact"]["id"],
            ),
            [],
        )

        foreign = self.service.create_artifact(
            tenant_id="other", name="private-neighbor.md", data=b"private marker", actor="owner"
        )
        self.assertEqual(self.service.search("acme", "private marker", actor="reader"), [])
        self.assertEqual(
            [item["artifact_id"] for item in self.service.search("other", "private marker")],
            [foreign["artifact"]["id"]],
        )

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

    def test_list_artifacts_returns_recent_named_readable_items(self):
        owned = self.service.create_artifact(
            tenant_id="acme", name="owned.md", data=b"owned", actor="reader"
        )
        private = self.service.create_artifact(
            tenant_id="acme", name="private.md", data=b"private", actor="other"
        )
        shared = self.service.create_artifact(
            tenant_id="acme", name="shared.md", data=b"shared", actor="owner"
        )
        self.service.create_artifact(
            tenant_id="other", name="foreign.md", data=b"foreign", actor="reader"
        )
        self.service.share_artifact(
            "acme",
            shared["artifact"]["id"],
            actor="owner",
            subject_actor_id="reader",
        )
        self.service.link("acme", owned["artifact"]["id"], shared["artifact"]["id"], "references")
        with self.service.connect() as db:
            for created, updated_at in (
                (owned, "2026-01-01T00:00:00+00:00"),
                (private, "2026-01-02T00:00:00+00:00"),
                (shared, "2026-01-03T00:00:00+00:00"),
            ):
                db.execute(
                    "UPDATE versions SET created_at = ? WHERE id = ?",
                    (updated_at, created["version"]["id"]),
                )
        listed = self.service.list_artifacts("acme", actor="reader")
        self.assertEqual([item["name"] for item in listed], ["shared.md", "owned.md"])
        self.assertEqual(
            self.service.list_artifacts("acme", limit=1, actor="reader")[0]["id"],
            shared["artifact"]["id"],
        )
        self.assertIn("updated_at", listed[0])
        self.assertEqual(
            next(item["graph_edges"] for item in listed if item["name"] == "owned.md"), 1
        )
        self.assertEqual(
            next(item["graph_edges"] for item in listed if item["name"] == "shared.md"), 1
        )
        self.assertNotIn("tenant_id", listed[0])

        asset = self.service.create_artifact(
            tenant_id="acme",
            name="agent-launch-board.html",
            data=b"launch controls",
            media_type="text/html",
            actor="reader",
        )
        self.service.create_artifact(
            tenant_id="acme",
            name="agent-launch-board.html",
            data=b"private controls",
            media_type="text/html",
            actor="other",
        )
        self.service.create_artifact(
            tenant_id="other",
            name="agent-launch-board.html",
            data=b"foreign controls",
            media_type="text/html",
            actor="reader",
        )
        by_name = self.service.list_artifacts(
            "acme", actor="reader", name="agent-launch-board.html"
        )
        by_type = self.service.list_artifacts("acme", actor="reader", media_type="text/html")
        self.assertEqual([item["id"] for item in by_name], [asset["artifact"]["id"]])
        self.assertEqual([item["id"] for item in by_type], [asset["artifact"]["id"]])

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

    def test_traversal_does_not_follow_injected_cross_tenant_edge(self):
        source = self.service.create_artifact(
            tenant_id="tenant-a", name="source", data=b"source", actor="same-actor"
        )
        foreign = self.service.create_artifact(
            tenant_id="tenant-b", name="foreign-secret", data=b"secret", actor="same-actor"
        )
        with self.service.connect() as db:
            db.execute(
                """
                INSERT INTO edges (
                    id, tenant_id, source_artifact_id, target_artifact_id,
                    edge_type, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "injected-cross-tenant-edge",
                    "tenant-a",
                    source["artifact"]["id"],
                    foreign["artifact"]["id"],
                    "references",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        self.assertEqual(
            self.service.traverse(
                "tenant-a", source["artifact"]["id"], max_depth=1, actor="same-actor"
            ),
            [],
        )
        self.assertEqual(
            [
                item["id"]
                for item in self.service.graph_component(
                    "tenant-a", source["artifact"]["id"], actor="same-actor"
                )
            ],
            [source["artifact"]["id"]],
        )
        self.assertEqual(
            self.service.search(
                "tenant-a",
                "secret",
                actor="same-actor",
                graph_root_artifact_id=source["artifact"]["id"],
            ),
            [],
        )

    def test_graph_component_stops_at_unreadable_bridge(self):
        root = self.service.create_artifact(
            tenant_id="acme", name="root", data=b"root", actor="owner"
        )
        bridge = self.service.create_artifact(
            tenant_id="acme", name="bridge", data=b"bridge", actor="owner"
        )
        beyond = self.service.create_artifact(
            tenant_id="acme", name="beyond", data=b"beyond", actor="owner"
        )
        self.service.link("acme", root["artifact"]["id"], bridge["artifact"]["id"], "references")
        self.service.link("acme", bridge["artifact"]["id"], beyond["artifact"]["id"], "references")
        self.service.share_artifact(
            "acme", root["artifact"]["id"], actor="owner", subject_actor_id="reader"
        )
        self.service.share_artifact(
            "acme", beyond["artifact"]["id"], actor="owner", subject_actor_id="reader"
        )

        self.assertEqual(
            [
                item["id"]
                for item in self.service.graph_component(
                    "acme", root["artifact"]["id"], actor="reader"
                )
            ],
            [root["artifact"]["id"]],
        )

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
        foreign = self.service.create_artifact(tenant_id="other", name="foreign.txt", data=b"x")
        private = self.service.create_artifact(
            tenant_id="acme", name="private.txt", data=b"private", actor="owner"
        )
        for root, actor in (
            ("art_missing", None),
            (foreign["artifact"]["id"], None),
            (private["artifact"]["id"], "member"),
        ):
            with self.assertRaisesRegex(FolioError, "artifact not found"):
                self.service.graph_component("acme", root, actor=actor)
            with self.assertRaisesRegex(FolioError, "artifact not found"):
                self.service.search("acme", "private", actor=actor, graph_root_artifact_id=root)
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

    def test_acl_defaults_private_and_revoke_is_durable(self):
        created = self.service.create_artifact(
            tenant_id="acme", name="private.txt", data=b"private marker", actor="owner"
        )
        artifact_id = created["artifact"]["id"]

        with self.assertRaisesRegex(FolioError, "artifact not found"):
            self.service.read_artifact("acme", artifact_id, actor="member")
        self.assertEqual(self.service.search("acme", "private", actor="member"), [])
        self.assertEqual(self.service.grep("acme", "marker", actor="member"), [])

        grant = self.service.share_artifact(
            "acme", artifact_id, actor="owner", subject_actor_id="member", reason="review"
        )
        self.assertEqual(
            self.service.read_artifact("acme", artifact_id, actor="member")["text"],
            "private marker",
        )
        revoked = self.service.revoke_share(
            "acme", artifact_id, actor="owner", grant_id=grant["id"], reason="done"
        )
        self.assertEqual(revoked["status"], "revoked")
        with self.assertRaisesRegex(FolioError, "artifact not found"):
            self.service.read_artifact("acme", artifact_id, actor="member")


if __name__ == "__main__":
    unittest.main()
