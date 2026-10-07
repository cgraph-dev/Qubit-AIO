/**
 * Subscribe to chat-session live-update frames from the single Qubit SSE feed.
 *
 * One reader for the whole `/chat` surface: the session list, the MODEL card,
 * and ChatPage's own model state all need to react to `session.created` /
 * `session.updated` / `session.deleted` / `model.changed`. Rather than each
 * mounting its own stream (three sockets/reconnects), ChatPage mounts this
 * once and fans the frames out through callbacks.
 *
 * Reuses `openEventsStream` + `parseSseChunk` (the same parser, auth path and
 * reconnect policy the sidebar and `useChatActivity` use) — no second event
 * stack. Fail-soft by contract: a stream error never throws into React; the
 * caller keeps its last-known state and the reader retries on a bounded
 * backoff.
 */
import { useEffect, useRef } from "react";

import { chatLiveFrame, type ChatSessionFrame } from "@/lib/chatLiveEvents";
import { openEventsStream, parseSseChunk } from "@/lib/events-sse";
import { QUBIT_BASE_PATH } from "@/lib/api";

const RECONNECT_MS = 3000;
const MAX_RECONNECT_MS = 30000;

export type ChatLiveHandler = (frame: ChatSessionFrame) => void;

/**
 * `version` rebuilds the reader on demand (a manual refresh path). `enabled`
 * gates the whole subscription so a page that does not use it never dials.
 */
export function useChatLiveEvents(
  onFrame: ChatLiveHandler,
  { enabled = true, version = 0 }: { enabled?: boolean; version?: number } = {},
): void {
  // The handler lives in a ref so a caller that passes an inline closure (a
  // new identity every render) does not restart the stream each render.
  const handlerRef = useRef<ChatLiveHandler>(onFrame);
  handlerRef.current = onFrame;

  useEffect(() => {
    if (!enabled) return;

    let unmounting = false;
    let controller: AbortController | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoff = RECONNECT_MS;
    let lastSeq: number | null = null;

    const scheduleReconnect = () => {
      if (unmounting || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, backoff);
      backoff = Math.min(backoff * 2, MAX_RECONNECT_MS);
    };

    const connect = async () => {
      if (unmounting) return;
      try {
        const attempt = new AbortController();
        controller = attempt;
        const conn = await openEventsStream({
          lastSeq,
          signal: attempt.signal,
          basePath: QUBIT_BASE_PATH,
        });
        if (unmounting) return;
        if (!conn.ok || !conn.reader) {
          scheduleReconnect();
          return;
        }
        backoff = RECONNECT_MS;

        const reader = conn.reader;
        const decoder = new TextDecoder();
        let buffer = "";
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parsed = parseSseChunk(buffer);
            buffer = parsed.rest;
            if (parsed.resync) {
              try {
                await reader.cancel();
              } catch {
                /* already released */
              }
              attempt.abort();
              scheduleReconnect();
              break;
            }
            for (const frame of parsed.frames) {
              if (frame.event === "resync" || !frame.data) continue;
              if (frame.id != null) lastSeq = frame.id;
              let rec: Record<string, unknown>;
              try {
                rec = JSON.parse(frame.data) as Record<string, unknown>;
              } catch {
                continue;
              }
              const live = chatLiveFrame(rec);
              if (live) handlerRef.current(live);
            }
          }
        } catch {
          /* aborted or dropped — reconnect path below decides */
        }
        if (unmounting) return;
        if (!reconnectTimer) scheduleReconnect();
      } catch {
        // The stream transport itself failed to initialize (e.g. a broken or
        // unmocked base path). The hook's contract is fail-soft: a stream
        // error must never throw into React and break the whole page, so we
        // simply retry on the bounded backoff and leave the caller's state.
        if (!unmounting) scheduleReconnect();
      }
    };

    void connect();

    return () => {
      unmounting = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      controller?.abort();
    };
  }, [enabled, version]);
}
