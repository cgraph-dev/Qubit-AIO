// Bot Mode room @mentions: highlight valid routing handles in rendered message
// content, and build the composer insert for the per-message Reply action.
// No storage, no globals: the DOM and handle list are injected.

// Same character set the room router accepts (groupMembersForTurn), so only
// mentions that actually route get highlighted.
const HANDLE = /^[a-z0-9_-]+$/i;
const SKIP = new Set(['CODE', 'PRE', 'A', 'SCRIPT', 'STYLE', 'TEXTAREA', 'BUTTON']);
const escapeRegExp = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function mentionHandles(members = []) {
  const seen = new Map();
  const add = (handle, owner) => {
    const value = String(handle || '').trim();
    if (!HANDLE.test(value)) return;
    const key = value.toLowerCase();
    if (!seen.has(key)) seen.set(key, { handle: value, owner: owner || '' });
  };
  for (const member of Array.isArray(members) ? members : []) {
    add(member?.name, member?.name);
    add(member?.title, member?.name);
  }
  add('everyone', '');
  return [...seen.values()];
}

export function buildMentionPattern(handles = []) {
  const list = handles.map((entry) => entry.handle).sort((a, b) => b.length - a.length);
  if (!list.length) return null;
  // Not preceded by a word char, dot, @ or dash (keeps emails and a@b@c inert);
  // not followed by more handle characters (@Lux must not match @Luxord).
  return new RegExp(`(^|[^A-Za-z0-9_.@-])@(${list.map(escapeRegExp).join('|')})(?![A-Za-z0-9_-])`, 'gi');
}

export function highlightMentions(root, { document = globalThis.document, members = [], inkFor } = {}) {
  if (!root?.querySelectorAll || !document) return 0;
  const handles = mentionHandles(members);
  const pattern = buildMentionPattern(handles);
  if (!pattern) return 0;
  const byKey = new Map(handles.map((entry) => [entry.handle.toLowerCase(), entry]));
  const texts = [];
  const walker = document.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.nodeValue || !node.nodeValue.includes('@')) continue;
    let skip = false;
    for (let el = node.parentNode; el && el !== root; el = el.parentNode) {
      if (SKIP.has(el.nodeName) || el.classList?.contains?.('room-mention')) { skip = true; break; }
    }
    if (!skip) texts.push(node);
  }
  let count = 0;
  for (const node of texts) {
    const text = node.nodeValue;
    pattern.lastIndex = 0;
    let last = 0;
    let match;
    let fragment = null;
    while ((match = pattern.exec(text))) {
      const start = match.index + match[1].length;
      fragment ||= document.createDocumentFragment();
      if (start > last) fragment.append(document.createTextNode(text.slice(last, start)));
      const entry = byKey.get(match[2].toLowerCase());
      const span = document.createElement('span');
      span.className = 'room-mention';
      span.setAttribute('data-mention', entry?.owner || 'everyone');
      const ink = entry?.owner ? inkFor?.(entry.owner) : '';
      if (ink) span.style.setProperty('--mention-ink', ink);
      span.textContent = `@${match[2]}`;
      fragment.append(span);
      last = start + match[2].length + 1;
      count += 1;
    }
    if (!fragment) continue;
    if (last < text.length) fragment.append(document.createTextNode(text.slice(last)));
    node.replaceWith(fragment);
  }
  return count;
}

// Insert "@handle " at the caret without disturbing the rest of the draft.
export function insertMention(value = '', selectionStart, selectionEnd, handle = '') {
  const text = String(value ?? '');
  const token = `@${String(handle).replace(/^@/, '')}`;
  const start = Number.isInteger(selectionStart) ? Math.min(Math.max(selectionStart, 0), text.length) : text.length;
  const end = Number.isInteger(selectionEnd) ? Math.min(Math.max(selectionEnd, start), text.length) : start;
  const before = text.slice(0, start);
  const after = text.slice(end);
  const lead = before && !/\s$/.test(before) ? ' ' : '';
  const spaceAfter = Boolean(after) && /^\s/.test(after);
  const tail = spaceAfter ? '' : ' ';
  const next = `${before}${lead}${token}${tail}${after}`;
  // Caret always lands after one separating space, reusing an existing one.
  return { value: next, caret: before.length + lead.length + token.length + 1 };
}
