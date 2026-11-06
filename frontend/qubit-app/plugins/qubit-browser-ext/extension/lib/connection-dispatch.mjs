import { normalizeGatewayMode, normalizeGatewayUrl } from './common.mjs';
import {
  CONNECTION_TRANSPORTS,
  migrateConnectionSettings,
  normalizeConnectionMode,
} from './connection-modes.mjs';

export const CONNECTION_ACTIONS = Object.freeze({
  LOCAL_API_PAIR_OR_CONNECT: 'local-api-pair-or-connect',
  REMOTE_API_CONNECT: 'remote-api-connect',
  REMOTE_DASHBOARD_ATTACH: 'remote-dashboard-attach',
  CLOUD_ACTIVE_TAB_ATTACH: 'cloud-active-tab-attach',
});

export function connectionActionForSettings(input = {}) {
  const settings = migrateConnectionSettings(input);
  if (settings.connectionMode === 'cloud') return CONNECTION_ACTIONS.CLOUD_ACTIVE_TAB_ATTACH;
  if (settings.connectionMode === 'local') return CONNECTION_ACTIONS.LOCAL_API_PAIR_OR_CONNECT;
  if (settings.connectionTransport === CONNECTION_TRANSPORTS.REMOTE_API) {
    return CONNECTION_ACTIONS.REMOTE_API_CONNECT;
  }
  return CONNECTION_ACTIONS.REMOTE_DASHBOARD_ATTACH;
}

// The gateway URL that a Connect click should use. The settings form can hold a
// URL the user typed but has not saved yet, and Connect must use what the panel
// shows. Only the URL is taken: a changed connection or gateway mode stays
// pending until Save, so an edit to either mode skips the adoption.
export function unsavedGatewayUrlForConnect({
  savedUrl = '',
  typedUrl = '',
  savedConnectionMode = '',
  formConnectionMode = '',
  savedGatewayMode = '',
  formGatewayMode = '',
} = {}) {
  const typed = String(typedUrl || '').trim();
  if (!typed) return null;
  const connectionMode = normalizeConnectionMode(savedConnectionMode);
  if (connectionMode === 'cloud') return null;
  if (normalizeConnectionMode(formConnectionMode) !== connectionMode) return null;
  if (normalizeGatewayMode(formGatewayMode) !== normalizeGatewayMode(savedGatewayMode)) return null;
  const nextUrl = normalizeGatewayUrl(typed);
  return nextUrl === normalizeGatewayUrl(savedUrl) ? null : nextUrl;
}
