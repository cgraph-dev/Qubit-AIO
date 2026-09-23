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
import { PanelRightClose, PanelRightOpen, RotateCcw } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { api } from "@/lib/api";
import type { TerminalProfileInfo, WsTicketGrant } from "@/lib/api";
import { WsTicketError } from "@/lib/api";
import { terminalRefusalCopy } from "@/lib/terminal-errors";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";
import { ChatSessionList } from "@/components/ChatSessionList";
import { ChatToolsSummary } from "@/components/ChatToolsSummary";

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
  const [state, setState] = useState<"connecting" | "open" | "closed" | "error">(
    "connecting",
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [grant, setGrant] = useState<WsTicketGrant | null>(null);
  const [profiles, setProfiles] = useState<TerminalProfileInfo[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);
  const [savingDefault, setSavingDefault] = useState(false);
  const selectedRef = useRef<string | null>(null);
  // Right-side panel (model control + status + sessions), collapsible and open
  // by default, matching the Qubit `/qubit-chat` ChatPage panel.
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  const [channel] = useState<string>(generateTerminalChatChannel);
  // Stable indirection for the self-scheduled reconnect: `connect` cannot
  // reference itself inside its own initializer, so it schedules the latest
  // callback through this ref instead (assigned after each render).
  const connectRef = useRef<() => void>(() => {});
  // Stable identity so ChatSidebar's SSE effect never re-subscribes per render.
  // "New chat" starts a fresh PTY: clear the resume target first, then
  // reconnect. Clearing the URL param keeps a later pick able to re-trigger the
  // resume effect (resumeRef already equals the new "" so the effect no-ops).
  const handleNewChat = useCallback(() => {
    resumeRef.current = "";
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("resume");
        return next;
      },
      { replace: true },
    );
    connectRef.current();
  }, [setSearchParams]);

  const connect = useCallback((profileId?: string) => {
    const term = termRef.current;
    if (!term || disposedRef.current) return;
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
          setSelectedProfile(ticket.profile_id);
        }
        const ws = new WebSocket(
          terminalWsUrl(ticket.ticket, cols, rows, resumeRef.current),
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

  // Model profiles for the picker (non-secret summaries with the guard
  // verdict each). A failing list never blocks the terminal open: the
  // ticket mint carries the real refusal.
  useEffect(() => {
    let cancelled = false;
    api
      .getTerminalProfiles()
      .then((rows) => {
        if (cancelled) return;
        setProfiles(rows);
        const current =
          rows.find((r) => r.default) ??
          rows.find((r) => r.meets_floor) ??
          rows[0] ??
          null;
        if (current && selectedRef.current === null) {
          selectedRef.current = current.id;
          setSelectedProfile(current.id);
        }
      })
      .catch(() => {
        if (!cancelled) setProfiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelectProfile = (id: string) => {
    selectedRef.current = id;
    setSelectedProfile(id);
    connect(id);
  };

  const handleSaveDefault = () => {
    const id = selectedRef.current;
    if (!id || savingDefault) return;
    setSavingDefault(true);
    setNotice(null);
    api
      .setTerminalProfile(id, (detail) => window.confirm(detail))
      .then((res) => {
        setProfiles((prev) =>
          prev.map((r) => ({ ...r, default: r.id === res.profile_id })),
        );
        if (!res.meets_floor) {
          setNotice(
            `Saved '${res.profile_id}' as the /chat default, but it does ` +
              `not meet the context floor — the terminal will refuse until ` +
              `its window or credentials are fixed.`,
          );
        }
      })
      .catch((e: unknown) => setNotice(String(e)))
      .finally(() => setSavingDefault(false));
  };

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
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-1">
            <span className="text-xs font-semibold tracking-wide text-text-secondary">
              {t.app.nav.chat} · Qubit
            </span>
            <span className="flex flex-wrap items-center gap-2">
              {grant?.model ? (
                <span
                  data-testid="terminal-active-model"
                  data-verified={
                    grant.context_verified === false ? "false" : "true"
                  }
                  className="text-xs text-text-tertiary"
                >
                  {grant.provider ? `${grant.provider}/` : ""}
                  {grant.model}
                  {typeof grant.context_tokens === "number"
                    ? ` · ${grant.context_tokens.toLocaleString()} ctx`
                    : ""}
                </span>
              ) : null}
              {grant?.context_verified === false ? (
                <span
                  role="status"
                  data-testid="terminal-context-unverified"
                  data-state="degraded"
                  className="text-xs text-warning"
                  title={
                    grant.context_window_source
                      ? `real window not resolved (${grant.context_window_source}); declared window trusted as flagged unverified`
                      : "declared context window could not be cross-checked"
                  }
                >
                  {t.app.nav.chat} ctx unverified
                </span>
              ) : null}
              <span
                data-state={
                  state === "open"
                    ? "ready"
                    : state === "connecting"
                      ? "loading"
                      : "degraded"
                }
                className="text-xs text-text-tertiary"
              >
                {state}
              </span>
              <Button
                size="sm"
                outlined
                aria-label="Reconnect terminal"
                prefix={<RotateCcw className="h-3.5 w-3.5" />}
                onClick={() => connectRef.current()}
              >
                Reconnect
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
            </span>
          </div>
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
            <section
              aria-label="Terminal model"
              data-testid="terminal-model-card"
              className="flex shrink-0 flex-col gap-1.5 rounded-lg border border-current/15 bg-background-base/60 p-2"
            >
              <span className="text-display text-xs tracking-wider text-text-tertiary">
                {t.app.nav.chat} terminal model
              </span>
              {profiles.length > 0 ? (
                <select
                  aria-label="Terminal model profile"
                  data-testid="terminal-profile-picker"
                  className="min-w-0 w-full truncate rounded border border-current/15 bg-background-base/60 px-1 py-0.5 text-xs text-text-secondary"
                  value={selectedProfile ?? ""}
                  onChange={(e) => handleSelectProfile(e.target.value)}
                >
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id} · {p.provider}/{p.model}
                      {p.context_tokens > 0
                        ? ` · ${(p.context_tokens / 1000).toFixed(0)}k ctx`
                        : ""}
                      {p.meets_floor ? "" : " · below floor"}
                      {p.has_credential ? "" : " · no credential"}
                    </option>
                  ))}
                </select>
              ) : null}
              <Button
                size="sm"
                outlined
                aria-label="Use selected profile for /chat"
                onClick={handleSaveDefault}
                disabled={!selectedProfile || savingDefault}
              >
                {savingDefault ? "Saving…" : "Set default"}
              </Button>
              <span className="text-[0.6875rem] leading-snug text-text-tertiary">
                Sets <span className="font-mono">terminal.profile</span> for
                this terminal. The model above the sessions list is the chat
                model (<span className="font-mono">model.default</span>).
              </span>
            </section>
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
            {/* Read-only summary of the real tools/skills/plugins/MCP
                registries. Independent of the PTY child: a failed fetch
                renders a short note and never blocks the terminal. */}
            <ChatToolsSummary profile={scopedProfile} />
            <div className="min-h-0 flex-1 overflow-hidden">
              <ChatSessionList
                activeSessionId={resumeParam || null}
                profile={scopedProfile}
                onNewChat={handleNewChat}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
