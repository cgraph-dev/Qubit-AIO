/* Mount the vendored Habblaud client inside the Qubit Office plugin.
 *
 * WHY AN IFRAME AND NOT A DIRECT MOUNT
 * ------------------------------------
 * The client is a complete application: it owns `document.documentElement.lang`,
 * sets meta tags, writes to `localStorage` under its own keys, installs global
 * keyboard handlers, and fetches `/api/*` with `credentials: 'same-origin'`. It
 * was written to BE the page.
 *
 * Mounting it inline into the dashboard would mean it fights the host for the
 * document, the routing and the keyboard. An iframe gives it the page it
 * expects, inside a document it fully owns, while the dashboard keeps its own.
 * That is the difference between embedding an app and corrupting two.
 *
 * WHY THE PREFIX MUST BE DERIVED, NOT HARDCODED
 * --------------------------------------------
 * `QUBIT_BASE_PATH` is not always empty — the dashboard can be served under a
 * subpath. The plugin is served from
 * `${QUBIT_BASE_PATH}/dashboard-plugins/qubit-office/habblaud/`, so the iframe
 * src has to be built from the same base the host is using. A hardcoded
 * `/dashboard-plugins/...` works at the root and 404s everywhere else.
 */

/** The plugin's served directory, relative to the dashboard base path. */
export const OFFICE_APP_PATH = "/plugin-apps/qubit-office/habblaud/";

/** Where the client expects its API. Qubit serves the office API under
 *  `/agent/api/...`; the client asks for `/api/...`. See `api-bridge`. */
export const CLIENT_API_PREFIX = "/api/";
export const QUBIT_API_PREFIX = "/agent/api/";

/**
 * Build the URL the iframe should load.
 *
 * `base` is the dashboard's own base path (empty at the root). The trailing
 * slash is enforced so a relative `bundle/...` inside the app resolves against
 * the app directory rather than its parent.
 */
export function officeAppUrl(base = ""): string {
  const b = base.endsWith("/") ? base.slice(0, -1) : base;
  return `${b}${OFFICE_APP_PATH}`;
}

/**
 * The query the client needs to run against a host it was not written for.
 *
 * `mock=1` is the client's OWN switch (client/src/app.ts) for simulated data.
 * We do not set it by default: an office showing invented agents is worse than
 * an office showing none. It is exposed so a broken API binding can be
 * distinguished from a broken renderer in one step.
 */
export interface OfficeAppOptions {
  /** Serve the client's built-in demo data instead of a live binding. */
  mock?: boolean;
  /** The dashboard's base path. */
  base?: string;
}

export function officeAppSrc(opts: OfficeAppOptions = {}): string {
  const url = officeAppUrl(opts.base ?? "");
  return opts.mock ? `${url}?mock=1` : url;
}

/** True when a message is one of OURS, so the host can ignore foreign posts. */
export function isOfficeAppMessage(data: unknown): boolean {
  return typeof data === "object" && data !== null
    && (data as { source?: string }).source === "qubit-office-app";
}
