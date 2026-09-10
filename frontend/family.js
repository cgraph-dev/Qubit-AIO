/* family.js — catalog-driven workspace renderer for Qubit dashboard families.
 *
 * Each family page sets window.FAMILY = {groups: [...]} before loading this
 * script. Every control rendered here calls /api/ops (the same backend the
 * CLI and voice surfaces use) — reads run immediately, mutations stage a
 * shared-journal confirmation first. No frontend-only state, no fabricated
 * success: a control only shows success with a backend receipt.
 */
(function () {
  'use strict';
  var CATALOG = [];
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); }
  function show(id, html) { document.getElementById(id).innerHTML = html; }
  function api(path, opts) {
    return vbApi(path, opts || {}).then(function (r) {
      return { status: r.status, body: r.body, authRequired: !!r.authRequired };
    });
  }
  function tag(state) { return '<span class="tag ' + esc(String(state)).replace(/[^a-zA-Z-]/g, '') + '">' + esc(state) + '</span>'; }
  function familyGroups() {
    var f = window.FAMILY || {};
    return (f.groups || []).slice();
  }
  window.loadCatalog = function () {
    show('cat-state', 'loading…');
    return api('/api/ops').then(function (res) {
      var status = res.status, body = res.body, authRequired = res.authRequired;
      if (authRequired) { document.getElementById('pairhint').classList.remove('hidden'); show('cat-state', tag('UNAVAILABLE') + ' pairing required'); return; }
      if (status !== 200 || !body || !body.ops) { show('cat-state', tag('UNAVAILABLE') + ' operations unavailable'); return; }
      var groups = {};
      familyGroups().forEach(function (g) { groups[g] = true; });
      CATALOG = body.ops.filter(function (o) { return groups[o.group]; });
      var present = {};
      CATALOG.forEach(function (o) { present[o.group] = true; });
      var missing = familyGroups().filter(function (g) { return !present[g]; });
      var sel = document.getElementById('group');
      if (sel) sel.innerHTML = '<option value="">All groups</option>' + familyGroups().map(function (g) { return '<option value="' + esc(g) + '">' + esc(g) + '</option>'; }).join('');
      show('cat-state', tag(CATALOG.length ? 'OK' : 'UNAVAILABLE') + ' ' + CATALOG.length + ' operations · same backend as CLI/voice'
        + (missing.length ? ' · empty here: ' + missing.map(esc).join(', ') : ''));
      checkEstop();
      render();
    });
  };
  function checkEstop() {
    api('/api/runtime').then(function (res) {
      var body = res.body;
      var latch = body && (body.estop_latched !== undefined ? body.estop_latched : (body && body.broker ? body.broker.estop_latched : (body && body.power ? body.power.emergency_stop : undefined)));
      if (latch === true) document.getElementById('estop').classList.remove('hidden');
    }).catch(function () { });
  }
  function fieldHtml(op, arg) {
    var id = 'f-' + op.id + '-' + arg.name;
    var req = arg.required ? ' required aria-required="true"' : '';
    var secret = arg.secret ? ' <span class="muted">(never stored)</span>' : '';
    var input = '';
    if (arg.type === 'choice' && arg.choices && arg.choices.length) {
      input = '<select id="' + esc(id) + '"' + req + '>' + (arg.required ? '' : '<option value="">—</option>') + arg.choices.map(function (c) { return '<option value="' + esc(c) + '">' + esc(c) + '</option>'; }).join('') + '</select>';
    } else if (arg.type === 'int') {
      input = '<input id="' + esc(id) + '" type="text" inputmode="numeric"' + req + ' placeholder="' + esc(arg.clarifier || arg.name) + '">';
    } else if (arg.secret) {
      input = '<input id="' + esc(id) + '" type="password" autocomplete="new-password"' + req + '>';
    } else if (arg.name === 'snapshot' || arg.name === 'answers' || arg.name === 'body') {
      input = '<textarea id="' + esc(id) + '" rows="3"' + req + ' placeholder="' + esc(arg.clarifier || arg.name) + '"></textarea>';
    } else {
      input = '<input id="' + esc(id) + '" type="text"' + req + ' placeholder="' + esc(arg.clarifier || arg.name) + '" spellcheck="false">';
    }
    return '<div class="field"><label for="' + esc(id) + '">' + esc(arg.name) + secret + '</label>' + input + '</div>';
  }
  window.render = function () {
    var q = ((document.getElementById('q') || {}).value || '').toLowerCase();
    var g = document.getElementById('group') ? document.getElementById('group').value : '';
    var list = CATALOG.filter(function (o) { return (!g || o.group === g) && (!q || (o.id + ' ' + o.title + ' ' + (o.help || '')).toLowerCase().indexOf(q) !== -1); });
    if (!list.length) { show('ops', '<div class="card muted">No operations match. Clear the filter.</div>'); return; }
    var byGroup = {};
    list.forEach(function (o) { (byGroup[o.group] = byGroup[o.group] || []).push(o); });
    var html = '';
    Object.keys(byGroup).sort().forEach(function (group) {
      html += '<h2>' + esc(group) + ' (' + byGroup[group].length + ')</h2><div class="op-grid">';
      byGroup[group].forEach(function (op) {
        html += '<div class="op" id="op-' + esc(op.id) + '"><h3>' + esc(op.title) + '</h3>'
          + '<div class="desc"><code>' + esc(op.id) + '</code> · ' + esc(op.help || '') + '</div>';
        if (op.interfaces && op.interfaces.length < 3)
          html += '<div class="desc">Interfaces: ' + esc(op.interfaces.join(', ')) + (op.interface_reason ? ' — ' + esc(op.interface_reason) : '') + '</div>';
        if (op.confirm) html += '<div class="desc">Confirmation-gated.</div>';
        (op.args || []).forEach(function (a) { html += fieldHtml(op, a); });
        html += '<div class="row"><button class="ok" onclick="runOp(\'' + esc(op.id) + '\')">Run</button></div>'
          + '<div id="res-' + esc(op.id) + '" aria-live="polite"></div></div>';
      });
      html += '</div>';
    });
    show('ops', html);
  };
  function collectArgs(op) {
    var args = {};
    (op.args || []).forEach(function (a) {
      var el = document.getElementById('f-' + op.id + '-' + a.name);
      if (!el) return;
      var v = el.value;
      if (v != null && String(v) !== '') args[a.name] = v;
    });
    return args;
  }
  function renderOut(opId, out) {
    var html = tag(out.state || 'UNAVAILABLE');
    if (out.code) html += ' <code>' + esc(out.code) + '</code>';
    if (out.speech) html += '<div>' + esc(out.speech) + '</div>';
    if (out.confirmation_id) {
      html += '<div class="row"><span class="muted">confirmation <code>' + esc(out.confirmation_id) + '</code></span>'
        + '<button class="ok" onclick="confirmOp(\'' + esc(opId) + '\',\'' + esc(out.confirmation_id) + '\')">Confirm</button>'
        + '<button class="warn" onclick="cancelOp(\'' + esc(opId) + '\',\'' + esc(out.confirmation_id) + '\')">Cancel</button></div>';
      if (out.sensitive_required) html += '<div class="muted">Re-enter ' + esc(out.sensitive_required.join(', ')) + ' to confirm (never stored).</div>';
    }
    if (out.receipt) html += '<pre>' + esc(JSON.stringify(out.receipt, null, 2)).slice(0, 4000) + '</pre>';
    if (out.detail) html += '<div class="err">' + esc(out.detail) + '</div>';
    if ((out.state === 'failed' || out.state === 'refused')) html += ' <span class="err">(no change made)</span>';
    show('res-' + opId, html);
    var region = document.getElementById('res-' + CSS.escape(opId));
    if (region) { region.setAttribute('tabindex', '-1'); region.focus({ preventScroll: true }); }
  }
  window.runOp = function (opId) {
    var op = null;
    CATALOG.forEach(function (o) { if (o.id === opId) op = o; });
    if (!op) return;
    show('res-' + opId, '<span class="muted">working…</span>');
    api('/api/ops/run', { method: 'POST', body: JSON.stringify({ id: opId, args: collectArgs(op) }) }).then(function (res) {
      if (res.authRequired) { renderOut(opId, { state: 'refused', code: 'UNPAIRED', detail: 'Pair this dashboard first.' }); return; }
      renderOut(opId, res.body || { state: 'failed', detail: 'empty response' });
    });
  };
  window.confirmOp = function (opId, cid) {
    var op = null;
    CATALOG.forEach(function (o) { if (o.id === opId) op = o; });
    var payloadSensitive = {};
    if (op) (op.args || []).forEach(function (a) {
      var el = document.getElementById('f-' + opId + '-' + a.name);
      if (el && el.type === 'password' && el.value) payloadSensitive[a.name] = el.value;
    });
    api('/api/ops/confirm', { method: 'POST', body: JSON.stringify({ confirmation_id: cid, sensitive: payloadSensitive }) }).then(function (res) {
      renderOut(opId, res.body || { state: 'failed', detail: 'empty response' });
    });
  };
  window.cancelOp = function (opId, cid) {
    api('/api/ops/cancel', { method: 'POST', body: JSON.stringify({ confirmation_id: cid }) }).then(function (res) {
      renderOut(opId, res.body || { state: 'failed', detail: 'empty response' });
    });
  };
  loadCatalog();
})();
