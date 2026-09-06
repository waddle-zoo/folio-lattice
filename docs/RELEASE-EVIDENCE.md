# Release evidence

## Candidate

- Date: 2026-09-05
- Baseline: `d30a837`
- Candidate implementation commit: recorded by the follow-up evidence commit
- Claim: bounded local public-contract UI and renderer security slice
- Enterprise-ready: **no**

## Required evidence

Results are recorded only after the exact command completes. A passing private
service or database test cannot replace an MCP/UI/browser/Docker gate.

| Gate | Run 1 | Run 2 |
| --- | --- | --- |
| Ruff, formatting, mypy, pytest, coverage (`make check`) | Pass: 26 tests, 83.99% | Pass: 26 tests, 83.99% |
| Real-browser UI/adversarial suite | Pass: 2 Chrome tests inside full gate | Pass: 2 Chrome tests inside full gate |
| Container build (`make docker-build`) | Pass | Pass; final image `sha256:c38f651b8661d31e844bd1ac6494f60ec9c308def2c75de53767bec9a33e1423` |
| Fresh-volume public MCP/UI/renderer Docker flow | Pass; isolated project and volume removed | Pass; isolated project and volume removed; final-image rerun also passed |
| Hosted-mode startup refusal | Pass in full suite | Pass in full suite |

Environment: macOS/Darwin, Python 3.12.10, Google Chrome 152.0.7977.77,
Docker client/server 20.10.17, MCP Python SDK 2.1.1.

The first candidate `make check` exposed 78.44% coverage after all 25 tests
passed. The gate correctly failed. Focused adapter failure-path tests were added;
the final repeated runs pass 26 tests at 83.99%. A final Docker command exceeded
one 30-second tool-output window during its sequential service restarts; service
logs showed the complete assertions, and an attached-session rerun produced the
expected `status: ok` record. Neither event is counted as a passing run by
itself.

## Reproduction

Use the exact commands in [`SECURITY-TEST-MATRIX.md`](SECURITY-TEST-MATRIX.md).
The Docker project name and host ports are intentionally isolated. Remove that
project with `docker compose -p fl-enterprise-validation down -v` after each
run; do not target other Compose projects or volumes.

The Docker flow must create its fixtures with an official MCP client, operate
on them through the UI and browser, restart services, and verify exact content,
version, hash, graph, search, bridge, headers, and failure behavior through
public endpoints.

Recorded Docker result:

```json
{"attached_bridge":"verified","persistence":"verified","public_ui_gateway":"verified","renderer":"verified_without_storage_mount","status":"ok"}
```

The Docker assertion also checks sandbox/CSP on successful HTML and rejected
renderer methods. The renderer container had an empty mount list. Restarted MCP
reads returned exact version, SHA-256, and text created before restart.

The real-browser flow used Chrome DevTools only as an input/accessibility
driver. It selected an actual upload file, submitted the production forms,
observed `aria-busy`, inspected the accessibility tree, used Enter to open an
artifact, and verified empty, success, missing, and optimistic-conflict states.
It exercised content search, literal grep, chunk read, graph link/navigation,
history, HTML/JavaScript/CSS preview selection, bridge allow/deny, sibling-frame
spoof rejection, and hostile CSP/sandbox behavior. Fixtures were created through
public MCP or the production upload form, never through service/database calls.

## Known product limits and release blockers

- Hosted mode has no authentication adapter and refuses startup. Credentials,
  sessions, subjects, ACLs, grants, sharing, and revocation do not exist.
- Local mode derives tenant and actor from process environment. Loopback Compose
  is for trusted local development only.
- There has been no independent penetration test, browser matrix, assistive-
  technology human pass, audit export/retention review, backup/restore exercise,
  TLS deployment profile, or operational rate-limit design.
- Search is SQLite FTS5 over chunk content. Grep is literal. Traversal follows
  outgoing edges only. There are no backlinks, path/subgraph queries, name/path
  filters, or graph metadata search.

These blockers prevent an enterprise-readiness claim even when every automated
gate in this file passes.
