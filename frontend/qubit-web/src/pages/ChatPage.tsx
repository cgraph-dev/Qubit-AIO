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
  ArrowUpRight,
  Bug,
  Code,
  Copy,
  FileText,
  GitBranch,
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

import {
  ChatSessionList,
  FRESH_CHAT_SESSION_ID,
} from "@/components/ChatSessionList";
import { ChatSessionArtifacts } from "@/components/ChatSessionArtifacts";
import { ChatToolsSummary } from "@/components/ChatToolsSummary";
import { Markdown } from "@/components/Markdown";
import { firstFileFromTransfer, firstImageFromClipboard } from "@/lib/chatImagePaste";

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
  withBackoff,
  type ChatStoreMessage,
  type ChatStreamEvent,
  type PendingConfirmation,
} from "@/lib/api";
import {
  asSpeechRecognition,
  BROWSER_PRIVACY_NOTICE,
  joinTranscript,
  LOCAL_PRIVACY_NOTICE,
  terminalCaptureAvailable,
  type ActiveVoiceEngine as ControllerActiveEngine,
} from "@/lib/voice";
import { useTranscriptionController } from "@/hooks/useTranscriptionController";
import { useDesktopVoiceHelper } from "@/hooks/useDesktopVoiceHelper";
import { useChatActivity } from "@/hooks/useChatActivity";
import { useChatLiveEvents } from "@/hooks/useChatLiveEvents";
import { type ChatSessionFrame } from "@/lib/chatLiveEvents";
import { shouldRemountSessionList } from "@/components/chatLiveListPolicy";
import { ChatWorkPanel } from "@/components/ChatWorkPanel";
import { ReadAloudButton } from "@/components/ReadAloudButton";
import { CommandPalette } from "@/components/CommandPalette";
import { ReportList } from "@/components/ReportList";
import { cn } from "@/lib/utils";
import { type PaletteItem } from "@/lib/commandPalette";
import {
  atTokenAt,
  filterSlashCommands,
  slashCompletionText,
  slashTokenAt,
  SLASH_COMMANDS,
  type SlashCommandItem,
} from "@/lib/slashCommands";
import { voiceEdit, type VoiceSessionMode } from "@/lib/voiceEdit";
import { PluginSlot } from "@/plugins";
import "./ChatPage.css";
import "./ChatPage.studio.css";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  /** Small render-time metadata line (model / elapsed). */
  meta?: string;
  /** A36: tool/system rows are rendered distinctly, not as assistant prose. */
  kind?: "message" | "tool";
  toolName?: string;
  /** Wave C/D1: a finished /chat-worker build report (markdown + sources). */
  report?: boolean;
  /** Full /chat-worker report body when `text` is the concise summary. */
  reportText?: string;
  /** Wave C web-branch source URLs, rendered under the reply. */
  sources?: string[];
  /** Wave C build-task key this message reports on. */
  taskKey?: string;
}

/** Wave C/D1: live /chat-worker build state from `progress` stream frames. */
interface TaskProgress {
  key: string;
  status: string;
  detail?: string;
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
    route?: string;
    task?: string;
    phase?: string;
    sources?: unknown;
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
  // A persisted /chat-worker build turn carries meta.route="task" + meta.task;
  // keep it rendering as a report with its task key + /chat link after a
  // reload (the live stream sets these, but the reload path must too — the
  // store persists the envelope in the message sidecar). The transient
  // "Working on it in /chat (task …)" starter row shares the envelope
  // (phase "started") but is NOT the report: the report index lists only the
  // finished report and terminal states, never the placeholder.
  const isTask =
    meta.route === "task" && Boolean(meta.task) && meta.phase !== "started";
  const sources = Array.isArray(meta.sources)
    ? meta.sources.filter((s): s is string => typeof s === "string")
    : undefined;
  return {
    role: message.role === "user" ? "user" : "assistant",
    text: message.content ?? "",
    meta: parts.join(" ") || undefined,
    kind: isTool ? "tool" : "message",
    toolName: meta.tool_name ? String(meta.tool_name) : undefined,
    report: isTask || undefined,
    taskKey: isTask ? String(meta.task) : undefined,
    sources: sources && sources.length > 0 ? sources : undefined,
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

/**
 * Best-effort read of a bounded pending-confirmation signal from a turn event
 * or a session-load response. Returns null when the field is absent or empty,
 * so callers can both SET (truthy) and CLEAR (falsy) from the same helper.
 */
function extractPendingConfirm(source: {
  pending_confirmation?: PendingConfirmation | null;
}): PendingConfirmation | null {
  const pending = source?.pending_confirmation;
  if (!pending || typeof pending !== "object") return null;
  const action_summary =
    typeof pending.action_summary === "string" && pending.action_summary
      ? pending.action_summary
      : undefined;
  const staged_ms =
    typeof pending.staged_ms === "number" ? pending.staged_ms : undefined;
  if (action_summary === undefined && staged_ms === undefined) return null;
  return { action_summary, staged_ms };
}

const STORAGE_PREFIX = "qubit.chat.transcript.";
const SESSION_PROGRESS_PREFIX = "qubit.chat.progress.";
const SESSION_KEY = "qubit.chat.session";
const DEFAULT_SESSION = "dashboard-chat";
/** How often a followed voice session re-reads its persisted transcript. */
const VOICE_LIVE_POLL_MS = 2000;

/** True when a chat-session id belongs to the voice bridge (`voice-*`). The
 *  desktop helper routes Right-Ctrl / wake turns to a `voice-*` session
 *  (`voicebridge/desktop_helper.py` `_DEFAULT_VOICE_SESSION`), so the prefix is
 *  the stable contract for "this session receives voice turns". */
function isVoiceSessionId(id: string | null | undefined): boolean {
  return typeof id === "string" && id.startsWith("voice-");
}

/**
 * Newest `voice-*` session id from a chat-session list, or null. "Newest" is
 * the greatest last_active/started_at; a list with no timestamps falls back to
 * the first voice row. Pure and non-fabricating — an empty list yields null.
 */
function pickNewestVoiceSession(
  sessions:
    | Array<{ id?: string; last_active?: number; started_at?: number }>
    | null
    | undefined,
): string | null {
  const voices = (sessions ?? []).filter((s) => isVoiceSessionId(s?.id));
  if (voices.length === 0) return null;
  const ts = (s: { last_active?: number; started_at?: number }) =>
    s.last_active ?? s.started_at ?? 0;
  const newest = voices.reduce((best, s) => (ts(s) > ts(best) ? s : best));
  return newest.id ?? null;
}

/** Voice-session namespace + retention, read from the settings view. */
interface VoiceSessionInfo {
  mode: VoiceSessionMode;
  ttlS: number;
  maxMessages: number;
  maxSessions: number;
}

/** Compact retention summary for the live-voice control, e.g.
 *  "keep 50 · 24h TTL · 200 msgs". Pure and total. */
function formatVoiceRetention(info: VoiceSessionInfo): string {
  const ttl =
    info.ttlS % 86400 === 0
      ? `${info.ttlS / 86400}d`
      : info.ttlS % 3600 === 0
        ? `${info.ttlS / 3600}h`
        : info.ttlS % 60 === 0
          ? `${info.ttlS / 60}m`
          : `${info.ttlS}s`;
  return `keep ${info.maxSessions} · ${ttl} TTL · ${info.maxMessages} msgs`;
}


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

interface SessionProgress {
  userTurns: number;
  toolRuns: number;
}

function loadSessionProgress(session: string): SessionProgress {
  try {
    const parsed: unknown = JSON.parse(
      storageGet(SESSION_PROGRESS_PREFIX + session) ?? "null",
    );
    if (!parsed || typeof parsed !== "object") return { userTurns: 0, toolRuns: 0 };
    const progress = parsed as Partial<SessionProgress>;
    return {
      userTurns: Number.isFinite(progress.userTurns) ? Math.max(0, Number(progress.userTurns)) : 0,
      toolRuns: Number.isFinite(progress.toolRuns) ? Math.max(0, Number(progress.toolRuns)) : 0,
    };
  } catch {
    return { userTurns: 0, toolRuns: 0 };
  }
}

function saveSessionProgress(session: string, progress: SessionProgress): void {
  storageSet(SESSION_PROGRESS_PREFIX + session, JSON.stringify(progress));
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

/**
 * Default ask submitted with a pasted/dropped image when the composer is empty.
 * Mirrors the server's `_vision_turn_text` default so the transcript's user
 * bubble matches what the agent was asked.
 */
const DEFAULT_IMAGE_ASK = "What is in this image, and what should I know about it?";

type VoiceEngine = "browser" | "local" | "terminal" | "auto";

const VOICE_ENGINES = ["auto", "browser", "local", "terminal"] as const;

/** Canonical `voice.input_engine` semantics; unknown/missing fails to `auto`. */
function normalizeVoiceEngine(value: unknown): VoiceEngine {
  const s = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (VOICE_ENGINES as readonly string[]).includes(s)
    ? (s as VoiceEngine)
    : "auto";
}

function localCaptureAvailable(): boolean {
  if (typeof navigator === "undefined") return false;
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
  // The transcription controller is created once; this ref lets the local/
  // terminal adapter read the *current* session id for the STT request without
  // recreating the controller on every session change.
  const sessionRef = useRef(session);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  // ── Live voice follow ────────────────────────────────────────────────
  // `voice-*` sessions receive the desktop helper's Right-Ctrl / wake turns.
  // While such a session is open (and the operator has not turned following
  // off), the transcript re-reads the SAME server store it already loads from
  // and appends only the new rows — no reload, no new backend route, no PTY.
  const isVoiceSession = isVoiceSessionId(session);
  const [followVoice, setFollowVoice] = useState(true);
  const [newestVoiceSession, setNewestVoiceSession] = useState<string | null>(
    null,
  );
  // Voice-session namespace + retention for the live control. Best-effort:
  // a failed/unavailable settings read shows the mode note only, never a
  // fabricated default presented as truth.
  const [voiceSessionInfo, setVoiceSessionInfo] = useState<VoiceSessionInfo | null>(
    null,
  );
  const [voiceSessionInfoError, setVoiceSessionInfoError] = useState(false);
  const followLiveVoice = isVoiceSession && followVoice;
  // The number of server rows already merged into `messages` for the open
  // session (index-based dedupe: a poll appends only `slice(known)`).
  const voiceSyncedRef = useRef(0);
  const voiceSyncBusyRef = useRef(false);
  // True only once the open session's transcript has settled, so a poll can
  // never race the initial load and duplicate its rows.
  const transcriptReadyRef = useRef(false);

  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    loadTranscript(session),
  );
  const [transcriptSession, setTranscriptSession] = useState(session);
  const [transcriptState, setTranscriptState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  // A ?resume= id that is NOT a Qubit-chat session (e.g. a canonical /chat
  // worker id): don't hijack this page's conversation — offer to open it in
  // the /chat terminal instead. Null when the current session is ours.
  const [foreignSession, setForeignSession] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [input, setInput] = useState("");
  // Slash autocomplete selection + Esc-dismissal (single-token "/" prefix only;
  // see `slashVisible` below). The composer owns no gateway socket, so the
  // popup filters the static registry catalog, not `complete.slash`.
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissedFor, setSlashDismissedFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [assistantActivity, setAssistantActivity] = useState<"thinking" | "summarizing">("thinking");
  // D1 Task 5: the live /chat-worker build card. Set from `progress` frames,
  // marked done from the task `done` receipt; cleared on the next send.
  const [taskProgress, setTaskProgress] = useState<TaskProgress | null>(null);
  // A bounded "awaiting confirmation" signal: the shared backend has a live
  // staged action that needs an explicit yes/no. Set from turn events (and
  // restored from session load); cleared when a turn resolves without the
  // signal or when the session changes. The Confirm/Cancel affordance reuses
  // the normal `send` path, so it settles through the same backend confirm.
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirmation | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [retryText, setRetryText] = useState<string | null>(null);
  // Live transcription (one controller for browser/local/terminal). The
  // snapshot drives the composer, status strip and Right-Ctrl/push-to-talk.
  const { controller, snapshot: voice } = useTranscriptionController({
    sessionId: () => sessionRef.current,
  });
  // True when the system-wide desktop helper is running and owns Right-Ctrl;
  // then this page yields the hotkey to it (still types into this composer).
  const desktopHelperLive = useDesktopVoiceHelper(isActive);
  // `voice.input_engine` (auto | browser | local | terminal) is the persisted
  // selection; it never changes the model, session, tools, policy, audit,
  // confirmation, or e-stop path.
  const [inputEngine, setInputEngine] = useState<VoiceEngine>("auto");
  // Informational (non-error) status, e.g. a backend-cancelled turn. Kept
  // separate from the transcription error so a cancellation is not styled as
  // a failure.
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  // The ACTIVE SESSION's own stored model (``sessions.model``), distinct from
  // the global default ``model`` state above. ``/qubit-chat`` owns its model
  // per session — like ``/chat`` (which passes its PTY grant) — so the side
  // panel must show THIS session's model, not the config default. Read from
  // ``GET /api/qubit/chat/sessions/:id`` on session load and after a pick.
  const [sessionModel, setSessionModel] = useState<string>("");
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
  // surfacing the freshly created session immediately (its internal fetch only
  // runs on mount / manual Refresh).
  const [sessionListNonce, setSessionListNonce] = useState(0);
  // True while the surface sits on a DELIBERATE fresh chat (New chat pressed,
  // nothing resumed yet). Drives the session panel's `activeSessionId` so it
  // renders no highlight while the mint round-trips, instead of falling back to
  // the server's `is_active` row (several sessions are legitimately live at
  // once). Cleared once a real session id resolves.
  const [freshChat, setFreshChat] = useState(false);
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
  const [paletteOpen, setPaletteOpen] = useState(false);
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

  const rightCtrlHeldRef = useRef(false);
  // D1 Task 6: mirrored into state so the listener status line re-renders.
  // Same source as the push-to-talk gate below — no new backend route.
  const [rightCtrlHeld, setRightCtrlHeld] = useState(false);
  // Controller text projection: the last committed string already merged into
  // the composer, and the last browser final already auto-sent (Chrome keeps
  // its proven send-on-final behavior).
  const lastCommittedRef = useRef("");
  const browserSentRef = useRef("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followTranscriptRef = useRef(true);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const loadReqRef = useRef(0);
  // Sessions already auto-resumed in this page load, so a surviving crash marker
  // can re-issue a turn exactly once (a crash loop must not spin here).
  const resumedRef = useRef<Set<string>>(new Set());
  // Late-bound sender: the load effect that detects an interrupted turn runs
  // before `send` is defined, so it reaches it through this ref.
  const sendRef = useRef<((raw: string, activity?: "thinking" | "summarizing") => void) | null>(null);
  // True while the in-flight turn is a build/task worker turn (its `progress`
  // frames set it). The report delta must not also accumulate into the
  // streaming bubble — it is written back once as the message's reportText.
  const taskTurnRef = useRef(false);

  // Keep the session pointer stable in storage so a reload lands back on the
  // same conversation even without a ?resume= param.
  useEffect(() => {
    if (resumeParam) storageSet(SESSION_KEY, resumeParam);
  }, [resumeParam]);

  // A real resolved session id means we are no longer on a deliberate fresh
  // chat, so the panel may highlight it again. Keyed on `resumeParam` (not the
  // `session` memo, which already falls back to a stored/default id) so only an
  // actual URL resume clears the flag.
  useEffect(() => {
    if (resumeParam) setFreshChat(false);
  }, [resumeParam]);

  // Mirror the resolved session into the URL (like /chat?resume=<id>) so the
  // address bar is a faithful deep link: bare /qubit-chat gains
  // ?resume=<session> on load via replace (no history spam). Guarded on
  // !resumeParam so it never loops; the session memo recomputes to the
  // identical string, so the transcript-load effect below does not re-fire.
  useEffect(() => {
    if (!resumeParam && session) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("resume", session);
          return next;
        },
        { replace: true },
      );
    }
  }, [resumeParam, session, setSearchParams]);

  // Load the selected session's persisted transcript from the Qubit chat
  // store (`GET /api/qubit/chat/sessions/<id>/messages`). The server store is
  // authoritative; the session list is an AUXILIARY read (newest live voice
  // session, foreign-id detection) so a slow or failing list request can never
  // strand the transcript — the reported "reload shows a blank chat until I
  // click a session in the right rail" defect, which was the transcript load
  // being gated behind the list fetch.
  useEffect(() => {
    const myReq = ++loadReqRef.current;
    // Instant paint from the cache; the server load below replaces it.
    setMessages(loadTranscript(session));
    setTranscriptSession(session);
    setStreamingText("");
    setError(null);
    setRetryText(null);
    setPendingConfirm(null);
    // Clear the previous turn's busy flag on ANY session switch. A turn stream
    // whose signal is aborted (which a session switch does) skips its own
    // `setBusy(false)` — see `startFresh` — so a switch made mid/after a turn
    // would otherwise leave `busy` true and strand the fresh transcript pane
    // blank until a refresh.
    setBusy(false);
    setTranscriptState("loading");
    // Re-baseline the live-voice merge counter for this session; it is set to
    // the settled server row count once the transcript load completes.
    voiceSyncedRef.current = 0;
    transcriptReadyRef.current = false;

    // Authoritative transcript read: probe the canonical session first so a
    // not-yet-created id (the unsaved default, a fresh id) never issues a
    // transcript request (which would 404 in the console) and an id that
    // belongs to the /chat terminal is flagged foreign. The probe is
    // independent of the LIST read, so a slow or failing list can no longer
    // strand the transcript — the reported "reload shows a blank chat until I
    // click a session in the right rail" defect.
    const loadMessages = withBackoff(() => api.getChatSession(session), {
      shouldStop: () => loadReqRef.current !== myReq,
    })
      .then((info) => {
        if (loadReqRef.current !== myReq) return;
        setReasoning(normalizeReasoning(info.meta?.reasoning));
        setSessionModel(String(info.session?.model || ""));
        setPendingConfirm(extractPendingConfirm(info));
        return withBackoff(() => api.getChatSessionMessages(session), {
          shouldStop: () => loadReqRef.current !== myReq,
        }).then((res) => {
          if (loadReqRef.current !== myReq) return;
          const loaded = (res.messages ?? []).map(toChatMessage);
          setMessages(loaded);
          setTranscriptSession(session);
          saveTranscript(session, loaded);
          setTranscriptState("ready");
          // The loaded rows are what a live-voice poll diffs against.
          voiceSyncedRef.current = loaded.length;
          transcriptReadyRef.current = true;
          // A turn cut short by a backend restart: re-issue it so the
          // operator does not have to retype `continue`. Guarded to once per
          // session per page load, so a crash loop cannot spin here.
          const interrupted = info.interrupted_turn;
          if (interrupted?.prompt && !resumedRef.current.has(session)) {
            resumedRef.current.add(session);
            sendRef.current?.(interrupted.prompt, "thinking");
          }
        });
      })
      .catch((err: unknown) => {
        // The probe 404s for an id with no server record. The unsaved default
        // is simply a fresh Qubit chat (empty composer); any OTHER id is a
        // /chat worker id — flag it foreign so the UI offers to open it there
        // and keeps this page usable.
        if (loadReqRef.current !== myReq) return;
        const message = err instanceof Error ? err.message : String(err ?? "");
        if (/\b404\b/.test(message)) {
          setForeignSession(session === DEFAULT_SESSION ? null : session);
          setReasoning(null);
          setMessages([]);
          setTranscriptState("ready");
          voiceSyncedRef.current = 0;
          transcriptReadyRef.current = true;
          return;
        }
        throw err;
      });

    // Auxiliary list read (never gates the transcript): the newest live voice
    // session for the one-click jump. Foreign-id detection is owned by the
    // probe above, so this read never sets it (two writers would race).
    const loadList = api
      .listChatSessions(100, 0)
      .then((list) => {
        if (loadReqRef.current !== myReq) return;
        setNewestVoiceSession(pickNewestVoiceSession(list.sessions));
      })
      .catch(() => {
        /* best-effort: the transcript load above already decided the state */
      });

    Promise.allSettled([loadMessages, loadList]).then(([messagesResult]) => {
      if (loadReqRef.current !== myReq) return;
      // Only a failed TRANSCRIPT read degrades the page; a failed list read
      // does not. A not-yet-created id resolves via the probe's 404 branch
      // (empty state), so anything that rejects here is a real load failure.
      if (messagesResult.status !== "rejected") return;
      const reason = messagesResult.reason;
      setTranscriptState("error");
      setError(reason instanceof Error ? reason.message : String(reason ?? "load failed"));
    });
  }, [session]);

  // Live voice follow: while the open session is a `voice-*` session (and
  // following has not been turned off), re-read the persisted transcript on a
  // short interval and on window focus, appending only the NEW rows. Dedupe is
  // index-based via `voiceSyncedRef`, so a repeated poll of the same rows can
  // never double-append and two genuinely identical utterances ("yes", "yes")
  // are both kept. Read-only against the same store the initial load uses — no
  // new backend route, no PTY; it stops the instant the session is not a voice
  // session. Best-effort: a failed poll is swallowed and heals on the next tick.
  useEffect(() => {
    if (!followLiveVoice) return;
    let alive = true;
    const sync = () => {
      if (voiceSyncBusyRef.current || !transcriptReadyRef.current) return;
      voiceSyncBusyRef.current = true;
      api
        .getChatSessionMessages(session)
        .then((res) => {
          if (!alive) return;
          const mapped = (res.messages ?? []).map(toChatMessage);
          const known = voiceSyncedRef.current;
          if (mapped.length > known) {
            const additions = mapped.slice(known);
            voiceSyncedRef.current = mapped.length;
            setMessages((prev) => prev.concat(additions));
          } else if (mapped.length < known) {
            // The store shrank (e.g. a session reset). Re-baseline without
            // dropping anything already rendered; the next poll appends anew.
            voiceSyncedRef.current = mapped.length;
          }
        })
        .catch(() => {
          /* best-effort — the next tick retries */
        })
        .finally(() => {
          voiceSyncBusyRef.current = false;
        });
    };
    const interval = setInterval(sync, VOICE_LIVE_POLL_MS);
    window.addEventListener("focus", sync);
    return () => {
      alive = false;
      clearInterval(interval);
      window.removeEventListener("focus", sync);
    };
  }, [followLiveVoice, session]);

  // Read the voice-session namespace + retention from the settings view so the
  // live control can show the real policy. Only fetched for a `voice-*`
  // session (a normal chat has no voice control), and best-effort: any failure
  // leaves the info null so the UI shows an honest unknown rather than a
  // fabricated default.
  useEffect(() => {
    if (!isVoiceSession) return;
    let alive = true;
    (async () => {
      try {
        const out = await voiceEdit.settings();
        if (!alive) return;
        const s = out.settings;
        setVoiceSessionInfo({
          mode:
            s.voice_session_mode === "per_command" ? "per_command" : "single",
          ttlS: Number(s.voice_session_ttl_s ?? 86400),
          maxMessages: Number(s.voice_session_max_messages ?? 200),
          maxSessions: Number(s.voice_session_max_sessions ?? 50),
        });
        setVoiceSessionInfoError(false);
      } catch {
        if (!alive) return;
        setVoiceSessionInfo(null);
        setVoiceSessionInfoError(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [isVoiceSession, session]);

  // Keep the read-through cache in step with the rendered transcript.
  useEffect(() => saveTranscript(session, messages), [session, messages]);

  // Progress is stored by canonical session id, independently of the visible
  // transcript window, so returning to this session restores its highest run.
  useEffect(() => {
    if (transcriptSession !== session) return;
    const saved = loadSessionProgress(session);
    const userTurns = messages.filter(
      (message) => message.role === "user" && message.kind !== "tool",
    ).length;
    const toolRuns = messages.filter((message) => message.kind === "tool").length;
    saveSessionProgress(session, {
      userTurns: Math.max(saved.userTurns, userTurns),
      toolRuns: Math.max(saved.toolRuns, toolRuns),
    });
  }, [messages, session, transcriptSession]);

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

  // Live chat-session updates: the server announces every session create /
  // model pick / rename / delete on the single SSE feed (a CUSTOM frame). This
  // is the client half of the live-update contract — without it the open page
  // kept stale state until a manual reload (the reported defect). One reader
  // here fans out to the session list (bump its nonce) and to the MODEL card
  // (re-read the effective model for the active session).
  const refreshModelLive = useCallback(() => {
    api
      .getModelInfo(scopedProfile)
      .then((info) => {
        setModel(info.model || "");
        setSupportsReasoning(info.capabilities?.supports_reasoning === true);
      })
      .catch(() => {
        /* keep last-known */
      });
  }, [scopedProfile]);

  useChatLiveEvents(
    useCallback(
      (frame: ChatSessionFrame) => {
        if (frame.isSession) {
          // Only a membership/order change remounts the list. `ChatSessionList`
          // is keyed on the nonce, so bumping it discards the panel's search
          // text, scroll position and "See more" offset. A per-session MODEL
          // pick publishes `session.updated` for that row — if the row is a
          // background/voice session it changes neither membership nor order,
          // and remounting there churned the open list under the operator (the
          // reported "switch provider, list jumps" defect). See
          // chatLiveListPolicy for the rule.
          if (shouldRemountSessionList(frame, session)) {
            setSessionListNonce((n) => n + 1);
          }
          // The active session's own model follows whenever the updated row is
          // ours, regardless of whether a remount was warranted.
          if (frame.session && frame.sessionId === session) {
            const m = String(frame.session.model || "");
            if (m) setSessionModel(m);
          }
          return;
        }
        if (frame.isModelChange) {
          refreshModelLive();
        }
      },
      [session, refreshModelLive],
    ),
  );

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

  // Follow new output only while the operator is already at the bottom.
  // Otherwise a small upward scroll is immediately undone on every stream
  // delta, making the scroll-up affordance appear to jump away.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followTranscriptRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, busy, error]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const updateFollow = () => {
      followTranscriptRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    };
    el.addEventListener("scroll", updateFollow, { passive: true });
    updateFollow();
    return () => el.removeEventListener("scroll", updateFollow);
  }, [transcriptSession]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      // The transcription controller (and its adapter/capture) is disposed by
      // the hook's own unmount effect.
      abortRef.current?.abort();
    };
  }, []);

  const send = useCallback(
    (raw: string, activity: "thinking" | "summarizing" = "thinking") => {
      const text = (raw || "").trim();
      if (!text || busy) return;
      setInput("");
      setError(null);
      setRetryText(null);
      setVoiceNotice(null);
      setBusy(true);
      setAssistantActivity(activity);
      setStreamingText("");
      setTaskProgress(null);
      taskTurnRef.current = false;
      setMessages((prev) => prev.concat([{ role: "user", text }]));
      // A locally-driven turn in a followed voice session appends rows the
      // server will persist too; advance the merge counter so the next poll
      // does not re-append them as "new".
      if (followLiveVoice) voiceSyncedRef.current += 1;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // SSE turn on the shared chat transport: deltas render live, the final
      // reply is persisted server-side, and a typed error is surfaced as-is.
      // Wave C build turns also emit `progress` frames (task card) and finish
      // with a full-report delta + a `done` receipt carrying route/sources.
      // The reasoning selector rides along (null = session preference, which
      // the server resolves from the persisted session metadata).
      streamChatTurn(
        reasoning ? { text, session, reasoning } : { text, session },
        {
          onDelta: (delta, event) => {
            if (controller.signal.aborted) return;
            // A build/task turn streams progress frames and THEN the whole
            // worker report as one delta; that same report is written back on
            // `done` as `reportText` (rendered in the "Full /chat report"
            // disclosure). Accumulating it into `streamingText` as well paints
            // the report twice — once in the streaming bubble, once in the
            // appended message — and the swap at `done` is the flicker. The
            // `progress` frame for this turn sets `taskTurnRef` before the
            // report delta arrives, so gate on it: a task turn's progress card
            // already shows liveness, and its report lands once, in the card.
            if (event?.route === "task" || taskTurnRef.current) return;
            setStreamingText((prev) =>
              prev + (typeof delta === "string" ? delta : ""),
            );
            const pending = extractPendingConfirm(event);
            if (pending) setPendingConfirm(pending);
          },
          onProgress: (event) => {
            if (controller.signal.aborted) return;
            // The FIRST progress frame of this turn also settles the fact that
            // the server persisted an extra assistant row the client never
            // renders: `_stream_task` writes "Working on it in /chat (task …)"
            // (voicebridge/chat_transport.py) before the report row. A live
            // voice session's poll diffs the persisted rows against
            // `voiceSyncedRef` (index-based), so without counting that row the
            // completed report is re-appended as "new" and renders twice. Only
            // the first frame counts — later keepalive `progress` frames (one
            // every ~5s while the worker runs) describe the SAME persisted row.
            if (!taskTurnRef.current && followLiveVoice) {
              voiceSyncedRef.current += 1;
            }
            taskTurnRef.current = true;
            const pending = extractPendingConfirm(event);
            if (pending) setPendingConfirm(pending);
            const key = event.task ?? "";
            if (!key) return;
            setTaskProgress({
              key,
              status: event.status ?? "working",
              detail: event.detail,
            });
          },
          onDone: (event) => {
            if (controller.signal.aborted) return;
            // Authoritative settle: a done frame that no longer carries the
            // signal clears the banner; one that still carries it keeps it.
            setPendingConfirm(extractPendingConfirm(event));
            const route = event.receipt?.route ?? event.route;
            const sources = event.receipt?.sources ?? event.sources;
            const taskKey = event.receipt?.task ?? event.task;
            if (route === "task" && taskKey) {
              setTaskProgress((prev) =>
                prev && prev.key === taskKey
                  ? { ...prev, status: "done", detail: "worker finished" }
                  : { key: taskKey, status: "done", detail: "worker finished" },
              );
            }
            setMessages((prev) =>
              prev.concat([
                {
                  role: "assistant",
                  // Build turns: write back the CONCISE summary (what the
                  // voice layer speaks) and keep the full report behind a
                  // "Full /chat report" disclosure. Coerce every field to a
                  // string — a frame whose summary/reply is not text would
                  // otherwise reach <Markdown> and blank the page.
                  text:
                    route === "task" && event.summary
                      ? String(event.summary)
                      : typeof event.reply === "string"
                        ? event.reply
                        : "",
                  meta: streamMeta(event),
                  report: route === "task" && Boolean(taskKey),
                  reportText:
                    route === "task" && event.summary &&
                    String(event.summary) !== String(event.reply ?? "")
                      ? typeof event.reply === "string"
                        ? event.reply
                        : undefined
                      : undefined,
                  taskKey: taskKey,
                  sources:
                    Array.isArray(sources) && sources.length > 0
                      ? sources.filter((s): s is string => typeof s === "string")
                      : undefined,
                },
              ]),
            );
            setStreamingText("");
            // The turn persisted on the canonical session store (the reply and
            // session metadata), so remount/refetch the session list: a brand
            // new default row or an updated ordering surfaces without a manual
            // refresh.
            if (followLiveVoice) voiceSyncedRef.current += 1;
            setSessionListNonce((n) => n + 1);
          },
          onError: (event) => {
            if (controller.signal.aborted) return;
            setPendingConfirm(extractPendingConfirm(event));
            setTaskProgress((prev) =>
              prev && prev.status === "working"
                ? {
                    ...prev,
                    status: "error",
                    detail: event.detail ?? event.code ?? "task failed",
                  }
                : prev,
            );
            setError(
              `[${event.code ?? "TURN_FAILED"}] ${event.detail ?? "turn failed"}`,
            );
            setRetryText(text);
            setStreamingText("");
            // A failed turn may still have touched the session row (it was
            // created/persisted before the error), so re-sync the list.
            setSessionListNonce((n) => n + 1);
          },
          onCancelled: (event) => {
            if (controller.signal.aborted) return;
            setPendingConfirm(extractPendingConfirm(event));
            setTaskProgress((prev) =>
              prev && prev.status === "working"
                ? { ...prev, status: "cancelled", detail: "turn cancelled" }
                : prev,
            );
            setStreamingText("");
            setVoiceNotice("turn cancelled — the shared backend aborted it");
            setSessionListNonce((n) => n + 1);
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
    [busy, session, reasoning, followLiveVoice],
  );

  // Publish the sender for the load effect (which runs before `send` exists) so
  // an interrupted turn detected on load can be re-issued automatically.
  useEffect(() => {
    sendRef.current = send;
  }, [send]);

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

  // Persist the voice engine selection on the canonical config
  // (`voice.input_engine`). A minimal slice is PUT: `config_save` flattens it
  // to the `voice.input_engine` override, so sibling keys are never clobbered
  // (and an unrelated rejected key can never revert the selection). Optimistic,
  // reverted on failure.
  const handleEngineChange = useCallback(
    (next: VoiceEngine) => {
      if (next === inputEngine) return;
      const prev = inputEngine;
      setInputEngine(next);
      controller.setRequestedEngine(next);
      void api
        .saveConfig({ voice: { input_engine: next } }, scopedProfile)
        .catch(() => {
          setInputEngine(prev);
          controller.setRequestedEngine(prev);
        });
    },
    [controller, inputEngine, scopedProfile],
  );

  const voiceEngineTitle =
    "Voice engine. " +
    `browser — ${BROWSER_PRIVACY_NOTICE}. ` +
    `local — ${LOCAL_PRIVACY_NOTICE}. ` +
    "terminal — uses the local desktop audio path through the same local STT backend.";

  // Panel → composer affordance: clicking a skill name in the side panel's
  // registry summary inserts `@name` as plain composer text (exactly as if
  // typed). The turn/send path is untouched — the backend sees ordinary text.
  const insertToolMention = useCallback((name: string) => {
    const mention = `@${name} `;
    setInput((prev) =>
      prev
        ? /[\s\n]$/.test(prev)
          ? `${prev}${mention}`
          : `${prev} ${mention}`
        : mention,
    );
    requestAnimationFrame(() => composerRef.current?.focus());
  }, []);

  // Slash autocomplete (/chat parity): the popup shows while the whole
  // composer is one "/" token (`/`, `/mod`). Once the user types a space
  // (command args) or a newline, it hides and Enter sends again. Esc hides it
  // until the token changes. No gateway socket is opened for this; the static
  // `SLASH_COMMANDS` catalog (same registry the TUI completer reads) is
  // filtered client-side.
  // Slash autocomplete: the ACTIVE "/" token is the one under the caret, found
  // anywhere in the line (`slashTokenAt`), matching the /chat terminal
  // completer. The previous test anchored the token to the start of the whole
  // input, so "/" typed mid-sentence never opened the palette.
  const slashToken = slashTokenAt(input);
  const slashMatches = useMemo(
    () => (slashToken === null ? [] : filterSlashCommands(slashToken.token)),
    [slashToken],
  );
  const slashVisible =
    slashToken !== null &&
    slashToken.token !== slashDismissedFor &&
    slashMatches.length > 0;
  const slashActive = slashMatches.length > 0 ? slashIndex % slashMatches.length : 0;
  // "@" mentions get a static hint row only (no filtering): skill/MCP names
  // resolve through the agent turn, so there is nothing to complete against.
  const atHint = !slashVisible ? atTokenAt(input)?.token ?? null : null;

  // C1: live agent/todo activity for this session, shown in ChatWorkPanel.
  // Fail-soft by contract — a dead feed yields an empty roster, never an error
  // that could disturb the transcript or composer.
  const activity = useChatActivity(session);

  // Reset the highlight whenever the token changes (new filter = new list).
  useEffect(() => {
    setSlashIndex(0);
  }, [slashToken]);

  const applySlash = useCallback((item: SlashCommandItem) => {
    setInput(slashCompletionText(item));
    setSlashIndex(0);
    setSlashDismissedFor(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  }, []);

  const onSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      send(input);
    },
    [input, send],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (slashVisible) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setSlashIndex((i) => (i + 1) % slashMatches.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setSlashIndex(
            (i) => (i - 1 + slashMatches.length) % slashMatches.length,
          );
          return;
        }
        if (event.key === "Tab") {
          event.preventDefault();
          const match = slashMatches[slashActive];
          if (match) applySlash(match);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setSlashDismissedFor(slashToken.token);
          return;
        }
        if (event.key === "Enter" && !event.shiftKey) {
          const match = slashMatches[slashActive];
          // A fully-typed token falls through and sends; a partial token is
          // completed first and a second Enter sends it.
          if (match && match.name.toLowerCase() !== input.toLowerCase()) {
            event.preventDefault();
            applySlash(match);
            return;
          }
        }
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send(input);
      }
    },
    [input, send, slashVisible, slashMatches, slashActive, slashToken, applySlash],
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
    // A switch/new-chat must clear the PREVIOUS turn's busy state. The turn
    // stream only clears `busy` in its own `.finally` when its signal is NOT
    // aborted; switching session aborts that stream and structurally skips the
    // reset, leaving `busy` stuck true. The transcript then evaluates
    // `!hasMessages && !busy` as false on the fresh (empty) session and renders
    // the message-list branch with zero rows — a blank/black transcript pane
    // until a page refresh remounts the component. Clear it here and on the
    // session-change effect below so the empty state paints immediately.
    setBusy(false);
    setStreamingText("");
    setTaskProgress(null);
    setPendingConfirm(null);
    // Enter the deliberate-fresh state immediately, before the async mint and
    // its `?resume=` write land. Without this the panel keeps reading the old
    // `session` (or `DEFAULT_SESSION` -> null) and falls back to the server's
    // `is_active` row for the whole round-trip, so the previous conversation
    // stays highlighted right after pressing New chat.
    setFreshChat(true);
    api
      .createChatSession()
      .then((res) => {
        const newId = res?.session?.id;
        if (!newId) {
          // No id to point at: stay fresh rather than snapping the highlight
          // back to an unrelated live session.
          return;
        }
        // Persist first so a reload lands on the fresh session even before the
        // URL effect runs; the `?resume=` param then drives the session memo.
        storageSet(SESSION_KEY, newId);
        // Seed THIS session's model from the create response so the side panel
        // shows the fresh session's model immediately (the transcript effect
        // also reads it, but that is async — this avoids a one-frame stale
        // card).
        setSessionModel(String(res?.session?.model || ""));
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

  // One-click "follow the live voice session": point the surface at the newest
  // `voice-*` row the chat store exposed, so the live poll above starts. Uses
  // the same `?resume=`/storage path as New chat — no new backend call.
  const openVoiceSession = useCallback(
    (id: string) => {
      storageSet(SESSION_KEY, id);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("resume", id);
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

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

  // Route-to-/chat + read-and-report: the local brain reads this session's
  // canonical transcript (which includes /chat terminal turns — one shared
  // store) and reports back here. Composed client-side over the same
  // backend endpoints the transcript view uses; the summary turn itself runs
  // on the local brain through the shared chat transport like any turn.
  const summarizeSession = useCallback(() => {
    if (busy) return;
    setError(null);
    api
      .getChatSessionMessages(session, 100)
      .then((res) => {
        const rows = Array.isArray(res?.messages) ? res.messages : [];
        const lines = rows
          .map((m: { role?: string; content?: unknown; text?: unknown }) => {
            const body = String(m.content ?? m.text ?? "").trim();
            if (!body) return "";
            return `${m.role === "user" ? "USER" : "ASSISTANT"}: ${body}`;
          })
          .filter(Boolean)
          .slice(-40);
        if (!lines.length) {
          setError("[SUMMARIZE] no transcript rows to report on");
          return;
        }
        send(
          `Report on this chat session (${session}): read the transcript below and summarize what happened, what was decided, and what is still open. Transcript:\n${lines.join("\n")}`,
          "summarizing",
        );
      })
      .catch((err: unknown) => {
        setError(
          `[SUMMARIZE] ${err instanceof Error ? err.message : String(err)}`,
        );
      });
  }, [api, busy, send, session, setError]);

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

  // A35/A36 + owner fix: read a browser file to a data URL and route it by kind.
  //   image -> AGENT turn: `/api/qubit/chat/image` describes it with the
  //            canonical vision model AND submits one agent turn on this
  //            session, so the agent (with its vision tool) does the work and
  //            its reply lands in the transcript. The old path only described
  //            the image and injected the text into the composer, so the
  //            text-only local brain answered without ever seeing it.
  //   doc   -> extract through the document backend into the COMPOSER, where
  //            the operator reviews before sending (review-before-send stays).
  const submitImageToAgent = useCallback(
    (file: File) => {
      // A turn is already in flight: never start a concurrent image turn.
      if (busy) return;
      setAttachState("describing");
      setAttachError(null);
      const reader = new FileReader();
      reader.onerror = () => {
        setAttachError("file read failed");
        setAttachState("error");
      };
      reader.onload = () => {
        const dataUrl = typeof reader.result === "string" ? reader.result : "";
        if (!dataUrl) {
          setAttachError("file read failed");
          setAttachState("error");
          return;
        }
        // The composer text (if any) is the user's question about the image;
        // with no prompt a sensible default ask is used. Either way the image
        // is routed to the agent, never described into the text-only brain.
        const prompt = input.trim();
        const userText = prompt || DEFAULT_IMAGE_ASK;
        setInput("");
        setError(null);
        setRetryText(null);
        setStreamingText("");
        setTaskProgress(null);
        setPendingConfirm(null);
        setBusy(true);
        setMessages((prev) => prev.concat([{ role: "user", text: userText }]));
        // The server persists these rows too; advance the merge counter so a
        // live-voice poll does not re-append them as "new".
        if (followLiveVoice) voiceSyncedRef.current += 1;
        api
          .submitChatImage(dataUrl, session, prompt)
          .then((res) => {
            const reply = (res.reply ?? "").trim();
            if (!reply) {
              setError(
                "[VISION_NO_REPLY] the agent returned no reply for the image",
              );
              setRetryText(userText);
              return;
            }
            setMessages((prev) =>
              prev.concat([
                {
                  role: "assistant",
                  text: reply,
                  meta: res.receipt?.model
                    ? `model=${res.receipt.model}`
                    : undefined,
                },
              ]),
            );
            if (followLiveVoice) voiceSyncedRef.current += 1;
          })
          .catch((err: unknown) => {
            setError(parseTurnError(err));
            setRetryText(userText);
          })
          .finally(() => {
            setAttachState("idle");
            setBusy(false);
            setSessionListNonce((n) => n + 1);
          });
      };
      reader.readAsDataURL(file);
    },
    [busy, input, session, followLiveVoice],
  );

  // A36: documents keep the review-before-send contract — extract and hand the
  // text to the COMPOSER; nothing is auto-sent.
  const extractDocumentToComposer = useCallback((file: File) => {
    setAttachState("describing");
    setAttachError(null);
    const reader = new FileReader();
    reader.onerror = () => {
      setAttachError("file read failed");
      setAttachState("error");
    };
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      if (!dataUrl) {
        setAttachError("file read failed");
        setAttachState("error");
        return;
      }
      api
        .extractChatFile(dataUrl, file.name)
        .then((res) => {
          const text = (res.text ?? "").trim();
          if (text) setInput((prev) => (prev ? `${prev}\n${text}` : text));
          setAttachState("idle");
        })
        .catch((err: unknown) => {
          setAttachError(err instanceof Error ? err.message : String(err));
          setAttachState("error");
        });
    };
    reader.readAsDataURL(file);
  }, []);

  const describeImage = useCallback(
    (file: File | null) => {
      if (!file) return;
      if ((file.type || "").startsWith("image/")) {
        submitImageToAgent(file);
      } else {
        extractDocumentToComposer(file);
      }
    },
    [submitImageToAgent, extractDocumentToComposer],
  );

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

  // A36b: a drag-drop from the file manager hands over a PATH (not bytes), and
  // the OS path is what the operator sees. Route it to the path extractor so a
  // dropped PDF behaves like a picked one instead of being typed as text (a
  // bare path in the composer used to be sent literally and answered "Unknown
  // slash command").
  const onDropPath = useCallback((event: React.DragEvent<HTMLElement>) => {
    const dt = event.dataTransfer;
    if (!dt) return;
    // A dropped FILE (a real browser drop) wins: use its bytes directly. This
    // accepts ANY file kind, not just images — `describeImage` routes an image
    // to the vision backend and a doc (PDF/text) to the extraction backend, so
    // a dropped PDF behaves like a picked one instead of being silently ignored.
    const dropped = firstFileFromTransfer(dt);
    if (dropped) {
      event.preventDefault();
      describeImage(dropped);
      return;
    }
    const text = dt.getData("text/uri-list") || dt.getData("text/plain") || "";
    const first = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line && !line.startsWith("#"));
    if (!first) return;
    const raw = first.startsWith("file://")
      ? decodeURIComponent(first.replace(/^file:\/\//, ""))
      : first;
    event.preventDefault();
    setAttachState("describing");
    setAttachError(null);
    api
      .extractChatFilePath(raw)
      .then((res) => {
        const body = (res.text ?? "").trim();
        if (body) setInput((prev) => (prev ? `${prev}\n${body}` : body));
        setAttachState("idle");
      })
      .catch((err: unknown) => {
        setAttachError(err instanceof Error ? err.message : String(err));
        setAttachState("error");
      });
  }, [describeImage]);

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

  // ── Ctrl-K command palette: a keyboard twin of the page's own affordances.
  // It runs nothing itself — every row routes back into a handler the page
  // already owns (send, session switch, panel toggles), so there is no second
  // chat surface and no new backend route.
  const paletteItems = useMemo<PaletteItem[]>(() => {
    const rows: PaletteItem[] = [
      {
        id: "action:clear-selection",
        kind: "action",
        label: "Clear message selection",
        keywords: "deselect reset",
        disabled: selectedMessages.size === 0,
      },
      {
        id: "action:copy-selected",
        kind: "action",
        label: "Copy selected messages",
        keywords: "clipboard",
        disabled: selectedMessages.size === 0,
      },
      {
        id: "action:debug",
        kind: "action",
        label: debugOpen ? "Hide debug overlay" : "Show debug overlay",
        keywords: "logs runtime inspect",
      },
    ];
    // Slash commands the surface already resolves (same catalog the composer's
    // `/` popover uses, so the palette never invents a command).
    for (const cmd of SLASH_COMMANDS) {
      rows.push({
        id: `command:${cmd.name}`,
        kind: "command",
        label: cmd.name,
        detail: cmd.description,
        keywords: "slash",
      });
    }
    for (const m of messages) {
      if (m.role !== "assistant" || !m.text) continue;
      rows.push({
        id: `report:${m.taskKey || m.text.slice(0, 24)}`,
        kind: "report",
        label: m.text.slice(0, 80),
        detail: "Read aloud",
        keywords: "report speak voice",
      });
    }
    return rows;
  }, [debugOpen, messages, selectedMessages.size]);

  const runPaletteItem = useCallback(
    (item: PaletteItem) => {
      setPaletteOpen(false);
      if (item.kind === "action") {
        if (item.id === "action:clear-selection") {
          setSelectedMessages(new Set());
          return;
        }
        if (item.id === "action:copy-selected") {
          copySelected();
          return;
        }
        if (item.id === "action:debug") {
          setDebugOpen((v) => !v);
          return;
        }
      }
      if (item.kind === "command") {
        sendRef.current?.(item.label);
        return;
      }
      if (item.kind === "report") {
        setPaletteOpen(false);
      }
    },
    [copySelected],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The controller owns the committed text; the composer keeps what was typed
  // before the utterance. On each new final we append only the delta.
  useEffect(() => {
    controller.setRequestedEngine(inputEngine);
  }, [controller, inputEngine]);

  useEffect(() => {
    const committed = voice.committedText;
    if (committed !== lastCommittedRef.current) {
      const delta = committed.slice(lastCommittedRef.current.length);
      lastCommittedRef.current = committed;
      if (delta.trim()) {
        setInput((prev) => joinTranscript(prev, delta));
      }
    }
  }, [voice.committedText]);

  // Chrome's proven behavior is preserved: the `browser` engine still sends the
  // final utterance automatically. local/terminal finalize into the composer and
  // stay editable (no auto-send).
  useEffect(() => {
    if (voice.engine !== "browser" || voice.state !== "idle" || busy) return;
    const text = voice.committedText.trim();
    if (!text || browserSentRef.current === text) return;
    browserSentRef.current = text;
    lastCommittedRef.current = "";
    controller.setCommittedText("");
    setInput("");
    send(text);
  }, [busy, controller, send, voice.committedText, voice.engine, voice.state]);

  const startVoice = useCallback(() => {
    if (busy || controller.active) return;
    setVoiceNotice(null);
    lastCommittedRef.current = "";
    browserSentRef.current = "";
    controller.setCommittedText("");
    void controller.start(inputEngine);
  }, [busy, controller, inputEngine]);

  const stopVoice = useCallback(() => {
    void controller.stop();
  }, [controller]);

  const cancelVoice = useCallback(() => {
    void controller.cancel();
    setVoiceNotice("recognition cancelled — interim text discarded");
  }, [controller]);
  const voiceActive =
    voice.state === "starting" ||
    voice.state === "listening" ||
    voice.state === "stopping";

  const toggleVoice = useCallback(() => {
    if (voiceActive) stopVoice();
    else startVoice();
  }, [startVoice, stopVoice, voiceActive]);

  // Right-Ctrl is a first-class push-to-talk control for this same `/qubit-chat`
  // surface. Press starts the selected input engine; hold shows the live interim
  // text; release finalizes; Escape cancels the active recognition. It drives the
  // SAME transcription controller as the Voice button and the SAME canonical
  // turn path as typed input — never a private runtime/session/broker path.
  // The listener is gated on `isActive`: the ChatPage host is mounted
  // persistently by the SPA shell, so without the gate Right-Ctrl would hijack
  // the key on every other dashboard route.
  useEffect(() => {
    // When the system-wide desktop helper is live it owns Right-Ctrl, so this
    // page must not also react (double handling). It still types into this
    // composer because the helper pastes at the focused cursor.
    if (!isActive || desktopHelperLive) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === "Escape") {
        if (controller.active) cancelVoice();
        return;
      }
      if (event.code !== "ControlRight" || event.repeat) return;
      if (rightCtrlHeldRef.current) return;
      rightCtrlHeldRef.current = true;
      setRightCtrlHeld(true);
      startVoice();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "ControlRight") return;
      if (!rightCtrlHeldRef.current) return;
      rightCtrlHeldRef.current = false;
      setRightCtrlHeld(false);
      stopVoice();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [cancelVoice, controller, desktopHelperLive, isActive, startVoice, stopVoice]);

  const voiceState: "idle" | "listening" | "unsupported" | "error" =
    voice.state === "starting" || voice.state === "listening"
      ? "listening"
      : voice.state === "error"
        ? "error"
        : "idle";
  const activeEngine = voice.engine;
  const voiceError = voice.error;
  const voiceFallback = voice.fallbackReason;
  const browserSrAvailable = asSpeechRecognition() !== null;
  const localAvailable = localCaptureAvailable();
  const terminalAvailable = terminalCaptureAvailable();
  const voiceAvailable = browserSrAvailable || localAvailable || terminalAvailable;
  const rightCtrlState =
    (inputEngine === "browser" && !browserSrAvailable) ||
    (inputEngine === "local" && !localAvailable) ||
    // `terminal` falls back to local capture, so it is only unavailable when
    // there is no desktop bridge AND no local capture either.
    (inputEngine === "terminal" && !terminalAvailable && !localAvailable) ||
    !voiceAvailable
      ? "unavailable"
      : "ready";
  // The engine a press would actually use (mirrors the controller's resolver):
  // the live engine while listening, otherwise the selected engine resolved
  // against availability. Surfaced so the operator can see browser vs local STT
  // and the privacy/network consequence, not just the config value.
  const effectiveEngine: ControllerActiveEngine | null =
    activeEngine ??
    (inputEngine === "browser" && browserSrAvailable
      ? "browser"
      : inputEngine === "local" && localAvailable
        ? "local"
        : inputEngine === "terminal"
          ? terminalAvailable
            ? "terminal"
            : localAvailable
              ? "local"
              : null
          : inputEngine === "auto"
            ? browserSrAvailable
              ? "browser"
              : localAvailable
                ? "local"
                : terminalAvailable
                  ? "terminal"
                  : null
            : null);
  const voicePrivacy =
    effectiveEngine === "browser"
      ? "network: browser speech recognition may use the browser vendor's service"
      : effectiveEngine === "local"
        ? "privacy: local STT — audio is transcribed on this machine"
        : effectiveEngine === "terminal"
          ? "privacy: terminal capture — audio stays on this machine via the local desktop path"
          : "unavailable: no speech input engine";
  const rightCtrlReason =
    rightCtrlState === "unavailable"
      ? inputEngine === "browser" && !browserSrAvailable
        ? "browser speech recognition unavailable"
        : inputEngine === "local" && !localAvailable
          ? "microphone capture unavailable"
          : inputEngine === "terminal" && !terminalAvailable && !localAvailable
            ? "desktop/terminal audio bridge unavailable"
            : "no speech input engine available"
      : null;

  const hasMessages = messages.length > 0 || streamingText.length > 0;
  const activeTranscript = transcriptSession === session ? messages : [];
  const transcriptUserTurns = activeTranscript.filter(
    (message) => message.role === "user" && message.kind !== "tool",
  ).length;
  const transcriptToolRuns = activeTranscript.filter((message) => message.kind === "tool").length;
  const savedSessionProgress = loadSessionProgress(session);
  const sessionUserTurns = Math.max(savedSessionProgress.userTurns, transcriptUserTurns);
  const sessionToolRuns = Math.max(savedSessionProgress.toolRuns, transcriptToolRuns);
  const sessionLevel = Math.floor(sessionUserTurns / 5) + 1;
  const levelProgress = sessionUserTurns % 5;
  const loadMission = useCallback((prompt: string) => {
    setInput((current) => current.trim() ? `${current}\n\n${prompt}` : prompt);
  }, []);
  const modelToolsLabel = `${t.app.modelToolsSheetTitle} ${t.app.modelToolsSheetSubtitle}`;

  return (
    <div className="qchat flex min-h-0 flex-1 flex-col gap-2">
      <PluginSlot name="chat:top" />

      <div className="qchat__workspace flex min-h-0 flex-1 flex-col">
        <section
          aria-label="Qubit Chat transcript"
          className={cn(
            "qchat__conversation relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg",
            "border border-current/15 bg-background-base/60 p-2 sm:p-3",
          )}
        >
          <div className="qchat__header flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-current/10 pb-2">
            <div className="flex min-w-0 items-center gap-2">
              <Typography
                mondwest
                className="qchat__title text-sm tracking-[0.08em] text-text-secondary"
              >
                Qubit Chat
              </Typography>
              <span
                className="text-xs text-text-tertiary"
                data-testid="chat-model"
              >
                {model ? `model ${model}` : "model —"}
              </span>
              {isVoiceSession ? (
                <label
                  className="flex items-center gap-1 text-xs text-text-tertiary"
                  title="Follow this voice session live — new voice turns appear automatically"
                >
                  <input
                    type="checkbox"
                    data-testid="voice-follow-toggle"
                    checked={followVoice}
                    onChange={(event) => setFollowVoice(event.target.checked)}
                    className="h-3 w-3 accent-current"
                  />
                  follow
                </label>
              ) : null}
              {followLiveVoice ? (
                <span
                  data-testid="voice-live"
                  data-state="live"
                  role="status"
                  aria-label="Live voice session active — voice turns appear automatically"
                  title="Live voice session — new Right-Ctrl / wake turns appear here without a reload"
                  className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-xs text-midground"
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-accent"
                    aria-hidden="true"
                  />
                  live voice
                  <span data-testid="voice-live-count" className="font-mono">
                    {messages.length}
                  </span>
                </span>
              ) : null}
              {newestVoiceSession && newestVoiceSession !== session ? (
                <button
                  type="button"
                  data-testid="open-voice-session"
                  onClick={() => {
                    if (newestVoiceSession) openVoiceSession(newestVoiceSession);
                  }}
                  title={`Open the live voice session ${newestVoiceSession}`}
                  className="rounded border border-accent/40 px-1.5 py-0.5 text-xs text-midground hover:bg-midground/10"
                >
                  Open live voice session
                </button>
              ) : null}
              {isVoiceSession ? (
                <div
                  data-testid="voice-session-controls"
                  className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-text-tertiary"
                  title="Voice turns are isolated in the voice-* namespace; retention is edited in Voice settings."
                >
                  <span className="font-mono">{session}</span>
                  {voiceSessionInfo ? (
                    <span data-testid="voice-session-policy">
                      {voiceSessionInfo.mode === "per_command"
                        ? "per-command sessions"
                        : "single shared session"}
                      {" · "}
                      {formatVoiceRetention(voiceSessionInfo)}
                    </span>
                  ) : voiceSessionInfoError ? (
                    <span data-testid="voice-session-policy">
                      retention unavailable
                    </span>
                  ) : (
                    <span data-testid="voice-session-policy">
                      loading retention…
                    </span>
                  )}
                  <a
                    href="/voice-edit"
                    className="rounded px-1 py-0.5 text-midground hover:bg-midground/10"
                    aria-label="Open voice session retention settings"
                  >
                    retention settings
                  </a>
                  <span>old voice sessions prune automatically</span>
                </div>
              ) : null}
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

          {foreignSession ? (
            <div
              data-testid="foreign-session-banner"
              className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-accent/40 bg-accent/5 px-3 py-2 text-xs"
            >
              <Terminal className="h-3.5 w-3.5 shrink-0 text-midground" />
              <span className="text-text-secondary">
                <span className="font-mono">{foreignSession}</span> is a
                {" "}/chat session, not a Qubit Chat session.
              </span>
              <a
                href={`/chat?resume=${encodeURIComponent(foreignSession)}`}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-midground hover:bg-midground/10"
              >
                open in /chat
              </a>
              <button
                type="button"
                onClick={startFresh}
                className="ml-auto rounded px-1.5 py-0.5 text-text-tertiary hover:bg-midground/10 hover:text-foreground"
              >
                start a new Qubit chat
              </button>
            </div>
          ) : null}

          <div
            className="qchat__run-hud"
            role="group"
            aria-label={`Current session level ${sessionLevel}; ${sessionUserTurns} user turns; ${sessionToolRuns} tool runs`}
            title="Local session rank: every five user turns advances one level."
          >
            <div className="qchat__rank-mark" data-level={sessionLevel} key={sessionLevel} aria-hidden="true">
              <span>LVL</span>
              <strong>{String(sessionLevel).padStart(2, "0")}</strong>
            </div>
            <div className="qchat__rank-track">
              <div className="qchat__rank-copy">
                <span>SESSION RUN</span>
                <strong><span className="qchat__rank-charge">{levelProgress}/5</span> {5 - levelProgress} turns to next level</strong>
              </div>
              <div
                className="qchat__rank-meter"
                role="progressbar"
                aria-label="Progress to next local session level"
                aria-valuemin={0}
                aria-valuemax={5}
                aria-valuenow={levelProgress}
                aria-valuetext={`${levelProgress} of 5 user turns`}
              >
                <span style={{ transform: `scaleX(${levelProgress / 5})` }} />
              </div>
            </div>
            <div className="qchat__run-stat">
              <span>TOOL RUNS</span>
              <strong>{String(sessionToolRuns).padStart(2, "0")}</strong>
            </div>
            <div className="qchat__run-stat qchat__run-stat--voice">
              <span>VOICE LINK</span>
              <strong data-state={voice.connected ? "online" : "offline"}>
                {voice.connected ? "ONLINE" : "IDLE"}
              </strong>
            </div>
          </div>

          <div
            role="status"
            data-testid="voice-status"
            data-state={voiceState}
            className="qchat__voice-status mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border border-current/10 px-2 py-1 text-xs text-text-tertiary"
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
            <span data-testid="voice-engine-label">
              stt: <span className="font-mono">{voice.engineLabel || "—"}</span>
            </span>
            <span data-testid="voice-latency">
              ttfp:{" "}
              <span className="font-mono">
                {voice.metrics.timeToFirstPartialMs == null
                  ? "—"
                  : `${Math.round(voice.metrics.timeToFirstPartialMs)}ms`}
              </span>
            </span>
            <span data-testid="voice-connected">
              link:{" "}
              <span className="font-mono">
                {voice.connected ? "connected" : "—"}
              </span>
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
              Right-Ctrl:{" "}
              <span className="font-mono">
                {desktopHelperLive ? "desktop helper" : rightCtrlState}
              </span>
              {desktopHelperLive
                ? " (system-wide)"
                : rightCtrlReason
                  ? ` (${rightCtrlReason})`
                  : ""}
            </span>
            <span data-testid="voice-privacy">{voicePrivacy}</span>
            {voiceFallback ? (
              <span data-testid="voice-fallback" className="text-warning">
                fallback: {voiceFallback}
              </span>
            ) : null}
            {/* D1 Task 6: listener state bound to the existing voice-state
                source (held key + controller snapshot + config engine +
                availability reason). No new backend route. */}
            <span
              data-testid="listener-state"
              data-listening={
                desktopHelperLive || rightCtrlHeld || voiceActive
                  ? "listening"
                  : "idle"
              }
              title={
                rightCtrlReason ?? voiceFallback ?? "voice listener state"
              }
            >
              Right-Ctrl:{" "}
              <span className="font-mono">
                {desktopHelperLive
                  ? "desktop helper"
                  : rightCtrlHeld || voiceActive
                    ? "listening"
                    : "idle"}
              </span>
              {" | "}engine:{" "}
              <span className="font-mono">{inputEngine}</span>
              {rightCtrlReason ?? voiceFallback
                ? ` (${rightCtrlReason ?? voiceFallback})`
                : ""}
            </span>
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
            onScroll={(event) => {
              const el = event.currentTarget;
              followTranscriptRef.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 48;
            }}
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-busy={busy}
            className="qchat__transcript min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-3"
          >
            {transcriptState === "loading" && !hasMessages ? (
              <div
                data-state="loading"
                className="qchat__transcript-loading"
                role="status"
                aria-label="Loading transcript"
              >
                <div className="qchat__transcript-skeleton" aria-hidden="true">
                  <div className="qchat__skeleton-message qchat__skeleton-message--assistant">
                    <span /><span /><span />
                  </div>
                  <div className="qchat__skeleton-message qchat__skeleton-message--user">
                    <span /><span />
                  </div>
                  <div className="qchat__skeleton-message qchat__skeleton-message--assistant">
                    <span /><span />
                  </div>
                </div>
                <span className="sr-only">Loading transcript…</span>
              </div>
            ) : !hasMessages && !busy ? (
              <div
                data-state="empty"
                className="qchat__empty flex h-full min-h-[8rem] flex-col items-center justify-center gap-2 text-center text-sm text-text-tertiary"
              >
                <p>Start a new run.</p>
                <p>
                  Choose a mission to prepare a prompt, or write your own below.
                </p>
                <div className="qchat__qubit-probe" aria-hidden="true">
                  <span className="qchat__qubit-ket">|0⟩</span>
                  <span className="qchat__qubit-wire"><i /></span>
                  <span className="qchat__qubit-symbol">ψ</span>
                  <span className="qchat__qubit-wire"><i /></span>
                  <span className="qchat__qubit-ket">|1⟩</span>
                  <span className="qchat__qubit-equation">α|0⟩ + β|1⟩</span>
                </div>
                <div className="qchat__mission-picker" aria-label="Mission presets">
                  <h2 className="qchat__mission-caption">Choose a mission</h2>
                  <div className="qchat__mission-list">
                    <button
                      type="button"
                      className="qchat__mission-card qchat__mission-card--violet"
                      onClick={() => loadMission("Help me trace a bug. Inspect the available context, list three plausible causes, then suggest the safest next check before changing anything.")}
                      aria-label="Load the Trace a bug mission into the composer"
                    >
                      <Bug className="qchat__mission-icon" aria-hidden="true" />
                      <span>TRACE A BUG</span>
                      <span className="qchat__mission-detail">Find the cause and safest next check</span>
                      <ArrowUpRight className="qchat__mission-arrow" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="qchat__mission-card qchat__mission-card--cyan"
                      onClick={() => loadMission("Map this system for me. Explain its key components, how data moves between them, and where a new feature should be added.")}
                      aria-label="Load the Map this system mission into the composer"
                    >
                      <GitBranch className="qchat__mission-icon" aria-hidden="true" />
                      <span>MAP THE SYSTEM</span>
                      <span className="qchat__mission-detail">See the components and data flow</span>
                      <ArrowUpRight className="qchat__mission-arrow" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="qchat__mission-card qchat__mission-card--amber"
                      onClick={() => loadMission("Pressure-test this plan. Find hidden assumptions, failure modes, and the smallest experiment that would prove or disprove it.")}
                      aria-label="Load the Pressure-test a plan mission into the composer"
                    >
                      <Code className="qchat__mission-icon" aria-hidden="true" />
                      <span>PRESSURE-TEST A PLAN</span>
                      <span className="qchat__mission-detail">Expose assumptions and smallest test</span>
                      <ArrowUpRight className="qchat__mission-arrow" aria-hidden="true" />
                    </button>
                  </div>
                </div>
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
                <ul className="qchat__messages flex list-none flex-col gap-3">
                {messages.map((message, index) => (
                  <li
                    key={`${message.role}-${index}`}
                    data-message-index={index}
                    className={cn(
                      "qchat__message rounded-lg border px-3 py-2 text-sm",
                      message.kind === "tool"
                        ? "qchat__message--tool"
                        : message.role === "user"
                          ? "qchat__message--user"
                          : "qchat__message--assistant",
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
                      {message.report ? (
                        <Markdown content={message.text} />
                      ) : (
                        message.text
                      )}
                    </div>
                    {message.role === "assistant" && message.text ? (
                      <div className="mt-1 flex items-center gap-2">
                        <ReadAloudButton
                          messageId={message.taskKey || `m${index}`}
                          text={message.text}
                        />
                      </div>
                    ) : null}
                    {message.reportText ? (
                      <details
                        data-testid="message-report-full"
                        className="mt-1 text-xs"
                      >
                        <summary className="cursor-pointer text-text-tertiary hover:text-foreground">
                          Full /chat report
                        </summary>
                        <div className="mt-1 whitespace-pre-wrap break-words text-text-secondary">
                          <Markdown content={message.reportText} />
                        </div>
                      </details>
                    ) : null}
                    {message.taskKey ? (
                      <div
                        data-testid="message-task-key"
                        className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-tertiary"
                      >
                        <span>
                          task <span className="font-mono">{message.taskKey}</span>
                          {message.report ? " · Spark report" : ""}
                        </span>
                        <a
                          href={`/chat?resume=${encodeURIComponent(message.taskKey)}`}
                          aria-label={`Open /chat session ${message.taskKey}`}
                          className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-midground hover:bg-midground/10"
                        >
                          <Terminal className="h-3 w-3" />
                          open in /chat
                        </a>
                      </div>
                    ) : null}
                    {message.sources && message.sources.length > 0 ? (
                      <div
                        data-testid="message-sources"
                        className="mt-1 text-xs text-text-tertiary"
                      >
                        <span className="font-semibold">Sources:</span>{" "}
                        {message.sources.map((src, i) => (
                          <span key={`${i}-${src}`}>
                            {i > 0 ? ", " : ""}
                            {/^https?:\/\//i.test(src) ? (
                              <a
                                href={src}
                                target="_blank"
                                rel="noreferrer"
                                className="underline underline-offset-2"
                              >
                                {src}
                              </a>
                            ) : (
                              <span className="font-mono">{src}</span>
                            )}
                          </span>
                        ))}
                      </div>
                    ) : null}
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
                {taskProgress ? (
                  <li
                    data-testid="task-progress"
                    data-status={taskProgress.status}
                    className="qchat__message qchat__message--progress mr-6 rounded-lg border border-accent/40 bg-accent/5 px-3 py-2 text-sm"
                    aria-live="polite"
                  >
                    <div className="mb-1 flex items-center gap-2 text-xs font-semibold tracking-wide text-text-secondary">
                      {taskProgress.status === "working" ? (
                        <Spinner className="shrink-0" />
                      ) : null}
                      <span>
                        {taskProgress.status === "working"
                          ? "Working in /chat"
                          : taskProgress.status === "done"
                            ? "Build finished — Spark report below"
                            : taskProgress.status === "cancelled"
                              ? "Build cancelled"
                              : "Build failed"}
                      </span>
                      <span className="font-mono font-normal text-text-tertiary">
                        {taskProgress.key}
                      </span>
                    </div>
                    {taskProgress.detail ? (
                      <div className="whitespace-pre-wrap break-words text-xs text-text-tertiary">
                        {taskProgress.detail}
                      </div>
                    ) : null}
                    <a
                      href={`/chat?resume=${encodeURIComponent(taskProgress.key)}`}
                      data-testid="task-open-chat"
                      aria-label={`Open the /chat session ${taskProgress.key} in the terminal`}
                      className="mt-1 inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-xs text-midground hover:bg-midground/10"
                    >
                      <Terminal className="h-3.5 w-3.5" />
                      open in /chat
                    </a>
                  </li>
                ) : null}
                {busy && streamingText ? (
                  <li
                    data-state="streaming"
                    className="qchat__message qchat__message--assistant qchat__message--streaming mr-6 rounded-lg border border-current/15 bg-background-base/70 px-3 py-2 text-sm"
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
                    data-activity={assistantActivity}
                    className="qchat__activity"
                    aria-live="polite"
                  >
                    <span className="qchat__activity-mark" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span>
                      {assistantActivity === "summarizing"
                        ? "Summarizing this session…"
                        : "Qubit is thinking…"}
                    </span>
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
              data-testid="attach-error"
              data-state="degraded"
              className="mt-2 border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              Attach failed: {attachError}
            </div>
          ) : null}

          {pendingConfirm ? (
            <div
              role="status"
              data-testid="pending-confirmation"
              aria-live="polite"
              aria-label="Awaiting confirmation"
              className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-accent/50 bg-accent/10 px-3 py-2 text-xs text-text-primary"
            >
              <div className="min-w-0 flex-1">
                <span className="font-semibold">Awaiting confirmation</span>
                <span className="ml-1 text-text-secondary">
                  {pendingConfirm.action_summary
                    ? pendingConfirm.action_summary
                    : "a staged action needs your yes/no"}
                </span>
              </div>
              <Button
                size="sm"
                onClick={() => send("yes")}
                disabled={busy}
                aria-label="Confirm the staged action"
              >
                Confirm
              </Button>
              <Button
                size="sm"
                outlined
                onClick={() => send("no")}
                disabled={busy}
                aria-label="Cancel the staged action"
              >
                Cancel
              </Button>
            </div>
          ) : null}

          <form
            onSubmit={onSubmit}
            className="qchat__composer mt-2 flex shrink-0 flex-wrap items-end gap-2 border-t border-current/10 pt-2"
          >
            <label className="sr-only" htmlFor="qubit-chat-input">
              Message Qubit
            </label>
            <label className="flex shrink-0 items-center gap-1 text-xs text-text-tertiary">
              <span className="sr-only">Voice engine</span>
              <select
                aria-label="Voice engine"
                data-testid="voice-engine-picker"
                className="rounded border border-current/15 bg-background-base/60 px-1 py-1 text-xs text-text-secondary"
                title={voiceEngineTitle}
                value={inputEngine}
                onChange={(e) =>
                  handleEngineChange(normalizeVoiceEngine(e.target.value))
                }
              >
                <option value="auto">Voice: auto</option>
                <option value="browser">Voice: browser</option>
                <option value="local">Voice: local STT</option>
                <option value="terminal">Voice: terminal</option>
              </select>
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
            {voice.interimText ? (
              <div
                data-testid="voice-interim"
                aria-live="polite"
                title="Live interim transcription — replaced as you speak, not yet committed"
                className="min-w-full rounded-md border border-dashed border-accent/40 bg-accent/5 px-3 py-1.5 text-sm italic text-text-secondary"
              >
                {voice.interimText}
              </div>
            ) : null}
            <ChatWorkPanel
              activity={activity}
              busy={busy}
            />
            <ReportList
              messages={messages}
              onOpen={(index) => {
                setPaletteOpen(false);
                document
                  .querySelector(`[data-message-index="${index}"]`)
                  ?.scrollIntoView({ behavior: "smooth", block: "center" });
              }}
            />
            <div className="relative min-w-full flex-1 sm:min-w-0">
              {slashVisible ? (
                <div
                  role="listbox"
                  aria-label="Slash commands"
                  className="absolute bottom-full left-0 right-0 z-10 mb-2 max-h-64 overflow-y-auto rounded-md border border-border bg-popover text-sm shadow-xl"
                >
                  {slashMatches.map((match, i) => (
                    <button
                      key={match.name}
                      type="button"
                      role="option"
                      aria-selected={i === slashActive}
                      onMouseEnter={() => setSlashIndex(i)}
                      onMouseDown={(e) => {
                        // Apply before the textarea loses focus (click would
                        // blur first); the composer is refocused in applySlash.
                        e.preventDefault();
                        applySlash(match);
                      }}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1.5 text-left",
                        i === slashActive && "bg-accent/10",
                      )}
                    >
                      <span className="shrink-0 truncate font-mono text-xs">
                        {match.name}
                      </span>
                      <span className="ml-auto truncate text-xs text-text-tertiary">
                        {match.description}
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
              {!slashVisible && atHint ? (
                <div className="absolute bottom-full left-0 right-0 z-10 mb-2 rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-text-tertiary shadow-xl">
                  Mention a skill or MCP server by name — @skills and @mcp
                  resolve through the agent turn.
                </div>
              ) : null}
              <textarea
                id="qubit-chat-input"
                ref={composerRef}
                value={input}
                disabled={busy}
                rows={2}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={onPasteImage}
                onDrop={onDropPath}
                onDragOver={(event) => event.preventDefault()}
                placeholder="Message Qubit… (Enter to send, Shift+Enter for newline)"
                aria-label="Message Qubit"
                className={cn(
                  "min-h-[2.5rem] w-full resize-none rounded-md border border-current/20",
                  "bg-background-base/70 px-3 py-2 text-sm text-foreground",
                  "focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60",
                )}
              />
            </div>
            <input
              ref={attachInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,.pdf,.txt,.text,.md,.markdown,.mdown,.log,.transcript,.trans,.vtt,.srt"
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
              aria-label="Attach image, PDF or file"
              title="Attach or paste an image (routed to the agent), or a PDF/text file (extracted into the composer to review before sending)"
              prefix={<ImagePlus className="h-4 w-4" />}
            >
              {attachState === "describing" ? "Reading…" : "Attach"}
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

          <div className="qchat__session-actions mt-2 flex items-center justify-between gap-2">
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
            <Button
              ghost
              size="sm"
              onClick={summarizeSession}
              disabled={busy || messages.length === 0}
              prefix={<FileText className="h-3.5 w-3.5" />}
              aria-label="Ask the local brain to report on this session"
            >
              summarize
            </Button>
            <a
              href={`/chat?resume=${encodeURIComponent(session)}`}
              aria-label="Continue this session in the /chat terminal"
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-midground hover:bg-midground/10"
            >
              <Terminal className="h-3.5 w-3.5" />
              open in /chat
            </a>
          </div>
        </section>

        {sidePanelOpen && (
          <div
            id="chat-side-panel"
            role="complementary"
            aria-label={modelToolsLabel}
            className="qchat__side flex min-h-0 shrink-0 flex-col gap-3 overflow-hidden lg:h-full lg:w-60"
          >
            <div className="qchat__model-panel shrink-0">
              <Suspense fallback={
                <div className="qchat__model-skeleton" role="status" aria-label="Loading model controls">
                  <span /><span /><span className="qchat__skeleton-control" />
                </div>
              }>
                <ChatSidebar
                  channel={channel}
                  profile={scopedProfile}
                  onDashboardNewSessionRequest={startFresh}
                  // The session's OWN stored model, exactly the shape /chat
                  // passes (its PTY grant). Passing it makes the panel show
                  // THIS session's model instead of the global default — the
                  // "independent panels" requirement — and selects the
                  // per-session write when the operator picks a model.
                  terminalModel={
                    sessionModel ? { model: sessionModel } : null
                  }
                  sessionId={session}
                  onSessionModelChange={setSessionModel}
                />
              </Suspense>
            </div>
            <div className="qchat__tools shrink-0">
              <ChatToolsSummary
                profile={scopedProfile}
                onMention={insertToolMention}
              />
            </div>
            <div className="qchat__artifacts shrink-0">
              <ChatSessionArtifacts key={session} sessionId={session} />
            </div>
            <div className="qchat__sessions min-h-0 flex-1 overflow-hidden">
              <ChatSessionList
                key={sessionListNonce}
                // The panel lists the store this page's transcript actually
                // resumes: `GET /api/qubit/chat/sessions/<id>` (the default
                // `chat` source), the SAME ChatSessionStore the canonical
                // `/api/sessions` route reads first. The canonical route is a
                // merged SUPERSET — it appends receipt rows and, unlike the
                // chat route, does NOT filter the picker-noise rows (0-message
                // `dashboard-*` probes). Listing it put ~18% blank rows in the
                // panel (measured live: 55/300 canonical rows had 0 messages vs
                // 0/324 chat rows); clicking one resolves 200 with no messages
                // and renders the empty-state pane. `source="canonical"` was
                // wrong for a /chat host; the default store is the one whose
                // every row opens with a real transcript (upstream parity: one
                // store == the transcript store).
                activeSessionId={
                  freshChat
                    ? FRESH_CHAT_SESSION_ID
                    : session === DEFAULT_SESSION
                      ? null
                      : session
                }
                profile={scopedProfile}
                onNewChat={startFresh}
              />
            </div>
          </div>
        )}
      </div>

      <PluginSlot name="chat:bottom" />
      <CommandPalette
        open={paletteOpen}
        items={paletteItems}
        onClose={() => setPaletteOpen(false)}
        onSelect={runPaletteItem}
      />
    </div>
  );
}
