from __future__ import annotations

from html import escape

CONNECTIONS_CSS = """
.settings-route .page { max-width: 1180px; }
.settings-heading { max-width: 48rem; margin: 1.5rem 0 1.75rem; }
.settings-heading h1 { margin-bottom: .65rem; }
.settings-heading p { color: var(--muted); line-height: 1.65; }
.settings-layout { display: grid; grid-template-columns: minmax(18rem, .78fr) minmax(0, 1.22fr); gap: 1rem; align-items: start; }
.settings-card { min-width: 0; padding: 1.25rem; }
.settings-card h2 { margin-bottom: .45rem; }
.settings-card > p { color: var(--muted); font-size: .84rem; line-height: 1.55; }
.settings-form { display: grid; gap: .85rem; margin-top: 1.25rem; }
.settings-form textarea { min-height: 5rem; }
.settings-form .field-help { margin: -.3rem 0 0; }
.settings-form button, .connection-actions button { min-height: 42px; }
.settings-list { margin-top: .8rem; }
.connection-item, .audit-item { border-top: 1px solid var(--line); padding: 1rem 0; }
.connection-item:first-child, .audit-item:first-child { border-top: 0; padding-top: .25rem; }
.connection-heading { display: flex; align-items: start; justify-content: space-between; gap: .75rem; }
.connection-heading strong { overflow-wrap: anywhere; }
.connection-meta { margin: .35rem 0 0; color: var(--muted); font-size: .76rem; overflow-wrap: anywhere; }
.connection-capabilities { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .7rem; }
.connection-capability, .connection-state { border: 1px solid var(--line); border-radius: 999px; padding: .22rem .5rem; color: var(--muted); font-size: .7rem; }
.connection-state { display: inline-flex; flex: 0 0 auto; font-weight: 800; white-space: nowrap; }
.connection-state[data-state="active"] { background: var(--green-soft); color: var(--green); }
.connection-state[data-state="pending"] { background: var(--amber-soft); color: var(--amber); }
.connection-state[data-state="revoked"] { background: #f1f1f1; }
.connection-state[data-state="error"] { background: #fff0f0; color: #8f2323; }
.connection-actions { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .8rem; }
.settings-status { min-height: 2.4rem; margin: 0 0 1rem; border-radius: 7px; padding: .65rem .8rem; background: var(--green-soft); color: var(--green); font-size: .82rem; }
.settings-status[data-state="pending"] { background: var(--amber-soft); color: var(--amber); }
.settings-status[data-state="error"] { background: #fff2f2; color: #8f2323; }
.admin-denied { border: 1px solid #e9b5b5; border-radius: 9px; padding: .9rem 1rem; background: #fff8f8; color: #6f4141; }
.admin-denied h2 { color: #8f2323; font-size: 1rem; }
.admin-denied p { margin-bottom: .65rem; font-size: .84rem; line-height: 1.55; }
.audit-section { margin-top: 1rem; }
.audit-controls { display: flex; align-items: end; gap: .6rem; flex-wrap: wrap; margin-top: .75rem; }
.audit-controls label { flex: 1 1 14rem; }
.revoke-dialog { width: min(430px, calc(100% - 2rem)); border: 1px solid var(--line); border-radius: 12px; padding: 1.25rem; background: var(--surface); color: var(--ink); box-shadow: 0 20px 56px rgb(24 30 22 / 18%); }
.revoke-dialog::backdrop { background: rgb(19 23 18 / 24%); }
.revoke-dialog p { color: var(--muted); font-size: .84rem; line-height: 1.55; }
.revoke-actions { display: flex; justify-content: flex-end; gap: .55rem; margin-top: 1rem; }
.revoke-actions button { min-height: 44px; }
.button-danger { border: 1px solid #c56a6a; border-radius: 7px; padding: .58rem .8rem; background: #a83434; color: white; font-weight: 800; }
@media (max-width: 760px) { .settings-layout { grid-template-columns: 1fr; } .connection-heading { align-items: stretch; flex-direction: column; } .connection-actions button { flex: 1 1 10rem; } }
@media (max-width: 520px) { .settings-card { padding: 1rem; } .connection-actions, .revoke-actions { flex-direction: column; } .connection-actions button, .revoke-actions button { width: 100%; } }
""".strip()


CONNECTIONS_JS = r"""
const byId = (id) => document.getElementById(id);
const records = new Map();
let pendingRevoke = null;
let revokeTrigger = null;
function unwrap(value) { return value && typeof value === 'object' && Object.hasOwn(value, 'result') ? value.result : value; }
function safeConnection(value) {
  const item = value && typeof value === 'object' ? value : {};
  return {id: typeof item.id === 'string' ? item.id : '', name: typeof item.name === 'string' ? item.name : 'Unnamed connection', origin: typeof item.origin === 'string' ? item.origin : 'Origin unavailable', transport: typeof item.transport === 'string' ? item.transport : 'streamable_http', status: typeof item.status === 'string' ? item.status : 'pending', health_status: typeof item.health_status === 'string' ? item.health_status : 'unknown', approved_tools: Array.isArray(item.approved_tools) ? item.approved_tools.filter((v) => typeof v === 'string') : [], approved_resources: Array.isArray(item.approved_resources) ? item.approved_resources.filter((v) => typeof v === 'string') : [], credential_configured: item.credential_configured === true};
}
function parseItems(value) { return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean); }
function stateFor(item) { if (item.status === 'revoked') return 'revoked'; if (item.health_status === 'unhealthy') return 'error'; if (item.status === 'active') return 'active'; return 'pending'; }
function stateLabel(state) { return {pending: 'Pending', active: 'Active', revoked: 'Revoked', error: 'Error'}[state] || 'Pending'; }
function pageState(state, message) { const node = byId('connections-status'); node.dataset.state = state; node.textContent = message; }
function adminError(error) {
  const panel = byId('admin-denied'); const form = byId('connection-register'); const message = byId('admin-denied-message'); const reauth = byId('connection-reauth');
  if (error.status === 403) { message.textContent = 'Your account can use Folio Lattice, but only an organization administrator can manage approved connections.'; form.hidden = true; reauth.hidden = true; panel.hidden = false; pageState('error', 'Administrator permission is required.'); }
  else if (error.status === 401) { message.textContent = 'Your session has expired. Sign in again to manage approved connections.'; form.hidden = true; reauth.hidden = false; panel.hidden = false; pageState('error', 'Sign-in is required.'); }
  else pageState('error', error.message || 'Connection service is unavailable. Try again.');
}
async function callConnection(tool, arguments_) {
  const response = await fetch('/api/admin/mcp', {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, credentials: 'same-origin', body: JSON.stringify({tool, arguments: arguments_})});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(result.message || result.error || 'Connection request could not be completed.'); error.status = response.status; throw error; }
  return unwrap(result);
}
function capabilities(item) { const target = document.createElement('div'); target.className = 'connection-capabilities'; const values = item.approved_tools.map((v) => `Tool: ${v}`).concat(item.approved_resources.map((v) => `Resource: ${v}`)); (values.length ? values : ['No capabilities approved']).forEach((value) => { const pill = document.createElement('span'); pill.className = 'connection-capability'; pill.textContent = value; target.append(pill); }); return target; }
function renderAudit(values) {
  const target = byId('audit-list'); target.replaceChildren(); if (!Array.isArray(values) || !values.length) { const empty = document.createElement('li'); empty.className = 'muted'; empty.textContent = 'No audit activity for this selection.'; target.append(empty); return; }
  values.forEach((value) => { const item = document.createElement('li'); item.className = 'audit-item'; const heading = document.createElement('div'); heading.className = 'connection-heading'; const action = document.createElement('strong'); action.textContent = typeof value.action === 'string' ? value.action : 'Connection action'; const outcome = document.createElement('span'); outcome.className = 'connection-state'; const state = typeof value.outcome === 'string' ? value.outcome : 'recorded'; outcome.textContent = state; outcome.dataset.state = state.includes('revok') ? 'revoked' : state === 'allowed' || state === 'healthy' ? 'active' : 'error'; heading.append(action, outcome); const meta = document.createElement('p'); meta.className = 'connection-meta'; meta.textContent = `${typeof value.actor_id === 'string' ? value.actor_id : 'Organization administrator'} · ${typeof value.created_at === 'string' ? value.created_at : 'Time unavailable'}`; item.append(heading, meta); target.append(item); });
}
function renderConnections() {
  const target = byId('connections-list'); target.replaceChildren(); const values = Array.from(records.values());
  if (!values.length) { const empty = document.createElement('li'); empty.className = 'muted'; empty.textContent = 'No approved connections registered yet.'; target.append(empty); }
  values.forEach((item) => { const state = stateFor(item); const row = document.createElement('li'); row.className = 'connection-item'; const heading = document.createElement('div'); heading.className = 'connection-heading'; const name = document.createElement('strong'); name.textContent = item.name; const badge = document.createElement('span'); badge.className = 'connection-state'; badge.dataset.state = state; badge.textContent = stateLabel(state); heading.append(name, badge); row.append(heading); const meta = document.createElement('p'); meta.className = 'connection-meta'; meta.textContent = `${item.origin} · ${item.transport}`; row.append(meta, capabilities(item)); const credential = document.createElement('p'); credential.className = 'connection-meta'; credential.textContent = item.credential_configured ? 'Brokered credentials are configured.' : 'No brokered credentials configured.'; row.append(credential); const actions = document.createElement('div'); actions.className = 'connection-actions'; const check = document.createElement('button'); check.type = 'button'; check.className = 'button-secondary'; check.textContent = state === 'pending' ? 'Check status' : 'Refresh status'; check.addEventListener('click', () => checkStatus(item.id, check)); actions.append(check); const audit = document.createElement('button'); audit.type = 'button'; audit.className = 'button-secondary'; audit.textContent = 'View audit'; audit.addEventListener('click', () => { byId('audit-connection').value = item.id; loadAudit(item.id); byId('audit-title').focus(); }); actions.append(audit); if (state !== 'revoked') { const revoke = document.createElement('button'); revoke.type = 'button'; revoke.className = 'button-danger'; revoke.textContent = 'Revoke'; revoke.setAttribute('aria-label', `Revoke ${item.name}`); revoke.addEventListener('click', () => openRevoke(item, revoke)); actions.append(revoke); } row.append(actions); target.append(row); });
  const select = byId('audit-connection'); const selected = select.value; select.replaceChildren(); const all = document.createElement('option'); all.value = ''; all.textContent = 'All connections'; select.append(all); values.forEach((item) => { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; select.append(option); }); select.value = values.some((item) => item.id === selected) ? selected : '';
}
async function loadAudit(id) { const arguments_ = {limit: 128}; if (id) arguments_.connection_id = id; try { renderAudit(await callConnection('external_mcp_audit', arguments_)); } catch (error) { renderAudit([]); adminError(error); } }
async function loadConnections() { pageState('pending', 'Loading approved connections…'); try { const value = await callConnection('external_mcp_connection_list', {limit: 128}); if (!Array.isArray(value)) throw new Error('The connection service returned an invalid list.'); records.clear(); value.map(safeConnection).filter((item) => item.id).forEach((item) => records.set(item.id, item)); renderConnections(); await loadAudit(byId('audit-connection').value || null); pageState(records.size ? 'active' : 'pending', records.size ? 'Connections loaded. Status is shown for each connection.' : 'No approved connections registered yet.'); } catch (error) { renderConnections(); adminError(error); } }
async function checkStatus(id, button) { const item = records.get(id); if (!item) return; button.disabled = true; button.setAttribute('aria-busy', 'true'); pageState('pending', `Checking ${item.name}…`); try { const updated = safeConnection(await callConnection('external_mcp_connection_status', {connection_id: id, probe: true})); records.set(id, {...item, ...updated}); renderConnections(); pageState(stateFor(updated), `${item.name} status: ${stateLabel(stateFor(updated))}.`); } catch (error) { records.set(id, {...item, health_status: 'unhealthy'}); renderConnections(); adminError(error); } finally { button.disabled = false; button.removeAttribute('aria-busy'); } }
function restoreFocus() { const trigger = revokeTrigger; revokeTrigger = null; if (trigger && document.contains(trigger)) trigger.focus(); }
function closeRevoke() { const dialog = byId('revoke-access'); if (dialog.open) dialog.close(); else { dialog.removeAttribute('open'); pendingRevoke = null; restoreFocus(); } }
function openRevoke(item, trigger) { pendingRevoke = item; revokeTrigger = trigger; byId('revoke-name').textContent = item.name; const dialog = byId('revoke-access'); if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', ''); setTimeout(() => byId('revoke-cancel').focus(), 0); }
byId('revoke-access').addEventListener('close', () => { pendingRevoke = null; restoreFocus(); });
byId('revoke-access').addEventListener('cancel', (event) => { event.preventDefault(); closeRevoke(); });
document.addEventListener('keydown', (event) => { const dialog = byId('revoke-access'); if (event.key === 'Escape' && dialog.hasAttribute('open') && !dialog.open) { event.preventDefault(); closeRevoke(); } });
byId('revoke-cancel').addEventListener('click', closeRevoke);
byId('revoke-confirm').addEventListener('click', async () => { if (!pendingRevoke) return; const item = pendingRevoke; const button = byId('revoke-confirm'); button.disabled = true; pageState('pending', `Revoking ${item.name}…`); try { const value = safeConnection(await callConnection('external_mcp_connection_revoke', {connection_id: item.id, reason: 'revoked in Connections settings'})); records.set(item.id, {...item, ...value, status: 'revoked'}); closeRevoke(); renderConnections(); await loadAudit(item.id); pageState('revoked', `${item.name} is revoked. Future external calls are blocked.`); } catch (error) { adminError(error); button.disabled = false; button.focus(); } });
byId('audit-refresh').addEventListener('click', () => loadAudit(byId('audit-connection').value || null));
byId('audit-connection').addEventListener('change', () => loadAudit(byId('audit-connection').value || null));
byId('connection-endpoint').addEventListener('input', (event) => { const input = event.currentTarget; try { input.setCustomValidity(input.value.trim() && new URL(input.value.trim()).protocol !== 'https:' ? 'Use an HTTPS endpoint.' : ''); } catch (_error) { input.setCustomValidity(input.value.trim() ? 'Enter a valid HTTPS endpoint.' : ''); } });
byId('connection-register').addEventListener('submit', async (event) => { event.preventDefault(); const tools = parseItems(byId('approved-tools').value); const resources = parseItems(byId('approved-resources').value); if (!tools.length && !resources.length) { pageState('error', 'Approve at least one exact tool or resource.'); byId('approved-tools').focus(); return; } const submit = byId('connection-submit'); submit.disabled = true; submit.setAttribute('aria-busy', 'true'); pageState('pending', 'Registering approved connection…'); try { await callConnection('external_mcp_connection_register', {name: byId('connection-name').value.trim(), endpoint: byId('connection-endpoint').value.trim(), approved_tools: tools, approved_resources: resources, allowed_origins: [byId('connection-origin').value.trim()], reason: 'registered in Connections settings'}); byId('connection-register').reset(); await loadConnections(); pageState('active', 'Connection registered. Its active state is shown below.'); } catch (error) { adminError(error); } finally { submit.disabled = false; submit.removeAttribute('aria-busy'); } });
loadConnections();
"""


def connections_html(*, auth_state: str = "local") -> str:
    state = escape(auth_state, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connections — Folio Lattice</title><link rel="stylesheet" href="/ui.css"><link rel="stylesheet" href="/connections.css"></head>
<body class="settings-route" data-auth-state="{state}"><a class="skip-link" href="#main">Skip to content</a><div class="app-shell"><header class="topbar"><div class="brand-lockup"><a class="brand-mark" href="/" aria-label="Folio Lattice home">F</a><div><div class="brand-name">Folio Lattice</div><div class="brand-subtitle">Knowledge workspace</div></div></div><nav class="primary-nav" aria-label="Primary"><a href="/">Library</a><a class="is-active" aria-current="page" href="/settings/connections">Settings</a></nav></header>
<main id="main" class="page" tabindex="-1" aria-busy="false"><div class="live-region"><p id="connections-status" class="settings-status" role="status" aria-live="polite">Loading approved connections…</p></div><section id="admin-denied" class="admin-denied" role="alert" hidden><h2>Connections administration is unavailable</h2><p id="admin-denied-message"></p><a id="connection-reauth" href="/sign-in?return_to=%2Fsettings%2Fconnections" hidden>Sign in again</a></section><header class="settings-heading"><p class="eyebrow">SETTINGS / CONNECTIONS</p><h1>Approved connections</h1><p>Manage the external MCP services your organization has approved. Requests are checked by the server; credentials stay with the broker and never enter this page.</p></header><div class="settings-layout"><section class="surface settings-card" aria-labelledby="register-title"><p class="eyebrow">NEW APPROVAL</p><h2 id="register-title">Register a connection</h2><p>Use an HTTPS endpoint and list only the exact tools or resources this connection may expose.</p><form id="connection-register" class="settings-form"><label for="connection-name">Connection name<input id="connection-name" required maxlength="255" autocomplete="off" placeholder="e.g. Team calendar"></label><label for="connection-endpoint">HTTPS endpoint<input id="connection-endpoint" type="url" required maxlength="4096" inputmode="url" autocomplete="off" placeholder="https://service.example/mcp"></label><p class="field-help">Private, local, and credential-bearing URLs are rejected by the service.</p><label for="approved-tools">Approved tools<textarea id="approved-tools" maxlength="16384" placeholder="calendar.events.list"></textarea></label><p class="field-help">One exact tool per line. Leave blank if approving resources only.</p><label for="approved-resources">Approved resources<textarea id="approved-resources" maxlength="16384" placeholder="calendar://events"></textarea></label><label for="connection-origin">Allowed HTTPS origin<input id="connection-origin" type="url" required maxlength="4096" inputmode="url" autocomplete="off" placeholder="https://service.example"></label><p class="field-help">The endpoint origin must be listed exactly.</p><button id="connection-submit" type="submit">Register connection</button></form></section><section class="surface settings-card" aria-labelledby="connections-title"><p class="eyebrow">CURRENT APPROVALS</p><h2 id="connections-title">Connections</h2><p>Active, revoked, health, and credential configuration states are shown without exposing secret values.</p><ul id="connections-list" class="settings-list"><li class="muted">Loading…</li></ul><section class="audit-section" aria-labelledby="audit-title"><h2 id="audit-title" tabindex="-1">Audit activity</h2><div class="audit-controls"><label for="audit-connection">Connection<select id="audit-connection"><option value="">All connections</option></select></label><button id="audit-refresh" class="button-secondary" type="button">Refresh audit</button></div><ul id="audit-list" class="settings-list"><li class="muted">Loading…</li></ul></section></section></div></main></div><dialog id="revoke-access" class="revoke-dialog" aria-labelledby="revoke-title"><h2 id="revoke-title">Revoke connection?</h2><p><strong id="revoke-name"></strong> will be blocked from future external calls immediately. This cannot recall data already observed by the service.</p><div class="revoke-actions"><button id="revoke-cancel" class="button-secondary" type="button">Keep connection</button><button id="revoke-confirm" class="button-danger" type="button">Revoke connection</button></div></dialog><script src="/connections.js"></script></body></html>"""
