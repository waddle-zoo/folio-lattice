from __future__ import annotations

import json
from html import escape

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

from .auth import get_request_principal
from .bridge import (
    MAX_BRIDGE_BODY_BYTES,
    AttachedMcpBridge,
    BridgeRequestError,
    validate_bridge_request,
)
from .public_mcp import PUBLIC_TOOLS, PublicMcpError, ToolCaller

CONTROL_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

UI_CSS = """
:root {
  color-scheme: light;
  font: 15px/1.5 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #1d2633;
  background: #f5f7f9;
  --ink: #1d2633;
  --muted: #647184;
  --soft: #8c98a8;
  --line: #dce2e9;
  --line-strong: #c9d2dd;
  --surface: #ffffff;
  --canvas: #f5f7f9;
  --blue: #2956d7;
  --blue-dark: #1d43b8;
  --blue-soft: #eef2ff;
  --green: #19714a;
  --green-soft: #eaf7f0;
  --amber: #9a5a16;
  --amber-soft: #fff5e7;
  --shadow: 0 12px 32px rgb(27 42 65 / 6%), 0 2px 6px rgb(27 42 65 / 4%);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { min-width: 320px; margin: 0; background: var(--canvas); color: var(--ink); }
a { color: var(--blue); }
a:hover { color: var(--blue-dark); }
button, input, textarea, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: wait; opacity: .58; }
button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, iframe:focus-visible, a:focus-visible, summary:focus-visible {
  outline: 3px solid #8ca9ff; outline-offset: 3px;
}
button[type="submit"] {
  border: 1px solid var(--blue);
  border-radius: 7px;
  padding: .63rem .9rem;
  background: var(--blue);
  color: white;
  font-weight: 700;
}
button[type="submit"]:hover:not(:disabled) { background: var(--blue-dark); }
input, textarea, select {
  display: block;
  width: 100%;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  padding: .67rem .75rem;
  background: var(--surface);
  color: var(--ink);
  box-shadow: inset 0 1px 1px rgb(27 42 65 / 3%);
}
input::placeholder { color: #8b97a6; }
textarea { min-height: 17rem; resize: vertical; font: .88rem/1.65 ui-monospace, SFMono-Regular, Menlo, monospace; }
label { display: block; color: #3d4a5b; font-size: .83rem; font-weight: 700; letter-spacing: .01em; }
label > input, label > textarea, label > select { margin-top: .35rem; font-weight: 400; }
ul { margin: 0; padding: 0; list-style: none; }
li { margin: 0; }
pre { overflow: auto; max-height: 15rem; margin: .75rem 0 0; border: 1px solid var(--line); border-radius: 8px; padding: .85rem; background: #f7f9fb; color: #334258; white-space: pre-wrap; }
h1, h2, h3, p { margin-top: 0; }
h1, h2, h3 { color: var(--ink); letter-spacing: -.025em; }
h1 { margin-bottom: .65rem; font-size: clamp(2rem, 4vw, 3.35rem); line-height: 1.08; }
h2 { margin-bottom: .35rem; font-size: 1.15rem; line-height: 1.25; }
h3 { margin-bottom: .3rem; font-size: .94rem; }
.skip-link { position: absolute; top: .75rem; left: .75rem; z-index: 5; transform: translateY(-150%); border-radius: 6px; padding: .6rem .8rem; background: var(--ink); color: white; }
.skip-link:focus { transform: translateY(0); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.app-shell { min-height: 100vh; }
.topbar { display: flex; align-items: center; gap: 1.6rem; max-width: 1240px; min-height: 76px; margin: auto; padding: 1rem 1.5rem; }
.brand-lockup { display: flex; align-items: center; gap: .7rem; min-width: max-content; }
.brand-mark { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 10px; background: var(--ink); color: white; font-weight: 800; letter-spacing: -.05em; }
.brand-name { font-weight: 800; letter-spacing: -.025em; }
.brand-subtitle { color: var(--muted); font-size: .72rem; }
.primary-nav { display: flex; gap: 1.15rem; margin-right: auto; }
.primary-nav a { padding: .35rem 0; color: var(--muted); font-size: .86rem; font-weight: 700; text-decoration: none; }
.primary-nav a:hover, .primary-nav a.is-active { color: var(--ink); }
.primary-nav a.is-active { border-bottom: 2px solid var(--blue); }
.nav-index { margin-right: .35rem; color: var(--soft); font: .65rem ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .05em; }
.topbar-open { display: flex; align-items: center; gap: .45rem; width: min(26rem, 35vw); }
.topbar-open label { flex: 1; }
.topbar-open input { height: 38px; padding-block: .45rem; }
.topbar-open button, .button-secondary { border: 1px solid var(--line-strong); border-radius: 7px; padding: .58rem .8rem; background: var(--surface); color: #39485d; font-weight: 700; }
.topbar-open button:hover, .button-secondary:hover { border-color: #a9b7c7; background: #f8fafc; }
.security-banner { display: flex; align-items: flex-start; gap: .8rem; max-width: 1240px; margin: .1rem auto 0; padding: .75rem 1.5rem; color: #70440e; font-size: .8rem; }
.security-banner::before { content: "!"; display: grid; flex: 0 0 21px; height: 21px; place-items: center; border: 1px solid #d9a05d; border-radius: 50%; background: var(--amber-soft); font-weight: 800; }
.security-banner strong { display: block; margin-bottom: .05rem; color: #70440e; }
.security-banner span { color: #88643b; }
.auth-context { display: flex; justify-content: flex-end; gap: 1rem; max-width: 1240px; margin: 0 auto; padding: .1rem 1.5rem .45rem; color: var(--muted); font-size: .72rem; }
.account-details { max-width: 1240px; margin: 0 auto; padding: 0 1.5rem; color: var(--muted); font-size: .78rem; }
.account-details > summary { width: max-content; margin-left: auto; cursor: pointer; }
.account-details[open] > summary { margin-bottom: .5rem; }
.account-details .security-banner, .account-details .auth-context { padding-inline: 0; }
.auth-recovery { margin: 0 0 1rem; border: 1px solid #e9b5b5; border-radius: 10px; padding: 1rem 1.1rem; background: #fff8f8; }
.auth-recovery h2 { margin-bottom: .25rem; color: #8f2323; font-size: 1rem; }
.auth-recovery p { margin-bottom: .7rem; color: #6f4141; font-size: .84rem; }
.auth-recovery a { display: inline-block; border-radius: 6px; padding: .5rem .7rem; background: var(--blue); color: white; font-size: .82rem; font-weight: 700; text-decoration: none; }
.page { max-width: 1240px; margin: auto; padding: 1.5rem 1.5rem 4rem; }
.live-region { min-height: 2rem; }
#status, #error { margin: 0 0 1rem; border-radius: 7px; padding: .65rem .8rem; font-size: .84rem; }
#status:empty { display: none; }
#status { background: var(--green-soft); color: var(--green); }
.error { border: 1px solid #e9b5b5; background: #fff2f2; color: #a12828; font-weight: 700; }
.eyebrow { margin-bottom: .45rem; color: var(--blue); font-size: .68rem; font-weight: 800; letter-spacing: .14em; }
.muted, .field-help { color: var(--muted); }
.lede { max-width: 42rem; margin-bottom: 1.7rem; color: var(--muted); font-size: 1.08rem; }
.welcome-panel { padding: 2.2rem 0 1.3rem; }
.welcome-grid { display: grid; gap: 1rem; }
.surface { border: 1px solid var(--line); border-radius: 12px; background: var(--surface); box-shadow: var(--shadow); }
.library-card { padding: 1.25rem 1.3rem; }
.library-list { border-top: 1px solid var(--line); }
.library-item { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; border-bottom: 1px solid var(--line); padding: .85rem .2rem; }
.library-item .inline-action { font-size: .98rem; font-weight: 800; }
.library-meta { color: var(--muted); font-size: .76rem; text-align: right; }
.graph-library { padding: 1.25rem 1.3rem; }
.graph-library .library-list { margin-top: .75rem; }
.graph-picker-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; }
.graph-picker-card { min-width: 0; border: 1px solid var(--line); border-radius: 9px; padding: .8rem; background: #fbfcff; }
.graph-picker-card .inline-action { display: block; width: 100%; font-size: .95rem; font-weight: 800; overflow-wrap: anywhere; }
.graph-picker-card .library-meta { display: block; margin-top: .35rem; text-align: left; }
.library-actions { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.library-actions > details { min-width: 0; }
.create-card { padding: 1.25rem 1.3rem 1.35rem; }
.create-card summary { display: flex; align-items: center; justify-content: space-between; gap: 1rem; cursor: pointer; list-style: none; }
.create-card summary::-webkit-details-marker { display: none; }
.create-card summary > span:first-child { display: flex; align-items: center; gap: .6rem; font-weight: 800; }
.create-card summary > span:first-child::before { content: "+"; display: grid; width: 25px; height: 25px; place-items: center; border-radius: 7px; background: var(--blue-soft); color: var(--blue); font-size: 1.1rem; }
.step { display: none; }
.summary-caption { color: var(--muted); font-size: .78rem; }
.stacked-form { display: grid; gap: .85rem; margin-top: 1.35rem; }
.row { display: flex; gap: .75rem; align-items: end; flex-wrap: wrap; }
.row > label { flex: 1 1 12rem; }
.field-help { margin: .35rem 0 0; font-size: .76rem; }
.guide-card { display: flex; flex-direction: column; padding: 1.3rem; }
.guide-card h2 { font-size: 1.25rem; }
.guide-card > p:not(.eyebrow) { color: var(--muted); font-size: .9rem; }
.feature-list { display: grid; gap: .75rem; margin-top: .45rem; }
.feature-list li { display: flex; gap: .6rem; color: #435167; font-size: .86rem; }
.feature-list li::before { content: "✓"; color: var(--green); font-weight: 800; }
.safety-note { margin-top: auto; border-top: 1px solid var(--line); padding-top: 1rem; color: #70522f; font-size: .78rem; }
.safety-note strong { display: block; margin-bottom: .2rem; color: #70440e; }
.find-panel { margin-top: 1rem; padding: 1.3rem; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.section-heading p { margin-bottom: 0; color: var(--muted); font-size: .8rem; }
.search-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; }
.search-form { border: 1px solid var(--line); border-radius: 9px; padding: 1rem; background: #fbfcfd; }
.search-form .form-kicker { margin-bottom: .7rem; color: var(--soft); font-size: .68rem; font-weight: 800; letter-spacing: .12em; }
.search-form button { margin-top: .85rem; }
.results-wrap { margin-top: 1.15rem; }
.results-label { margin-bottom: .5rem; color: var(--muted); font-size: .77rem; font-weight: 800; text-transform: uppercase; letter-spacing: .09em; }
.results-list { border-top: 1px solid var(--line); }
.result-item { display: flex; align-items: baseline; gap: .55rem; border-bottom: 1px solid var(--line); padding: .75rem .2rem; color: var(--muted); font-size: .84rem; }
.inline-action { border: 0; padding: .1rem 0; background: transparent; color: var(--blue); font-weight: 700; text-align: left; }
.inline-action:hover { color: var(--blue-dark); text-decoration: underline; }
.workspace { margin-top: 1.8rem; }
.workspace-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; margin-bottom: 1.2rem; }
.breadcrumb { display: flex; gap: .45rem; margin-bottom: .8rem; color: var(--muted); font-size: .78rem; }
.breadcrumb a { text-decoration: none; }
.artifact-title-row { display: flex; align-items: center; gap: .85rem; }
.artifact-icon { display: grid; width: 44px; height: 50px; place-items: center; border: 1px solid #bfcbea; border-radius: 8px; background: var(--blue-soft); color: var(--blue); font-size: 1.15rem; font-weight: 800; }
.artifact-title-row h1 { margin-bottom: .25rem; font-size: clamp(1.8rem, 4vw, 2.6rem); overflow-wrap: anywhere; }
.title-metadata { display: flex; align-items: center; gap: .5rem; color: var(--muted); font-size: .78rem; }
.title-metadata .dot { width: 4px; height: 4px; border-radius: 50%; background: #aab5c2; }
.workspace-nav { display: flex; gap: 1rem; }
.workspace-nav a { color: var(--muted); font-size: .78rem; font-weight: 700; text-decoration: none; }
.workspace-nav a:hover { color: var(--blue); }
.workspace-layout { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(19rem, .85fr); gap: 1rem; align-items: start; }
.workspace.has-tree { margin-top: 0; }
.workspace.has-tree .workspace-layout { grid-template-columns: minmax(13rem, .32fr) minmax(0, 1fr); }
.workspace.has-tree .primary-column, .workspace.has-tree .secondary-column { grid-column: 2; }
.workspace.has-tree .primary-column > .surface, .workspace.has-tree .secondary-column > .surface { border: 0; border-radius: 0; box-shadow: none; }
.tree-panel { min-width: 0; border: 1px solid var(--line); border-radius: 12px; padding: 1rem; background: var(--surface); box-shadow: var(--shadow); }
.tree-heading { border-bottom: 1px solid var(--line); padding-bottom: .75rem; }
.tree-heading h2 { margin-bottom: .2rem; font-size: .95rem; }
.tree-breadcrumb { overflow-wrap: anywhere; margin: 0; color: var(--muted); font-size: .72rem; }
.artifact-tree { margin-top: .75rem; }
.artifact-tree ul { margin: 0; padding-left: .9rem; list-style: none; }
.artifact-tree > ul { padding-left: 0; }
.artifact-tree li { margin: .15rem 0; }
.artifact-tree details > summary { cursor: pointer; padding: .25rem 0; color: #46556b; font-size: .8rem; font-weight: 700; }
.artifact-tree details > summary::marker { color: var(--blue); }
.tree-file { width: 100%; padding: .3rem .25rem; overflow-wrap: anywhere; font-size: .8rem; }
.tree-file[aria-current="page"] { border-radius: 5px; background: var(--blue-soft); color: var(--blue-dark); }
.view-switch { display: inline-flex; gap: .15rem; border: 1px solid var(--line-strong); border-radius: 8px; padding: .15rem; background: var(--surface); }
.view-switch button { border: 0; border-radius: 6px; padding: .45rem .7rem; background: transparent; color: var(--muted); font-size: .78rem; font-weight: 800; }
.view-switch button.is-active { background: var(--blue-soft); color: var(--blue-dark); }
.workspace-actions { display: flex; gap: .5rem; margin-left: auto; }
.workspace-actions a { border: 1px solid var(--line-strong); border-radius: 7px; padding: .52rem .7rem; color: #39485d; font-size: .78rem; font-weight: 700; text-decoration: none; }
.workspace-actions a:hover { border-color: #a9b7c7; background: #f8fafc; }
.workspace.has-tree.graph-mode .tree-panel { position: sticky; top: 1rem; }
.primary-column { display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.secondary-column { display: grid; gap: 1rem; min-width: 0; }
.card-pad { padding: 1.25rem; }
.reader-card { padding: 1.25rem; }
.reader-card .card-heading { align-items: center; }
.reader-card .state-pill { background: var(--blue-soft); color: var(--blue); }
.reader-card pre { min-height: 10rem; max-height: 34rem; margin-top: 0; background: #fbfcfe; font: .9rem/1.7 ui-monospace, SFMono-Regular, Menlo, monospace; }
.document-content { min-height: 10rem; color: var(--ink); font-size: 1rem; line-height: 1.75; }
.document-content h1, .document-content h2, .document-content h3 { margin: 1.25rem 0 .45rem; }
.document-content h1:first-child, .document-content h2:first-child, .document-content h3:first-child { margin-top: 0; }
.document-content p { margin: 0 0 .9rem; white-space: pre-wrap; }
.document-content ul { margin: 0 0 .9rem 1.3rem; list-style: disc; }
.document-content li { margin: .25rem 0; }
.document-content pre { margin: .8rem 0 1rem; }
.primary-column > #reader { order: 1; }
.primary-column > #preview-card { order: 2; }
.workspace.web-first .primary-column > #preview-card { order: 1; }
.workspace.web-first .primary-column > #reader { order: 2; }
.workspace.web-first #preview-card { min-height: 34rem; }
.primary-column > #update { order: 3; }
.advanced-field { margin-top: .5rem; border-top: 1px solid var(--line); padding-top: .7rem; }
.advanced-field summary { cursor: pointer; color: var(--muted); font-size: .8rem; }
.card-heading { display: flex; align-items: start; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.card-heading h2 { margin-bottom: 0; }
.state-pill, .capability { display: inline-flex; align-items: center; border-radius: 999px; padding: .27rem .55rem; background: var(--green-soft); color: var(--green); font-size: .7rem; font-weight: 800; white-space: nowrap; }
.metadata-grid { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .6rem 1.25rem; margin: 0; }
.metadata-grid dt { color: var(--muted); font-size: .75rem; font-weight: 700; }
.metadata-grid dd { margin: 0; overflow-wrap: anywhere; color: #35445a; font: .76rem/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
.editor-card { padding: 1.25rem; }
.editor-card .field-help { margin: -.45rem 0 1rem; }
.editor-card form { display: grid; gap: .8rem; }
.binary-note { border-radius: 7px; padding: .7rem .8rem; background: var(--amber-soft); color: var(--amber); font-size: .82rem; }
.utility-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.utility-card { padding: 1.15rem 1.25rem; }
.utility-card h2 { margin-bottom: .2rem; }
.utility-card > p { margin-bottom: .8rem; color: var(--muted); font-size: .78rem; }
.resource-list { border-top: 1px solid var(--line); }
.resource-item, .history-item, .graph-item { border-bottom: 1px solid var(--line); padding: .6rem 0; color: #47566b; font-size: .8rem; }
.resource-item .inline-action, .history-item .inline-action { width: 100%; }
.graph-card, .preview-card, .access-card { padding: 1.25rem; }
.access-list { margin: .9rem 0 1rem; border-top: 1px solid var(--line); }
.access-row { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.access-row span { overflow-wrap: anywhere; }
.access-row button { border: 0; padding: .25rem 0; background: transparent; color: var(--blue); font-weight: 700; }
.privacy-line { display: flex; align-items: center; gap: .5rem; color: var(--muted); font-size: .8rem; }
.graph-card > p, .preview-card > p { color: var(--muted); font-size: .8rem; }
.graph-list { margin: .9rem 0 1rem; border-top: 1px solid var(--line); }
.graph-item { display: flex; align-items: center; gap: .35rem; flex-wrap: wrap; }
.graph-item::before { content: "↳"; color: var(--blue); font-weight: 800; }
.graph-item .inline-action { overflow-wrap: anywhere; }
.graph-map { display: grid; gap: 1rem; margin: .9rem 0 1rem; border: 1px solid var(--line); border-radius: 10px; padding: 1rem; background: #fbfcff; }
.graph-source { justify-self: center; border-color: var(--blue); background: var(--blue-soft); color: var(--blue-dark); }
.graph-links { display: grid; gap: .65rem; }
.graph-link { display: grid; grid-template-columns: minmax(5rem, .35fr) minmax(0, 1fr); align-items: center; gap: .6rem; }
.graph-edge-label { color: var(--muted); font-size: .72rem; font-weight: 800; text-align: right; }
.graph-node { border: 1px solid var(--line-strong); border-radius: 8px; padding: .55rem .7rem; background: var(--surface); color: var(--blue); font-size: .8rem; font-weight: 800; text-align: left; overflow-wrap: anywhere; }
.graph-target:hover { border-color: var(--blue); background: var(--blue-soft); }
.link-form { display: grid; gap: .75rem; border-top: 1px solid var(--line); padding-top: 1rem; }
.preview-card { background: #fbfcff; }
.preview-card .card-heading { align-items: center; }
.preview-card .card-heading h2 { display: flex; align-items: center; gap: .5rem; }
.preview-card .card-heading h2::before { content: "●"; color: #d1903b; font-size: .75rem; }
.preview-actions { display: flex; justify-content: flex-end; margin: .75rem 0; }
.preview-card:fullscreen { overflow: auto; padding: 1.25rem; background: var(--canvas); }
.preview-card:fullscreen .preview-frame { height: calc(100vh - 12rem); }
.preview-card:fullscreen iframe { height: 100%; min-height: 0; }
.capability-list { display: flex; flex-wrap: wrap; gap: .4rem; margin: .8rem 0; }
.capability { background: #edf1f8; color: #475972; }
#bridge-status { min-height: 2.3rem; margin: .7rem 0; border-radius: 7px; padding: .55rem .65rem; background: #f0f3f8; color: #526176; font-size: .76rem; }
.preview-frame { overflow: hidden; border: 1px solid var(--line-strong); border-radius: 8px; background: white; }
iframe { display: block; width: 100%; min-height: 28rem; border: 0; background: white; }
.human-route { overflow: hidden; }
.human-route .app-shell { min-height: 100vh; display: grid; grid-template-rows: auto minmax(0, 1fr); }
.human-route .topbar { height: 3.6rem; min-height: 3.6rem; flex-wrap: nowrap; padding-block: .65rem; }
.human-route .page { width: 100%; max-width: none; height: calc(100vh - 3.6rem); min-height: 0; padding: 0; }
.standalone-route .page { height: 100vh; }
.human-route #status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.human-route .error, .human-route .auth-recovery { margin: 1rem; }
.human-viewer { height: 100%; min-height: 0; }
.human-viewer-site, .human-viewer-site iframe { width: 100%; height: 100%; min-height: 0; }
.human-viewer-site iframe { display: block; border: 0; background: white; }
.human-document { height: 100%; overflow: auto; background: var(--surface); }
.human-document-body { width: min(48rem, calc(100% - 2rem)); margin: 0 auto; padding: clamp(2rem, 7vw, 5.5rem) 0 6rem; color: var(--ink); font-size: 1.05rem; line-height: 1.75; }
.human-document-body > :first-child { margin-top: 0; }
.human-document-body h1 { font-size: clamp(2rem, 5vw, 3.5rem); line-height: 1.08; letter-spacing: -.04em; }
.human-document-body h2 { margin-top: 2.4rem; font-size: 1.55rem; }
.human-document-body pre { overflow: auto; padding: 1rem; border-radius: 8px; background: var(--canvas); }
.human-document-body.plain-text { white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.human-viewer-title { min-width: 0; margin: 0 auto 0 0; overflow: hidden; color: var(--muted); font-size: .9rem; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.library-action { color: var(--blue); font-size: .76rem; font-weight: 800; }
[hidden] { display: none !important; }
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --ink: #edf3fb;
    --muted: #a7b3c4;
    --soft: #8290a4;
    --line: #344154;
    --line-strong: #4a5a70;
    --surface: #17202d;
    --canvas: #101720;
    --blue: #8cafff;
    --blue-dark: #b4caff;
    --blue-soft: #202f54;
    --green: #7bd6a8;
    --green-soft: #17382d;
    --amber: #efb66e;
    --amber-soft: #3b2b18;
    --shadow: 0 12px 32px rgb(0 0 0 / 22%);
  }
  input, textarea, select, pre, .search-form, .preview-card { background: #111a26; color: var(--ink); }
  .topbar-open button, .button-secondary { background: var(--surface); color: var(--ink); }
  .feature-list li, .metadata-grid dd, .resource-item, .history-item, .graph-item { color: #c3cedc; }
  .capability, #bridge-status { background: #253247; color: #c9d5e5; }
}
@media (max-width: 900px) {
  .topbar { flex-wrap: wrap; gap: .8rem 1.3rem; }
  .topbar-open { order: 3; width: 100%; }
  .workspace-heading { align-items: start; flex-direction: column; }
  .workspace-layout { grid-template-columns: 1fr; }
  .workspace.has-tree .workspace-layout { grid-template-columns: 1fr; }
  .workspace.has-tree .primary-column, .workspace.has-tree .secondary-column { grid-column: auto; }
  .library-actions { grid-template-columns: 1fr; }
  .secondary-column { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
}
@media (max-width: 640px) {
  .topbar, .security-banner, .page { padding-inline: 1rem; }
  .primary-nav { order: 2; width: 100%; }
  .welcome-panel { padding-top: 1.3rem; }
  .welcome-grid, .search-grid, .utility-grid, .secondary-column { grid-template-columns: 1fr; }
  .section-heading { align-items: start; flex-direction: column; gap: .35rem; }
  .metadata-grid { grid-template-columns: 1fr; gap: .15rem; }
  .metadata-grid dd { margin-bottom: .45rem; }
  .result-item { align-items: start; flex-direction: column; gap: .15rem; }
  .workspace-nav { flex-wrap: wrap; }
  .auth-context { justify-content: flex-start; flex-wrap: wrap; padding-inline: 1rem; }
  .account-details { padding-inline: 1rem; }
  .library-item { align-items: start; flex-direction: column; gap: .2rem; }
  .library-meta { text-align: left; }
}

/* Product UI: a quiet graph library and a content-first workspace. */
body:not(.debug-route):not(.human-route):not(.standalone-route),
body.workspace-route {
  color-scheme: light;
  --ink: #171717;
  --muted: #6e6e6e;
  --soft: #8b8b8b;
  --line: #e2e2e2;
  --line-strong: #cfcfcf;
  --surface: #ffffff;
  --canvas: #fafafa;
  --blue: #252525;
  --blue-dark: #111111;
  --blue-soft: #f0f0f0;
  --green: #444444;
  --green-soft: #f1f1f1;
  --shadow: 0 18px 42px rgb(0 0 0 / 6%);
  background: var(--canvas);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .topbar {
  max-width: none;
  min-height: 76px;
  padding: 0 34px;
  border-bottom: 1px solid var(--line);
  background: rgb(250 250 250 / 88%);
  backdrop-filter: blur(16px);
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .brand-lockup { gap: .55rem; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .brand-mark {
  width: 30px;
  height: 30px;
  border-radius: 10px;
  background: #e8e8e8;
  color: var(--ink);
  font-size: .85rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .brand-name { font-size: .92rem; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .brand-subtitle { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav {
  align-items: center;
  gap: 1.5rem;
  margin-left: auto;
  margin-right: 0;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a {
  border: 0;
  padding: .5rem 0;
  color: var(--muted);
  font-size: .82rem;
  font-weight: 700;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a:hover,
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a.is-active { color: var(--ink); }
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a.is-active { border-bottom: 2px solid var(--ink); }
body:not(.debug-route):not(.human-route):not(.standalone-route) .nav-index { display: none; }
.avatar {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 50%;
  background: var(--ink);
  color: #fff;
  font-size: .72rem;
  font-weight: 800;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .page {
  width: min(1180px, calc(100% - 68px));
  max-width: none;
  padding: 68px 0 100px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel { padding: 0; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > .eyebrow { margin-bottom: 13px; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > h1 {
  max-width: 680px;
  margin-bottom: 14px;
  font-size: clamp(40px, 6vw, 74px);
  line-height: .98;
  letter-spacing: -.065em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > .lede {
  max-width: 520px;
  margin-bottom: 0;
  color: var(--muted);
  font-size: 1rem;
  line-height: 1.55;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading {
  align-items: end;
  margin: 66px 0 22px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading h2 {
  margin-bottom: 0;
  font-size: 1.35rem;
  letter-spacing: -.045em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading p {
  margin: 0;
  color: var(--muted);
  font-size: .8rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 15px;
  margin: 0;
  border: 0;
  list-style: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card {
  display: flex;
  min-height: 235px;
  flex-direction: column;
  justify-content: space-between;
  min-width: 0;
  padding: 22px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--surface);
  box-shadow: 0 4px 14px rgb(0 0 0 / 3%);
  transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card:hover {
  border-color: #b4b4b4;
  box-shadow: var(--shadow);
  transform: translateY(-3px);
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-card-action {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-card-top {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 14px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-icon {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 13px;
  background: var(--blue-soft);
  color: var(--blue);
  font-size: 1.1rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-open-mark {
  color: var(--muted);
  font-size: 1.1rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card h3 {
  margin: 20px 0 8px;
  overflow-wrap: anywhere;
  font-size: 1.2rem;
  letter-spacing: -.03em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card .graph-description {
  max-width: 245px;
  margin: 0;
  color: var(--muted);
  font-size: .8rem;
  line-height: 1.48;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card .graph-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 28px;
  color: var(--muted);
  font-size: .7rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-meta .status-dot { gap: 6px; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-meta .status-dot::before {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--blue);
  content: "";
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .library-actions {
  display: flex;
  justify-content: flex-start;
  margin-top: 20px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card {
  width: min(480px, 100%);
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card summary {
  display: inline-flex;
  min-height: 42px;
  padding: 0 17px;
  border-radius: 999px;
  background: #e8e8e8;
  color: var(--ink);
  font-size: .8rem;
  font-weight: 800;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card summary > span:first-child::before { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card[open] {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  box-shadow: var(--shadow);
}
body.workspace-route { min-height: 100vh; background: #f7f7f5; }
body.workspace-route .app-shell { min-height: 100vh; }
body.workspace-route .topbar {
  max-width: none;
  min-height: 66px;
  padding: 0 28px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
body.workspace-route .brand-lockup { display: none; }
body.workspace-route .primary-nav { width: 100%; margin: 0; }
body.workspace-route .primary-nav a { display: none; }
body.workspace-route .page { width: 100%; max-width: none; height: calc(100vh - 66px); min-height: 0; padding: 0; }
body.workspace-route .workspace { margin: 0; }
body.workspace-route .workspace-heading {
  display: flex;
  align-items: center;
  min-height: 64px;
  margin: 0;
  padding: 0 28px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
body.workspace-route .workspace-heading > div:first-child { min-width: 0; margin-right: auto; }
body.workspace-route .workspace-heading .breadcrumb { display: none; }
body.workspace-route .artifact-title-row { gap: 0; }
body.workspace-route .artifact-title-row .artifact-icon,
body.workspace-route .artifact-title-row .eyebrow,
body.workspace-route .artifact-title-row .title-metadata { display: none; }
body.workspace-route .artifact-title-row h1 {
  margin: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: .95rem;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}
body.workspace-route .workspace-nav { align-items: center; gap: 8px; }
body.workspace-route .workspace-nav > a:first-child {
  order: -2;
  border: 0;
  padding: 8px 0;
  color: var(--muted);
  font-size: .82rem;
}
body.workspace-route .workspace-nav > a:not(:first-child) { display: none; }
body.workspace-route .workspace-nav .view-switch,
body.workspace-route .workspace-nav .workspace-actions { display: inline-flex; }
body.workspace-route .workspace-layout {
  min-height: calc(100vh - 130px);
  grid-template-columns: 290px minmax(0, 1fr);
  gap: 0;
}
body.workspace-route .tree-panel {
  min-height: calc(100vh - 130px);
  padding: 28px 17px;
  border: 0;
  border-right: 1px solid var(--line);
  border-radius: 0;
  background: #f1f1f1;
  box-shadow: none;
}
body.workspace-route .tree-heading { border: 0; padding: 0 10px 20px; }
body.workspace-route .tree-heading h2 { font-size: .8rem; letter-spacing: .08em; text-transform: uppercase; }
body.workspace-route .tree-breadcrumb { color: var(--muted); }
body.workspace-route .artifact-tree details > summary,
body.workspace-route .tree-file {
  min-height: 36px;
  padding: 9px 10px;
  border-radius: 9px;
  color: #383838;
  font-size: .84rem;
}
body.workspace-route .artifact-tree details > summary:hover,
body.workspace-route .tree-file:hover { background: #e6e6e6; }
body.workspace-route .tree-file[aria-current="page"] { background: #dedede; color: var(--ink); font-weight: 800; }
body.workspace-route .primary-column,
body.workspace-route .secondary-column { grid-column: 2; }
body.workspace-route .primary-column > .surface,
body.workspace-route .secondary-column > .surface {
  border: 0;
  border-radius: 0;
  box-shadow: none;
}
body.workspace-route .content-inner {
  width: min(780px, calc(100% - 72px));
  padding: 64px 0 100px;
}
body.workspace-route .content-breadcrumb { margin-bottom: 32px; font-size: .76rem; }
body.workspace-route .reader-card,
body.workspace-route .preview-card {
  padding: 64px max(36px, calc((100% - 780px) / 2)) 100px;
  background: var(--surface);
}
body.workspace-route .reader-card .card-heading,
body.workspace-route .preview-card .card-heading { display: none; }
body.workspace-route .reader-card .field-help,
body.workspace-route .preview-card > .field-help { display: none; }
body.workspace-route .reader-card .document-content {
  max-width: 780px;
  margin: 0 auto;
  font-size: 1rem;
}
body.workspace-route .reader-card .document-content::before {
  display: block;
  margin-bottom: 26px;
  color: var(--muted);
  content: "Document";
  font-size: .68rem;
  font-weight: 800;
  letter-spacing: .16em;
  text-transform: uppercase;
}
body.workspace-route .preview-card { min-height: calc(100vh - 130px); padding-top: 0; }
body.workspace-route .preview-frame { min-height: calc(100vh - 130px); border: 0; border-radius: 0; }
body.workspace-route .preview-frame iframe { min-height: calc(100vh - 130px); }
body.workspace-route .graph-card { padding: 64px max(36px, calc((100% - 1100px) / 2)) 100px; background: var(--surface); }
body.workspace-route .graph-card .card-heading { display: none; }
body.workspace-route .graph-card .graph-map { min-height: calc(100vh - 260px); border: 1px solid var(--line); border-radius: 0; background: #fafafa; box-shadow: none; }
body.workspace-route .graph-card .graph-list,
body.workspace-route .graph-card .link-form,
body.workspace-route .workspace-share { display: none; }
body.workspace-route .workspace-actions a { border: 0; color: var(--muted); background: transparent; font-size: .8rem; }
body.workspace-route .workspace-actions a:hover { color: var(--ink); background: transparent; }
body.workspace-route .view-switch { border-color: var(--line); border-radius: 999px; background: #f1f1f1; }
body.workspace-route .view-switch button { min-height: 30px; border-radius: 999px; color: var(--muted); }
body.workspace-route .view-switch button.is-active { background: var(--surface); color: var(--ink); }
@media (max-width: 900px) {
  body:not(.debug-route):not(.human-route):not(.standalone-route) .page { width: min(100% - 36px, 620px); padding-top: 48px; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-list { grid-template-columns: 1fr; }
  body.workspace-route .workspace-layout { grid-template-columns: 224px minmax(0, 1fr); }
  body.workspace-route .workspace-heading { padding-inline: 16px; }
  body.workspace-route .workspace-nav .workspace-actions { display: none; }
  body.workspace-route .tree-panel { padding: 17px 12px; }
}
@media (max-width: 590px) {
  body:not(.debug-route):not(.human-route):not(.standalone-route) .topbar { padding-inline: 18px; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav { gap: .8rem; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a { display: none; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-nav a:first-child { display: inline-flex; }
  body.workspace-route .workspace-layout { display: block; }
  body.workspace-route .tree-panel { min-height: auto; }
  body.workspace-route .primary-column,
  body.workspace-route .secondary-column { display: block; }
  body.workspace-route .reader-card,
  body.workspace-route .preview-card { padding: 36px 22px 70px; }
}

/* Human product pass: the system disappears behind the work. */
body:not(.debug-route):not(.human-route):not(.standalone-route),
body.workspace-route {
  color-scheme: light;
  --ink: #181a18;
  --muted: #70756f;
  --soft: #969c95;
  --line: #dfe2dc;
  --line-strong: #c8cdc5;
  --surface: #ffffff;
  --canvas: #f7f8f5;
  --blue: #2956d7;
  --blue-dark: #1f46b9;
  --blue-soft: #eef2ff;
  --shadow: 0 10px 24px rgb(28 35 25 / 5%);
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .page {
  max-width: 1120px;
  padding: 46px 32px 80px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .live-region { min-height: 0; }
body:not(.debug-route):not(.human-route):not(.standalone-route) #status {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel { padding: 0; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > h1 {
  margin-bottom: 30px;
  font-size: clamp(2rem, 4vw, 3rem);
  letter-spacing: -.05em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > .lede { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library {
  border: 0;
  border-radius: 0;
  padding: 0;
  background: transparent;
  box-shadow: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading {
  align-items: center;
  margin-bottom: 18px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading h2 {
  margin: 0;
  font-size: 1rem;
  letter-spacing: -.01em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-library .section-heading .eyebrow { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  border: 0;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card {
  min-height: 162px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0;
  background: var(--surface);
  box-shadow: none;
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card:hover {
  border-color: #aeb6aa;
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-card-action {
  display: flex;
  width: 100%;
  min-height: 122px;
  flex-direction: column;
  align-items: stretch;
  border: 0;
  padding: 18px 18px 4px;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-icon {
  color: var(--muted);
  font-size: 1.15rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-open-mark {
  color: var(--soft);
  font-size: 1.05rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card h3 {
  margin: 0 0 6px;
  overflow-wrap: anywhere;
  font-size: 1.02rem;
  letter-spacing: -.02em;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card .graph-description {
  display: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card .graph-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid var(--line);
  padding: 12px 18px;
  color: var(--muted);
  font-size: .72rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-meta .status-dot::before { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-button {
  border: 0;
  border-radius: 0;
  padding: 0;
  background: transparent;
  color: var(--ink);
  font-size: .82rem;
  font-weight: 800;
  text-decoration: underline;
  text-decoration-color: var(--line-strong);
  text-underline-offset: 4px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .primary-button:hover {
  background: transparent;
  color: var(--blue);
  text-decoration-color: currentColor;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .library-actions { display: block; margin-top: 24px; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card {
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  padding: 16px 0 0;
  background: transparent;
  box-shadow: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card summary {
  display: inline-flex;
  min-height: 32px;
  padding: 0;
  background: transparent;
  color: var(--ink);
  font-size: .82rem;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card summary > span:first-child::before { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .create-card[open] {
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  padding: 16px 0 0;
  background: transparent;
  box-shadow: none;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .shared-empty {
  margin-top: 64px;
  border-top: 1px solid var(--line);
  padding-top: 20px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .shared-empty .section-heading { margin-bottom: 4px; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library {
  margin-top: 16px;
  padding: 24px 28px;
}
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library .section-heading { margin-bottom: 8px; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library .section-heading .eyebrow { display: none; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library .section-heading h2 { font-size: 1rem; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library .library-list { margin-top: 0; }
body:not(.debug-route):not(.human-route):not(.standalone-route) .artifact-library .library-item { padding-inline: 0; }
body.workspace-route {
  min-height: 100vh;
  overflow: hidden;
  background: var(--canvas);
}
body.workspace-route .topbar { display: none; }
body.workspace-route:not(.debug-route):not(.human-route):not(.standalone-route) .page {
  width: 100%;
  max-width: none;
  margin: 0;
  height: 100vh;
  min-height: 0;
  padding: 0;
}
body.workspace-route .live-region { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
body.workspace-route .workspace { display: block; height: 100vh; margin: 0; }
body.workspace-route .workspace-heading {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 18px;
  min-height: 58px;
  height: 58px;
  margin: 0;
  border-bottom: 1px solid var(--line);
  padding: 0 24px;
  background: var(--surface);
}
body.workspace-route .workspace-heading > div:first-child {
  display: block;
  min-width: 0;
  margin: 0;
  flex: 1 1 auto;
}
body.workspace-route .workspace-heading .breadcrumb,
body.workspace-route .artifact-title-row .artifact-icon,
body.workspace-route .artifact-title-row .eyebrow,
body.workspace-route .artifact-title-row .title-metadata { display: none; }
body.workspace-route .artifact-title-row { gap: 0; }
body.workspace-route .artifact-title-row h1 {
  margin: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: .92rem;
  font-weight: 800;
  letter-spacing: -.015em;
  text-overflow: ellipsis;
  white-space: nowrap;
}
body.workspace-route .workspace-nav {
  display: flex;
  width: auto;
  align-items: center;
  flex: 0 1 auto;
  gap: 10px;
  min-width: 0;
}
body.workspace-route .workspace-nav > a:first-child {
  order: -2;
  border: 0;
  padding: 8px 0;
  color: var(--muted);
  font-size: .8rem;
  white-space: nowrap;
}
body.workspace-route .workspace-nav > a:first-child:hover { color: var(--ink); }
body.workspace-route .workspace-nav > a:not(:first-child):not(#standalone-link):not(.workspace-actions a) { display: none; }
body.workspace-route .workspace-nav .view-switch { order: -1; }
body.workspace-route .workspace-nav .workspace-actions { order: 0; margin-left: 2px; }
body.workspace-route .workspace-actions a,
body.workspace-route .workspace-actions button {
  border: 0;
  border-radius: 6px;
  padding: 7px 8px;
  color: var(--muted);
  background: transparent;
  font-size: .78rem;
  font-weight: 700;
}
body.workspace-route .workspace-actions a:hover,
body.workspace-route .workspace-actions button:hover { color: var(--ink); background: #f1f2ee; }
body.workspace-route .workspace-option {
  display: inline-flex;
  order: 0;
  border-radius: 6px;
  padding: 7px 8px;
  color: var(--muted);
  font-size: .78rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
}
body.workspace-route .workspace-option:hover { color: var(--ink); background: #f1f2ee; }
body.workspace-route .workspace-nav > a#standalone-link { display: inline-flex; }
body.workspace-route .view-switch {
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 2px;
  background: #f6f7f4;
}
body.workspace-route .view-switch button {
  min-height: 28px;
  border-radius: 5px;
  padding: 4px 9px;
  color: var(--muted);
  font-size: .75rem;
}
body.workspace-route .view-switch button.is-active { background: var(--surface); color: var(--ink); box-shadow: 0 1px 3px rgb(20 25 19 / 9%); }
body.workspace-route .workspace-search {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 220px;
  margin-left: 4px;
}
body.workspace-route .workspace-search input {
  height: 32px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0 9px;
  background: #fafbf9;
  font-size: .76rem;
}
body.workspace-route .workspace-search input:focus { background: var(--surface); }
body.workspace-route .workspace-search button {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0;
  background: var(--surface);
  color: var(--muted);
  font-size: .95rem;
}
body.workspace-route .workspace-search button:hover { color: var(--ink); background: #f1f2ee; }
body.workspace-route .workspace-search-results {
  position: fixed;
  top: 67px;
  right: 24px;
  z-index: 5;
  width: min(380px, calc(100% - 32px));
  max-height: 60vh;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 16px;
  background: var(--surface);
  box-shadow: 0 18px 42px rgb(24 30 22 / 14%);
}
body.workspace-route .workspace-search-results .result-item { padding: 9px 0; }
body.workspace-route .workspace-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 0;
  height: calc(100vh - 58px);
  min-height: 0;
}
body.workspace-route .workspace.has-tree .workspace-layout { grid-template-columns: 260px minmax(0, 1fr); }
body.workspace-route .tree-panel {
  min-width: 0;
  height: calc(100vh - 58px);
  overflow: auto;
  border: 0;
  border-right: 1px solid var(--line);
  border-radius: 0;
  padding: 24px 16px;
  background: #f1f2ef;
  box-shadow: none;
}
body.workspace-route .tree-heading { border: 0; padding: 0 10px 18px; }
body.workspace-route .tree-heading h2 { margin-bottom: 3px; font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; }
body.workspace-route .tree-breadcrumb { color: var(--soft); }
body.workspace-route .artifact-tree details > summary,
body.workspace-route .tree-file {
  min-height: 36px;
  border-radius: 6px;
  padding: 8px 10px;
  color: #4b514b;
  font-size: .82rem;
}
body.workspace-route .artifact-tree details > summary:hover,
body.workspace-route .tree-file:hover { background: #e5e7e2; }
body.workspace-route .tree-file[aria-current="page"] { background: #dfe2dc; color: var(--ink); font-weight: 800; }
body.workspace-route .workspace.has-tree .primary-column,
body.workspace-route .workspace.has-tree .secondary-column { grid-column: 2; min-width: 0; min-height: 0; }
body.workspace-route .primary-column { display: block; height: calc(100vh - 58px); }
body.workspace-route .primary-column > .surface,
body.workspace-route .secondary-column > .surface { border: 0; border-radius: 0; box-shadow: none; }
body.workspace-route #reader:not([hidden]),
body.workspace-route #preview-card:not([hidden]) {
  display: block;
  height: calc(100vh - 58px);
  min-height: 0;
  overflow: auto;
}
body.workspace-route .reader-card {
  padding: 54px clamp(30px, 7vw, 118px) 84px;
  background: var(--surface);
}
body.workspace-route .reader-card .card-heading,
body.workspace-route .reader-card #reader-note,
body.workspace-route .preview-card .card-heading,
body.workspace-route .preview-card > .field-help { display: none; }
body.workspace-route .reader-card .document-slot {
  max-width: 780px;
  min-height: 0;
  margin: 0 auto;
}
body.workspace-route .reader-card .document-slot::before { display: none; }
body.workspace-route .reader-card .document-content::before { display: none; }
body.workspace-route .reader-card .document-content {
  max-width: none;
  margin: 0;
  color: var(--ink);
  font-size: 1.04rem;
  line-height: 1.8;
}
body.workspace-route .reader-card .document-content h1 {
  margin: 0 0 28px;
  font-size: clamp(2rem, 4vw, 3.15rem);
  line-height: 1.08;
  letter-spacing: -.05em;
}
body.workspace-route .reader-card .document-content h2 { margin: 42px 0 10px; font-size: 1.45rem; }
body.workspace-route .reader-card .document-content h3 { margin: 30px 0 8px; font-size: 1.08rem; }
body.workspace-route .reader-card .document-content p { max-width: 68ch; margin-bottom: 1.1rem; }
body.workspace-route .reader-card .document-content ul { max-width: 68ch; margin-bottom: 1.1rem; }
body.workspace-route .reader-card .document-content pre { margin: 24px 0; border: 1px solid var(--line); border-radius: 7px; padding: 16px; background: #f6f7f4; }
body.workspace-route .preview-card { height: calc(100vh - 58px); padding: 0; background: var(--surface); }
body.workspace-route .preview-frame,
body.workspace-route .preview-frame iframe { height: calc(100vh - 58px); min-height: 0; border: 0; border-radius: 0; }
body.workspace-route .graph-card { height: calc(100vh - 58px); min-height: 0; padding: 42px clamp(28px, 6vw, 100px); background: var(--surface); }
body.workspace-route .graph-card .card-heading { display: none; }
body.workspace-route .graph-card .graph-map { min-height: 0; margin: 0; border: 1px solid var(--line); border-radius: 7px; background: #fafbf9; box-shadow: none; }
body.workspace-route .graph-card .graph-list,
body.workspace-route .graph-card .link-form { display: none; }
body.workspace-route .secondary-column { position: static; }
body.workspace-route .workspace.graph-mode .primary-column { display: none; }
body.workspace-route .workspace.graph-mode .secondary-column {
  display: block;
  grid-row: 1;
  height: calc(100vh - 58px);
}
body.workspace-route #update:not([open]),
body.workspace-route #workspace-share:not([open]) { display: none; }
body.workspace-route #update[open],
body.workspace-route #workspace-share[open] {
  position: fixed;
  z-index: 6;
  inset: 76px 24px auto auto;
  display: block;
  width: min(460px, calc(100% - 32px));
  max-height: calc(100vh - 100px);
  overflow: auto;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 22px;
  background: var(--surface);
  box-shadow: 0 20px 56px rgb(24 30 22 / 18%);
}
body.workspace-route #update::backdrop,
body.workspace-route #workspace-share::backdrop { background: rgb(19 23 18 / 18%); }
body.workspace-route .panel-heading { display: flex; align-items: start; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
body.workspace-route .panel-heading h2 { margin: 0; font-size: 1.15rem; }
body.workspace-route .panel-heading .eyebrow { margin-bottom: 4px; }
body.workspace-route .panel-close {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0;
  background: var(--surface);
  color: var(--muted);
  font-size: 1.3rem;
  line-height: 1;
}
body.workspace-route .panel-close:hover { color: var(--ink); background: #f1f2ee; }
body.workspace-route #update[open] .stacked-form,
body.workspace-route #workspace-share[open] .link-form { margin-top: 0; }
body.workspace-route #workspace-share .card-heading { align-items: start; }
body.workspace-route #workspace-share .privacy-line { align-items: center; gap: 8px; margin-bottom: 18px; }
body.workspace-route #workspace-share .state-pill { flex: 0 0 auto; }
@media (max-width: 900px) {
  body:not(.debug-route):not(.human-route):not(.standalone-route) .page { width: 100%; max-width: 1120px; padding-inline: 22px; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  body.workspace-route .workspace-heading { padding-inline: 16px; gap: 12px; }
  body.workspace-route .workspace-nav .workspace-actions { display: flex; }
  body.workspace-route .workspace-search { width: min(180px, 23vw); }
  body.workspace-route .workspace.has-tree .workspace-layout { grid-template-columns: 224px minmax(0, 1fr); }
  body.workspace-route .tree-panel { padding-inline: 12px; }
}
@media (max-width: 640px) {
  body:not(.debug-route):not(.human-route):not(.standalone-route) .page { padding: 30px 16px 60px; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .welcome-panel > h1 { margin-bottom: 30px; }
  body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-list { grid-template-columns: 1fr; }
  body.workspace-route .workspace-heading { height: auto; min-height: 58px; padding-block: 10px; }
  body.workspace-route .workspace-heading > div:first-child { max-width: 30%; }
  body.workspace-route .workspace-nav { flex-wrap: wrap; justify-content: flex-end; }
  body.workspace-route .workspace-search { width: 150px; }
  body.workspace-route .workspace.has-tree .workspace-layout { display: grid; grid-template-columns: minmax(158px, 42vw) minmax(0, 1fr); height: calc(100vh - 78px); }
  body.workspace-route .tree-panel { height: calc(100vh - 78px); }
  body.workspace-route .primary-column { height: calc(100vh - 78px); }
  body.workspace-route #reader:not([hidden]),
  body.workspace-route #preview-card:not([hidden]) { height: calc(100vh - 78px); }
  body.workspace-route .preview-frame,
  body.workspace-route .preview-frame iframe { height: calc(100vh - 78px); }
  body.workspace-route .reader-card { padding: 34px 22px 60px; }
  body.workspace-route .graph-card { height: calc(100vh - 78px); padding: 26px 20px; }
  body.workspace-route #update[open],
  body.workspace-route #workspace-share[open] { inset: 70px 16px auto; width: auto; max-height: calc(100vh - 86px); }
}
@media (prefers-reduced-motion: reduce) {
  body:not(.debug-route):not(.human-route):not(.standalone-route) .graph-picker-card { transition: none; }
}
""".strip()

UI_JS = r"""
const byId = (id) => document.getElementById(id);
const parts = location.pathname.split('/').filter(Boolean);
const debugMode = parts[0] === 'inspect';
const workspaceMode = parts[0] === 'workspace';
const standaloneMode = parts[0] === 'standalone';
const humanArtifactMode = parts[0] === 'artifacts' || standaloneMode || workspaceMode;
const artifactId = debugMode || humanArtifactMode ? decodeURIComponent(parts[1] || '') : '';
const renderOrigin = document.body.dataset.renderOrigin;
let wasAuthenticated = document.body.dataset.authState === 'authenticated';
let activeRequests = 0;
let workspaceWeb = false;

function artifactPath(id) {
  const prefix = debugMode ? 'inspect' : workspaceMode ? 'workspace' : 'artifacts';
  return `/${prefix}/${encodeURIComponent(id)}`;
}
function workspacePath(id) {
  return `/${debugMode ? 'inspect' : 'workspace'}/${encodeURIComponent(id)}`;
}
function standalonePath(id) {
  return `/standalone/${encodeURIComponent(id)}`;
}

function safeReturnPath() {
  const path = location.pathname;
  return path === '/' || /^\/(?:inspect|artifacts|standalone|workspace)\/[^/]+$/.test(path) ? path : '/';
}
function authLink(path = safeReturnPath()) {
  return `/sign-in?return_to=${encodeURIComponent(path)}`;
}
function clearAuthRecovery() {
  byId('auth-recovery').hidden = true;
  byId('auth-action').hidden = true;
}
function hideProtectedView() {
  byId('workspace')?.setAttribute('hidden', '');
  byId('welcome')?.setAttribute('hidden', '');
  byId('human-viewer')?.setAttribute('hidden', '');
  if (byId('title')) byId('title').textContent = 'Artifact';
  byId('artifact-details')?.replaceChildren();
  byId('chunks')?.replaceChildren();
  byId('versions')?.replaceChildren();
  byId('graph')?.replaceChildren();
  byId('people-with-access')?.replaceChildren();
  if (byId('chunk-content')) byId('chunk-content').textContent = '';
  if (byId('readable-content')) byId('readable-content').replaceChildren();
  byId('results')?.replaceChildren();
  byId('recent-artifacts')?.replaceChildren();
  byId('artifact-tree')?.replaceChildren();
  byId('preview')?.removeAttribute('src');
  byId('human-preview')?.removeAttribute('src');
}
function showAuthFailure() {
  const message = wasAuthenticated
    ? 'Your session expired. Sign in again.'
    : 'Sign-in required. Sign in to continue.';
  wasAuthenticated = false;
  hideProtectedView();
  byId('auth-context')?.setAttribute('hidden', '');
  failure(message);
  byId('auth-recovery-title').textContent = 'Authentication required';
  byId('auth-recovery-message').textContent = message;
  byId('auth-action').textContent = 'Sign in';
  byId('auth-action').href = authLink();
  byId('auth-action').hidden = false;
  byId('auth-recovery').hidden = false;
}
function showForbiddenFailure() {
  hideProtectedView();
  failure('This document is not available to you.');
  byId('auth-recovery-title').textContent = 'Access unavailable';
  byId('auth-recovery-message').textContent = 'This document is not available to you.';
  byId('auth-action').textContent = 'Return home';
  byId('auth-action').href = '/';
  byId('auth-action').hidden = false;
  byId('auth-recovery').hidden = false;
}
function handleFailure(error) {
  if (error.status === 401) { showAuthFailure(); return; }
  if (error.status === 403) { showForbiddenFailure(); return; }
  if (error.status === 409) {
    failure('A newer version already exists. Refresh before saving again; your edit remains here.');
    return;
  }
  failure(error.message);
}

function status(message) {
  clearAuthRecovery();
  byId('error').hidden = true;
  byId('status').textContent = message;
}
function failure(message) {
  clearAuthRecovery();
  byId('status').textContent = '';
  byId('error').textContent = message;
  byId('error').hidden = false;
  byId('error').focus();
}
function busy(value) {
  // A request count keeps parallel artifact reads from clearing busy state early.
  activeRequests = Math.max(0, activeRequests + (value ? 1 : -1));
  const isBusy = activeRequests > 0;
  byId('main').setAttribute('aria-busy', String(isBusy));
  document.querySelectorAll('button[type="submit"]').forEach((item) => { item.disabled = isBusy; });
}
async function call(tool, args, endpoint = '/api/mcp') {
  busy(true);
  try {
    const response = await fetch(endpoint, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify(endpoint === '/api/bridge' ? args : {tool, arguments: args}),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `Request failed (${response.status})`);
      error.status = response.status; throw error;
    }
    return result;
  } finally { busy(false); }
}
function list(id, items, render, empty) {
  const target = byId(id); if (!target) return;
  target.replaceChildren();
  if (!items.length) {
    const item = document.createElement('li'); item.className = 'muted';
    item.textContent = empty; target.append(item); return;
  }
  items.forEach((value) => target.append(render(value)));
}
function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || 'Unknown date');
  return new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric'}).format(date);
}
function button(label, action) {
  const value = document.createElement('button'); value.type = 'button';
  value.className = 'inline-action';
  value.textContent = label; value.addEventListener('click', action); return value;
}
function setWorkspaceMode(mode, persist = true) {
  const graph = mode === 'graph';
  byId('read-mode')?.classList.toggle('is-active', !graph);
  byId('graph-mode')?.classList.toggle('is-active', graph);
  byId('read-mode')?.setAttribute('aria-selected', String(!graph));
  byId('graph-mode')?.setAttribute('aria-selected', String(graph));
  byId('read-mode')?.setAttribute('tabindex', graph ? '-1' : '0');
  byId('graph-mode')?.setAttribute('tabindex', graph ? '0' : '-1');
  byId('reader')?.toggleAttribute('hidden', graph || workspaceWeb);
  byId('preview-card')?.toggleAttribute('hidden', graph || !workspaceWeb);
  if (graph) {
    ['update', 'workspace-share'].forEach((id) => {
      const panel = byId(id);
      if (panel?.open && typeof panel.close === 'function') panel.close();
    });
  }
  byId('graph-context')?.toggleAttribute('hidden', !graph);
  byId('workspace')?.classList.toggle('graph-mode', graph);
  if (workspaceMode && persist) {
    const query = new URLSearchParams(location.search);
    if (graph) query.set('view', 'graph'); else query.delete('view');
    const suffix = query.toString();
    history.replaceState(null, '', `${location.pathname}${suffix ? `?${suffix}` : ''}`);
  }
}
byId('read-mode')?.addEventListener('click', () => setWorkspaceMode('read'));
byId('graph-mode')?.addEventListener('click', () => setWorkspaceMode('graph'));
document.querySelectorAll('[role="tab"]').forEach((tab) => tab.addEventListener('keydown', (event) => {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const next = tabs[(tabs.indexOf(event.currentTarget) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
  next.focus(); next.click();
}));

function updatePrimaryNav() {
  if (debugMode || workspaceMode || standaloneMode) return;
  const hash = location.hash;
  const target = hash === '#graphs' ? '/#graphs' : hash === '#shared' ? '/#shared' : '/';
  document.querySelectorAll('.primary-nav a').forEach((link) => {
    const active = link.getAttribute('href') === target;
    link.classList.toggle('is-active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}
addEventListener('hashchange', updatePrimaryNav);
updatePrimaryNav();

let lastPanelTrigger = null;
function openWorkspacePanel(id, triggerId) {
  const panel = byId(id);
  if (!panel) return;
  lastPanelTrigger = byId(triggerId);
  lastPanelTrigger?.setAttribute('aria-expanded', 'true');
  if (typeof panel.showModal === 'function') panel.showModal();
  else panel.classList.add('is-open');
  setTimeout(() => panel.querySelector('textarea, input:not([type="hidden"])')?.focus(), 0);
}
function closeWorkspacePanel(id) {
  const panel = byId(id);
  if (!panel) return;
  lastPanelTrigger?.setAttribute('aria-expanded', 'false');
  if (typeof panel.close === 'function' && panel.open) panel.close();
  else panel.classList.remove('is-open');
  lastPanelTrigger?.focus();
  lastPanelTrigger = null;
}
byId('update')?.addEventListener('close', () => {
  byId('update')?.classList.remove('is-open');
  lastPanelTrigger?.setAttribute('aria-expanded', 'false');
  lastPanelTrigger?.focus(); lastPanelTrigger = null;
});
byId('workspace-share')?.addEventListener('close', () => {
  byId('workspace-share')?.classList.remove('is-open');
  lastPanelTrigger?.setAttribute('aria-expanded', 'false');
  lastPanelTrigger?.focus(); lastPanelTrigger = null;
});
byId('close-edit')?.addEventListener('click', () => closeWorkspacePanel('update'));
byId('close-share')?.addEventListener('click', () => closeWorkspacePanel('workspace-share'));

function renderArtifactTree(artifacts) {
  const target = byId('artifact-tree');
  if (!target) return;
  target.replaceChildren();
  if (!artifacts.length) {
    const empty = document.createElement('p'); empty.className = 'muted';
    empty.textContent = 'No artifacts yet.'; target.append(empty); return;
  }
  const root = {folders: new Map(), files: []};
  artifacts.forEach((artifact) => {
    const parts = String(artifact.name || artifact.id).split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, index) => {
      if (index === parts.length - 1) { node.files.push({name: part, artifact}); return; }
      if (!node.folders.has(part)) node.folders.set(part, {folders: new Map(), files: []});
      node = node.folders.get(part);
    });
  });
  const renderNode = (node) => {
    const list = document.createElement('ul');
    [...node.folders.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([name, child]) => {
      const item = document.createElement('li');
      const details = document.createElement('details'); details.open = true;
      const summary = document.createElement('summary'); summary.textContent = name;
      details.append(summary, renderNode(child)); item.append(details); list.append(item);
    });
    [...node.files].sort((left, right) => left.name.localeCompare(right.name)).forEach(({name, artifact}) => {
      const item = document.createElement('li');
      const open = button(name, () => location.assign(workspacePath(artifact.id)));
      open.classList.add('tree-file'); open.setAttribute('aria-label', `Open ${artifact.name}`);
      if (artifact.id === artifactId) open.setAttribute('aria-current', 'page');
      item.append(open); list.append(item);
    });
    return list;
  };
  target.append(renderNode(root));
}
function renderGraphMap(edges) {
  const map = byId('graph-map');
  if (!map) return;
  map.replaceChildren();
  const source = document.createElement('div'); source.className = 'graph-node graph-source';
  source.textContent = byId('title')?.textContent || 'Current artifact'; map.append(source);
  if (!edges.length) {
    const empty = document.createElement('p'); empty.className = 'muted';
    empty.textContent = 'No connected artifacts.'; map.append(empty); return;
  }
  const links = document.createElement('div'); links.className = 'graph-links';
  edges.forEach((edge) => {
    const target = edge.target_artifact_name || 'Related artifact';
    const link = button(target, () => location.assign(workspacePath(edge.target_artifact_id)));
    link.classList.add('graph-node', 'graph-target');
    link.setAttribute('aria-label', `${edge.edge_type}: ${target}`);
    const edgeLabel = document.createElement('span'); edgeLabel.className = 'graph-edge-label';
    edgeLabel.textContent = edge.edge_type;
    const item = document.createElement('div'); item.className = 'graph-link';
    item.append(edgeLabel, link); links.append(item);
  });
  map.append(links);
}
function base64(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  }
  return btoa(binary);
}
const webMediaTypes = new Set([
  'text/html', 'application/xhtml+xml', 'text/css', 'application/javascript',
  'text/javascript', 'application/x-javascript',
]);
function isWebArtifact(artifact, version) {
  return webMediaTypes.has(version.media_type) || /\.(html?|css|m?js)$/i.test(artifact.name);
}
function renderMarkdown(markdown) {
  const root = document.createElement('div'); root.className = 'document-content';
  let listTarget = null; let codeTarget = null;
  for (const line of String(markdown || '').split('\n')) {
    if (line.trim().startsWith('```')) {
      if (codeTarget) { root.append(codeTarget); codeTarget = null; }
      else { codeTarget = document.createElement('pre'); codeTarget.append(document.createElement('code')); }
      listTarget = null; continue;
    }
    if (codeTarget) { codeTarget.firstChild.textContent += `${line}\n`; continue; }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const item = document.createElement(`h${heading[1].length}`); item.textContent = heading[2]; root.append(item);
      listTarget = null; continue;
    }
    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      listTarget ||= document.createElement('ul');
      if (!listTarget.parentNode) root.append(listTarget);
      const item = document.createElement('li'); item.textContent = bullet[1]; listTarget.append(item); continue;
    }
    listTarget = null;
    if (!line.trim()) continue;
    const paragraph = document.createElement('p'); paragraph.textContent = line; root.append(paragraph);
  }
  if (codeTarget) root.append(codeTarget);
  return root;
}
function showRead(read) {
  const artifact = read.artifact; const version = read.version;
  byId('title').textContent = artifact.name;
  byId('artifact-path').textContent = artifact.name;
  if (byId('tree-breadcrumb')) byId('tree-breadcrumb').textContent = `In this graph / ${artifact.name}`;
  if (byId('artifact-media')) byId('artifact-media').textContent = version.media_type;
  const details = byId('artifact-details');
  if (details) {
    details.textContent = '';
    const values = [
      ['Artifact', artifact.id], ['Media type', version.media_type], ['Version', version.id],
      ['SHA-256', version.blob_hash], ['Bytes', String(version.byte_size)],
      ['Actor', version.actor], ['Reason', version.reason], ['Created', version.created_at],
    ];
    values.forEach(([term, description]) => {
      const dt = document.createElement('dt'); dt.textContent = term;
      const dd = document.createElement('dd'); dd.textContent = description;
      details.append(dt, dd);
    });
  }
  const editable = Object.hasOwn(read, 'text');
  const readable = byId('readable-content');
  readable.replaceChildren();
  if (!editable) readable.textContent = 'This file opens in its safe preview.';
  else if (version.media_type === 'text/markdown' || /\.md$/i.test(artifact.name)) readable.append(renderMarkdown(read.text));
  else readable.textContent = read.text ?? '';
  byId('reader-kind').textContent = editable ? 'Text' : 'Binary';
  byId('reader-note').textContent = editable ? 'Readable document' : 'Safe preview';
  if (byId('content')) byId('content').value = read.text ?? '';
  if (byId('save')) byId('save').disabled = !editable;
  if (byId('media-type')) byId('media-type').value = version.media_type;
  if (byId('parent-version')) byId('parent-version').value = version.id;
  if (byId('human-content')) {
    byId('human-content').value = read.text ?? '';
    byId('human-content').disabled = !editable;
  }
  if (byId('human-parent-version')) byId('human-parent-version').value = version.id;
  if (byId('human-save')) byId('human-save').disabled = !editable;
  if (byId('binary-note')) byId('binary-note').hidden = editable;
  if (byId('chunks')) list('chunks', read.chunks || [], (chunk) => {
    const li = document.createElement('li'); li.className = 'resource-item';
    li.append(button(`Chunk ${chunk.ordinal + 1}: ${chunk.start_offset}–${chunk.end_offset}`, async () => {
      try {
        status('Reading chunk…');
        const value = await call('artifact_read_chunk', {chunk_id: chunk.id});
        byId('chunk-content').textContent = value.content;
        status(`Read chunk ${chunk.ordinal + 1}; offsets are Unicode code points.`);
      } catch (error) { handleFailure(error); }
    })); return li;
  }, 'No text chunks for this version.');
  const web = isWebArtifact(artifact, version);
  workspaceWeb = web;
  byId('workspace').classList.toggle('web-first', web);
  if (workspaceMode) {
    byId('reader')?.toggleAttribute('hidden', web);
    byId('preview-card')?.toggleAttribute('hidden', !web);
  }
  if (byId('preview-title')) byId('preview-title').textContent = web ? 'Open this site' : 'Safe preview';
  const standalone = byId('standalone-link');
  if (standalone) {
    standalone.href = standalonePath(artifact.id);
    standalone.toggleAttribute('hidden', !web);
  }
  const query = new URLSearchParams({version_id: version.id});
  const preview = byId('preview');
  const previewSource = `${renderOrigin}/render/${encodeURIComponent(artifact.id)}?${query}`;
  if (preview && preview.getAttribute('src') !== previewSource) preview.src = previewSource;
}
function showHumanRead(read) {
  const artifact = read.artifact; const version = read.version;
  const web = isWebArtifact(artifact, version);
  if (byId('human-title')) byId('human-title').textContent = artifact.name;
  document.title = `${artifact.name} — Folio Lattice`;
  byId('human-viewer').hidden = false;
  byId('human-site').hidden = !web;
  byId('human-document').hidden = web;
  if (web) {
    const query = new URLSearchParams({version_id: version.id});
    byId('human-preview').title = artifact.name;
    const previewSource = `${renderOrigin}/render/${encodeURIComponent(artifact.id)}?${query}`;
    if (byId('human-preview').getAttribute('src') !== previewSource) {
      byId('human-preview').src = previewSource;
    }
    return;
  }
  const documentBody = byId('human-document-body');
  documentBody.replaceChildren();
  const markdown = version.media_type === 'text/markdown' || /\.md$/i.test(artifact.name);
  documentBody.classList.toggle('plain-text', !markdown);
  if (!Object.hasOwn(read, 'text')) {
    const message = document.createElement('p');
    message.textContent = 'This file type can be examined in the Inspector.';
    documentBody.append(message);
  } else if (markdown) documentBody.append(renderMarkdown(read.text));
  else documentBody.textContent = read.text ?? '';
}
async function togglePreviewFullscreen() {
  const panel = byId('preview-card');
  try {
    if (document.fullscreenElement === panel) await document.exitFullscreen();
    else if (panel.requestFullscreen) await panel.requestFullscreen();
    else throw new Error('Full-screen preview is not supported in this browser.');
  } catch (error) { failure(error.message); }
}
byId('fullscreen-preview')?.addEventListener('click', togglePreviewFullscreen);
addEventListener('fullscreenchange', () => {
  const active = document.fullscreenElement === byId('preview-card');
  const button = byId('fullscreen-preview');
  if (button) button.textContent = active ? 'Exit full screen' : 'Open full screen';
});
async function loadArtifact(versionId = null) {
  if (!artifactId) {
    await loadLibrary(); return;
  }
  byId('welcome')?.setAttribute('hidden', '');
  status('Loading artifact…');
  const args = {artifact_id: artifactId}; if (versionId) args.version_id = versionId;
  if (humanArtifactMode && !workspaceMode) {
    const read = await call('artifact_read', args);
    showHumanRead(read);
    status(new URLSearchParams(location.search).has('created')
      ? `Created ${read.artifact.name}. Version 1 saved.`
      : `Opened ${read.artifact.name}.`);
    return;
  }
  const graphRequest = call('graph_traverse', {start_artifact_id: artifactId, max_depth: 2, limit: 100});
  const treeRequest = workspaceMode ? call('artifact_list', {limit: 100}) : Promise.resolve([]);
  const [read, versions, graph, tree] = await Promise.all([
    call('artifact_read', args),
    debugMode ? call('artifact_versions', {artifact_id: artifactId, limit: 100}) : Promise.resolve([]),
    graphRequest,
    treeRequest,
  ]);
  byId('workspace').hidden = false; showRead(read);
  if (workspaceMode) {
    const graphIds = new Set([artifactId, ...graph.map((edge) => edge.target_artifact_id)]);
    renderArtifactTree(tree.filter((artifact) => graphIds.has(artifact.id)));
    renderGraphMap(graph);
    setWorkspaceMode(new URLSearchParams(location.search).get('view') === 'graph' ? 'graph' : 'read', false);
  }
  if (debugMode && byId('versions')) list('versions', versions, (version) => {
    const li = document.createElement('li'); li.className = 'history-item';
    const label = `${version.created_at} — ${version.reason}${version.id === read.version.id ? ' (shown)' : ''}`;
    li.append(button(label, () => loadArtifact(version.id).catch(handleFailure)));
    return li;
  }, 'No versions found.');
  if (byId('graph')) {
    list('graph', graph, (edge) => {
      const li = document.createElement('li'); li.className = 'graph-item';
      const edgeType = document.createElement('span'); edgeType.textContent = `${edge.edge_type} → `;
      const targetLabel = edge.target_artifact_name || 'Related artifact';
      li.append(edgeType, button(targetLabel, () => {
        location.assign(workspacePath(edge.target_artifact_id));
      })); return li;
    }, 'No relationships.');
    renderGraphMap(graph);
  }
  if (debugMode) {
    try {
      const grants = await call('artifact_acl', {artifact_id: artifactId});
      byId('sharing').hidden = false;
      list('people-with-access', grants.filter((grant) =>
        grant.status === 'active' && grant.reason !== 'artifact owner'), (grant) => {
        const li = document.createElement('li'); li.className = 'resource-item access-row';
        const role = grant.action === 'read' ? 'Can view' : grant.action === 'write' ? 'Can edit' : 'Can manage';
        const label = document.createElement('span'); label.textContent = `${grant.subject_id} — ${role}`;
        const remove = button('Remove access', async () => {
          try {
            await call('artifact_revoke', {artifact_id: artifactId, grant_id: grant.id,
              reason: 'removed in inspection UI'});
            await loadArtifact(); status(`Removed access for ${grant.subject_id}.`);
          } catch (error) { handleFailure(error); }
        });
        li.append(label, remove); return li;
      }, 'No one else has access.');
    } catch (error) {
      if (error.status === 403 || error.status === 404) byId('sharing').hidden = true;
      else throw error;
    }
  }
  status(new URLSearchParams(location.search).has('created')
    ? `Created ${read.artifact.name}. Version 1 saved.`
    : `Loaded ${read.artifact.name}.`);
}

async function loadLibrary() {
  byId('workspace').hidden = true;
  byId('welcome').hidden = false;
  status('Loading recent artifacts…');
  try {
    const artifacts = await call('artifact_list', {limit: 20});
    const renderArtifact = (artifact, destination = (debugMode ? artifactPath : workspacePath)) => {
      const li = document.createElement('li'); li.className = 'library-item';
      const open = button(artifact.name, () => {
        location.assign(destination(artifact.id));
      });
      if (isWebArtifact(artifact, {media_type: artifact.media_type})) {
        open.setAttribute('aria-label', `Open site ${artifact.name}`);
        const action = document.createElement('span'); action.className = 'library-action';
        action.textContent = 'Open site'; li.append(open, action);
      } else li.append(open);
      const meta = document.createElement('span'); meta.className = 'library-meta';
      meta.textContent = `Updated ${formatDate(artifact.updated_at)}`;
      li.append(meta); return li;
    };
    const renderGraphCard = (artifact) => {
      const li = document.createElement('li'); li.className = 'graph-picker-card';
      const open = document.createElement('button'); open.type = 'button';
      open.className = 'graph-card-action'; open.setAttribute('aria-label', `Open graph ${artifact.name}`);
      open.addEventListener('click', () => location.assign(workspacePath(artifact.id)));
      const top = document.createElement('span'); top.className = 'graph-card-top';
      const icon = document.createElement('span'); icon.className = 'graph-icon'; icon.setAttribute('aria-hidden', 'true');
      icon.textContent = /\.(html?|css|m?js)$/i.test(artifact.name) ? '↗' : '✦';
      const mark = document.createElement('span'); mark.className = 'graph-open-mark';
      mark.setAttribute('aria-hidden', 'true'); mark.textContent = '↗'; top.append(icon, mark);
      const title = document.createElement('h3'); title.textContent = artifact.name;
      const description = document.createElement('p'); description.className = 'graph-description';
      description.textContent = 'A connected workspace for documents, sites, and the context around them.';
      open.append(top, title, description);
      const meta = document.createElement('div'); meta.className = 'graph-meta';
      const count = document.createElement('span'); count.className = 'status-dot';
      count.textContent = `${artifact.graph_edges || 0} linked item${artifact.graph_edges === 1 ? '' : 's'}`;
      const updated = document.createElement('span'); updated.textContent = `Updated ${formatDate(artifact.updated_at)}`;
      meta.append(count, updated); li.append(open, meta); return li;
    };
    const graphs = artifacts.filter((artifact) => artifact.graph_edges > 0);
    list('recent-artifacts', artifacts, renderArtifact, 'No artifacts yet. Create one to start your library.');
    list('graph-artifacts', graphs, renderGraphCard, 'No graphs yet. Create one to start your library.');
    list('artifact-library', artifacts.filter((artifact) => artifact.graph_edges === 0), renderArtifact,
      'No standalone artifacts. Create one to keep a file outside a graph.');
    if (byId('library-count')) byId('library-count').textContent = `${artifacts.length} artifact${artifacts.length === 1 ? '' : 's'}`;
    status('Library ready.');
  } catch (error) { handleFailure(error); }
}

byId('open')?.addEventListener('submit', (event) => {
  event.preventDefault(); const id = byId('artifact-id').value.trim();
  if (id) location.assign(`/inspect/${encodeURIComponent(id)}`);
});
byId('create-file')?.addEventListener('change', () => {
  const file = byId('create-file').files[0]; if (!file) return;
  if (!byId('create-name').value) byId('create-name').value = file.name;
});
byId('create')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const name = byId('create-name').value.trim();
    status('Creating the first immutable version…');
    const file = byId('create-file').files[0];
    const pasted = byId('create-text').value;
    if (!file && !pasted.trim()) throw new Error('Add a file or paste some content first.');
    const bytes = file ? new Uint8Array(await file.arrayBuffer()) : new TextEncoder().encode(pasted);
    const mediaOverride = byId('create-media')?.value.trim();
    const created = await call('artifact_create', {
      name, media_type: mediaOverride || file?.type || null,
      reason: byId('create-reason')?.value || 'library create', content_base64: base64(bytes),
      source_context: {interface: 'inspection-ui'},
    });
    location.assign(`${artifactPath(created.artifact.id)}?created=1`);
  } catch (error) { handleFailure(error); }
});
async function discover(tool, field, inputId) {
  try {
    status(tool === 'artifact_search' ? 'Searching indexed content…' : 'Running literal grep…');
    const results = await call(tool, {[field]: byId(inputId).value, limit: 20});
    list('results', results, (result) => {
      const li = document.createElement('li'); li.className = 'result-item';
      const excerpt = result.snippet || result.content || '';
      const resultLabel = debugMode && result.artifact_name
        ? `${result.artifact_id} — ${result.artifact_name}`
        : result.artifact_name || 'Open artifact';
      li.append(button(resultLabel, () => location.assign(artifactPath(result.artifact_id))));
      const span = document.createElement('span'); span.textContent = ` — ${excerpt.slice(0, 240)}`;
      li.append(span); return li;
    }, 'No results.'); status(`${results.length} result${results.length === 1 ? '' : 's'}.`);
  } catch (error) { handleFailure(error); }
}
byId('search')?.addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_search', 'query', 'search-query');
});
byId('grep')?.addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_grep', 'pattern', 'grep-pattern');
});
byId('new-entry')?.addEventListener('click', () => {
  const panel = byId('new');
  if (panel) {
    panel.open = true;
    setTimeout(() => panel.querySelector('input')?.focus(), 0);
  }
});
byId('workspace-search')?.addEventListener('submit', (event) => {
  event.preventDefault();
  const results = byId('workspace-search-results');
  if (results) results.hidden = false;
  discover('artifact_search', 'query', 'workspace-search-query');
});
byId('edit-entry')?.addEventListener('click', (event) => {
  event.preventDefault();
  openWorkspacePanel('update', 'edit-entry');
});
byId('share-entry')?.addEventListener('click', (event) => {
  event.preventDefault();
  openWorkspacePanel('workspace-share', 'share-entry');
});
byId('edit')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Saving a new immutable version…');
    const written = await call('artifact_write', {
      artifact_id: artifactId, parent_version_id: byId('parent-version').value,
      media_type: byId('media-type').value, reason: byId('reason').value,
      content_base64: base64(new TextEncoder().encode(byId('content').value)),
      source_context: {interface: 'inspection-ui'},
    });
    history.replaceState(null, '', location.pathname);
    await loadArtifact(); status(`Saved new version ${written.id}.`);
  } catch (error) {
    if (error.status === 409) failure('A newer version already exists. Refresh before saving again; your edit remains here.');
    else handleFailure(error);
  }
});
byId('human-edit')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Saving a new version…');
    const written = await call('artifact_write', {
      artifact_id: artifactId, parent_version_id: byId('human-parent-version').value,
      media_type: null, reason: 'library update',
      content_base64: base64(new TextEncoder().encode(byId('human-content').value)),
      source_context: {interface: 'library-ui'},
    });
    history.replaceState(null, '', location.pathname);
    closeWorkspacePanel('update');
    await loadArtifact(); status('Saved a new version.');
  } catch (error) {
    if (error.status === 409) failure('A newer version already exists. Refresh before saving again; your edit remains here.');
    else handleFailure(error);
  }
});
byId('link')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Creating relationship…');
    await call('graph_link', {source_artifact_id: artifactId,
      target_artifact_id: byId('target-id').value, edge_type: byId('edge-type').value,
      metadata: {}});
    await loadArtifact(); status('Relationship created. Traversal follows outgoing edges only.');
  } catch (error) { handleFailure(error); }
});
byId('share')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const subject = byId('share-recipient').value.trim();
    await call('artifact_share', {artifact_id: artifactId, subject_actor_id: subject,
      action: byId('share-role').value, reason: 'shared in inspection UI'});
    byId('share-recipient').value = '';
    closeWorkspacePanel('workspace-share');
    await loadArtifact(); status(`Shared with ${subject}.`);
  } catch (error) { handleFailure(error); }
});
addEventListener('message', async (event) => {
  const frame = byId('human-preview') || byId('preview');
  if (event.origin !== 'null' || event.source !== frame.contentWindow) return;
  const value = event.data;
  if (!value || typeof value !== 'object' || value.type !== 'folio.mcp.request' ||
      typeof value.id !== 'string' || typeof value.attachment !== 'string' ||
      typeof value.tool !== 'string' || !value.arguments || typeof value.arguments !== 'object') return;
  const source = event.source;
  try {
    if (byId('bridge-status')) byId('bridge-status').textContent = `Attached artifact requested ${value.tool}…`;
    const response = await call('', {request_id: value.id, artifact_id: artifactId,
      attachment: value.attachment, tool: value.tool, arguments: value.arguments}, '/api/bridge');
    source.postMessage({type: 'folio.mcp.response', id: value.id, ok: true, result: response.result}, '*');
    if (byId('bridge-status')) byId('bridge-status').textContent = `Allowed attached tool ${value.tool}.`;
  } catch (error) {
    const message = error.status === 401
      ? (wasAuthenticated ? 'Your session expired. Sign in again.' : 'Sign-in required. Sign in to continue.')
      : error.status === 403 ? 'This document is not available to you.' : error.message;
    if (error.status === 401) handleFailure(error);
    source.postMessage({type: 'folio.mcp.response', id: value.id, ok: false, error: message}, '*');
    if (byId('bridge-status')) byId('bridge-status').textContent = 'Attached request did not run.';
  }
});

loadArtifact().catch(handleFailure);
""".strip()


def ui_html(
    render_origin: str,
    *,
    auth_state: str = "local",
    organization: str | None = None,
    actor: str | None = None,
    debug: bool = False,
    human: bool = False,
    workspace: bool = False,
    standalone: bool = False,
) -> str:
    origin = escape(render_origin, quote=True)
    state = escape(auth_state, quote=True)
    if standalone:
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Artifact</title><link rel="stylesheet" href="/ui.css"></head>
<body class="human-route standalone-route" data-render-origin="{origin}" data-auth-state="{state}">
<main id="main" class="page" aria-busy="false">
  <div class="live-region sr-only"><p id="status" role="status" aria-live="polite"></p><p id="error" class="error" role="alert" aria-live="assertive" tabindex="-1" hidden></p></div>
  <section id="auth-recovery" class="auth-recovery" aria-labelledby="auth-recovery-title" hidden><h2 id="auth-recovery-title">Authentication required</h2><p id="auth-recovery-message"></p><a id="auth-action" href="/sign-in?return_to=%2F" hidden>Sign in</a></section>
  <article id="human-viewer" class="human-viewer" hidden>
    <section id="human-site" class="human-viewer-site" aria-label="Website" hidden><iframe id="human-preview" title="Artifact site" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></section>
    <article id="human-document" class="human-document" aria-label="Document" hidden><div id="human-document-body" class="human-document-body"></div></article>
  </article>
</main><script src="/ui.js"></script></body></html>"""
    if human and not debug and not workspace:
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Artifact — Folio Lattice</title><link rel="stylesheet" href="/ui.css"></head>
<body class="human-route" data-render-origin="{origin}" data-auth-state="{state}">
<a class="skip-link" href="#main">Skip to content</a>
<div class="app-shell">
<header class="topbar"><a id="back-to-library" class="button-secondary" href="/">← Library</a><h1 id="human-title" class="human-viewer-title">Opening artifact…</h1></header>
<main id="main" class="page" aria-busy="false">
  <div class="live-region"><p id="status" role="status" aria-live="polite"></p><p id="error" class="error" role="alert" aria-live="assertive" tabindex="-1" hidden></p></div>
  <section id="auth-recovery" class="auth-recovery" aria-labelledby="auth-recovery-title" hidden><h2 id="auth-recovery-title">Authentication required</h2><p id="auth-recovery-message"></p><a id="auth-action" href="/sign-in?return_to=%2F" hidden>Sign in</a></section>
  <article id="human-viewer" class="human-viewer" hidden>
    <section id="human-site" class="human-viewer-site" aria-label="Website" hidden><iframe id="human-preview" title="Artifact site" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></section>
    <article id="human-document" class="human-document" aria-label="Document" hidden><div id="human-document-body" class="human-document-body"></div></article>
  </article>
</main></div><script src="/ui.js"></script></body></html>"""
    organization_value = escape(organization or "Unavailable", quote=True)
    actor_value = escape(actor or "Unavailable", quote=True)
    auth_context_hidden = " hidden" if not organization and not actor else ""
    local_warning = (
        '<div id="local-warning" class="security-banner" role="note" aria-label="Local security notice"><div><strong>Unauthenticated local development</strong><span>Keep this service on a trusted network. Do not expose it to untrusted users.</span></div></div>'
        if auth_state == "local"
        else ""
    )
    account_surface = (
        f'<details class="account-details"><summary>Workspace status</summary>{local_warning}'
        f'<p id="auth-context" class="auth-context" aria-label="Active account"{auth_context_hidden}>'
        f'<span id="organization-context">Organization: {organization_value}</span>'
        f'<span id="actor-context">Actor: {actor_value}</span></p></details>'
        if debug
        else ""
    )
    debug_open = (
        '<details class="surface create-card"><summary><span>Open by identifier</span></summary>'
        '<form id="open" class="stacked-form"><label for="artifact-id">Artifact identifier'
        '<input id="artifact-id" required maxlength="255" placeholder="art_…"></label>'
        '<button class="button-secondary" type="submit">Open</button></form></details>'
        if debug
        else ""
    )
    debug_workspace_nav = (
        '<a href="#graph-context">Relationships</a><a href="#details">Details</a>' if debug else ""
    )
    debug_details = (
        '<section id="details" class="surface card-pad" aria-labelledby="details-title">'
        '<div class="card-heading"><div><p class="eyebrow">DETAILS</p><h2 id="details-title">'
        'Artifact details</h2></div><span class="state-pill">Current version</span></div>'
        '<dl id="artifact-details" class="metadata-grid"></dl></section>'
        if debug
        else ""
    )
    debug_access = (
        '<section id="sharing" class="surface access-card" aria-labelledby="sharing-title">'
        '<div class="card-heading"><div><p class="eyebrow">ACCESS</p><h2 id="sharing-title">'
        'People with access</h2></div><span id="privacy-status" class="state-pill">Private</span></div>'
        '<p class="privacy-line">Only people you add can open this artifact.</p>'
        '<ul id="people-with-access" class="access-list"><li class="muted">Loading access…</li></ul>'
        '<form id="share" class="link-form"><label for="share-recipient">Person identifier'
        '<input id="share-recipient" required maxlength="255" placeholder="person-id"></label>'
        '<label for="share-role">Access<select id="share-role"><option value="read">Can view</option>'
        '<option value="write">Can edit</option></select></label><button type="submit">Share</button>'
        "</form></section>"
        if debug
        else ""
    )
    debug_graph = (
        '<section id="graph-context" class="surface graph-card" aria-labelledby="graph-title">'
        '<div class="card-heading"><div><p class="eyebrow">GRAPH</p><h2 id="graph-title">'
        'Relationships</h2></div></div><div id="graph-map" class="graph-map" role="group" aria-label="Relationship map"></div>'
        '<ul id="graph" class="graph-list"></ul>'
        '<form id="link" class="link-form"><div class="row"><label>Target artifact identifier'
        '<input id="target-id" required maxlength="255" placeholder="art_…"></label>'
        '<label>Relationship type<input id="edge-type" value="references" required maxlength="100"></label>'
        '</div><button type="submit">Create relationship</button></form></section>'
        if debug
        else ""
    )
    human_graph = (
        '<section id="graph-context" class="surface graph-card" aria-labelledby="graph-title"'
        + (" hidden" if workspace else "")
        + ">"
        '<div class="card-heading"><div><p class="eyebrow">GRAPH</p><h2 id="graph-title">Related artifacts</h2></div></div>'
        '<p>Move through connected documents by name.</p><div id="graph-map" class="graph-map" role="group" aria-label="Relationship map"></div>'
        '<ul id="graph" class="graph-list"></ul></section>'
        if human and not debug
        else ""
    )
    debug_editor = (
        '<section id="editor" class="surface editor-card" aria-labelledby="edit-title"><div class="card-heading"><div><p class="eyebrow">CURRENT VERSION</p><h2 id="edit-title">Content</h2></div></div><p id="binary-note" class="binary-note" hidden>Binary content is metadata-only and cannot be edited as text.</p><form id="edit"><input id="parent-version" type="hidden"><label for="content">Content</label><textarea id="content" spellcheck="false"></textarea><div class="row"><label for="media-type">Media type<input id="media-type" required maxlength="255"></label><label for="reason">Reason<input id="reason" value="inspection UI edit" required maxlength="2000"></label></div><p class="field-help">Saving creates a new version.</p><button id="save" type="submit">Save new version</button></form></section>'
        '<section id="history" class="utility-grid" aria-label="Artifact history"><div class="surface utility-card"><p class="eyebrow">CONTENT</p><h2>Chunks</h2><ul id="chunks" class="resource-list"></ul><pre id="chunk-content">Choose a chunk.</pre></div><div class="surface utility-card"><p class="eyebrow">HISTORY</p><h2>Versions</h2><ul id="versions" class="resource-list"></ul></div></section>'
        if debug
        else ""
    )
    human_editor = (
        '<dialog id="update" class="surface editor-card" aria-labelledby="update-title">'
        '<div class="panel-heading"><div><p class="eyebrow">EDIT</p><h2 id="update-title">Edit artifact</h2></div>'
        '<button id="close-edit" class="panel-close" type="button" aria-label="Close editor">×</button></div>'
        '<form id="human-edit" class="stacked-form"><input id="human-parent-version" type="hidden">'
        '<label for="human-content">Content<textarea id="human-content" spellcheck="true"></textarea></label>'
        '<button id="human-save" type="submit">Save new version</button></form></dialog>'
        if human and not debug
        else ""
    )
    human_share = (
        '<dialog id="workspace-share" class="surface access-card" aria-labelledby="workspace-share-title">'
        '<div class="card-heading"><div><p class="eyebrow">SHARE</p><h2 id="workspace-share-title">Share this artifact</h2></div>'
        '<button id="close-share" class="panel-close" type="button" aria-label="Close sharing">×</button></div>'
        '<p class="privacy-line"><span class="state-pill">Private by default</span> Only people you add can open this artifact.</p>'
        '<form id="share" class="link-form"><label for="share-recipient">Person identifier'
        '<input id="share-recipient" required maxlength="255" placeholder="person-id"></label>'
        '<label for="share-role">Access<select id="share-role"><option value="read">Can view</option>'
        '<option value="write">Can edit</option></select></label><button type="submit">Share</button></form></dialog>'
        if workspace
        else ""
    )
    debug_create_fields = (
        '<details class="advanced-field"><summary>Advanced fields</summary><div class="stacked-form">'
        '<label for="create-media">Media type<input id="create-media" maxlength="255" placeholder="Inferred from name"></label>'
        '<label for="create-reason">Reason<input id="create-reason" value="inspection UI create" required maxlength="2000"></label>'
        "</div></details>"
        if debug
        else ""
    )
    workspace_nav = (
        '<a id="back-to-library" class="button-secondary" href="/">' + ("← Graphs" if workspace else "Back to library") + '</a>'
        + (
            '<div class="view-switch" role="tablist" aria-label="Workspace view">'
            '<button id="read-mode" type="button" role="tab" aria-selected="true" aria-controls="reader" tabindex="0" class="is-active">Read</button>'
            '<button id="graph-mode" type="button" role="tab" aria-selected="false" aria-controls="graph-context" tabindex="-1">Graph</button></div>'
            '<div class="workspace-actions"><button id="share-entry" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="workspace-share">Share</button>'
            '<button id="edit-entry" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="update">Edit</button></div>'
            '<a id="standalone-link" class="workspace-option" href="#" target="_blank" rel="noreferrer" hidden>Open site ↗</a>'
            '<form id="workspace-search" class="workspace-search" role="search"><label class="sr-only" for="workspace-search-query">Search this graph</label>'
            '<input id="workspace-search-query" type="search" maxlength="500" placeholder="Search graph"><button type="submit" aria-label="Search graph">⌕</button></form>'
            if workspace
            else '<a href="#reader">Read</a><a href="#preview-card">Preview</a><a href="#graph-context">Graph</a>'
        )
        + (
            '<a href="#editor">Edit</a><a href="#history">History</a>' + debug_workspace_nav
            if debug
            else ""
        )
    )
    workspace_tree = (
        '<aside id="artifact-tree-panel" class="tree-panel" aria-label="Artifact tree">'
        '<div class="tree-heading"><h2>Files</h2><p id="tree-breadcrumb" class="tree-breadcrumb">In this graph</p></div>'
        '<nav id="artifact-tree" class="artifact-tree" aria-label="Files and folders">'
        '<p class="muted">Loading artifacts…</p></nav></aside>'
        if workspace
        else ""
    )
    root_library = (
        '<section id="graphs" class="surface graph-library" aria-labelledby="graphs-title">'
        '<div class="section-heading"><div><p class="eyebrow">YOUR KNOWLEDGE SPACE</p><span class="sr-only">GRAPH PICKER</span><h2 id="graphs-title">Your graphs</h2><span class="sr-only">Choose a graph</span></div>'
        '<a class="primary-button" href="#new" id="new-entry">＋ New artifact</a></div><ul id="graph-artifacts" class="library-list graph-picker-list">'
        '<li class="muted">Loading graphs…</li></ul></section>'
        '<section id="artifacts" class="surface artifact-library" aria-labelledby="artifacts-title">'
        '<div class="section-heading"><div><p class="eyebrow">FILES WITHOUT LINKS</p><h2 id="artifacts-title">Your artifacts</h2></div></div>'
        '<ul id="artifact-library" class="library-list"><li class="muted">Loading artifacts…</li></ul></section>'
        if not debug
        else '<section class="surface library-card" aria-labelledby="recent-title"><div class="section-heading"><div><p class="eyebrow">LIBRARY</p><h2 id="recent-title">Recent artifacts</h2></div><p id="library-count">Loading recent artifacts…</p></div><ul id="recent-artifacts" class="library-list"><li class="muted">Loading recent artifacts…</li></ul></section>'
    )
    root_library_actions = (
        '<details id="new" class="surface create-card"><summary><span>New graph or artifact</span></summary><form id="create" class="stacked-form">'
        '<label for="create-name">Name<input id="create-name" required maxlength="255" placeholder="e.g. notes/project.md"></label>'
        '<label for="create-file">Upload a file<input id="create-file" type="file"></label>'
        '<label for="create-text">Or paste content<textarea id="create-text" placeholder="Start writing…"></textarea></label>'
        f'{debug_create_fields}<button type="submit">Create artifact</button></form></details>'
        + (
            '<details id="find" class="surface create-card"><summary><span>Search library</span></summary><div class="stacked-form"><div class="search-grid">'
            '<form id="search" class="search-form"><label for="search-query">Search documents and files<input id="search-query" required maxlength="500" placeholder="Phrase or keyword"></label><button type="submit">Search</button></form>'
            '<form id="grep" class="search-form"><label for="grep-pattern">Exact text<input id="grep-pattern" required maxlength="500" placeholder="Exact text"></label><button type="submit">Find exact text</button></form>'
            '</div><div class="results-wrap"><p class="results-label">Results</p><ul id="results" class="results-list"><li class="muted">No search run yet.</li></ul></div></div></details>'
            if debug
            else ""
        )
    )
    primary_nav = (
        '<a href="/"><span class="nav-index" aria-hidden="true">01</span>Library</a>'
        '<a href="/#graphs"><span class="nav-index" aria-hidden="true">02</span>Graphs</a>'
        + (
            '<a href="/#find"><span class="nav-index" aria-hidden="true">03</span>Search</a>'
            '<a href="/#new"><span class="nav-index" aria-hidden="true">04</span>New</a>'
            if debug
            else '<a href="/#shared">Shared with me</a><span class="avatar" aria-label="Brandon’s account">BS</span>'
        )
    )
    preview_debug = (
        '<button id="fullscreen-preview" class="button-secondary" type="button">Open full screen</button>'
        if debug
        else ""
    )
    preview_details = (
        "<details><summary>Preview capabilities</summary><p>Network and host access are blocked.</p>"
        '<div class="capability-list" aria-label="Preview capabilities"><span class="capability">Read</span>'
        '<span class="capability">Indexed search</span><span class="capability">Outgoing traversal</span></div>'
        '<p id="bridge-status" role="status" aria-live="polite">No attached tool call yet.</p></details>'
        if debug
        else '<p class="field-help">Safe preview runs in an isolated frame with host and network access blocked.</p>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Folio Lattice</title><link rel="stylesheet" href="/ui.css"></head>
<body class="{'debug-route' if debug else 'workspace-route' if workspace else ''}" data-render-origin="{origin}" data-auth-state="{state}">
<a class="skip-link" href="#main">Skip to content</a>
<div class="app-shell">
<header class="topbar">
  <div class="brand-lockup"><span class="brand-mark" aria-hidden="true">F</span><div><div class="brand-name">Folio Lattice</div><div class="brand-subtitle">Knowledge workspace</div></div></div>
  <nav class="primary-nav" aria-label="Primary">{primary_nav}</nav>
</header>
{account_surface}
<main id="main" class="page" aria-busy="false">
  <div class="live-region"><p id="status" role="status" aria-live="polite"></p><p id="error" class="error" role="alert" aria-live="assertive" tabindex="-1" hidden></p></div>
  <section id="auth-recovery" class="auth-recovery" aria-labelledby="auth-recovery-title" hidden><h2 id="auth-recovery-title">Authentication required</h2><p id="auth-recovery-message"></p><a id="auth-action" href="/sign-in?return_to=%2F" hidden>Sign in</a></section>
  <section id="welcome" class="welcome-panel" aria-labelledby="welcome-title">
    <p class="eyebrow">LIBRARY</p>
    <h1 id="welcome-title">Graphs</h1>
    <p class="lede">Everything you and your agents are building, arranged around the work—not the machinery underneath.</p>
    <div class="welcome-grid">
      {root_library}
      <div class="library-actions">{root_library_actions}{debug_open}</div>
      {'' if debug else '<section id="shared" class="shared-empty" aria-labelledby="shared-title"><div class="section-heading"><div><p class="eyebrow">SHARED WITH YOU</p><h2 id="shared-title">Shared with me</h2></div></div><p class="muted">Nothing shared with you yet.</p></section>'}
    </div>
  </section>
  <article id="workspace" class="workspace{" has-tree" if workspace else ""}" hidden>
    <header class="workspace-heading"><div><div class="breadcrumb"><a href="/">Library</a><span aria-hidden="true">/</span><span>artifacts</span><span aria-hidden="true">/</span><span id="artifact-path">Artifact</span></div><div class="artifact-title-row"><span class="artifact-icon" aria-hidden="true">▤</span><div><p class="eyebrow">ARTIFACT</p><h1 id="title">Artifact</h1>{'<div class="title-metadata"><span id="artifact-media">Loading media type…</span><span class="dot" aria-hidden="true"></span><span>Current version</span></div>' if debug else ""}</div></div></div><nav class="workspace-nav" aria-label="Artifact sections">{workspace_nav}</nav></header>
    {('<div id="workspace-search-results" class="workspace-search-results" hidden><ul id="results" class="results-list"><li class="muted">Search this graph.</li></ul></div>' if workspace else '')}
    <div class="workspace-layout">{workspace_tree}<div class="primary-column">
      <section id="reader" class="surface reader-card" aria-labelledby="reader-title"><div class="card-heading"><div><p class="eyebrow">READ</p><h2 id="reader-title">Readable document</h2></div><span id="reader-kind" class="state-pill">Text</span></div><p id="reader-note" class="field-help">Loading readable content…</p><div id="readable-content" class="document-slot">Loading content…</div></section>
      <section id="preview-card" class="surface preview-card" aria-labelledby="preview-title"><div class="card-heading"><div><p class="eyebrow">PREVIEW</p><h2 id="preview-title">Artifact preview</h2></div><div class="preview-actions">{preview_debug}</div></div><div class="preview-frame"><iframe id="preview" title="Sandboxed artifact preview" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></div>{preview_details}</section>
      {debug_editor}
      {human_editor}
      {human_share}
      {debug_details}
    </div><aside class="secondary-column">
      {debug_access}
      {debug_graph}
      {human_graph}
    </aside></div>
  </article>
</main></div><script src="/ui.js"></script></body></html>"""


def control_headers(render_origin: str) -> dict[str, str]:
    return {
        **CONTROL_HEADERS,
        "Content-Security-Policy": "; ".join(
            [
                "default-src 'none'",
                "base-uri 'none'",
                "connect-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                f"frame-src {render_origin}",
                "img-src 'none'",
                "object-src 'none'",
                "script-src 'self'",
                "style-src 'self'",
            ]
        ),
    }


async def _body(receive: Receive, maximum: int) -> bytes:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise PublicMcpError("request disconnected")
        body.extend(message.get("body", b""))
        if len(body) > maximum:
            raise OverflowError
        more = message.get("more_body", False)
    return bytes(body)


def _error_status(error: Exception) -> int:
    message = str(error)
    if isinstance(error, BridgeRequestError) and error.reason == "capability_not_attached":
        return 403
    if message.endswith("not found"):
        return 404
    if "parent version mismatch" in message:
        return 409
    if message.endswith("timed out"):
        return 504
    if "unavailable" in message or "result exceeds" in message:
        return 502
    return 400


class InspectionApp:
    """Static core-loop UI over one bounded public-MCP adapter."""

    def __init__(
        self,
        caller: ToolCaller,
        *,
        control_origin: str,
        render_origin: str,
        max_request_bytes: int,
        bridge: AttachedMcpBridge | None = None,
        auth_state: str = "local",
        organization: str | None = None,
        actor: str | None = None,
    ):
        self.caller = caller
        self.control_origin = control_origin
        self.render_origin = render_origin
        self.max_request_bytes = max_request_bytes
        self.bridge = bridge or AttachedMcpBridge(caller)
        self.auth_state = auth_state
        self.organization = organization
        self.actor = actor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = scope["method"]
        path = scope["path"]
        headers = control_headers(self.render_origin)
        if method == "GET" and (
            path == "/"
            or path.startswith("/inspect/")
            or path.startswith("/artifacts/")
            or path.startswith("/standalone/")
            or path.startswith("/workspace/")
        ):
            principal = get_request_principal()
            await HTMLResponse(
                ui_html(
                    self.render_origin,
                    auth_state="authenticated" if principal is not None else self.auth_state,
                    organization=(
                        principal.tenant_id if principal is not None else self.organization
                    ),
                    actor=principal.actor_id if principal is not None else self.actor,
                    debug=path.startswith("/inspect/"),
                    human=path.startswith("/artifacts/")
                    or path.startswith("/standalone/")
                    or path.startswith("/workspace/"),
                    workspace=path.startswith("/workspace/"),
                    standalone=path.startswith("/standalone/"),
                ),
                headers=headers,
            )(scope, receive, send)
            return
        if method == "GET" and path == "/ui.css":
            await Response(UI_CSS, media_type="text/css", headers=headers)(scope, receive, send)
            return
        if method == "GET" and path == "/ui.js":
            await Response(UI_JS, media_type="application/javascript", headers=headers)(
                scope, receive, send
            )
            return
        if method == "POST" and path in {"/api/mcp", "/api/bridge"}:
            await self._api(scope, receive, send, bridge=path == "/api/bridge")
            return
        await JSONResponse({"error": "not found"}, status_code=404, headers=CONTROL_HEADERS)(
            scope, receive, send
        )

    async def _api(self, scope: Scope, receive: Receive, send: Send, *, bridge: bool) -> None:
        headers = {key.lower(): value for key, value in scope["headers"]}
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin != self.control_origin:
            if bridge:
                self.bridge.audit_rejection("origin_mismatch")
            await JSONResponse(
                {"error": "request origin is not permitted"},
                status_code=403,
                headers=CONTROL_HEADERS,
            )(scope, receive, send)
            return
        content_type = headers.get(b"content-type", b"").decode("latin-1")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            if bridge:
                self.bridge.audit_rejection("invalid_content_type")
            await JSONResponse(
                {"error": "Content-Type must be application/json"},
                status_code=400,
                headers=CONTROL_HEADERS,
            )(scope, receive, send)
            return
        try:
            maximum = (
                min(self.max_request_bytes, MAX_BRIDGE_BODY_BYTES)
                if bridge
                else self.max_request_bytes
            )
            raw = await _body(receive, maximum)
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BridgeRequestError("request body must be valid JSON") from exc
            if bridge:
                request = validate_bridge_request(payload)
                result = await self.bridge.call(request)
                response = {"request_id": request["request_id"], "result": result}
            else:
                if not isinstance(payload, dict) or set(payload) != {"tool", "arguments"}:
                    raise PublicMcpError("MCP request has invalid fields")
                tool = payload["tool"]
                arguments = payload["arguments"]
                if not isinstance(tool, str) or tool not in PUBLIC_TOOLS:
                    raise PublicMcpError("tool is not part of the Folio MCP contract")
                if not isinstance(arguments, dict):
                    raise PublicMcpError("arguments must be a JSON object")
                response = await self.caller.call(tool, arguments)
            await JSONResponse(response, headers=CONTROL_HEADERS)(scope, receive, send)
        except OverflowError:
            if bridge:
                self.bridge.audit_rejection("oversized_body")
            await JSONResponse(
                {"error": "request body too large"}, status_code=413, headers=CONTROL_HEADERS
            )(scope, receive, send)
        except (BridgeRequestError, PublicMcpError) as exc:
            if (
                bridge
                and isinstance(exc, BridgeRequestError)
                and exc.reason != "capability_not_attached"
            ):
                self.bridge.audit_rejection(exc.reason)
            await JSONResponse(
                {"error": str(exc)}, status_code=_error_status(exc), headers=CONTROL_HEADERS
            )(scope, receive, send)
        except Exception:
            await JSONResponse(
                {"error": "request failed safely"}, status_code=502, headers=CONTROL_HEADERS
            )(scope, receive, send)
