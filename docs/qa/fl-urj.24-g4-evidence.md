# fl-urj.24 G4 dedicated-origin evidence

Date: 2026-09-17

This records the exact source, browser, image, and runtime evidence for the
G4 dedicated-origin sandbox fix. It is evidence for review; it does not claim
enterprise-readiness or promote the milestone.

## Source identity

| item | value |
| --- | --- |
| canonical base commit | `24deddcc616129c349042298766662aed9240b79` |
| canonical base tree | `939dd9acbd71190b7f081994024a4dcbffded7fd` |
| prior G4 commit | `b80c934ecdcc05f9bcc1cd1231fa1d7b7300ca32` |
| prior G4 tree | `cb69b12d90c74ad533e2f6a5eb859237f291b1af` |
| source fix commit | `845019fe01a57f191b402936e98968d3cf94f4d3` |
| source fix tree | `c5299f181c85532c2df14784066ab234f87c8014` |
| final candidate commit | `367cb28bed22de83088fc647bd5602a9bef37eb0` |
| final candidate tree | `3461fc6da6f566978efa91d4a9242197293cb973` |
| candidate branch | `codex/fl-urj.24-g4-candidate` |
| review worktree | `/tmp/folio-g4-final-review.7LIqeK` |

The source fix is `test(renderer): use CDP hostile matrix`. It changes only the
asynchronous browser harness: a real Chrome DevTools session waits for the
fixture result, and download probing uses a new target so it cannot replace the
artifact frame being tested. The final candidate `367cb28` adds only this
evidence document and its SHA-256 manifest on top of that source fix.

SHA-256 hashes at the final candidate tree (unchanged from the source fix):

```text
e9d4b050f37f42c9b49b8e8bdfa021e659183f4ff92c8f2da10ee2f9abaec6f4  src/folio_lattice/sandbox.py
12d92de37779bf25eb3c332590f2dbb0411ac6806687633e174a3db10adb9f56  tests/test_sandbox.py
0cd00f2cb23d34c7980c52db59a4fa9dc9197ce79abfdf30895e64c019eb4d10  tests/test_browser_e2e.py
5886ab68f9c78b4e0e73f292c73c19fd8303771fcb70c8327184445c4b8cde78  tests/test_e2e.py
ca963ab7484502f6df555ba86759f31503a7dd2c79687a87f8593916f05082e8  docker-compose.yml
4f366e650f081bcf51a3ec6700117acf5112b2f430d1eb31d2aa0a1dc00f99b5  Dockerfile
```

## Browser acceptance

Executable and version:

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
Google Chrome 153.0.8010.47
```

Independent clean-worktree command:

```text
FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' PYTHONPATH=src pytest -q -s tests/test_browser_e2e.py
......                                                                   [100%]
6 passed in 30.59s
```

The hostile fixture's real-browser result was successful for every expected
denial: parent DOM, cookies, local/session storage, IndexedDB, popup, download,
form, nested frame, worker, fetch/XHR/WebSocket/EventSource/beacon/image
egress, and top navigation. CSP violations included `connect-src`, `frame-src`,
and `img-src`; the static policy test also asserts `form-action`, `worker-src`,
`child-src`, `manifest-src`, and `navigate-to`. The same browser session passed
the linked HTML/CSS/JS allow path.

Focused repair command:

```text
FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' PYTHONPATH=src pytest -q -s tests/test_browser_e2e.py::BrowserSandboxE2ETests::test_scripts_render_but_hostile_browser_matrix_fails_closed
1 passed in 8.15s
```

The regression bisect removed each added CSP/Permissions-Policy directive and
then the added fixture operations. No header removal restored the child frame.
Removing only the download probe did: the original cross-origin download link
navigated/replaced the artifact frame before the asynchronous result was
posted (`Page.frameDetached`, reason `swap`). The repair uses `target="_blank"`
under the existing no-popups policy and uses an explicit CDP wait, not a timing
delay or retry workaround. `dump-dom` remains unsuitable for this asynchronous
fixture because it snapshots before the result is posted.

## Repository checks

Mandated wrapper, with `uv` made available only through `PATH`:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' make check
```

Result:

```text
ruff check: All checks passed!
ruff format --check: 59 files already formatted
mypy: Success: no issues found in 22 source files
pytest: 233 passed, 8 skipped in 99.80s
coverage: 80.61% (required 80%)
process status: 0
```

The same clean worktree previously passed the non-browser review command:

```text
PYTHONPATH=src pytest -q tests --ignore=tests/test_browser_e2e.py
227 passed, 8 skipped, 99 subtests passed in 47.78s
```

The earlier `make check` attempt without the PATH override failed before any
check because `uv` was absent from PATH; no Makefile change was made.

## Live image and runtime identity

Live image inspection command:

```text
docker image inspect rig-renderer:latest --format 'Id={{.Id}} RepoDigests={{json .RepoDigests}} Created={{.Created}}'
Id=sha256:b3fd406aa8376f5ea162bf881937d1a2adc797cd37e7ec3602cf87f06b985307 RepoDigests=[] Created=2026-09-17T17:29:35.961619763Z
```

Both running services use that exact image ID:

```text
renderer container: ba8ece67fa2fcca4e2894e4c98618aa0e3365771fc42a8ac92f1eaa7685a9a7a
folio container:    b221936c9c8fd9f9ce3c21d51ec67168903bc3ec18286cf079812a2c1f9cf99e
```

Read-only inspection output:

```text
renderer: user=10001:10001 readonly=true capdrop=["ALL"] security=["no-new-privileges:true"] mounts=[]
folio:    user=10001:10001 readonly=true capdrop=["ALL"] security=["no-new-privileges:true"] mounts=[/data RW volume folio-lattice_folio_data]
```

Read-only runtime probes for both containers:

```text
uid=10001(folio) gid=10001(folio) groups=10001(folio)
CapBnd: 0000000000000000
NoNewPrivs: 1
```

The capability/ACL/expiry/replay and URL-log assertions are covered by the
full `tests/test_e2e.py`, `tests/test_web.py`, and repository run above. The
renderer has no database/blob mount or corresponding private-storage
environment, while the folio service retains only its `/data` volume.

## Published-ref QA status

A fresh detached QA worktree was created from the published evidence object
`81cecfc1ed06d17051a9ba2cfe77e8f29e8634d8` (tree
`f5e78c4e25f01a29ab578781ec645d04812270c6`) at
`/tmp/folio-g4-qa-review.IJRztc`. It was clean before testing. The exact
command was:

```text
PATH=/Users/brandonsovran/.local/bin:$PATH FOLIO_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' make check
```

Result: Ruff, format, and mypy passed; `233 passed, 8 skipped in 89.60s`;
coverage `80.61%`; process status `0`.

This is a fresh published-object verification, not mayor/QA acceptance.
Independent QA review and promotion decision remain requested; G4 stays
blocked until that review is acknowledged.
