/* vb.js — shared authenticated fetch helper for the Qubit dashboard.

 * One helper for EVERY API request. The header token lives ONLY in browser
 * localStorage ('vdb_token'); it is NEVER read from a URL query string and
 * never embedded in HTML, reports, logs, screenshots, or error messages.
 * A missing/wrong token yields a typed {status:401, authRequired:true} and a
 * plain-language "Pair this dashboard" hint linking to /token-setup.
 */
(function () {
  'use strict';
  var TOKEN_KEY = 'vdb_token';
  var API_KEY = 'vdb_api';

  function vbToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }

  function vbApiBase() {
    try { return (localStorage.getItem(API_KEY) || '').replace(/\/+$/, ''); } catch (e) { return ''; }
  }

  /* Never accept ?token= — query-string tokens are rejected by the backend
   * (Packet 1B) and leak through history/referrers. Header only. */
  function vbHeaders(extra) {
    var h = Object.assign({}, extra || {});
    var t = vbToken();
    if (t) h['X-Api-Token'] = t;
    return h;
  }

  /* Plain-language pairing hint with a link to /token-setup. */
  function vbPairHint(containerId) {
    var el = containerId ? document.getElementById(containerId) : null;
    if (!el) el = document.getElementById('pair-hint');
    if (!el) {
      el = document.createElement('div');
      el.id = 'pair-hint';
      el.style.cssText = 'padding:10px 12px;margin:8px 0;border:1px solid #7a5f1f;'
        + 'border-radius:8px;background:#1a1f2e;color:#ffd27a;font-size:13px;';
      var body = document.body;
      if (body && body.firstChild) body.insertBefore(el, body.firstChild);
      else if (body) body.appendChild(el);
    }
    el.innerHTML = 'This dashboard is not paired with the bridge. '
      + '<a href="/token-setup" style="color:#6db3ff">Pair this dashboard</a> '
      + 'to store the X-Api-Token header token in this browser.';
    return el;
  }

  /* One shared request helper. Sends X-Api-Token from localStorage on every
   * request; checks Content-Type BEFORE parsing JSON (a plain-text 401/404/500
   * never reaches JSON.parse); a 401 returns {ok:false, status:401,
   * authRequired:true} and shows the pairing hint; network/timeout returns
   * {ok:false, status:0, error:'network'}. Never places the token in a URL. */
  async function vbApi(path, opts) {
    var base = vbApiBase();
    var o = Object.assign({}, opts || {});
    o.headers = vbHeaders(o.headers || {});
    var method = String(o.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      if (!o.headers['Content-Type']) o.headers['Content-Type'] = 'application/json';
    }
    var res;
    try {
      res = await fetch(base + path, o);
    } catch (e) {
      return { ok: false, status: 0, authRequired: false, error: 'network', body: null };
    }
    if (res.status === 401) {
      vbPairHint();
      return { ok: false, status: 401, authRequired: true, error: 'auth', body: null };
    }
    var ctype = (res.headers.get('Content-Type') || '').toLowerCase();
    var body = null;
    if (ctype.indexOf('json') !== -1) {
      try { body = await res.json(); }
      catch (e) { body = null; }
    } else {
      var text = '';
      try { text = await res.text(); } catch (e) { text = ''; }
      body = text ? { raw: String(text).slice(0, 1000) } : null;
    }
    var ok = res.status >= 200 && res.status < 300;
    return { ok: ok, status: res.status, body: body, authRequired: false,
             error: ok ? null : ('http-' + res.status) };
  }

  window.vbToken = vbToken;
  window.vbApiBase = vbApiBase;
  window.vbHeaders = vbHeaders;
  window.vbApi = vbApi;
  window.vbPairHint = vbPairHint;
})();