/**
 * Fetch-based SSE reader for the Qubit typed-event feed.
 *
 * Qubit keeps exactly one event path: the read-only SSE feed served by
 * ``voicebridge/webui.py:_events_stream`` at ``/api/events/stream``. Every
 * WebSocket event subscribe (``/api/events``, ``/api/ws``) answers HTTP 426
 * ``WS_SINGLE_PATH``, so there is no socket to open. ``EventSource`` cannot
 * attach the dashboard auth header, so the feed is consumed with ``fetch()``
 * plus a ``ReadableStream``/``TextDecoder`` frame parser.
 *
 * Wire contract (server side):
 *   - ``retry: 3000`` on connect;
 *   - ``id: <journal-seq>`` + ``data: <AG-UI json>`` per event;
 *   - ``: ping`` comment heartbeats every ~15s;
 *   - ``: resync`` / ``event: resync`` / ``data: {}`` only on a *genuine*
 *     journal cursor gap (a dropped seq above the client's cursor) — the
 *     client resumes from the retained ``lastSeq`` and the journal replay
 *     fills the gap; it does NOT restart cold.
 * Replay resumes from ``?last_seq=<id>`` (the client-side equivalent of the
 * ``Last-Event-ID`` request header).
 */

/** Native Qubit SSE route (``GET /api/events/stream``). */
export const EVENTS_STREAM_PATH = "/api/events/stream";

const SSE_ACCEPT = "text/event-stream";
const SESSION_HEADER = "X-Qubit-Session-Token";
// The native SSE gate (``_api_token_ok``) reads only ``X-Api-Token``; the
// SPA's canonical header is ``X-Qubit-Session-Token``. Both carry the same
// server-injected token, so send both and satisfy either gate.
const API_TOKEN_HEADER = "X-Api-Token";

export interface SseFrame {
  /** Journal sequence from ``id:``, or null when the frame omits it. */
  id: number | null;
  /** ``event:`` name; defaults to ``message``. */
  event: string;
  /** Concatenated ``data:`` lines. */
  data: string;
}

export interface ParsedSseChunk {
  frames: SseFrame[];
  /** Bytes not yet terminated by a blank-line frame boundary. */
  rest: string;
  /** A ``resync`` sentinel was seen — drop the cursor and replay from zero. */
  resync: boolean;
}

/**
 * Parse every complete SSE frame out of an accumulating buffer.
 *
 * Pure and DOM-free so it can be unit-tested without a stream. Comment lines
 * (``: ping``) and field-only blocks (``retry: 3000``) are not frames.
 */
export function parseSseChunk(buffer: string): ParsedSseChunk {
  const frames: SseFrame[] = [];
  let rest = buffer;
  let resync = false;

  let index = rest.indexOf("\n\n");
  while (index >= 0) {
    const block = rest.slice(0, index);
    rest = rest.slice(index + 2);
    const frame = parseSseBlock(block);
    if (frame) {
      if (frame.event === "resync") {
        resync = true;
      }
      frames.push(frame);
    }
    index = rest.indexOf("\n\n");
  }

  return { frames, rest, resync };
}

function parseSseBlock(block: string): SseFrame | null {
  let id: number | null = null;
  let event = "message";
  let data = "";
  let hasField = false;

  for (const rawLine of block.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line.startsWith(":")) {
      continue; // comment / heartbeat
    }
    const colon = line.indexOf(":");
    if (colon < 0) {
      continue;
    }
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, "");

    if (field === "id") {
      const parsed = Number.parseInt(value, 10);
      if (Number.isFinite(parsed)) {
        id = parsed;
      }
    } else if (field === "event") {
      event = value || "message";
    } else if (field === "data") {
      data += (data ? "\n" : "") + value;
      hasField = true;
    }
  }

  if (!hasField && id === null && event === "message") {
    return null; // comment or non-frame field block
  }
  return { id, event, data };
}

/**
 * ``GET /api/events/stream`` URL with the replay cursor appended when known.
 * ``lastSeq === null`` deliberately omits ``?last_seq=`` so the server
 * replays the whole journal (the safe baseline after a resync / first
 * connect).
 */
export function eventsStreamUrl(lastSeq: number | null, basePath = ""): string {
  const query = lastSeq != null ? `?last_seq=${encodeURIComponent(String(lastSeq))}` : "";
  return `${basePath}${EVENTS_STREAM_PATH}${query}`;
}

function eventsStreamHeaders(): Headers {
  const headers = new Headers({ Accept: SSE_ACCEPT });
  const token =
    typeof window !== "undefined" ? window.__QUBIT_SESSION_TOKEN__ ?? "" : "";
  if (token) {
    headers.set(SESSION_HEADER, token);
    headers.set(API_TOKEN_HEADER, token);
  }
  return headers;
}

export interface EventsStreamRequest {
  /** Journal cursor to resume from; null replays the whole journal. */
  lastSeq: number | null;
  /** Aborts the in-flight fetch and releases the reader on unmount/timeout. */
  signal: AbortSignal;
  /** Server base path (``window.__QUBIT_BASE_PATH__``); usually "". */
  basePath?: string;
}

export interface EventsStreamConnection {
  ok: boolean;
  status: number;
  /** True on 401/403: retrying cannot succeed without re-auth. */
  authRequired: boolean;
  reader: ReadableStreamDefaultReader<Uint8Array> | null;
}

/**
 * Open the SSE stream with the dashboard auth header. Never throws: a network
 * failure or non-2xx becomes a typed result so the caller owns retry policy.
 */
export async function openEventsStream(
  request: EventsStreamRequest,
): Promise<EventsStreamConnection> {
  try {
    const res = await fetch(eventsStreamUrl(request.lastSeq, request.basePath ?? ""), {
      headers: eventsStreamHeaders(),
      credentials: "include",
      signal: request.signal,
    });
    if (res.status === 401 || res.status === 403) {
      return { ok: false, status: res.status, authRequired: true, reader: null };
    }
    if (!res.ok || !res.body) {
      return { ok: false, status: res.status, authRequired: false, reader: null };
    }
    return {
      ok: true,
      status: res.status,
      authRequired: false,
      reader: res.body.getReader(),
    };
  } catch {
    return { ok: false, status: 0, authRequired: false, reader: null };
  }
}
