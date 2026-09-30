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
import { Terminal as XtermTerminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { api } from "@/lib/api";
import type { WsTicketGrant } from "@/lib/api";
import { WsTicketError } from "@/lib/api";
import { terminalRefusalCopy } from "@/lib/terminal-errors";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import { ChatSessionList } from "@/components/ChatSessionList";

// Lazily loaded so the informational dashboard event SSE stream (opened by
// ChatSidebar) is code-split out of the base chunk — same pattern as
// `pages/ChatPage.tsx`.
const ChatSidebar = lazy(() =>
  import("@/components/ChatSidebar").then((m) => ({ default: m.ChatSidebar })));

const RESIZE = (cols: number, rows: number) => `\x1b[RESIZE:${cols};${rows}]`;

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

function terminalWsUrl(
  ticket: string,
  cols: number,
  rows: number,
  resume?: string,
  cwd?: string,
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
  // Workspace for a FRESH chat (the server ignores it on resume, which keeps
  // its own cwd). Same wire the reference uses: /api/pty?cwd=.
  if (cwd) qs.set("cwd", cwd);
  return `${proto}//${window.location.host}/agent/api/pty?${qs.toString()}`;
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
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [, setState] = useState<"connecting" | "open" | "closed" | "error">(
    "connecting",
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [grant, setGrant] = useState<WsTicketGrant | null>(null);
  // The profile the terminal last resolved (from the ticket grant); used when
  // minting the next ticket and in refusal copy. Not rendered as a picker —
  // model switching lives in the MODEL card (`/api/model/set`), as in the reference.
  const selectedRef = useRef<string | null>(null);
  // Right-side panel (model control + status + sessions), collapsible and open
  // by default, matching the Qubit `/qubit-chat` ChatPage panel.
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  // Bumped after a New chat mint so `ChatSessionList` remounts and refetches,
  // surfacing the freshly created session immediately (its internal fetch only
  // runs on mount / manual Refresh).
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
  const connectRef = useRef<() => void>(() => {});
  // "New chat" mints a genuinely NEW session on the canonical store (the same
  // store the sessions list and transcript read) and resumes it, so the
  // terminal opens a fresh transcript instead of re-opening the fixed
  // canonical default (`dashboard-chat`). The implicit first load (no
  // `?resume=`) still converges on the canonical default for continuity.
  // A failed mint falls back to a fresh canonical-default PTY with a notice.
  const handleNewChat = useCallback(() => {
    setNotice(null);
    api
      .createChatSession()
      .then((res) => {
        if (disposedRef.current) return;
        const newId = res?.session?.id;
        // Seed the ref BEFORE the URL update so the resume effect no-ops and
        // exactly one reconnect happens (no double-spawn / duplicate).
        resumeRef.current = newId ?? "";
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (newId) next.set("resume", newId);
            else next.delete("resume");
            return next;
          },
          { replace: false },
        );
        connectRef.current();
        if (newId) setSessionListNonce((n) => n + 1);
      })
      .catch((err: unknown) => {
        if (disposedRef.current) return;
        setNotice(
          `[CHAT_SESSION_CREATE] ${
            err instanceof Error ? err.message : String(err)
          } — starting a fresh terminal instead`,
        );
        resumeRef.current = "";
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.delete("resume");
            return next;
          },
          { replace: false },
        );
        connectRef.current();
      });
  }, [setSearchParams]);

  const connect = useCallback((profileId?: string) => {
    const term = termRef.current;
    if (!term || disposedRef.current) return;
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
    const picked = profileId ?? selectedRef.current ?? undefined;
    setState("connecting");
    setNotice(null);
    const cols = term.cols || 80;
    const rows = term.rows || 24;
    api
      .wsTicket(picked)
      .then((ticket) => {
        if (disposedRef.current) return;
        setGrant(ticket);
        if (ticket.profile_id) {
          selectedRef.current = ticket.profile_id;
        }
        const ws = new WebSocket(
          terminalWsUrl(ticket.ticket, cols, rows, resumeRef.current, workspaceRef.current),
        );
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;
        ws.onopen = () => {
          setState("open");
          setNotice(null);
          fitRef.current?.fit();
        };
        ws.onmessage = (ev: MessageEvent) => {
          if (ev.data instanceof ArrayBuffer) {
            term.write(new Uint8Array(ev.data));
          } else if (typeof ev.data === "string") {
            term.write(ev.data);
          }
        };
        ws.onerror = () => setState("error");
        ws.onclose = () => {
          wsRef.current = null;
          setState("closed");
          if (!disposedRef.current) {
            // Reconnect with a FRESH single-use ticket.
            reconnectTimerRef.current = setTimeout(
              () => connectRef.current(),
              1500,
            );
          }
        };
      })
      .catch((err: unknown) => {
        setState("error");
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
      });
  }, []);

  useEffect(() => {
    connectRef.current = () => connect();
  }, [connect]);

  // Reconnect when the resume target changes (a session row was picked, or
  // "New chat" cleared it). The first render is a no-op: the ref was seeded
  // with the same value the mount-time connect already used.
  useEffect(() => {
    if (resumeRef.current === resumeParam) return;
    resumeRef.current = resumeParam;
    connectRef.current();
  }, [resumeParam]);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    disposedRef.current = false;
    const term = new XtermTerminal({
      convertEol: false,
      cursorBlink: true,
      fontFamily: "var(--font-mono, monospace)",
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
    term.open(el);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const dataSub = term.onData((data) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(new TextEncoder().encode(data));
      }
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(new TextEncoder().encode(RESIZE(cols, rows)));
      }
    });
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(el);

    connect();
    return () => {
      disposedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      try {
        wsRef.current?.close();
      } catch {
        /* already closed */
      }
      wsRef.current = null;
      dataSub.dispose();
      resizeSub.dispose();
      observer.disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [connect]);

  useEffect(() => {
    if (isActive) fitRef.current?.fit();
  }, [isActive]);

  const modelToolsLabel = `${t.app.modelToolsSheetTitle} ${t.app.modelToolsSheetSubtitle}`;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row lg:gap-3">
        <section
          aria-label="Qubit terminal pane"
          className="flex min-h-0 min-w-0 flex-1 flex-col gap-2"
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
          <div
            ref={hostRef}
            aria-label="Qubit terminal"
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
                activeSessionId={resumeParam || null}
                profile={scopedProfile}
                onNewChat={handleNewChat}
                workspaceCwd={workspaceCwd}
                onWorkspaceChange={setWorkspaceCwd}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
