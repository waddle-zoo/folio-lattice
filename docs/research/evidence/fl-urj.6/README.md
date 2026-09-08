# fl-urj.6.2 UX regression evidence

Status: automated host browser pass; manual assistive-technology and Docker gates pending  
Baseline commit: `00fef28`  
Owner: UX/usability crew  
Scope: inspection UI create/open/search/grep/read/write/version/graph/preview/
attached-MCP flows, keyboard and focus behavior, accessible names, recovery,
responsive layout, and explicit local security limitations.

## What this worker added

- `tests/test_browser_e2e.py` now asserts page orientation, accessible names,
  keyboard order, no positive `tabindex`, live status roles, mobile overflow,
  creation confirmation, concurrent busy-state behavior, human-readable search
  and graph targets, attached-MCP denial semantics, full workspace control
  names, and post-error control recovery.
- `tests/test_web.py` now guards the static contract surface, core control IDs,
  debug-output exclusions, safe status copy, dark-mode styling hooks, and the
  public MCP/UI/renderer/bridge seams.
- `docs/qa/ux-usability-review-2026-09-07.md` records the defects, impact,
  proposed fixes, boundary disclosures, and host checklist.

The assertions intentionally describe the target contract. They may fail on
the current source until the listed UX defects are fixed; a red assertion is a
tracked redesign blocker, not evidence of a passing release.

## 2026-09-07 host run

The Mayor ran the merged implementation on macOS with the locked Python 3.12
environment and the host Chrome/Chromium browser. This is pre-commit evidence
for the tree immediately following `00fef28`; a post-landing browser rerun is
required against the published commit.

- `uv run pytest -q tests/test_web.py tests/test_service.py`: **16 passed**
  before the renderer regression was added; the final `make check` includes the
  expanded 17-test subset.
- `uv run pytest -q tests/test_e2e.py`: **6 passed in 7.60s**.
- `uv run pytest -q tests/test_browser_e2e.py`: **3 passed in 31.93s** after
  exercising hosted-auth recovery, keyboard focus, responsive overflow,
  create/search/grep/chunk/version/graph flows, bridge allow/deny behavior,
  hostile embedded content, iframe-only renderer access, and HTML/CSS/JS
  rendering.
- `make check`: **31 passed in 41.29s**, **84.23% coverage**, Ruff, format, and
  mypy all passed.

Host validation exposed and fixed merge defects in hosted-auth context wiring,
parallel request busy state, accessible copy/names, human-readable search and
graph labels, bridge-denial wording, dark-mode expectations, and iframe-only
renderer enforcement. No manual screen-reader result or Docker image result is
recorded here, so this evidence does not satisfy an enterprise release gate.

## Exact host-run evidence schema

Mayor/QA must append one JSON object per host run to the release record or
attach it as a CI artifact. Do not replace failed checks with prose.

```json
{
  "schema": "fl-urj.6.2",
  "run_id": "2026-09-07T00:00:00Z-host-01",
  "baseline_commit": "206325f",
  "tested_commit": "<40-char-commit>",
  "result": "pass|fail|blocked",
  "environment": {
    "os": "<name/version>",
    "python": "<version>",
    "browser": "Chrome|Chromium",
    "browser_version": "<version>",
    "assistive_technology": "<name/version or none>",
    "docker": "<version or unavailable>",
    "host": "<redacted host label>"
  },
  "commands": [
    {"command": "make check", "exit_code": 0, "summary": "<short output>"},
    {"command": "make test-e2e", "exit_code": 0, "summary": "<short output>"},
    {"command": "make browser-test", "exit_code": 0, "summary": "<short output>"},
    {"command": "make docker-build", "exit_code": 0, "summary": "<short output>"},
    {"command": "make docker-test", "exit_code": 0, "summary": "<short output>"}
  ],
  "flows": {
    "create_upload": {"status": "pass|fail", "evidence": "<id>"},
    "open_read_full": {"status": "pass|fail", "evidence": "<id>"},
    "search": {"status": "pass|fail", "evidence": "<id>"},
    "literal_grep": {"status": "pass|fail", "evidence": "<id>"},
    "read_chunk": {"status": "pass|fail", "evidence": "<id>"},
    "write_new_version": {"status": "pass|fail", "evidence": "<id>"},
    "version_history": {"status": "pass|fail", "evidence": "<id>"},
    "graph_link_navigation": {"status": "pass|fail", "evidence": "<id>"},
    "preview_html_css_js": {"status": "pass|fail", "evidence": "<id>"},
    "attached_mcp_allow_deny": {"status": "pass|fail", "evidence": "<id>"},
    "direct_renderer_top_level_safety": {"status": "pass|fail", "evidence": "<id>"},
    "binary_metadata_only": {"status": "pass|fail", "evidence": "<id>"},
    "missing_unavailable_conflict_recovery": {"status": "pass|fail", "evidence": "<id>"}
  },
  "accessibility": {
    "heading_landmarks": "pass|fail",
    "accessible_names": "pass|fail",
    "keyboard_only": "pass|fail",
    "keyboard_recovery_after_error": "pass|fail",
    "focus_visible_and_ordered": "pass|fail",
    "named_landmarks": "pass|fail",
    "no_positive_tabindex_or_trap": "pass|fail",
    "busy_status_alerts": "pass|fail",
    "screen_reader_manual": "pass|fail|not_run"
  },
  "responsive": {
    "viewports_css_px": [320, 375, 768, 1280],
    "horizontal_overflow": "pass|fail",
    "control_reflow": "pass|fail",
    "preview_reachable": "pass|fail"
  },
  "security_disclosures": {
    "local_unauthenticated_banner": "pass|fail",
    "hosted_auth_limit_visible": "pass|fail|not_applicable",
    "preview_untrusted_and_isolated": "pass|fail",
    "direct_renderer_top_navigation_blocked": "pass|fail",
    "bridge_denial_means_not_run": "pass|fail",
    "no_stack_paths_credentials_or_content_leak": "pass|fail"
  },
  "defects": [
    {
      "id": "UX-00",
      "severity": "P0|P1|P2",
      "status": "open|fixed|accepted",
      "repro": "<test or manual step>",
      "evidence": "<screenshot/log/accessibility-tree id>"
    }
  ],
  "artifacts": [
    {"kind": "junit|screenshot|ax-tree|video|docker-log|manual-notes", "path": "<path>"}
  ],
  "review": {
    "ux_reviewer": "<name> or role",
    "qa_reviewer": "<name> or role",
    "mayor_decision": "<approval/blocker note>"
  }
}
```

Required evidence IDs must resolve to retained artifacts. Redact credentials,
private content, tenant identifiers, local paths, and personal data; retain
the fact and location of any redaction.

## Defect thresholds

`result: pass` requires all of the following:

- 100% of listed core flows pass, including at least one failure/recovery path.
- Direct renderer navigation attempts leave the control/harness page intact,
  generate no top-level request, and allow the fixture script to report its
  blocked result.
- 0 open P0 defects and 0 open P1 defects.
- 0 open P2 defects affecting core navigation, keyboard operation, labels,
  contrast, responsive access, privacy/security disclosure, or recovery.
- 0 skipped browser assertions. A missing browser, Docker, or screen-reader
  run is `blocked`, not `pass`.
- 100% of submit controls remain disabled for the full async transaction;
  every failure restores controls and preserves query or unsaved edit state.
- 100% of controls in the core loop have meaningful accessible names; no
  positive `tabindex`, keyboard trap, focus loss on alert, or color-only state;
  page exposes named `banner`, `main`, and content-region landmarks.
- Keyboard recovery uses real key events after a conflict/missing-artifact
  alert, returns focus to a usable control, and preserves unsaved/query state.
- No horizontal page overflow at 320px, 375px, 768px, or 1280px CSS width.
- Local unauthenticated, untrusted-preview, attached-capability, and hosted
  auth limitations remain visible and accurate.
- No stack trace, filesystem path, credential, bearer token, tenant ID, or
  artifact content appears in an error or security-status disclosure.

Any of these is a hard block regardless of test count:

- user can mistake local mode for authenticated/private mode;
- a denied attached-MCP request appears to have executed;
- stale edit is discarded or immutable history changes unexpectedly;
- a core task requires pointer-only interaction or an internal ID without an
  approved UX exception;
- preview escape, direct egress, or host-origin access changes control-page
  state;
- assistive-technology manual pass is absent for a hosted readiness claim.

## Host execution commands

Run from a clean checkout at the tested commit:

```bash
make check
make test-e2e
FOLIO_BROWSER=/path/to/Chrome make browser-test
make docker-build
make docker-up
make docker-test
make docker-down
```

Record exact exit codes and retained output in the schema above. Mayor owns
the final Chrome/Docker run and any Git commit or merge decision.
