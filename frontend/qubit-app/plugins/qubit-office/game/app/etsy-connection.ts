/* Typed client for the Etsy OAuth connection (connection only).
 *
 * WHAT IT READS/WRITES
 * --------------------
 * ``GET  /agent/api/office/business/etsy/status``   — typed status
 * ``POST /agent/api/office/business/etsy/authorize`` — pending PKCE URL
 * ``POST /agent/api/office/business/etsy/complete``  — exchange {code,state}
 * ``POST /agent/api/office/business/etsy/disconnect``— clear the local token
 *
 * The backend never returns an access token, refresh token, client secret or
 * code verifier, so neither does this client. A "connected" label is shown
 * only when the server reports ``state: "connected"`` AND ``connected: true``;
 * an absent/failed read is an error, never an optimistic success.
 *
 * This module does not list, publish, price, refund, message or spend. Those
 * remain behind the existing Business OS approval gates.
 */
import { API_PREFIX, hostBasePath, readToken } from "./agents-panel";

export const ETSY_PATH = "/api/office/business/etsy";
export const ETSY_STATUS_PATH = `${ETSY_PATH}/status`;
export const ETSY_AUTHORIZE_PATH = `${ETSY_PATH}/authorize`;
export const ETSY_COMPLETE_PATH = `${ETSY_PATH}/complete`;
export const ETSY_DISCONNECT_PATH = `${ETSY_PATH}/disconnect`;

export type EtsyConnectionState =
  | "not_configured"
  | "authorization_required"
  | "connected"
  | "expired"
  | "error";

export interface EtsyStatus {
  platform: string;
  state: EtsyConnectionState | string;
  configured: boolean;
  connected: boolean;
  missing: string[];
  scopes: string[];
  redirect_uri?: string;
  code?: string;
  detail?: string;
  pending?: boolean;
  pending_expires_at?: number;
  expires_at?: number;
  expires_in?: number;
  obtained_at?: number;
  has_refresh?: boolean;
  scope?: string;
}

export interface EtsyStatusPayload {
  ok?: boolean;
  schema?: string;
  etsy?: EtsyStatus;
  code?: string;
  detail?: string;
  explanation?: string;
  state?: string;
}

export interface EtsyAuthorizationStart {
  url: string;
  state: string;
  expires_at: number;
}

/** What the panel renders: a real status, or a NAMED reason there is none. */
export type EtsyReadState =
  | { kind: "loading" }
  | { kind: "ready"; status: EtsyStatus }
  | { kind: "error"; message: string; code?: string };

export class EtsyApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "EtsyApiError";
    this.code = code;
    this.status = status;
  }
}

function rebase(base: string | undefined, path: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${b}${API_PREFIX}${path}`;
}

/** The status URL, rebased onto the dashboard's base path. */
export function etsyStatusUrl(base?: string): string {
  return rebase(base, ETSY_STATUS_PATH);
}

export function etsyAuthorizeUrl(base?: string): string {
  return rebase(base, ETSY_AUTHORIZE_PATH);
}

export function etsyCompleteUrl(base?: string): string {
  return rebase(base, ETSY_COMPLETE_PATH);
}

export function etsyDisconnectUrl(base?: string): string {
  return rebase(base, ETSY_DISCONNECT_PATH);
}

function headers(win?: Window | null): Record<string, string> {
  const token = readToken(win);
  const out: Record<string, string> = { Accept: "application/json" };
  if (token) out["X-Qubit-Session-Token"] = token;
  return out;
}

/** True only for the server's own connected status. */
export function isConnected(status: EtsyStatus | null | undefined): boolean {
  return Boolean(status && status.state === "connected" && status.connected);
}

/** A short, honest label for the status pill. */
export function statusLabel(status: EtsyStatus): string {
  switch (status.state) {
    case "connected": return isConnected(status) ? "Connected" : "Not connected";
    case "authorization_required": return "Not connected";
    case "expired": return "Expired";
    case "not_configured": return "Not configured";
    case "error": return "Error";
    default: return "Unknown";
  }
}

/** What is missing, in plain words. An empty list is a real answer. */
export function missingText(status: EtsyStatus): string {
  if (status.state === "not_configured") {
    const labels = status.missing.length ? status.missing : ["client settings"];
    return `Set ${labels.join(", ")} in the Etsy OAuth configuration.`;
  }
  if (status.state === "authorization_required") {
    return "Etsy settings are present; authorize an account to connect it.";
  }
  if (status.state === "expired") {
    return status.has_refresh
      ? "The access token expired; reconnect or refresh the account."
      : "The access token expired; reconnect the account.";
  }
  if (status.state === "error") {
    return status.detail || status.code || "Etsy status could not be read.";
  }
  return "";
}

/**
 * Read the typed status. Never throws: a transport/HTTP/refusal failure is a
 * named error state so the panel cannot render "connected" by default.
 */
export async function fetchEtsyStatus(opts: {
  fetchImpl?: typeof fetch;
  win?: Window | null;
  base?: string;
  signal?: AbortSignal;
} = {}): Promise<EtsyReadState> {
  const f = opts.fetchImpl ?? fetch;
  try {
    const res = await f(etsyStatusUrl(opts.base), {
      headers: headers(opts.win),
      credentials: "same-origin",
      signal: opts.signal,
    });
    if (res.status === 401) {
      return {
        kind: "error",
        message: "Etsy status was refused (HTTP 401); the office session "
          + "token was not accepted. Reload the page to refresh it.",
        code: "UNAUTHORIZED",
      };
    }
    const body = (await res.json().catch(() => null)) as EtsyStatusPayload | null;
    if (!body || typeof body !== "object") {
      return {
        kind: "error",
        message: `Etsy status answered HTTP ${res.status} with no JSON body.`,
      };
    }
    if (body.ok !== true || !body.etsy) {
      return {
        kind: "error",
        message: body.detail || body.code
          ? `Etsy status could not be read: ${body.detail || body.code}`
          : `Etsy status answered not-ok (HTTP ${res.status}).`,
        code: body.code,
      };
    }
    return { kind: "ready", status: body.etsy };
  } catch (exc) {
    return {
      kind: "error",
      message: `Etsy status could not be reached: ${
        exc instanceof Error ? exc.message : String(exc)}`,
    };
  }
}

async function post<T>(
  path: string,
  body: Record<string, unknown>,
  opts: {
    fetchImpl?: typeof fetch;
    win?: Window | null;
    base?: string;
    signal?: AbortSignal;
  },
): Promise<T> {
  const f = opts.fetchImpl ?? fetch;
  const requestHeaders = headers(opts.win);
  requestHeaders["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await f(rebase(opts.base, path), {
      method: "POST",
      headers: requestHeaders,
      credentials: "same-origin",
      body: JSON.stringify({ owner: true, ...body }),
      signal: opts.signal,
    });
  } catch (exc) {
    throw new EtsyApiError(
      `The Etsy connection could not be reached: ${
        exc instanceof Error ? exc.message : String(exc)}`,
      "ETSY_UNREACHABLE",
      0,
    );
  }
  const parsed = (await res.json().catch(() => null)) as
    | (T & EtsyStatusPayload)
    | null;
  if (!parsed || typeof parsed !== "object") {
    throw new EtsyApiError(
      `The Etsy connection answered HTTP ${res.status} with no JSON body.`,
      "ETSY_BAD_RESPONSE",
      res.status,
    );
  }
  if (!res.ok || parsed.ok !== true) {
    const code = String(parsed.code || `ETSY_HTTP_${res.status}`);
    const detail = String(parsed.detail || parsed.explanation
      || `HTTP ${res.status}`);
    throw new EtsyApiError(detail, code, res.status);
  }
  return parsed as T;
}

/** Start the OAuth flow: returns the URL to open and the pending state. */
export async function startEtsyAuthorization(opts: {
  fetchImpl?: typeof fetch;
  win?: Window | null;
  base?: string;
  signal?: AbortSignal;
} = {}): Promise<EtsyAuthorizationStart> {
  const payload = await post<{ etsy: EtsyAuthorizationStart }>(
    ETSY_AUTHORIZE_PATH, {}, opts);
  const started = payload.etsy;
  if (!started || typeof started.url !== "string" || !started.url) {
    throw new EtsyApiError(
      "The Etsy authorization route did not return a URL.",
      "ETSY_NO_URL",
      502,
    );
  }
  return started;
}

/** Exchange the redirected code. The response is the real resulting status. */
export async function completeEtsyAuthorization(
  code: string,
  state: string,
  opts: {
    fetchImpl?: typeof fetch;
    win?: Window | null;
    base?: string;
    signal?: AbortSignal;
  } = {},
): Promise<EtsyStatus> {
  const cleanCode = String(code ?? "").trim();
  const cleanState = String(state ?? "").trim();
  if (!cleanCode || !cleanState) {
    throw new EtsyApiError(
      "Both the authorization code and state are required.",
      "ETSY_BAD_INPUT",
      400,
    );
  }
  const payload = await post<{ etsy: EtsyStatus }>(
    ETSY_COMPLETE_PATH, { code: cleanCode, state: cleanState }, opts);
  if (!payload.etsy) {
    throw new EtsyApiError(
      "The Etsy completion route returned no status.",
      "ETSY_NO_STATUS",
      502,
    );
  }
  return payload.etsy;
}

/** Remove the local Etsy token (does not call Etsy or touch a listing). */
export async function disconnectEtsy(opts: {
  fetchImpl?: typeof fetch;
  win?: Window | null;
  base?: string;
  signal?: AbortSignal;
} = {}): Promise<EtsyStatus> {
  const payload = await post<{ etsy: EtsyStatus }>(
    ETSY_DISCONNECT_PATH, {}, opts);
  if (!payload.etsy) {
    throw new EtsyApiError(
      "The Etsy disconnect route returned no status.",
      "ETSY_NO_STATUS",
      502,
    );
  }
  return payload.etsy;
}

/**
 * Extract the ``code`` from a pasted Etsy redirect URL.
 *
 * A full URL is parsed; a bare code is returned unchanged (trimmed). An empty
 * value stays empty so the caller can refuse it rather than send "undefined".
 */
export function parseAuthorizationInput(value: string): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw);
    if (url.searchParams.get("code")) {
      return String(url.searchParams.get("code")).trim();
    }
  } catch {
    // Not a URL: treat the text as the bare code.
  }
  return raw;
}

/** Extract the OAuth ``state`` when the input is a full redirect URL. */
export function parseStateInput(value: string): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw);
    return String(url.searchParams.get("state") || "").trim();
  } catch {
    return "";
  }
}
