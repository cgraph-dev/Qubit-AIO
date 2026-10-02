/**
 * Qubit WebChat — a separate minimal chat page on the Qubit SSE transport.
 *
 * Explicitly NOT a duplex socket: turns stream over
 * `POST /api/qubit/chat/stream` (the same `voicebridge.chat_transport` SSE
 * module both chats use), mirroring the sibling stream-reader pattern
 * (`start` / `delta` / `progress` / `report` / `done` frames). Session state
 * is server-backed: the page creates or reuses a chat-session id, reloads
 * the persisted transcript on mount, and reconnects with resume (a failed
 * turn keeps the transcript and offers Retry on the SAME session id).
 * Display projection mirrors the upstream webchat semantics the port keeps:
 * bounded history load, session-id continuity across reconnects, and no
 * local transcript invention. No voice controls (the voice surface owns
 * those), no PTY (the terminal surface owns that).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { Markdown } from "@/components/Markdown";
import {
  createWebchatSession,
  getWebchatHistory,
  listWebchatSessions,
  streamWebchatTurn,
} from "@/lib/ocFamilyWebchatApi";
import type { ChatStreamEvent } from "@/lib/api";

interface WebchatMessage {
  role: "user" | "assistant";
  text: string;
  /** Small render-time metadata line (model / elapsed). */
  meta?: string;
  /** Source URLs from a finished build/report frame. */
  sources?: string[];
}

interface WebchatProgress {
  status: string;
  detail?: string;
  task?: string;
}

type WebchatStatus = "loading" | "idle" | "streaming" | "error";

function receiptMeta(event: ChatStreamEvent): string | undefined {
  const parts: string[] = [];
  if (event.receipt?.model) parts.push(String(event.receipt.model));
  if (typeof event.elapsed_ms === "number") parts.push(`${event.elapsed_ms} ms`);
  return parts.length ? parts.join(" · ") : undefined;
}

export function WebChatPage() {
  const [searchParams] = useSearchParams();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WebchatMessage[]>([]);
  const [progress, setProgress] = useState<WebchatProgress[]>([]);
  const [status, setStatus] = useState<WebchatStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const lastPromptRef = useRef<string>("");
  const sessionRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Session create/reuse + bounded history load (server is the authority).
  useEffect(() => {
    let cancelled = false;
    async function init() {
      try {
        const requested = searchParams.get("session");
        let id: string | null = requested;
        if (id) {
          const history = await getWebchatHistory(id);
          if (cancelled || !mountedRef.current) return;
          setMessages(
            (history.messages ?? []).map((m) => ({
              role: m.role === "user" ? "user" : "assistant",
              text: m.content,
            })),
          );
        } else {
          const listed = await listWebchatSessions(50, 0);
          if (cancelled || !mountedRef.current) return;
          const first = listed.sessions?.[0]?.id ?? null;
          if (first) {
            id = first;
            const history = await getWebchatHistory(first);
            if (cancelled || !mountedRef.current) return;
            setMessages(
              (history.messages ?? []).map((m) => ({
                role: m.role === "user" ? "user" : "assistant",
                text: m.content,
              })),
            );
          } else {
            const created = await createWebchatSession("webchat");
            if (cancelled || !mountedRef.current) return;
            id = created.session.id;
          }
        }
        if (cancelled || !mountedRef.current) return;
        sessionRef.current = id;
        setSessionId(id);
        setStatus("idle");
      } catch (e) {
        if (cancelled || !mountedRef.current) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    }
    void init();
    return () => {
      cancelled = true;
    };
    // Mount-only: session continuity lives in sessionRef, not the query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runTurn = useCallback(async (text: string, session: string) => {
    lastPromptRef.current = text;
    setMessages((prev) => [...prev, { role: "user", text }]);
    setProgress([]);
    setError(null);
    setStatus("streaming");
    let acc = "";
    const accIndexRef = { i: -1 };
    setMessages((prev) => {
      accIndexRef.i = prev.length;
      return [...prev, { role: "assistant", text: "" }];
    });
    const patchAssistant = (fn: (m: WebchatMessage) => WebchatMessage) =>
      setMessages((prev) =>
        prev.map((m, i) => (i === accIndexRef.i ? fn(m) : m)),
      );
    try {
      await streamWebchatTurn({ text, session }, {
        onStart: (event) => {
          const sid =
            event.session_id ?? event.session ?? (typeof event.session === "string" ? event.session : undefined);
          if (typeof sid === "string" && sid) {
            sessionRef.current = sid;
            setSessionId(sid);
          }
        },
        onDelta: (delta) => {
          acc += delta;
          const snapshot = acc;
          patchAssistant((m) => ({ ...m, text: snapshot }));
        },
        onProgress: (event) => {
          setProgress((prev) => [
            ...prev,
            {
              status: event.status ?? event.event,
              detail: event.detail,
              task: event.task,
            },
          ]);
          if (event.text) {
            const snapshot = (acc + event.text);
            acc = snapshot;
            patchAssistant((m) => ({ ...m, text: snapshot }));
          }
        },
        onDone: (event) => {
          const reply =
            event.reply ?? (typeof acc === "string" && acc ? acc : "");
          const meta = receiptMeta(event);
          const sources = event.sources ?? event.receipt?.sources;
          patchAssistant((m) => ({
            ...m,
            text: reply || m.text,
            ...(meta ? { meta } : {}),
            ...(sources?.length ? { sources } : {}),
          }));
          setProgress([]);
          setStatus("idle");
        },
        onError: (event) => {
          // Transcript is kept; reconnect resumes via Retry on the same id.
          setError(event.detail || event.code || "STREAM_ERROR");
          setStatus("error");
        },
        onCancelled: () => {
          setStatus("idle");
        },
      });
    } catch (e) {
      // Transport-level failure (HTTP status, aborted fetch): same resume path.
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  }, []);

  const handleSend = useCallback(() => {
    const text = input.trim();
    const session = sessionRef.current;
    if (!text || status === "streaming" || !session) return;
    setInput("");
    void runTurn(text, session);
  }, [input, status, runTurn]);

  const handleRetry = useCallback(() => {
    const session = sessionRef.current;
    const text = lastPromptRef.current;
    if (!text || status === "streaming" || !session) return;
    setError(null);
    void runTurn(text, session);
  }, [status, runTurn]);

  const handleNewChat = useCallback(async () => {
    if (status === "streaming") return;
    setStatus("loading");
    setError(null);
    try {
      const created = await createWebchatSession("webchat");
      if (!mountedRef.current) return;
      sessionRef.current = created.session.id;
      setSessionId(created.session.id);
      setMessages([]);
      setProgress([]);
      setStatus("idle");
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  }, [status]);

  const streaming = status === "streaming";

  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="webchat-page">
      <header className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">WebChat</h1>
          <p className="text-xs text-muted-foreground" data-testid="webchat-session">
            {sessionId ? `Session ${sessionId}` : "Opening session…"}
          </p>
        </div>
        <button
          type="button"
          aria-label="New chat"
          data-testid="webchat-new"
          disabled={streaming || status === "loading"}
          onClick={handleNewChat}
          className="rounded border px-3 py-1.5 text-sm"
        >
          New chat
        </button>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto" data-testid="webchat-transcript">
        {messages.length === 0 && status === "idle" && (
          <p className="text-sm text-muted-foreground">
            Start a new conversation below. History is stored on the server.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            data-testid={`webchat-message-${i}`}
            data-role={m.role}
            className={m.role === "user" ? "text-right" : "text-left"}
          >
            <div
              className={
                m.role === "user"
                  ? "inline-block rounded bg-primary/10 px-3 py-2 text-sm"
                  : "rounded border px-3 py-2"
              }
            >
              {m.role === "user" ? (
                <span className="text-sm">{m.text}</span>
              ) : (
                <Markdown
                  content={m.text || "…"}
                  streaming={streaming && i === messages.length - 1}
                />
              )}
              {m.meta && (
                <div className="mt-1 text-xs text-muted-foreground">{m.meta}</div>
              )}
              {m.sources && m.sources.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-xs">
                  {m.sources.map((s, j) => (
                    <li key={j}>
                      <a
                        href={s}
                        target="_blank"
                        rel="noreferrer"
                        className="underline"
                      >
                        {s}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
        {progress.length > 0 && (
          <div data-testid="webchat-progress" className="text-xs text-muted-foreground">
            {progress.map((p, i) => (
              <div key={i}>
                {p.task ? `${p.task}: ` : ""}{p.status}
                {p.detail ? ` — ${p.detail}` : ""}
              </div>
            ))}
          </div>
        )}
      </div>

      {status === "error" && error && (
        <div
          data-testid="webchat-error"
          role="alert"
          className="rounded border border-destructive/40 px-3 py-2 text-sm"
        >
          <span>{error}</span>{" "}
          <button
            type="button"
            aria-label="Retry"
            data-testid="webchat-retry"
            onClick={handleRetry}
            className="underline"
          >
            Retry
          </button>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
        className="flex gap-2"
      >
        <textarea
          data-testid="webchat-input"
          aria-label="Message"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={2}
          placeholder="Type a message…"
          className="flex-1 rounded border px-3 py-2 text-sm"
        />
        <button
          type="submit"
          aria-label="Send"
          data-testid="webchat-send"
          disabled={streaming || status === "loading" || !input.trim()}
          className="rounded border px-4 py-2 text-sm"
        >
          Send
        </button>
      </form>

      <p data-testid="webchat-status" className="text-xs text-muted-foreground">
        {status === "loading" && "Opening session…"}
        {status === "idle" && "Ready"}
        {status === "streaming" && "Streaming…"}
        {status === "error" && "Connection interrupted — Retry resumes this session."}
      </p>
    </div>
  );
}

export default WebChatPage;
