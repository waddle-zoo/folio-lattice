# Chrome browser harness

`tests/test_browser_e2e.py` uses Chrome/Chromium DevTools as an input and
accessibility driver. Chrome startup is bounded by
`FOLIO_CHROME_STARTUP_TIMEOUT_SECONDS`, which defaults to 30 seconds and is
accepted only from 1 through 300 seconds.

This file documents harness behavior; it is not release evidence. A release
evidence record must include exact source SHA, timestamp, command, browser
version, artifact hash, and PASS/REJECT result.

When Chrome or its DevTools endpoint fails to start, the harness terminates the
process safely and includes the captured stdout/stderr tail in the assertion.
The same bounded diagnostic path is used for local service startup failures;
browser DOM-dump timeouts retain stderr after killing the process. This keeps
startup failures actionable without turning an unbounded browser wait into a
passing or skipped gate.

Example:

```sh
FOLIO_CHROME_STARTUP_TIMEOUT_SECONDS=60 \
FOLIO_BROWSER=/path/to/chrome \
make browser-test
```
