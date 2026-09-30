"""Packet 8B: repository-owned, VERSIONED extraction JavaScript.

These are the ONLY expressions ever passed to CDP ``Runtime.evaluate`` by the
monitor. They are static constants: no page/model/message text is interpolated
into them (rule 44), they are bounded (slice caps), and they return a fixed
``{health, rows, count}`` shape that the adapters normalize. Each row carries a
viewport-relative bounding ``rect`` (CSS px) so the adapter can capture a
clipped screenshot of ONLY that row and never the full viewport. Version
changes must bump the adapter ``version`` field.
"""
from __future__ import annotations

GMAIL_EXTRACT_JS = r"""
(() => {
  const vw = window.innerWidth || 0, vh = window.innerHeight || 0;
  const h = {
    url: location.href, title: document.title || '',
    vw: vw, vh: vh,
    hasLogin: !!document.querySelector('form[action*="login"], form[action*="signin"], input[type="password"], [data-testid*="login"]'),
    hasConsent: !!document.querySelector('button[name*="accept"], [aria-label*="accept all"], [data-testid*="consent"]'),
    bodyLen: (document.body ? (document.body.innerText || '') : '').length
  };
  const out = [];
  const rows = Array.from(document.querySelectorAll('table tbody tr[role="row"], table tbody tr.zA'));
  for (const r of rows) {
    if (out.length >= 60) break;
    const link = r.querySelector('a[href*="mail/u"]') || r.querySelector('a[href*="#inbox"]');
    const senderEl = r.querySelector('.yW span, .zF, .gD');
    const subjEl = r.querySelector('.bog, .y6, .bA4');
    const snipEl = r.querySelector('.y2, .y6, .gUb');
    const timeEl = r.querySelector('.xW, .xH, .xY');
    const cls = r.getAttribute('class') || '';
    const href = link ? (link.getAttribute('href') || '') : '';
    const m = href.match(/[?&#]th=([^&?#]+)/);
    const tid = m ? m[1] : '';
    const rc = r.getBoundingClientRect();
    out.push({
      sourceItemId: tid, threadId: tid,
      sender: senderEl ? senderEl.textContent.trim() : '',
      subject: subjEl ? subjEl.textContent.trim() : '',
      preview: snipEl ? snipEl.textContent.trim() : '',
      timeText: timeEl ? timeEl.textContent.trim() : '',
      href: href, unread: /zE/.test(cls) ? true : (cls ? false : null),
      rect: { x: rc.x, y: rc.y, width: rc.width, height: rc.height }
    });
  }
  return { health: h, rows: out, count: out.length };
})()
"""

INSTAGRAM_EXTRACT_JS = r"""
(() => {
  const vw = window.innerWidth || 0, vh = window.innerHeight || 0;
  const href = location.href || '';
  let path = '/';
  try { path = new URL(href).pathname || '/'; } catch (e) { path = '/'; }
  const isDirect = /^\/direct\//.test(path);
  const isDirectInbox = /^\/direct\/inbox\//.test(path);
  const isHome = path === '/' || /^\/(accounts\/|about|explore)/.test(path);
  const h = {
    url: href, title: document.title || '', vw: vw, vh: vh,
    isDirect: isDirect, isDirectInbox: isDirectInbox, isHome: isHome,
    hasLogin: !!document.querySelector('form[action*="login"], input[name="password"], [data-testid*="login"]'),
    hasConsent: !!document.querySelector('button[name*="accept"], [aria-label*="accept all"], [data-testid*="consent"]'),
    bodyLen: (document.body ? (document.body.innerText || '') : '').length
  };
  const out = [];
  const accepted = [];
  const seen = {};
  // Instagram Direct inbox exposes NO semantic/ARIA markers for thread rows.
  // Use a SHORT, TESTED composite layout fallback scoped to the authenticated
  // main column, validated by the bounded text contract
  // "<label> · <N><unit> [Unread]" and a row rect inside the main column.
  // No page text is interpolated; every field is bounded and each thread is
  // accepted once (nested duplicates are skipped).
  const rows = Array.from(document.querySelectorAll('main div[class*="x9f619"][class*="x78zum5"]'));
  for (const r of rows) {
    if (out.length >= 40) break;
    if (accepted.some(function (a) { return a.contains(r); })) continue;
    const text = (r.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length > 220) continue;
    const m = text.match(/^(.*?)\s*(·\s*\d+[smhdw])\s*(Unread)?$/);
    if (!m) continue;
    const label = (m[1] || '').trim().slice(0, 80);
    if (!label) continue;
    const key = label.toLowerCase();
    if (seen[key]) continue;
    seen[key] = true;
    const rc = r.getBoundingClientRect();
    if (rc.width < 120 || rc.width > 800) continue;
    accepted.push(r);
    out.push({
      sender: label, display: label, preview: '',
      timeText: m[2].trim(),
      unread: /Unread/.test(m[3] || '') ? true : null,
      href: 'https://www.instagram.com/direct/inbox/',
      threadId: '',
      rect: { x: rc.x, y: rc.y, width: rc.width, height: rc.height }
    });
  }
  return { health: h, rows: out, count: out.length };
})()
"""

MESSENGER_EXTRACT_JS = r"""
(() => {
  const vw = window.innerWidth || 0, vh = window.innerHeight || 0;
  const h = {
    url: location.href, title: document.title || '',
    vw: vw, vh: vh,
    hasLogin: !!document.querySelector('form[action*="login"], input[type="password"], [name*="login"]'),
    hasConsent: !!document.querySelector('button[name*="accept"], [aria-label*="accept all"], [data-testid*="consent"]'),
    bodyLen: (document.body ? (document.body.innerText || '') : '').length
  };
  const out = [];
  const seen = {};
  const links = Array.from(document.querySelectorAll('a[href^="/t/"]'));
  for (const a of links) {
    if (out.length >= 40) break;
    const href = a.getAttribute('href') || '';
    const m = href.match(/^\/t\/([^/?]+)/);
    if (!m) continue;
    const tid = m[1];
    if (seen[tid]) continue;
    seen[tid] = true;
    const box = a.closest('div[role="listitem"], li') || a.parentElement || a;
    const rc = box.getBoundingClientRect();
    const text = (box.textContent || '').replace(/\s+/g, ' ').trim();
    out.push({ threadId: tid, text: text.slice(0, 240),
               href: 'https://www.messenger.com/t/' + tid,
               rect: { x: rc.x, y: rc.y, width: rc.width, height: rc.height } });
  }
  return { health: h, rows: out, count: out.length };
})()
"""

EXTRACT_VERSIONS = {
    "gmail": "8b-gmail-2",
    "instagram": "8b-instagram-3",
    "messenger": "8b-messenger-2",
}