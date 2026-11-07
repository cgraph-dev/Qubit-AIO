// The gateway's Qubit Web hand-off pages on this computer (`/webapp/launch`, `/webapp/window`) are plain pages that
// show for a moment while a new Qubit Web tab signs in. This content script covers them before they paint
// (qubit-web-launch.css), so a Qubit Web tab never shows one as it is:
//   - the first Qubit Web tab in a run of the gateway gets the "Opening Qubit Web" design (the extension's own
//     opening page, framed over the hand-off page);
//   - later ones get Qubit Web's own background, so the tab reads as Qubit Web starting.
// The background worker says which (lib/qubit-web-opening.mjs, handleLaunchLook). If the gateway puts up a message
// instead of moving on (a link that expired, a browser that will not keep the sign-in), the cover steps aside so the
// message can be read.
// It never reads the page's address fragment (where the ticket is), its storage or its script. It only notices
// whether the gateway's status line still says the page is opening.
(() => {
  const root = document.documentElement;
  if (!root || !['127.0.0.1', 'localhost'].includes(location.hostname)) return;
  const kind = /\/webapp\/launch$/.test(location.pathname) ? 'launch' : /\/webapp\/window$/.test(location.pathname) ? 'window' : '';
  if (!kind) return;
  const api = globalThis.qubitBrowserApi;
  const COVER = 'qubitBrowserCover';
  root.dataset[COVER] = 'quiet';
  let frame = null;

  const stepAside = () => {
    root.dataset[COVER] = 'notice';
    frame?.remove();
    frame = null;
  };

  // The gateway's opening lines. Any other text in the status line is a message for the person.
  const opening = (text) => /^(?:Opening Qubit|Authorizing this Qubit window)/.test(String(text || '').trim());
  const watchStatus = () => {
    const status = document.getElementById('status');
    if (!status) return;
    if (!opening(status.textContent)) {
      stepAside();
      return;
    }
    new MutationObserver(() => {
      if (!opening(status.textContent)) stepAside();
    }).observe(status, { childList: true, characterData: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watchStatus, { once: true });
  else watchStatus();

  if (kind !== 'launch' || typeof api?.runtime?.sendMessage !== 'function') return;
  Promise.resolve(api.runtime.sendMessage({ type: 'QUBIT_WEB_LAUNCH_LOOK' }))
    .then((answer) => {
      if (answer?.look !== 'page' || root.dataset[COVER] !== 'quiet') return;
      frame = document.createElement('iframe');
      frame.className = 'qubit-browser-cover-frame';
      frame.title = 'Qubit Web';
      frame.src = `${api.runtime.getURL('qubit-web-opening.html')}?origin=${encodeURIComponent(location.origin)}`;
      root.append(frame);
      root.dataset[COVER] = 'page';
    })
    .catch(() => {
      /* no answer: the quiet cover stays */
    });
})();
