import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SupplyChainPolicyTests(unittest.TestCase):
    def test_release_input_verifier_rejects_untracked_source(self) -> None:
        verifier_source = ROOT / "scripts/verify-release-inputs.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            scripts = fixture_root / "scripts"
            workflows = fixture_root / ".github" / "workflows"
            scripts.mkdir()
            workflows.mkdir(parents=True)
            verifier = scripts / verifier_source.name
            verifier.write_text(verifier_source.read_text())
            verifier.chmod(0o755)
            (workflows / "release.yml").write_text(
                "uses: actions/checkout@" + "a" * 40 + "\n", encoding="utf-8"
            )
            (fixture_root / "Dockerfile").write_text(
                "FROM python:3.12-slim@sha256:" + "b" * 64 + "\n", encoding="utf-8"
            )
            for required in ("pyproject.toml", "uv.lock"):
                (fixture_root / required).write_text("fixture\n", encoding="utf-8")
            self._run_git(fixture_root, "init", "-q")
            self._run_git(fixture_root, "config", "user.email", "qa@example.invalid")
            self._run_git(fixture_root, "config", "user.name", "QA fixture")
            self._run_git(fixture_root, "add", ".")
            self._run_git(fixture_root, "commit", "-qm", "fixture")
            source_sha = self._run_git(fixture_root, "rev-parse", "HEAD")
            environment = {**os.environ, "SOURCE_SHA": source_sha}

            passed = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(passed.stdout)["untracked_inputs"], "clean")

            (fixture_root / "src" / "untracked.py").parent.mkdir()
            (fixture_root / "src" / "untracked.py").write_text("source\n", encoding="utf-8")
            rejected = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("untracked source or build inputs", rejected.stderr)

    def test_dependency_policy_is_green_for_checked_in_inputs(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/verify_dependency_policy.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["violations"], [])

    def test_workflows_and_base_images_are_immutable(self) -> None:
        workflow_text = "\n".join(
            path.read_text() for path in (ROOT / ".github/workflows").glob("*.yml")
        )
        action_refs = re.findall(
            r"^\s*-?\s*uses:\s*[^\s]+@([0-9a-f]{40})(?:\s|$)",
            workflow_text,
            re.MULTILINE,
        )
        self.assertGreater(len(action_refs), 0)
        all_action_refs = re.findall(
            r"^\s*-?\s*uses:\s*[^\s]+@([^\s]+)", workflow_text, re.MULTILINE
        )
        self.assertEqual(all_action_refs, action_refs)

        supply_chain = (ROOT / ".github/workflows/supply-chain.yml").read_text()
        for required in (
            "workflow_dispatch",
            "publish",
            "startsWith(github.ref, 'refs/tags/v')",
            "REQUIRE_SIGNATURE",
            "sbom: true",
            "provenance: mode=max",
            "cosign verify",
            "cosign verify-attestation",
            "IMAGE_DIGEST_REF",
            "verify-supply-chain.sh",
            "severity-cutoff: high",
            "--output json",
            "overwrite: false",
            "image_revision",
            "jq -s -e -r",
            "expected exactly one non-empty attestation payload",
        ):
            self.assertIn(required, supply_chain)

        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn('org.opencontainers.image.revision="$VCS_REF"', dockerfile)
        self.assertIn("ARG VCS_REF=unknown", dockerfile)
        base_images = re.findall(r"^FROM\s+(?:--[^\s]+\s+)*([^\s]+)", dockerfile, re.MULTILINE)
        self.assertGreater(len(base_images), 0)
        self.assertTrue(all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in base_images))
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("VCS_REF: ${VCS_REF:?", compose)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("build --build-arg VCS_REF", makefile)

    def test_supply_chain_verifier_rejects_tampered_provenance(self) -> None:
        verifier_source = ROOT / "scripts/verify-supply-chain.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            scripts = fixture_root / "scripts"
            scripts.mkdir()
            verifier = scripts / verifier_source.name
            verifier.write_text(verifier_source.read_text())
            verifier.chmod(0o755)
            (fixture_root / "fixture.txt").write_text("clean fixture\n")
            self._run_git(fixture_root, "init", "-q")
            self._run_git(fixture_root, "config", "user.email", "qa@example.invalid")
            self._run_git(fixture_root, "config", "user.name", "QA fixture")
            self._run_git(fixture_root, "add", "fixture.txt", "scripts/verify-supply-chain.sh")
            self._run_git(fixture_root, "commit", "-qm", "fixture")
            source_sha = self._run_git(fixture_root, "rev-parse", "HEAD")

            evidence = fixture_root / "evidence"
            evidence.mkdir()
            (evidence / "sbom.json").write_text(
                json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "fixture"}]})
            )
            (evidence / "licenses.json").write_text(
                json.dumps({"components": [{"name": "fixture"}]})
            )
            (evidence / "vulnerabilities.json").write_text(
                json.dumps({"runs": [{"tool": {"driver": {"rules": []}}, "results": []}]})
            )
            image = f"ghcr.io/example/folio-lattice:sha-{source_sha}@sha256:{'0' * 64}"
            provenance = self._provenance(source_sha, image.split("@", 1)[0])
            provenance_path = evidence / "provenance.json"
            provenance_path.write_text(json.dumps(provenance))
            environment = {
                **os.environ,
                "SOURCE_SHA": source_sha,
                "SOURCE_REPOSITORY": "https://github.com/example/folio-lattice",
                "WORKFLOW_REF": "example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0",
                "BUILDER_ID": "https://github.com/example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0",
                "IMAGE_REF": image,
                "SBOM_PATH": str(evidence / "sbom.json"),
                "LICENSE_PATH": str(evidence / "licenses.json"),
                "VULNERABILITY_PATH": str(evidence / "vulnerabilities.json"),
                "PROVENANCE_PATH": str(provenance_path),
                "REQUIRE_SIGNATURE": "0",
            }
            passed = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(passed.stdout)["status"], "pass")

            (evidence / "sbom.json").write_text(json.dumps({"spdxVersion": "SPDX-2.3"}))
            rejected_empty_sbom = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected_empty_sbom.returncode, 0)
            self.assertIn("SBOM", rejected_empty_sbom.stderr)

            (evidence / "sbom.json").write_text(
                json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "fixture"}]})
            )
            (evidence / "vulnerabilities.json").write_text(
                json.dumps({"runs": [{"tool": {"driver": {"rules": [{}]}}}]})
            )
            rejected_malformed_scan = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected_malformed_scan.returncode, 0)
            self.assertIn("Critical/High", rejected_malformed_scan.stderr)

            (evidence / "vulnerabilities.json").write_text(
                json.dumps({"runs": [{"tool": {"driver": {"rules": []}}, "results": []}]})
            )

            high_findings = json.loads((evidence / "vulnerabilities.json").read_text())
            high_findings["runs"][0]["tool"]["driver"]["rules"] = [
                {"properties": {"security-severity": "7.0"}}
            ]
            (evidence / "vulnerabilities.json").write_text(json.dumps(high_findings))
            rejected_vulnerability = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected_vulnerability.returncode, 0)
            self.assertIn("Critical/High", rejected_vulnerability.stderr)

            (evidence / "vulnerabilities.json").write_text(
                json.dumps({"runs": [{"tool": {"driver": {"rules": []}}, "results": []}]})
            )
            tampered = json.loads(provenance_path.read_text())
            tampered["predicate"]["buildDefinition"]["externalParameters"]["source"]["digest"][
                "sha1"
            ] = "f" * 40
            provenance_path.write_text(json.dumps(tampered))
            rejected = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("provenance fields", rejected.stderr)

    def test_supply_chain_verifier_binds_cosign_outputs_to_digest(self) -> None:
        verifier_source = ROOT / "scripts/verify-supply-chain.sh"
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            scripts = fixture_root / "scripts"
            scripts.mkdir()
            verifier = scripts / verifier_source.name
            verifier.write_text(verifier_source.read_text())
            verifier.chmod(0o755)
            (fixture_root / "fixture.txt").write_text("clean fixture\n")
            self._run_git(fixture_root, "init", "-q")
            self._run_git(fixture_root, "config", "user.email", "qa@example.invalid")
            self._run_git(fixture_root, "config", "user.name", "QA fixture")
            self._run_git(fixture_root, "add", "fixture.txt", "scripts/verify-supply-chain.sh")
            self._run_git(fixture_root, "commit", "-qm", "fixture")
            source_sha = self._run_git(fixture_root, "rev-parse", "HEAD")

            evidence = fixture_root / "evidence"
            evidence.mkdir()
            (evidence / "sbom.json").write_text(
                json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "fixture"}]})
            )
            (evidence / "licenses.json").write_text(
                json.dumps({"components": [{"name": "fixture"}]})
            )
            (evidence / "vulnerabilities.json").write_text(
                json.dumps({"runs": [{"tool": {"driver": {"rules": []}}, "results": []}]})
            )
            image_name = f"ghcr.io/example/folio-lattice:sha-{source_sha}"
            digest = "sha256:" + "0" * 64
            image = f"{image_name}@{digest}"
            provenance = self._provenance(source_sha, image_name)
            provenance_path = evidence / "provenance.json"
            provenance_path.write_text(json.dumps(provenance))
            signature_path = evidence / "signature.json"
            attestation_path = evidence / "attestation.json"
            record = {
                "critical": {
                    "identity": {"docker-reference": image_name},
                    "image": {"docker-manifest-digest": digest},
                },
                "payload": base64.b64encode(provenance_path.read_bytes()).decode(),
            }
            signature_path.write_text(json.dumps([record]))
            attestation_path.write_text(json.dumps([record]))
            environment = {
                **os.environ,
                "SOURCE_SHA": source_sha,
                "SOURCE_REPOSITORY": "https://github.com/example/folio-lattice",
                "WORKFLOW_REF": "example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0",
                "BUILDER_ID": "https://github.com/example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0",
                "IMAGE_REF": image,
                "SBOM_PATH": str(evidence / "sbom.json"),
                "LICENSE_PATH": str(evidence / "licenses.json"),
                "VULNERABILITY_PATH": str(evidence / "vulnerabilities.json"),
                "PROVENANCE_PATH": str(provenance_path),
                "SIGNATURE_PATH": str(signature_path),
                "ATTESTATION_PATH": str(attestation_path),
                "REQUIRE_SIGNATURE": "1",
            }
            passed = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(passed.stdout)["signature"], "verified")

            signature_path.write_text(json.dumps(record))
            attestation_path.write_text(json.dumps(record))
            object_output = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(object_output.returncode, 0, object_output.stderr)

            signature_path.write_text(json.dumps([record, record]))
            attestation_path.write_text(json.dumps([record, record]))
            multiple_output = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(multiple_output.returncode, 0)
            self.assertIn("signature verification", multiple_output.stderr)

            signature_path.write_text(json.dumps(record))
            attestation_path.write_text(json.dumps({"critical": record["critical"]}))
            missing_payload = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(missing_payload.returncode, 0)
            self.assertIn("attestation verification", missing_payload.stderr)

            mismatched_payload = dict(record)
            mismatched_payload["payload"] = base64.b64encode(b"{}\n").decode()
            signature_path.write_text(json.dumps(record))
            attestation_path.write_text(json.dumps(mismatched_payload))
            rejected_payload = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected_payload.returncode, 0)
            self.assertIn("attestation payload", rejected_payload.stderr)

            signature_path.write_text(json.dumps([record]))
            attestation_path.write_text(
                json.dumps(
                    [
                        {
                            "critical": {
                                "identity": {"docker-reference": image_name},
                                "image": {"docker-manifest-digest": "sha256:" + "f" * 64},
                            },
                            "payload": "e30=",
                        }
                    ]
                )
            )
            rejected = subprocess.run(
                ["bash", str(verifier)],
                cwd=fixture_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("attestation verification", rejected.stderr)

    @staticmethod
    def _run_git(cwd: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    @staticmethod
    def _provenance(source_sha: str, image: str) -> dict[str, object]:
        return {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": image, "digest": {"sha256": "0" * 64}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "externalParameters": {
                        "source": {
                            "uri": "https://github.com/example/folio-lattice",
                            "digest": {"sha1": source_sha},
                        },
                        "workflow": {
                            "ref": "example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0"
                        },
                    },
                    "resolvedDependencies": [
                        {
                            "uri": "https://github.com/example/folio-lattice",
                            "digest": {"sha1": source_sha},
                        }
                    ],
                },
                "runDetails": {
                    "builder": {
                        "id": "https://github.com/example/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/v0.1.0"
                    },
                    "metadata": {"invocationId": "fixture-run"},
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
