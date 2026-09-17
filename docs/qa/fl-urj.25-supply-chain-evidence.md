# fl-urj.25 supply-chain evidence

Date: 2026-09-17

This is an independent supply-chain audit from a clean worktree at the exact
canonical source. It records evidence and blockers; it does not promote an
image or claim release readiness.

## Exact source and image under test

| item | value |
| --- | --- |
| canonical source commit | `763c3d1e48434bbb0950b5ec57ebbdaf6a46f756` |
| canonical source tree before this evidence file | `3461fc6da6f566978efa91d4a9242197293cb973` |
| isolated audit worktree | `/tmp/folio-g4-supply-chain.rDyJ31` |
| local image tag | `folio-lattice-supply-chain:sha-763c3d1` |
| local image ID/config digest | `sha256:e02a04f68681b804701cbadc98601493bbc8c96ba50d9ba3395e0770cf481f82` |
| local image RepoDigests | `[]` |
| image labels | `org.opencontainers.image.revision=763c3d1e48434bbb0950b5ec57ebbdaf6a46f756`, `org.opencontainers.image.source=https://github.com/waddle-zoo/folio-lattice` |

The image was built from the clean worktree with:

```text
docker build --build-arg VCS_REF=763c3d1e48434bbb0950b5ec57ebbdaf6a46f756 \
  --tag folio-lattice-supply-chain:sha-763c3d1 .
```

The saved archive has config
`e02a04f68681b804701cbadc98601493bbc8c96ba50d9ba3395e0770cf481f82.json`,
ten layers, Linux/arm64 metadata, and `User=folio`. This binds the archive
used for the independent scan to the local image ID and source label. The
archive is disposable local evidence and is not committed; its exact hash is
below so it can be reproduced and compared.

## Independent artifact evidence

Tools used: Syft `1.52.0`, Grype `0.119.0`, and Cosign `3.1.3`.

The archive was generated with `docker save`, scanned independently with:

```text
EVIDENCE_DIR=/tmp/fl25-supply-chain-evidence.nvnCrm
syft docker-archive:"$EVIDENCE_DIR/folio-image.tar" \
  -o cyclonedx-json > independent-sbom.cdx.json
grype sbom:"$EVIDENCE_DIR/independent-sbom.cdx.json" -o sarif \
  --file "$EVIDENCE_DIR/independent-vulnerability.sarif"
jq '{components: [(.components // [])[] | {"bom-ref": ."bom-ref", name, version, licenses}]}' \
  "$EVIDENCE_DIR/independent-sbom.cdx.json" > "$EVIDENCE_DIR/independent-licenses.json"
```

The durable hashes and parsed results are:

| artifact | SHA-256 | verified result |
| --- | --- | --- |
| saved exact-source image archive | `361b3b9a89319281ceec5a2d66216fcade0fc27647bec178d3133085c322620e` | config matches image ID above |
| independent CycloneDX SBOM | `31d1c46789230e411362f98eac512e4d7edee18e6caad0be6480ca4360f3a8f3` | CycloneDX 1.7, 4,466 components |
| redacted license inventory derived from that SBOM | `6265b481e901accf8de682d7adaff3c9bc7355421505cb9a760577d888bd8c4a` | populated component inventory |
| independent vulnerability SARIF | `53867bbbc61b83e443c25f69c8069fe3c1f42ee8c095bc7fb0ec20fbb9c58433` | 277 rules/results; 13 Critical, 84 High, 97 High-or-Critical |

The vulnerability policy is a hard blocker: the repository verifier rejects
any SARIF numeric `security-severity >= 7`, and the independent scan contains
97 such findings. No vulnerability result was suppressed or replaced with a
placeholder.

## Policy and repository checks

The clean checkout passed the direct-dependency policy and immutable release
input checks:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH python scripts/verify_dependency_policy.py
{"dependencies":["mcp","mypy","PyJWT","pytest","pytest-cov","ruff","websockets"],"lock_package_count":45,"policy":"direct dependencies are bounded and present in uv.lock","status":"pass","violations":[]}

SOURCE_SHA=763c3d1e48434bbb0950b5ec57ebbdaf6a46f756 \
  bash scripts/verify-release-inputs.sh
{"status":"pass","source_sha":"763c3d1e48434bbb0950b5ec57ebbdaf6a46f756","action_count":15,"base_image_count":2,"immutable_refs":true,"tracked_checkout":"clean","untracked_inputs":"clean"}
```

The Docker base references are:

```text
ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c
python:3.12.13-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
```

Focused policy tests passed: `5 passed in 1.56s`.

With the explicit real Chrome executable, the complete repository check passed:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH \
FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
make check
```

Result: Ruff, format, and mypy passed; `233 passed, 8 skipped in 74.61s`
with `80.61%` coverage and process status `0`. The browser suite independently
passed `6 passed in 24.59s` with the same executable.

## Registry, provenance, signing, and promotion blockers

No published immutable image was available for this source. The exact checks
returned:

```text
docker manifest inspect ghcr.io/waddle-zoo/folio-lattice:sha-763c3d1e48434bbb0950b5ec57ebbdaf6a46f756
manifest unknown
status=1

docker image inspect folio-lattice-supply-chain:sha-763c3d1 --format \
  'RepoDigests={{json .RepoDigests}} Id={{.Id}}'
RepoDigests=[] Id=sha256:e02a04f68681b804701cbadc98601493bbc8c96ba50d9ba3395e0770cf481f82

COSIGN_CERTIFICATE_IDENTITY_REGEXP='https://github.com/waddle-zoo/folio-lattice/.github/workflows/supply-chain.yml@refs/tags/.*' \
COSIGN_CERTIFICATE_OIDC_ISSUER='https://token.actions.githubusercontent.com' \
cosign verify ghcr.io/waddle-zoo/folio-lattice@sha256:e02a04f68681b804701cbadc98601493bbc8c96ba50d9ba3395e0770cf481f82
DENIED: requested access to the resource is denied
status=1
```

Therefore this local audit has no registry manifest digest, signed image,
verified attestation, or CI-generated SLSA provenance to inspect. The local
Docker Buildx is `v0.9.1` and does not expose `--sbom` or `--provenance`; the
workflow statically contains pinned actions and the intended `sbom: true` and
`provenance: mode=max` settings, but that release job was not executed here.

The explicit blockers are:

1. The image fails the configured vulnerability cutoff with 97 High-or-Critical
   findings.
2. GHCR has no manifest for the source tag, and the local image has no
   `RepoDigests` entry; immutable digest promotion cannot be verified.
3. No GHCR pull/push credentials or GitHub Actions OIDC signing identity is
   available in this audit environment; Cosign signature and attestation
   verification was denied. Signing, provenance attestation, and promotion
   remain unexecuted and unverified.

No release-readiness or promotion claim is made. Closing these blockers
requires a clean CI release run with the vulnerability policy satisfied, an
immutable pushed digest, and independently verifiable Cosign signature,
attestation, and source/image binding.
