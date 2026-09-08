# Hyperset hosted-auth E2E gate

Gate: `fl-urj.5.2`.

[`tests/hyperset_hosted_consumer.py`](../../tests/hyperset_hosted_consumer.py)
is an external black-box consumer. It imports no Folio package and does not
open a database or blob path. Every artifact, graph, search, chunk, version,
and persistence assertion uses the public Streamable HTTP MCP endpoint and the
official MCP Python client.

## What it proves

1. Three signed test-issuer access tokens are checked for an asymmetric
   compact-JWS algorithm, `kid`, signature bytes, exact `iss`, audience,
   subject, and active lifetime, then cryptographically verified against the
   issuer's published JWKS.
2. The owner token creates and reads two artifacts in tenant A, writes an
   immutable second version with exact parent and provenance, links the
   artifacts, and exercises read/chunk/search/grep/traversal/history.
3. Tenant B's token cannot read the artifact or chunk, list its history,
   traverse its graph, search its marker, or grep its marker. Error responses
   are checked for fixture content and identifiers.
4. An external membership hook disables the tenant-A member subject. The same
   bearer credential is retried until it fails or the configured revocation
   bound expires.
5. An external restart hook restarts the deployed service. The still-active
   owner token then compares exact public read data, version history, graph
   data, content hash, and provenance with the pre-restart snapshot.

The revoke and restart hooks are orchestration seams, not Folio access paths.
They must call the deployment's test-only admin/restart controls, never read
Folio storage, and return only after the requested operation is complete. The
revocation hook receives the member's `(issuer, subject)` subject in
`FOLIO_TEST_REVOKED_SUBJECT`; no bearer token is passed to it.

## Deterministic command

After a hosted deployment and test issuer are running:

```sh
FOLIO_GATE_COMMAND='make hosted-e2e' \
FOLIO_EVIDENCE_PATH=/tmp/fl-urj-5.2.json \
FOLIO_SOURCE_SHA="$SOURCE_SHA" \
FOLIO_IMAGE_DIGEST="$IMAGE_DIGEST" \
FOLIO_TEST_ISSUER_URL='https://issuer.test.example' \
FOLIO_TEST_ISSUER_METADATA_URL='https://issuer.test.example/.well-known/openid-configuration' \
FOLIO_EXPECTED_AUDIENCE='https://folio.test.example/mcp' \
FOLIO_EXPECTED_TENANT_A='tenant-a' \
FOLIO_EXPECTED_TENANT_B='tenant-b' \
FOLIO_TENANT_A_MCP_URL='https://tenant-a.folio.test.example/mcp' \
FOLIO_TENANT_B_MCP_URL='https://tenant-b.folio.test.example/mcp' \
FOLIO_TOKEN_COMMAND_A_OWNER='issuer-test-cli access-token --profile a-owner' \
FOLIO_TOKEN_COMMAND_A_MEMBER='issuer-test-cli access-token --profile a-member' \
FOLIO_TOKEN_COMMAND_B='issuer-test-cli access-token --profile b' \
FOLIO_REVOKE_COMMAND='docker compose -p folio-hosted-e2e exec -T auth revoke-membership' \
FOLIO_RESTART_COMMAND='docker compose -p folio-hosted-e2e restart folio' \
FOLIO_REVOCATION_BOUND_SECONDS=10 \
make hosted-e2e
```

The token commands may instead be replaced by `FOLIO_TOKEN_A_OWNER`,
`FOLIO_TOKEN_A_MEMBER`, and `FOLIO_TOKEN_B` values obtained from the test
issuer. Raw tokens are accepted only in memory and are never written to the
evidence file. Token commands must print either the compact access token or a
JSON OAuth response containing `access_token`. The metadata URL is required;
the discovery document must contain the exact issuer and a `jwks_uri` before
MCP calls begin.

For a hosted Docker run, use the exact same target URLs, image digest, project
name, token commands, revoke command, and restart command. The gate does not
run `docker compose up`, infer a service name, or inspect Compose storage;
deployment setup remains outside the consumer boundary.

## Evidence

The command writes one redacted JSON document to `FOLIO_EVIDENCE_PATH`,
defaulting to `/tmp/folio-lattice-fl-urj-5.2.json`. Its schema is
[`hyperset-hosted-evidence.schema.json`](hyperset-hosted-evidence.schema.json).
It records gate status, public endpoints, source/image fingerprints, token
claim metadata and short fingerprints, assertion outcomes, artifact/version/
edge IDs, SHA-256 hashes, and exact provenance snapshots. It excludes bearer
tokens, cookies, private keys, command output, and content bytes.

Exit codes are `0` for `pass`, `1` for an assertion or deployment failure, and
`2` for `blocked` configuration. The bounded hosted OIDC adapter is now in the
repository, but a real hosted deployment, external test issuer, revocation
hook, and restart hook are still required. A missing external fixture must
return `blocked`; it is never replaced with a local-mode pass.
