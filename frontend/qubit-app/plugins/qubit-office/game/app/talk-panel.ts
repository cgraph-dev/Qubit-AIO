/* The Talk panel's data seam: one office agent's real canonical conversation.
 *
 * WHAT THIS IS
 * ------------
 * Clicking "Talk" on a roster seat opens a conversation panel. Its transcript
 * is the CANONICAL chat-session store — the same one `/chat`, `/qubit-chat`,
 * the CLI and voice use — never a panel-local or fabricated log. A seat with
 * no conversation is a typed empty state; an unreachable token/route is a
 * typed unavailable state; a send is the existing `POST /api/qubit/chat` turn,
 * not a second inference path.
 *
 * WHY A DETERMINISTIC SESSION ID
 * ------------------------------
 * The office roster has no stored seat -> chat-session link, and inventing one
 * server-side is out of scope for this UI lane. A deterministic id per seat
 * (`office-talk-<seat-slug>`) gives the same seat the same conversation across
 * reloads, lets an existing conversation be discovered with one GET, and
 * keeps the transcript in the canonical store rather than in this component.
 * The store returns 404 for a seat with no conversation yet, which is exactly
 * the typed empty state the panel shows.
 *
 * NO FAKE CHATTER
 * ---------------
 * Every text row returned by these helpers came from the API. Nothing here
 * appends an optimistic message, a placeholder reply, or a "thinking" message;
 * pending work is a typed status line in the component, not transcript content.
 */

import { API_PREFIX, hostBasePath, readToken, type AgentSeat } from "./agents-panel";

/** The seat -> session prefix. Kept exported so tests pin the contract. */
export const TALK_SESSION_PREFIX = "office-talk-";

/** The canonical chat routes this panel uses (under the office `/agent`). */
export const TALK_SESSION_ROUTE = "/api/qubit/chat/sessions";
export const TALK_TURN_ROUTE = "/api/qubit/chat";

/** A route-relative URL rebased onto the host's base and office prefix. */
export function talkUrl(path: string, base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const trimmed = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${trimmed}${API_PREFIX}${path}`;
}

/**
 * A deterministic, store-legal session id for a roster seat.
 *
 * The canonical store bounds ids at 80 chars and accepts a free-form string;
 * this helper keeps only URL-safe characters, collapses runs, and never emits
 * the bare prefix (an empty/odd slug still yields a distinct id).
 */
export function talkSessionId(seat: Pick<AgentSeat, "slug">): string {
  const clean = String(seat && seat.slug ? seat.slug : "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${TALK_SESSION_PREFIX}${clean || "seat"}`.slice(0, 80);
}

/** The persisted session title, so the conversation is identifiable later. */
export function talkSessionName(seat: Pick<AgentSeat, "name">): string {
  const name = String(seat && seat.name ? seat.name : "").trim();
  return `Office talk · ${name || "Agent"}`.slice(0, 120);
}

/** One transcript row as the canonical store serves it. */
export interface TalkMessage {
  role: string;
  content: string;
  timestamp?: number;
}

/** The panel's state: every branch carries a typed reason, never a blank box. */
export type TalkState =
  | { kind: "loading" }
  | { kind: "empty"; code: string; detail: string; sessionId: string }
  | {
      kind: "ready";
      sessionId: string;
      name: string;
      messages: TalkMessage[];
    }
  | { kind: "unavailable"; code: string; detail: string };

/** The send result: success carries the real reply, failure carries the code. */
export type TalkSendResult =
  | { ok: true; reply: string }
  | { ok: false; code: string; detail: string };

type JsonFetch = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    credentials?: "same-origin";
    signal?: AbortSignal;
  },
) => Promise<{
  ok?: boolean;
  status: number;
  json: () => Promise<unknown>;
  text?: () => Promise<string>;
}>;

export interface TalkRequestOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
}

function requestHeaders(
  win: Window | null | undefined,
  body: boolean,
): { headers: Record<string, string>; token: string } {
  const token = readToken(win);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers["X-Qubit-Session-Token"] = token;
  return { headers, token };
}

/**
 * Keep only real, displayable rows. A malformed row is dropped rather than
 * rendered as an empty message, and no row is ever synthesised.
 */
export function normalizeTalkMessages(raw: unknown): TalkMessage[] {
  if (!Array.isArray(raw)) return [];
  const out: TalkMessage[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const rec = row as Record<string, unknown>;
    const role = typeof rec.role === "string" ? rec.role : "";
    const content = typeof rec.content === "string" ? rec.content : "";
    if (!role || !content.trim()) continue;
    const message: TalkMessage = { role, content };
    if (typeof rec.timestamp === "number" && Number.isFinite(rec.timestamp)) {
      message.timestamp = rec.timestamp;
    }
    out.push(message);
  }
  return out;
}

/** Rows a conversation panel renders as exchange text (user/assistant only). */
export function visibleTalkMessages(messages: TalkMessage[]): TalkMessage[] {
  return messages.filter((row) => row.role === "user" || row.role === "assistant");
}

/**
 * Read one seat's canonical conversation.
 *
 * A 404 is the store's honest "no conversation exists" answer and becomes a
 * typed empty state. A 401/403 is a typed unavailable state (the composer is
 * disabled); any other failure is unavailable with the HTTP status or the
 * transport error, never a fabricated transcript.
 */
export async function fetchTalkTranscript(
  seat: Pick<AgentSeat, "slug">,
  options: TalkRequestOptions = {},
): Promise<TalkState> {
  const sessionId = talkSessionId(seat);
  const f = ((options.fetchImpl
    ?? (typeof fetch === "function" ? fetch : null)) as JsonFetch | null);
  if (!f) {
    return {
      kind: "unavailable",
      code: "TRANSPORT_UNAVAILABLE",
      detail: "This page has no fetch transport, so the conversation cannot be read.",
    };
  }
  const { headers, token } = requestHeaders(options.win, false);
  if (!token) {
    return {
      kind: "unavailable",
      code: "NO_SESSION_TOKEN",
      detail:
        "The office session token is unavailable, so the canonical chat store "
        + "cannot be reached. Reload the page from the Qubit dashboard.",
    };
  }
  try {
    const response = await f(
      talkUrl(`${TALK_SESSION_ROUTE}/${encodeURIComponent(sessionId)}`,
        options.base),
      { headers, credentials: "same-origin", signal: options.signal },
    );
    if (response.status === 404) {
      return {
        kind: "empty",
        code: "NO_SESSION",
        sessionId,
        detail: "This agent has no conversation in the canonical chat store yet.",
      };
    }
    if (response.status === 401 || response.status === 403) {
      return {
        kind: "unavailable",
        code: "TOKEN_REFUSED",
        detail: `The chat store refused the office session (HTTP ${response.status}).`,
      };
    }
    if (response.ok === false) {
      return {
        kind: "unavailable",
        code: "STORE_ERROR",
        detail: `The chat store answered HTTP ${response.status}.`,
      };
    }
    const payload = (await response.json().catch(() => null)) as
      | { session?: { id?: unknown; name?: unknown }; messages?: unknown }
      | null;
    if (!payload || typeof payload !== "object") {
      return {
        kind: "unavailable",
        code: "BAD_PAYLOAD",
        detail: "The chat store answered with no JSON object.",
      };
    }
    const session = payload.session && typeof payload.session === "object"
      ? payload.session as { id?: unknown; name?: unknown }
      : null;
    if (!session) {
      return {
        kind: "unavailable",
        code: "BAD_PAYLOAD",
        detail: "The chat store answered without a session record.",
      };
    }
    return {
      kind: "ready",
      sessionId: typeof session.id === "string" && session.id
        ? session.id : sessionId,
      name: typeof session.name === "string" ? session.name : "",
      messages: normalizeTalkMessages(payload.messages),
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      kind: "unavailable",
      code: "NETWORK",
      detail: `The conversation could not be reached: ${message.slice(0, 200)}`,
    };
  }
}

/**
 * Create the seat's conversation if it does not exist, then read it.
 *
 * A concurrent create is not an error: the store answers 409/EXISTS and this
 * falls through to the same transcript read.
 */
export async function startTalkSession(
  seat: Pick<AgentSeat, "slug" | "name">,
  options: TalkRequestOptions = {},
): Promise<TalkState> {
  const sessionId = talkSessionId(seat);
  const f = ((options.fetchImpl
    ?? (typeof fetch === "function" ? fetch : null)) as JsonFetch | null);
  if (!f) {
    return {
      kind: "unavailable",
      code: "TRANSPORT_UNAVAILABLE",
      detail: "This page has no fetch transport, so a conversation cannot be started.",
    };
  }
  const { headers, token } = requestHeaders(options.win, true);
  if (!token) {
    return {
      kind: "unavailable",
      code: "NO_SESSION_TOKEN",
      detail:
        "The office session token is unavailable, so a conversation cannot be "
        + "started. Reload the page from the Qubit dashboard.",
    };
  }
  try {
    const response = await f(talkUrl(TALK_SESSION_ROUTE, options.base), {
      method: "POST",
      headers,
      body: JSON.stringify({ id: sessionId, name: talkSessionName(seat) }),
      credentials: "same-origin",
      signal: options.signal,
    });
    if (response.status === 401 || response.status === 403) {
      return {
        kind: "unavailable",
        code: "TOKEN_REFUSED",
        detail: `The chat store refused the office session (HTTP ${response.status}).`,
      };
    }
    // 409/EXISTS is fine: another tab created it; read its transcript below.
    if (response.ok !== false && response.status !== 409) {
      return fetchTalkTranscript(seat, options);
    }
    if (response.status === 409) {
      return fetchTalkTranscript(seat, options);
    }
    const payload = (await response.json().catch(() => null)) as
      | { code?: unknown; detail?: unknown }
      | null;
    const code = payload && typeof payload.code === "string"
      ? payload.code : "CREATE_FAILED";
    const detail = payload && typeof payload.detail === "string"
      ? payload.detail : `The chat store answered HTTP ${response.status}.`;
    return { kind: "unavailable", code, detail };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      kind: "unavailable",
      code: "NETWORK",
      detail: `The conversation could not be started: ${message.slice(0, 200)}`,
    };
  }
}

/**
 * Send one turn through the canonical non-streaming chat action.
 *
 * The reply is returned exactly as the store produced it; on any refusal the
 * result is typed and no reply text is invented.
 */
export async function sendTalkMessage(
  seat: Pick<AgentSeat, "slug">,
  text: string,
  options: TalkRequestOptions = {},
): Promise<TalkSendResult> {
  const trimmed = String(text || "").trim();
  if (!trimmed) {
    return { ok: false, code: "EMPTY_MESSAGE", detail: "Type a message first." };
  }
  const sessionId = talkSessionId(seat);
  const f = ((options.fetchImpl
    ?? (typeof fetch === "function" ? fetch : null)) as JsonFetch | null);
  if (!f) {
    return {
      ok: false,
      code: "TRANSPORT_UNAVAILABLE",
      detail: "This page has no fetch transport, so the message cannot be sent.",
    };
  }
  const { headers, token } = requestHeaders(options.win, true);
  if (!token) {
    return {
      ok: false,
      code: "NO_SESSION_TOKEN",
      detail:
        "The office session token is unavailable, so the message cannot be sent.",
    };
  }
  try {
    const response = await f(talkUrl(TALK_TURN_ROUTE, options.base), {
      method: "POST",
      headers,
      body: JSON.stringify({ text: trimmed, session: sessionId }),
      credentials: "same-origin",
      signal: options.signal,
    });
    const payload = (await response.json().catch(() => null)) as
      | {
          ok?: unknown; reply?: unknown; text?: unknown;
          code?: unknown; detail?: unknown;
        }
      | null;
    if (response.ok !== false && payload && payload.ok === true) {
      const reply = typeof payload.reply === "string"
        ? payload.reply
        : (typeof payload.text === "string" ? payload.text : "");
      if (reply) return { ok: true, reply };
      return {
        ok: false,
        code: "EMPTY_REPLY",
        detail: "The agent turn completed without reply text.",
      };
    }
    const code = payload && typeof payload.code === "string"
      ? payload.code
      : (response.status === 401 || response.status === 403
        ? "TOKEN_REFUSED" : "SEND_FAILED");
    const detail = payload && typeof payload.detail === "string"
      ? payload.detail
      : `The chat action answered HTTP ${response.status}.`;
    return { ok: false, code, detail };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      code: "NETWORK",
      detail: `The message could not be sent: ${message.slice(0, 200)}`,
    };
  }
}
