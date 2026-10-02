// ROUTES MANIFEST — Qubit webchat family (integrator implements verbatim).
// Every HTTP path used by this module, with its method. All paths already
// exist server-side; this module invents no routes and performs no fetches
// outside this list (reuse-only — NEEDS_BRIDGE is empty by design).
//
//   Webchat (minimal chat on the Qubit SSE transport — explicitly NOT a
//   duplex socket; no duplex runtime exists):
//     GET  /api/qubit/chat/sessions                  (webchat.sessions.list)
//     POST /api/qubit/chat/sessions                  (webchat.sessions.create)
//     GET  /api/qubit/chat/sessions/{id}/messages    (webchat.sessions.history)
//     POST /api/qubit/chat/stream                    (webchat.turn.stream, SSE)
//     POST /api/qubit/chat                           (webchat.turn.send)
//
// Transport note: turns stream over `POST /api/qubit/chat/stream` (the same
// `voicebridge.chat_transport` SSE module both chats use). Session state is
// server-backed, so a reload or a reconnect resumes the stored conversation
// by session id. This page carries no voice controls (the voice surface) and
// no terminal/PTY (the terminal surface).
//
// Endpoint reality note (verified read-only 2026-10-02): `POST /api/qubit/chat`
// (non-streaming turn) exists on the backend (`qubit_compat` chat-turn route)
// but `frontend/qubit-web/src/lib/api.ts` ships no wrapper for it — so
// `sendWebchatTurn` calls it through the shared `fetchJSON` helper instead.
// Everything else delegates to the existing `api` session-store methods and
// `streamChatTurn`.

import {
  api,
  fetchJSON,
  streamChatTurn,
  type ChatStoreCreateResponse,
  type ChatStoreMessagesResponse,
  type ChatStoreSessionsResponse,
  type ChatStreamEvent,
  type ChatStreamHandlers,
} from "@/lib/api";

/** One canonical route in the webchat namespace. */
export interface FamilyRoute {
  family: "webchat";
  action: string;
  method: "GET" | "POST";
  path: string;
}

/**
 * Manifest of every path+method this module calls. All rows are reuse-only:
 * each path already exists server-side. `{id}` is substituted at call time.
 */
export const ROUTES: FamilyRoute[] = [
  { family: "webchat", action: "webchat.sessions.list", method: "GET", path: "/api/qubit/chat/sessions" },
  { family: "webchat", action: "webchat.sessions.create", method: "POST", path: "/api/qubit/chat/sessions" },
  { family: "webchat", action: "webchat.sessions.history", method: "GET", path: "/api/qubit/chat/sessions/{id}/messages" },
  { family: "webchat", action: "webchat.turn.stream", method: "POST", path: "/api/qubit/chat/stream" },
  { family: "webchat", action: "webchat.turn.send", method: "POST", path: "/api/qubit/chat" },
];

/**
 * Endpoints this module would need a new backend bridge for. Empty by
 * design: every webchat capability reuses an existing chat endpoint.
 */
export const NEEDS_BRIDGE: string[] = [];

/* ------------------------------------------------------------------ */
/*  sessions                                                            */
/* ------------------------------------------------------------------ */

/** webchat.sessions.list: the server-backed chat-session store (read-only). */
export function listWebchatSessions(
  limit = 50,
  offset = 0,
): Promise<ChatStoreSessionsResponse> {
  return api.listChatSessions(limit, offset);
}

/** webchat.sessions.create: open a fresh server-backed session. */
export function createWebchatSession(
  name = "",
): Promise<ChatStoreCreateResponse> {
  return api.createChatSession(name);
}

/** webchat.sessions.history: reload the persisted transcript (read-only). */
export function getWebchatHistory(
  id: string,
  limit = 500,
): Promise<ChatStoreMessagesResponse> {
  return api.getChatSessionMessages(id, limit);
}

/* ------------------------------------------------------------------ */
/*  turns                                                               */
/* ------------------------------------------------------------------ */

/** Body for one webchat turn (mirrors the shared SSE transport contract). */
export interface WebchatTurnBody {
  text: string;
  session?: string;
  model?: string;
  reasoning?: string;
}

/**
 * webchat.turn.stream: one persisted agent turn over SSE
 * (`POST /api/qubit/chat/stream`). Frames surface through the shared
 * handlers: `start` / `delta` / `progress` (+ `report`) / `done`, with
 * typed `error` / `cancelled` instead of fabricated output.
 */
export function streamWebchatTurn(
  body: WebchatTurnBody,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamChatTurn(body, handlers, signal);
}

/** Non-streaming turn receipt (`POST /api/qubit/chat`, reuse-only). */
export interface WebchatTurnResponse {
  reply?: string;
  session_id?: string;
  state?: string;
  code?: string;
  detail?: string;
  [key: string]: unknown;
}

/**
 * webchat.turn.send: the same turn without streaming
 * (`POST /api/qubit/chat`). Exists for parity with the backend route; the
 * page prefers `streamWebchatTurn`.
 */
export function sendWebchatTurn(
  body: WebchatTurnBody,
): Promise<WebchatTurnResponse> {
  return fetchJSON<WebchatTurnResponse>("/api/qubit/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const ocFamilyWebchatApi = {
  ROUTES,
  NEEDS_BRIDGE,
  listWebchatSessions,
  createWebchatSession,
  getWebchatHistory,
  streamWebchatTurn,
  sendWebchatTurn,
};

export type { ChatStreamEvent, ChatStreamHandlers };
