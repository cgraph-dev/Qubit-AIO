/* Narrow same-origin route adapter for the vendored Habblaud client.
 *
 * Habblaud owns a complete same-origin app and uses literal /api/* requests.
 * Keep those requests inside its iframe; never rewrite generic /api calls in the
 * Qubit dashboard. Only safe read routes are answered by Qubit's office API.
 *
 * WHY THE TOKEN IS ATTACHED HERE
 * -----------------------------
 * The vendored client talks to `/api/access` with `credentials: 'same-origin'`
 * and NO token header — it was written against a server with no auth. Qubit
 * gates every compat route behind `_qubit_token_ok`, so the rewritten request
 * (`/agent/api/office/habblaud/access`) came back 401 and the client ran
 * `location.assign('/login')`, landing the iframe on `/sessions` — a blank
 * office that every text assertion still called "ready". The bridge is the one
 * place that knows both the rewrite and the host session, so it attaches
 * `X-Qubit-Session-Token` from the document's injected global. A token already
 * present on the request wins (never overwritten), so a caller that
 * authenticated itself keeps its own credential.
 */
type BridgeState = {
  active: boolean;
  originalFetch: typeof fetch;
  bridge: typeof fetch | null;
};
const activeBridges = new WeakMap<Window, BridgeState>();

export function installOfficeApiBridge(
  win: Window,
  opts: { apiPrefix?: string } = {},
): () => void {
  const apiPrefix = opts.apiPrefix ?? (win as Window & { __QUBIT_API_PREFIX__?: string }).__QUBIT_API_PREFIX__ ?? "/agent";
  const prefix = `${apiPrefix.replace(/\/+$/, "")}/api/office/habblaud/`;
  const previousFetch = win.fetch;
  const previousState = activeBridges.get(win);
  const originalFetch = previousState?.originalFetch ?? previousFetch;
  const priorBridge = previousState?.bridge;
  if (previousState) previousState.active = false;
  const state: BridgeState = { active: true, originalFetch, bridge: null };
  activeBridges.set(win, state);
  const sessionToken = (): string => {
    const own = (win as { __QUBIT_SESSION_TOKEN__?: string }).__QUBIT_SESSION_TOKEN__;
    if (own) return own;
    try {
      const parent = win.parent as unknown as { __QUBIT_SESSION_TOKEN__?: string } | null;
      return (parent && parent.__QUBIT_SESSION_TOKEN__) || "";
    } catch {
      return "";
    }
  };
  const bridge: typeof win.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (!state.active || activeBridges.get(win) !== state) return originalFetch(input, init);
    try {
      const probe = new Request(input, init);
      const probeUrl = new URL(probe.url, win.location.href);
      if (probeUrl.origin === win.location.origin && probeUrl.pathname.startsWith("/api/")) {
        const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
        if (method !== "GET") {
          return Promise.resolve(new Response(JSON.stringify({
            error: "This office capability is unavailable in Qubit Office.",
            capability: probeUrl.pathname,
            disabled: true,
          }), { status: 501, headers: { "Content-Type": "application/json" } }));
        }
      }
    } catch {
      // Preserve native fetch behavior for malformed input.
    }
    let request: Request;
    try {
      request = new Request(input, init);
    } catch {
      return originalFetch(input, init);
    }
    const url = new URL(request.url, win.location.href);
    if (url.origin !== win.location.origin || !url.pathname.startsWith("/api/")) {
      return originalFetch(input, init);
    }
    const method = request.method.toUpperCase();
    const path = url.pathname;
    let target: string | null = null;
    if (method === "GET" && path === "/api/access") {
      target = `${prefix}access`;
    } else if (method === "GET" && path === "/api/sessions/recent") {
      target = `${prefix}sessions/recent`;
    } else if (method === "GET" && path === "/api/timeline/days") {
      target = `${prefix}timeline/days`;
    } else if (method === "GET" && path.startsWith("/api/timeline/")
               && path !== "/api/timeline/days") {
      const day = path.slice("/api/timeline/".length);
      target = `${prefix}timeline/${encodeURIComponent(decodeURIComponent(day))}`;
    } else if (method === "GET" && path === "/api/stats/days") {
      target = `${prefix}stats/days`;
    } else if (method === "GET" && path === "/api/stats") {
      target = `${prefix}stats`;
    } else if (method === "GET" && path.startsWith("/api/agents/")
               && path.slice("/api/agents/".length).indexOf("/") < 0) {
      const id = path.slice("/api/agents/".length);
      target = `${prefix}agents/${encodeURIComponent(decodeURIComponent(id))}`;
    }
    if (!target) {
      if (path.startsWith("/api/")) {
        return Promise.resolve(new Response(JSON.stringify({
          error: "This office capability is unavailable in Qubit Office.",
          capability: path,
          disabled: true,
        }), { status: 501, headers: { "Content-Type": "application/json" } }));
      }
      return originalFetch(input, init);
    }
    const headers = new Headers(request.headers);
    const token = sessionToken();
    if (token && !headers.has("X-Qubit-Session-Token")) {
      headers.set("X-Qubit-Session-Token", token);
    }
    const rewritten = new URL(target, win.location.href);
    rewritten.search = url.search;
    return originalFetch(new Request(rewritten, {
      method: "GET",
      credentials: "same-origin",
      headers,
      signal: request.signal,
    }));
  };
  const restore = () => {
    state.active = false;
    if (activeBridges.get(win) === state) activeBridges.delete(win);
    if (win.fetch === bridge) win.fetch = priorBridge ?? originalFetch;
  };
  state.bridge = bridge;
  win.fetch = bridge;
  return restore;
}
