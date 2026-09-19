# fl-urj.7.7 adversarial follow-up

Run timestamp: `2026-09-09T03:49:06Z`
Target: visible `origin/main` at
`861eda6d1e3536c6791dbe1d6e1022ad1c0f5106`
Worker `HEAD`: `206325f9304b106e6e00e49e92923102068fdcb9`; dirty work preserved.

No newer `origin/main` push was visible when this follow-up ran. No product
code or the 861 admin console was counted as progress.

## Assertions added or rechecked

- Production UI route surface: `/debug`, `/__debug__`, `/admin/debug`, and
  `/_debug` return 404 with `Cache-Control: no-store`; responses do not expose
  traceback or content payload markers. The production page contains no debug
  aliases. `tests/test_web.py::WebAppTests::test_production_ui_route_surface_has_no_debug_aliases`.
- Admin/share recovery: the browser assertion now requires a 403 share failure
  to focus the alert, retain `denied-reader`, re-enable submit controls, expose
  `Return home`, and make that recovery action reachable by Tab and Enter.
- Keyboard/focus: Skip to content, named AX regions and controls, no positive
  tabindex, open-to-summary tab order, and error recovery remain asserted.
- Responsive: browser assertions cover 320, 375, 768, and 1280 CSS pixels
  without horizontal overflow; the 320px error state also bounds the preview.
- Safe rendering: iframe-only render requests, `sandbox="allow-scripts"`,
  no `allow-same-origin`, hostile artifact isolation, and no attached bridge
  request after denial remain asserted.

## Executable worker evidence

Against a clean archive of `861eda6`, using the cached Python 3.12 locked
dependencies because `uv` is not installed:

```text
PYTHONPATH="/private/tmp/folio-lattice-gates.1xbwGV/src:/Users/brandonsovran/.cache/uv/archive-v0/di_5_hSyO6BgRgdP:/Users/brandonsovran/.cache/uv/archive-v0/WDyuIkQpnpJ9-1c9:/Users/brandonsovran/.cache/uv/archive-v0/TOmDBZoEKR0hdK5Y/lib/python3.12/site-packages:/Users/brandonsovran/.cache/uv/archive-v0/C6eMwxVFWwa4ztck:/Users/brandonsovran/.cache/uv/archive-v0/oUF6_H19H1-qjwan:/Users/brandonsovran/.cache/uv/archive-v0/71j5OPu8ja2ekM8_" python3 -m pytest -q tests/test_web.py
13 passed in 1.50s (exit 0)

python3 -m py_compile tests/test_web.py tests/test_browser_e2e.py && git diff --check
pass (exit 0)

ruff check tests/test_web.py tests/test_browser_e2e.py && ruff format --check tests/test_web.py tests/test_browser_e2e.py
All checks passed; 2 files already formatted (exit 0)

FOLIO_BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" PYTHONPATH="/private/tmp/folio-lattice-gates.1xbwGV/src:/Users/brandonsovran/.cache/uv/archive-v0/di_5_hSyO6BgRgdP:/Users/brandonsovran/.cache/uv/archive-v0/WDyuIkQpnpJ9-1c9:/Users/brandonsovran/.cache/uv/archive-v0/TOmDBZoEKR0hdK5Y/lib/python3.12/site-packages:/Users/brandonsovran/.cache/uv/archive-v0/C6eMwxVFWwa4ztck:/Users/brandonsovran/.cache/uv/archive-v0/oUF6_H19H1-qjwan:/Users/brandonsovran/.cache/uv/archive-v0/71j5OPu8ja2ekM8_" python3 -m pytest -q tests/test_browser_e2e.py -k 'test_auth_states_are_accessible_safe_and_preserve_input or test_real_ui_upload_search_grep_chunk_graph_versions_conflict_and_bridge'
2 failed, 1 deselected in 1.02s (exit 1): both failed at free_port() with PermissionError: [Errno 1] Operation not permitted; no browser assertions executed.
```

The existing ACL unit baseline remains 4 passed. Manual keyboard-only and
assistive-technology runs remain `not_run`: this worker has no CUA browser/AT
surface. The gate stays open pending Mayor/rig host Chrome, Docker, and real
manual AT evidence.

## Host execution requested

From a host checkout at the tested commit, run the commands in
`docs/research/evidence/MAYOR-HANDOFF-2026-09-09.md`, retain exact exit codes,
AX tree/screenshots and Docker logs, then append the manual keyboard and
screen-reader result. No enterprise-readiness claim is made.
