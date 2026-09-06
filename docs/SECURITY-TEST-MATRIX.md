# Security regression matrix

Date: 2026-09-05

This is a reproducible pentest-style regression plan, not a certification. Each
row needs an automated test or an explicit release blocker. End-to-end setup
uses public MCP clients and browser UI actions; it does not seed or inspect the
database directly.

| ID | Attack or failure | Expected result | Evidence target |
| --- | --- | --- | --- |
| WEB-01 | Stored `<script>` and DOM escape attempt | Runs only in opaque preview; parent DOM and origin unreadable | Real Chrome hostile fixture |
| WEB-02 | `fetch`, XHR, WebSocket, EventSource, beacon | All egress denied by CSP; no receiver observes a request | Chrome plus local canary server |
| WEB-03 | top navigation, popup, form, download, nested frame | Top URL unchanged; no popup/submission/frame/load succeeds | Chrome hostile fixture |
| WEB-04 | local/session storage, IndexedDB, cookies | No host state available; opaque-origin storage calls fail | Chrome hostile fixture |
| WEB-05 | CSP/header weakening on error/content routes | Sandbox, CSP, referrer, MIME, and cache headers remain present | HTTP and Chrome tests |
| MSG-01 | sibling opaque frame spoofs a bridge request | Rejected because source window differs | Chrome bridge fixture |
| MSG-02 | non-null or lookalike origin message | Rejected before server call | Browser unit/E2E fixture |
| MSG-03 | direct bridge POST omits/wrong `Origin` | 403 and deny audit; no MCP call | ASGI and process E2E |
| MCP-01 | request write/create/link/grep/chunk/versions via artifact bridge | Denied; only read/search/traverse are attached | Bridge process E2E |
| MCP-02 | unknown attachment, upstream URL, headers, tenant, actor, or extra fields | Schema rejection; no dispatch | Bridge adversarial tests |
| MCP-03 | malformed JSON/types/IDs/bounds | 400-class generic response; server stays healthy | ASGI/process tests |
| MCP-04 | oversized body/arguments/result | 413 or bounded bridge error; no partial output | ASGI/process tests |
| MCP-05 | slow upstream/tool | Deadline exceeded; bounded error and audit duration | Injected slow-client integration test |
| MCP-06 | tool error contains internals/content | UI receives bounded public error only; audit omits content/args | Error/leak assertions |
| TEN-01 | tenant B reads tenant A artifact/version/chunk | Not found with no distinguishing metadata | Two real MCP servers, shared volume |
| TEN-02 | tenant B searches/greps for tenant A marker | Empty result | Two real MCP servers, shared volume |
| TEN-03 | tenant B traverses tenant A start/edge | Not found; no node/edge leak | Two real MCP servers, shared volume |
| VER-01 | stale optimistic write | Conflict; current pointer/history/content unchanged | UI plus public MCP verification |
| VER-02 | invalid write/link payload | No version/edge/blob becomes visible | Public MCP before/after comparison |
| AUTH-01 | start with `FOLIO_DEPLOYMENT_MODE=hosted` and no adapter | Process exits before listening with one clear gate error | Subprocess and container config test |
| AUTH-02 | local mode is mistaken for authenticated | UI banner and health payload explicitly say unauthenticated local development | HTTP/browser test |

## Reproduction gates

From a clean checkout:

```sh
make check
make browser-test
make docker-build
FOLIO_HOST_PORT=18010 FOLIO_RENDER_HOST_PORT=18011 \
  docker compose -p fl-enterprise-validation up -d --build
FOLIO_BASE_URL=http://127.0.0.1:18010 \
FOLIO_RENDER_URL=http://127.0.0.1:18011 \
  uv run python tests/test_docker_e2e.py
docker compose -p fl-enterprise-validation down -v
```

Run the local check/browser gates twice. Run Docker from a newly removed named
volume twice where practical. Capture commit, OS/browser, image digest, commands,
test counts, coverage, and any exception in `docs/RELEASE-EVIDENCE.md`. Never
remove or reuse another project's containers, network, or volume.
