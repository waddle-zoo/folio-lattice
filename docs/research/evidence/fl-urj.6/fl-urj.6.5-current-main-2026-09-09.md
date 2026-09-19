# fl-urj.6.5 current-main assertion handoff

Test target: `origin/main` at `861eda6d1e3536c6791dbe1d6e1022ad1c0f5106`.
No product code or admin-console behavior changed.

Added targeted browser/static assertions in the existing dirty test files:

- App-open affordance and Library return: `tests/test_browser_e2e.py:782-793`;
  static contract markers in `tests/test_web.py:249-252`.
- Named artifact navigation and content-first viewer: existing named search/
  graph checks plus `tests/test_browser_e2e.py:1023-1033` Back recovery.
- Empty state, loading/busy state, keyboard focus/error recovery, and all
  required responsive widths: `tests/test_browser_e2e.py:685-750`.
- Full-screen preview entry/exit preserves iframe `sandbox="allow-scripts"`
  and render source: `tests/test_browser_e2e.py:840-854`.
- Browser Back uses CDP `Page.getNavigationHistory` plus
  `Page.navigateToHistoryEntry`: `tests/test_browser_e2e.py:250-258`.

Worker validation:

- `python3 -m py_compile tests/test_browser_e2e.py tests/test_web.py`: pass.
- Ruff 0.16.6 check and format check for both files: pass.
- Current dirty `tests/test_web.py` against clean 861 source: **12 passed**.
- Browser execution: blocked before server bind by sandbox loopback policy;
  no browser pass claimed.

Mayor must run browser/Docker commands from a host checkout at this exact
commit and append resulting artifacts to the release evidence. `861eda6`
admin-console behavior is explicitly out of scope for this assertion slice.
