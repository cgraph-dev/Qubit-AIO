/* Per-seat model/provider panel — Qubit-owned, over the Office iframe.
 *
 * WHY THIS IS NOT INSIDE THE IF-RAME
 * ----------------------------------
 * The vendored Habblaud client cannot mutate anything: `installOfficeApiBridge`
 * answers every non-GET with a 501 by design (the upstream app was written
 * against an unauthenticated server, so Qubit routes its writes through the
 * confirmation authority instead). A model picker is a write. Mounting it in
 * the iframe would mean either defeating that guard or shipping a control that
 * reports success and does nothing.
 *
 * So the panel lives here, in the Qubit-owned shell, and talks to Qubit's own
 * API directly with the host session token:
 *
 *   GET  /agent/api/office/habblaud/agents/<id>/model
 *   POST /agent/api/office/habblaud/agents/<id>/model
 *
 * The GET returns the seat's current assignment AND the selectable catalogue —
 * the same `/api/model/options` payload the Model Provider panel serves — so
 * this panel and the Model Provider panel can never offer different providers.
 *
 * The pure helpers below carry all the decisions (what to show, what to send),
 * so they are testable without a DOM.
 */

/** The Office API prefix, matching api-bridge.ts's default. */
export const OFFICE_API_PREFIX = "/agent";
export const SEAT_MODEL_ROUTE = `${OFFICE_API_PREFIX}/api/office/habblaud/agents`;

/** One provider row as `/api/model/options` serves it (secrets already stripped). */
export interface ProviderOption {
  slug?: string;
  name?: string;
  models?: string[];
  authenticated?: boolean;
  is_current?: boolean;
}

/** The seat-model GET payload. */
export interface SeatModelState {
  agent_id?: number;
  slug?: string;
  model?: string;
  provider?: string;
  inherits?: boolean;
  providers?: ProviderOption[];
  providers_note?: string;
  detail?: string;
  ok?: boolean;
  code?: string;
}

export function seatModelUrl(agentId: string | number): string {
  return `${SEAT_MODEL_ROUTE}/${encodeURIComponent(String(agentId))}/model`;
}

/** "provider/model" for display, or the inherit notice. Never a blank cell. */
export function describeSeatRouting(state: SeatModelState | null): string {
  if (!state || !state.ok) return "Model assignment unavailable";
  if (state.inherits || (!state.model && !state.provider)) {
    return "Inherits the profile default model";
  }
  const provider = state.provider || "default provider";
  return state.model ? `${provider} / ${state.model}` : provider;
}

/** Only providers the host actually holds a credential for are selectable. */
export function selectableProviders(state: SeatModelState | null): ProviderOption[] {
  const rows = state?.providers ?? [];
  return rows
    .filter((row) => !!row && typeof row.slug === "string" && row.slug.length > 0)
    .filter((row) => (row.models ?? []).length > 0)
    .filter((row) => row.authenticated !== false);
}

export function modelsFor(state: SeatModelState | null, providerSlug: string): string[] {
  const row = selectableProviders(state).find((r) => r.slug === providerSlug);
  return [...(row?.models ?? [])];
}

/**
 * The body to POST. An empty model is a deliberate CLEAR (back to inherit),
 * so it is sent as an empty string rather than omitted — omitting it would
 * read as "no change" on the server.
 */
export function assignmentPayload(provider: string, model: string): {
  provider: string;
  model: string;
} {
  return { provider: (provider ?? "").trim(), model: (model ?? "").trim() };
}

/**
 * Turn a failed response into one sentence a person can act on. The server's
 * typed codes are the source; this never invents a cause.
 */
export function describeWriteFailure(status: number, body: unknown): string {
  const code =
    body && typeof body === "object" && "code" in (body as Record<string, unknown>)
      ? String((body as Record<string, unknown>).code)
      : "";
  const detail =
    body && typeof body === "object" && "detail" in (body as Record<string, unknown>)
      ? String((body as Record<string, unknown>).detail)
      : "";
  if (code === "UNKNOWN_MODEL") {
    return "That model is not in the catalogue. Refresh the model list, or assign it as a custom model.";
  }
  if (code === "AGENT_NOT_FOUND") return "That seat no longer exists; reload the office.";
  if (code === "ESTOP_ENGAGED") return "The emergency stop is latched; release it before changing a seat.";
  if (code === "MALFORMED") return "The request was incomplete; pick a provider and a model.";
  if (detail) return detail;
  return `The change was not saved (HTTP ${status}).`;
}

/** Attach the host session token, exactly as api-bridge.ts does for reads. */
export function authHeaders(token: string): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Qubit-Session-Token"] = token;
  return headers;
}

function hostToken(win: Window | null | undefined): string {
  if (!win) return "";
  const own = (win as Window & { __QUBIT_SESSION_TOKEN__?: string }).__QUBIT_SESSION_TOKEN__;
  if (own) return own;
  try {
    const parent = win.parent as unknown as { __QUBIT_SESSION_TOKEN__?: string } | null;
    return (parent && parent.__QUBIT_SESSION_TOKEN__) || "";
  } catch {
    return "";
  }
}

export async function fetchSeatModel(
  agentId: string | number,
  fetchImpl: typeof fetch,
  win?: Window | null,
): Promise<SeatModelState> {
  const res = await fetchImpl(seatModelUrl(agentId), {
    method: "GET",
    credentials: "same-origin",
    headers: authHeaders(hostToken(win)),
  });
  const body = (await res.json().catch(() => ({}))) as SeatModelState;
  if (!res.ok || body.ok === false) {
    return { ...body, ok: false, detail: describeWriteFailure(res.status, body) };
  }
  return { ...body, ok: true };
}

export async function saveSeatModel(
  agentId: string | number,
  provider: string,
  model: string,
  fetchImpl: typeof fetch,
  win?: Window | null,
): Promise<SeatModelState> {
  const res = await fetchImpl(seatModelUrl(agentId), {
    method: "POST",
    credentials: "same-origin",
    headers: authHeaders(hostToken(win)),
    body: JSON.stringify(assignmentPayload(provider, model)),
  });
  const body = (await res.json().catch(() => ({}))) as SeatModelState;
  if (!res.ok || body.ok === false) {
    return { ...body, ok: false, detail: describeWriteFailure(res.status, body) };
  }
  return { ...body, ok: true };
}
