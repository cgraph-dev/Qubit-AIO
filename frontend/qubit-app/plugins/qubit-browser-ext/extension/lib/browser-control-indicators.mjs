export const BROWSER_CONTROL_INDICATOR_MESSAGE = 'QUBIT_BROWSER_CONTROL_INDICATOR';

function boundedRect(value = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const rect = {
    x: Math.round(Number(source.x)),
    y: Math.round(Number(source.y)),
    width: Math.round(Number(source.width)),
    height: Math.round(Number(source.height)),
  };
  if (!Object.values(rect).every(Number.isFinite) || rect.width <= 0 || rect.height <= 0) return null;
  return rect;
}

async function notify(tabsApi, tabId, phase, action, targetRect = null) {
  if (!tabsApi?.sendMessage || !Number.isInteger(Number(tabId)) || Number(tabId) <= 0) return false;
  try {
    await tabsApi.sendMessage(Number(tabId), {
      type: BROWSER_CONTROL_INDICATOR_MESSAGE,
      phase,
      action: String(action || '').trim(),
      ...(boundedRect(targetRect) ? { targetRect: boundedRect(targetRect) } : {}),
    });
    return true;
  } catch {
    return false;
  }
}

// The overlay's minimum visible time is enforced by the content script, so a
// finished action is never held back for presentation.
export function withBrowserControlIndicator({
  adapter,
  tabsApi,
} = {}) {
  if (!adapter?.contract || typeof adapter.execute !== 'function') throw new TypeError('Browser control adapter is required.');
  return {
    ...adapter,
    async execute(action, args, context = {}) {
      const tabId = Number(context?.scope?.tabId);
      const targetRect = typeof adapter.targetBounds === 'function'
        ? await adapter.targetBounds(context).catch(() => null)
        : null;
      await notify(tabsApi, tabId, 'start', action, targetRect);
      try {
        if (action === 'browser_screenshot') await notify(tabsApi, tabId, 'suspend', action);
        try {
          return await adapter.execute(action, args, context);
        } finally {
          if (action === 'browser_screenshot') await notify(tabsApi, tabId, 'resume', action);
        }
      } finally {
        // Fire and forget: the result must not wait on the content script.
        void notify(tabsApi, tabId, 'finish', action);
      }
    },
  };
}
