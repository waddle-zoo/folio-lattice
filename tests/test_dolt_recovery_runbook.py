from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNBOOK = Path(__file__).parents[1] / "docs/qa/dolt-recovery-runbook.md"


class DoltRecoveryRunbookTests(unittest.TestCase):
    def test_runbook_requires_diagnostics_and_safe_path_identity(self) -> None:
        text = RUNBOOK.read_text()
        for required in (
            "kill -QUIT",
            "gt dolt status",
            "gt escalate -s HIGH",
            "lsof -nP",
            "-iTCP:3311",
            "gastown/folio_lattice/.beads/dolt",
            "gt dolt start",
            "SHOW DATABASES",
            "temporary data-dir",
            "fixture\ndatabases",
            "GT_DOLT_DATA=",
            "SIGQUIT",
            "may terminate the Dolt",
            'dolt --data-dir="$GT_DOLT_DATA" sql -q',
            "do not send a second stop",
        ):
            self.assertIn(required, text)
        self.assertNotIn("~/gt/.dolt-data", text)
        self.assertNotIn("without killing it", text)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("noms/LOCK", text.replace("never remove `noms/LOCK`", ""))

    def test_isolated_fixture_selects_only_verified_rig_impostor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "gt" / ".dolt-data"
            impostor = root / "gastown" / "folio_lattice" / ".beads" / "dolt"
            unrelated = root / "other" / "dolt"
            processes = (
                {"pid": 101, "cwd": str(configured), "data_dir": str(configured)},
                {"pid": 202, "cwd": str(impostor), "data_dir": str(impostor)},
                {"pid": 303, "cwd": str(unrelated), "data_dir": str(unrelated)},
            )
            candidates = [
                process
                for process in processes
                if process["data_dir"].startswith(str(impostor))
                and process["data_dir"] != str(configured)
            ]
            self.assertEqual([process["pid"] for process in candidates], [202])
            self.assertNotEqual(candidates[0]["pid"], 101)
            self.assertFalse((impostor / ".dolt" / "noms" / "LOCK").exists())

    def test_dolt_global_data_dir_order_is_supported(self) -> None:
        dolt = shutil.which("dolt")
        if dolt is None:
            self.skipTest("dolt CLI is not installed")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [dolt, f"--data-dir={directory}", "sql", "--help"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dolt sql", result.stdout)


if __name__ == "__main__":
    unittest.main()
