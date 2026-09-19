from __future__ import annotations

import asyncio
import base64
import os
import unittest
from typing import Any
from unittest.mock import patch

from hyperset_consumer import exercise


class _Response:
    def __init__(self, result: Any) -> None:
        self.is_error = False
        self.content: list[Any] = []
        self.structured_content = {"result": result}


class _Client:
    instances: list[_Client] = []

    def __init__(self, url: str, *, raise_exceptions: bool) -> None:
        self.url = url
        self.raise_exceptions = raise_exceptions
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.source: dict[str, Any] | None = None
        self.updated_text = ""
        _Client.instances.append(self)

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _Response:
        self.calls.append((name, arguments))
        if name == "artifact_create":
            content = base64.b64decode(arguments["content_base64"]).decode()
            if self.source is None:
                self.source = {
                    "artifact": {"id": "source", "tenant_id": "tenant"},
                    "version": {"id": "v1"},
                }
                self.updated_text = content
                return _Response(
                    {"artifact": {"id": "source", "tenant_id": "tenant"}, "version": {"id": "v1"}}
                )
            return _Response(
                {
                    "artifact": {"id": "target", "tenant_id": "tenant"},
                    "version": {"id": "target-v1"},
                }
            )
        if name == "artifact_write":
            self.updated_text = base64.b64decode(arguments["content_base64"]).decode()
            return _Response(
                {
                    "id": "v2",
                    "parent_version_id": "v1",
                    "blob_hash": "a" * 64,
                    "actor": os.environ.get("FOLIO_ACTOR", "hyperset"),
                }
            )
        if name == "graph_link":
            return _Response({"target_artifact_id": "target"})
        if name == "artifact_search":
            return _Response([{"chunk_id": "chunk"}])
        if name == "graph_traverse":
            return _Response([{"target_artifact_id": "target"}])
        if name == "artifact_versions":
            return _Response([{"id": "v1"}, {"id": "v2"}])
        if name == "artifact_read":
            return _Response({"text": self.updated_text})
        if name == "artifact_read_chunk":
            return _Response({"offset_unit": "unicode_code_points"})
        if name == "artifact_grep":
            return _Response([{"offset_unit": "unicode_code_points"}])
        raise AssertionError(f"unexpected tool {name}")


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


class HypersetConsumerTests(unittest.TestCase):
    def test_default_actor_is_hyperset_without_process_context(self) -> None:
        _Client.instances.clear()
        with patch.dict(os.environ, {}, clear=True), patch("hyperset_consumer.Client", _Client):
            result = asyncio.run(exercise("http://fresh-state.test"))

        self.assertEqual(result["actor"], "hyperset")
        self.assertTrue(
            all(
                not _contains_key(arguments, "actor") for _, arguments in _Client.instances[0].calls
            )
        )

    def test_expected_actor_comes_from_process_context_without_request_identity(self) -> None:
        for actor in ("fresh-runner-state-a", "fresh-runner-state-b"):
            with self.subTest(actor=actor):
                _Client.instances.clear()
                with (
                    patch.dict(os.environ, {"FOLIO_ACTOR": actor}),
                    patch("hyperset_consumer.Client", _Client),
                ):
                    result = asyncio.run(exercise("http://fresh-state.test"))

                self.assertEqual(result["actor"], actor)
                client = _Client.instances[0]
                self.assertEqual(client.url, "http://fresh-state.test/mcp")
                self.assertTrue(client.raise_exceptions)
                self.assertTrue(
                    all(not _contains_key(arguments, "actor") for _, arguments in client.calls)
                )


if __name__ == "__main__":
    unittest.main()
