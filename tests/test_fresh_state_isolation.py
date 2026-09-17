from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class FreshStateIsolationTests(unittest.TestCase):
    def test_make_rejects_shell_metacharacter_project_names(self) -> None:
        root = Path(__file__).parents[1]
        valid_environment = {**os.environ, "FOLIO_COMPOSE_PROJECT": "folio-fresh-test_1"}
        valid = subprocess.run(
            ["make", "validate-compose-project"],
            cwd=root,
            env=valid_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        for project in (
            "safe; printf SHELL_INJECTION_MARKER",
            "safe project",
            "$(printf SHELL_INJECTION_MARKER)",
            "`printf SHELL_INJECTION_MARKER`",
            "$HOME",
        ):
            with self.subTest(project=project):
                environment = {**os.environ, "FOLIO_COMPOSE_PROJECT": project}
                rejected = subprocess.run(
                    ["make", "validate-compose-project"],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("project-name grammar", rejected.stderr)

                dry_run = subprocess.run(
                    ["make", "-n", "docker-down"],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
                self.assertNotIn("SHELL_INJECTION_MARKER", dry_run.stdout)
                self.assertIn(
                    'COMPOSE_PROJECT_NAME="${FOLIO_COMPOSE_PROJECT:-${COMPOSE_PROJECT_NAME:-}}"',
                    dry_run.stdout,
                )

    def test_docker_test_starts_pinned_compose_and_uses_selected_urls(self) -> None:
        root = Path(__file__).parents[1]
        result = subprocess.run(
            [
                "make",
                "-n",
                "VCS_REF=1f9fb0164f1902981801aa11e9205d6e4d07beaf",
                "FOLIO_COMPOSE_PROJECT=folio-fresh-test",
                "FOLIO_HOST_PORT=19001",
                "FOLIO_RENDER_HOST_PORT=19101",
                "docker-test",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'build --build-arg VCS_REF="1f9fb0164f1902981801aa11e9205d6e4d07beaf"', result.stdout
        )
        self.assertIn(
            'COMPOSE_PROJECT_NAME="${FOLIO_COMPOSE_PROJECT:-${COMPOSE_PROJECT_NAME:-}}" docker compose up -d',
            result.stdout,
        )
        self.assertIn(
            'FOLIO_BASE_URL="${FOLIO_BASE_URL:-http://127.0.0.1:${FOLIO_HOST_PORT:-8000}}"',
            result.stdout,
        )
        self.assertIn(
            'FOLIO_RENDER_URL="${FOLIO_RENDER_URL:-http://127.0.0.1:${FOLIO_RENDER_HOST_PORT:-8001}}"',
            result.stdout,
        )

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
                "FOLIO_FRESH_STATE_MODE": "non-docker",
            }
            result = subprocess.run(
                [
                    str(script),
                    "--",
                    "sh",
                    "-c",
                    'test "$FOLIO_TENANT_ID" != hyperset-v0 && '
                    'test "$FOLIO_BASE_URL" = "http://127.0.0.1:$((19000 + FOLIO_FRESH_STATE_RUN))" && '
                    'test "$FOLIO_RENDER_URL" = "http://127.0.0.1:$((19100 + FOLIO_FRESH_STATE_RUN))" && '
                    'test "$VCS_REF" = "$FOLIO_RELEASE_CANDIDATE_SHA"',
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["mode"], "non-docker")
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
            self.assertEqual(run["compose_cleanup"], "not_applicable")
            self.assertEqual(run["port_preflight"], "pass")

    def test_shared_defaults_cannot_bypass_release_mode(self) -> None:
        root = Path(__file__).parents[1]
        candidate = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        script = root / "scripts" / "repeat-fresh-state.sh"
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            blockers = temp / "blockers.json"
            blockers.write_text('[{"status":"closed"}]', encoding="utf-8")
            environment = {
                **os.environ,
                "FOLIO_RELEASE_CANDIDATE_SHA": candidate,
                "FOLIO_FRESH_STATE_BLOCKERS_PATH": str(blockers),
                "FOLIO_FRESH_STATE_PORT_BASE": "19020",
                "FOLIO_FRESH_STATE_ALLOW_SHARED_DEFAULTS": "true",
            }
            result = subprocess.run(
                [str(script), "--", "true"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-release", result.stderr)

    def test_duplicate_ports_are_rejected_even_in_non_release_mode(self) -> None:
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
                "FOLIO_FRESH_STATE_MODE": "non-release",
                "FOLIO_FRESH_STATE_ALLOW_SHARED_DEFAULTS": "true",
                "FOLIO_FRESH_STATE_PORT_BASE": "19000",
                "FOLIO_HOST_PORT": "19022",
                "FOLIO_RENDER_HOST_PORT": "19023",
            }
            result = subprocess.run(
                [str(script), "--", "true"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate fresh-state port", result.stderr)


if __name__ == "__main__":
    unittest.main()
