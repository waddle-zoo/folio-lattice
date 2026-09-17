import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from folio_lattice.service import FolioError, FolioLattice


def cursor_for(item: dict[str, object]) -> str:
    return f"{item['updated_at']}|{item['id']}"


class ArtifactListPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_continuation_covers_more_than_100_rows_without_shape_change(self) -> None:
        created = [
            self.service.create_artifact(
                tenant_id="acme",
                name=f"artifact-{index:03d}.txt",
                data=str(index).encode(),
                actor="reader",
            )
            for index in range(205)
        ]
        with self.service.connect() as db:
            for index, item in enumerate(created):
                timestamp = f"2026-01-01T{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}.000+00:00"
                db.execute(
                    "UPDATE versions SET created_at = ? WHERE id = ?",
                    (timestamp, item["version"]["id"]),
                )

        first = self.service.list_artifacts("acme", limit=100, actor="reader")
        second = self.service.list_artifacts(
            "acme", limit=100, actor="reader", cursor=cursor_for(first[-1])
        )
        third = self.service.list_artifacts(
            "acme", limit=100, actor="reader", cursor=cursor_for(second[-1])
        )
        listed = first + second + third

        self.assertIsInstance(first, list)
        self.assertEqual(len(listed), 205)
        self.assertEqual(len({item["id"] for item in listed}), 205)
        self.assertEqual(
            [item["updated_at"] for item in listed],
            sorted((item["updated_at"] for item in listed), reverse=True),
        )

    def test_duplicate_timestamps_use_id_tiebreaker(self) -> None:
        created = [
            self.service.create_artifact(
                tenant_id="acme", name=f"same-{index}", data=b"x", actor="reader"
            )
            for index in range(5)
        ]
        with self.service.connect() as db:
            for item in created:
                db.execute(
                    "UPDATE versions SET created_at = ? WHERE id = ?",
                    ("2026-01-01T00:00:00.000+00:00", item["version"]["id"]),
                )

        first = self.service.list_artifacts("acme", limit=2, actor="reader")
        second = self.service.list_artifacts(
            "acme", limit=2, actor="reader", cursor=cursor_for(first[-1])
        )
        third = self.service.list_artifacts(
            "acme", limit=2, actor="reader", cursor=cursor_for(second[-1])
        )

        listed = first + second + third
        self.assertEqual(len(listed), 5)
        self.assertEqual(len({item["id"] for item in listed}), 5)

    def test_cursor_keeps_tenant_and_acl_filters(self) -> None:
        hidden = self.service.create_artifact(
            tenant_id="acme", name="hidden", data=b"hidden", actor="owner"
        )
        shared = self.service.create_artifact(
            tenant_id="acme", name="shared", data=b"shared", actor="owner"
        )
        owned = self.service.create_artifact(
            tenant_id="acme", name="owned", data=b"owned", actor="reader"
        )
        foreign = self.service.create_artifact(
            tenant_id="other", name="foreign", data=b"foreign", actor="reader"
        )
        self.service.share_artifact(
            "acme", shared["artifact"]["id"], actor="owner", subject_actor_id="reader"
        )
        with self.service.connect() as db:
            timestamps = (
                (hidden, "2026-01-04T00:00:00.000+00:00"),
                (shared, "2026-01-03T00:00:00.000+00:00"),
                (owned, "2026-01-02T00:00:00.000+00:00"),
                (foreign, "2026-01-05T00:00:00.000+00:00"),
            )
            for item, timestamp in timestamps:
                db.execute(
                    "UPDATE versions SET created_at = ? WHERE id = ?",
                    (timestamp, item["version"]["id"]),
                )

        first = self.service.list_artifacts("acme", limit=1, actor="reader")
        second = self.service.list_artifacts(
            "acme", limit=1, actor="reader", cursor=cursor_for(first[-1])
        )
        self.assertEqual([item["name"] for item in first + second], ["shared", "owned"])
        self.assertNotIn("hidden", {item["name"] for item in first + second})
        self.assertNotIn("foreign", {item["name"] for item in first + second})

    def test_cursor_composes_with_exact_metadata_filters(self) -> None:
        matches = [
            self.service.create_artifact(
                tenant_id="acme",
                name="agent-launch-board.html",
                data=str(index).encode(),
                media_type="text/html",
                actor="reader",
            )
            for index in range(5)
        ]
        self.service.create_artifact(
            tenant_id="acme",
            name="agent-launch-board.html",
            data=b"wrong type",
            media_type="text/plain",
            actor="reader",
        )
        self.service.create_artifact(
            tenant_id="acme",
            name="other.html",
            data=b"wrong name",
            media_type="text/html",
            actor="reader",
        )
        with self.service.connect() as db:
            for index, item in enumerate(matches):
                db.execute(
                    "UPDATE versions SET created_at = ? WHERE id = ?",
                    (
                        f"2026-01-01T00:00:0{index}.000+00:00",
                        item["version"]["id"],
                    ),
                )

        arguments = {
            "limit": 2,
            "actor": "reader",
            "name": "agent-launch-board.html",
            "media_type": "text/html",
        }
        first = self.service.list_artifacts("acme", **arguments)
        second = self.service.list_artifacts("acme", cursor=cursor_for(first[-1]), **arguments)
        third = self.service.list_artifacts("acme", cursor=cursor_for(second[-1]), **arguments)

        listed = first + second + third
        self.assertEqual(len(listed), 5)
        self.assertEqual(
            {item["id"] for item in listed}, {item["artifact"]["id"] for item in matches}
        )
        self.assertTrue(all(item["name"] == "agent-launch-board.html" for item in listed))
        self.assertTrue(all(item["media_type"] == "text/html" for item in listed))

    def test_malformed_cursor_fails_closed(self) -> None:
        for cursor in (
            "bad",
            "not-a-timestamp|art_x",
            "2026-01-01T00:00:00|art_x",
            "2026-01-01T00:00:00.000+00:00|",
            "2026-01-01T00:00:00.000+00:00|art_x|extra",
            "x" * 513,
            42,
        ):
            with (
                self.subTest(cursor=cursor),
                self.assertRaisesRegex(FolioError, "invalid artifact_list cursor"),
            ):
                self.service.list_artifacts("acme", cursor=cursor)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
