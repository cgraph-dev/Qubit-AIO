import { buildQubitWebSocketUrl } from "@qubit/shared";

// Qubit serves the dashboard at the root of its host. The Python backend
// injects ``window.__HERMES_BASE_PATH__ = ""`` into index.html (the reader
// stays so a deployment behind ``X-Forwarded-Prefix`` can still address its
// own ``/api/...`` and ``/dashboard-plugins/...`` URLs without a rebuild).
// Empty string means "served at root".
function readBasePath(): string {
  if (typeof window === "undefined") return "";
  const raw = window.__HERMES_BASE_PATH__ ?? "";
  if (!raw) return "";
  // Normalise: ensure leading slash, strip trailing slash.
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

export const QUBIT_BASE_PATH = readBasePath();
const BASE = QUBIT_BASE_PATH;

// Qubit-owned API namespace. The vendored SPA addresses its backend through
// ``/api/<x>``; Qubit mounts that same dispatcher under ``/agent/api/<x>``.
// The server injects ``window.__QUBIT_API_PREFIX__ = "/agent"`` into
// index.html. Rewriting here — the single seam every ``fetchJSON`` /
// ``authedFetch`` / ``buildWsUrl`` call (and the plugin SDK routed through
// them) passes through — keeps each literal ``/api/...`` call site unchanged
// while the wire path is Qubit-owned.
function readApiPrefix(): string {
  if (typeof window === "undefined") return "/agent";
  const raw = window.__QUBIT_API_PREFIX__;
  if (raw === undefined || raw === null) return "/agent";
  if (!raw) return "";
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

export const QUBIT_API_PREFIX = readApiPrefix();

/** Prefix a vendored ``/api/<x>`` endpoint with the Qubit-owned API namespace
 *  (``/agent/api/<x>``). Non-``/api`` paths (auth redirects, plugin asset
 *  URLs) are returned unchanged. */
export function withApiPrefix(url: string): string {
  if (!QUBIT_API_PREFIX) return url;
  if (url === "/api" || url.startsWith("/api/")) {
    return `${QUBIT_API_PREFIX}${url}`;
  }
  return url;
}

import type { DashboardTheme } from "@/themes/types";
import {
  attemptDashboardTokenReloadOnce,
  clearDashboardTokenReloadAttempt,
} from "@/lib/dashboard-auth-reload";

// Ephemeral session token for protected endpoints.
// Injected into index.html by the server — never fetched via API.
declare global {
  interface Window {
    __HERMES_SESSION_TOKEN__?: string;
    __HERMES_BASE_PATH__?: string;
    /** Qubit-owned API namespace injected by the server (``"/agent"``).
     *  Rewrites the vendored ``/api/<x>`` paths via :func:`withApiPrefix`. */
    __QUBIT_API_PREFIX__?: string;
    /** Server-injected flag: ``true`` when the dashboard's OAuth gate is
     * engaged (public bind, no ``--insecure``). Toggles the SPA's
     * WS-upgrade path from legacy ``?token=`` to single-use ``?ticket=``
     * fetched via :func:`getWsTicket`. */
    __HERMES_AUTH_REQUIRED__?: boolean;
  }
}
const SESSION_HEADER = "X-Hermes-Session-Token";

function setSessionHeader(headers: Headers, token: string): void {
  if (!headers.has(SESSION_HEADER)) {
    headers.set(SESSION_HEADER, token);
  }
}

// ── Global management-profile scope ──────────────────────────────────
// The dashboard is a machine-level management surface: one header switcher
// (ProfileProvider in App.tsx) decides which profile the management pages
// read/write, and fetchJSON transparently appends ?profile=<name> to the
// profile-scoped endpoint families below. "" = the dashboard process's own
// profile (legacy behavior). Calls that already carry an explicit profile
// (e.g. ProfileBuilder writes) are left untouched — explicit beats global.
let _managementProfile = "";

export function setManagementProfile(name: string): void {
  _managementProfile = (name || "").trim();
}

export function getManagementProfile(): string {
  return _managementProfile;
}

// Endpoint families that honor ?profile= on the backend (web_server.py
// _profile_scope or explicit per-profile DB opens). Anything else — ops,
// cron (which has its own per-job profile params), profiles themselves — is
// machine-global or self-scoped and must NOT be rewritten.
const PROFILE_SCOPED_PREFIXES = [
  "/api/status",
  "/api/gateway",
  "/api/analytics",
  "/api/skills",
  "/api/tools/toolsets",
  "/api/config",
  "/api/env",
  "/api/mcp",
  "/api/messaging/platforms",
  "/api/messaging/telegram/onboarding",
  "/api/messaging/whatsapp/onboarding",
  // OAuth/account state is profile-owned too: status, login sessions, polling,
  // cancellation, and disconnect must all follow the selected management
  // profile rather than silently targeting the dashboard process's profile.
  "/api/providers/oauth",
  "/api/model/info",
  "/api/model/set",
  "/api/model/auxiliary",
  "/api/model/moa",
  "/api/model/options",
  // A named profile keeps its own pairing whitelist, and its gateway only
  // consults that one — approving into the global store would grant access
  // the running gateway never sees.
  "/api/pairing",
];

function withManagementProfile(url: string): string {
  if (!_managementProfile) return url;
  if (url.includes("profile=")) return url; // explicit param wins
  const path = url.split("?")[0];
  if (!PROFILE_SCOPED_PREFIXES.some((p) => path.startsWith(p))) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}profile=${encodeURIComponent(_managementProfile)}`;
}

// ── Staged-confirmation contract ─────────────────────────────────────
// Qubit's compat backend never fakes a 2xx for a gated mutation: it answers
// 409 with a typed `{state:"NEEDS_CONFIRMATION", code:"CONFIRM_REQUIRED",
// detail, confirmation_id}` envelope. A non-empty `confirmation_id` settles on
// the shared approval journal via `POST /api/ops/confirm`; the direct compat
// writes (skills/cron/toolset/config) settle by re-issuing the same request
// with `confirm: true`. Both paths live here so every caller shares one
// round-trip.
export interface ConfirmEnvelope {
  state?: string;
  code?: string;
  detail?: string;
  speech?: string;
  confirmation_id?: string;
  qubit_counterpart?: string;
}

/** Parse a typed staged-confirmation envelope out of a response body (or the
 *  `"<status>: <body>"` string `fetchJSON` would have thrown). Null when the
 *  payload is not a confirm-gate. */
export function parseConfirmEnvelope(input: string): ConfirmEnvelope | null {
  const at = input.indexOf("{");
  if (at < 0) return null;
  let body: unknown;
  try {
    body = JSON.parse(input.slice(at));
  } catch {
    return null;
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const env = body as ConfirmEnvelope;
  const code = String(env.code || env.state || "");
  // Qubit's confirm-gates answer with several typed spellings: the compat
  // mutations use `CONFIRM_REQUIRED`/`CONFIRMATION_REQUIRED` +
  // `state:"NEEDS_CONFIRMATION"`; the gateway supervisor uses the namespaced
  // `GATEWAY_CONFIRM_REQUIRED` (and `_CONFIRM_DENIAL_CODES` variants) with
  // `state:"CONFIRM_REQUIRED"`. All are the same stage — settle by re-issuing
  // with `confirm: true`. Recognize the family rather than one literal.
  if (
    code === "CONFIRM_REQUIRED" ||
    code === "CONFIRMATION_REQUIRED" ||
    code.endsWith("CONFIRM_REQUIRED") ||
    env.state === "NEEDS_CONFIRMATION" ||
    env.state === "CONFIRM_REQUIRED"
  ) {
    return env;
  }
  return null;
}

/** Raised when the backend stages a mutation for confirmation instead of
 *  executing it. Call {@link ConfirmRequiredError.settle} to settle the stage
 *  (after a human confirms). */
export class ConfirmRequiredError extends Error {
  readonly detail: string;
  readonly confirmationId: string;
  private readonly settleFn: () => Promise<unknown>;

  constructor(
    detail: string,
    confirmationId: string,
    settle: () => Promise<unknown>,
  ) {
    super(`CONFIRM_REQUIRED: ${detail}`);
    this.name = "ConfirmRequiredError";
    this.detail = detail;
    this.confirmationId = confirmationId;
    this.settleFn = settle;
  }

  get hasConfirmationId(): boolean {
    return this.confirmationId.length > 0;
  }

  settle<T = unknown>(): Promise<T> {
    return this.settleFn() as Promise<T>;
  }
}

/** Re-issue the original mutation with the settle flag.
 *
 *  Qubit's compat mutations settle by re-issuing the SAME request carrying
 *  either the staged ``confirmation_id`` (consumed exactly once by
 *  ``_compat_confirm`` / the verified-skills mutation contract) or an explicit
 *  ``confirm: true``. ``POST /api/ops/confirm`` is NOT the settle path for
 *  these stages — the approval journal is consumed per-action. The flag rides
 *  on both the JSON body and the query string, because the compat layer only
 *  parses bodies for POST/PUT/PATCH (so a DELETE needs the query form). */
function reissueToSettle(
  url: string,
  init: RequestInit | undefined,
  confirmationId: string,
): { url: string; init: RequestInit } {
  let body = init?.body;
  const patch: Record<string, unknown> = confirmationId
    ? { confirmation_id: confirmationId }
    : { confirm: true };
  if (typeof body === "string" && body.trim()) {
    try {
      const parsed: unknown = JSON.parse(body);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        Object.assign(parsed as Record<string, unknown>, patch);
        body = JSON.stringify(parsed);
      }
    } catch {
      /* non-JSON body — leave it untouched */
    }
  }
  const query = confirmationId
    ? `confirmation_id=${encodeURIComponent(confirmationId)}`
    : "confirm=true";
  const sep = url.includes("?") ? "&" : "?";
  // A bodyless DELETE (or an unparseable body) still settles via the query.
  return { url: `${url}${sep}${query}`, init: { ...init, body } };
}

/** Result states that mean the settled operation did NOT succeed, even though
 *  the transport returned HTTP 200. The ops-confirm envelope fabricates
 *  `ok:true/state:executed` while carrying the real outcome under
 *  `receipt.result.state` (e.g. `MISSING`/`FAILED`/`REFUSED`). Treating that
 *  as success is the dishonest "confirm says done but nothing changed" path
 *  (AI8 D-5/D-9), so the settle helpers reject it with the real code+detail. */
const FAILED_RESULT_STATES = new Set([
  "FAILED",
  "REFUSED",
  "MISSING",
  "ERROR",
  "UNAVAILABLE",
  "DENIED",
  "CANCELLED",
]);

function rejectFabricatedSuccess(result: unknown): void {
  if (!result || typeof result !== "object") return;
  const env = result as Record<string, unknown>;
  const receipt =
    env.receipt && typeof env.receipt === "object"
      ? (env.receipt as Record<string, unknown>)
      : null;
  const inner =
    receipt?.result && typeof receipt.result === "object"
      ? (receipt.result as Record<string, unknown>)
      : null;
  const innerState =
    typeof inner?.state === "string" ? inner.state.toUpperCase() : "";
  if (innerState && FAILED_RESULT_STATES.has(innerState)) {
    const code = typeof inner?.code === "string" ? inner.code : innerState;
    const detail =
      typeof inner?.detail === "string"
        ? inner.detail
        : typeof env.detail === "string"
          ? env.detail
          : "operation failed";
    throw new Error(`${code}: ${detail}`);
  }
  // A 2xx body that still says `ok:false` is a failure, not a success.
  if (env.ok === false) {
    const code = typeof env.code === "string" ? env.code : "FAILED";
    const detail =
      typeof env.detail === "string" ? env.detail : "operation failed";
    throw new Error(`${code}: ${detail}`);
  }
}

async function settleStagedConfirm<T>(
  url: string,
  init: RequestInit | undefined,
  options: FetchJSONOptions | undefined,
  confirmationId: string,
): Promise<T> {
  // Three stage flavors exist behind the compat layer, keyed by how the stage
  // was minted:
  //   * action stages (``_compat_confirm``: cron/toolset/config/hooks, and
  //     ``verified_skills.mutations``: skill_create/update/hub) consume the
  //     SAME request re-issued with ``confirmation_id``.
  //   * ops-journal stages (``h_skills_toggle`` -> ``OPS.run_op``) consume
  //     ``POST /api/ops/confirm {confirmation_id}``.
  // Re-issue first: it settles the action stages directly, and an ops stage
  // simply re-stages (harmless, TTL-bounded) — after which the ORIGINAL id
  // still settles on the ops journal. The reverse order is unsafe:
  // ``POST /api/ops/confirm`` can consume a verified-skills stage and break
  // its own re-issue path.
  const retry = reissueToSettle(url, init, confirmationId);
  try {
    const result = await fetchJSON<T>(retry.url, retry.init, options);
    rejectFabricatedSuccess(result);
    return result;
  } catch (e) {
    if (!(e instanceof ConfirmRequiredError) || !confirmationId) throw e;
    // The re-issue re-staged: this was an ops-journal entry. Settle the
    // original stage on the shared journal instead.
    const result = await fetchJSON<T>("/api/ops/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation_id: confirmationId }),
    });
    rejectFabricatedSuccess(result);
    return result;
  }
}

function defaultConfirmPrompt(detail: string): boolean {
  if (typeof window === "undefined" || typeof window.confirm !== "function") {
    return false;
  }
  try {
    return window.confirm(detail);
  } catch {
    // jsdom/SSR: confirm() is present but unimplemented.
    return false;
  }
}

/** Lowercase a human name into a ``SkillManifest.skill_id`` that matches the
 *  backend's ``^[a-z][a-z0-9_.-]{2,63}$``. */
function skillIdFromName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^[-_.]+|[-_.]+$/g, "")
    .slice(0, 63);
  const prefixed = /^[a-z]/.test(slug) ? slug : `s-${slug}`;
  return prefixed.length >= 3 ? prefixed : `${prefixed}-skill`;
}

/** First non-empty line of the SKILL.md body with YAML frontmatter stripped —
 *  a truthful, non-fabricated description fallback. */
function skillDescriptionFromContent(content: string): string {
  const body = content.replace(/^---\n[\s\S]*?\n---\n?/, "");
  const line = body
    .split("\n")
    .map((l) => l.replace(/^#+\s*/, "").trim())
    .find((l) => l.length > 0);
  return (line ?? "").slice(0, 200);
}

/** sha256 hex of a UTF-8 string. Requires a secure context (the dashboard's
 *  loopback origin qualifies); refuses rather than fabricating a digest when
 *  WebCrypto is unavailable. */
async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error(
      "CRYPTO_UNAVAILABLE: sha256 is required to author a skill manifest; " +
        "open the dashboard from a secure context (loopback or https).",
    );
  }
  const digest = await subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Build the validated ``SkillManifest`` shape ``skill_create`` reads for a
 *  locally authored skill. All provenance fields are explicit and honest
 *  (source ``dashboard:local``), never guessed from upstream data. */
async function buildLocalSkillManifest(skill: {
  name: string;
  content: string;
  category?: string;
}): Promise<Record<string, unknown>> {
  const skillId = skillIdFromName(skill.name);
  return {
    skill_id: skillId,
    version: "1.0.0",
    name: skill.name.trim() || skillId,
    description:
      skillDescriptionFromContent(skill.content) || "Locally authored skill",
    source: "dashboard:local",
    revision: "1",
    license: "proprietary",
    content_hash: await sha256Hex(skill.content),
    capabilities: ["skill.local"],
    scopes: ["local"],
    tier: "reversible",
    sensitivity: "internal",
    approval_policy: "confirm",
    timeout_s: 30,
    resource_limits: {},
    audit_events: ["skill.install"],
    compatibility: "",
    rollback: "remove the skill",
    // Forward-compat: the compat handler currently drops this, but a
    // locally-authored skill's payload belongs in the manifest.
    instructions: skill.content,
    category: skill.category?.trim() || "qubit",
  };
}

/** Run a mutation, transparently completing the staged-confirmation
 *  round-trip. The supplied prompt (default `window.confirm`) is shown when
 *  the backend stages; declining rethrows the typed
 *  {@link ConfirmRequiredError} so the caller can surface it. */
export async function runMutationWithConfirm<T>(
  call: () => Promise<T>,
  confirmPrompt?: (detail: string) => boolean | Promise<boolean>,
): Promise<T> {
  try {
    return await call();
  } catch (e) {
    if (!(e instanceof ConfirmRequiredError)) throw e;
    const ask = confirmPrompt ?? defaultConfirmPrompt;
    if (!(await ask(e.detail))) throw e;
    return e.settle<T>();
  }
}

/** Settle a staged confirmation by id on the shared approval journal
 *  (`POST /api/ops/confirm`). This is the pending-approvals surface's settle
 *  call: unlike {@link runMutationWithConfirm} it does not re-issue an original
 *  request, so it is only correct for entries that endpoint itself consumes.
 *  A compat-staged mutation settles by re-issuing its own request with the id
 *  (handled transparently by {@link ConfirmRequiredError.settle}). */
export async function settleConfirmation<T = unknown>(
  confirmationId: string,
): Promise<T> {
  const result = await fetchJSON<T>("/api/ops/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
  rejectFabricatedSuccess(result);
  return result;
}

export async function fetchJSON<T>(
  url: string,
  init?: RequestInit,
  options?: FetchJSONOptions,
): Promise<T> {
  url = withManagementProfile(url);
  // Inject the session token into all /api/ requests.
  const headers = new Headers(init?.headers);
  const token = window.__HERMES_SESSION_TOKEN__;
  if (token) {
    setSessionHeader(headers, token);
  }
  const res = await fetch(`${BASE}${withApiPrefix(url)}`, {
    ...init,
    headers,
    // ``credentials: 'include'`` so the cookie-auth path (gated mode) works
    // for any fetch routed through here. Loopback mode is unaffected — the
    // server doesn't read cookies and the legacy session-token header is
    // already attached above.
    credentials: init?.credentials ?? "include",
  });
  if (res.status === 401) {
    // Phase 6: the gated middleware emits a structured envelope so the
    // SPA can full-page-navigate to /login on session expiry. Parse it,
    // and only redirect on the known error codes — domain-level 401s
    // (e.g. "you don't have permission to read this monitor") bubble
    // up as regular errors so callers can handle them.
    let body: { error?: string; login_url?: string } = {};
    try {
      body = await res.clone().json();
    } catch {
      /* non-JSON 401 — let it fall through */
    }
    if (
      (body.error === "unauthenticated" || body.error === "session_expired") &&
      body.login_url
    ) {
      // Preserve where the user was so /auth/callback can land them back
      // after re-auth. The gate's login_url already carries a ``next=``
      // built from the request path, but the SPA may be deep inside a
      // SPA route the gate never saw — e.g. a hash route or a client-side
      // /sessions/<id> deep link. Save the current location as a
      // fallback the post-login handler can read.
      try {
        sessionStorage.setItem(
          "qubit.lastLocation",
          window.location.pathname + window.location.search,
        );
      } catch {
        /* SSR / privacy mode — ignore */
      }
      window.location.assign(body.login_url);
      // Never resolve — the page is about to unload.
      return new Promise<T>(() => {});
    }
    // Loopback mode: ``_SESSION_TOKEN`` rotates on every server restart
    // (``qubit update``, ``qubit gateway restart``, etc.). A tab kept
    // open across the restart holds the OLD token in
    // ``window.__HERMES_SESSION_TOKEN__`` from the previous HTML render,
    // so every fetch returns 401. The HTML is served ``Cache-Control:
    // no-store`` so a reload picks up the freshly-injected token. Trigger
    // that reload once on the first stale-token 401 — gated mode is
    // handled above, so reaching here in gated mode means a real
    // middleware failure that should not reload-loop.
    if (!window.__HERMES_AUTH_REQUIRED__ && !options?.allowUnauthorized) {
      if (attemptDashboardTokenReloadOnce()) {
        return new Promise<T>(() => {});
      }
    }
  }
  if (res.ok) {
    // Clear the stale-token reload guard: a successful 2xx proves the
    // current ``window.__HERMES_SESSION_TOKEN__`` is valid, so the next
    // 401 — if any — should be allowed to trigger its own reload cycle.
    clearDashboardTokenReloadAttempt();
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    // A typed confirm-gate is a normal stage, not a failure: surface it as a
    // ConfirmRequiredError carrying the settle path so callers can complete it.
    if (res.status === 409) {
      const envelope = parseConfirmEnvelope(text);
      if (envelope) {
        const confirmationId = String(envelope.confirmation_id || "");
        const detail = String(
          envelope.detail ||
            envelope.speech ||
            "This action needs confirmation.",
        );
        throw new ConfirmRequiredError(detail, confirmationId, () =>
          settleStagedConfirm(url, init, options, confirmationId),
        );
      }
    }
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

/** Encode a plugin registry key for URL paths (preserves `/` segment separators). */
function pluginPath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

/**
 * Fetch a single-use ticket for a WebSocket upgrade in gated mode.
 *
 * The dashboard's gated-mode WS auth (``qubit_cli.web_server._ws_auth_ok``)
 * rejects the legacy ``?token=<_SESSION_TOKEN>`` path and only accepts
 * ``?ticket=<minted>`` consumed against the in-memory ticket store. Browsers
 * can't set ``Authorization`` on a WS upgrade, so this round-trip via the
 * authenticated REST endpoint is the bridge from cookie auth to WS auth.
 *
 * Tickets are single-use and TTL=30s — every WS connect attempt must
 * fetch a fresh ticket.
 */
export async function getWsTicket(): Promise<{ ticket: string; ttl_seconds: number }> {
  const res = await fetch(`${BASE}${withApiPrefix("/api/auth/ws-ticket")}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`/api/auth/ws-ticket: HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Resolve the auth query-param pair (``[name, value]``) for a WebSocket
 * connect. In gated mode mints a fresh single-use ticket; in loopback
 * mode returns the injected session token.
 */
export async function buildWsAuthParam(): Promise<[string, string]> {
  if (window.__HERMES_AUTH_REQUIRED__) {
    const { ticket } = await getWsTicket();
    return ["ticket", ticket];
  }
  const token = window.__HERMES_SESSION_TOKEN__ ?? "";
  return ["token", token];
}

/**
 * Authenticated ``fetch`` for dashboard ``/api/...`` requests that aren't
 * plain JSON — file uploads (``FormData``), binary downloads (blobs), etc.
 * Mirrors ``fetchJSON``'s auth handling but returns the raw ``Response`` so
 * the caller can read ``.blob()`` / ``.formData()`` / stream it.
 *
 * Auth, in both modes, exactly as ``fetchJSON`` does it:
 *  - loopback / ``--insecure``: attach the ``X-Hermes-Session-Token`` header.
 *  - gated OAuth: no token header (it's absent by design); the
 *    ``hermes_session_at`` cookie rides along via ``credentials: 'include'``.
 *
 * Unlike ``fetchJSON`` this does NOT parse the body, does NOT throw on
 * non-2xx (the caller decides — a 404 on a download is meaningful), and
 * does NOT run the global 401 → /login redirect (binary endpoints aren't
 * navigation targets). Callers that want the redirect behaviour should use
 * ``fetchJSON``.
 */
export async function authedFetch(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = window.__HERMES_SESSION_TOKEN__;
  if (token) {
    setSessionHeader(headers, token);
  }
  return fetch(`${BASE}${withApiPrefix(url)}`, {
    ...init,
    headers,
    credentials: init?.credentials ?? "include",
  });
}

/**
 * Build an absolute ``ws(s)://`` URL for a dashboard WebSocket endpoint,
 * with the correct auth query param appended for the active mode (fresh
 * single-use ``ticket`` in gated mode, ``token`` in loopback). Plugins and
 * the SPA should use this instead of hand-assembling a WS URL + reading
 * ``window.__HERMES_SESSION_TOKEN__`` directly, so the gated-mode ticket
 * path can never be forgotten.
 *
 * ``path`` is the dashboard-relative path (e.g.
 * ``"/api/plugins/kanban/events"``); the base-path prefix and host are
 * applied here. Extra query params can be supplied via ``params`` and are
 * merged before the auth param.
 */
export async function buildWsUrl(
  path: string,
  params?: Record<string, string>,
): Promise<string> {
  return buildQubitWebSocketUrl({
    authParam: await buildWsAuthParam(),
    basePath: BASE,
    params,
    path: withApiPrefix(path),
  });
}

/** Build a ``?profile=<name>`` query suffix, or "" when unset.
 *
 * Used by the skills/toolsets endpoints so the dashboard can manage a
 * profile other than the one the server process runs under. */
function profileQuery(profile?: string): string {
  return profile ? `?profile=${encodeURIComponent(profile)}` : "";
}

function appendProfileParam(url: string, profile?: string): string {
  if (!profile || url.includes("profile=")) return url;
  return `${url}${url.includes("?") ? "&" : "?"}profile=${encodeURIComponent(profile)}`;
}

function appendQueryParam(url: string, key: string, value?: string): string {
  if (!value) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${key}=${encodeURIComponent(value)}`;
}

export interface SessionQueryOptions {
  profile?: string;
  order?: "created" | "recent";
  source?: string | null;
  sources?: string[];
  excludeSources?: string[];
}

function normalizeSessionQueryOptions(
  profileOrOptions?: string | SessionQueryOptions,
  order: "created" | "recent" = "created",
): SessionQueryOptions {
  if (typeof profileOrOptions === "string") {
    return { profile: profileOrOptions, order };
  }
  return {
    profile: getManagementProfile(),
    order,
    ...(profileOrOptions ?? {}),
  };
}

function appendSessionFilters(url: string, options: SessionQueryOptions): string {
  let next = url;
  next = appendQueryParam(next, "source", options.source ?? undefined);
  if (options.sources && options.sources.length > 0) {
    next = appendQueryParam(next, "sources", options.sources.join(","));
  }
  if (options.excludeSources && options.excludeSources.length > 0) {
    next = appendQueryParam(next, "exclude_sources", options.excludeSources.join(","));
  }
  return appendProfileParam(next, options.profile);
}

export interface WsTicketGrant {
  ticket: string;
  expires_in: number;
  scope: string;
  profile_id?: string;
  provider?: string;
  model?: string;
  context_tokens?: number;
  min_context_tokens?: number;
  /** Cross-check provenance for the declared context window (non-secret). */
  context_verification?: "when_available" | "required" | "declared";
  context_window_source?: string;
  context_verified?: boolean;
  real_context_tokens?: number;
}

/** One model profile as the `/chat` picker sees it (non-secret). */
export interface TerminalProfileInfo {
  id: string;
  kind: string;
  provider: string;
  model: string;
  locality: string;
  enabled: boolean;
  default: boolean;
  context_tokens: number;
  min_context_tokens: number;
  meets_floor: boolean;
  code: string;
  has_credential: boolean;
}

/** Typed `/chat` terminal refusal (e.g. context floor / unverified window). */
export class WsTicketError extends Error {
  readonly code: string;
  readonly detail: string;
  constructor(code: string, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "WsTicketError";
    this.code = code;
    this.detail = detail;
  }
}

/** Typed model-assignment failure. Qubit routes the "main model" through the
 *  real model-profile registry (`GET /api/terminal/profiles` +
 *  `PUT /api/terminal/profile`); a provider/model pair with no registered
 *  profile is a typed refusal, never a fabricated success. Auxiliary task
 *  pins are not offered by the compat layer. */
export class ModelAssignmentError extends Error {
  readonly code: string;
  readonly detail: string;
  constructor(code: string, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ModelAssignmentError";
    this.code = code;
    this.detail = detail;
  }
}

/** Match a provider/model pair against the real model-profile registry. The
 *  provider is optional (a model-only match is accepted); an empty model never
 *  matches. Kept pure so the resolution is unit-testable. */
export function resolveModelProfile(
  profiles: TerminalProfileInfo[],
  provider: string,
  model: string,
): TerminalProfileInfo | undefined {
  const wantedModel = (model || "").trim();
  if (!wantedModel) return undefined;
  const wantedProvider = (provider || "").trim().toLowerCase();
  return profiles.find(
    (profile) =>
      profile.model === wantedModel &&
      (wantedProvider === "" ||
        profile.provider.trim().toLowerCase() === wantedProvider),
  );
}

export const api = {
  buildWsUrl,
  /**
   * Mint a short-lived, single-use, scoped PTY ticket for the `/chat`
   * terminal WebSocket. The long-lived session token is never placed on the
   * WebSocket URL; the browser presents this ticket once. The response also
   * carries the non-secret active Qubit model profile so a model switch is
   * visible in the terminal chrome.
   */
  wsTicket: async (
    profileId?: string,
    scope?: "pty" | "rpc" | "stt",
  ): Promise<WsTicketGrant> => {
    const res = await authedFetch("/api/auth/ws-ticket", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(profileId ? { profile: profileId } : {}),
        ...(scope ? { scope } : {}),
      }),
    });
    let body: Record<string, unknown> = {};
    try {
      body = (await res.json()) as Record<string, unknown>;
    } catch {
      body = {};
    }
    if (!res.ok) {
      // The server returns a typed context-floor refusal (409) / policy gate;
      // surface its code+detail so the terminal chrome can explain WHY it
      // refused (never a bare HTTP status).
      const code = String(body.code ?? `HTTP_${res.status}`);
      const detail = String(
        body.detail ?? body.error ?? `ws-ticket refused (${res.status})`,
      );
      throw new WsTicketError(code, detail);
    }
    if (typeof body.ticket !== "string" || !body.ticket) {
      throw new WsTicketError("WS_TICKET_MISSING", "ws-ticket response had no ticket");
    }
    return body as unknown as WsTicketGrant;
  },
  /**
   * WebSocket URL for the authenticated streaming-STT socket
   * (`/api/audio/stt/ws`). Mints a short-lived, single-use, scoped (`stt`)
   * ticket over the authenticated HTTP route — the long-lived session token is
   * never placed on the WebSocket URL. Used by the local/terminal voice engine
   * for live interim transcription.
   */
  sttWsUrl: async (): Promise<string> => {
    const grant = await api.wsTicket(undefined, "stt");
    return buildQubitWebSocketUrl({
      authParam: ["ticket", grant.ticket],
      basePath: BASE,
      path: withApiPrefix("/api/audio/stt/ws"),
    });
  },
  /**
   * Model profiles eligible for the `/chat` terminal, with the per-profile
   * context-guard verdict (non-secret; no credential values). Powers the
   * terminal profile picker (`GET /api/terminal/profiles`).
   */
  getTerminalProfiles: async (): Promise<TerminalProfileInfo[]> =>
    fetchJSON<TerminalProfileInfo[]>("/api/terminal/profiles"),
  /**
   * Persist the `/chat` model profile (`PUT /api/terminal/profile`). The
   * shared confirm round-trip settles the staged 409 the same way every
   * other compat mutation does.
   */
  setTerminalProfile: (
    profileId: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(() =>
      fetchJSON<{ ok: boolean; profile_id: string; meets_floor: boolean }>(
        "/api/terminal/profile",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_id: profileId }),
        },
      ),
    confirm,
    ),
  /**
   * The active model profile from the real registry (the one the `/chat`
   * terminal spawns). Derived from `GET /api/terminal/profiles` `default`;
   * `null` when the registry has no default. This is the honest "main model"
   * the Models page shows and switches.
   */
  getActiveTerminalProfile: async (): Promise<TerminalProfileInfo | null> => {
    const profiles = await api.getTerminalProfiles();
    return profiles.find((profile) => profile.default) ?? null;
  },
  /**
   * Transcribe a browser audio recording through the canonical local ASR
   * adapter (`POST /api/audio/transcribe`). Used by the `/qubit-chat` voice
   * input when the selected engine is `local` (or `auto` fell back to local).
   * Returns the real transcript; a missing model is a typed refusal.
   */
  transcribeAudio: async (
    dataUrl: string,
    mimeType?: string,
  ): Promise<{ ok?: boolean; transcript?: string; provider?: string; detail?: string }> =>
    fetchJSON<{ ok?: boolean; transcript?: string; provider?: string; detail?: string }>(
      "/api/audio/transcribe",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data_url: dataUrl,
          ...(mimeType ? { mime_type: mimeType } : {}),
        }),
      },
    ),
  getStatus: () => fetchJSON<StatusResponse>("/api/status"),
  /**
   * Canonical runtime inventory (A43): the backend-authoritative view of
   * product/agent/voice/broker/approvals/providers. Read-only; typed leaves.
   */
  getRuntime: () =>
    fetchJSON<Record<string, unknown>>("/api/runtime"),
  /** A41: canonical agent/worker roster (read-only). */
  getAgents: () =>
    fetchJSON<{
      agents?: Array<{
        id: string;
        kind?: string;
        route?: string;
        state?: string;
        enabled?: boolean;
        model?: string;
      }>;
      worker?: Record<string, unknown>;
      authority?: string;
    }>("/api/qubit/agents"),
  /**
   * Identity probe for the dashboard auth gate (Phase 7).
   *
   * Returns the verified Session as JSON when gated mode is active and a
   * valid cookie is attached. Loopback mode is unaffected — the endpoint
   * still exists but is never useful there (no Session, no cookie). The
   * AuthWidget component swallows 401s from this call: if the gate isn't
   * engaged, /api/auth/me returns 401 and the widget renders nothing.
   *
   * ``allowUnauthorized`` is load-bearing: in loopback mode this endpoint
   * 401s by design, and fetchJSON's default loopback behaviour treats a
   * 401 as a rotated session token and full-page-reloads to pick up a
   * fresh one. Because every *other* dashboard request succeeds (and so
   * clears the one-shot reload guard), that turns this expected 401 into
   * an infinite reload loop. Opting out keeps the 401 a plain throw the
   * widget can catch.
   */
  getAuthMe: () =>
    fetchJSON<AuthMeResponse>("/api/auth/me", undefined, {
      allowUnauthorized: true,
    }),
  logout: () =>
    fetch(withApiPrefix("/api/auth/logout"), {
      method: "POST",
      credentials: "include",
    }).then((r) => {
      // /api/auth/logout returns 302 → /login. Follow that with a full-page
      // navigation rather than letting fetch() opaquely consume the
      // redirect — the SPA needs to leave the protected area.
      window.location.assign("/login");
      return r;
    }),
  getSessions: (
    limit = 20,
    offset = 0,
    profileOrOptions: string | SessionQueryOptions = getManagementProfile(),
    order: "created" | "recent" = "created",
  ) => {
    const options = normalizeSessionQueryOptions(profileOrOptions, order);
    return fetchJSON<PaginatedSessions>(
      appendSessionFilters(
        `/api/sessions?limit=${limit}&offset=${offset}&order=${options.order ?? order}`,
        options,
      ),
    );
  },
  getSessionMessages: async (id: string, profile = getManagementProfile()) => {
    const res = await fetchJSON<SessionMessagesResponse>(
      appendProfileParam(
        `/api/sessions/${encodeURIComponent(id)}/messages?limit=500&order=latest`,
        profile,
      ),
    );
    return {
      ...res,
      messages: (res.messages ?? []).map(normalizeSessionMessage),
    };
  },
  getSessionDetail: (id: string, profile = getManagementProfile()) =>
    fetchJSON<SessionInfo>(
      appendProfileParam(`/api/sessions/${encodeURIComponent(id)}`, profile),
    ),
  getSessionLatestDescendant: (id: string, profile = getManagementProfile()) =>
    fetchJSON<SessionLatestDescendantResponse>(
      appendProfileParam(
        `/api/sessions/${encodeURIComponent(id)}/latest-descendant`,
        profile,
      ),
    ),
  deleteSession: (id: string, profile = getManagementProfile()) =>
    fetchJSON<{ ok: boolean }>(
      appendProfileParam(`/api/sessions/${encodeURIComponent(id)}`, profile),
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        // The confirm dialog is the human confirmation; the backend requires
        // an explicit confirm flag on the delete request.
        body: JSON.stringify({ confirm: true }),
      },
    ),
  getEmptySessionsCount: (profile = getManagementProfile()) =>
    fetchJSON<{ count: number }>(
      appendProfileParam("/api/sessions/empty/count", profile),
    ),
  deleteEmptySessions: (profile = getManagementProfile()) =>
    fetchJSON<{ ok: boolean; deleted: number }>(
      appendProfileParam("/api/sessions/empty", profile),
      {
        method: "DELETE",
      },
    ),
  bulkDeleteSessions: (ids: string[], profile = getManagementProfile()) =>
    fetchJSON<{ ok: boolean; deleted: number }>("/api/sessions/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, profile: profile || undefined }),
    }),
  renameSession: (id: string, title: string, profile = getManagementProfile()) =>
    fetchJSON<{ ok: boolean; title: string }>(
      `/api/sessions/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        // The backend patch handler reads `name`; `title` was silently
        // dropped and returned 400 MALFORMED.
        body: JSON.stringify({ name: title, profile: profile || undefined }),
      },
    ),
  getSessionStats: (profile = getManagementProfile()) =>
    fetchJSON<SessionStoreStats>(appendProfileParam("/api/sessions/stats", profile)),
  exportSessionUrl: (id: string, profile = getManagementProfile()) =>
    withApiPrefix(
      appendProfileParam(`/api/sessions/${encodeURIComponent(id)}/export`, profile),
    ),
  importSessions: (
    sessions: Array<Record<string, unknown>>,
    profile = getManagementProfile(),
  ) =>
    fetchJSON<SessionImportResponse>("/api/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessions, profile: profile || undefined }),
    }),
  pruneSessions: (
    older_than_days: number,
    source?: string,
    profile = getManagementProfile(),
  ) =>
    fetchJSON<{ ok: boolean; removed: number; skipped_open: number }>(
      "/api/sessions/prune",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          older_than_days,
          source,
          profile: profile || undefined,
        }),
      },
    ),
  /**
   * Qubit-native chat-session store (`/chat` authority). Distinct from the
   * receipts `getSessions` store above; the Chat sidebar and transcript use
   * these so a reload restores the real server transcript.
   */
  listChatSessions: (limit = 50, offset = 0, query = "") => {
    const q = query ? `&q=${encodeURIComponent(query)}` : "";
    return fetchJSON<ChatStoreSessionsResponse>(
      `/api/qubit/chat/sessions?limit=${limit}&offset=${offset}${q}`,
    );
  },
  getChatSession: (id: string) =>
    fetchJSON<ChatStoreSessionResponse>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}`,
    ),
  getChatSessionMessages: (id: string, limit = 500) =>
    fetchJSON<ChatStoreMessagesResponse>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}/messages?limit=${limit}&offset=0`,
    ),
  /**
   * Persist a per-session preference (`reasoning`, `name`, `archived`) on
   * the canonical session store (`PATCH /api/qubit/chat/sessions/:id`).
   * The preference is reloaded with the session, so it survives refresh.
   */
  patchChatSession: (id: string, patch: Record<string, unknown>) =>
    fetchJSON<{ ok: boolean; session?: ChatStoreSession }>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      },
    ),
  createChatSession: (name = "") =>
    fetchJSON<ChatStoreCreateResponse>("/api/qubit/chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deleteChatSession: (id: string, confirm = true) =>
    fetchJSON<{ ok: boolean; session?: ChatStoreSession }>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}?confirm=${confirm ? "true" : "false"}`,
      { method: "DELETE" },
    ),
  /**
   * A33/A36: per-session chat artifacts. The backend `chat_stores` routes are
   * the same store the CLI uses; these surface drafts, operator comments, and
   * structured CI details for the active session (read-only).
   */
  getChatSessionDrafts: (id: string, limit = 100) =>
    fetchJSON<ChatDraftsResponse>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}/drafts?limit=${limit}`,
    ),
  getChatSessionComments: (id: string, limit = 100) =>
    fetchJSON<ChatCommentsResponse>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}/comments?limit=${limit}`,
    ),
  getChatSessionCi: (id: string, limit = 100) =>
    fetchJSON<ChatCiResponse>(
      `/api/qubit/chat/sessions/${encodeURIComponent(id)}/ci?limit=${limit}`,
    ),
  /** A35: describe a pasted chat image through the canonical vision backend. */
  describeChatImage: (dataUrl: string, prompt = "") =>
    fetchJSON<{ description?: string; model?: string }>(
      "/api/qubit/chat/vision",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: dataUrl, prompt, confirm: true }),
      },
    ),
  listFiles: (path?: string) => {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return fetchJSON<ManagedFilesResponse>(`/api/files${query}`);
  },
  readFile: (path: string) =>
    fetchJSON<ManagedFileReadResponse>(
      `/api/files/read?path=${encodeURIComponent(path)}`,
    ),
  uploadFile: (path: string, file: File, overwrite = true) => {
    // Ported from the upstream dashboard (web/src/lib/api.ts:543-557): stream
    // the raw bytes as multipart/form-data to /api/files/upload-stream. Do NOT
    // set Content-Type — the browser adds the multipart boundary. The previous
    // path read the file as UTF-8 text (`File.text()`, lossy) and POSTed it as
    // JSON, so a genuinely-binary upload was silently mangled and reported as
    // success. Qubit's compat dispatcher parses the multipart body
    // (voicebridge/webui.py parse_multipart_form), and the handler
    // (qubit_compat.h_files_upload) serves bounded UTF-8 *text* writes through
    // the confined file broker; bytes that do not decode as UTF-8 answer a
    // typed 403 FILES_BINARY refusal, which the caller surfaces honestly. The
    // mutation is confirmation-staged by the broker (409 + confirmation_id):
    // callers wrap this in runMutationWithConfirm so the request is re-issued
    // with the id after human confirmation.
    const form = new FormData();
    form.append("path", path);
    form.append("overwrite", String(overwrite));
    form.append("file", file, file.name);
    return fetchJSON<ManagedFileWriteResponse>("/api/files/upload-stream", {
      method: "POST",
      body: form,
    });
  },
  createDirectory: (path: string) =>
    fetchJSON<ManagedFileWriteResponse>("/api/files/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  deleteFile: (path: string, recursive = false) =>
    fetchJSON<{ ok: boolean; path: string }>("/api/files", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, recursive }),
    }),
  getLogs: (params: {
    file?: string;
    lines?: number;
    level?: string;
    component?: string;
    search?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params.file) qs.set("file", params.file);
    if (params.lines) qs.set("lines", String(params.lines));
    if (params.level && params.level !== "ALL") qs.set("level", params.level);
    if (params.component && params.component !== "all") qs.set("component", params.component);
    // The vendored status router post-filters on a case-insensitive needle
    // (`qubit_cli/web_routers/status.py` get_logs). Only send it when set so
    // the unfiltered tail path is unchanged.
    if (params.search && params.search.trim()) qs.set("search", params.search.trim());
    return fetchJSON<LogsResponse>(`/api/logs?${qs.toString()}`);
  },
  getAnalytics: (days: number, profile = getManagementProfile()) =>
    fetchJSON<AnalyticsResponse>(
      appendProfileParam(`/api/analytics/usage?days=${days}`, profile),
    ),
  getModelsAnalytics: (days: number, profile = getManagementProfile()) =>
    fetchJSON<ModelsAnalyticsResponse>(
      appendProfileParam(`/api/analytics/models?days=${days}`, profile),
    ),
  getConfig: (profile = getManagementProfile()) =>
    fetchJSON<Record<string, unknown>>(appendProfileParam("/api/config", profile)),
  getDefaults: () => fetchJSON<Record<string, unknown>>("/api/config/defaults"),
  getSchema: () => fetchJSON<{ fields: Record<string, unknown>; category_order: string[] }>("/api/config/schema"),
  getModelInfo: (profile = getManagementProfile()) =>
    fetchJSON<ModelInfoResponse>(appendProfileParam("/api/model/info", profile)),
  getModelOptions: (
    profileOrOptions?: string | { profile?: string; refresh?: boolean },
  ) => {
    const profile =
      typeof profileOrOptions === "string"
        ? profileOrOptions
        : profileOrOptions?.profile;
    const refresh =
      typeof profileOrOptions === "object" && !!profileOrOptions.refresh;
    const qs = new URLSearchParams();
    if (profile) qs.set("profile", profile);
    if (refresh) qs.set("refresh", "1");
    // Dashboard surfaces (Models page, profile builder, cron) are
    // management/setup UIs: keep the full provider universe with setup
    // affordances. The endpoint now defaults to the configured subset for
    // desktop chat pickers (#56974), so opt in explicitly here.
    qs.set("include_unconfigured", "1");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchJSON<ModelOptionsResponse>(`/api/model/options${suffix}`);
  },
  getAuxiliaryModels: (profile = getManagementProfile()) =>
    fetchJSON<AuxiliaryModelsResponse>(
      appendProfileParam("/api/model/auxiliary", profile),
    ),
  getMoaModels: () => fetchJSON<MoaConfigResponse>("/api/model/moa"),
  saveMoaModels: (body: MoaConfigResponse) =>
    fetchJSON<MoaConfigResponse & { ok: boolean }>("/api/model/moa", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  /**
   * Assign the "main" model. Posts the provider/model straight to the canonical
   * `POST /api/model/set` (the reference's route: `web/src/lib/api.ts:623`),
   * which resolves the pair into the real runtime model — registering and
   * activating a model profile and mirroring the runtime `config.yaml` model.
   *
   * The previous implementation resolved the pair against
   * `GET /api/terminal/profiles`, whose registry held only the single local
   * default, so every one of the ~54 catalog providers threw
   * `UNKNOWN_MODEL_PROFILE` and could never be selected.
   *
   * Auxiliary task pins are still not offered by the compat layer
   * (`AUX_PIN_REFUSED`); surface that typed refusal instead of a fake success.
   */
  setModelAssignment: async (
    body: ModelAssignmentRequest,
    profile = getManagementProfile(),
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ): Promise<ModelAssignmentResponse> => {
    void confirm;
    const scope = (body.scope || "main").toLowerCase();
    if (scope !== "main") {
      throw new ModelAssignmentError(
        "AUX_PIN_REFUSED",
        "auxiliary task pins are not offered; the main model profile owns routing",
      );
    }
    const res = await fetchJSON<
      ModelAssignmentResponse & { code?: string; detail?: string }
    >(appendProfileParam("/api/model/set", profile), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, scope: "main" }),
    }).catch((err: unknown) => {
      // fetchJSON throws a plain `"<status>: <json>"` Error on a typed 4xx.
      // Re-surface the backend's typed code so callers can discriminate a
      // real refusal from a network failure.
      const message = err instanceof Error ? err.message : String(err);
      const match = message.match(/^\d+:\s*(\{[\s\S]*\})$/);
      if (match) {
        try {
          const envelope = JSON.parse(match[1]) as {
            code?: string;
            detail?: string;
          };
          if (envelope.code) {
            throw new ModelAssignmentError(
              envelope.code,
              envelope.detail || message,
            );
          }
        } catch (parseErr) {
          if (parseErr instanceof ModelAssignmentError) throw parseErr;
        }
      }
      throw err;
    });
    // The expensive-model prompt is a 200 confirm_required envelope (upstream
    // shape); pass it through so the picker shows it and re-calls with
    // confirm_expensive_model=true.
    if (res.confirm_required) return res;
    if (!res.ok) {
      throw new ModelAssignmentError(
        res.code || "MODEL_SET_FAILED",
        res.detail || `could not set ${body.provider}/${body.model}`,
      );
    }
    return res;
  },
  saveConfig: (
    config: Record<string, unknown>,
    profile = getManagementProfile(),
    confirm = false,
    onConfirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    // ``config_save`` stages a typed 409 without ``confirm``; the shared
    // round-trip re-issues with it (an explicit ``confirm=true`` call goes
    // straight through, preserving the legacy signature).
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>(appendProfileParam("/api/config", profile), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(confirm ? { config, confirm: true } : { config }),
        }),
      onConfirm,
    ),
  // Raw YAML config read/write (`/api/config/raw`) is refused by Qubit policy
  // (SECRET_NEVER_SERVED): the reference endpoint serves full plaintext secrets,
  // so no wrapper is offered. The structured ConfigPage form (`PUT /api/config`)
  // is the supported editor; secret values go through the Keys reference store.
  getEnvVars: () => fetchJSON<Record<string, EnvVarInfo>>("/api/env"),
  setEnvVar: (key: string, value: string) =>
    fetchJSON<{ ok: boolean }>("/api/env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      // B-08: env writes are confirm-gated and accept vault/ref references
      // only; confirm the explicit user action so a valid reference settles
      // (a raw secret still returns a typed refusal).
      body: JSON.stringify({ key, value, confirm: true }),
    }),
  deleteEnvVar: (key: string) =>
    fetchJSON<{ ok: boolean }>("/api/env", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, confirm: true }),
    }),
  revealEnvVar: (key: string) =>
    fetchJSON<{ key: string; value: string }>("/api/env/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }),

  // Cron jobs
  getCronJobs: (profile = "all") =>
    fetchJSON<CronJob[]>(`/api/cron/jobs?profile=${encodeURIComponent(profile)}`),
  getCronDeliveryTargets: () =>
    fetchJSON<{ targets: CronDeliveryTarget[] }>("/api/cron/delivery-targets"),
  // ``cron_create`` reads the schedule as ``when`` (or ``schedule.expr``);
  // the editor models it as a display string, so map it and let the shared
  // confirm round-trip re-issue with ``confirm: true``.
  createCronJob: (
    job: CronJobMutation,
    profile = "default",
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<CronJob>(
          `/api/cron/jobs?profile=${encodeURIComponent(profile)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ...job,
              when: job.when ?? job.schedule,
            }),
          },
        ),
      confirm,
    ),
  pauseCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/pause?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  updateCronJob: (
    id: string,
    updates: CronJobMutation,
    profile = "default",
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<CronJob>(
          `/api/cron/jobs/${encodeURIComponent(id)}?profile=${encodeURIComponent(profile)}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            // ``cron_update`` reads the mutation fields at the body root.
            body: JSON.stringify({ ...updates, when: updates.when ?? updates.schedule }),
          },
        ),
      confirm,
    ),
  resumeCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/resume?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  triggerCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/trigger?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  // ``cron_delete`` is confirm-gated; the helper retries with confirm on both
  // the body and the query string (the compat body parser covers DELETE).
  deleteCronJob: (
    id: string,
    profile = "default",
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>(
          `/api/cron/jobs/${encodeURIComponent(id)}?profile=${encodeURIComponent(profile)}`,
          { method: "DELETE" },
        ),
      confirm,
    ),

  // Automation Blueprints — parameterized automation blueprints
  getAutomationBlueprints: () =>
    fetchJSON<{ blueprints: AutomationBlueprint[] }>("/api/cron/blueprints"),
  instantiateAutomationBlueprint: (
    body: { blueprint: string; values: Record<string, string> },
    profile = "default",
  ) =>
    fetchJSON<CronJob>(`/api/cron/blueprints/instantiate?profile=${encodeURIComponent(profile)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // Profiles
  getProfiles: () =>
    fetchJSON<{ profiles: ProfileInfo[] }>("/api/profiles"),
  getActiveProfile: () =>
    fetchJSON<ActiveProfileInfo>("/api/profiles/active"),
  setActiveProfile: (name: string) =>
    fetchJSON<{ ok: boolean; active: string }>("/api/profiles/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  createProfile: (
    body: {
      name: string;
      clone_from?: string | null;
      clone_from_default?: boolean;
      clone_all?: boolean;
      no_skills?: boolean;
      description?: string;
      provider?: string;
      model?: string;
      mcp_servers?: McpServerCreate[];
      keep_skills?: string[];
      hub_skills?: string[];
    },
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    // ``h_profile_create`` stages a typed 409 on the shared journal (real
    // ``confirmation_id``); the shared round-trip re-issues with ``confirm``
    // to execute the principal registration.
    runMutationWithConfirm(
      () =>
        fetchJSON<{
          ok: boolean;
          name: string;
          path: string;
          model_set?: boolean;
          mcp_written?: number;
          skills_disabled?: number;
          hub_installs?: Array<{ identifier: string; pid: number | null }>;
        }>("/api/profiles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      confirm,
    ),
  // ``profile_desc`` (``_profile_field``) is confirm-gated: without ``confirm``
  // it stages a typed 409; the shared round-trip re-issues with it. The handler
  // echoes the stored field (``{ok, field:{key,value}}``), not a fabricated
  // description shape.
  updateProfileDescription: (
    name: string,
    description: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ProfileFieldResponse>(
          `/api/profiles/${encodeURIComponent(name)}/description`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description }),
          },
        ),
      confirm,
    ),
  describeProfileAuto: (name: string, overwrite = true) =>
    fetchJSON<ProfileDescribeAutoResult>(
      `/api/profiles/${encodeURIComponent(name)}/describe-auto`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overwrite }),
      },
    ),
  // ``profile_model`` is confirm-gated and persists into the principal's
  // profile (``B.profile_set``); the round-trip re-issues with ``confirm``.
  setProfileModel: (
    name: string,
    provider: string,
    model: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(async () => {
      const res = await fetchJSON<{
        ok: boolean;
        provider: string;
        model: string;
      }>(`/api/profiles/${encodeURIComponent(name)}/model`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model }),
      });
      // Never claim success when the server reports a different provider than
      // the one that was applied (the old hardcoded ``ollama-local`` bug).
      if (res && res.provider && res.provider !== provider) {
        throw new Error(
          `PROFILE_MODEL_NOT_APPLIED: requested ${provider}/${model}, ` +
            `server reported ${res.provider}/${res.model}`,
        );
      }
      return res;
    }, confirm),
  // ``profile_rename`` is confirm-gated and renames the principal in the SAME
  // registry the list reads; a collision is a typed 400, never a false success.
  renameProfile: (
    name: string,
    newName: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; name: string; path: string }>(
          `/api/profiles/${encodeURIComponent(name)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: newName }),
          },
        ),
      confirm,
    ),
  // ``profile_delete`` is confirm-gated; the round-trip re-issues the DELETE
  // with the settle flag on the query string.
  deleteProfile: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; removed?: boolean }>(
          `/api/profiles/${encodeURIComponent(name)}`,
          { method: "DELETE" },
        ),
      confirm,
    ),
  getProfileSetupCommand: (name: string) =>
    fetchJSON<{ command: string }>(
      `/api/profiles/${encodeURIComponent(name)}/setup-command`,
    ),
  getProfileSoul: (name: string) =>
    fetchJSON<{ content: string; exists: boolean }>(
      `/api/profiles/${encodeURIComponent(name)}/soul`,
    ),
  // ``profile_soul_set`` is confirm-gated like the other profile fields.
  updateProfileSoul: (
    name: string,
    content: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ProfileFieldResponse>(
          `/api/profiles/${encodeURIComponent(name)}/soul`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content }),
          },
        ),
      confirm,
    ),

  // Skills & Toolsets
  //
  // All calls accept an optional ``profile`` so the Skills page can manage
  // any profile's skills/toolsets — not just the one the dashboard process
  // runs under. Omitted/empty profile = the dashboard's own profile.
  getSkills: (profile?: string) =>
    fetchJSON<SkillInfo[]>(`/api/skills${profileQuery(profile)}`),
  // ``h_skills_toggle`` settles on the shared approval journal: the staged 409
  // carries a real confirmation_id, so the round-trip runs through
  // POST /api/ops/confirm (handled by runMutationWithConfirm).
  toggleSkill: (
    name: string,
    enabled: boolean,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>("/api/skills/toggle", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            enabled,
            profile: profile || undefined,
          }),
        }),
      confirm,
    ),
  getSkillContent: (name: string, profile?: string) =>
    fetchJSON<SkillContent>(
      `/api/skills/content?name=${encodeURIComponent(name)}${profile ? `&profile=${encodeURIComponent(profile)}` : ""}`,
    ),
  // ``skill_create`` validates and installs a full ``SkillManifest`` (staged
  // disabled, confirm-gated). The editor only collects name/content/category,
  // so build an honest local manifest here — the payload the compat contract
  // actually reads.
  createSkill: async (
    skill: { name: string; content: string; category?: string },
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) => {
    const manifest = await buildLocalSkillManifest(skill);
    return runMutationWithConfirm(
      () =>
        fetchJSON<SkillWriteResult>("/api/skills", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            manifest,
            profile: profile || undefined,
          }),
        }),
      confirm,
    );
  },
  updateSkillContent: (
    name: string,
    content: string,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<SkillWriteResult>("/api/skills/content", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            content,
            profile: profile || undefined,
          }),
        }),
      confirm,
    ),
  getToolsets: (profile?: string) =>
    fetchJSON<ToolsetInfo[]>(`/api/tools/toolsets${profileQuery(profile)}`),
  toggleToolset: (
    name: string,
    enabled: boolean,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{
          ok: boolean;
          name: string;
          platform: string;
          enabled: boolean;
        }>(`/api/tools/toolsets/${encodeURIComponent(name)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled, profile: profile || undefined }),
        }),
      confirm,
    ),
  getToolsetConfig: (name: string, profile?: string) =>
    fetchJSON<ToolsetConfig>(
      `/api/tools/toolsets/${encodeURIComponent(name)}/config${profileQuery(profile)}`,
    ),
  selectToolsetProvider: (
    name: string,
    provider: string,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; name: string; provider: string }>(
          `/api/tools/toolsets/${encodeURIComponent(name)}/provider`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider, profile: profile || undefined }),
          },
        ),
      confirm,
    ),
  saveToolsetEnv: (
    name: string,
    env: Record<string, string>,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ToolsetEnvResult>(
          `/api/tools/toolsets/${encodeURIComponent(name)}/env`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ env, profile: profile || undefined }),
          },
        ),
      confirm,
    ),
  runToolsetPostSetup: (name: string, key: string, profile?: string) =>
    fetchJSON<ActionResponse & { key: string }>(
      `/api/tools/toolsets/${encodeURIComponent(name)}/post-setup`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, profile: profile || undefined }),
      },
    ),

  // Session search (FTS5)
  searchSessions: (
    q: string,
    profileOrOptions: string | SessionQueryOptions = getManagementProfile(),
  ) => {
    const options = normalizeSessionQueryOptions(profileOrOptions);
    return fetchJSON<SessionSearchResponse>(
      appendSessionFilters(
        `/api/sessions/search?q=${encodeURIComponent(q)}`,
        options,
      ),
    );
  },

  // OAuth provider management
  getOAuthProviders: () =>
    fetchJSON<OAuthProvidersResponse>("/api/providers/oauth"),
  disconnectOAuthProvider: (providerId: string) =>
    fetchJSON<{ ok: boolean; provider: string }>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}`,
      {
        method: "DELETE",
      },
    ),
  startOAuthLogin: (providerId: string) =>
    fetchJSON<OAuthStartResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/start`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    ),
  submitOAuthCode: (providerId: string, sessionId: string, code: string) =>
    fetchJSON<OAuthSubmitResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/submit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, code }),
      },
    ),
  pollOAuthSession: (providerId: string, sessionId: string) =>
    fetchJSON<OAuthPollResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/poll/${encodeURIComponent(sessionId)}`,
    ),
  cancelOAuthSession: (sessionId: string) =>
    fetchJSON<{ ok: boolean }>(
      `/api/providers/oauth/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
      },
    ),

  // Messaging platforms (gateway channels)
  getMessagingPlatforms: () =>
    fetchJSON<MessagingPlatformsResponse>("/api/messaging/platforms"),
  // `msg_update` stages a typed 409 CONFIRM_REQUIRED (shared approval journal)
  // without an explicit confirm. The shared round-trip re-issues the SAME PUT
  // with the staged `confirmation_id` (or settles it on the journal) once the
  // human confirms — so Enable/Disable/Save actually complete instead of
  // toasting a dead 409. `hot_served` is optional (the reference backend
  // reports it; Qubit's compat layer does not).
  updateMessagingPlatform: (
    id: string,
    body: MessagingPlatformUpdate,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; platform: string; hot_served?: boolean }>(
          `/api/messaging/platforms/${encodeURIComponent(id)}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
        ),
      confirm,
    ),
  testMessagingPlatform: (id: string) =>
    fetchJSON<MessagingPlatformTestResult>(
      `/api/messaging/platforms/${encodeURIComponent(id)}/test`,
      { method: "POST" },
    ),
  startTelegramOnboarding: (body: { bot_name?: string }) =>
    fetchJSON<TelegramOnboardingStartResponse>(
      "/api/messaging/telegram/onboarding/start",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  getTelegramOnboardingStatus: (pairingId: string) =>
    fetchJSON<TelegramOnboardingStatusResponse>(
      `/api/messaging/telegram/onboarding/${encodeURIComponent(pairingId)}`,
    ),
  applyTelegramOnboarding: (
    pairingId: string,
    body: { allowed_user_ids: string[]; profile?: string },
  ) =>
    fetchJSON<TelegramOnboardingApplyResponse>(
      `/api/messaging/telegram/onboarding/${encodeURIComponent(pairingId)}/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  cancelTelegramOnboarding: (pairingId: string) =>
    fetchJSON<{ ok: boolean }>(
      `/api/messaging/telegram/onboarding/${encodeURIComponent(pairingId)}`,
      { method: "DELETE" },
    ),
  startWhatsAppOnboarding: (body: {
    mode?: "bot" | "self-chat";
    allowed_users?: string;
  }) =>
    fetchJSON<WhatsAppOnboardingStartResponse>(
      "/api/messaging/whatsapp/onboarding/start",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  getWhatsAppOnboardingStatus: (pairingId: string) =>
    fetchJSON<WhatsAppOnboardingStatusResponse>(
      `/api/messaging/whatsapp/onboarding/${encodeURIComponent(pairingId)}`,
    ),
  applyWhatsAppOnboarding: (
    pairingId: string,
    body: { mode?: "bot" | "self-chat"; allowed_users?: string; profile?: string },
  ) =>
    fetchJSON<WhatsAppOnboardingApplyResponse>(
      `/api/messaging/whatsapp/onboarding/${encodeURIComponent(pairingId)}/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  cancelWhatsAppOnboarding: (pairingId: string) =>
    fetchJSON<{ ok: boolean }>(
      `/api/messaging/whatsapp/onboarding/${encodeURIComponent(pairingId)}`,
      { method: "DELETE" },
    ),

  // Gateway / update actions
  getGatewayStatus: () =>
    fetchJSON<GatewayStatusResponse>("/api/gateway"),
  // Qubit's gateway supervisor confirms-gates every lifecycle transition
  // (start/stop/restart), so a bare POST stages a typed 409
  // GATEWAY_CONFIRM_REQUIRED instead of executing. The shared round-trip
  // re-issues the SAME request with `confirm: true` once the human has
  // confirmed through the caller's ConfirmDialog. The supervisor reads
  // `confirm` from the JSON body (not the query), so the request MUST carry a
  // JSON body for the re-issue to settle — an empty `{}` stages the refusal,
  // then `reissueToSettle` injects `confirm: true`. The optional `confirm`
  // callback defers the human decision; without it the same refuse-on-409
  // behavior is preserved for non-UI callers.
  restartGateway: (confirm?: (detail: string) => boolean | Promise<boolean>) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ActionResponse>("/api/gateway/restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }),
      confirm,
    ),
  updateQubit: () =>
    // The System page gates this behind its own confirm dialog, so the user
    // consent already happened: send confirm=true (the backend refuses with
    // 409 CONFIRM_REQUIRED otherwise) and never run a real update from a
    // test process (the backend test guard returns a typed 503).
    fetchJSON<ActionResponse>("/api/qubit/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    }),
  checkQubitUpdate: (force = false) =>
    fetchJSON<UpdateCheckResponse>(
      `/api/qubit/update/check${force ? "?force=true" : ""}`,
    ),
  getActionStatus: (name: string, lines = 200) =>
    fetchJSON<ActionStatusResponse>(
      `/api/actions/${encodeURIComponent(name)}/status?lines=${lines}`,
    ),

  // Dashboard plugins
  getPlugins: () =>
    fetchJSON<PluginManifestResponse[]>("/api/dashboard/plugins"),
  rescanPlugins: () =>
    fetchJSON<{ ok: boolean; count: number }>("/api/dashboard/plugins/rescan"),

  getPluginsHub: () => fetchJSON<PluginsHubResponse>("/api/dashboard/plugins/hub"),

  // Real local plugin catalog (loadable bundled/dashboard/overlay/user rows
  // with provenance). The upstream remote index is a typed refusal the backend
  // reports; the page renders that refusal rather than a fabricated catalog.
  getPluginCatalog: (params?: {
    q?: string;
    source?: string;
    installed?: boolean;
    enabled?: boolean;
    all?: boolean;
  }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.source) qs.set("source", params.source);
    if (params?.installed) qs.set("installed", "1");
    if (params?.enabled) qs.set("enabled", "1");
    if (params?.all) qs.set("all", "1");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchJSON<PluginCatalogResponse>(
      `/api/dashboard/plugins/catalog${suffix}`,
    );
  },

  getPluginCatalogDetail: (id: string) =>
    fetchJSON<PluginCatalogDetailResponse>(
      `/api/dashboard/plugins/catalog/${pluginPath(id)}`,
    ),

  reloadPlugins: () =>
    fetchJSON<PluginReloadResponse>("/api/dashboard/plugins/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    }),

  installAgentPlugin: (body: AgentPluginInstallRequest) =>
    fetchJSON<AgentPluginInstallResponse>("/api/dashboard/agent-plugins/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // B-08: the backend stages without an explicit confirm; the user already
      // pressed the install control, so confirm the real state change instead
      // of leaving the control unable to complete.
      body: JSON.stringify({ confirm: true, ...body }),
    }),

  // Curated, pinned index install. Sends the entry's curated `pinned_ref` so
  // the backend verifies the pin; a pinned/verified request still goes through
  // the canonical e-stop -> confirmation -> audit path, and the remote source
  // is never fetched (raw remote fetch stays refused).
  installAgentPluginFromIndex: (catalogName: string, ref: string) =>
    fetchJSON<AgentPluginInstallResponse>("/api/dashboard/agent-plugins/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true, catalog_name: catalogName, ref }),
    }),

  enableAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string; unchanged?: boolean }>(
      `/api/dashboard/agent-plugins/${pluginPath(name)}/enable`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      },
    ),

  disableAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string; unchanged?: boolean }>(
      `/api/dashboard/agent-plugins/${pluginPath(name)}/disable`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      },
    ),

  updateAgentPlugin: (name: string) =>
    fetchJSON<AgentPluginUpdateResponse>(
      `/api/dashboard/agent-plugins/${pluginPath(name)}/update`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      },
    ),

  removeAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string }>(
      `/api/dashboard/agent-plugins/${pluginPath(name)}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      },
    ),

  savePluginProviders: (body: PluginProvidersPutRequest) =>
    fetchJSON<{ ok: boolean }>("/api/dashboard/plugin-providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      // B-08: the handler resolves the toolset from `name`; the context-engine
      // control must identify itself or the write is MALFORMED.
      body: JSON.stringify({ name: "context_engine", confirm: true, ...body }),
    }),

  setPluginVisibility: (name: string, hidden: boolean) =>
    fetchJSON<{ ok: boolean; name: string; hidden: boolean }>(
      `/api/dashboard/plugins/${pluginPath(name)}/visibility`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
      },
    ),

  // Dashboard themes
  getThemes: () =>
    fetchJSON<DashboardThemesResponse>("/api/dashboard/themes"),
  setTheme: (name: string) =>
    fetchJSON<{ ok: boolean; theme: string }>("/api/dashboard/theme", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  getFontPref: () =>
    fetchJSON<DashboardFontResponse>("/api/dashboard/font"),
  setFontPref: (font: string) =>
    fetchJSON<{ ok: boolean; font: string }>("/api/dashboard/font", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ font }),
    }),

  // ── Admin: MCP servers ──────────────────────────────────────────────
  getMcpServers: () => fetchJSON<{ servers: McpServer[] }>("/api/mcp/servers"),
  // ``mcp_create`` stages a typed 409 with a real ``confirmation_id`` without
  // ``confirm``; the shared round-trip re-issues the same request to settle it.
  addMcpServer: (
    body: McpServerCreate,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<McpServer>("/api/mcp/servers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      confirm,
    ),
  authMcpServer: (name: string) =>
    fetchJSON<McpOAuthFlow>(
      `/api/mcp/servers/${encodeURIComponent(name)}/auth`,
      { method: "POST" },
    ),
  getMcpOAuthFlow: (flowId: string) =>
    fetchJSON<McpOAuthFlow>(
      `/api/mcp/oauth/flows/${encodeURIComponent(flowId)}`,
    ),
  removeMcpServer: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>(
          `/api/mcp/servers/${encodeURIComponent(name)}`,
          { method: "DELETE" },
        ),
      confirm,
    ),
  testMcpServer: (name: string) =>
    fetchJSON<McpTestResult>(
      `/api/mcp/servers/${encodeURIComponent(name)}/test`,
      { method: "POST" },
    ),
  setMcpServerEnabled: (
    name: string,
    enabled: boolean,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; name: string; enabled: boolean }>(
          `/api/mcp/servers/${encodeURIComponent(name)}/enabled`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled }),
          },
        ),
      confirm,
    ),
  getMcpCatalog: () =>
    fetchJSON<{ entries: McpCatalogEntry[]; diagnostics: McpCatalogDiagnostic[] }>(
      "/api/mcp/catalog",
    ),
  installMcpCatalogEntry: (
    name: string,
    env: Record<string, string> = {},
    enable = true,
  ) =>
    fetchJSON<{ ok: boolean; name: string; background: boolean; action?: string }>(
      "/api/mcp/catalog/install",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, env, enable }),
      },
    ),
  // ``mcp_invoke`` is confirm-gated: without ``confirm`` it stages a real
  // single-use approval (409); the shared round-trip re-issues with it. The
  // same canonical entry point the CLI (`qubit mcp invoke`) and voice use, so
  // the dashboard, CLI, and voice share one broker/policy/audit path.
  invokeMcpTool: (
    name: string,
    tool: string,
    args: Record<string, unknown> = {},
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<McpInvokeResult>(
          `/api/mcp/servers/${encodeURIComponent(name)}/invoke`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool, args }),
          },
        ),
      confirm,
    ),
  // Read-only MCP capability drains over the canonical `voicebridge.mcp_servers`
  // registry (compat `GET /api/mcp/servers/{name}/resources|prompts|
  // resource-templates`). No confirmation: the shared e-stop + method gate
  // still apply server-side. A real drained list resolves verbatim; an
  // inventory-only / gated / unknown / transport-failed server resolves to a
  // typed `{ok:false, code, detail}` disposition — never a fabricated list.
  getMcpServerResources: (name: string) =>
    fetchJSON<McpCapabilityResult>(
      `/api/mcp/servers/${encodeURIComponent(name)}/resources`,
    ),
  getMcpServerPrompts: (name: string) =>
    fetchJSON<McpCapabilityResult>(
      `/api/mcp/servers/${encodeURIComponent(name)}/prompts`,
    ),
  getMcpServerResourceTemplates: (name: string) =>
    fetchJSON<McpCapabilityResult>(
      `/api/mcp/servers/${encodeURIComponent(name)}/resource-templates`,
    ),

  // ── Admin: Pairing ──────────────────────────────────────────────────
  // The mutating endpoints read the profile off the BODY, so the query-param
  // rewrite in withManagementProfile doesn't reach them — send it explicitly
  // or an approval lands in the wrong profile's whitelist.
  getPairing: () => fetchJSON<PairingResponse>("/api/pairing"),
  // approve/revoke/clear are staged 409 confirm-gated on the backend; settle
  // through the shared round-trip so the badges are working controls rather
  // than dead ends (the button press is the explicit user confirmation).
  approvePairing: (
    platform: string,
    request_id: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; user: PairingUser }>("/api/pairing/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            platform,
            request_id,
            profile: getManagementProfile() || undefined,
          }),
        }),
      confirm,
    ),
  revokePairing: (
    platform: string,
    user_id: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>("/api/pairing/revoke", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            platform,
            user_id,
            profile: getManagementProfile() || undefined,
          }),
        }),
      confirm,
    ),
  clearPendingPairing: (
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; cleared: number }>(
          "/api/pairing/clear-pending",
          { method: "POST" },
        ),
      confirm,
    ),

  // ── Admin: Webhooks ─────────────────────────────────────────────────
  getWebhooks: () => fetchJSON<WebhooksResponse>("/api/webhooks"),
  // Store-wide enable is staged 409 confirm-gated; settle via the shared
  // round-trip (the toggle press is the explicit user confirmation).
  enableWebhooks: (
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<WebhookEnableResponse>("/api/webhooks/enable", {
          method: "POST",
        }),
      confirm,
    ),
  // The canonical store keys subscriptions by ``id`` and validates a real
  // delivery ``url`` + ``secret_ref``; send exactly that shape so the create
  // lands in the same store the CLI/voice use (no proxy/store split-brain).
  createWebhook: (
    body: WebhookCreate,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<WebhookRoute & { secret: string }>("/api/webhooks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: body.name,
            url: body.url,
            events: body.events,
            secret_ref: body.secret_ref,
          }),
        }),
      confirm,
    ),
  deleteWebhook: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean }>(
          `/api/webhooks/${encodeURIComponent(name)}`,
          { method: "DELETE" },
        ),
      confirm,
    ),
  setWebhookEnabled: (
    name: string,
    enabled: boolean,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<{ ok: boolean; name: string; enabled: boolean }>(
          `/api/webhooks/${encodeURIComponent(name)}/enabled`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled }),
          },
        ),
      confirm,
    ),
  // The four per-subscription operations the reference has no UI for but the
  // canonical store exposes: rotate the signing secret, send a real signed
  // test, read redacted delivery receipts, and retry the last failure. The
  // three mutations are confirm-gated; the round-trip re-issues with it.
  rotateWebhookSecret: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<WebhookRotateResponse>(
          `/api/webhooks/${encodeURIComponent(name)}/rotate`,
          { method: "POST" },
        ),
      confirm,
    ),
  testWebhook: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<WebhookDeliveryResult>(
          `/api/webhooks/${encodeURIComponent(name)}/test`,
          { method: "POST" },
        ),
      confirm,
    ),
  getWebhookDeliveries: (name: string, limit = 50) =>
    fetchJSON<WebhookDeliveriesResponse>(
      `/api/webhooks/${encodeURIComponent(name)}/deliveries?limit=${limit}`,
    ),
  retryWebhook: (
    name: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<WebhookDeliveryResult>(
          `/api/webhooks/${encodeURIComponent(name)}/retry`,
          { method: "POST" },
        ),
      confirm,
    ),

  // ── Admin: Credential pool ──────────────────────────────────────────
  // A32: served by the Qubit-owned redacted inventory/removal routes (the
  // upstream /api/credentials/pool stays gated because it carries token
  // previews). Names/labels only; removal is confirmed + audited.
  getCredentialPool: () =>
    fetchJSON<{ providers: CredentialPoolProvider[] }>(
      "/api/qubit/credentials/pool",
    ),
  addCredentialPoolEntry: (
    provider: string,
    key_ref: string,
    label?: string,
  ) =>
    // B-08: the upstream pool route is gated and never serves secrets. This
    // is the Qubit-native add path: the caller supplies an env:/file:
    // *reference* (never a raw key) and the server resolves it.
    fetchJSON<{ ok: boolean; provider: string; entry?: unknown }>(
      "/api/qubit/credentials/pool",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, key_ref, label, confirm: true }),
      },
    ),
  removeCredentialPoolEntry: (provider: string, index: number) =>
    fetchJSON<{ ok: boolean; provider?: string; removed?: unknown }>(
      `/api/qubit/credentials/pool/${encodeURIComponent(provider)}/${index}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      },
    ),

  // ── Admin: Memory provider ──────────────────────────────────────────
  getMemory: () => fetchJSON<MemoryStatus>("/api/memory"),
  getMemoryProviderConfig: (provider: string) =>
    fetchJSON<MemoryProviderConfig>(
      `/api/memory/providers/${encodeURIComponent(provider)}/config`,
    ),
  updateMemoryProviderConfig: (provider: string, values: Record<string, unknown>) =>
    fetchJSON<{ ok: boolean; active: string }>(
      `/api/memory/providers/${encodeURIComponent(provider)}/config`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        // B-08: confirm-gated; the explicit user action settles the operation.
        body: JSON.stringify({ values, confirm: true }),
      },
    ),
  setupMemoryProvider: (provider: string, values: Record<string, unknown> = {}) =>
    fetchJSON<MemoryProviderSetupResponse>(
      `/api/memory/providers/${encodeURIComponent(provider)}/setup`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values, confirm: true }),
      },
    ),
  setMemoryProvider: (provider: string) =>
    fetchJSON<{ ok: boolean; active: string }>("/api/memory/provider", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    }),
  resetMemory: (target: "all" | "memory" | "user") =>
    fetchJSON<{ ok: boolean; deleted: string[] }>("/api/memory/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // B-08: destructive and confirm-gated; carry target + explicit confirm.
      body: JSON.stringify({ target, confirm: true }),
    }),

  // ── Admin: Gateway lifecycle ────────────────────────────────────────
  // Confirm-gated exactly like restartGateway (the supervisor refuses an
  // unconfirmed transition with 409 GATEWAY_CONFIRM_REQUIRED); the shared
  // round-trip re-issues the same JSON body with `confirm: true`.
  startGateway: (confirm?: (detail: string) => boolean | Promise<boolean>) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ActionResponse>("/api/gateway/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }),
      confirm,
    ),
  stopGateway: (confirm?: (detail: string) => boolean | Promise<boolean>) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<ActionResponse>("/api/gateway/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }),
      confirm,
    ),

  // ── Admin: Power / emergency stop ───────────────────────────────────
  // One shared latch (`voicebridge.power`): the compat route mounts the same
  // handler the classic dashboard's /api/power uses, so CLI/voice/dashboard
  // all observe one emergency-stop state. Reads are immediate; mutations are
  // explicitly confirmed here (the backend also refuses an unconfirmed action).
  getPowerStatus: () => fetchJSON<PowerStatusResponse>("/api/power"),
  powerAction: (action: PowerAction, note?: string) =>
    fetchJSON<PowerActionResponse>("/api/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, confirmed: true, note }),
    }),

  // ── Admin: Operations ───────────────────────────────────────────────
  runDoctor: () =>
    fetchJSON<ActionResponse>("/api/ops/doctor", { method: "POST" }),
  runSecurityAudit: () =>
    fetchJSON<ActionResponse>("/api/ops/security-audit", { method: "POST" }),
  runBackup: (passphrase: string, output?: string) =>
    fetchJSON<ActionResponse>("/api/ops/backup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // B-08: backup_create requires an operator passphrase + confirm; the UI
      // would otherwise hit BAD_ARGS. The passphrase is a secret arg and is
      // never journaled.
      body: JSON.stringify({ output, passphrase, confirm: true }),
    }),
  downloadBackup: (archive: string) =>
    authedFetch(
      `/api/ops/backup/download?archive=${encodeURIComponent(archive)}`,
    ),
  runImport: (archive: string, force = false, passphrase = "") =>
    fetchJSON<ActionResponse>("/api/ops/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archive, force, passphrase, confirm: true }),
    }),
  runImportUpload: (file: File, force = false) => {
    const form = new FormData();
    form.append("force", String(force));
    form.append("file", file, file.name);
    return fetchJSON<ActionResponse>("/api/ops/import-upload", {
      method: "POST",
      body: form,
    });
  },
  getHooks: () => fetchJSON<HooksResponse>("/api/ops/hooks"),
  createHook: (body: HookCreate) =>
    fetchJSON<{ ok: boolean; event: string; command: string; approved: boolean }>(
      "/api/ops/hooks",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  deleteHook: (event: string, command: string) =>
    fetchJSON<{ ok: boolean }>("/api/ops/hooks", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      // B-08: confirm-gated deletion; the ConfirmDialog already asked.
      body: JSON.stringify({ event, command, confirm: true }),
    }),
  getSystemStats: () => fetchJSON<SystemStats>("/api/system/stats"),

  // ── Admin: Curator ──────────────────────────────────────────────────
  getCurator: () => fetchJSON<CuratorStatus>("/api/curator"),
  setCuratorPaused: (paused: boolean) =>
    fetchJSON<{ ok: boolean; paused: boolean }>("/api/curator/paused", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    }),
  runCurator: () =>
    fetchJSON<ActionResponse>("/api/curator/run", { method: "POST" }),

  // ── Admin: Portal ───────────────────────────────────────────────────
  getPortal: () => fetchJSON<PortalStatus>("/api/portal"),

  // ── Admin: Diagnostics (backgrounded) ───────────────────────────────
  runPromptSize: () =>
    fetchJSON<ActionResponse>("/api/ops/prompt-size", { method: "POST" }),
  runDump: () => fetchJSON<ActionResponse>("/api/ops/dump", { method: "POST" }),
  runConfigMigrate: () =>
    fetchJSON<ActionResponse>("/api/ops/config-migrate", { method: "POST" }),
  runDebugShare: (opts?: { redact?: boolean; lines?: number }) =>
    fetchJSON<DebugShareResponse>("/api/ops/debug-share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        redact: opts?.redact ?? true,
        lines: opts?.lines ?? 200,
      }),
    }),


  getCheckpoints: () => fetchJSON<CheckpointsResponse>("/api/ops/checkpoints"),
  pruneCheckpoints: () =>
    fetchJSON<ActionResponse>("/api/ops/checkpoints/prune", { method: "POST" }),

  // ── Admin: Skills hub ───────────────────────────────────────────────
  // ``profile`` scopes install/uninstall/update and the installed-state
  // annotations to that profile (omitted = the dashboard's own profile).
  installSkillFromHub: (
    identifier: string,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<SkillHubMutationResponse>("/api/skills/hub/install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ identifier, profile: profile || undefined }),
        }),
      confirm,
    ),
  uninstallSkillFromHub: (
    name: string,
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<SkillHubMutationResponse>("/api/skills/hub/uninstall", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, profile: profile || undefined }),
        }),
      confirm,
    ),
  updateSkillsFromHub: (
    profile?: string,
    confirm?: (detail: string) => boolean | Promise<boolean>,
  ) =>
    runMutationWithConfirm(
      () =>
        fetchJSON<SkillHubMutationResponse>("/api/skills/hub/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: profile || undefined }),
        }),
      confirm,
    ),
  searchSkillsHub: (q: string, source = "all", limit = 20, profile?: string) =>
    fetchJSON<SkillHubSearchResponse>(
      `/api/skills/hub/search?q=${encodeURIComponent(q)}&source=${encodeURIComponent(source)}&limit=${limit}${profile ? `&profile=${encodeURIComponent(profile)}` : ""}`,
    ),
  getSkillHubSources: (profile?: string) =>
    fetchJSON<SkillHubSourcesResponse>(
      `/api/skills/hub/sources${profileQuery(profile)}`,
    ),
  previewSkillFromHub: (identifier: string) =>
    fetchJSON<SkillHubPreview>(
      `/api/skills/hub/preview?identifier=${encodeURIComponent(identifier)}`,
    ),
  scanSkillFromHub: (identifier: string) =>
    fetchJSON<SkillHubScan>(
      `/api/skills/hub/scan?identifier=${encodeURIComponent(identifier)}`,
    ),
};

/** Identity payload returned by ``GET /api/auth/me`` (Phase 7).
 *
 * Returned by the dashboard's gated middleware when a valid session cookie
 * is attached. ``email`` and ``display_name`` are empty strings under the
 * Qubit Labs contract V1 (the access token has no email/name claims —
 * see Contract Anchor C4 in the plan). The AuthWidget surfaces a
 * truncated ``user_id`` instead.
 */
export interface AuthMeResponse {
  user_id: string;
  email: string;
  display_name: string;
  org_id: string;
  provider: string;
  expires_at: number;
}

export interface ActionResponse {
  archive?: string;
  name: string;
  ok: boolean;
  pid: number | null;
  error?: string;
  message?: string;
  uploaded_bytes?: number;
  update_command?: string;
  /** Op-specific payload (e.g. backup_create returns {path, bytes}). */
  result?: Record<string, unknown>;
  code?: string;
  detail?: string;
  state?: string;
}

/** GET /api/power — the shared power-control/policy status (classic dashboard
 *  and compat route read the same ``voicebridge.power`` latch). `control`
 *  carries the durable emergency-stop / global-disable state the SPA renders
 *  and mutates. */
export interface PowerControlStatus {
  state?: string;
  emergency_stop: boolean;
  global_disable: boolean;
  persisted?: boolean;
  max_concurrency?: number;
  active?: number;
  circuits?: Record<string, unknown>;
}

export interface PowerStatusResponse {
  state?: string;
  profile?: unknown;
  grant?: Record<string, unknown> | null;
  grant_health?: Record<string, unknown>;
  control?: PowerControlStatus;
  mcp?: Record<string, unknown>;
  detail?: string;
  code?: string;
}

export type PowerAction =
  | "emergency-stop"
  | "reset"
  | "disable"
  | "enable"
  | "reset-grant";

export interface PowerActionResponse {
  state?: string;
  health?: string;
  detail?: string;
  code?: string;
}

/** GET /api/gateway — gateway records plus the supervision policy string.
 *  Qubit refuses process supervision, so `supervision` carries a typed
 *  `GATEWAY_NO_SUPERVISION: …` explanation the dashboard renders instead of
 *  offering start/stop/restart controls that can only fail. */
export interface GatewayStatusResponse {
  ok?: boolean;
  gateway?: { state?: string; gateways?: unknown[] };
  state?: string;
  supervision?: string;
}

export interface DebugShareResponse {
  ok: boolean;
  // label -> paste URL, e.g. { Report: "https://paste.rs/abc", "agent.log": "..." }
  urls: Record<string, string>;
  // "label: error" strings for optional full-log uploads that failed.
  failures: string[];
  redacted: boolean;
  auto_delete_seconds: number;
}

export interface SessionStoreStats {
  total: number;
  active_store: number;
  archived: number;
  messages: number;
  by_source: Record<string, number>;
}

export interface SessionImportResponse {
  ok: boolean;
  imported: number;
  skipped: number;
  detached: number;
  imported_ids: string[];
  skipped_ids: string[];
  errors: Array<Record<string, unknown>>;
}

export interface SkillHubResult {
  name: string;
  description: string;
  source: string;
  identifier: string;
  trust_level: string;
  repo: string | null;
  tags: string[];
}

/** Lock-entry summary for an already-installed hub skill (keyed by identifier). */
/** Result of a confirm-gated skills-hub install/uninstall/update. The compat
 *  handlers settle on the shared approval journal (staged 409 with a real
 *  ``confirmation_id``, then ``POST /api/ops/confirm``), so the resolved
 *  envelope is journal-shaped rather than an ``ActionResponse``. */
export interface SkillHubMutationResponse {
  ok?: boolean;
  state?: string;
  code?: string;
  detail?: string;
  /** Spawned background action name, when the backend ran one. Absent for the
   *  journal-settled local install path. */
  name?: string;
  receipt?: unknown;
}

export interface SkillHubInstalledEntry {
  name: string | null;
  trust_level: string | null;
  scan_verdict: string | null;
}

export interface SkillHubSearchResponse {
  results: SkillHubResult[];
  /** source_id -> number of results returned by that source. */
  source_counts: Record<string, number>;
  /** source ids that didn't return within the parallel-search timeout. */
  timed_out: string[];
  /** identifier -> installed lock entry (for "already installed" badges). */
  installed: Record<string, SkillHubInstalledEntry>;
}

export interface SkillHubSource {
  id: string;
  label: string;
  /** GitHub only: whether the API is currently rate-limited. */
  rate_limited?: boolean;
  /** Centralized-index source only: whether the index loaded. */
  available?: boolean;
}

export interface SkillHubSourcesResponse {
  sources: SkillHubSource[];
  index_available: boolean;
  /** Featured/popular skills from the centralized index (zero extra API calls). */
  featured: SkillHubResult[];
  installed: Record<string, SkillHubInstalledEntry>;
}

export interface SkillHubPreview {
  name: string;
  description: string;
  source: string;
  identifier: string;
  trust_level: string;
  repo: string | null;
  tags: string[];
  /** Rendered SKILL.md content (the actual skill text). */
  skill_md: string;
  /** Relative paths of every file in the bundle. */
  files: string[];
}

export interface SkillHubScanFinding {
  severity: string;
  category: string;
  file: string;
  line: number;
  description: string;
}

export interface SkillHubScan {
  name: string;
  identifier: string;
  source: string;
  trust_level: string;
  /** "safe" | "caution" | "dangerous". */
  verdict: string;
  summary: string;
  /** Install-policy decision for this trust+verdict combo. */
  policy: "allow" | "ask" | "block";
  policy_reason: string;
  findings: SkillHubScanFinding[];
  severity_counts: Record<string, number>;
}

// ── Admin types ───────────────────────────────────────────────────────

export interface McpServer {
  name: string;
  transport: "http" | "stdio" | "unknown";
  url: string | null;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  auth: "header" | "oauth" | null;
  enabled: boolean;
  tools: string[] | null;
  /** Live transport state. A configured server is not necessarily connected;
   *  `connected` is the honest runtime truth the TUI banner counts. */
  connected?: boolean;
  /** Backend status label (e.g. `configured-not-connected`). */
  status?: string;
  /** The server's declared tool allowlist (`MS.list_servers`). An empty list is
   *  honest — the record declares no tools. */
  capabilities?: string[];
  /** Whether the canonical store permits a client invocation right now. */
  invocable?: boolean;
  /** Typed reason when `invocable` is false (e.g. operator-required). */
  invocation_code?: string | null;
  /** Which store the row was read from (`qubit-registry`). */
  source?: string;
  /** The mount policy attached to the row (Qubit never auto-mounts). */
  policy?: string;
}

export interface McpCatalogEntry {
  name: string;
  description: string;
  source: string;
  transport: "http" | "stdio";
  auth_type: "api_key" | "oauth" | "none";
  required_env: Array<{ name: string; prompt: string; required: boolean }>;
  // Transport details — what actually connects (http) or runs (stdio).
  command: string | null;
  args: string[];
  url: string | null;
  // Git bootstrap (only set for entries that clone + build locally).
  install_url: string | null;
  install_ref: string | null;
  bootstrap: string[];
  // Default tool pre-selection (null = all tools pre-checked) + guidance text.
  default_enabled: string[] | null;
  post_install: string;
  needs_install: boolean;
  installed: boolean;
  enabled: boolean;
}

export interface McpCatalogDiagnostic {
  name: string;
  kind: string;
  message: string;
}


export type McpHttpAuth = "none" | "header" | "oauth";

export interface McpServerCreate {
  name: string;
  /** Canonical registry transport; omitted -> backend defaults to stdio. */
  transport?: "http" | "stdio";
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  auth?: McpHttpAuth;
  bearer_token?: string;
}

export interface McpTestResult {
  ok: boolean;
  error?: string;
  tools: Array<{ name: string; description: string }>;
}

/** Result of a real, confirm-gated `POST /api/mcp/servers/{name}/invoke`.
 *  `state` is the canonical store's verdict (`OK`/`INVOCABLE` = success); the
 *  output is redacted by the client transport. */
export interface McpInvokeResult {
  ok: boolean;
  state?: string;
  code?: string;
  detail?: string;
  server?: string;
  tool?: string;
  result?: unknown;
  duration_ms?: number;
}

/** One drained MCP capability list (`GET /api/mcp/servers/{name}/resources|
 *  prompts|resource-templates`). Served verbatim from the canonical
 *  `voicebridge.mcp_servers` registry over the brokered client transport.
 *  `ok:false` is the honest typed disposition (unknown / gated / disabled /
 *  transport-failed server) — never a fabricated empty list. The list key
 *  varies by capability (`resources` | `prompts` | `resource_templates`, with
 *  `resourceTemplates` accepted as the wire alias); `result` carries the raw
 *  drained payload and `names` the server-reported names. */
export interface McpCapabilityResult {
  ok: boolean;
  server: string;
  capability: string;
  state?: string;
  connected?: boolean;
  invocable?: boolean;
  code?: string;
  detail?: string;
  operator_prerequisite?: string;
  qubit_counterpart?: string;
  authority?: string;
  count?: number;
  names?: string[] | null;
  resources?: Array<Record<string, unknown>>;
  prompts?: Array<Record<string, unknown>>;
  resource_templates?: Array<Record<string, unknown>>;
  /** Wire alias for `resource_templates` (accepted defensively). */
  resourceTemplates?: Array<Record<string, unknown>>;
  result?: Record<string, unknown> | null;
}

export interface McpOAuthFlow {
  flow_id: string;
  server_name: string;
  status: "starting" | "authorization_required" | "approved" | "error";
  authorization_url: string | null;
  error: string | null;
  tools?: Array<{ name: string; description: string }>;
}

export interface MessagingPlatformEnvVar {
  key: string;
  required: boolean;
  is_set: boolean;
  redacted_value: string | null;
  description: string;
  prompt: string;
  help: string;
  url: string | null;
  is_password: boolean;
  advanced: boolean;
}

export interface MessagingPlatform {
  id: string;
  name: string;
  description: string;
  docs_url: string;
  enabled: boolean;
  configured: boolean;
  gateway_running: boolean;
  /**
   * "connected" | "disabled" | "not_configured" | "pending_restart" |
   * "gateway_stopped" | "startup_failed" | "disconnected" | "fatal" | string
   */
  state: string;
  error_code: string | null;
  error_message: string | null;
  updated_at: string | null;
  home_channel: { platform: string; chat_id: string; name: string; thread_id?: string } | null;
  whatsapp_setup?: {
    mode?: string;
    allowed_users_set?: boolean;
    home_channel_set?: boolean;
  } | null;
  env_vars: MessagingPlatformEnvVar[];
}

export interface MessagingPlatformsResponse {
  env_path: string;
  gateway_start_command: string;
  platforms: MessagingPlatform[];
}

export interface MessagingPlatformUpdate {
  enabled?: boolean;
  env?: Record<string, string>;
  clear_env?: string[];
}

export interface MessagingPlatformTestResult {
  ok: boolean;
  state: string;
  message: string;
}

export interface PairingUser {
  platform: string;
  user_id: string;
  user_name?: string;
  request_id?: string;
  age_minutes?: number;
}

export interface PairingResponse {
  pending: PairingUser[];
  approved: PairingUser[];
}

export interface WebhookRoute {
  name: string;
  description: string;
  events: string[];
  deliver: string;
  deliver_only: boolean;
  prompt: string;
  skills: string[];
  created_at: string | null;
  url: string;
  secret_set: boolean;
  enabled: boolean;
}

export interface WebhooksResponse {
  enabled: boolean;
  base_url: string;
  subscriptions: WebhookRoute[];
}

export interface WebhookEnableResponse {
  ok: boolean;
  platform: "webhook";
  enabled: true;
  needs_restart: boolean;
  restart_started?: boolean;
  restart_action?: string;
  restart_pid?: number | null;
  restart_error?: string;
}

export interface WebhookCreate {
  /** Subscription id — must match ``^[a-z0-9][a-z0-9_.-]{0,63}$``. */
  name: string;
  /** Outbound delivery URL (loopback http(s) or an allowlisted https host). */
  url: string;
  /** Event names; at least one is required by the canonical store. */
  events: string[];
  /** Signing-secret reference: ``env:VAR`` or ``file:PATH`` (never a value). */
  secret_ref: string;
}

/** Redacted delivery receipt (``webhooks.deliveries``). The payload is never
 *  retained; only the verdict/status/error are. */
export interface WebhookDelivery {
  subscription?: string;
  event?: string;
  verdict?: string;
  status?: number | null;
  error?: string | null;
  ts?: string;
  [key: string]: unknown;
}

export interface WebhookDeliveriesResponse {
  ok: boolean;
  state?: string;
  deliveries: WebhookDelivery[];
  count: number;
  subscription: string | null;
}

/** Result of a real signed test/retry delivery (``webhooks.test_delivery`` /
 *  ``retry_delivery``). `verdict` is the honest outcome — `DELIVERED` or
 *  `FAILED` — never fabricated. */
export interface WebhookDeliveryResult {
  ok: boolean;
  state?: string;
  mode?: string;
  delivered?: boolean;
  verdict?: string;
  http_status?: number | null;
  error?: string | null;
  subscription?: string;
  event?: string;
  timestamp?: string;
  note?: string;
  retried_after_ts?: string;
}

/** Result of a confirmed signing-secret rotation. The new value is written to a
 *  Qubit-owned ``file:`` reference and never returned — only the kind. */
export interface WebhookRotateResponse {
  ok: boolean;
  state?: string;
  subscription?: WebhookRoute;
  reference_kind?: string;
  rotated_at?: string;
  note?: string;
}

export interface CredentialPoolEntry {
  index: number;
  id: string | null;
  label: string | null;
  auth_type: string | null;
  source: string | null;
  priority: number;
  last_status: string | null;
  request_count: number;
  token_preview: string;
  has_refresh: boolean;
}

export interface CredentialPoolProvider {
  provider: string;
  entries: CredentialPoolEntry[];
}

export interface MemoryProviderInfo {
  name: string;
  description: string;
  available: boolean;
  configured: boolean;
  status: "ready" | "needs_config" | "unavailable" | "missing";
  setup?: MemoryProviderSetupInfo;
}

export interface MemoryStatus {
  active: string;
  providers: MemoryProviderInfo[];
  builtin_files: { memory: number; user: number };
}

export interface MemoryProviderExternalDependency {
  name: string;
  install: string;
  check: string;
}

export interface MemoryProviderSetupInfo {
  pip_dependencies: string[];
  external_dependencies: MemoryProviderExternalDependency[];
  required_env: string[];
  dependencies_installed: boolean;
}

export interface MemoryProviderSetupResult {
  kind: string;
  name: string;
  status: string;
  command: string;
  returncode: number | null;
  stdout: string;
  stderr: string;
}

export interface MemoryProviderSetupResponse {
  ok: boolean;
  provider: string;
  results: MemoryProviderSetupResult[];
  status?: MemoryProviderInfo | null;
}

export interface MemoryProviderFieldOption {
  value: string;
  label: string;
  description?: string;
}

export interface MemoryProviderField {
  key: string;
  label: string;
  kind: "text" | "secret" | "select" | "boolean" | "integer" | "number";
  description: string;
  placeholder: string;
  required: boolean;
  value: string | boolean | number;
  is_set: boolean;
  options: MemoryProviderFieldOption[];
  url: string;
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  when?: Record<string, string | boolean | number> | null;
}

export interface MemoryProviderConfig {
  name: string;
  label: string;
  fields: MemoryProviderField[];
  setup?: MemoryProviderSetupInfo;
}

export interface HookEntry {
  event: string;
  matcher: string | null;
  command: string | null;
  timeout: number | null;
  allowed: boolean;
  approved_at?: string | null;
  executable?: boolean;
}

export interface HooksResponse {
  hooks: HookEntry[];
  valid_events: string[];
}

export interface HookCreate {
  event: string;
  command: string;
  matcher?: string;
  timeout?: number;
  approve?: boolean;
}

export interface UpdateCheckResponse {
  install_method: string;
  current_version: string;
  // commits behind: >=1 known count, 0 up to date, -1 behind by unknown
  // count (nix/pypi), or null when the check could not run.
  behind: number | null;
  update_available: boolean;
  can_apply: boolean;
  update_command: string;
  message: string | null;
}

export interface SystemStats {
  os: string;
  os_release: string;
  os_version: string;
  platform: string;
  arch: string;
  hostname: string;
  python_version: string;
  python_impl: string;
  qubit_version: string;
  cpu_count: number | null;
  psutil: boolean;
  cpu_percent?: number;
  load_avg?: number[];
  uptime_seconds?: number;
  memory?: { total: number; available: number; used: number; percent: number };
  disk?: { total: number; used: number; free: number; percent: number };
  process?: { pid: number; rss: number; create_time: number; num_threads: number };
}

export interface CuratorStatus {
  enabled: boolean;
  paused: boolean;
  interval_hours: number | null;
  last_run_at: string | null;
  min_idle_hours: number | null;
  stale_after_days: number | null;
  archive_after_days: number | null;
}

export interface PortalFeature {
  label: string;
  state: string;
}

export interface PortalStatus {
  logged_in: boolean;
  portal_url: string | null;
  inference_url: string | null;
  provider: string;
  subscription_url: string;
  features: PortalFeature[];
}

export interface CheckpointSession {
  session: string;
  files: number;
  bytes: number;
}

export interface CheckpointsResponse {
  sessions: CheckpointSession[];
  total_bytes: number;
}

/** Per-call overrides for {@link fetchJSON}. */
interface FetchJSONOptions {
  /** When true, a 401 response is surfaced as a normal thrown error rather
   *  than triggering the loopback stale-token page reload. Use for probes
   *  whose 401 is an expected signal (e.g. /api/auth/me in non-gated mode)
   *  rather than evidence of a rotated session token. */
  allowUnauthorized?: boolean;
}

export interface ActionStatusResponse {
  exit_code: number | null;
  lines: string[];
  name: string;
  pid: number | null;
  running: boolean;
}

export interface PlatformStatus {
  error_code?: string;
  error_message?: string;
  state: string;
  updated_at: string;
}

export interface StatusResponse {
  active_sessions: number;
  /** Phase 7: ``true`` when the dashboard's OAuth gate is engaged
   * (public bind, no ``--insecure``). Read alongside ``auth_providers``
   * to render a "gated / loopback" badge. */
  auth_required?: boolean;
  /** Phase 7: registered ``DashboardAuthProvider`` names (e.g. ``["nous"]``).
   * Empty in loopback mode; empty + ``auth_required=true`` is a
   * fail-closed state (the dashboard will refuse to bind). */
  auth_providers?: string[];
  /** Supported dashboard auth flows for the client to choose from. In gated
   * mode always includes ``"cookie"``; includes ``"native_pkce"`` when any
   * interactive session provider is registered (OAuth providers broker the
   * IDP redirect; password providers complete at /login in the system
   * browser), signalling that the desktop can use the RFC 8252
   * system-browser + loopback + PKCE flow (no embedded webview, no session
   * cookies). Absent / missing ``"native_pkce"`` ⇒ an older gateway ⇒ the
   * desktop falls back to the embedded-webview flow. */
  auth_flows?: string[];
  /** False when the dashboard is running in a hosted/managed layout where
   * updates are handled by the outer launcher instead of ``qubit update``. */
  can_update_qubit?: boolean;
  config_path: string;
  config_version: number;
  env_path: string;
  gateway_exit_reason: string | null;
  gateway_health_url: string | null;
  gateway_pid: number | null;
  gateway_platforms: Record<string, PlatformStatus>;
  gateway_running: boolean;
  /** Every profile the gateway process serves when the managed profile is carried by the
   * shared multiplexer (e.g. ["default", "alpha", "beta"]); null/absent for a standalone
   * gateway or an older backend. Ported from the pinned upstream `StatusResponse`. */
  gateway_shared_with?: string[] | null;
  gateway_state: string | null;
  gateway_updated_at: string | null;
  qubit_home: string;
  latest_config_version: number;
  /** NS-656: memory-pressure rollup from the gateway heartbeat +
   * lifecycle ledger. Absent on older gateways. */
  memory?: MemoryPressureStatus;
  /** NS-656: disk-usage rollup for the QUBIT_HOME volume. Absent on
   * older gateways. */
  disk?: DiskPressureStatus;
  release_date: string;
  version: string;
}

/** NS-656: coarse memory telemetry served by /api/status. */
export interface MemoryPressureStatus {
  pressure: "ok" | "elevated" | "critical" | "unknown";
  gateway_rss_mb?: number | null;
  system_total_mb?: number | null;
  system_available_mb?: number | null;
  swap_used_mb?: number | null;
  sampled_at?: string | null;
  /** Previous gateway life died without running any exit path. */
  last_boot_unclean?: boolean;
  /** ...and its final heartbeat showed near-exhausted memory. Heuristic —
   * strong evidence of an OOM kill, not proof the kernel OOM killer acted. */
  last_boot_suspected_oom?: boolean;
  /** Identity of the current gateway life (sentinel started_at). Changes on
   * every restart; keys per-incident banner dismissal. */
  boot_id?: string | null;
}

/** NS-656: coarse disk telemetry served by /api/status. Live statvfs
 * sample of the QUBIT_HOME volume — no staleness dimension, so no
 * sampled_at. */
export interface DiskPressureStatus {
  pressure: "ok" | "elevated" | "critical" | "unknown";
  total_mb?: number | null;
  free_mb?: number | null;
  used_percent?: number | null;
}

export interface SessionInfo {
  id: string;
  source: string | null;
  model: string | null;
  title: string | null;
  started_at: number;
  ended_at: number | null;
  last_active: number;
  is_active: boolean;
  message_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  preview: string | null;
  parent_session_id?: string | null;
  /** Archived rows still appear in the list (the store keeps them); the UI
   *  renders a Restore control via the native chat-session store. */
  archived?: boolean;
  /** Owning profile stamped by the list/detail endpoints (the store the row
   * was read from). Absent on search-endpoint rows, which carry no stamp. */
  profile?: string;
}

export interface SessionLatestDescendantResponse {
  requested_session_id: string;
  session_id: string;
  path: string[];
  changed: boolean;
}

export interface PaginatedSessions {
  sessions: SessionInfo[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One row of the Qubit-native chat-session store
 * (`GET /api/qubit/chat/sessions`, `voicebridge.chat_transport`). This is the
 * authority for `/chat` — distinct from the receipts `GET /api/sessions`
 * store used by the Sessions page.
 */
export interface ChatStoreSession {
  id: string;
  source?: string | null;
  model?: string | null;
  title?: string | null;
  name?: string | null;
  archived?: boolean;
  started_at?: number;
  ended_at?: number | null;
  last_active?: number;
  is_active?: boolean;
  message_count?: number;
  preview?: string | null;
}

export interface ChatStoreSessionsResponse {
  sessions: ChatStoreSession[];
  total: number;
  limit: number;
  offset: number;
  state?: string;
  code?: string | null;
  source?: string;
}

export interface ChatStoreMessage {
  role: string;
  content: string;
  timestamp?: number;
  meta?: Record<string, unknown>;
}

export interface ChatStoreMessagesResponse {
  session_id: string;
  messages: ChatStoreMessage[];
  total: number;
}

export interface ChatStoreSessionResponse {
  session: ChatStoreSession;
  messages: ChatStoreMessage[];
  message_count?: number;
  /** Per-session preferences from the session `model_config` metadata
   * (e.g. `reasoning`). */
  meta?: Record<string, unknown>;
}

export interface ChatStoreCreateResponse {
  session: ChatStoreSession;
  messages?: ChatStoreMessage[];
}

export interface ChatDraftEntry {
  id?: string | number;
  session_id: string;
  path: string;
  bytes: number;
  sha256?: string;
  base_sha256?: string;
  actor?: string;
  created_at?: number;
  updated_at?: number;
  content?: string;
}

export interface ChatDraftsResponse {
  state?: string;
  session_id?: string;
  drafts?: ChatDraftEntry[];
  total?: number;
}

export interface ChatCommentEntry {
  id?: string | number;
  session_id: string;
  body: string;
  author?: string;
  target?: string;
  at?: number;
  meta?: Record<string, unknown>;
}

export interface ChatCommentsResponse {
  state?: string;
  session_id?: string;
  comments?: ChatCommentEntry[];
  total?: number;
}

export interface ChatCiEntry {
  id?: string | number;
  session_id: string;
  check: string;
  status: string;
  detail?: string;
  url?: string;
  ref?: string;
  at?: number;
  meta?: Record<string, unknown>;
}

export interface ChatCiResponse {
  state?: string;
  session_id?: string;
  ci?: ChatCiEntry[];
  total?: number;
}

/** Parsed SSE frame from `POST /api/qubit/chat/stream`. */
export interface ChatStreamEvent {
  event: string;
  text?: string;
  reply?: string;
  code?: string;
  detail?: string;
  session_id?: string;
  session?: string;
  elapsed_ms?: number;
  message?: ChatStoreMessage;
  message_count?: number;
  receipt?: {
    model?: string;
    runtime?: string;
    path?: string;
    reasoning_applied?: { level?: string; max_tokens?: number | null };
  };
}

export interface ChatStreamHandlers {
  onStart?: (event: ChatStreamEvent) => void;
  onDelta?: (text: string, event: ChatStreamEvent) => void;
  onDone?: (event: ChatStreamEvent) => void;
  onError?: (event: ChatStreamEvent) => void;
  onCancelled?: (event: ChatStreamEvent) => void;
}

function parseSseFrame(frame: string): ChatStreamEvent | null {
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.trimEnd();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return JSON.parse(dataLines.join("\n")) as ChatStreamEvent;
  } catch {
    return null;
  }
}

/**
 * Stream one persisted agent turn over the Qubit SSE transport
 * (`POST /api/qubit/chat/stream`). The same `voicebridge.chat_transport`
 * module backs the non-streaming turn and CLI/voice; it persists the user
 * message and the final reply into the chat-session store, so a reload
 * restores the transcript. Nothing is fabricated on model failure — a typed
 * `error` event is surfaced instead.
 */
/** Reasoning levels the `/qubit-chat` selector offers (budgets in tokens). */
export const CHAT_REASONING_LEVELS = [
  { id: "none", budget: 64 },
  { id: "minimal", budget: 128 },
  { id: "low", budget: 256 },
  { id: "medium", budget: 512 },
  { id: "high", budget: 1024 },
  { id: "max", budget: 2048 },
] as const;

export async function streamChatTurn(
  body: { text: string; session?: string; model?: string; reasoning?: string },
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch("/api/qubit/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  const reader = res.body?.getReader();
  if (!reader) throw new Error("STREAM_UNSUPPORTED: no response body");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const event = parseSseFrame(frame);
      if (event) {
        switch (event.event) {
          case "start":
            handlers.onStart?.(event);
            break;
          case "delta":
            handlers.onDelta?.(event.text ?? "", event);
            break;
          case "done":
            handlers.onDone?.(event);
            break;
          case "cancelled":
            handlers.onCancelled?.(event);
            break;
          case "error":
            handlers.onError?.(event);
            break;
          default:
            break;
        }
      }
      idx = buffer.indexOf("\n\n");
    }
  }
}

export interface EnvVarInfo {
  is_set: boolean;
  redacted_value: string | null;
  description: string;
  url: string | null;
  category: string;
  is_password: boolean;
  tools: string[];
  advanced: boolean;
  /** True when this var is a messaging-platform credential owned by the Channels page. */
  channel_managed?: boolean;
  /** True when this key is set in .env but not in any catalog (user-added custom key). */
  custom?: boolean;
}

export interface TelegramOnboardingStartResponse {
  pairing_id: string;
  suggested_username: string;
  deep_link: string;
  qr_payload: string;
  expires_at: string;
}

export type TelegramOnboardingStatusResponse =
  | { status: "waiting"; expires_at: string }
  | {
      status: "ready";
      bot_username: string;
      owner_user_id?: string;
      expires_at: string;
    };

export interface TelegramOnboardingApplyResponse {
  ok: boolean;
  platform: "telegram";
  bot_username?: string;
  needs_restart: boolean;
  restart_started?: boolean;
  restart_action?: string;
  restart_pid?: number | null;
  restart_error?: string;
}

export interface WhatsAppOnboardingStartResponse {
  pairing_id: string;
  status:
    | "starting"
    | "installing"
    | "waiting"
    | "connected"
    | "error"
    | "expired"
    | "cancelled";
  qr_payload?: string | null;
  expires_at: string;
  mode: "bot" | "self-chat";
  allowed_users: string;
  account_id?: string | null;
  account_name?: string | null;
  account_phone?: string | null;
  error?: string | null;
}

export type WhatsAppOnboardingStatusResponse = WhatsAppOnboardingStartResponse;

export interface WhatsAppOnboardingApplyResponse {
  ok: boolean;
  platform: "whatsapp";
  needs_restart: boolean;
  restart_started?: boolean;
  restart_action?: string;
  restart_pid?: number | null;
  restart_error?: string;
}

export interface SessionMessageToolCall {
  id: string;
  function: { name: string; arguments: string };
}

/**
 * Extra per-message fields the Qubit chat-session store nests under ``meta``
 * (``voicebridge/qubit_session_store._msg``): the assistant tool calls, the
 * tool-row name/id, and the assistant chain-of-thought. The compat
 * ``/api/sessions/{id}/messages`` endpoint serves them unchanged, so the
 * dashboard maps them to the top-level shape it renders (never drops them).
 */
export interface SessionMessageMeta {
  tool_calls?: SessionMessageToolCall[];
  tool_name?: string;
  tool_call_id?: string;
  reasoning?: string | null;
  reasoning_content?: string | null;
  [key: string]: unknown;
}

export interface SessionMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string | null;
  tool_calls?: SessionMessageToolCall[];
  tool_name?: string;
  tool_call_id?: string;
  timestamp?: number;
  meta?: SessionMessageMeta | null;
  /** Assistant reasoning mapped from ``meta.reasoning``/``reasoning_content``. */
  reasoning?: string | null;
}

/**
 * Flatten the Qubit-nested ``meta`` fields onto the top-level message shape
 * the transcript renders. The backend stores ``tool_calls``/``tool_name``/
 * ``reasoning`` under ``meta`` (a wire-shape mismatch with the reference, which
 * carries them at top level); mapping here keeps both shapes working.
 */
export function normalizeSessionMessage(msg: SessionMessage): SessionMessage {
  const meta =
    msg.meta && typeof msg.meta === "object"
      ? (msg.meta as SessionMessageMeta)
      : null;
  if (!meta) return msg;
  const toolCalls =
    msg.tool_calls ??
    (Array.isArray(meta.tool_calls) ? meta.tool_calls : undefined);
  const toolName =
    msg.tool_name ??
    (typeof meta.tool_name === "string" ? meta.tool_name : undefined);
  const toolCallId =
    msg.tool_call_id ??
    (typeof meta.tool_call_id === "string" ? meta.tool_call_id : undefined);
  const rawReasoning =
    msg.reasoning ?? meta.reasoning ?? meta.reasoning_content ?? null;
  const reasoning =
    typeof rawReasoning === "string" && rawReasoning.trim().length > 0
      ? rawReasoning
      : (msg.reasoning ?? null);
  return {
    ...msg,
    tool_calls: toolCalls,
    tool_name: toolName,
    tool_call_id: toolCallId,
    reasoning,
  };
}

export interface SessionMessagesResponse {
  session_id: string;
  messages: SessionMessage[];
  pagination?: {
    limit: number;
    offset: number;
    order: "latest" | "oldest";
    returned: number;
  };
}

export interface LogsResponse {
  file: string;
  lines: string[];
}

export interface ManagedFileEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size: number | null;
  mtime: number;
  mime_type: string | null;
}

export interface ManagedFilesResponse {
  root: string | null;
  path: string;
  parent: string | null;
  locked_root: string | null;
  can_change_path: boolean;
  entries: ManagedFileEntry[];
  /** Honest reason the listing is empty (e.g. no approved project folder). */
  detail?: string;
}

export interface ManagedFileReadResponse {
  name: string;
  path: string;
  size: number;
  mime_type: string;
  data_url: string;
  root: string | null;
  locked_root: string | null;
  can_change_path: boolean;
}

export interface ManagedFileWriteResponse {
  ok: boolean;
  path: string;
  entry?: ManagedFileEntry;
  root?: string | null;
  locked_root?: string | null;
  can_change_path?: boolean;
  /** Present on a broker-confirmed write (state/receipt). */
  state?: string;
  receipt?: Record<string, unknown>;
  qubit_counterpart?: string;
}

export interface AnalyticsDailyEntry {
  day: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  estimated_cost: number;
  actual_cost: number;
  sessions: number;
  api_calls: number;
}

export interface AnalyticsModelEntry {
  model: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  sessions: number;
  api_calls: number;
}

export interface AnalyticsSkillEntry {
  skill: string;
  view_count: number;
  manage_count: number;
  total_count: number;
  percentage: number;
  last_used_at: number | null;
}

export interface AnalyticsSkillsSummary {
  total_skill_loads: number;
  total_skill_edits: number;
  total_skill_actions: number;
  distinct_skills_used: number;
}

export interface AnalyticsResponse {
  daily: AnalyticsDailyEntry[];
  by_model: AnalyticsModelEntry[];
  totals: {
    total_input: number;
    total_output: number;
    total_cache_read: number;
    total_reasoning: number;
    total_estimated_cost: number;
    total_actual_cost: number;
    total_sessions: number;
    total_api_calls: number;
  };
  skills: {
    summary: AnalyticsSkillsSummary;
    top_skills: AnalyticsSkillEntry[];
  };
}

export interface ActiveProfileInfo {
  active: string;
  current: string;
}

export interface ProfileDescribeAutoResult {
  ok: boolean;
  reason: string;
  description: string | null;
  description_auto: boolean;
}

/** ``_profile_field`` (description/SOUL) response: the stored field record,
 *  never a fabricated description shape. */
export interface ProfileFieldResponse {
  ok: boolean;
  field?: {
    key?: string;
    value?: string;
    trust?: string;
    updated_at?: string;
  };
}

export interface ProfileInfo {
  name: string;
  path: string;
  is_default: boolean;
  model: string | null;
  provider: string | null;
  has_env: boolean;
  skill_count: number;
  gateway_running: boolean;
  description: string;
  description_auto: boolean;
  display_name?: string;
  distribution_name: string | null;
  distribution_version: string | null;
  distribution_source: string | null;
  has_alias: boolean;
}

export interface ModelsAnalyticsModelEntry {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  estimated_cost: number;
  actual_cost: number;
  sessions: number;
  api_calls: number;
  tool_calls: number;
  last_used_at: number;
  avg_tokens_per_session: number;
  capabilities: {
    supports_tools?: boolean;
    supports_vision?: boolean;
    supports_reasoning?: boolean;
    context_window?: number;
    max_output_tokens?: number;
    model_family?: string;
  };
}

export interface ModelsAnalyticsResponse {
  models: ModelsAnalyticsModelEntry[];
  totals: {
    distinct_models: number;
    total_input: number;
    total_output: number;
    total_cache_read: number;
    total_reasoning: number;
    total_estimated_cost: number;
    total_actual_cost: number;
    total_sessions: number;
    total_api_calls: number;
  };
  period_days: number;
}

export interface CronJobRepeat {
  times: number | null;
  completed?: number;
}

export interface CronJobMutation {
  name?: string;
  prompt?: string;
  schedule?: string;
  /** Qubit compat schedule expression (``cron_create``/``cron_update`` read
   *  ``when``); the editor's display string is mapped onto it in api.ts. */
  when?: string;
  deliver?: string;
  skills?: string[];
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
  script?: string | null;
  no_agent?: boolean;
  context_from?: string[] | null;
  enabled_toolsets?: string[] | null;
  workdir?: string | null;
}

export interface CronJob {
  id: string;
  profile?: string | null;
  profile_name?: string | null;
  qubit_home?: string | null;
  is_default_profile?: boolean;
  name?: string | null;
  prompt?: string | null;
  script?: string | null;
  skills?: string[] | null;
  schedule?: { kind?: string; expr?: string; run_at?: string; display?: string };
  schedule_display?: string | null;
  repeat?: CronJobRepeat | null;
  enabled: boolean;
  state?: string | null;
  deliver?: string | null;
  model?: string | null;
  provider?: string | null;
  base_url?: string | null;
  no_agent?: boolean | null;
  context_from?: string[] | string | null;
  enabled_toolsets?: string[] | null;
  workdir?: string | null;
  /** Epoch seconds (Qubit `_cron_shape`), epoch ms, or ISO — normalize with
   *  `formatCronTime` (never `new Date(value)` directly). */
  last_run_at?: number | string | null;
  next_run_at?: number | string | null;
  last_status?: string | null;
  last_error?: string | null;
  last_delivery_error?: string | null;
  last_fire_error?: { at?: string | null; detail?: string | null } | null;
}

export interface CronDeliveryTarget {
  id: string;
  name: string;
  home_target_set: boolean;
  home_env_var: string | null;
}

export interface AutomationBlueprintField {
  name: string;
  type: "time" | "enum" | "text" | "weekdays";
  label: string;
  default: string | null;
  options: string[];
  optional: boolean;
  /** When false, options are suggestions — any value is accepted. */
  strict?: boolean;
  help: string;
}

export interface AutomationBlueprint {
  key: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  fields: AutomationBlueprintField[];
  command: string;
  appUrl: string;
}

export interface SkillInfo {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
}

export interface SkillContent {
  name: string;
  content: string;
  path: string;
}

export interface SkillWriteResult {
  success: boolean;
  message?: string;
  path?: string;
  error?: string;
}

export interface ToolsetInfo {
  name: string;
  label: string;
  description: string;
  platform: string;
  platform_label: string;
  enabled: boolean;
  configured: boolean;
  tools: string[];
}

export interface ToolsetProviderEnvVar {
  key: string;
  prompt: string;
  url: string | null;
  default: string | null;
  is_set: boolean;
}

export interface ToolsetProvider {
  name: string;
  badge: string;
  tag: string;
  env_vars: ToolsetProviderEnvVar[];
  post_setup: string | null;
  requires_nous_auth: boolean;
  is_active: boolean;
}

export interface ToolsetConfig {
  name: string;
  has_category: boolean;
  providers: ToolsetProvider[];
  active_provider: string | null;
}

export interface ToolsetEnvResult {
  ok: boolean;
  name: string;
  saved: string[];
  skipped: string[];
  is_set: Record<string, boolean>;
}

export interface SessionSearchResult extends SessionInfo {
  session_id: string;
  snippet: string;
  role: string | null;
  session_started: number | null;
  lineage_root?: string;
}

export interface SessionSearchResponse {
  results: SessionSearchResult[];
  // The compat search route reuses the sessions list handler, which also
  // emits `sessions`; tolerate either envelope so the page never goes blank.
  sessions?: SessionSearchResult[];
}

// ── Model info types ──────────────────────────────────────────────────

export interface ModelInfoResponse {
  model: string;
  provider: string;
  auto_context_length: number;
  config_context_length: number;
  effective_context_length: number;
  capabilities: {
    supports_tools?: boolean;
    supports_vision?: boolean;
    supports_reasoning?: boolean;
    context_window?: number;
    max_output_tokens?: number;
    model_family?: string;
  };
}

// ── Model options / assignment types ──────────────────────────────────

export interface ModelOptionProvider {
  name: string;
  slug: string;
  models?: string[];
  total_models?: number;
  is_current?: boolean;
  is_user_defined?: boolean;
  source?: string;
  warning?: string;
  authenticated?: boolean;
}

export interface ModelOptionsResponse {
  model?: string;
  provider?: string;
  providers?: ModelOptionProvider[];
}

export interface AuxiliaryTaskAssignment {
  task: string;
  provider: string;
  model: string;
  base_url: string;
}

export interface AuxiliaryModelsResponse {
  tasks: AuxiliaryTaskAssignment[];
  main: { provider: string; model: string };
}

export interface MoaModelSlot {
  provider: string;
  model: string;
  /** Optional per-slot reasoning effort — round-tripped, not edited here. */
  reasoning_effort?: string;
  enabled?: boolean;
}

export interface MoaConfigResponse {
  default_preset: string;
  active_preset: string;
  presets: Record<string, {
    reference_models: MoaModelSlot[];
    aggregator: MoaModelSlot;
    reference_temperature: number;
    aggregator_temperature: number;
    reference_timeout: number | null;
    degraded_reference_policy: "loud" | "silent";

    /** Fan-out cadence (user_turn default | per_iteration | every_n:N) — round-tripped. */
    fanout?: string;
    enabled: boolean;
  }>;
  reference_models: MoaModelSlot[];
  aggregator: MoaModelSlot;
  reference_temperature: number;
  aggregator_temperature: number;
  reference_timeout: number | null;
  degraded_reference_policy: "loud" | "silent";

  enabled: boolean;
}

export interface ModelAssignmentRequest {
  confirm_expensive_model?: boolean;
  scope: "main" | "auxiliary";
  provider: string;
  model: string;
  /** Optional OpenAI-compatible endpoint URL for custom/local main providers. */
  base_url?: string;
  /** For auxiliary: task slot name, "" for all, "__reset__" to reset all. */
  task?: string;
}

/** An auxiliary task still pinned to a provider that differs from the
 *  newly-selected main provider after a main-model switch. */
export interface StaleAuxAssignment {
  task: string;
  provider: string;
  model: string;
}

export interface ModelAssignmentResponse {
  confirm_message?: string;
  confirm_required?: boolean;
  ok: boolean;
  scope?: string;
  provider?: string;
  model?: string;
  tasks?: string[];
  reset?: boolean;
  /** Auxiliary slots still pinned to a different provider than the new main.
   *  Switching main never clears aux pins; this lets the UI warn the user
   *  their helper tasks aren't following the switch. Only set on scope:'main'. */
  stale_aux?: StaleAuxAssignment[];
}

// ── OAuth provider types ────────────────────────────────────────────────

export interface OAuthProviderStatus {
  logged_in: boolean;
  source?: string | null;
  source_label?: string | null;
  token_preview?: string | null;
  expires_at?: string | null;
  has_refresh_token?: boolean;
  last_refresh?: string | null;
  error?: string;
}

export interface OAuthProvider {
  id: string;
  name: string;
  /** "pkce" (browser redirect + paste code), "device_code" (show code + URL),
   *  or "external" (delegated to a separate CLI like Claude Code or Qwen). */
  flow: "pkce" | "device_code" | "external";
  cli_command: string;
  docs_url: string;
  status: OAuthProviderStatus;
}

export interface OAuthProvidersResponse {
  providers: OAuthProvider[];
}

/** Discriminated union — the shape of /start depends on the flow. */
export type OAuthStartResponse =
  | {
      session_id: string;
      flow: "pkce";
      auth_url: string;
      expires_in: number;
    }
  | {
      session_id: string;
      flow: "device_code";
      user_code: string;
      verification_url: string;
      expires_in: number;
      poll_interval: number;
    };

export interface OAuthSubmitResponse {
  ok: boolean;
  status: "approved" | "error";
  message?: string;
}

export interface OAuthPollResponse {
  session_id: string;
  status: "pending" | "approved" | "denied" | "expired" | "error";
  error_message?: string | null;
  expires_at?: number | null;
}

// ── Dashboard theme types ──────────────────────────────────────────────

export interface DashboardThemeSummary {
  description: string;
  label: string;
  name: string;
  /** Full theme definition for user themes; undefined for built-ins
   *  (which the frontend already has locally). */
  definition?: DashboardTheme;
}

export interface DashboardThemesResponse {
  active: string;
  themes: DashboardThemeSummary[];
}

export interface DashboardFontResponse {
  /** Active font-override id, or "theme" when no override is set. */
  font: string;
}

// ── Dashboard plugin types ─────────────────────────────────────────────

export interface PluginManifestResponse {
  name: string;
  label: string;
  description: string;
  icon: string;
  version: string;
  tab: {
    path: string;
    position?: string;
    override?: string;
    hidden?: boolean;
  };
  slots?: string[];
  entry: string;
  css?: string | null;
  has_api: boolean;
  source: string;
}

export interface HubAgentPluginRow {
  name: string;
  version: string;
  description: string;
  source: string;
  runtime_status: "disabled" | "enabled" | "inactive";
  has_dashboard_manifest: boolean;
  dashboard_manifest: PluginManifestResponse | null;
  path: string;
  can_remove: boolean;
  can_update_git: boolean;
  auth_required: boolean;
  auth_command: string;
  user_hidden: boolean;
}

export interface PluginsHubProviders {
  memory_provider: string;
  memory_options: MemoryProviderInfo[];
  context_engine: string;
  context_options: Array<{ name: string; description: string }>;
}

export interface PluginsHubResponse {
  plugins: HubAgentPluginRow[];
  orphan_dashboard_plugins: PluginManifestResponse[];
  providers: PluginsHubProviders;
  catalog_index?: PluginPinnedIndex;
}

// ── Real local plugin catalog ──────────────────────────────────────────

export interface PluginCatalogProvides {
  tools: string[];
  hooks: string[];
  skills: string[];
  web_providers: string[];
  browser_providers: string[];
  requires_env: string[];
  optional_env: string[];
}

export interface PluginCatalogDashboard {
  name: string;
  label: string;
  description: string;
  icon: string;
  version: string;
  tab: { path: string; position?: string; override?: string; hidden?: boolean };
  entry?: string;
  css?: string | null;
  has_api: boolean;
  source: string;
}

export interface PluginCatalogEntry {
  id: string;
  name: string;
  label: string;
  description: string;
  version: string;
  source: string;
  kind: string;
  path: string;
  manifest_path: string;
  installed: boolean;
  enabled: boolean;
  runtime_status: "enabled" | "disabled" | "inactive";
  loadable: boolean;
  load_state: string;
  has_dashboard_manifest: boolean;
  dashboard: PluginCatalogDashboard | null;
  provides: PluginCatalogProvides;
  provenance: Record<string, string>;
  can_enable: boolean;
  can_disable: boolean;
  can_remove: boolean;
  can_update: boolean;
  can_hide: boolean;
  can_install: boolean;
  user_hidden: boolean;
  declared_dependencies: string[];
  install_capability: string;
  error: string | null;
}

export interface PluginCatalogRemote {
  state: string;
  code: string;
  detail: string;
  prerequisite: string;
}

// ── Curated, pinned remote plugin index (Owner Decision #1) ────────────

export interface PluginPinnedIndexEntry {
  id: string;
  name: string;
  label: string;
  description: string;
  repo: string;
  subdir: string;
  pinned_ref: string;
  pinned_ref_short: string;
  homepage: string;
  maintainer: string;
  license: string;
  tier: string;
  category: string;
  capabilities: string[];
  api_version?: number | null;
  tags: string[];
  curated: boolean;
  pinned: boolean;
  install_state: string;
  install_code: string;
  install_capability: string;
  provenance: Record<string, string>;
}

export interface PluginPinnedIndexInstall {
  state: string;
  code: string;
  detail: string;
  prerequisite: string;
  endpoint: string;
}

export interface PluginPinnedIndex {
  ok: boolean;
  state: string;
  schema: string;
  curated: boolean;
  pinned: boolean;
  source: string;
  curation: string;
  license_note: string;
  install_policy: string;
  query: string;
  count: number;
  install_capability: string;
  entries: PluginPinnedIndexEntry[];
  install: PluginPinnedIndexInstall;
  raw_remote_fetch: PluginCatalogRemote;
}

export interface PluginCatalogResponse {
  ok: boolean;
  state: string;
  schema: string;
  generated_at: string;
  query: string;
  source: string;
  count: number;
  counts: Record<string, number>;
  estop: boolean;
  remote: PluginCatalogRemote;
  pinned_index?: PluginPinnedIndex;
  install_capability: string;
  entries: PluginCatalogEntry[];
}

export interface PluginCatalogValidationCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface PluginCatalogDependencies {
  ok: boolean;
  missing_dependencies: string[];
  missing_plugins: string[];
  incompatible: string[];
  warnings: string[];
  install_hint: string;
}

export interface PluginCatalogConfiguration {
  declared_env: string[];
  optional_env: string[];
  python_dependencies: string[];
  config_surface: string;
  writable_via: string;
}

export interface PluginCatalogDetail extends PluginCatalogEntry {
  validation: { ok: boolean; checks: PluginCatalogValidationCheck[] };
  dependencies: PluginCatalogDependencies;
  configuration: PluginCatalogConfiguration;
  manifest: Record<string, unknown>;
}

export interface PluginCatalogDetailResponse {
  ok: boolean;
  state: string;
  schema: string;
  estop: boolean;
  plugin: PluginCatalogDetail;
}

export interface PluginReloadResponse {
  ok: boolean;
  code?: string;
  reloaded?: boolean;
  changed?: boolean;
  first_reload?: boolean;
  plugin_count?: number;
  catalog_count?: number;
  reloaded_plugins?: string[];
  errors?: Array<{ id: string; code: string; detail: string }>;
  digest?: string;
}

export interface AgentPluginInstallRequest {
  identifier?: string;
  catalog_name?: string;
  ref?: string;
  force?: boolean;
  enable?: boolean;
}

export interface AgentPluginInstallResponse {
  ok: boolean;
  plugin_name?: string;
  warnings?: string[];
  missing_env?: string[];
  after_install_path?: string | null;
  enabled?: boolean;
  error?: string;
}

export interface AgentPluginUpdateResponse {
  ok: boolean;
  name?: string;
  output?: string;
  unchanged?: boolean;
  error?: string;
}

export interface PluginProvidersPutRequest {
  memory_provider?: string;
  context_engine?: string;
}
