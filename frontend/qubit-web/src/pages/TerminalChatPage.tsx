/**
 * Qubit terminal chat (`/chat`) — the real Qubit terminal frontend.
 *
 * Mounts the vendored xterm terminal and streams the real vendor PTY over the
 * single Qubit WebSocket upgrade path (`/agent/api/pty`). Auth is a
 * short-lived, single-use, scoped ticket minted over the authenticated HTTP
 * route (`POST /agent/api/auth/ws-ticket`); the long-lived session token is
 * never placed on the WebSocket URL. `/qubit-chat` keeps the Qubit-native
 * ChatPage and is intentionally a separate surface.
 */

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal as XtermTerminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Mic, PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { api, withBackoff } from "@/lib/api";
import type { WsTicketGrant } from "@/lib/api";
import { WsTicketError } from "@/lib/api";
import { terminalRefusalCopy } from "@/lib/terminal-errors";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import {
  ChatSessionList,
  FRESH_CHAT_SESSION_ID,
} from "@/components/ChatSessionList";
import { copyTextToClipboard } from "@/lib/clipboard";
import { createPtyCompositionForwarder } from "@/lib/pty-composition";
import { shouldRestoreTerminalFocus } from "@/lib/pty-focus";
import { PtyResumeSanitizer } from "@/lib/pty-resume-sanitizer";
import { ptyAttachToken } from "@/lib/pty-attach-token";
import {
  PTY_CONNECTING_TIMEOUT_MS,
  PTY_RECONNECT_INPUT_MESSAGE,
  PTY_RESUME_RECONNECT_THROTTLE_MS,
  PTY_RESUME_SANITIZE_WINDOW_MS,
  shouldBlockPtyInput,
  shouldReconnectPtyOnPageResume,
  type PtyConnectionState,
} from "@/lib/pty-reconnect";
import {
  PTY_RESUME_LOADING_MAX_MS,
  PTY_RESUME_LOADING_MESSAGE,
  shouldFinishResumeHydrationOnChunk,
  shouldShowResumeLoadingOverlay,
} from "@/lib/pty-resume-loading";
import {
  MOBILE_REPLACEMENT_WINDOW_MS,
  normalizePtyMobileInput,
  shouldTreatInputAsMobileReplacement,
} from "@/lib/pty-mobile-input";
import { computeKeyboardInset, shouldPinScroll } from "@/lib/keyboard-inset";
import {
  resolvePtyKeyboardShortcut,
  sendPtyShortcutSequence,
} from "@/lib/pty-keyboard-shortcuts";
import {
  isViewportPinnedToBottom,
  parseResumeControlMessage,
  shouldFollowPtyOutput,
} from "@/lib/pty-scroll";
import { PTY_RECONNECTING_BANNER } from "@/lib/pty-close-copy";
import {
  RESUME_MISSING_TITLE,
  isResumeProbeMissing,
  resumeMissingMessage,
  resumeProbeState,
  shouldDialTerminalForResume,
  shouldShowResumeChecking,
} from "@/lib/chat-resume-guard";
import {
  TERMINAL_FONT_FAMILY,
  refitWhenTerminalFontLoads,
} from "@/lib/terminal-font-refit";
import { loseWebglContexts } from "@/lib/xterm-webgl-release";

// Lazily loaded so the informational dashboard event SSE stream (opened by
// ChatSidebar) is code-split out of the base chunk — same pattern as
// `pages/ChatPage.tsx`.
const ChatSidebar = lazy(() =>
  import("@/components/ChatSidebar").then((m) => ({ default: m.ChatSidebar })));

// ESC byte built explicitly: a "[RESIZE:c;r]" frame the PTY bridge
// intercepts server-side (TIOCSWINSZ) and never passes to the child.
const RESIZE = (cols: number, rows: number) =>
  `${String.fromCharCode(27)}[RESIZE:${cols};${rows}]`;

/**
 * Stable per-mount channel id for the right-side panel's event feed. It only
 * needs to be unique to this dashboard tab so sibling tabs never share a feed
 * (mirrors `generateChatChannel` in `pages/ChatPage.tsx`).
 */
function generateTerminalChatChannel(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `qubit-terminal-${crypto.randomUUID()}`;
  }
  return `qubit-terminal-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

/**
 * CSS width for the WebGL-vs-canvas renderer decision.
 *
 * Prefer the terminal host's `clientWidth` — Chrome DevTools device mode often
 * keeps `window.innerWidth` at the full desktop value while the drawn layout
 * is phone-sized. (Ported from the reference ChatPage; page-local glue, not a
 * lib helper.)
 */
function terminalTierWidthPx(host: HTMLElement | null): number {
  if (typeof window === "undefined") return 1280;
  const fromHost = host?.clientWidth ?? 0;
  if (fromHost > 2) return Math.round(fromHost);
  const doc =
    typeof document !== "undefined"
      ? (document.documentElement?.clientWidth ?? 0)
      : 0;
  const vv = window.visualViewport;
  const inner = window.innerWidth;
  const vvw = vv?.width ?? inner;
  const layout = Math.min(inner, vvw, doc > 0 ? doc : inner);
  return Math.max(1, Math.round(layout));
}

// Socket-close reconnect ladder. Our `pty-reconnect` lib has no delay ladder
// (upstream's `ptyReconnectDelayMs`/`PTY_RECONNECT_MAX_ATTEMPTS` were not
// ported), so the backoff lives here as page-local glue: short base, tight
// cap, unbounded attempts — matching this page's existing outer-retry spirit
// (a dead backend re-attaches by itself; no gave-up overlay on `/chat`).
const PTY_SOCKET_RECONNECT_BASE_MS = 250;
const PTY_SOCKET_RECONNECT_MAX_MS = 3000;
function ptySocketReconnectDelayMs(attempt: number): number {
  return Math.min(
    PTY_SOCKET_RECONNECT_MAX_MS,
    PTY_SOCKET_RECONNECT_BASE_MS * 2 ** Math.max(0, attempt - 1),
  );
}

// Application-level keepalive: browsers cannot send WS ping frames, so a
// RESIZE control frame (consumed by the PTY bridge, never reaching the child)
// keeps quiet terminals alive behind idle-closing proxies. Same 20 s budget
// as the reference; page-local because the lib has no keepalive constant.
const PTY_KEEPALIVE_INTERVAL_MS = 20_000;

function terminalWsUrl(
  ticket: string,
  cols: number,
  rows: number,
  resume?: string,
  cwd?: string,
  attach?: string,
  fresh?: boolean,
): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = new URLSearchParams({
    ticket,
    cols: String(cols),
    rows: String(rows),
  });
  // The PTY child resumes this canonical session id (`QUBIT_TUI_RESUME`), so
  // picking a session in the right-hand list reopens its transcript instead of
  // spawning a fresh one. The server validates/ensures the id against the one
  // canonical store (`terminal_bridge.canonical_terminal_session`).
  if (resume) qs.set("resume", resume);
  // Forced-fresh intent (New chat / "Start new session"): tells the server to
  // ignore any active-session fallback and force an empty resume, so a fresh
  // press can NEVER re-adopt a detached terminal (upstream parity:
  // chat_ws.py turns `fresh=1` into resume=None + unlink). Sent only for an
  // explicit New chat, never on a plain reload/reconnect.
  if (fresh) qs.set("fresh", "1");
  // Workspace for a FRESH chat (the server ignores it on resume, which keeps
  // its own cwd). Same wire the reference uses: /api/pty?cwd=.
  if (cwd) qs.set("cwd", cwd);
  // Stable per-tab PTY identity. The server's PtySessionRegistry reuses the
  // live terminal for a repeated `attach` token instead of forking a new one,
  // and replays buffered output — so a page reload re-attaches to the running
  // turn instead of killing it. Without this the socket takes the legacy
  // 1:1 path that terminates the PTY on disconnect.
  if (attach) qs.set("attach", attach);
  return `${proto}//${window.location.host}/agent/api/pty?${qs.toString()}`;
}

type TerminalPtyState = "connecting" | "open" | "reconnecting" | "closed" | "error";

/** `error` is a local UI flavor; the lib predicates only know lib states. */
function toLibPtyState(state: TerminalPtyState): PtyConnectionState {
  return state === "error" ? "closed" : state;
}

export default function TerminalChatPage({ isActive = true }: { isActive?: boolean }) {
  const { t } = useI18n();
  const { profile: scopedProfile } = useProfileScope();
  // `?resume=<id>` is the terminal's session target. `ChatSessionList` sets it
  // when a row is picked (and clears it for "New chat"); the effect below
  // reconnects the PTY so the vendored TUI reopens that transcript. The ref
  // seeds the first connect without double-spawning on mount.
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume") || "";
  const resumeRef = useRef(resumeParam);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XtermTerminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const disposedRef = useRef(false);
  const isActiveRef = useRef(isActive);
  useEffect(() => {
    isActiveRef.current = isActive;
  }, [isActive]);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Outer retry for an EXHAUSTED ticket ladder. `withBackoff` gives up after
  // ~7s of a down backend and a failed `connect()` can leave no socket at all,
  // so `ws.onclose` never re-arms and the page wedges until a reload. This
  // bounded cross-ladder delay keeps re-attempting so a backend that returns
  // later re-attaches by itself. Reset whenever a socket opens.
  const outerAttemptRef = useRef(0);
  const [state, setState] = useState<TerminalPtyState>("connecting");
  // Lib-shaped mirror of the connection state for the `pty-reconnect`
  // predicates (`shouldBlockPtyInput`, `shouldReconnectPtyOnPageResume`).
  const ptyStateRef = useRef<PtyConnectionState>("connecting");
  const setPtyState = (next: TerminalPtyState) => {
    ptyStateRef.current = toLibPtyState(next);
    setState(next);
  };
  const [notice, setNotice] = useState<string | null>(null);
  const [grant, setGrant] = useState<WsTicketGrant | null>(null);
  // Resume-replay guards, one set per connection (see `connect`).
  const sanitizerRef = useRef<PtyResumeSanitizer | null>(null);
  const decoderRef = useRef<TextDecoder | null>(null);
  const eraseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resumeMaxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keepaliveRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Socket-close ladder attempt (page-local backoff); the ticket-phase outer
  // retry above is separate and untouched.
  const reconnectAttemptRef = useRef(0);
  // True while the async ticket fetch hasn't resolved into a socket yet, so a
  // page-resume event in that gap doesn't fire a redundant reconnect.
  const connectInFlightRef = useRef(false);
  const lastResumeReconnectRef = useRef(0);
  // Resume-replay viewport pin (`pty-scroll`): pinned while a resumed session
  // replays, released the moment the user scrolls up to read the backlog.
  const stickToBottomRef = useRef(true);
  // This connection's resume target: `?resume=` when known up front, else the
  // server's one-off JSON control frame (the implicit active-session fallback).
  const effectiveResumeRef = useRef<string | null>(null);
  const [resumeTarget, setResumeTarget] = useState<string | null>(null);
  // True while the surface sits on a DELIBERATE fresh chat (New chat pressed,
  // nothing resumed). Drives the session panel's `activeSessionId` so it renders
  // no highlight instead of falling back to an unrelated `is_active` row. Reset
  // the moment a real session id is resolved (a row pick, a URL resume, or the
  // server's active-session control frame).
  const [freshChat, setFreshChat] = useState(false);
  const [hydrating, setHydrating] = useState(false);
  // `/chat?resume=<id>` existence guard. The server's resume path ENSURES an
  // explicit id (creating a phantom session for a bogus one) and the terminal
  // then paints an empty canvas. Verify the id against the read-only session
  // probe first: `verifiedId` = the id we proved exists, `missingId` = the id
  // we proved does NOT. The page must not dial the PTY for a missing id.
  const [resumeVerifiedId, setResumeVerifiedId] = useState<string | null>(null);
  const [resumeMissingId, setResumeMissingId] = useState<string | null>(null);
  // Mirror of the resume probe for `connect()` (a stable callback that must not
  // re-create on every probe transition). `connect` refuses to dial while a
  // resume id is still being verified or is known-missing.
  const resumeDialAllowedRef = useRef(true);
  // Previous render's dial-gate value, so the effect below can detect a
  // blocked→allowed transition and make the one deferred connect attempt.
  const prevDialAllowedRef = useRef(true);
  // Mobile IME tracking (`pty-mobile-input`) + composition fallback.
  const ptyInputLineRef = useRef("");
  const mobileReplacementUntilRef = useRef(0);
  const blockedInputNoticeRef = useRef(false);
  const compositionRef = useRef<ReturnType<
    typeof createPtyCompositionForwarder
  > | null>(null);
  // The profile the terminal last resolved (from the ticket grant); used when
  // minting the next ticket and in refusal copy. Not rendered as a picker —
  // model switching lives in the MODEL card (`/api/model/set`), as in the reference.
  const selectedRef = useRef<string | null>(null);
  // Stable per-tab PTY attach token: the server reuses the live terminal for a
  // repeated token, so a reload re-attaches to the running turn (unattended
  // tasks keep going) instead of forking a fresh, empty one. Rotated only for a
  // deliberately NEW chat, which must not inherit the previous terminal.
  //
  // The token MUST outlive a page reload: a `useRef` is reset on reload, so
  // deriving it from `generateTerminalChatChannel()` (a fresh random id every
  // mount) made every reload present a NEW key. The registry then had no entry
  // for it and forked another PTY, orphaning the previous one — which nothing
  // reaps (its socket never cleanly detached), so reloads accumulated runaway
  // PTY/agent pairs until one of them squatted the resume session with "open in
  // another Qubit window". `ptyAttachToken()` persists the id in sessionStorage
  // and claims it with a Web Lock so a reload of THIS tab reuses it while a
  // second live tab mints its own (see lib/pty-attach-token.ts).
  //
  // Await the shared helper before dialing: it owns both persistence and
  // duplicate-tab lock claims, so the wire identity cannot diverge from disk.
  const attachRef = useRef<string>("");
  // Rotate (New chat) MUST persist the new token, not just mutate the ref: a
  // reload re-reads `sessionStorage` and, with a stale token, the server's
  // step-1b `_reattach(staleKey)` re-adopts the still-live DETACHED terminal —
  // so New chat silently reverted to the old chat on the next reload and the
  // operator had to press it several times. `ptyAttachToken(rotate=true)` mints
  // a token, WRITES it to sessionStorage and claims its Web Lock; we keep that
  // exact token as the wire value so a reload re-attaches to the terminal New
  // chat actually spawned. Resolved asynchronously (`connect` awaits it) so the
  // mint and the wire value are the helper's single token.
  const attachToken = useCallback(async (rotate = false): Promise<string> => {
    // The persisted, lock-claimed token must be the exact wire identity. A
    // speculative synchronous id diverges from storage on first mount and
    // bypasses duplicate-tab ownership checks on reload.
    const token = await ptyAttachToken(rotate);
    attachRef.current = token;
    return token;
  }, []);
  const connectionGenerationRef = useRef(0);
  // Right-side panel (model control + status + sessions), collapsible and open
  // by default, matching the Qubit `/qubit-chat` ChatPage panel.
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  // Bumped after a New chat so `ChatSessionList` remounts and refetches,
  // surfacing the freshly created session once the TUI registers it (its
  // internal fetch only runs on mount / manual Refresh).
  const [sessionListNonce, setSessionListNonce] = useState(0);
  // Picked workspace for a FRESH chat (absolute host path; "" = server
  // default). Sent on the PTY WS as ?cwd= and only affects fresh chats.
  const [workspaceCwd, setWorkspaceCwd] = useState("");
  // Read inside the connect closure without re-creating it on every change.
  const workspaceRef = useRef("");
  useEffect(() => {
    workspaceRef.current = workspaceCwd;
  }, [workspaceCwd]);
  const [channel] = useState<string>(generateTerminalChatChannel);
  // Stable indirection for the self-scheduled reconnect: `connect` cannot
  // reference itself inside its own initializer, so it schedules the latest
  // callback through this ref instead (assigned after each render).
  const connectRef = useRef<(profileId?: string, forceFresh?: boolean) => void>(() => {});
  // "New chat" clears the resume param and spawns a genuinely fresh PTY
  // (upstream semantics): with no `?resume=` the server emits no resume env,
  // so the vendored TUI forges a brand-new session instead of resuming an
  // old one. Nothing is minted up front — the new session appears once the
  // TUI creates it. The ref is seeded BEFORE the URL update so the resume
  // effect no-ops and exactly one reconnect happens (no double-spawn).
  // A NEW chat must not inherit the previous terminal, so `connect` rotates
  // the attach token AND persists it (attachToken(true) writes sessionStorage)
  // and sends `?fresh=1` so the server can never re-adopt the old detached
  // PTY on a later reload (upstream parity: chat_ws.py `fresh` handling).
  const handleNewChat = useCallback(() => {
    setNotice(null);
    resumeRef.current = "";
    // Mark the surface as a DELIBERATE fresh chat so the session panel stops
    // highlighting the previously-resumed row. Clearing `?resume=` alone made
    // `activeSessionId` null, which the panel reads as "no selection known yet"
    // and answers with the server's `is_active` fallback — lighting an
    // unrelated live session (subagents and the PTY are often all `is_active`),
    // so "New chat" looked like it kept the old session selected.
    setFreshChat(true);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("resume");
        return next;
      },
      { replace: false },
    );
    connectRef.current(undefined, true);
    setSessionListNonce((n) => n + 1);
  }, [setSearchParams]);

  const clearPerConnectionTimers = useCallback(() => {
    if (connectingTimerRef.current) {
      clearTimeout(connectingTimerRef.current);
      connectingTimerRef.current = null;
    }
    if (keepaliveRef.current) {
      clearInterval(keepaliveRef.current);
      keepaliveRef.current = null;
    }
    if (eraseTimerRef.current) {
      clearTimeout(eraseTimerRef.current);
      eraseTimerRef.current = null;
    }
    if (resumeMaxTimerRef.current) {
      clearTimeout(resumeMaxTimerRef.current);
      resumeMaxTimerRef.current = null;
    }
  }, []);

  /** Arm the resume-replay guards (erase suppression + loading cap). */
  const beginResumeReplay = useCallback(
    (sanitizer: PtyResumeSanitizer) => {
      stickToBottomRef.current = true;
      if (!eraseTimerRef.current) {
        eraseTimerRef.current = setTimeout(() => {
          eraseTimerRef.current = null;
          sanitizer.endEraseSuppression();
        }, PTY_RESUME_SANITIZE_WINDOW_MS);
      }
      if (!resumeMaxTimerRef.current) {
        setHydrating(true);
        resumeMaxTimerRef.current = setTimeout(() => {
          resumeMaxTimerRef.current = null;
          if (!disposedRef.current) setHydrating(false);
        }, PTY_RESUME_LOADING_MAX_MS);
      }
    },
    [],
  );

  const finishResumeHydration = useCallback(() => {
    if (resumeMaxTimerRef.current) {
      clearTimeout(resumeMaxTimerRef.current);
      resumeMaxTimerRef.current = null;
    }
    if (!disposedRef.current) setHydrating(false);
  }, []);

  const connect = useCallback((profileId?: string, forceFresh = false) => {
    const term = termRef.current;
    if (!term || disposedRef.current) return;
    // Do not dial while a `?resume=<id>` is unverified or known-missing: the
    // server would ENS(`ensure`) a bogus id into a phantom session and paint an
    // empty canvas. An explicit New chat (`forceFresh`) is never gated — it
    // clears the resume and legitimately starts fresh.
    if (!forceFresh && !resumeDialAllowedRef.current) return;
    const generation = ++connectionGenerationRef.current;
    const current = () => !disposedRef.current && connectionGenerationRef.current === generation;
    const resume = forceFresh ? "" : resumeRef.current;
    // Tear down any previous terminal BEFORE opening a new one. Without this
    // a Reconnect / profile switch / New chat opened a SECOND PTY while the
    // first stayed alive: the server caps concurrent PTYs and enforces one
    // live owner per session, so the operator hit PTY_LIMIT or "session
    // already has a live owner" on the next turn. Detaching the handlers
    // first stops the old socket's onclose from scheduling its own reconnect.
    const prev = wsRef.current;
    if (prev) {
      prev.onopen = null;
      prev.onmessage = null;
      prev.onerror = null;
      prev.onclose = null;
      try {
        prev.close();
      } catch {
        /* already closed */
      }
      wsRef.current = null;
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    clearPerConnectionTimers();
    const picked = profileId ?? selectedRef.current ?? undefined;
    setPtyState("connecting");
    setNotice(null);
    // Fresh sanitizer + streaming decoder per connection: the sanitizer holds
    // cross-frame trailing state (partial escapes, newline runs) that must not
    // leak between sockets.
    const sanitizer = new PtyResumeSanitizer();
    sanitizerRef.current = sanitizer;
    decoderRef.current = new TextDecoder();
    effectiveResumeRef.current = resumeRef.current || null;
    setResumeTarget(resumeRef.current || null);
    if (effectiveResumeRef.current) {
      beginResumeReplay(sanitizer);
    } else {
      setHydrating(false);
    }
    // A connect is now in flight (ticket fetch hasn't resolved into a socket
    // yet) so a page-resume event in that gap doesn't double-connect.
    connectInFlightRef.current = true;
    const cols = term.cols || 80;
    const rows = term.rows || 24;
    // Ride out a backend restart: the ticket route is the first thing that
    // fails (`[WS_TICKET] NetworkError`) when `voicebridge run` bounces, and a
    // single failed fetch used to leave the terminal disconnected until reload.
    // Thread the resume target so the grant reports the session's STORED
    // model (server overlays `sessions.model`); without it the MODEL card
    // showed the config default while the terminal ran the session's own
    // model (2026-10-05).
    withBackoff(() => api.wsTicket(picked, undefined, resume || undefined), {
      shouldStop: () => !current(),
    })
      .then(async (ticket) => {
        if (!current()) return;
        setGrant(ticket);
        if (ticket.profile_id) {
          selectedRef.current = ticket.profile_id;
        }
        // Resolve the attach token (async for a rotated/fresh one, which
        // persists + claims before we open the socket) so a reload re-attaches
        // to the terminal THIS connect spawns.
        const attach = await attachToken(forceFresh);
        if (!current()) return;
        const ws = new WebSocket(
          terminalWsUrl(
            ticket.ticket,
            cols,
            rows,
            resume,
            workspaceRef.current,
            attach,
            forceFresh,
          ),
        );
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;
        // A mobile socket can wedge in CONNECTING after a radio handoff and
        // never fire onclose, so neither the resume predicate nor the ladder
        // can recover it. Force-close past the lib budget; the resulting
        // onclose routes into the ordinary backoff.
        connectingTimerRef.current = setTimeout(() => {
          connectingTimerRef.current = null;
          if (wsRef.current === ws && ws.readyState === WebSocket.CONNECTING) {
            try {
              ws.close();
            } catch {
              /* already tearing down */
            }
          }
        }, PTY_CONNECTING_TIMEOUT_MS);
        ws.onopen = () => {
          if (connectingTimerRef.current) {
            clearTimeout(connectingTimerRef.current);
            connectingTimerRef.current = null;
          }
          connectInFlightRef.current = false;
          // A live socket means the backend is back: reset both ladders so a
          // later drop starts its backoff from the base again.
          reconnectAttemptRef.current = 0;
          setPtyState("open");
          // A connect/reconnect is activity: remount the session list so it
          // refetches and re-sorts the active (resumed) session to the top.
          // The list also self-refreshes on focus/visibility/interval; this
          // page adds no polling of its own.
          setSessionListNonce((n) => n + 1);
          setNotice(null);
          blockedInputNoticeRef.current = false;
          // A live socket means the backend is back: reset the outer-retry
          // ladder so a later drop starts its backoff from the base again.
          outerAttemptRef.current = 0;
          fitRef.current?.fit();
          // Send the initial RESIZE immediately so the TUI has a size to lay
          // out against on its first paint.
          try {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(RESIZE(term.cols || 80, term.rows || 24));
            }
          } catch {
            /* socket already gone; onclose recovers */
          }
          if (keepaliveRef.current) clearInterval(keepaliveRef.current);
          keepaliveRef.current = setInterval(() => {
            const sock = wsRef.current;
            const live = termRef.current;
            if (sock && sock.readyState === WebSocket.OPEN && live) {
              try {
                sock.send(RESIZE(live.cols, live.rows));
              } catch {
                /* socket already gone; onclose recovers */
              }
            }
          }, PTY_KEEPALIVE_INTERVAL_MS);
          // Resumed sessions replay scrollback over the socket. Start pinned
          // to the bottom so the latest output is in view; released once the
          // user scrolls up.
          if (effectiveResumeRef.current) stickToBottomRef.current = true;
          if (
            shouldRestoreTerminalFocus(
              typeof document !== "undefined" ? document.activeElement : null,
              hostRef.current,
            )
          ) {
            term.focus();
          }
        };
        ws.onmessage = (ev: MessageEvent) => {
          if (typeof ev.data === "string") {
            // The implicit active-session fallback (no `?resume=` on the URL)
            // announces itself via a one-off JSON control frame before any
            // PTY replay bytes arrive. Real PTY output always arrives as
            // binary frames, so any other text frame (e.g. the ANSI "chat
            // unavailable" banners) falls through to the write path below
            // unchanged.
            const resumeId = parseResumeControlMessage(ev.data);
            if (resumeId) {
              effectiveResumeRef.current = resumeId;
              setResumeTarget(resumeId);
              // The server told us which session this connection is on: we are
              // no longer on a fresh chat, so let the panel highlight it.
              setFreshChat(false);
              const active = sanitizerRef.current;
              if (active) beginResumeReplay(active);
              return;
            }
          }
          const decoder = decoderRef.current ?? new TextDecoder();
          const text =
            typeof ev.data === "string"
              ? ev.data
              : decoder.decode(new Uint8Array(ev.data as ArrayBuffer), {
                  stream: true,
                });
          const effectiveResume = effectiveResumeRef.current;
          // Gate hydration on the payload actually written to xterm. The
          // sanitizer can turn a nonempty erase-only / all-newline /
          // partial-CSI resume frame into "" — keying off raw `text` would
          // hide the wait notice while the terminal is still blank.
          const rendered = effectiveResume
            ? (sanitizerRef.current?.next(text) ?? text)
            : text;
          // Pin the viewport to the bottom as each replayed chunk COMMITS
          // (xterm write callback) instead of guessing with a fixed delay.
          const followScroll = shouldFollowPtyOutput(
            effectiveResume,
            stickToBottomRef.current,
          )
            ? () => termRef.current?.scrollToBottom()
            : undefined;
          term.write(rendered, followScroll);
          if (
            effectiveResume &&
            shouldFinishResumeHydrationOnChunk(rendered)
          ) {
            finishResumeHydration();
          }
        };
        ws.onerror = () => setPtyState("error");
        ws.onclose = () => {
          wsRef.current = null;
          connectInFlightRef.current = false;
          if (connectingTimerRef.current) {
            clearTimeout(connectingTimerRef.current);
            connectingTimerRef.current = null;
          }
          if (keepaliveRef.current) {
            clearInterval(keepaliveRef.current);
            keepaliveRef.current = null;
          }
          // Drain buffered sanitizer state. A buffered partial escape is
          // dropped (an unterminated CSI would wedge xterm's parser); a
          // buffered newline run is emitted collapsed.
          if (effectiveResumeRef.current) {
            if (eraseTimerRef.current) {
              clearTimeout(eraseTimerRef.current);
              eraseTimerRef.current = null;
            }
            const drained = sanitizerRef.current;
            if (drained) {
              try {
                term.write(drained.flush());
              } catch {
                /* terminal going away */
              }
            }
          }
          if (resumeMaxTimerRef.current) {
            clearTimeout(resumeMaxTimerRef.current);
            resumeMaxTimerRef.current = null;
          }
          setHydrating(false);
          if (disposedRef.current) return;
          // Transient transport drop: reconnect on the socket-close ladder
          // with a FRESH single-use ticket. Unbounded attempts (this page's
          // standing contract — a backend that returns later re-attaches
          // without a reload); the ticket-phase outer retry below is separate.
          const attempt = reconnectAttemptRef.current + 1;
          reconnectAttemptRef.current = attempt;
          setPtyState("reconnecting");
          setNotice(PTY_RECONNECTING_BANNER);
          reconnectTimerRef.current = setTimeout(
            () => {
              reconnectTimerRef.current = null;
              connectRef.current();
            },
            ptySocketReconnectDelayMs(attempt),
          );
        };
      })
      .catch((err: unknown) => {
        if (!current()) return;
        connectInFlightRef.current = false;
        setPtyState("error");
        if (err instanceof WsTicketError) {
          // Typed /chat refusal (context floor, unverified window, policy):
          // actionable copy naming the profile and the remedy, never a bare
          // code. The picker above stays usable so the operator can switch.
          setNotice(
            terminalRefusalCopy(
              err.code,
              err.detail,
              selectedRef.current ?? undefined,
            ),
          );
        } else {
          setNotice(
            `[WS_TICKET] ${err instanceof Error ? err.message : String(err)}`,
          );
        }
        // The ladder is exhausted (or a typed refusal arrived): no socket
        // exists, so `ws.onclose` can never re-arm. Keep trying on a bounded,
        // backed-off schedule so a backend that returns later re-attaches
        // WITHOUT a reload. Cancel-safe: the timer lives in `reconnectTimerRef`
        // (cleared on unmount and at the top of the next `connect()`), and
        // `disposedRef` short-circuits after unmount.
        if (!disposedRef.current) {
          if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
          const attempt = outerAttemptRef.current;
          outerAttemptRef.current = Math.min(attempt + 1, 20);
          const delay = Math.min(30000, 2000 * 2 ** Math.min(attempt, 4));
          reconnectTimerRef.current = setTimeout(() => {
            reconnectTimerRef.current = null;
            connectRef.current();
          }, delay);
        }
      });
  }, [attachToken, beginResumeReplay, clearPerConnectionTimers, finishResumeHydration]);

  useEffect(() => {
    connectRef.current = (profileId, forceFresh) => connect(profileId, forceFresh);
  }, [connect]);

  // Verify a `?resume=<id>` against the read-only session probe BEFORE the
  // terminal dials. Without this the server's `canonical_terminal_session`
  // ENSURES any explicit id — creating a phantom session and painting an empty
  // canvas for a bogus/deleted/foreign id — which is the reported "blank /chat
  // until I click a session" symptom. A 404 marks the id missing so
  // `shouldDialTerminalForResume` blocks the PTY and the not-found state shows;
  // any other failure is a real load error and leaves the id unverified (we do
  // NOT invent a "missing" verdict from a network fault).
  useEffect(() => {
    const id = resumeParam.trim();
    if (!id) {
      setResumeVerifiedId(null);
      setResumeMissingId(null);
      return;
    }
    if (resumeVerifiedId === id || resumeMissingId === id) return;
    let cancelled = false;
    withBackoff(() => api.getSessionDetail(id), {
      shouldStop: () => cancelled || disposedRef.current,
    })
      .then(() => {
        if (cancelled || disposedRef.current) return;
        setResumeVerifiedId(id);
        setResumeMissingId(null);
      })
      .catch((err: unknown) => {
        if (cancelled || disposedRef.current) return;
        if (isResumeProbeMissing(err)) {
          setResumeMissingId(id);
          setResumeVerifiedId(null);
        } else {
          // A real probe failure: do not block the terminal on it. Leave the
          // id unverified-but-dialable so a flaky backend still reaches the
          // PTY's own error surface rather than a false "not found".
          setResumeVerifiedId(id);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resumeParam, resumeVerifiedId, resumeMissingId]);

  const resumeProbe = resumeProbeState({
    resumeId: resumeParam,
    verifiedId: resumeVerifiedId,
    missingId: resumeMissingId,
  });
  // Update the dial gate DURING RENDER, not in an effect: the xterm mount
  // effect (declared later) calls `connect()` before any gate-sync effect would
  // have run, so an effect-set ref would be stale (still `true`) on first mount
  // and the terminal would dial an unverified/missing id.
  resumeDialAllowedRef.current = shouldDialTerminalForResume(resumeProbe);

  // Keep `connect`'s gate in sync, and dial the terminal as soon as a pending
  // resume id is verified. The mount-time connect (inside the xterm effect)
  // fires before the probe resolves, so it is refused by the gate and this
  // effect makes the one real attempt once the id is proven to exist.
  useEffect(() => {
    const allowed = shouldDialTerminalForResume(resumeProbe);
    const wasBlocked = !prevDialAllowedRef.current;
    prevDialAllowedRef.current = allowed;
    if (allowed && wasBlocked) {
      connectRef.current();
    }
  }, [resumeProbe]);

  // Reconnect when the resume target changes (a session row was picked, or
  // "New chat" cleared it). The first render is a no-op: the ref was seeded
  // with the same value the mount-time connect already used.
  useEffect(() => {
    if (resumeRef.current === resumeParam) return;
    resumeRef.current = resumeParam;
    // A real `?resume=` id means we are on a stored session again, not a fresh
    // one; clearing it via the URL (back button) leaves the deliberate-fresh
    // flag untouched so New chat's intent survives a client-side nav.
    if (resumeParam) setFreshChat(false);
    connectRef.current();
  }, [resumeParam]);

  // Reconnect when the page resumes (tab return, sleep/wake, signal regain)
  // if the socket is down. A burst of visibilitychange/pageshow/focus/online
  // on tab-return collapses into a single attempt via the lib throttle.
  useEffect(() => {
    const maybeResumeReconnect = () => {
      const now = Date.now();
      if (now - lastResumeReconnectRef.current < PTY_RESUME_RECONNECT_THROTTLE_MS) {
        return;
      }
      const should = shouldReconnectPtyOnPageResume({
        isActive: isActiveRef.current,
        visibilityState:
          typeof document !== "undefined" ? document.visibilityState : undefined,
        online: typeof navigator === "undefined" ? true : navigator.onLine,
        socketReadyState: wsRef.current ? wsRef.current.readyState : null,
        ptyState: ptyStateRef.current,
        connectInFlight: connectInFlightRef.current,
      });
      if (!should) return;
      lastResumeReconnectRef.current = now;
      connectRef.current();
    };
    const onVisibility = () => maybeResumeReconnect();
    const onFocus = () => maybeResumeReconnect();
    const onOnline = () => maybeResumeReconnect();
    const onPageShow = () => maybeResumeReconnect();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    window.addEventListener("online", onOnline);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, []);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    disposedRef.current = false;
    const term = new XtermTerminal({
      convertEol: false,
      cursorBlink: true,
      fontFamily: TERMINAL_FONT_FAMILY,
      fontSize: 13,
      scrollback: 5000,
      theme: { background: "transparent", foreground: "inherit" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    try {
      term.loadAddon(new Unicode11Addon());
      term.unicode.activeVersion = "11";
    } catch {
      /* addon unavailable in some builds */
    }
    // WebGL renderer with graceful canvas fallback. Narrow hosts (phones,
    // device emulation) keep the canvas renderer whose cells track `fontSize`
    // faithfully; wide layouts get WebGL for crisp box-drawing. A lost
    // context disposes its addon so the terminal falls back instead of
    // blanking.
    if (terminalTierWidthPx(el) >= 768) {
      try {
        const webgl = new WebglAddon();
        webgl.onContextLoss(() => {
          try {
            webgl.dispose();
          } catch {
            /* already disposed */
          }
        });
        term.loadAddon(webgl);
      } catch (err) {
        console.warn(
          "[qubit-chat] WebGL renderer unavailable; falling back to canvas",
          err,
        );
      }
    }
    term.open(el);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    // Keystrokes → PTY, gated while disconnected so typing into a dead
    // terminal prints a one-shot "reconnecting" notice instead of vanishing.
    const forwardPtyData = (data: string, useMobileReplacement = true) => {
      const ws = wsRef.current;
      if (
        !ws ||
        ws.readyState !== WebSocket.OPEN ||
        shouldBlockPtyInput(ptyStateRef.current)
      ) {
        if (!blockedInputNoticeRef.current) {
          blockedInputNoticeRef.current = true;
          try {
            term.write(
              `\\r\\n\\x1b[33m[${PTY_RECONNECT_INPUT_MESSAGE}]\\x1b[0m\\r\\n`,
            );
          } catch {
            /* terminal going away */
          }
        }
        return;
      }
      const normalized = normalizePtyMobileInput(
        data,
        ptyInputLineRef.current,
        useMobileReplacement && Date.now() <= mobileReplacementUntilRef.current,
      );
      ptyInputLineRef.current = normalized.nextLine;
      if (normalized.normalized) {
        mobileReplacementUntilRef.current = 0;
      }
      try {
        ws.send(normalized.data);
      } catch {
        /* socket already gone; onclose recovers */
      }
    };

    // IME/dead-key fallback: xterm occasionally emits no onData for a
    // composition commit; compositionend carries the authoritative text.
    const compositionForwarder = createPtyCompositionForwarder((data) => {
      forwardPtyData(data, false);
    });
    compositionRef.current = compositionForwarder;

    const dataSub = term.onData((data) => {
      compositionForwarder.noteTerminalData(data);
      forwardPtyData(data);
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(RESIZE(cols, rows));
        } catch {
          /* socket already gone; onclose recovers */
        }
      }
    });
    // Release the stick-to-bottom pin the moment the user scrolls up, so
    // auto-follow only runs during the resume replay — never against their
    // manual review of the backlog.
    const scrollSub = term.onScroll(() => {
      try {
        stickToBottomRef.current = isViewportPinnedToBottom(
          term.buffer.active,
        );
      } catch {
        /* buffer unavailable during teardown */
      }
    });
    // Copy + word-delete shortcuts. Copy only when xterm holds a selection
    // (bare Ctrl+C still reaches the TUI as SIGINT); word-deletes send the
    // readline bindings the TUI expects. Paste is intentionally untouched —
    // this page has no image-upload path, so the default textarea paste stays.
    const isMac =
      typeof navigator !== "undefined" &&
      /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent ?? "");
    // attachCustomKeyEventHandler returns void (no disposable); the handler
    // dies with the terminal on dispose below.
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== "keydown") return true;
      const selection = term.getSelection();
      const shortcut = resolvePtyKeyboardShortcut(
        ev,
        isMac,
        Boolean(selection),
      );
      if (shortcut === "copy" && selection) {
        // Copy inside the keydown handler preserves the user gesture, which
        // an async round-trip could lose ("Document is not focused").
        void copyTextToClipboard(selection).then((copied) => {
          if (!copied) console.warn("[qubit-chat] direct copy failed");
        });
        term.clearSelection();
        ev.preventDefault();
        return false;
      }
      if (shortcut === "delete-word-backward") {
        ev.preventDefault();
        sendPtyShortcutSequence(wsRef.current, ptyStateRef.current, "\\x17");
        return false;
      }
      if (shortcut === "delete-word-forward") {
        ev.preventDefault();
        sendPtyShortcutSequence(wsRef.current, ptyStateRef.current, "\\x1bd");
        return false;
      }
      return true;
    });

    // Mobile IME + composition wiring on xterm's hidden textarea.
    const textarea = term.textarea;
    const isMobileLike =
      typeof navigator !== "undefined" &&
      /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    const markReplacementInput = (ev: Event) => {
      const input = ev as InputEvent;
      if (
        shouldTreatInputAsMobileReplacement(
          input.inputType,
          input.data,
          isMobileLike,
        )
      ) {
        mobileReplacementUntilRef.current =
          Date.now() + MOBILE_REPLACEMENT_WINDOW_MS;
      }
    };
    const markCompositionEnd = (ev: CompositionEvent) => {
      mobileReplacementUntilRef.current =
        Date.now() + MOBILE_REPLACEMENT_WINDOW_MS;
      compositionForwarder.onCompositionEnd(ev.data);
    };
    if (textarea) {
      textarea.setAttribute("autocomplete", "off");
      textarea.setAttribute("autocorrect", "off");
      textarea.setAttribute("autocapitalize", "off");
      textarea.setAttribute("spellcheck", "false");
      textarea.addEventListener("beforeinput", markReplacementInput, true);
      textarea.addEventListener("compositionend", markCompositionEnd, true);
    }
    // IME composition guard: React 18's root-level delegation intercepts
    // keydown with keyCode 229 (composition-in-progress) and synthesises an
    // onCompositionStart that interferes with xterm's own IME handling,
    // silently dropping the first keystroke of each composition chunk (most
    // visible with Cyrillic on Firefox-based browsers). xterm relies on native
    // composition events on its textarea, not keydown, so stopping the 229
    // keydown before it reaches the React root is safe.
    const imeCompositionGuard = (e: KeyboardEvent) => {
      if (e.keyCode === 229 || e.key === "Process") {
        e.stopPropagation();
      }
    };
    el.addEventListener("keydown", imeCompositionGuard, true);

    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(el);

    // Re-measure once the bundled font swaps in: xterm sizes its grid against
    // the fallback font until then, and the WebGL atlas keeps fallback glyphs.
    const stopFontRefit = refitWhenTerminalFontLoads(term, () => {
      try {
        fit.fit();
      } catch {
        /* host going away */
      }
    });

    if (
      shouldRestoreTerminalFocus(
        typeof document !== "undefined" ? document.activeElement : null,
        el,
      )
    ) {
      term.focus();
    }

    connect();
    return () => {
      disposedRef.current = true;
      ++connectionGenerationRef.current;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      clearPerConnectionTimers();
      try {
        wsRef.current?.close();
      } catch {
        /* already closed */
      }
      wsRef.current = null;
      dataSub.dispose();
      resizeSub.dispose();
      scrollSub.dispose();
      // (key handler needs no dispose — see above.)
      if (textarea) {
        textarea.removeEventListener("beforeinput", markReplacementInput, true);
        textarea.removeEventListener("compositionend", markCompositionEnd, true);
      }
      el.removeEventListener("keydown", imeCompositionGuard, true);
      compositionForwarder.dispose();
      compositionRef.current = null;
      observer.disconnect();
      stopFontRefit();
      connectInFlightRef.current = false;
      // The WebGL addon leaves its GL context alive on dispose; release it
      // before removing the canvases so reconnect storms can't walk into the
      // browser's context cap and blank the live terminal.
      loseWebglContexts(el);
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [connect, clearPerConnectionTimers]);

  // Soft-keyboard inset (phones): the keyboard overlays the layout viewport
  // instead of resizing it, so the host box never changes, fit() computes the
  // same grid, and the TUI input line hides under the keyboard. The obscured
  // height becomes bottom padding on the host, which shrinks it → the
  // ResizeObserver refit fires → the PTY re-lays-out above the keyboard.
  // Listeners live only while this tab is active.
  useEffect(() => {
    if (!isActive || typeof window === "undefined") return;
    // Capture the host for cleanups: the ref may point elsewhere by unmount.
    const insetHost = hostRef.current;
    let appliedInset = 0;
    const syncKeyboardInset = () => {
      const host = insetHost ?? hostRef.current;
      if (!host) return;
      const vv = window.visualViewport;
      const inset = computeKeyboardInset(
        vv ? { height: vv.height, offsetTop: vv.offsetTop } : null,
        window.innerHeight,
      );
      if (inset !== appliedInset) {
        appliedInset = inset;
        host.style.paddingBottom = inset > 0 ? `${inset}px` : "";
        try {
          fitRef.current?.fit();
        } catch {
          /* host going away */
        }
      }
      if (shouldPinScroll(inset)) {
        try {
          termRef.current?.scrollToBottom();
        } catch {
          /* ignore */
        }
      }
    };
    syncKeyboardInset();
    const vv = window.visualViewport;
    if (!vv) return () => {
      if (insetHost) insetHost.style.paddingBottom = "";
    };
    vv.addEventListener("resize", syncKeyboardInset);
    vv.addEventListener("scroll", syncKeyboardInset);
    return () => {
      vv.removeEventListener("resize", syncKeyboardInset);
      vv.removeEventListener("scroll", syncKeyboardInset);
      if (insetHost) insetHost.style.paddingBottom = "";
    };
  }, [isActive]);

  useEffect(() => {
    if (!isActive) return;
    // The host may have been display:none while hidden (zero box), which no
    // ResizeObserver fires for — refit on return, then restore focus only if
    // the user wasn't holding it elsewhere (sidebar picks must not be yanked).
    let raf1 = 0;
    let raf2 = 0;
    raf1 = requestAnimationFrame(() => {
      raf1 = 0;
      raf2 = requestAnimationFrame(() => {
        raf2 = 0;
        try {
          fitRef.current?.fit();
        } catch {
          /* host going away */
        }
        if (
          shouldRestoreTerminalFocus(
            typeof document !== "undefined" ? document.activeElement : null,
            hostRef.current,
          )
        ) {
          termRef.current?.focus();
        }
      });
    });
    return () => {
      if (raf1) cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, [isActive]);

  // Keep the MODEL card live: re-mint the (cheap, PTY-disrupting-free) grant
  // every 10 s and adopt it when the model/provider changed. A terminal
  // `/model` switch or a dashboard pick in another tab changes the session's
  // stored model but emits no structured event on the raw PTY byte stream, so
  // without this poll the card stayed stale until a reload (2026-10-05).
  // Never touches the socket, the transcript, or the session.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (disposedRef.current) return;
      void api
        .wsTicket(
          selectedRef.current ?? undefined,
          undefined,
          resumeRef.current || undefined,
        )
        .then((next) => {
          if (disposedRef.current) return;
          setGrant((prev) =>
            prev && prev.model === next.model && prev.provider === next.provider
              ? prev
              : next,
          );
        })
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, 10000);
    return () => window.clearInterval(timer);
  }, []);

  const modelToolsLabel = `${t.app.modelToolsSheetTitle} ${t.app.modelToolsSheetSubtitle}`;
  // Wait notice only while a resumed chat is still blank; reconnect/closed
  // states keep their own copy and must not stack it.
  const showResumeLoadingOverlay = shouldShowResumeLoadingOverlay({
    hasResumeTarget: Boolean(resumeTarget),
    ptyState: toLibPtyState(state),
    hydrating,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row lg:gap-3">
        <section
          aria-label="Qubit terminal pane"
          className="relative flex min-h-0 min-w-0 flex-1 flex-col gap-2"
        >
          {!sidePanelOpen ? (
            <div className="flex shrink-0 justify-end px-1">
              <Button
                ghost
                size="icon"
                onClick={() => setSidePanelOpen(true)}
                aria-expanded={false}
                aria-controls="chat-side-panel"
                aria-label="Show chat side panel"
                title="Show side panel"
              >
                <PanelRightOpen className="h-4 w-4" />
              </Button>
            </div>
          ) : null}
          {notice ? (
            <div
              role="alert"
              data-state="degraded"
              className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              {notice}
            </div>
          ) : null}
          {showResumeLoadingOverlay ? (
            <div
              role="status"
              className="shrink-0 rounded-md border border-current/15 bg-background-base/60 px-3 py-2 text-xs opacity-80"
            >
              {PTY_RESUME_LOADING_MESSAGE}
            </div>
          ) : null}
          {shouldShowResumeChecking(resumeProbe) ? (
            <div
              role="status"
              data-state="checking-resume"
              className="shrink-0 rounded-md border border-current/15 bg-background-base/60 px-3 py-2 text-xs opacity-80"
            >
              Checking that this session exists…
            </div>
          ) : null}
          {resumeProbe === "missing" ? (
            <div
              role="alert"
              data-state="resume-missing"
              className="flex min-h-0 flex-1 flex-col items-start justify-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-6"
            >
              <h2 className="text-sm font-medium text-destructive">
                {RESUME_MISSING_TITLE}
              </h2>
              <p className="max-w-prose text-xs text-text-secondary">
                {resumeMissingMessage(resumeParam)}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={handleNewChat}>
                  New chat
                </Button>
                <a
                  href="/sessions"
                  className="inline-flex items-center rounded-md px-2 py-1 text-xs text-midground hover:bg-midground/10"
                >
                  Browse sessions
                </a>
              </div>
            </div>
          ) : null}
          <div
            ref={hostRef}
            aria-label="Qubit terminal"
            hidden={resumeProbe === "missing"}
            className="min-h-0 flex-1 overflow-hidden rounded-lg border border-current/15 bg-background-base/60 p-2 [&_.xterm]:h-full [&_.xterm-viewport]:!bg-transparent"
          />
        </section>

        {sidePanelOpen ? (
          <div
            id="chat-side-panel"
            role="complementary"
            aria-label={modelToolsLabel}
            className="flex max-h-[50vh] min-h-0 shrink-0 flex-col gap-3 overflow-hidden lg:h-full lg:max-h-none lg:w-60"
          >
            <div className="flex shrink-0 items-center justify-end">
              <Button
                ghost
                size="icon"
                onClick={() => setSidePanelOpen(false)}
                aria-expanded
                aria-controls="chat-side-panel"
                aria-label="Collapse chat side panel"
                title="Collapse side panel"
              >
                <PanelRightClose className="h-4 w-4" />
              </Button>
            </div>
            <div className="shrink-0">
              <Suspense fallback={<div aria-hidden="true" className="h-24" />}>
                {/* The MODEL card must show the model the terminal actually
                    resolved (the PTY grant), not config.yaml's default role. */}
                <ChatSidebar
                  channel={channel}
                  profile={scopedProfile}
                  terminalModel={
                    grant?.model
                      ? {
                          provider: grant.provider,
                          model: grant.model,
                          contextTokens: grant.context_tokens,
                        }
                      : null
                  }
                />
              </Suspense>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <ChatSessionList
                key={sessionListNonce}
                activeSessionId={
                  freshChat ? FRESH_CHAT_SESSION_ID : resumeTarget
                }
                profile={scopedProfile}
                onNewChat={handleNewChat}
                workspaceCwd={workspaceCwd}
                onWorkspaceChange={setWorkspaceCwd}
                source="canonical"
              />
            </div>
            <div className="shrink-0 px-2">
              <a
                href={
                  resumeParam
                    ? `/qubit-chat?resume=${encodeURIComponent(resumeParam)}`
                    : "/qubit-chat"
                }
                aria-label="Open this session in voice chat"
                className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-midground hover:bg-midground/10"
              >
                <Mic className="h-3.5 w-3.5" />
                open in voice chat
              </a>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
