// Sender checks for the background runtime.onMessage router.
//
// Content scripts run inside web pages, so anything a page can steer a content
// script into sending must be treated as page-reachable. Message types that only
// the extension's own pages (side panel, Qubit Web, offscreen/permission pages)
// send are refused unless the sender really is one of those pages.

/**
 * True only when `sender` is one of this extension's own pages.
 *
 * Content scripts always carry `sender.tab` plus the host page's URL, so they
 * never match. An extension page opened in a tab (permission or dictation page)
 * has a tab but an extension-origin URL, so it matches on the URL.
 */
export function isExtensionPageSender(sender, runtimeApi) {
  if (!sender || typeof sender !== 'object') return false;
  const extensionId = String(runtimeApi?.id || '');
  if (!extensionId || sender.id !== extensionId) return false;
  const senderUrl = typeof sender.url === 'string' ? sender.url : '';
  if (senderUrl) {
    // Prefix match on the extension's own base URL (ends with "/"), so a page
    // at https://chrome-extension-id.example/ or a lookalike host never matches.
    // Not URL.origin: non-special schemes report "null" outside Chromium.
    let base = '';
    try {
      base = String(runtimeApi.getURL?.('') || '');
    } catch {
      return false;
    }
    return /^[a-z-]+-extension:\/\/[^/]+\/$/i.test(base) && senderUrl.toLowerCase().startsWith(base.toLowerCase());
  }
  return !sender.tab;
}

/** Router refusal shape, identical for every refused type. */
export const FORBIDDEN_SENDER_RESPONSE = Object.freeze({ ok: false, reason: 'forbidden_sender' });
