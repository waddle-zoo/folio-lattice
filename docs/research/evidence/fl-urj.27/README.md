# fl-urj.27 / G7 exact-head UX evidence

Status: automated browser and static UX checks pass; G7 remains blocked on
independent manual assistive-technology and nontechnical review.

The retained run records are:

- [`20260917T0716Z-176a2ce-browser.json`](20260917T0716Z-176a2ce-browser.json),
  the initial exact-head library, graph, rendering, sharing, bridge, keyboard,
  focus, status, error, and responsive-layout evidence.
- [`20260917T0731Z-dbfc3da-admin-browser.json`](20260917T0731Z-dbfc3da-admin-browser.json),
  the approved-connection admin browser evidence and async form-reset fix.

## Automated exact-head evidence

The initial `176a2ce` browser suite passed twice: **5 passed, 0 failed, 0
skipped** in 39.09s and 37.31s. `tests/test_web.py` passed **16 tests + 9
subtests**; the focused static UX/admin subset passed **6 tests** with 10
deselected.

On canonical base `dbfc3da`, implementation commit `c161d65` added a real
browser scenario for `/settings/connections` and `/api/admin/mcp`. The full
browser suite then passed twice: **6 passed, 0 failed, 0 skipped** in 41.24s
and 40.24s. The focused scenario passed **1 test** with 5 deselected; the web
and static suites remained green. Ruff, formatting, Python compilation, and
`git diff --check` passed.

The admin scenario covers pointer registration, live status and busy-state
restoration, keyboard Enter activation of the revoke dialog, cancel-focus
restoration, pointer confirmation/revocation, bounded loopback rejection,
form recovery, accessible names, and absence of `credential_ref` or bearer
values in rendered UI.

## Fix found by the real browser

Registration exposed a genuine async UI defect: after awaiting the public
admin request, `event.currentTarget` was null, so the form reset raised and
the connection list never refreshed. `c161d65` resets the stable
`#connection-register` element directly. The focused scenario failed before
the fix and passes after it.

## Open G7 blocker

These packages are automated evidence, not an independent accessibility
review. No manual screen-reader/assistive-technology run, reviewer matrix, or
nontechnical participant review is attached. Both structured records are
therefore marked `result: blocked`; keep `fl-urj.27` and G7 open and make no
enterprise-readiness claim.

## What remains for fl-urj.28

The repeated fresh-state release package still needs a frozen source/image/
configuration manifest and two independently retained runs for every required
package, including backup/restore/migration/DR, hosted auth/tenant, approved
connections, TLS/readiness/rollback, audit/observability, and final manual
browser/accessibility review. Each run needs exact commands, redacted output,
structured result, hash, reviewer, and timestamps; skips, failures, and the
manual-AT blocker must remain visible.

