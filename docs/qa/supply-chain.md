# Supply-chain gate

The `Supply chain` workflow has two deliberately separate paths:

- Pull requests run the locked-environment, dependency-policy, workflow-YAML,
  and `make check` gates.
- Release publication runs only for a `v*` tag, or for a manually dispatched
  run whose `publish` input is explicitly `true` and whose ref is a `v*` tag.
  The `release` environment remains the approval boundary.

Release inputs are checked before publishing. Every workflow action and every
Docker base image is pinned by full commit SHA or digest. Direct dependencies
must have bounded constraints and appear in `uv.lock`. The image build emits a
CycloneDX SBOM and provenance, and the vulnerability scan fails on High or
Critical findings. License inventory, SBOM, vulnerability, and provenance
artifacts contain metadata only; secrets are not uploaded.

Before signing, the verifier requires a clean checkout, the exact source SHA,
an image digest, populated SBOM/license/vulnerability evidence, and
source-bound provenance. Cosign then signs and attests that digest. The final
verification checks the signature, attestation, source repository and SHA,
workflow ref, builder identity, image subject, and invocation ID. The digest
record is the only release promotion input. This gate is evidence plumbing,
not a release-readiness claim; dependency gates `.24` and `.29` remain
separate closure blockers.
