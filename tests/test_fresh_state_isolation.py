from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class FreshStateIsolationTests(unittest.TestCase):
    def test_runner_records_unique_project_volume_ports_and_identity(self) -> None:
        root = Path(__file__).parents[1]
        candidate = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        script = root / "scripts" / "repeat-fresh-state.sh"
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            blockers = temp / "blockers.json"
            blockers.write_text('[{"status":"closed"}]', encoding="utf-8")
            evidence = temp / "evidence.json"
            environment = {
                **os.environ,
                "FOLIO_RELEASE_CANDIDATE_SHA": candidate,
                "FOLIO_FRESH_STATE_BLOCKERS_PATH": str(blockers),
                "FOLIO_FRESH_STATE_EVIDENCE": str(evidence),
                "FOLIO_FRESH_STATE_RUNS": "2",
                "FOLIO_FRESH_STATE_PORT_BASE": "19000",
            }
            result = subprocess.run(
                [str(script), "--", "sh", "-c", 'test "$FOLIO_TENANT_ID" != hyperset-v0'],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record["status"], "pass")
        self.assertEqual(
            record["project_policy"], "unique project/volume/ports/tenant/actor per run"
        )
        self.assertEqual(len(record["runs"]), 2)
        self.assertEqual(len({run["compose_project"] for run in record["runs"]}), 2)
        for run in record["runs"]:
            self.assertEqual(run["volume_scope"], "project-scoped")
            self.assertNotIn(run["control_port"], {"8000", 8000})
            self.assertNotIn(run["renderer_port"], {"8001", 8001})
            self.assertNotIn(run["tenant_id"], {"dev", "hyperset-v0"})
            self.assertIn(run["compose_cleanup"], {"pass", "not_available"})


if __name__ == "__main__":
    unittest.main()
