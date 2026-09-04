/* ui.js — shared rendering, status, and stream primitives for the Qubit
 * dashboard. Load AFTER vb.js (uses vbHeaders/vbPairHint/vbApiBase).
 *
 * Every dynamic value rendered here goes through textContent (vbBuild /
 * vbAppend / vbStatus / vbAnnounce) or the escaping choke-point; no raw
 * dynamic innerHTML is created by these helpers. The only raw fetch in the
 * frontend is the SSE stream, centralized in vbStream below, because vbApi is
 * a JSON helper and EventSource cannot send the X-Api-Token header.
 */
(function () {
  'use strict';

  function noop() {}

  /* Effective API base. A transient ?api= override is persisted to the same
   * localStorage key vbApiBase() reads, so vbApi() and vbStream() agree on the
   * base for the whole session (matches the existing "set-api" save control). */
  function vbResolveBase() {
    try {
      var q = new URLSearchParams(location.search).get('api');
      if (q) {
        var cur = (localStorage.getItem('vdb_api') || '').replace(/\/+$/, '');
        var next = String(q).replace(/\/+$/, '');
        if (next && next !== cur) localStorage.setItem('vdb_api', next);
      }
    } catch (e) { /* private mode: fall through to vbApiBase */ }
    return window.vbApiBase ? window.vbApiBase() : '';
  }

  function vbClear(el) {
    if (el) while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  }

  /* Append strings (as text nodes) and nodes to el. Never interpreted as HTML. */
  function vbAppend(el, children) {
    var arr = Array.isArray(children) ? children : [children];
    for (var i = 0; i < arr.length; i++) {
      var c = arr[i];
      if (c == null) continue;
      if (typeof c === 'string' || typeof c === 'number') {
        el.appendChild(document.createTextNode(String(c)));
      } else if (c && c.nodeType) {
        el.appendChild(c);
      }
    }
    return el;
  }

  /* Build a DOM element safely.
   * attrs: className/class, dataset, aria-*, role, boolean attrs (true -> '' ),
   * and event listeners as onclick/onchange/onkeydown/... values. */
  function vbBuild(tag, attrs, children) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    for (var k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      var v = attrs[k];
      if (v == null) continue;
      if (k === 'class' || k === 'className') {
        el.className = String(v);
      } else if (k === 'dataset') {
        for (var d in v) {
          if (Object.prototype.hasOwnProperty.call(v, d)) el.dataset[d] = v[d];
        }
      } else if (k.indexOf('on') === 0 && k.length > 2) {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) {
        el.setAttribute(k, '');
      } else if (v !== false) {
        el.setAttribute(k, String(v));
      }
    }
    return vbAppend(el, children);
  }

  /* Typed status text + state class. state in {live, ready, offline, degraded}. */
  var STATE_RE = /^(live|ready|offline|degraded)$/;
  function vbStatus(el, text, state) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (!el) return el;
    el.textContent = text == null ? '' : String(text);
    el.className = String(el.className || '').split(/\s+/)
      .filter(function (c) { return !STATE_RE.test(c); })
      .join(' ');
    if (state) el.classList.add(state);
    return el;
  }

  /* Plain-language, typed description of a vbApi result for human output. */
  function vbApiMessage(res) {
    if (!res) return 'No response from the bridge.';
    if (res.authRequired) {
      return 'Not paired with the bridge. Open Pair device and save your token.';
    }
    if (res.error === 'network' || res.status === 0) {
      return 'Bridge unreachable. Start the bridge, then refresh this page.';
    }
    if (!res.ok) {
      return 'The bridge returned an error (HTTP ' + res.status + '). Try again.';
    }
    return '';
  }

  /* Announce a transient result to screen readers (polite live region). */
  function vbAnnounce(text) {
    var el = document.getElementById('vb-announcer');
    if (!el) {
      el = document.createElement('div');
      el.id = 'vb-announcer';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('class', 'sr-only');
      var body = document.body;
      if (body) body.appendChild(el);
    }
    el.textContent = String(text || '');
  }

  /* Open the SSE stream with the shared header token. Returns a Promise of a
   * typed object {ok, status, authRequired, reader, cancel} — never parsed
   * JSON. Callers own the frame parser and must never execute streamed data. */
  function vbStream(path, opts) {
    opts = opts || {};
    var base = window.vbResolveBase ? window.vbResolveBase() : '';
    var headers = Object.assign(
      {}, window.vbHeaders ? window.vbHeaders(opts.headers || {}) : {});
    if (opts.accept) headers['Accept'] = opts.accept;
    var ctrl = opts.signal || new AbortController();
    return fetch(base + path, { headers: headers, signal: ctrl.signal })
      .then(function (res) {
        if (res.status === 401) {
          if (window.vbPairHint) window.vbPairHint();
          return { ok: false, status: 401, authRequired: true, reader: null,
                   cancel: noop };
        }
        if (!res.ok || !res.body) {
          return { ok: false, status: res.status, authRequired: false,
                   error: 'http-' + res.status, reader: null, cancel: noop };
        }
        return { ok: true, status: res.status, authRequired: false,
                 reader: res.body.getReader(),
                 cancel: function () { try { ctrl.abort(); } catch (e) {} } };
      })
      .catch(function () {
        return { ok: false, status: 0, authRequired: false, error: 'network',
                 reader: null, cancel: noop };
      });
  }

  window.vbResolveBase = vbResolveBase;
  window.vbClear = vbClear;
  window.vbAppend = vbAppend;
  window.vbBuild = vbBuild;
  window.vbStatus = vbStatus;
  window.vbApiMessage = vbApiMessage;
  window.vbAnnounce = vbAnnounce;
  window.vbStream = vbStream;
})();