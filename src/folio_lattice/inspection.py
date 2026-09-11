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
""".strip()

UI_JS = r"""
const byId = (id) => document.getElementById(id);
const parts = location.pathname.split('/').filter(Boolean);
const debugMode = parts[0] === 'inspect';
const humanArtifactMode = parts[0] === 'artifacts';
const artifactId = debugMode || humanArtifactMode ? decodeURIComponent(parts[1] || '') : '';
const renderOrigin = document.body.dataset.renderOrigin;
let wasAuthenticated = document.body.dataset.authState === 'authenticated';
let activeRequests = 0;

function artifactPath(id) {
  return `/${debugMode ? 'inspect' : 'artifacts'}/${encodeURIComponent(id)}`;
}

function safeReturnPath() {
  const path = location.pathname;
  return path === '/' || /^\/(?:inspect|artifacts)\/[^/]+$/.test(path) ? path : '/';
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
  byId('preview')?.removeAttribute('src');
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
  const target = byId(id); target.replaceChildren();
  if (!items.length) {
    const item = document.createElement('li'); item.className = 'muted';
    item.textContent = empty; target.append(item); return;
  }
  items.forEach((value) => target.append(render(value)));
}
function button(label, action) {
  const value = document.createElement('button'); value.type = 'button';
  value.className = 'inline-action';
  value.textContent = label; value.addEventListener('click', action); return value;
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
        status(`Read chunk ${chunk.ordinal + 1}.`);
      } catch (error) { handleFailure(error); }
    })); return li;
  }, 'No chunks.');
  const web = isWebArtifact(artifact, version);
  byId('workspace').classList.toggle('web-first', web);
  if (byId('preview-title')) byId('preview-title').textContent = web ? 'Open this site' : 'Safe preview';
  const query = new URLSearchParams({version_id: version.id});
  byId('preview').src = `${renderOrigin}/render/${encodeURIComponent(artifact.id)}?${query}`;
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
  byId('welcome').hidden = true;
  status('Loading artifact…');
  const args = {artifact_id: artifactId}; if (versionId) args.version_id = versionId;
  const graphRequest = call('graph_traverse', {start_artifact_id: artifactId, max_depth: 2, limit: 100});
  const [read, versions, graph] = await Promise.all([
    call('artifact_read', args),
    debugMode ? call('artifact_versions', {artifact_id: artifactId, limit: 100}) : Promise.resolve([]),
    graphRequest,
  ]);
  byId('workspace').hidden = false; showRead(read);
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
        location.assign(artifactPath(edge.target_artifact_id));
      })); return li;
    }, 'No relationships.');
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
    const renderArtifact = (artifact) => {
      const li = document.createElement('li'); li.className = 'library-item';
      const open = button(artifact.name, () => {
        location.assign(artifactPath(artifact.id));
      });
      const meta = document.createElement('span'); meta.className = 'library-meta';
      meta.textContent = `Updated ${artifact.updated_at}`;
      li.append(open, meta); return li;
    };
    list('recent-artifacts', artifacts, renderArtifact, 'No artifacts yet. Create one to start your library.');
    list('graph-artifacts', artifacts.filter((artifact) => artifact.graph_edges > 0), renderArtifact, 'No connected artifacts yet.');
    byId('library-count').textContent = `${artifacts.length} recent artifact${artifacts.length === 1 ? '' : 's'}`;
    status('Library ready.');
  } catch (error) { handleFailure(error); }
}

byId('open')?.addEventListener('submit', (event) => {
  event.preventDefault(); const id = byId('artifact-id').value.trim();
  if (id) location.assign(`/inspect/${encodeURIComponent(id)}`);
});
byId('create-file').addEventListener('change', () => {
  const file = byId('create-file').files[0]; if (!file) return;
  if (!byId('create-name').value) byId('create-name').value = file.name;
});
byId('create').addEventListener('submit', async (event) => {
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
byId('search').addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_search', 'query', 'search-query');
});
byId('grep').addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_grep', 'pattern', 'grep-pattern');
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
    await loadArtifact(); status('Relationship created.');
  } catch (error) { handleFailure(error); }
});
byId('share')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const subject = byId('share-recipient').value.trim();
    await call('artifact_share', {artifact_id: artifactId, subject_actor_id: subject,
      action: byId('share-role').value, reason: 'shared in inspection UI'});
    byId('share-recipient').value = '';
    await loadArtifact(); status(`Shared with ${subject}.`);
  } catch (error) { handleFailure(error); }
});
addEventListener('message', async (event) => {
  const frame = byId('preview');
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
) -> str:
    origin = escape(render_origin, quote=True)
    state = escape(auth_state, quote=True)
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
        'Relationships</h2></div></div><ul id="graph" class="graph-list"></ul>'
        '<form id="link" class="link-form"><div class="row"><label>Target artifact identifier'
        '<input id="target-id" required maxlength="255" placeholder="art_…"></label>'
        '<label>Relationship type<input id="edge-type" value="references" required maxlength="100"></label>'
        '</div><button type="submit">Create relationship</button></form></section>'
        if debug
        else ""
    )
    human_graph = (
        '<section id="graph-context" class="surface graph-card" aria-labelledby="graph-title">'
        '<div class="card-heading"><div><p class="eyebrow">GRAPH</p><h2 id="graph-title">Related artifacts</h2></div></div>'
        '<p>Move through connected documents by name.</p><ul id="graph" class="graph-list"></ul></section>'
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
        '<details id="update" class="surface editor-card card-pad"><summary>Update document</summary>'
        '<form id="human-edit" class="stacked-form"><input id="human-parent-version" type="hidden">'
        '<label for="human-content">Content<textarea id="human-content" spellcheck="true"></textarea></label>'
        '<button id="human-save" type="submit">Save new version</button></form></details>'
        if human and not debug
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
        '<a id="back-to-library" class="button-secondary" href="/">Back to library</a>'
        '<a href="#reader">Read</a><a href="#preview-card">Preview</a><a href="#graph-context">Graph</a>'
        + (
            '<a href="#editor">Edit</a><a href="#history">History</a>' + debug_workspace_nav
            if debug
            else ""
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
<body data-render-origin="{origin}" data-auth-state="{state}">
<a class="skip-link" href="#main">Skip to content</a>
<div class="app-shell">
<header class="topbar">
  <div class="brand-lockup"><span class="brand-mark" aria-hidden="true">F</span><div><div class="brand-name">Folio Lattice</div><div class="brand-subtitle">Knowledge workspace</div></div></div>
  <nav class="primary-nav" aria-label="Primary"><a class="is-active" href="/"><span class="nav-index" aria-hidden="true">01</span>Library</a><a href="/#graphs"><span class="nav-index" aria-hidden="true">02</span>Graphs</a><a href="/#find"><span class="nav-index" aria-hidden="true">03</span>Search</a><a href="/#new"><span class="nav-index" aria-hidden="true">04</span>New</a></nav>
</header>
{account_surface}
<main id="main" class="page" aria-busy="false">
  <div class="live-region"><p id="status" role="status" aria-live="polite"></p><p id="error" class="error" role="alert" aria-live="assertive" tabindex="-1" hidden></p></div>
  <section id="auth-recovery" class="auth-recovery" aria-labelledby="auth-recovery-title" hidden><h2 id="auth-recovery-title">Authentication required</h2><p id="auth-recovery-message"></p><a id="auth-action" href="/sign-in?return_to=%2F" hidden>Sign in</a></section>
  <section id="welcome" class="welcome-panel" aria-labelledby="welcome-title">
    <p class="eyebrow">YOUR SPACE</p>
    <h1 id="welcome-title">Library</h1>
    <div class="welcome-grid">
      <section class="surface library-card" aria-labelledby="recent-title"><div class="section-heading"><div><p class="eyebrow">LIBRARY</p><h2 id="recent-title">Recent artifacts</h2></div><p id="library-count">Loading recent artifacts…</p></div><ul id="recent-artifacts" class="library-list"><li class="muted">Loading recent artifacts…</li></ul></section>
      <div class="library-actions">
        <section id="graphs" class="surface graph-library" aria-labelledby="graphs-title"><div class="section-heading"><div><p class="eyebrow">GRAPHS</p><h2 id="graphs-title">Connected artifacts</h2></div><p>Follow named links.</p></div><ul id="graph-artifacts" class="library-list"><li class="muted">Loading graphs…</li></ul></section>
        <details id="new" class="surface create-card"><summary><span>New artifact</span></summary><form id="create" class="stacked-form">
          <label for="create-name">Name<input id="create-name" required maxlength="255" placeholder="e.g. launch-notes.md"></label>
          <label for="create-file">Upload a file<input id="create-file" type="file"></label>
          <label for="create-text">Or paste content<textarea id="create-text" placeholder="Start writing…"></textarea></label>
          {debug_create_fields}<button type="submit">Create artifact</button>
        </form></details>
        <details id="find" class="surface create-card"><summary><span>Search library</span></summary><div class="stacked-form"><div class="search-grid">
          <form id="search" class="search-form"><label for="search-query">Search documents and files<input id="search-query" required maxlength="500" placeholder="Phrase or keyword"></label><button type="submit">Search</button></form>
          <form id="grep" class="search-form"><label for="grep-pattern">Exact text<input id="grep-pattern" required maxlength="500" placeholder="Exact text"></label><button type="submit">Find exact text</button></form>
        </div><div class="results-wrap"><p class="results-label">Results</p><ul id="results" class="results-list"><li class="muted">No search run yet.</li></ul></div></div></details>
        {debug_open}
      </div>
    </div>
  </section>
  <article id="workspace" class="workspace" hidden>
    <header class="workspace-heading"><div><div class="breadcrumb"><a href="/">Library</a><span aria-hidden="true">/</span><span>artifacts</span><span aria-hidden="true">/</span><span id="artifact-path">Artifact</span></div><div class="artifact-title-row"><span class="artifact-icon" aria-hidden="true">▤</span><div><p class="eyebrow">ARTIFACT</p><h1 id="title">Artifact</h1>{'<div class="title-metadata"><span id="artifact-media">Loading media type…</span><span class="dot" aria-hidden="true"></span><span>Current version</span></div>' if debug else ""}</div></div></div><nav class="workspace-nav" aria-label="Artifact sections">{workspace_nav}</nav></header>
    <div class="workspace-layout"><div class="primary-column">
      <section id="reader" class="surface reader-card" aria-labelledby="reader-title"><div class="card-heading"><div><p class="eyebrow">READ</p><h2 id="reader-title">Readable document</h2></div><span id="reader-kind" class="state-pill">Text</span></div><p id="reader-note" class="field-help">Loading readable content…</p><div id="readable-content" class="document-content">Loading content…</div></section>
      <section id="preview-card" class="surface preview-card" aria-labelledby="preview-title"><div class="card-heading"><div><p class="eyebrow">PREVIEW</p><h2 id="preview-title">Artifact preview</h2></div>{preview_debug}</div><div class="preview-frame"><iframe id="preview" title="Sandboxed artifact preview" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></div>{preview_details}</section>
      {debug_editor}
      {human_editor}
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
            path == "/" or path.startswith("/inspect/") or path.startswith("/artifacts/")
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
                    human=path.startswith("/artifacts/"),
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
