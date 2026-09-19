# fl-urj.7.7 UX regression evidence

Baseline: `861eda6` (`origin/main`)
Worker checkout inspected: `206325f`
Status: host browser and Docker execution pending

## Rebased scope

The 861 baseline already lands the viewer-first workspace and human-readable
search result slice: a visible `Workspace` heading, reader content before the
editor, named artifact sections, artifact names in search/graph results,
private-by-default access controls, share/revoke actions, focus-visible CSS,
and responsive reflow hooks.

Only these unlanded regressions remain in this worker change:

- `tests/test_web.py::WebAppTests::test_human_search_accepts_punctuation_without_fts_error`
  requires a human query such as `budget:` to return a named result instead of
  surfacing an SQLite FTS error.
- `tests/test_browser_e2e.py::BrowserHostedAuthE2ETests::test_auth_states_are_accessible_safe_and_preserve_input`
  exercises Skip to content with a real Enter key and requires focus to land on
  `main`, plus named landmark roles.
- `tests/test_browser_e2e.py::BrowserSandboxE2ETests::test_real_ui_upload_search_grep_chunk_graph_versions_conflict_and_bridge`
  repeats punctuation-safe search in the browser flow and requires no alert
  plus a human-readable result.

The prior target assertions for share dialogs, safe-link/undo copy, notification
failure, and admin connection states are intentionally excluded: those controls
are not part of the 861 UI contract. Do not record them as passed or blocked for
this slice; open a separate target-state change when that UI exists.

No browser or Docker pass is claimed by this worker. CUA reported no browser.

## Host evidence schema

Retain one JSON object per host run. `blocked` is required when a browser,
Docker, or assistive technology is unavailable; it is never a passing result.

```json
{
  "schema": "fl-urj.7.7",
  "baseline_commit": "861eda6",
  "tested_commit": "<40-char-sha>",
  "run_id": "<UTC-run-id>",
  "result": "pass|fail|blocked",
  "environment": {
    "os": "<name/version>",
    "python": "<version>",
    "browser": "<name/version or unavailable>",
    "assistive_technology": "<name/version or unavailable>",
    "viewport_css_px": [320, 375, 768, 1280],
    "docker": "<version or unavailable>"
  },
  "commands": [
    {"command": "make check", "exit_code": 0, "evidence": "<id>"},
    {"command": "make test-e2e", "exit_code": 0, "evidence": "<id>"},
    {"command": "FOLIO_BROWSER=<absolute-path> make browser-test", "exit_code": 0, "evidence": "<id>"},
    {"command": "make docker-build", "exit_code": 0, "evidence": "<id>"},
    {"command": "make docker-test", "exit_code": 0, "evidence": "<id>"}
  ],
  "regressions": {
    "viewer_reader_precedes_editor": "pass|fail",
    "search_result_has_artifact_name": "pass|fail",
    "punctuation_search_returns_result": "pass|fail",
    "skip_link_moves_focus_to_main": "pass|fail",
    "named_banner_main_regions": "pass|fail",
    "keyboard_error_recovery_preserves_input": "pass|fail",
    "no_positive_tabindex_or_trap": "pass|fail",
    "responsive_no_horizontal_overflow": "pass|fail",
    "private_share_revoke_baseline": "pass|fail",
    "direct_renderer_top_level_safety": "pass|fail",
    "attached_bridge_allow_deny": "pass|fail"
  },
  "manual": {
    "keyboard_only_core_flow": "pass|fail|not_run",
    "screen_reader_core_flow": "pass|fail|not_run",
    "nontechnical_search_task": "success|help|failed"
  },
  "defects": [
    {
      "id": "UX-00",
      "severity": "P0|P1|P2",
      "status": "open|fixed|accepted",
      "repro": "<test or manual step>",
      "evidence": "<id>"
    }
  ],
  "artifacts": [
    {"kind": "junit|screenshot|ax-tree|trace|manual-notes|docker-log", "path": "<redacted-path>"}
  ],
  "review": {
    "ux": "<reviewer>",
    "qa": "<reviewer>",
    "mayor_decision": "<note>"
  }
}
```

Redact cookies, tokens, credentials, private artifact text, tenant IDs, email
addresses, and local paths. Evidence IDs and pass/fail outcomes must remain
resolvable.

## Defect thresholds

`result: pass` requires:

- zero open P0/P1 defects and zero P2 defects affecting navigation, keyboard
  operation, labels, focus, status announcements, responsive access, privacy,
  or recovery;
- all listed browser assertions execute (no skipped browser assertions);
- the reader precedes the editor and search/graph results expose human names;
- punctuation input produces a result or a plain-language recoverable empty
  state, never a raw FTS/SQLite error;
- Skip to content moves focus to a programmatically focusable `main`, all core
  controls have meaningful accessible names, and no positive `tabindex` exists;
- keyboard-only create, open, search, read, version, graph, preview, share,
  revoke, and error-recovery paths retain input and return focus usefully;
- no horizontal overflow at 320, 375, 768, or 1280 CSS pixels;
- private/share/revoke behavior and direct-renderer/attached-MCP safety remain
  green on the 861 baseline tests;
- manual keyboard and screen-reader runs are recorded for any readiness claim.

Hard block: missing browser/Docker/assistive-technology evidence, a pointer-only
core action, lost focus or input after an error, misleading privacy or revoke
state, opaque-only search results, raw technical search errors, renderer escape,
or an attached request that appears to run after denial.

## Exact host commands

Run from the repository root at the tested commit. The browser path must be an
absolute executable path; the macOS example is the standard Chrome install.

```bash
export FOLIO_BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

make check
make test-e2e
FOLIO_BROWSER="$FOLIO_BROWSER" uv run pytest -q tests/test_browser_e2e.py -k 'test_auth_states_are_accessible_safe_and_preserve_input or test_real_ui_upload_search_grep_chunk_graph_versions_conflict_and_bridge'
FOLIO_BROWSER="$FOLIO_BROWSER" make browser-test

make docker-build
make docker-up
FOLIO_BASE_URL=http://127.0.0.1:8000 FOLIO_RENDER_URL=http://127.0.0.1:8001 make docker-test
make docker-down
```

Record each command’s exact exit code and evidence ID. Mayor owns host
execution, Git application, and the final merge decision.
