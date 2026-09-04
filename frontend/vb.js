/* vb.js — shared authenticated fetch helper for the Qubit dashboard.

 * One helper for EVERY API request. The header token lives ONLY in browser
 * localStorage ('vdb_token'); it is NEVER read from a URL query string and
 * never embedded in HTML, reports, logs, screenshots, or error messages.
 * A missing/wrong token yields a typed {status:401, authRequired:true} and a
 * plain-language "Pair this dashboard" hint linking to /token-setup.
 *
 * API base: same-origin by default. The ONLY way to point the dashboard at a
 * remote bridge is the explicit Settings/Pairing action, which validates the
 * origin, requires user confirmation, tests the candidate token BEFORE saving,
 * and never follows a ?api= query string (an attacker-controlled URL is
 * ignored so the stored token is never sent to an unapproved origin).
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

  /* Strict origin validation for the explicit pairing/settings action.
   * Accepts: empty (same-origin), loopback http, or https (confirmed by the
   * caller). Rejects credentials, query strings, fragments, and non-http(s). */
  function vbValidateApiOrigin(input) {
    var v = String(input || '').trim().replace(/\/+$/, '');
    if (!v) return { base: '', error: '' };
    var u = null;
    try { u = new URL(v); } catch (e) { u = null; }
    if (!u) return { base: '', error: 'Enter a valid server address (https://…), or leave empty for this device.' };
    var proto = String(u.protocol || '').toLowerCase();
    if (proto !== 'http:' && proto !== 'https:') {
      return { base: '', error: 'Only http(s) server addresses are allowed.' };
    }
    if (proto === 'http:') {
      var host = String(u.hostname || '').toLowerCase();
      if (!/^(127\.0\.0\.1|localhost|\[::1\])$/.test(host)) {
        return { base: '', error: 'Plain HTTP is only allowed for loopback (127.0.0.1 / localhost).' };
      }
    }
    if (u.username || u.password) {
      return { base: '', error: 'Do not include credentials in the server address.' };
    }
    if (u.search || u.hash) {
      return { base: '', error: 'Do not include query strings or fragments in the server address.' };
    }
    return { base: u.origin, error: '' };
  }

  /* Never accept ?token= — query-string tokens are rejected by the backend
   * (Packet 1B) and leak through history/referrers. Header only. */
  function vbHeaders(extra) {
    var h = Object.assign({}, extra || {});
    var t = vbToken();
    if (t) h['X-Api-Token'] = t;
    return h;
  }

  /* Plain-language pairing hint with a link to /token-setup. Built with DOM
   * nodes and textContent (Packet 8L); no token is placed in any URL. */
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
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(document.createTextNode(
      'This dashboard is not paired with the bridge. '));
    var link = document.createElement('a');
    link.href = '/token-setup';
    link.style.cssText = 'color:#6db3ff;display:inline-flex;'
      + 'align-items:center;min-height:44px;padding:0 8px;';
    link.textContent = 'Pair this dashboard';
    el.appendChild(link);
    el.appendChild(document.createTextNode(
      ' to store the X-Api-Token header token in this browser.'));
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

  /* Shared authenticated BINARY helper (Packet 8L): same X-Api-Token header
   * contract as vbApi, but returns a Blob for downloads / Blob-object-URLs.
   * Never parses binary as JSON and never places the token in a URL. */
  async function vbFetchBinary(path) {
    var base = vbApiBase();
    try {
      var res = await fetch(base + path, {
        headers: vbHeaders({ 'Accept': 'application/octet-stream' })
      });
      if (res.status === 401) {
        vbPairHint();
        return { ok: false, status: 401, authRequired: true, blob: null };
      }
      if (!res.ok) {
        return { ok: false, status: res.status, authRequired: false, blob: null };
      }
      var blob = await res.blob();
      return { ok: true, status: res.status, authRequired: false, blob: blob };
    } catch (e) {
      return { ok: false, status: 0, authRequired: false, error: 'network', blob: null };
    }
  }

  /* Download a Blob as a file; revoke the object URL after the click. */
  function vbDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { try { URL.revokeObjectURL(url); } catch (e) {} }, 4000);
  }

  /* Open a Blob (authenticated HTML view) in a new tab via a temporary object
   * URL; revoke it after the tab has had time to load. */
  function vbOpenBinary(blob) {
    var url = URL.createObjectURL(blob);
    var w = window.open(url, '_blank');
    setTimeout(function () { try { URL.revokeObjectURL(url); } catch (e) {} }, 60000);
    return w;
  }

  /* Test a candidate pairing (api base + token) WITHOUT persisting it. Used by
   * the Settings and token-setup flows so an invalid/expired token or an
   * unreachable bridge never replaces the working stored token. */
  async function vbApiTest(base, token) {
    var b = String(base || '').replace(/\/+$/, '');
    try {
      var res = await fetch(b + '/api/status', {
        headers: { 'X-Api-Token': String(token || '') },
        cache: 'no-store'
      });
      return { status: res.status, ok: res.status >= 200 && res.status < 300 };
    } catch (e) {
      return { status: 0, ok: false, error: 'network' };
    }
  }

  window.vbToken = vbToken;
  window.vbApiBase = vbApiBase;
  window.vbValidateApiOrigin = vbValidateApiOrigin;
  window.vbHeaders = vbHeaders;
  window.vbApi = vbApi;
  window.vbFetchBinary = vbFetchBinary;
  window.vbDownload = vbDownload;
  window.vbOpenBinary = vbOpenBinary;
  window.vbApiTest = vbApiTest;
  window.vbPairHint = vbPairHint;
})();