# Thin inspection UI and renderer research

Date: 2026-09-05

## Question

What is the smallest useful browser slice that exercises existing artifact,
version, and graph state without creating a second product model or weakening
the sandbox boundary?

## Findings

- HTML's iframe sandbox gives embedded content an opaque origin unless
  `allow-same-origin` is granted. `allow-scripts` can enable artifact JavaScript
  without enabling forms, popups, downloads, top navigation, or same-origin
  host access.
- The HTML Standard warns against combining `allow-scripts` and
  `allow-same-origin` for same-origin content because that combination can let
  content remove its own iframe sandbox.
- CSP `sandbox` applies the same restrictions from the renderer response. This
  makes isolation a renderer contract instead of relying only on correct host
  markup.
- CSP `connect-src` covers script-driven connections. Setting it to `'none'`
  blocks fetch, XHR, WebSocket, and similar direct egress paths.
- CSP `frame-ancestors` can restrict embedding to the configured inspection
  origin. It must be delivered as an HTTP response header, not a meta element.

Primary sources:

- [HTML Standard: iframe sandbox](https://html.spec.whatwg.org/multipage/iframe-embed-object.html#attr-iframe-sandbox)
- [Content Security Policy Level 3: `connect-src`](https://www.w3.org/TR/CSP3/#directive-connect-src)
- [Content Security Policy Level 3: `sandbox`](https://www.w3.org/TR/CSP3/#directive-sandbox)
- [Content Security Policy Level 3: `frame-ancestors`](https://www.w3.org/TR/CSP3/#directive-frame-ancestors)
- [Chrome Headless mode](https://developer.chrome.com/docs/automation-and-testing/headless)

## Minimum vertical slice

1. Keep `FolioLattice` as the only state model.
2. Add a static inspection page that opens one artifact identifier, reads its
   current content, immutable versions, and outgoing graph, and writes a new
   text version using the current version as the optimistic parent.
3. Add only two inspection JSON operations: combined read and version write.
   They are UI implementation details, not a second public artifact API.
4. Run a second renderer process on a separately configured origin. It opens the
   same persistent state read-only and serves only HTML, JavaScript, and CSS
   previews.
5. Apply both iframe and response-level sandboxing. Do not add a message bridge,
   network proxy, credential path, package resolver, or resource graph bundler.

## Acceptance plan

- Process-level HTTP test: create and link artifacts, inspect combined state,
  edit through the UI route, verify immutable parent/version history, and fetch
  all three renderable media types from the renderer origin.
- Header test: require response CSP sandboxing, no connections, exact frame
  ancestor, no referrer, no sniffing, and no CORS grant.
- Real-browser hostile test: run a stored HTML fixture inside the same sandbox
  contract and prove scripts run while fetch, XHR, WebSocket, host DOM access,
  local storage, popups, forms, and top navigation fail.
- Docker test: exercise the renderer beside the existing MCP persistence loop.

## Deferred

Artifact listing, creation UI, graph editing, binary editing, diffing, sharing,
auth, credentials, attached-MCP bridging, vector search, bundled dependency
resolution, and a frontend framework remain outside this slice.
