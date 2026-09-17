# fl-urj.27 UX usability retest — exact 60c7099

Date: 2026-09-17 local
Target: exact origin/main / 60c7099e5507d9e62e23270a91a36376ba7444d3
Branch: fl-urj-27-ux_usability-60c7099
Owner: UX/usability crew
Result: **IN PROGRESS; gate OPEN**

No product UX code was changed. The dirty shared usability checkout and its
prior 75ff776 evidence were preserved. This report is redacted: it contains no
tenant data, credentials, cookies, member IDs, or artifact contents.

## Corrected matrix disposition

The current UX/usability matrix has exactly **6 FAILED, 2 PASSED, and 16
SKIPPED** cases. Skipped means not executed or not executable in this
environment; it is not a pass. The two passes are static/contract evidence
only. No UX gate or fl-urj.27 completion is declared.

| Disposition | Count | Meaning |
|---|---:|---|
| Failed | 6 | Deterministic current-source UX failures below |
| Passed | 2 | Static/contract checks only; live behavior remains unverified |
| Skipped | 16 | Host, assistive-technology, dependency, or missing-surface boundary |

## Environment and method

- OS: macOS 15.7.3, build 24G419, arm64.
- Browser: Google Chrome 153.0.8010.47 present; live app browser fixture
  blocked before product assertions by loopback bind permission.
- Python: 3.12.10 with cached dependencies for focused checks.
- Docker: client 20.10.17 / Compose v2.10.2; daemon socket denied.
- Assistive technology: no screen-reader/browser accessibility surface was
  available. Manual screen-reader output was skipped.
- Requested responsive widths: 320, 375, 768, and 1280 CSS px. No rendered
  width/overflow result is claimed because the live browser run was blocked.
- Input model for the six source-confirmed failures: keyboard-only and
  screen-reader semantic inspection at each requested width; deterministic
  repros below are source/DOM checks, not a claim of live key-event output.

## Evidence commands and dispositions

Run from the exact-head clone with the repository's cached Python path:

    python -m compileall -q src tests                         PASS
    pytest -q tests/test_web.py tests/test_acl.py tests/test_service.py \
      tests/test_auth.py tests/test_sessions.py tests/test_external_mcp.py \
      tests/test_identity.py tests/test_public_mcp_bounds.py tests/test_hosted_scope.py
                                                                82 passed, 4 skipped
    pytest -q                                                   99 passed, 11 failed, 20 skipped
    FOLIO_BROWSER=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
      pytest -q tests/test_browser_e2e.py                       5 failed before assertions
    make check                                                   BLOCKED: offline uv dependency resolution
    docker compose build                                         BLOCKED: daemon socket denied
    ruff check src tests                                         PASS
    ruff format --check src tests                                PASS (39 formatted)
    python -m compileall -q src tests                            PASS

The repository-test counts above are separate from the 24-case UX matrix and
must not be substituted for its 6/2/16 disposition. The five browser failures
and the HTTP/renderer failures occurred in free_port() before product
assertions (PermissionError: [Errno 1] Operation not permitted binding
127.0.0.1). The direct-suite stdio failure additionally lacked uvicorn in the
reconstructed cache; four session cases were skipped for the same loopback
restriction. make check could not resolve uncached mcp-types==2.1.1 with
network disabled. Docker could not reach unix:///var/run/docker.sock.

## Passed — exactly 2

### PASS-01 — Human route landmarks and isolated preview semantics

Static generated DOM checks pass for the root, workspace, and human artifact
shells: a primary navigation landmark, main, heading, workspace tree, and
workspace role=tablist are present. Markdown reader and HTML preview are
distinct surfaces; HTML uses iframe sandbox=allow-scripts without
allow-same-origin.

Evidence: src/folio_lattice/inspection.py:2802-2809,2816-2824,2978-2986,3070-3080.

Disposition is static/contract PASS only. It does not cover live click,
keyboard, browser accessibility-tree, iframe, or mobile rendering.

### PASS-02 — Recoverable error semantics

Static source checks find dedicated 401 sign-in recovery, 403/404 return
actions, 409 edit-preservation handling, generic error handling, and
role=status / role=alert live-region hooks with a focusable error target.

Evidence: src/folio_lattice/inspection.py error handlers and generated live
regions around 2793-2795,2820-2821,3077-3079.

Disposition is source/contract PASS only. Live failure injection and recovery
were skipped.

## Failed — exactly 6

Every failure below records route/task, viewport and input method, semantic
control, expected behavior, actual behavior, severity, deterministic
reproduction, and remediation routing.

### UX-27-R1 — P1 — Skip link does not move focus to main

- Route/task: / and /artifacts/<id>; skip repeated navigation.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only, then screen-reader
  landmark inspection.
- Control: .skip-link[href="#main"] -> main#main.
- Expected: Enter moves focus to the primary content landmark.
- Actual: main#main has no focus target (tabindex=-1 or equivalent), so focus
  does not reliably land on main.
- Severity: P1.
- Deterministic repro: render either route, Tab to Skip to content, press
  Enter, inspect document.activeElement; inspect main#main attributes.
- Remediation: fl-urj.7.7.

Evidence: src/folio_lattice/inspection.py:2816,2819-2820,3070,3077-3078.

### UX-27-R2 — P1 — Duplicate filenames remain ambiguous in navigation

- Route/task: Library -> graph -> left file tree -> graph target; open one of
  two same-named artifacts.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only and screen-reader
  accessible-name inspection.
- Controls: #artifact-tree .tree-file, .library-item > button,
  .graph-card-action, and graph relationship buttons.
- Expected: same-name artifacts have readable path/metadata and unique visible
  and accessible names throughout the task flow.
- Actual: tree, library, and graph controls expose basename/name only. Search
  adds an opaque artifact_id only for some duplicate result sets; it does not
  resolve the Library/tree/graph path.
- Severity: P1.
- Deterministic repro: create two artifacts with identical names, open the
  graph workspace, Tab through library/tree/graph controls, and compare
  visible labels and accessible names.
- Remediation: fl-urj.7.7; coordinate any duplicate-ID contract dependency
  with fl-urj.6.2/.7.6.

Evidence: src/folio_lattice/inspection.py:1915-1919,2331-2337,2354-2368,2371-2374,2492-2502.

### UX-27-R3 — P1 — Standalone preview has no return navigation

- Route/task: web artifact Open site -> /standalone/<id>.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only.
- Controls: a.library-action[target="_blank"], #standalone-link, and the
  standalone document shell.
- Expected: standalone reader/preview retains a reliable Library/back/close
  route to continue the human-first task.
- Actual: standalone renders only viewer/main; it has no Library/back control.
  The launch opens a new tab, so browser Back is not a dependable in-product
  return path.
- Severity: P1.
- Deterministic repro: activate Open site, inspect the new tab for a
  Library/back/close control, then attempt to return through the task flow.
- Remediation: fl-urj.7.7.

Evidence: src/folio_lattice/inspection.py:2798-2810,2360-2364,2984.

### UX-27-R4 — P2 — Edit and revoke cancellation lose keyboard focus

- Route/task: workspace Edit -> Cancel/Back to reading; Share -> Remove
  access -> Keep access.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only and screen-reader
  focus inspection.
- Controls: #edit-entry -> #close-edit/#human-cancel; dialog#revoke-access ->
  #revoke-cancel.
- Expected: cancel closes the surface and restores focus to the invoking
  control.
- Actual: edit focuses #human-content on open and hides the editor without
  refocusing #edit-entry; revoke stores no invoking trigger and closes with no
  focus restoration.
- Severity: P2.
- Deterministic repro: activate Edit, cancel, inspect document.activeElement;
  then activate Share -> Remove access -> Keep access and inspect focus again.
- Remediation: fl-urj.7.7.

Evidence: src/folio_lattice/inspection.py:1861-1871,1883-1884,2234-2252.

### UX-27-R5 — P2 — Share recipient requires an opaque identifier

- Route/task: workspace Share; identify a recipient before granting access.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only and screen-reader
  label inspection.
- Control: label[for="share-recipient"] / #share-recipient with
  placeholder=member-id.
- Expected: a nontechnical user can select or identify a recipient by display
  name/member context, with clear organization scope.
- Actual: only an opaque Approved person identifier text field is provided;
  there is no member lookup, display-name/group affordance, or way to discover
  the identifier.
- Severity: P2.
- Deterministic repro: open Share, Tab to the recipient field, and attempt to
  select a recipient without a pre-known raw ID.
- Remediation: fl-urj.7.7.

Evidence: src/folio_lattice/inspection.py:2948-2952.

### UX-27-R6 — P1 — Admin, approved connections, and audit surfaces are absent

- Route/task: tenant/library primary navigation; find admin, approved
  connections, revoke/policy, and audit export.
- Viewport/input: 320/375/768/1280 CSS px; keyboard-only and screen-reader
  navigation inspection.
- Controls/routes: nav[aria-label="Primary"], /admin, /connections,
  /audit/export.
- Expected: an authorized admin can discover tenant/member controls,
  connection management, and audit export from the human-first UI.
- Actual: primary nav exposes Library, Graphs, Search, and Shared with me only;
  the human route has no admin/connection/audit entry or route. Approved
  connection is debug/Inspector-only.
- Severity: P1.
- Deterministic repro: start at /, inspect all primary links and visible
  sections, try /admin, /connections, and /audit/export, and search the human
  UI for those controls.
- Remediation: fl-urj.7.6 for admin/connection/audit product surfaces;
  fl-urj.7.7 for human-first navigation entry points.

Evidence: src/folio_lattice/inspection.py:2878-2888,3036-3050,3199-3237.

## Skipped — exactly 16

These cases were not run and are not passes.

| ID | Route/task | Reason |
|---|---|---|
| SKIP-01 | Sign-in/OIDC start, callback, logout | Live browser fixture blocked before assertions; no hosted identity provider in environment |
| SKIP-02 | Tenant identity and library scoping | Live authenticated session unavailable |
| SKIP-03 | Library -> graph picker click flow | Loopback browser fixture blocked |
| SKIP-04 | Left-tree keyboard traversal and focus order | Live key events unavailable |
| SKIP-05 | Markdown reader rendering | Live browser fixture blocked |
| SKIP-06 | HTML content preview/iframe loading | Live browser fixture blocked; Docker/host path unavailable |
| SKIP-07 | Create artifact | Live browser fixture blocked |
| SKIP-08 | Upload artifact | Live browser fixture blocked |
| SKIP-09 | Edit/save immutable version write | Live browser fixture blocked |
| SKIP-10 | Search and grep result interaction | Live browser fixture blocked |
| SKIP-11 | Version history end-to-end | Live browser fixture blocked; human route has no live history control |
| SKIP-12 | Share/revoke HTTP flow | Live authenticated browser fixture blocked |
| SKIP-13 | Approved connection HTTP flow | No human connection surface; live service host unavailable |
| SKIP-14 | Audit export | No human audit-export surface; live service host unavailable |
| SKIP-15 | Responsive overflow/reflow at 320/375/768/1280 | No rendered browser metrics |
| SKIP-16 | Manual screen-reader output | No assistive-technology surface available |

## Handoff and gate

The exact six-failure list was prepared and delivery was attempted with gt
nudge targeting folio_lattice/crew/ux. Immediate mode reported no tmux server
running; queue mode reported target session fl-crew-ux not found. No bead
comment was written because the shared Dolt server was also unreachable
(127.0.0.1:3311, operation not permitted). This report is the durable redacted
handoff.

The evidence branch push was attempted and blocked because the environment
could not resolve github.com. The local evidence commit remains
e030097; no product files are included.

Actionable routing is explicit in each failure: interaction/discoverability
remediation to fl-urj.7.7, tenant/admin/connection/audit surfaces to
fl-urj.7.6, and duplicate-name contract coordination to fl-urj.6.2/.7.6 as
applicable. This lane does not self-certify those dependencies.

Keep fl-urj.27 in_progress. After a real canonical remediation commit lands,
rerun the same 24 cases against that exact SHA and preserve this before-result
beside the after-result. Closure requires all P1/P2 failures fixed and
independently retested; skipped/blocked cases must be rerun or explicitly
carried as blockers.
