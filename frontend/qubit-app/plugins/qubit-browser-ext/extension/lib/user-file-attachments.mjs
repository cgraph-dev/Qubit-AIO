import { contextCharLimit } from './browser-context-protocol.mjs';
import { redactSensitiveText } from './redaction.mjs';

// Original user files live in IndexedDB, never as multi-megabyte transcript
// strings. Only file.attach acknowledgments authorize agent-visible paths.
export const FILE_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;
const TEXT_PREVIEW_CHARS = 12_000;
const VIEW_PREVIEW_CHARS = 1_000_000;
const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|xml|yaml|yml|toml|html|htm|css|js|jsx|ts|tsx|mjs|py|log|sql|rs|go|java|c|cpp|h|svg)$/i;
const VIDEO_EXT = /\.(mp4|webm|mov|m4v|mkv|avi)$/i;
const AUDIO_EXT = /\.(mp3|wav|ogg|opus|m4a|flac)$/i;

function attachmentError(key, cause) {
  return Object.assign(new Error(key), { attachmentFailure: true, uiKey: key, cause });
}

export function attachmentSourceKey(settings = {}) {
  return JSON.stringify([settings.gatewayUrl || '', settings.gatewayMode || '', settings.activeProfile || settings.profile || 'default']);
}

export function createUserFileStore({ indexedDB = globalThis.indexedDB } = {}) {
  let database;
  const open = () => {
    if (!indexedDB?.open) return Promise.reject(attachmentError('attachments.file_unavailable'));
    database ||= new Promise((resolve, reject) => {
      const request = indexedDB.open('qubit-user-files-v1', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('files');
        const bindings = request.result.createObjectStore('bindings', { keyPath: 'id' });
        bindings.createIndex('scopeKey', 'scopeKey');
      };
      request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
      request.onerror = () => { database = null; reject(request.error); };
      request.onblocked = () => { database = null; reject(attachmentError('attachments.file_unavailable')); };
    });
    return database;
  };
  const run = async (table, mode, operation) => {
    const db = await open();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(table, mode);
      const request = operation(transaction.objectStore(table));
      let result;
      request.onsuccess = () => { result = request.result; };
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error || request.error);
      transaction.onabort = () => reject(transaction.error || request.error);
    });
  };
  return {
    putFile: (id, value) => run('files', 'readwrite', table => table.put(value, id)),
    getFile: id => run('files', 'readonly', table => table.get(id)),
    putBinding: value => run('bindings', 'readwrite', table => table.put(value)),
    getBindings: scopeKey => run('bindings', 'readonly', table => table.index('scopeKey').getAll(scopeKey)),
  };
}
const defaultStore = createUserFileStore();

export async function createUserFileAttachment(file, { store = defaultStore } = {}) {
  if (!file || !Number.isFinite(file.size) || file.size > FILE_ATTACHMENT_MAX_BYTES) {
    throw attachmentError('attachments.attachment_too_large');
  }
  const name = String(file.name || 'attachment');
  const mimeType = String(file.type || 'application/octet-stream');
  const blobId = globalThis.crypto.randomUUID();
  const isText = mimeType.startsWith('text/') || TEXT_EXT.test(name);
  const fullText = isText ? await file.text() : '';
  const item = {
    id: blobId, blobId, kind: 'file', name, label: name, size: file.size,
    type: mimeType, mimeType, detail: `${mimeType} · ${file.size} bytes`,
    isText, text: fullText.slice(0, TEXT_PREVIEW_CHARS), textTruncated: fullText.length > TEXT_PREVIEW_CHARS,
  };
  await store.putFile(blobId, { blob: file, name, mimeType, size: file.size, createdAt: Date.now() });
  return item;
}

export async function createPageContextAttachment(pageContext, settings = {}, options = {}) {
  if (settings.includePageText === false || pageContext?.restricted || pageContext?.ok === false
    || pageContext?.meta?.siteAdapter?.suppressed === true) return null;
  const text = redactSensitiveText(String(pageContext?.text || ''));
  if (text.length <= contextCharLimit(settings.contextDepth)) return null;
  const partial = Boolean(pageContext?.meta?.extraction?.truncated || pageContext?.extraction?.content?.truncated);
  const attachment = await createUserFileAttachment(new File([text], 'page-context.txt', { type: 'text/plain' }), options);
  // Only the gateway-acknowledged path is sent; avoid duplicating a text preview.
  attachment.text = '';
  attachment.detail = `${partial ? 'Partial' : 'Complete'} captured page text · ${text.length} chars · read the attached file for content beyond the inline excerpt`;
  return attachment;
}

export function normalizeUserFileAttachments(items = []) {
  const seen = new Set();
  return (Array.isArray(items) ? items : []).filter(item => {
    if (!item || item.kind !== 'file') return false;
    const key = item.blobId || item.id || `${item.name || item.label}:${item.localPath || item.text || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map(item => ({ ...item, name: String(item.name || item.label || 'attachment'), label: String(item.label || item.name || 'attachment') }));
}

async function blobDataUrl(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768));
  }
  return `data:${blob.type || 'application/octet-stream'};base64,${globalThis.btoa(binary)}`;
}

export async function stageUserFiles(client, sessionId, items = [], { store = defaultStore, sourceKey = '' } = {}) {
  for (const item of items) {
    if (item?.kind !== 'file') continue;
    if (item.fileAttachedSessionId === sessionId && item.fileAttachSourceKey === sourceKey && item.localPath && item.fileRefText) continue;
    if (!client?.request || !sessionId) throw attachmentError('attachments.upload_unavailable');
    const retained = item.blobId ? await store.getFile(item.blobId) : null;
    if (!retained?.blob) throw attachmentError('attachments.file_unavailable');
    if (retained.blob.size > FILE_ATTACHMENT_MAX_BYTES) throw attachmentError('attachments.attachment_too_large');
    let result;
    try {
      result = await client.request('file.attach', { session_id: sessionId, data_url: await blobDataUrl(retained.blob), name: item.name || item.label });
    } catch (cause) {
      throw attachmentError('attachments.upload_unavailable', cause);
    }
    if (result?.attached !== true || typeof result.path !== 'string' || !result.path
      || typeof result.ref_text !== 'string' || !result.ref_text.startsWith('@file:')) {
      throw attachmentError('attachments.upload_unavailable');
    }
    item.localPath = result.path;
    item.fileRefText = result.ref_text;
    item.fileAttachedSessionId = sessionId;
    item.fileAttachSourceKey = sourceKey;
  }
  return items;
}

export function attachmentFileContext(item = {}) {
  if (item.localPath && item.fileRefText) {
    // References must precede a bounded preview so context budgeting cannot
    // keep the excerpt but silently drop the only complete-file address.
    return `${item.fileRefText}\nGateway file path: ${item.localPath}\nOriginal file: ${item.name || item.label}\n${item.text || ''}`;
  }
  return item.text || item.detail || '';
}

export function fileMessageMatchText(value = '') {
  const raw = String(value || '');
  try {
    const parsed = JSON.parse(raw);
    const text = typeof parsed.human_input === 'string' ? parsed.human_input : parsed.human_input?.text;
    if (typeof text === 'string') return text.trim();
  } catch { /* ordinary message text */ }
  const human = /USER_REQUEST_START\s*([\s\S]*?)\s*USER_REQUEST_END/.exec(raw);
  return (human?.[1] || raw).replace(/\r\n/g, '\n').replace(/\n\n\[(?:ATTACHMENTS|FILE_REFERENCES)\][\s\S]*$/i, '').trim();
}

export async function rememberUserFileAttachments(sourceKey, sessionId, text, items, { store = defaultStore } = {}) {
  const files = normalizeUserFileAttachments(items).filter(item => item.blobId);
  if (!files.length || !sessionId) return;
  const scopeKey = JSON.stringify([sourceKey, sessionId]);
  const id = JSON.stringify([scopeKey, files.map(item => item.blobId)]);
  await store.putBinding({ id, scopeKey, text: fileMessageMatchText(text), files, createdAt: Date.now() });
}

export async function restoreUserFileAttachments(messages, sourceKey, sessionId, { store = defaultStore, displayText = message => fileMessageMatchText(message.content) } = {}) {
  if (!Array.isArray(messages) || !sessionId || !messages.some(message => message.role === 'user')) return messages;
  const bindings = (await store.getBindings(JSON.stringify([sourceKey, sessionId])) || []).sort((a, b) => a.createdAt - b.createdAt);
  if (!bindings.length) return messages;
  const claimed = new Set();
  const restored = [...messages];
  let changed = false;
  for (let index = restored.length - 1; index >= 0; index--) {
    const message = restored[index];
    if (message.role !== 'user') continue;
    const text = fileMessageMatchText(displayText(message));
    const binding = bindings.findLast(row => !claimed.has(row.id) && row.text === text);
    if (!binding) continue;
    claimed.add(binding.id);
    const original = Array.isArray(message.attachments) ? message.attachments : [];
    const ids = new Set(original.map(item => item.blobId).filter(Boolean));
    const missing = binding.files.filter(item => !ids.has(item.blobId));
    if (!missing.length) continue;
    restored[index] = { ...message, attachments: [...original, ...missing] };
    changed = true;
  }
  return changed ? restored : messages;
}

export function appendUserFileAttachments(container, items, { translate = key => key, onOpen, onDownload } = {}) {
  const files = normalizeUserFileAttachments(items);
  if (!files.length || !container) return null;
  const doc = container.ownerDocument;
  const group = doc.createElement('div');
  group.className = 'user-file-attachments';
  for (const item of files) {
    const card = doc.createElement('div');
    card.className = 'user-file-card';
    const badge = doc.createElement('span');
    badge.className = 'user-file-kind';
    badge.textContent = (item.name.split('.').pop() || 'FILE').toUpperCase().slice(0, 5);
    const info = doc.createElement('div');
    info.className = 'user-file-info';
    const name = doc.createElement('strong');
    name.textContent = item.name;
    name.title = item.name;
    const size = doc.createElement('small');
    size.textContent = item.size ? `${Math.ceil(item.size / 1024)} KiB` : translate('ui.file');
    info.append(name, size);
    const actions = doc.createElement('div');
    actions.className = 'user-file-actions';
    for (const [kind, handler] of [['open', onOpen], ['download', onDownload]]) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.dataset.fileAction = kind;
      button.textContent = translate(`ui.${kind}`);
      button.setAttribute('aria-label', `${translate(`ui.${kind}`)}: ${item.name}`);
      if (kind === 'download') button.disabled = !item.blobId;
      button.addEventListener('click', event => { event.stopPropagation(); handler?.(item); });
      actions.append(button);
    }
    card.append(badge, info, actions);
    group.append(card);
  }
  container.append(group);
  return group;
}

export async function downloadUserFileAttachment(item, { store = defaultStore, downloads = globalThis.browser?.downloads || globalThis.chrome?.downloads, urlApi = globalThis.URL } = {}) {
  const retained = item.blobId ? await store.getFile(item.blobId) : null;
  if (!retained?.blob || !downloads?.download) throw attachmentError('attachments.file_unavailable');
  const url = urlApi.createObjectURL(retained.blob);
  try {
    await downloads.download({ url, filename: (item.name || item.label || 'attachment').split(/[\\/]/).pop(), saveAs: true });
  } finally {
    // A download id acknowledges startup, not byte completion. Keep the object
    // URL alive long enough for the native downloader; never persist it.
    setTimeout(() => urlApi.revokeObjectURL(url), 60_000);
  }
}

export async function openUserFileAttachment(item, { store = defaultStore, document = globalThis.document, translate = key => key } = {}) {
  const doc = document;
  const retained = item.blobId ? await store.getFile(item.blobId) : null;
  const dialog = doc.createElement('dialog');
  dialog.className = 'user-file-viewer';
  dialog.setAttribute('aria-label', item.name || item.label);
  const header = doc.createElement('header');
  const title = doc.createElement('strong');
  title.textContent = item.name || item.label;
  const close = doc.createElement('button');
  close.type = 'button';
  close.textContent = translate('ui.close');
  close.addEventListener('click', () => dialog.close?.());
  header.append(title, close);
  const body = doc.createElement('div');
  body.className = 'user-file-viewer-body';
  let objectUrl = '';
  if (retained?.blob && (VIDEO_EXT.test(item.name) || AUDIO_EXT.test(item.name))) {
    const media = doc.createElement(VIDEO_EXT.test(item.name) ? 'video' : 'audio');
    media.controls = true;
    media.preload = 'metadata';
    objectUrl = globalThis.URL.createObjectURL(retained.blob);
    media.src = objectUrl;
    body.append(media);
  } else if (retained?.blob && (item.isText || TEXT_EXT.test(item.name))) {
    const text = await retained.blob.text();
    const preview = doc.createElement('pre');
    preview.textContent = text.slice(0, VIEW_PREVIEW_CHARS);
    body.append(preview);
    if (text.length > VIEW_PREVIEW_CHARS) {
      const note = doc.createElement('p'); note.textContent = translate('attachments.preview_limited'); body.append(note);
    }
  } else {
    const note = doc.createElement('p');
    note.textContent = translate(retained?.blob ? 'attachments.download_to_open' : 'attachments.file_unavailable');
    body.append(note);
    if (!retained?.blob && item.text) {
      const excerpt = doc.createElement('pre'); excerpt.textContent = item.text; body.append(excerpt);
    }
  }
  const download = doc.createElement('button');
  download.type = 'button';
  download.textContent = translate('ui.download');
  download.disabled = !retained?.blob;
  download.addEventListener('click', () => { void downloadUserFileAttachment(item, { store }).catch(() => { download.disabled = true; }); });
  dialog.append(header, body, download);
  doc.body.append(dialog);
  dialog.addEventListener('close', () => { if (objectUrl) globalThis.URL.revokeObjectURL(objectUrl); dialog.remove(); }, { once: true });
  if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
  return dialog;
}
