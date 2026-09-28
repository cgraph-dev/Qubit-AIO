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
import {
  Bug,
  Code,
  Copy,
  FileText,
  Globe,
  ImagePlus,
  Mic,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Search,
  Send,
  Square,
  Terminal,
  Wrench,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ClipboardEvent as ReactClipboardEvent,
} from "react";
import { useSearchParams } from "react-router";

import { ChatSessionList } from "@/components/ChatSessionList";
import { ChatSessionArtifacts } from "@/components/ChatSessionArtifacts";
import { firstImageFromClipboard } from "@/lib/chatImagePaste";

// Lazily loaded so the informational dashboard event WebSocket (opened by
// ChatSidebar) is code-split out of the normal-chat chunk and never runs on a
// plain chat turn (the raw-PTY/code-1006 reconnect regression).
const ChatSidebar = lazy(() =>
  import("@/components/ChatSidebar").then((m) => ({ default: m.ChatSidebar })));
import { usePageHeader } from "@/contexts/usePageHeader";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import {
  api,
  CHAT_REASONING_LEVELS,
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
  /** A36: tool/system rows are rendered distinctly, not as assistant prose. */
  kind?: "message" | "tool";
  toolName?: string;
}

/** A36: map a tool name to a bounded icon (no per-tool registry invented). */
function ToolIcon({ name }: { name?: string }) {
  const n = (name ?? "").toLowerCase();
  if (n.includes("terminal") || n.includes("shell") || n.includes("bash")) {
    return <Terminal className="h-3.5 w-3.5" />;
  }
  if (n.includes("file") || n.includes("read") || n.includes("write")) {
    return <FileText className="h-3.5 w-3.5" />;
  }
  if (n.includes("search") || n.includes("grep") || n.includes("find")) {
    return <Search className="h-3.5 w-3.5" />;
  }
  if (
    n.includes("browser") ||
    n.includes("http") ||
    n.includes("web") ||
    n.includes("fetch")
  ) {
    return <Globe className="h-3.5 w-3.5" />;
  }
  if (n.includes("code") || n.includes("python") || n.includes("edit")) {
    return <Code className="h-3.5 w-3.5" />;
  }
  return <Wrench className="h-3.5 w-3.5" />;
}

/** Map a persisted chat-store message to a render message. */
function toChatMessage(message: ChatStoreMessage): ChatMessage {
  const meta = (message.meta ?? {}) as {
    model?: string;
    elapsed_ms?: number;
    tool_name?: string;
    tool_calls?: unknown;
  };
  const parts = [
    meta.model ? `model=${meta.model}` : "",
    meta.elapsed_ms != null ? `elapsed=${meta.elapsed_ms}ms` : "",
  ].filter(Boolean);
  const isTool =
    message.role === "tool" ||
    message.role === "system" ||
    Boolean(meta.tool_name) ||
    Array.isArray(meta.tool_calls);
  return {
    role: message.role === "user" ? "user" : "assistant",
    text: message.content ?? "",
    meta: parts.join(" ") || undefined,
    kind: isTool ? "tool" : "message",
    toolName: meta.tool_name ? String(meta.tool_name) : undefined,
  };
}

/** Build the small metadata line for a streamed assistant reply. */
function streamMeta(event: ChatStreamEvent): string | undefined {
  const applied = event.receipt?.reasoning_applied;
  const parts = [
    event.receipt?.model ? `model=${event.receipt.model}` : "",
    event.elapsed_ms != null ? `elapsed=${event.elapsed_ms}ms` : "",
    applied?.level
      ? `reasoning=${applied.level}` +
        (typeof applied.max_tokens === "number"
          ? `(${applied.max_tokens})`
          : "")
      : "",
  ].filter(Boolean);
  return parts.join(" ") || undefined;
}

/** Client-side guard mirroring the server's level set (server re-validates). */
function normalizeReasoning(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const key = value.trim().toLowerCase();
  return CHAT_REASONING_LEVELS.some((l) => l.id === key) ? key : null;
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

/**
 * Stable per-mount channel id for the A31 side panel. Qubit ties this to the
 * embedded TUI's PTY child; Qubit has no PTY child (PTY_NO_SHELL), so the
 * ChatSidebar event feed is informational and the id only needs to be unique
 * to this dashboard tab so sibling tabs never share a feed.
 */
function generateChatChannel(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `qubit-chat-${crypto.randomUUID()}`;
  }
  return `qubit-chat-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

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

type VoiceEngine = "browser" | "local" | "auto";
type ActiveVoiceEngine = "browser" | "local" | null;

const VOICE_ENGINES = ["browser", "local", "auto"] as const;

/** Canonical `voice.input_engine` semantics; unknown/missing fails to `auto`. */
function normalizeVoiceEngine(value: unknown): VoiceEngine {
  const s = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (VOICE_ENGINES as readonly string[]).includes(s)
    ? (s as VoiceEngine)
    : "auto";
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("audio read failed"));
    reader.onload = () =>
      resolve(typeof reader.result === "string" ? reader.result : "");
    reader.readAsDataURL(blob);
  });
}

function localCaptureAvailable(): boolean {
  if (typeof navigator === "undefined" || typeof MediaRecorder === "undefined") {
    return false;
  }
  const md = navigator.mediaDevices as MediaDevices | undefined;
  return typeof md?.getUserMedia === "function";
}

const HTML_TAG_RE = /<\/?[a-z][\s\S]*>/i;

/**
 * A37 — sandboxed HTML preview. Assistant text that contains markup can be
 * previewed inside a maximally-restricted iframe (`sandbox=""`): scripts,
 * forms, popups, top-navigation and same-origin access are all disabled, so a
 * generated page cannot reach the dashboard session. Non-HTML text renders
 * nothing (the transcript's plain-text view is already the safe projection).
 */
function SandboxedHtmlPreview({ html }: { html: string }) {
  if (!HTML_TAG_RE.test(html)) return null;
  return (
    <details className="mt-2 border border-border/50 bg-background-base/40 px-2 py-1.5">
      <summary className="cursor-pointer text-xs text-text-secondary">
        HTML preview (sandboxed)
      </summary>
      <iframe
        title="Sandboxed HTML preview"
        srcDoc={html}
        sandbox=""
        referrerPolicy="no-referrer"
        className="mt-2 h-40 w-full rounded-sm border border-border bg-white"
      />
      <p className="mt-1 text-xs text-text-tertiary">
        Scripts, forms, popups and same-origin access are disabled by the
        sandbox.
      </p>
    </details>
  );
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
  // `voice.input_engine` (browser | local | auto) and the engine actually
  // running the current utterance (browser SpeechRecognition vs local STT).
  const [inputEngine, setInputEngine] = useState<VoiceEngine>("auto");
  const [activeEngine, setActiveEngine] = useState<ActiveVoiceEngine>(null);
  const [voiceFallback, setVoiceFallback] = useState<string | null>(null);
  // Informational (non-error) status, e.g. a backend-cancelled turn. Kept
  // separate from `voiceError` so a cancellation is not styled as a failure.
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  // Whether the active model actually supports a reasoning budget, served by
  // `GET /api/model/info` (`capabilities.supports_reasoning`). The selector is
  // gated on this real capability — offering it for a non-reasoning model would
  // be a dead control.
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  // Reasoning selector: null = model default. Persisted per session on the
  // canonical session store (PATCH) and sent with every turn; the server
  // re-validates and receipts the applied budget.
  const [reasoning, setReasoning] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  // A26: the Qubit chat control family includes a side-panel toggle and a
  // terminal-input control. The side panel is collapsible; the terminal input
  // is a typed refusal because Qubit policy (PTY_NO_SHELL) refuses raw shells.
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  // Bumped after a New-chat mint so `ChatSessionList` remounts and refetches,
  // surfacing the freshly created session immediately.
  const [sessionListNonce, setSessionListNonce] = useState(0);
  const [terminalNoticeOpen, setTerminalNoticeOpen] = useState(false);
  // A31: the side panel is one `role="complementary"` "Model & tools" wrapper
  // (the pinned Qubit structure) mounting the model box and session switcher.
  const [channel] = useState<string>(generateChatChannel);
  // A34: transcript selection projection.
  const [selectedMessages, setSelectedMessages] = useState<Set<number>>(
    () => new Set(),
  );
  // A43: debug overlay over the canonical runtime inventory + logs.
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugState, setDebugState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [debugError, setDebugError] = useState<string | null>(null);
  const [debugRuntime, setDebugRuntime] = useState<string | null>(null);
  const [debugInventory, setDebugInventory] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [debugLogs, setDebugLogs] = useState<string[]>([]);
  // A35: paste/attach an image and describe it via the canonical vision backend.
  const [attachState, setAttachState] = useState<
    "idle" | "describing" | "error"
  >("idle");
  const [attachError, setAttachError] = useState<string | null>(null);
  const attachInputRef = useRef<HTMLInputElement | null>(null);

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const heardRef = useRef("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const rightCtrlHeldRef = useRef(false);
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
          // No server record yet: keep the cached/empty transcript and the
          // model default (no persisted preference to load).
          setReasoning(null);
          setTranscriptState("ready");
          return null;
        }
        return api.getChatSessionMessages(session).then((res) => {
          if (loadReqRef.current !== myReq) return;
          const loaded = (res.messages ?? []).map(toChatMessage);
          setMessages(loaded);
          saveTranscript(session, loaded);
          setTranscriptState("ready");
          // Session-persisted reasoning preference (server is authoritative;
          // a session without one keeps the model default).
          api
            .getChatSession(session)
            .then((info) => {
              if (loadReqRef.current !== myReq) return;
              setReasoning(normalizeReasoning(info.meta?.reasoning));
            })
            .catch(() => {
              /* keep the default; the turn path still validates */
            });
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
      .then((info) => {
        setModel(info.model || "");
        setSupportsReasoning(
          info.capabilities?.supports_reasoning === true,
        );
      })
      .catch(() => {
        setModel("");
        setSupportsReasoning(false);
      });
  }, [scopedProfile]);

  // Resolve the selected speech input engine once from the canonical config
  // (`voice.input_engine`). Read-only; the setting changes speech input only —
  // never the model, session, tools, policy, audit, confirmation, or e-stop.
  useEffect(() => {
    let alive = true;
    api
      .getConfig?.()
      .then((cfg) => {
        if (!alive) return;
        const voice = (cfg as { voice?: { input_engine?: unknown } } | undefined)
          ?.voice;
        setInputEngine(normalizeVoiceEngine(voice?.input_engine));
      })
      .catch(() => {
        /* config unavailable — keep the safe `auto` default */
      });
    return () => {
      alive = false;
    };
  }, []);

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
      const stream = mediaStreamRef.current;
      if (stream) {
        try {
          stream.getTracks().forEach((track) => track.stop());
        } catch {
          /* already stopped */
        }
      }
      mediaStreamRef.current = null;
      mediaRecorderRef.current = null;
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
      setVoiceNotice(null);
      setBusy(true);
      setStreamingText("");
      setMessages((prev) => prev.concat([{ role: "user", text }]));

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // SSE turn on the shared chat transport: deltas render live, the final
      // reply is persisted server-side, and a typed error is surfaced as-is.
      // The reasoning selector rides along (null = session preference, which
      // the server resolves from the persisted session metadata).
      streamChatTurn(
        reasoning ? { text, session, reasoning } : { text, session },
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
            setVoiceNotice("turn cancelled — the shared backend aborted it");
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
    [busy, session, reasoning],
  );

  // Persist the selector per session on the canonical store (validated
  // server-side; a not-yet-created session 404s harmlessly — the next turn
  // creates it and the following change persists).
  const handleReasoningChange = useCallback(
    (id: string) => {
      const level = id === "" ? null : id;
      setReasoning(level);
      api
        .patchChatSession(session, { reasoning: level })
        .catch(() => {
          /* session may not exist yet; per-turn send still applies */
        });
    },
    [session],
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

  // "New chat" mints a genuinely NEW session on the canonical store (the same
  // store the sessions list and transcript read) and points the surface at it,
  // so a fresh chat never re-opens the fixed canonical default
  // (`dashboard-chat`). The implicit first load (no `?resume=`) still converges
  // on the canonical default for continuity. A failed mint surfaces a typed
  // error and leaves the current session untouched.
  const startFresh = useCallback(() => {
    setMessages([]);
    setError(null);
    setRetryText(null);
    api
      .createChatSession()
      .then((res) => {
        const newId = res?.session?.id;
        if (!newId) return;
        // Persist first so a reload lands on the fresh session even before the
        // URL effect runs; the `?resume=` param then drives the session memo.
        storageSet(SESSION_KEY, newId);
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set("resume", newId);
            return next;
          },
          { replace: false },
        );
        setSessionListNonce((n) => n + 1);
      })
      .catch((err: unknown) => {
        setError(
          `[CHAT_SESSION_CREATE] ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      });
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

  // A43: load the canonical runtime inventory + agent logs for the overlay.
  const loadDebug = useCallback(() => {
    setDebugState("loading");
    setDebugError(null);
    Promise.all([
      api.getStatus().catch((err: unknown) => {
        throw err instanceof Error ? err : new Error(String(err));
      }),
      api.getRuntime().catch(() => null),
      api
        .getLogs({ file: "agent", lines: 50 })
        .catch(() => ({ file: "agent", lines: [] as string[] })),
    ])
      .then(([status, inventory, logs]) => {
        setDebugRuntime(
          status.gateway_state ??
            (status.gateway_running ? "running" : "stopped"),
        );
        setDebugInventory(inventory);
        setDebugLogs(logs.lines ?? []);
        setDebugState("ready");
      })
      .catch((err: unknown) => {
        setDebugError(err instanceof Error ? err.message : String(err));
        setDebugState("error");
      });
  }, []);

  useEffect(() => {
    if (debugOpen && debugState === "idle") loadDebug();
  }, [debugOpen, debugState, loadDebug]);

  // A35: read a browser image to a data URL and describe it through the
  // canonical vision backend, appending the description to the composer.
  const describeImage = useCallback((file: File | null) => {
    if (!file) return;
    setAttachState("describing");
    setAttachError(null);
    const reader = new FileReader();
    reader.onerror = () => {
      setAttachError("image read failed");
      setAttachState("error");
    };
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      api
        .describeChatImage(dataUrl)
        .then((res) => {
          const desc = (res.description ?? "").trim();
          if (desc) setInput((prev) => (prev ? `${prev}\n${desc}` : desc));
          setAttachState("idle");
        })
        .catch((err: unknown) => {
          setAttachError(err instanceof Error ? err.message : String(err));
          setAttachState("error");
        });
    };
    reader.readAsDataURL(file);
  }, []);

  const onPasteImage = useCallback(
    (event: ReactClipboardEvent<HTMLTextAreaElement>) => {
      const file = firstImageFromClipboard(event.clipboardData);
      if (file) {
        event.preventDefault();
        describeImage(file);
      }
    },
    [describeImage],
  );

  const toggleSelected = useCallback((index: number) => {
    setSelectedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const copySelected = useCallback(() => {
    const text = messages
      .filter((_, index) => selectedMessages.has(index))
      .map((m) => `${m.role === "user" ? "You" : "Qubit"}: ${m.text}`)
      .join("\n\n");
    if (!text) return;
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopyState("copied");
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopyState("idle"), 1500);
      })
      .catch(() => {
        /* clipboard unavailable — ignore */
      });
  }, [messages, selectedMessages]);

  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  const cleanupLocalCapture = useCallback(() => {
    const stream = mediaStreamRef.current;
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    if (stream) {
      try {
        stream.getTracks().forEach((track) => track.stop());
      } catch {
        /* already stopped */
      }
    }
  }, []);

  const stopLocalCapture = useCallback(() => {
    const rec = mediaRecorderRef.current;
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
  }, []);

  /**
   * Local input engine: capture the mic in the browser and transcribe through
   * the canonical Qubit local ASR adapter (`POST /api/audio/transcribe`). The
   * resulting transcript enters the SAME `send()` turn path as typed input.
   */
  const startLocalCapture = useCallback(() => {
    if (!localCaptureAvailable()) {
      setVoiceState("unsupported");
      setActiveEngine(null);
      setVoiceError(
        "[VOICE_UNSUPPORTED] This browser has no microphone capture for local " +
          "STT. Type below, or use CLI `qubit` voice instead.",
      );
      return;
    }
    setVoiceError(null);
    setVoiceNotice(null);
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        mediaStreamRef.current = stream;
        const chunks: Blob[] = [];
        let recorder: MediaRecorder;
        try {
          recorder = new MediaRecorder(stream);
        } catch (err) {
          cleanupLocalCapture();
          setVoiceState("idle");
          setActiveEngine(null);
          setVoiceError(
            "[VOICE_UNSUPPORTED] Could not start local capture: " +
              (err instanceof Error ? err.message : String(err)),
          );
          return;
        }
        mediaRecorderRef.current = recorder;
        setVoiceState("listening");
        setActiveEngine("local");
        recorder.ondataavailable = (event: BlobEvent) => {
          if (event.data && event.data.size) chunks.push(event.data);
        };
        recorder.onerror = () => {
          cleanupLocalCapture();
          setVoiceState("idle");
          setActiveEngine(null);
          setVoiceError("[VOICE_ERROR] Local capture failed.");
        };
        recorder.onstop = () => {
          const mime = recorder.mimeType || "audio/webm";
          cleanupLocalCapture();
          setVoiceState("idle");
          setActiveEngine(null);
          const blob = new Blob(chunks, { type: mime });
          if (!blob.size) {
            setVoiceError(
              "[VOICE_NO_SPEECH] No audio captured — try again, or type.",
            );
            return;
          }
          blobToDataUrl(blob)
            .then((dataUrl) => {
              if (!dataUrl) throw new Error("audio read failed");
              return api.transcribeAudio(dataUrl, mime.split(";", 1)[0]);
            })
            .then((res) => {
              const text = (res.transcript ?? "").trim();
              if (!text) {
                setVoiceError(
                  "[VOICE_NO_SPEECH] No speech heard — try again, or type.",
                );
                return;
              }
              setInput("");
              send(text);
            })
            .catch((err: unknown) => {
              setVoiceError(parseTurnError(err));
            });
        };
        recorder.start();
      })
      .catch(() => {
        cleanupLocalCapture();
        setVoiceState("idle");
        setActiveEngine(null);
        setVoiceError(
          "[VOICE_DENIED] Microphone blocked. Allow mic access, or type below.",
        );
      });
  }, [cleanupLocalCapture, send]);

  const stopVoice = useCallback(() => {
    if (mediaRecorderRef.current) {
      stopLocalCapture();
      return;
    }
    try {
      recRef.current?.stop();
    } catch {
      /* already stopped */
    }
  }, [stopLocalCapture]);

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
    const wantsBrowser =
      inputEngine === "browser" || (inputEngine === "auto" && SR !== null);
    if (!wantsBrowser) {
      // Explicit `local`, or `auto` after browser recognition was unavailable:
      // use local Qubit STT, with the fallback reason visible in the UI.
      setVoiceFallback(
        inputEngine === "auto"
          ? "browser speech recognition unavailable — using local Qubit STT"
          : null,
      );
      startLocalCapture();
      return;
    }
    if (!SR) {
      setVoiceState("unsupported");
      setActiveEngine(null);
      setVoiceError(
        "[VOICE_UNSUPPORTED] This browser offers no speech recognition. " +
          "Type below, or use CLI `qubit` voice instead.",
      );
      return;
    }
    setVoiceError(null);
    setVoiceFallback(null);
    setVoiceNotice(null);
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
    setActiveEngine("browser");
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
      setActiveEngine(null);
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
      setActiveEngine(null);
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
      setActiveEngine(null);
      setVoiceError(
        "[VOICE_ERROR] Could not start the microphone: " +
          (err instanceof Error ? err.message : String(err)),
      );
    }
  }, [armWatchdog, busy, clearWatchdog, inputEngine, send, startLocalCapture,
      voiceState]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "listening") stopVoice();
    else startVoice();
  }, [startVoice, stopVoice, voiceState]);

  // Right-Ctrl is a first-class push-to-talk control for this same `/qubit-chat`
  // surface. Press starts the selected input engine; release stops it and the
  // transcript enters the same `send()` canonical turn path as typed input.
  // It is a control trigger, never a private runtime/session/broker/action path.
  // The listener is gated on `isActive`: the ChatPage host is mounted
  // persistently by the SPA shell, so without the gate Right-Ctrl would hijack
  // the key on every other dashboard route.
  useEffect(() => {
    if (!isActive) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "ControlRight" || event.repeat) return;
      if (rightCtrlHeldRef.current) return;
      rightCtrlHeldRef.current = true;
      startVoice();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "ControlRight") return;
      if (!rightCtrlHeldRef.current) return;
      rightCtrlHeldRef.current = false;
      stopVoice();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [isActive, startVoice, stopVoice]);

  const browserSrAvailable = asSpeechRecognition() !== null;
  const localAvailable = localCaptureAvailable();
  const voiceAvailable = browserSrAvailable || localAvailable;
  const rightCtrlState =
    (inputEngine === "browser" && !browserSrAvailable) ||
    (inputEngine === "local" && !localAvailable) ||
    !voiceAvailable
      ? "unavailable"
      : "ready";
  // The engine a press would actually use (mirrors `startVoice`): the live
  // engine while listening, otherwise the selected engine resolved against
  // browser availability. Surfaced so the operator can see browser vs local
  // STT and the privacy/network consequence, not just the config value.
  const effectiveEngine: ActiveVoiceEngine =
    activeEngine ??
    (inputEngine === "browser" && browserSrAvailable
      ? "browser"
      : inputEngine === "local" && localAvailable
        ? "local"
        : inputEngine === "auto"
          ? browserSrAvailable
            ? "browser"
            : localAvailable
              ? "local"
              : null
          : null);
  const voicePrivacy =
    effectiveEngine === "browser"
      ? "network: browser speech recognition may use the browser vendor's service"
      : effectiveEngine === "local"
        ? "privacy: local STT — audio is transcribed on this machine"
        : "unavailable: no speech input engine";
  const rightCtrlReason =
    rightCtrlState === "unavailable"
      ? inputEngine === "browser" && !browserSrAvailable
        ? "browser speech recognition unavailable"
        : inputEngine === "local" && !localAvailable
          ? "microphone capture unavailable"
          : "no speech input engine available"
      : null;

  const hasMessages = messages.length > 0 || streamingText.length > 0;
  const modelToolsLabel = `${t.app.modelToolsSheetTitle} ${t.app.modelToolsSheetSubtitle}`;

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
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-current/10 pb-2">
            <div className="flex min-w-0 items-center gap-2">
              <Typography
                mondwest
                className="text-sm tracking-[0.08em] text-text-secondary"
              >
                Qubit Chat
              </Typography>
              <span
                className="text-xs text-text-tertiary"
                data-testid="chat-model"
              >
                {model ? `model ${model}` : "model —"}
              </span>
            </div>

            <div className="flex items-center gap-1">
              <Button
                ghost
                size="icon"
                onClick={() => setTerminalNoticeOpen((open) => !open)}
                aria-expanded={terminalNoticeOpen}
                aria-label="Terminal input"
                title="Terminal input (Qubit policy)"
              >
                <Terminal className="h-4 w-4" />
              </Button>
              <Button
                ghost
                size="icon"
                onClick={() => setDebugOpen((open) => !open)}
                aria-expanded={debugOpen}
                aria-label={debugOpen ? "Hide debug overlay" : "Show debug overlay"}
                title="Debug overlay (runtime + logs)"
              >
                <Bug className="h-4 w-4" />
              </Button>
              <Button
                ghost
                size="icon"
                onClick={() => setSidePanelOpen((open) => !open)}
                aria-expanded={sidePanelOpen}
                aria-controls="chat-side-panel"
                aria-label={
                  sidePanelOpen
                    ? "Collapse chat side panel"
                    : "Show chat side panel"
                }
                title={
                  sidePanelOpen
                    ? "Collapse chat side panel"
                    : "Show chat side panel"
                }
              >
                {sidePanelOpen ? (
                  <PanelRightClose className="h-4 w-4" />
                ) : (
                  <PanelRightOpen className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>

          <div
            role="status"
            data-testid="voice-status"
            data-state={voiceState}
            className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border border-current/10 px-2 py-1 text-xs text-text-tertiary"
          >
            <span>
              engine: <span className="font-mono">{inputEngine}</span>
            </span>
            <span>
              active: <span className="font-mono">{activeEngine ?? "—"}</span>
            </span>
            <span>
              mic: <span className="font-mono">{voiceState}</span>
            </span>
            <span
              data-testid="right-ctrl-status"
              data-state={rightCtrlState}
              title={
                rightCtrlReason
                  ? `Right-Ctrl unavailable: ${rightCtrlReason}`
                  : "Right-Ctrl push-to-talk is ready"
              }
            >
              Right-Ctrl: <span className="font-mono">{rightCtrlState}</span>
              {rightCtrlReason ? ` (${rightCtrlReason})` : ""}
            </span>
            <span data-testid="voice-privacy">{voicePrivacy}</span>
            {voiceFallback ? (
              <span data-testid="voice-fallback" className="text-warning">
                fallback: {voiceFallback}
              </span>
            ) : null}
            {voiceNotice ? (
              <span data-testid="voice-notice">{voiceNotice}</span>
            ) : null}
          </div>

          {terminalNoticeOpen && (
            <div
              role="note"
              data-state="refused"
              className="mt-2 border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              <span className="font-mono font-semibold">[PTY_NO_SHELL]</span>{" "}
              Qubit policy refuses a raw terminal/PTY in the dashboard. Use the
              message composer below (the canonical shared chat turn) or the CLI
              `qubit` for shell work. Nothing was executed.
            </div>
          )}

          {debugOpen && (
            <div
              data-state={debugState}
              role="region"
              aria-label="Debug overlay"
              className="mt-2 border border-border bg-background-base/80 px-3 py-2 text-xs"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="font-mondwest tracking-[0.08em] text-text-secondary">
                  Debug · runtime
                </span>
                <Button
                  ghost
                  size="xs"
                  onClick={loadDebug}
                  disabled={debugState === "loading"}
                  aria-label="Refresh debug overlay"
                >
                  {debugState === "loading" ? <Spinner /> : "refresh"}
                </Button>
              </div>
              {debugState === "loading" ? (
                <p className="flex items-center gap-2 text-text-tertiary">
                  <Spinner className="shrink-0" /> Loading runtime + logs…
                </p>
              ) : debugState === "error" ? (
                <p role="alert" className="text-destructive">
                  {debugError}
                </p>
              ) : (
                <>
                  <p className="text-text-secondary">
                    gateway: <span className="font-mono">{debugRuntime}</span>
                  </p>
                  {debugInventory ? (
                    <p className="text-text-secondary">
                      runtime:{" "}
                      <span className="font-mono">
                        {JSON.stringify({
                          agent:
                            (debugInventory.agent as { state?: string })
                              ?.state ?? null,
                          broker:
                            (debugInventory.broker as { state?: string })
                              ?.state ?? null,
                          approvals:
                            (debugInventory.approvals as { pending?: number })
                              ?.pending ?? null,
                          provider:
                            (
                              debugInventory.providers as {
                                resident_alias?: string;
                              }
                            )?.resident_alias ?? null,
                        })}
                      </span>
                    </p>
                  ) : null}
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[0.6875rem] leading-tight text-text-tertiary">
                    {debugLogs.length > 0
                      ? debugLogs.join("\n")
                      : "No log lines found."}
                  </pre>
                </>
              )}
            </div>
          )}

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
              <>
                {selectedMessages.size > 0 && (
                  <div
                    data-state="selection"
                    className="mb-2 flex flex-wrap items-center gap-2 border border-border bg-muted/20 px-3 py-2 text-xs"
                  >
                    <span className="text-text-secondary">
                      {selectedMessages.size} selected
                    </span>
                    <Button
                      size="xs"
                      outlined
                      onClick={copySelected}
                      prefix={<Copy className="h-3.5 w-3.5" />}
                      aria-label="Copy selected messages"
                    >
                      {copyState === "copied" ? "copied" : "copy selected"}
                    </Button>
                    <Button
                      ghost
                      size="xs"
                      onClick={() => setSelectedMessages(new Set())}
                      aria-label="Clear message selection"
                    >
                      clear
                    </Button>
                  </div>
                )}
                <ul className="flex list-none flex-col gap-3">
                {messages.map((message, index) => (
                  <li
                    key={`${message.role}-${index}`}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-sm",
                      message.kind === "tool"
                        ? "mr-6 border-dashed border-current/15 bg-background-base/40"
                        : message.role === "user"
                          ? "ml-6 border-accent/40 bg-accent/10"
                          : "mr-6 border-current/15 bg-background-base/70",
                    )}
                  >
                    {message.kind === "tool" ? (
                      <div className="mb-1 flex items-center gap-2 text-xs font-semibold tracking-wide text-text-tertiary">
                        <ToolIcon name={message.toolName} />
                        <span>
                          {message.toolName
                            ? `tool: ${message.toolName}`
                            : "tool"}
                        </span>
                      </div>
                    ) : (
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold tracking-wide text-text-secondary">
                          {message.role === "user" ? "You" : "Qubit"}
                        </span>
                        <label className="flex items-center gap-1 text-xs text-text-tertiary">
                          <input
                            type="checkbox"
                            checked={selectedMessages.has(index)}
                            onChange={() => toggleSelected(index)}
                            aria-label={`Select message ${index + 1} from ${message.role === "user" ? "You" : "Qubit"}`}
                            className="h-3 w-3 accent-current"
                          />
                        </label>
                      </div>
                    )}
                    <div className="whitespace-pre-wrap break-words">
                      {message.text}
                    </div>
                    {message.kind !== "tool" && message.role === "assistant" ? (
                      <SandboxedHtmlPreview html={message.text} />
                    ) : null}
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
              </>
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

          {attachError ? (
            <div
              role="alert"
              data-state="degraded"
              className="mt-2 border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              Image attach failed: {attachError}
            </div>
          ) : null}

          <form
            onSubmit={onSubmit}
            className="mt-2 flex shrink-0 flex-wrap items-end gap-2 border-t border-current/10 pt-2"
          >
            <label className="sr-only" htmlFor="qubit-chat-input">
              Message Qubit
            </label>
            {supportsReasoning ? (
              <label className="flex shrink-0 items-center gap-1 text-xs text-text-tertiary">
                <span className="sr-only">Reasoning effort</span>
                <select
                  aria-label="Reasoning effort"
                  data-testid="chat-reasoning-picker"
                  className="rounded border border-current/15 bg-background-base/60 px-1 py-1 text-xs text-text-secondary"
                  title="Reasoning effort: per-request output budget sent to the model and receipted per turn"
                  value={reasoning ?? ""}
                  onChange={(e) => handleReasoningChange(e.target.value)}
                >
                  <option value="">Reasoning: default</option>
                  {CHAT_REASONING_LEVELS.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.id} · {l.budget}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <textarea
              id="qubit-chat-input"
              value={input}
              disabled={busy}
              rows={2}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={onPasteImage}
              placeholder="Message Qubit… (Enter to send, Shift+Enter for newline)"
              aria-label="Message Qubit"
              className={cn(
                "min-h-[2.5rem] min-w-full flex-1 resize-none rounded-md border border-current/20 sm:min-w-0",
                "bg-background-base/70 px-3 py-2 text-sm text-foreground",
                "focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60",
              )}
            />
            <input
              ref={attachInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
              className="hidden"
              aria-hidden="true"
              tabIndex={-1}
              onChange={(event) => {
                describeImage(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
            <Button
              type="button"
              outlined
              onClick={() => attachInputRef.current?.click()}
              disabled={busy || attachState === "describing"}
              aria-label="Attach image"
              title="Attach or paste an image; Qubit describes it with vision"
              prefix={<ImagePlus className="h-4 w-4" />}
            >
              {attachState === "describing" ? "Reading…" : "Image"}
            </Button>
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

        {sidePanelOpen && (
          <div
            id="chat-side-panel"
            role="complementary"
            aria-label={modelToolsLabel}
            className="flex min-h-0 shrink-0 flex-col gap-3 overflow-hidden lg:h-full lg:w-60"
          >
            <div className="shrink-0">
              <Suspense fallback={<div aria-hidden="true" className="h-24" />}>
                <ChatSidebar
                  channel={channel}
                  profile={scopedProfile}
                  onDashboardNewSessionRequest={startFresh}
                />
              </Suspense>
            </div>
            <ChatSessionArtifacts key={session} sessionId={session} />
            <div className="min-h-0 flex-1 overflow-hidden">
              <ChatSessionList
                key={sessionListNonce}
                activeSessionId={resumeParam}
                profile={scopedProfile}
                onNewChat={startFresh}
              />
            </div>
          </div>
        )}
      </div>

      <PluginSlot name="chat:bottom" />
    </div>
  );
}
