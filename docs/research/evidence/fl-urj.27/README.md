# fl-urj.27 / G7 exact-head UX evidence

Status: automated browser and static UX checks pass; G7 remains blocked.

Tested source is exact canonical `176a2cebd2573287203f5931d4aad9d792be7905`.
The retained run record is [`20260917T0716Z-176a2ce-browser.json`](20260917T0716Z-176a2ce-browser.json).

## Exact-head reruns

- `tests/test_browser_e2e.py`: **5 passed, 0 failed, 0 skipped** in 39.09s
  (39.55s wall).
- Fresh repeat of the same command: **5 passed, 0 failed, 0 skipped** in
  37.31s (37.77s wall).
- `tests/test_web.py`: **16 passed, 9 subtests passed, 0 failed, 0 skipped**.
- Focused static UX/admin subset: **6 passed, 10 deselected, 0 failed, 0
  skipped**.

The browser suite covers auth recovery, library/graph/filesystem navigation,
Markdown and HTML/CSS/JS rendering, search/grep/chunk/version flows,
share/revoke and ACL denial, bridge allow/deny, security disclosures,
keyboard/pointer activation, focus and busy/error status behavior, and the
320px responsive overflow check. The static suite covers labeled controls,
landmarks, status/alert roles, nontechnical copy, sandbox hooks, and the
approved-connection admin contract/API states.

## Open G7 blockers

This is automated evidence, not an independent accessibility review. No
screen-reader or other assistive-technology run was performed, no independent
reviewer matrix/sign-off is attached, and the browser suite does not exercise
approved-connection settings/admin states with keyboard and pointer. Those
items remain open in `fl-urj.27`; the evidence is intentionally marked
`result: blocked` in the JSON record.

## What remains for fl-urj.28

The repeated fresh-state release package still needs a frozen source/image/
configuration manifest and two independently retained runs for every required
package, including backup/restore/migration/DR, hosted auth/tenant, approved
connections, TLS/readiness/rollback, audit/observability, and the final
browser/accessibility package. Each run needs exact commands, redacted output,
structured result, hash, reviewer, and timestamps; skips, failures, and the
manual-AT blocker must remain visible. `fl-urj.28` stays open until the pair
has no unreviewed blockers.
