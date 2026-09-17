# fl-urj.24 hosted runtime evidence

Date: 2026-09-17

This is fresh evidence for the hosted-runtime portion of fl-urj.24. It is
review material only: .24 remains open, and this record makes no
enterprise-readiness claim.

## Exact source and image bindings

| item | value |
| --- | --- |
| canonical source commit | `24deddcc616129c349042298766662aed9240b79` |
| canonical source tree | `939dd9acbd71190b7f081994024a4dcbffded7fd` |
| hosted-runtime source parent | `448c156543e393b990d9454897c1f9d8bf366ea2` |
| final reviewed source commit | `1224dce607087b42f64ab94987dcf2a7487320d9` |
| final reviewed source tree | `3e6dfa1dfb5c0fa678b4fe250c6c04f136893dea` |
| branch | `codex/fl-urj.24-hosted-runtime` |
| isolated worktree | `/tmp/folio-g4-final-review.7LIqeK` |
| candidate image | `folio-lattice-g4-hosted:1224dce` |
| candidate image ID | `sha256:749798473d4ccfaf0421b821b65cb2f141974e3ecd7dd4590e64a14e30cdff74` |
| candidate image revision label | `1224dce607087b42f64ab94987dcf2a7487320d9` |
| live canonical image | `rig-folio:latest`, `rig-renderer:latest` |
| live canonical image ID | `sha256:b3fd406aa8376f5ea162bf881937d1a2adc797cd37e7ec3602cf87f06b985307` |
| live image revision label | `24deddcc616129c349042298766662aed9240b79` |

The final commit is the hosted TLS guard's test-fixture alignment on top of
`448c156`; it does not change the production image payload from that source
parent. The candidate image was nevertheless rebuilt and tested under the
final 40-character source label.

Source-file SHA-256 values at the final tree:

```text
4f366e650f081bcf51a3ec6700117acf5112b2f430d1eb31d2aa0a1dc00f99b5  Dockerfile
2b9f3c565ab6c7cf1d4c2649546b3cdc9e4ea6a9cbb835bee48f6d0a509fc017  deploy/compose/hosted.yml
1f64639df912d917660eee67cd7bf4252a546b41ebcfddcda63b3bc88aa0681d  src/folio_lattice/server.py
69b42a4e82814e126bca1050919fa135ca4f3896980d2dd422a5a404caeeb9e6  tests/test_deployment.py
4181b67049d565efcd1f32adeee0ddd896af4d08051c2ec4eb7f455ca2040472  tests/test_hosted_auth_production.py
5556808a1093e384ec6b460a3f3758d7627ab40ad985dcea60371f4715d19856  tests/test_backup_restore.py
268f69c1bb2693cee9bfff9729fdef4b0b873360f07afd8a562306b4f10b1bf9  tests/test_backup_ops.py
c4798ce3b38f66aafb64096111d9daa01bfbcbd636d4f06f5577d3200baf8622  tests/test_web.py
```

## Hosted profile and readiness

The hosted compose profile now requires TLS certificate/key paths and wires
the dedicated `FOLIO_RENDER_ORIGIN` into both services. It also retains the
hosted requirements for HTTPS OIDC/JWKS, secure cookies, external key/store
adapters, immutable image revision, read-only rootfs, UID 10001, `cap_drop:
ALL`, and `no-new-privileges`. Compose validation was run with all required
values and returned `compose_config_status=0`.

The exact hosted-process positive probe used a disposable HTTPS JWKS issuer,
the source-tree external-adapter factory seam, and a fresh backup record. It
returned:

```json
{
  "status_code": 200,
  "ready": true,
  "deployment_mode": "hosted",
  "dependencies": {
    "database": {"ready": true},
    "blob": {"ready": true},
    "migration": {"ready": true},
    "provider": {"ready": true, "required": true},
    "backup_operations": {
      "ready": true,
      "required": true,
      "alerts": [],
      "metrics": {
        "backup_ready": 1,
        "backup_key_custody_ready": 1,
        "backup_store_ready": 1,
        "backup_rpo_exceeded": 0,
        "backup_stale": 0,
        "backup_age_seconds": 30.065965
      }
    },
    "tls_certificate": {"ready": true, "required": true}
  }
}
```

This positive probe validates the application contract with a disposable
external-adapter implementation; it is not evidence that vendor KMS/WORM
credentials or a production provider have been supplied.

The real hosted production test was also run without backup wiring. It
returned the expected fail-closed state: HTTPS product startup succeeded,
`/readyz` returned 503 with `backup_operations.ready=false`, and `/metrics`
reported `backup_ready=0`; the HTTPS OIDC/JWKS path then authenticated the
membership actor (`/v1/me` 200). This prevents an unconfigured backup seam
from being reported as ready.

## Exact image runtime probes

Build command:

```text
docker build --build-arg VCS_REF=1224dce607087b42f64ab94987dcf2a7487320d --tag folio-lattice-g4-hosted:1224dce .
```

The container was created from image ID
`sha256:749798473d4ccfaf0421b821b65cb2f141974e3ecd7dd4590e64a14e30cdff74`
with a read-only cert volume and a UID-owned data volume. Results:

```text
ready_code=200
database=true blob=true migration=true acl=true audit=true
provider={"ready":true,"required":false}
tls_certificate={"ready":true,"required":true}
strict-transport-security: max-age=31536000; includeSubDomains
TLS 1.2: Protocol TLSv1.2; Verify return code: 0 (ok)
TLS 1.1: exit 1 (no protocols available)
logout: HTTP/1.1 204 No Content; Secure; HttpOnly; SameSite=lax
plaintext HTTP: curl exit 52 (empty reply; TLS-only listener)
restart_ready_code=200
user=10001:10001 readonly=true capdrop=["ALL"] security=["no-new-privileges:true"]
uid=10001 gid=10001 capbnd=0000000000000000 no_new_privs=1
```

The exact image probe is local-mode TLS because hosted mode intentionally
requires a real OIDC and external-backup binding; the hosted HTTPS readiness
and backup probes above cover that profile separately. The renderer compose
service has no storage volume or published port; its renderer-specific
capability and ACL/expiry/replay/URL-log controls remain covered by the G4
browser and non-browser suites.

## Backup, migration, upgrade, and rollback

Command:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH uv run pytest -q -s tests/test_hosted_auth_production.py tests/test_deployment.py tests/test_backup_restore.py tests/test_backup_ops.py
```

Result: `31 passed, 9 subtests passed in 4.33s`.

This run includes consistency-set restore with repeatable migration,
corruption/non-empty-target refusal, rollback after readiness failure,
rollback after second-placement failure, verified-state upgrade rollback,
repeatable upgrade with blob preservation, backup RPO/age checks, external
key/store readiness, rotation-version mismatch, immutable-store protections,
metadata integrity, and fail-closed adapter wiring.

## Full repository validation

Command:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' make check
```

Result: Ruff passed; 59 files formatted; mypy passed for 22 source files;
`233 passed, 8 skipped in 81.96s`; coverage `80.61%` against an 80% floor;
process status `0`. The eight skips are the explicitly optional hosted-auth
negative seam tests, which require `FOLIO_HOSTED_AUTH_TEST_TARGET`.

## Independent QA review

A fresh detached QA worktree was created at
`/tmp/folio-g4-hosted-qa.dT8H4a` from published commit
`88d334b9e406854665adab6c45b6d7c94e63f1d8` (tree
`518f1fc766c518d7b55e92fb73f465d79b876cfa`). It was clean before testing;
the evidence document hash matched the committed `.sha256` manifest. QA ran:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' make check
```

Independent result: Ruff, format, and mypy passed; `233 passed, 8 skipped in
85.87s`; coverage `80.61%`; process status `0`.

No changes were made to the persistent Security checkout. Independent QA
review is recorded for the source/image/evidence object above; integration
and any .24 promotion remain pending mayor acknowledgement.
