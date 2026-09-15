/**
 * Qubit Chat — the dashboard `/chat` surface.
 *
 * Qubit owns a single event path: SSE plus the native agent turn
 * at `POST /api/qubit/chat/stream` (the same `voicebridge.chat_transport`
 * module the non-streaming turn, CLI, and voice use). Raw shell/PTY
 * WebSockets are refused by Qubit policy (`PTY_NO_SHELL`), so this page never
 * opens `/api/pty`. The transcript is server-backed: it loads from the
 * persisted chat-session store (`GET /api/qubit/chat/sessions/<id>/messages`)
 * and streams deltas over SSE, so a reload restores the real conversation.
 * Includes loading/empty/error/retry states, optional browser voice input,
 * and the sessions switcher.
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { Copy, Mic, RotateCcw, Send, Square } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useSearchParams } from "react-router";

import { ChatSessionList } from "@/components/ChatSessionList";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import {
  api,
  streamChatTurn,
  type ChatStoreMessage,
  type ChatStreamEvent,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  /** Small render-time metadata line (model / elapsed). */
  meta?: string;
}

/** Map a persisted chat-store message to a render message. */
function toChatMessage(message: ChatStoreMessage): ChatMessage {
  const meta = (message.meta ?? {}) as {
    model?: string;
    elapsed_ms?: number;
  };
  const parts = [
    meta.model ? `model=${meta.model}` : "",
    meta.elapsed_ms != null ? `elapsed=${meta.elapsed_ms}ms` : "",
  ].filter(Boolean);
  return {
    role: message.role === "user" ? "user" : "assistant",
    text: message.content ?? "",
    meta: parts.join(" ") || undefined,
  };
}

/** Build the small metadata line for a streamed assistant reply. */
function streamMeta(event: ChatStreamEvent): string | undefined {
  const parts = [
    event.receipt?.model ? `model=${event.receipt.model}` : "",
    event.elapsed_ms != null ? `elapsed=${event.elapsed_ms}ms` : "",
  ].filter(Boolean);
  return parts.join(" ") || undefined;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
}

const STORAGE_PREFIX = "qubit.chat.transcript.";
const SESSION_KEY = "qubit.chat.session";
const DEFAULT_SESSION = "dashboard-chat";
const VOICE_WATCHDOG_MS = 60_000;

function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode / quota — best-effort */
  }
}

/**
 * Read-through transcript cache. The server chat store
 * (`/api/qubit/chat/sessions/<id>/messages`) is authoritative and always
 * revalidated on session change; this cache only paints the last-known
 * transcript instantly (and covers an offline reload). It is never treated
 * as the source of truth for a turn.
 */
function loadTranscript(session: string): ChatMessage[] {
  const raw = storageGet(STORAGE_PREFIX + session);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (m): m is ChatMessage =>
        !!m &&
        typeof m === "object" &&
        ((m as ChatMessage).role === "user" ||
          (m as ChatMessage).role === "assistant") &&
        typeof (m as ChatMessage).text === "string",
    );
  } catch {
    return [];
  }
}

function saveTranscript(session: string, messages: ChatMessage[]): void {
  storageSet(STORAGE_PREFIX + session, JSON.stringify(messages.slice(-200)));
}

/** Extract a typed code/detail from a fetch Error("<status>: <body>"). */
function parseTurnError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? "turn failed");
  const sep = raw.indexOf(":");
  const body = sep >= 0 ? raw.slice(sep + 1).trim() : raw;
  try {
    const parsed = JSON.parse(body) as {
      code?: string;
      error?: string;
      detail?: string;
    };
    const code = parsed.code ?? parsed.error ?? "";
    const detail = parsed.detail ?? "";
    if (code || detail) {
      return [code && `[${code}]`, detail].filter(Boolean).join(" ");
    }
  } catch {
    /* non-JSON body — return as-is */
  }
  return body || raw;
}

function asSpeechRecognition(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export default function ChatPage({ isActive = true }: { isActive?: boolean }) {
  const { setTitle } = usePageHeader();
  const { t } = useI18n();
  const { profile: scopedProfile } = useProfileScope();
  const [searchParams, setSearchParams] = useSearchParams();

  const resumeParam = searchParams.get("resume");
  const session = useMemo(
    () => (resumeParam || storageGet(SESSION_KEY) || DEFAULT_SESSION).slice(0, 80),
    [resumeParam],
  );

  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    loadTranscript(session),
  );
  const [transcriptState, setTranscriptState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [streamingText, setStreamingText] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryText, setRetryText] = useState<string | null>(null);
  const [voiceState, setVoiceState] = useState<
    "idle" | "listening" | "unsupported"
  >("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const heardRef = useRef("");
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const loadReqRef = useRef(0);

  // Keep the session pointer stable in storage so a reload lands back on the
  // same conversation even without a ?resume= param.
  useEffect(() => {
    if (resumeParam) storageSet(SESSION_KEY, resumeParam);
  }, [resumeParam]);

  // Load the selected session's persisted transcript from the Qubit chat
  // store (`GET /api/qubit/chat/sessions/<id>/messages`). Resolve the session
  // against `listChatSessions()` first so a not-yet-created session (e.g. the
  // unsaved default `dashboard-chat`) never issues a transcript request — the
  // empty state renders instead of a typed 404 in the console. Server state is
  // authoritative; nothing is invented.
  useEffect(() => {
    const myReq = ++loadReqRef.current;
    // Instant paint from the cache; the server load below replaces it.
    setMessages(loadTranscript(session));
    setStreamingText("");
    setError(null);
    setRetryText(null);
    setTranscriptState("loading");
    api
      .listChatSessions(100, 0)
      .then((list) => {
        if (loadReqRef.current !== myReq) return null;
        const exists = (list.sessions ?? []).some((s) => s.id === session);
        if (!exists) {
          // No server record yet: keep the cached/empty transcript.
          setTranscriptState("ready");
          return null;
        }
        return api.getChatSessionMessages(session).then((res) => {
          if (loadReqRef.current !== myReq) return;
          const loaded = (res.messages ?? []).map(toChatMessage);
          setMessages(loaded);
          saveTranscript(session, loaded);
          setTranscriptState("ready");
        });
      })
      .catch((err: unknown) => {
        if (loadReqRef.current !== myReq) return;
        setTranscriptState("error");
        setError(err instanceof Error ? err.message : String(err ?? "load failed"));
      });
  }, [session]);

  // Keep the read-through cache in step with the rendered transcript.
  useEffect(() => saveTranscript(session, messages), [session, messages]);

  useEffect(() => {
    setTitle(isActive ? t.app.nav.chat : null);
    return () => setTitle(null);
  }, [isActive, setTitle, t.app.nav.chat]);

  useEffect(() => {
    api
      .getModelInfo(scopedProfile)
      .then((info) => setModel(info.model || ""))
      .catch(() => setModel(""));
  }, [scopedProfile]);

  // Follow the newest message.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, error]);

  useEffect(() => {
    return () => {
      if (watchdogRef.current) clearTimeout(watchdogRef.current);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      try {
        recRef.current?.abort();
      } catch {
        /* already stopped */
      }
      abortRef.current?.abort();
    };
  }, []);

  const send = useCallback(
    (raw: string) => {
      const text = (raw || "").trim();
      if (!text || busy) return;
      setInput("");
      setError(null);
      setRetryText(null);
      setBusy(true);
      setStreamingText("");
      setMessages((prev) => prev.concat([{ role: "user", text }]));

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // SSE turn on the shared chat transport: deltas render live, the final
      // reply is persisted server-side, and a typed error is surfaced as-is.
      streamChatTurn(
        { text, session },
        {
          onDelta: (delta) => {
            if (controller.signal.aborted) return;
            setStreamingText((prev) => prev + delta);
          },
          onDone: (event) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.concat([
                {
                  role: "assistant",
                  text: event.reply ?? "",
                  meta: streamMeta(event),
                },
              ]),
            );
            setStreamingText("");
          },
          onError: (event) => {
            if (controller.signal.aborted) return;
            setError(
              `[${event.code ?? "TURN_FAILED"}] ${event.detail ?? "turn failed"}`,
            );
            setRetryText(text);
            setStreamingText("");
          },
          onCancelled: () => {
            if (controller.signal.aborted) return;
            setStreamingText("");
          },
        },
        controller.signal,
      )
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setError(parseTurnError(err));
          setRetryText(text);
          setStreamingText("");
        })
        .finally(() => {
          if (!controller.signal.aborted) setBusy(false);
        });
    },
    [busy, session],
  );

  const onSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      send(input);
    },
    [input, send],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send(input);
      }
    },
    [input, send],
  );

  const startFresh = useCallback(() => {
    storageSet(SESSION_KEY, DEFAULT_SESSION);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("resume");
        return next;
      },
      { replace: false },
    );
    setMessages([]);
    setError(null);
    setRetryText(null);
  }, [setSearchParams]);

  const copyLast = useCallback(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant");
    if (!last) return;
    navigator.clipboard
      ?.writeText(last.text)
      .then(() => {
        setCopyState("copied");
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopyState("idle"), 1500);
      })
      .catch(() => {
        /* clipboard unavailable — ignore */
      });
  }, [messages]);

  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  const stopVoice = useCallback(() => {
    try {
      recRef.current?.stop();
    } catch {
      /* already stopped */
    }
  }, []);

  const armWatchdog = useCallback(
    (rec: SpeechRecognitionLike) => {
      clearWatchdog();
      watchdogRef.current = setTimeout(() => {
        watchdogRef.current = null;
        try {
          rec.stop();
        } catch {
          /* noop */
        }
        recRef.current = null;
        setVoiceState("idle");
        setVoiceError(
          "[VOICE_TIMEOUT] No speech heard within 60s — the microphone may be " +
            "unavailable. Type below, or use CLI `qubit` voice instead.",
        );
      }, VOICE_WATCHDOG_MS);
    },
    [clearWatchdog],
  );

  const startVoice = useCallback(() => {
    if (busy || voiceState === "listening") return;
    const SR = asSpeechRecognition();
    if (!SR) {
      setVoiceState("unsupported");
      setVoiceError(
        "[VOICE_UNSUPPORTED] This browser offers no speech recognition. " +
          "Type below, or use CLI `qubit` voice instead.",
      );
      return;
    }
    setVoiceError(null);
    let rec: SpeechRecognitionLike;
    try {
      rec = new SR();
    } catch (err) {
      setVoiceState("unsupported");
      setVoiceError(
        "[VOICE_UNSUPPORTED] Could not start speech recognition: " +
          (err instanceof Error ? err.message : String(err)),
      );
      return;
    }
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    recRef.current = rec;
    heardRef.current = "";
    setVoiceState("listening");
    armWatchdog(rec);
    rec.onresult = (event) => {
      armWatchdog(rec);
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) final += result[0].transcript;
        else interim += result[0].transcript;
      }
      const heard = (final || interim).trim();
      if (heard) {
        heardRef.current = heard;
        setInput(heard);
      }
      if (final.trim()) {
        try {
          rec.stop();
        } catch {
          /* noop */
        }
      }
    };
    rec.onerror = (event) => {
      const code = event?.error;
      clearWatchdog();
      recRef.current = null;
      setVoiceState("idle");
      if (code === "not-allowed" || code === "service-not-allowed") {
        setVoiceError(
          "[VOICE_DENIED] Microphone blocked. Allow mic access, or type below.",
        );
      } else if (code === "no-speech" || code === "aborted") {
        setVoiceError("[VOICE_NO_SPEECH] No speech heard — try again, or type.");
      } else {
        setVoiceError(`[VOICE_ERROR] Speech recognition failed (${code}).`);
      }
    };
    rec.onend = () => {
      clearWatchdog();
      recRef.current = null;
      const said = (heardRef.current || "").trim();
      heardRef.current = "";
      setVoiceState("idle");
      if (said) {
        setInput("");
        send(said);
      }
    };
    try {
      rec.start();
    } catch (err) {
      recRef.current = null;
      setVoiceState("idle");
      setVoiceError(
        "[VOICE_ERROR] Could not start the microphone: " +
          (err instanceof Error ? err.message : String(err)),
      );
    }
  }, [armWatchdog, busy, clearWatchdog, send, voiceState]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "listening") stopVoice();
    else startVoice();
  }, [startVoice, stopVoice, voiceState]);

  const hasMessages = messages.length > 0 || streamingText.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <PluginSlot name="chat:top" />

      <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row lg:gap-3">
        <section
          aria-label="Qubit Chat transcript"
          className={cn(
            "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg",
            "border border-current/15 bg-background-base/60 p-2 sm:p-3",
          )}
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-current/10 pb-2">
            <Typography
              mondwest
              className="text-sm tracking-[0.08em] text-text-secondary"
            >
              Qubit Chat
            </Typography>
            <span className="text-xs text-text-tertiary" data-testid="chat-model">
              {model ? `model ${model}` : "model —"}
            </span>
          </div>

          <div
            ref={scrollRef}
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-busy={busy}
            className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-3"
          >
            {transcriptState === "loading" && !hasMessages ? (
              <div
                data-state="loading"
                className="flex h-full min-h-[8rem] items-center justify-center gap-2 text-sm text-text-tertiary"
              >
                <Spinner className="shrink-0" />
                <span>Loading transcript…</span>
              </div>
            ) : !hasMessages && !busy ? (
              <div
                data-state="empty"
                className="flex h-full min-h-[8rem] flex-col items-center justify-center gap-2 text-center text-sm text-text-tertiary"
              >
                <p>No messages yet — ask something.</p>
                <p className="text-xs">
                  Native agent turns on the shared Qubit backend (same module as
                  CLI and voice).
                </p>
              </div>
            ) : (
              <ul className="flex list-none flex-col gap-3">
                {messages.map((message, index) => (
                  <li
                    key={`${message.role}-${index}`}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-sm",
                      message.role === "user"
                        ? "ml-6 border-accent/40 bg-accent/10"
                        : "mr-6 border-current/15 bg-background-base/70",
                    )}
                  >
                    <div className="mb-1 text-xs font-semibold tracking-wide text-text-secondary">
                      {message.role === "user" ? "You" : "Qubit"}
                    </div>
                    <div className="whitespace-pre-wrap break-words">
                      {message.text}
                    </div>
                    {message.meta ? (
                      <div className="mt-1 text-xs text-text-tertiary">
                        {message.meta}
                      </div>
                    ) : null}
                  </li>
                ))}
                {busy && streamingText ? (
                  <li
                    data-state="streaming"
                    className="mr-6 rounded-lg border border-current/15 bg-background-base/70 px-3 py-2 text-sm"
                    aria-live="polite"
                  >
                    <div className="mb-1 text-xs font-semibold tracking-wide text-text-secondary">
                      Qubit
                    </div>
                    <div className="whitespace-pre-wrap break-words">
                      {streamingText}
                    </div>
                  </li>
                ) : busy ? (
                  <li
                    data-state="loading"
                    className="mr-6 flex items-center gap-2 text-sm text-text-tertiary"
                  >
                    <Spinner className="shrink-0" />
                    <span>Qubit is thinking…</span>
                  </li>
                ) : null}
              </ul>
            )}
          </div>

          {error ? (
            <div
              role="alert"
              data-state="degraded"
              className="mt-2 flex flex-wrap items-center gap-2 border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              <span className="min-w-0 flex-1 break-words">{error}</span>
              {retryText ? (
                <Button
                  size="sm"
                  outlined
                  onClick={() => send(retryText)}
                  prefix={<RotateCcw className="h-3.5 w-3.5" />}
                  aria-label="Retry the last message"
                >
                  Retry
                </Button>
              ) : null}
            </div>
          ) : null}

          {voiceError ? (
            <div
              role="alert"
              data-state="degraded"
              className="mt-2 border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              {voiceError}
            </div>
          ) : null}

          <form
            onSubmit={onSubmit}
            className="mt-2 flex shrink-0 items-end gap-2 border-t border-current/10 pt-2"
          >
            <label className="sr-only" htmlFor="qubit-chat-input">
              Message Qubit
            </label>
            <textarea
              id="qubit-chat-input"
              value={input}
              disabled={busy}
              rows={2}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message Qubit… (Enter to send, Shift+Enter for newline)"
              aria-label="Message Qubit"
              className={cn(
                "min-h-[2.5rem] min-w-0 flex-1 resize-none rounded-md border border-current/20",
                "bg-background-base/70 px-3 py-2 text-sm text-foreground",
                "focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60",
              )}
            />
            <Button
              type="button"
              outlined
              onClick={toggleVoice}
              disabled={busy}
              aria-label={
                voiceState === "listening" ? "Stop voice input" : "Voice input"
              }
              title="Speak instead of typing (same backend turn)"
              prefix={
                voiceState === "listening" ? (
                  <Square className="h-4 w-4" />
                ) : (
                  <Mic className="h-4 w-4" />
                )
              }
            >
              {voiceState === "listening" ? "Stop" : "Voice"}
            </Button>
            <Button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Send"
              prefix={<Send className="h-4 w-4" />}
            >
              Send
            </Button>
          </form>

          <div className="mt-2 flex items-center justify-between gap-2">
            <Button
              ghost
              size="sm"
              onClick={startFresh}
              prefix={<RotateCcw className="h-3.5 w-3.5" />}
              aria-label="Start a new chat session"
            >
              New chat
            </Button>
            <Button
              ghost
              size="sm"
              onClick={copyLast}
              disabled={!messages.some((m) => m.role === "assistant")}
              prefix={<Copy className="h-3.5 w-3.5" />}
              aria-label="Copy last assistant response"
            >
              {copyState === "copied" ? "copied" : "copy last response"}
            </Button>
          </div>
        </section>

        <aside
          role="complementary"
          aria-label="Chat sessions"
          className="flex min-h-0 shrink-0 flex-col gap-2 overflow-hidden lg:h-full lg:w-64"
        >
          <div className="min-h-0 flex-1 overflow-hidden">
            <ChatSessionList
              activeSessionId={resumeParam}
              profile={scopedProfile}
              onNewChat={startFresh}
            />
          </div>
        </aside>
      </div>

      <PluginSlot name="chat:bottom" />
    </div>
  );
}
