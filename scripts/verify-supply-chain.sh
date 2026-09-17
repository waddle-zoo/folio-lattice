#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "supply-chain verification failed: $1" >&2
  exit 1
}

: "${SOURCE_SHA:?SOURCE_SHA is required}"
: "${SOURCE_REPOSITORY:?SOURCE_REPOSITORY is required}"
: "${WORKFLOW_REF:?WORKFLOW_REF is required}"
: "${BUILDER_ID:?BUILDER_ID is required}"
: "${IMAGE_REF:?IMAGE_REF must be a digest reference}"
: "${SBOM_PATH:?SBOM_PATH is required}"
: "${LICENSE_PATH:?LICENSE_PATH is required}"
: "${VULNERABILITY_PATH:?VULNERABILITY_PATH is required}"
: "${PROVENANCE_PATH:?PROVENANCE_PATH is required}"
REQUIRE_SIGNATURE="${REQUIRE_SIGNATURE:-1}"
[[ "$REQUIRE_SIGNATURE" == 0 || "$REQUIRE_SIGNATURE" == 1 ]] \
  || fail "REQUIRE_SIGNATURE must be 0 or 1"
if [[ "$REQUIRE_SIGNATURE" == 1 ]]; then
  : "${SIGNATURE_PATH:?SIGNATURE_PATH is required when REQUIRE_SIGNATURE=1}"
  : "${ATTESTATION_PATH:?ATTESTATION_PATH is required when REQUIRE_SIGNATURE=1}"
fi

[[ "$SOURCE_SHA" == "$(git rev-parse HEAD)" ]] || fail "source SHA does not match checkout"
git diff --quiet || fail "tracked checkout has unstaged changes"
git diff --cached --quiet || fail "tracked checkout has staged changes"
[[ "$IMAGE_REF" =~ @sha256:[0-9a-f]{64}$ ]] || fail "image is not pinned to a digest"

[[ -s "$SBOM_PATH" ]] || fail "SBOM is missing"
[[ -s "$LICENSE_PATH" ]] || fail "license report is missing"
[[ -s "$VULNERABILITY_PATH" ]] || fail "vulnerability report is missing"
[[ -s "$PROVENANCE_PATH" ]] || fail "provenance is missing"
if [[ "$REQUIRE_SIGNATURE" == 1 ]]; then
  [[ -s "$SIGNATURE_PATH" ]] || fail "signature verification is missing"
  [[ -s "$ATTESTATION_PATH" ]] || fail "attestation verification is missing"
fi

jq -e '
  (.bomFormat == "CycloneDX" and (.components | length > 0)) or
  (.spdxVersion | strings | startswith("SPDX-"))
' "$SBOM_PATH" >/dev/null || fail "SBOM is not populated CycloneDX or SPDX"
jq -e '(.components | length > 0)' "$LICENSE_PATH" >/dev/null \
  || fail "license report is not populated"

jq -e '
  def high:
    if type == "number" then . >= 7
    elif type == "string" then
      ((ascii_upcase == "HIGH") or (ascii_upcase == "CRITICAL") or
       ((try tonumber catch 0) >= 7))
    else false
    end;
  if (.matches? != null) then
    ([.matches[]?.vulnerability.severity] | any(. == "Critical" or . == "High")) | not
  elif (.runs? != null) then
    ([.runs[]?.tool.driver.rules[]?.properties?.["security-severity"] // empty | high]
      | any) | not
  else false
  end
' "$VULNERABILITY_PATH" >/dev/null || fail "Critical/High vulnerability found"

image_name="${IMAGE_REF%@*}"
digest="${IMAGE_REF##*@}"

verify_cosign_binding() {
  local path="$1"
  local label="$2"
  jq -e \
    --arg image "$image_name" \
    --arg digest "$digest" \
    '
      type == "array" and length > 0 and
      any(.[];
        (.critical.image["docker-manifest-digest"] //
          .critical.image["Docker-manifest-digest"]) == $digest and
        (.critical.identity["docker-reference"] == $image or
          .critical.identity["docker-reference"] == ($image | split("@")[0]) or
          .critical.identity["docker-reference"] ==
            ($image | sub(":([^/:]+)$"; "")))
      )
    ' "$path" >/dev/null \
    || fail "$label does not bind the signed object to the image digest"
}

if [[ "$REQUIRE_SIGNATURE" == 1 ]]; then
  verify_cosign_binding "$SIGNATURE_PATH" "signature verification"
  verify_cosign_binding "$ATTESTATION_PATH" "attestation verification"
  jq -e 'type == "array" and any(.[].payload?; strings | length > 0)' \
    "$ATTESTATION_PATH" >/dev/null \
    || fail "attestation verification does not contain a payload"
fi

jq -e \
  --arg source "$SOURCE_SHA" \
  --arg digest "${digest#sha256:}" \
  --arg image "$image_name" \
  --arg repository "$SOURCE_REPOSITORY" \
  --arg workflow "$WORKFLOW_REF" \
  --arg builder "$BUILDER_ID" \
  '
    ._type == "https://in-toto.io/Statement/v1" and
    .predicateType == "https://slsa.dev/provenance/v1" and
    ([.subject[]? | select(.name == $image and .digest.sha256 == $digest)] | length == 1) and
    .predicate.buildDefinition.externalParameters.source.uri == $repository and
    .predicate.buildDefinition.externalParameters.source.digest.sha1 == $source and
    ([.predicate.buildDefinition.resolvedDependencies[]?
      | select(.uri == $repository and .digest.sha1 == $source)] | length >= 1) and
    .predicate.buildDefinition.externalParameters.workflow.ref == $workflow and
    .predicate.runDetails.builder.id == $builder and
    (.predicate.runDetails.metadata.invocationId | strings | length > 0)
  ' "$PROVENANCE_PATH" >/dev/null \
  || fail "provenance fields do not bind source, image, workflow, and builder"

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

jq -n \
  --arg source_sha "$SOURCE_SHA" \
  --arg image_ref "$IMAGE_REF" \
  --arg sbom_sha256 "$(hash_file "$SBOM_PATH")" \
  --arg license_sha256 "$(hash_file "$LICENSE_PATH")" \
  --arg vulnerability_sha256 "$(hash_file "$VULNERABILITY_PATH")" \
  --arg signature "$(if [[ "$REQUIRE_SIGNATURE" == 1 ]]; then printf verified; else printf pending; fi)" \
  '{status: "pass", source_sha: $source_sha, image_ref: $image_ref,
    sbom_sha256: $sbom_sha256, license_sha256: $license_sha256,
    vulnerability_sha256: $vulnerability_sha256, signature: $signature,
    provenance: "verified", redaction: "no secrets"}'
