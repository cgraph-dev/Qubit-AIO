/**
 * ChatSessionList — a ChatGPT-style conversation switcher that sits beside
 * the Qubit Chat transcript on the dashboard Chat tab.
 *
 * It lists the most recent sessions from the Qubit-native chat-session store
 * (`GET /api/qubit/chat/sessions`, `voicebridge.chat_transport`) — NOT the
 * receipts `GET /api/sessions` store used by the Sessions page. Selecting a
 * row sets `/chat?resume=<id>` and ChatPage loads that session's persisted
 * SSE transcript from the same store. The "New session" action starts a
 * fresh chat.
 *
 * Best-effort: a failed fetch surfaces a small inline error with a retry
 * affordance and the transcript pane keeps working.
 *
 * This is a navigation surface, NOT a session-management one — delete,
 * rename, export, and bulk actions live on the Sessions page. Keeping this
 * panel read-only (plus select / new) avoids duplicating that machinery and
 * keeps the chat context focused on switching conversations quickly.
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { AlertCircle, MessageSquarePlus, RefreshCw, Terminal } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { useI18n } from "@/i18n";
import { ChatWorkspacePicker } from "@/components/ChatWorkspacePicker";
import { mergeActiveFirst } from "@/components/chatSessionOrder";
import { api, type ChatStoreSession } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

const SESSION_LIMIT = 30;
/** Best-effort live refresh cadence for the switcher while the tab is open. */
const SESSION_REFRESH_MS = 15_000;
/** Which session store this list reads. `chat` keeps the Qubit-native
 * chat-session store (`GET /api/qubit/chat/sessions`, voice channel);
 * `canonical` reads the canonical receipts store (`GET /api/sessions`,
 * profile-scoped, the store the `/chat` PTY resumes). */
export type ChatSessionListSource = "chat" | "canonical";
interface ChatSessionListProps {
  /** Active resume target (the session currently shown in the transcript). */
  activeSessionId: string | null;
  /** Management profile from the dashboard switcher (chat store is single-
   * principal; retained for interface compatibility with the host). */
  profile?: string;
  className?: string;
  /** Optional callback fired after a row is picked (e.g. close mobile sheet). */
  onPicked?: () => void;
  /**
   * Starts a fresh chat. ChatPage clears `?resume` and resets the transcript;
   * when omitted we clear the resume param ourselves.
   */
  onNewChat?: () => void;
  /** Picked workspace (absolute host path) for a FRESH chat; "" = server default. */
  workspaceCwd?: string;
  /** Owned by the host so the picker applies on the next New chat. */
  onWorkspaceChange?: (cwd: string) => void;
  /**
   * Which session store to list. Defaults to `chat` (exact current
   * behavior); `/chat` hosts pass `canonical` so rows match the store the
   * PTY resumes.
   */
  source?: ChatSessionListSource;
}

function rowLabel(session: ChatStoreSession, untitled: string): string {
  const title = session.name?.trim() || session.title?.trim();
  if (title && title !== "Untitled") return title;
  const preview = session.preview?.trim();
  if (preview) return preview;
  return untitled;
}

export function ChatSessionList({
  activeSessionId,
  className,
  onPicked,
  onNewChat,
  profile,
  workspaceCwd,
  onWorkspaceChange,
  source = "chat",
}: ChatSessionListProps) {
  const { t } = useI18n();
  const [, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<ChatStoreSession[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped to force a refetch (after switching, on Refresh, on mount).
  const [reloadNonce, setReloadNonce] = useState(0);

  // Monotonic request token: only the most recent fetch is allowed to
  // commit state, so a fast refresh (or Refresh spam) can't land a stale
  // list out of order.
  const reqRef = useRef(0);

  // `profile` scopes the canonical listing; the empty-string fallback keeps
  // the dep stable when no profile is selected (default profile). The chat
  // store is single-principal and ignores it.
  const scopeKey = profile ?? "";

  const load = useCallback(() => {
    const myReq = ++reqRef.current;
    setLoading(true);
    setError(null);
    // Canonical rows carry the same render fields (id/title/preview/source/
    // last_active/message_count), so they flow through the shared row render.
    const request =
      source === "canonical"
        ? api
            .getSessions(SESSION_LIMIT, 0, scopeKey, "recent")
            .then((res) => ({ sessions: res.sessions }))
        : api.listChatSessions(SESSION_LIMIT, 0);
    request
      .then((res) => {
        if (reqRef.current !== myReq) return;
        setSessions(res.sessions);
      })
      .catch((e: Error) => {
        if (reqRef.current !== myReq) return;
        setError(e.message || "failed to load chat sessions");
      })
      .finally(() => {
        if (reqRef.current === myReq) setLoading(false);
      });
  }, [scopeKey, source]);

  useEffect(() => {
    // Dashboard data surfaces fetch from an effect on mount + scope change;
    // keep this local and explicit until the shared lint profile is updated
    // for async loaders (matches FilesPage). Re-runs when `activeSessionId`
    // changes so the row you are on moves to the top as soon as the host
    // switches sessions; `reloadNonce` is the manual Refresh trigger.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load, reloadNonce, activeSessionId]);

  // Live updates (best-effort, no new deps): stay fresh while the tab is open
  // without a manual Refresh. Refetch on window focus, when the tab becomes
  // visible, and on a modest interval. `load`'s monotonic `reqRef` token keeps
  // a slow response from clobbering a newer one; all listeners + the interval
  // are torn down on unmount.
  useEffect(() => {
    const onFocus = () => load();
    const onVisibility = () => {
      if (document.visibilityState === "visible") load();
    };
    const interval = window.setInterval(load, SESSION_REFRESH_MS);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  const reload = useCallback(() => setReloadNonce((n) => n + 1), []);

  // The active session must always be visible and on top, even when the host's
  // resume id is a canonical/worker id the chat-store list does not contain
  // (mergeActiveFirst prepends a synthetic row for that case). Pure + DOM-free
  // so the ordering contract is unit-tested alongside its own module.
  const orderedSessions = useMemo(
    () => mergeActiveFirst(sessions, activeSessionId),
    [sessions, activeSessionId],
  );

  // Picking a row sets `/chat?resume=<id>`. Re-picking the row already in
  // the terminal is a no-op (avoids a needless PTY teardown).
  const pick = useCallback(
    (id: string) => {
      onPicked?.();
      if (id === activeSessionId) return;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("resume", id);
          return next;
        },
        { replace: false },
      );
    },
    [activeSessionId, onPicked, setSearchParams],
  );

  // "New chat" prefers ChatPage's robust handler (clears resume + forces a
  // PTY respawn even from an already-fresh session). Fallback: clear the
  // resume param ourselves, which spawns a fresh PTY whenever one was being
  // resumed. Session management (delete/rename/export) lives on the Sessions
  // page; this panel only switches and starts conversations.
  const startNew = useCallback(() => {
    onPicked?.();
    if (onNewChat) {
      onNewChat();
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("resume");
        return next;
      },
      { replace: false },
    );
  }, [onNewChat, onPicked, setSearchParams]);

  const content = useMemo(() => {
    if (loading && sessions === null) {
      return (
        <div className="qchat__sessions-skeleton" role="status" aria-label={t.common.loading}>
          {[0, 1, 2, 3].map((row) => (
            <div className="qchat__session-skeleton-row" key={row} aria-hidden="true">
              <span />
              <span />
            </div>
          ))}
        </div>
      );
    }
    if (error) {
      return (
        <div className="flex flex-col items-start gap-2 px-2 py-4 text-xs">
          <div className="flex items-start gap-2 text-destructive">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="wrap-break-word">{error}</span>
          </div>
          <Button size="sm" outlined onClick={reload} prefix={<RefreshCw />}>
            {t.common.retry}
          </Button>
        </div>
      );
    }
    if (orderedSessions.length === 0) {
      return (
        <div className="px-2 py-6 text-center text-xs text-text-secondary">
          {t.sessions.noSessions}
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-0.5">
        {orderedSessions.map((s) => {
          const isActive = s.id === activeSessionId;
          return (
            <ListItem
              key={s.id}
              onClick={() => pick(s.id)}
              aria-current={isActive ? "true" : undefined}
              className={cn(
                "flex-col items-start gap-0.5 rounded px-2 py-1.5",
                "normal-case tracking-normal",
                isActive
                  ? "qchat__session-row--active bg-primary/15 font-medium text-foreground"
                  : "text-text-secondary hover:bg-midground/5 hover:text-foreground",
              )}
            >
              <span className="w-full truncate text-sm font-medium">
                {rowLabel(s, t.sessions.untitledSession)}
              </span>
              <span className="flex w-full items-center gap-1.5 text-[0.6875rem] text-text-tertiary">
                <span>{timeAgo(s.last_active ?? s.started_at ?? 0)}</span>
                {(s.message_count ?? 0) > 0 && (
                  <>
                    <span aria-hidden>·</span>
                    <span>{s.message_count} msgs</span>
                  </>
                )}
                {s.source && s.source !== "cli" && (
                  <>
                    <span aria-hidden>·</span>
                    <span className="truncate">{s.source}</span>
                  </>
                )}
                {/* Reach the terminal session where the agent actually runs
                    (the /chat PTY). stopPropagation keeps the row's own
                    "resume in this page" click intact. */}
                <a
                  href={`/chat?resume=${encodeURIComponent(s.id)}`}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={`Open session ${s.id} in the /chat terminal`}
                  title="Open in /chat"
                  className="ml-auto inline-flex shrink-0 items-center gap-0.5 rounded px-1 py-0.5 text-midground hover:bg-midground/10"
                >
                  <Terminal className="h-3 w-3" />
                  /chat
                </a>
              </span>
            </ListItem>
          );
        })}
      </div>
    );
  }, [activeSessionId, error, loading, orderedSessions, pick, reload, t]);

  return (
    // Not an <aside>: the containing #chat-side-panel is the complementary
    // landmark (drift: /chat exposed three <aside> landmarks).
    <div
      className={cn(
        "flex h-full w-full min-w-0 shrink-0 flex-col overflow-hidden",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 px-2 pb-2">
        <span className="text-display text-xs tracking-wider text-text-tertiary">
          {t.sessions.title}
        </span>
        <Button
          ghost
          size="icon"
          onClick={reload}
          aria-label={t.common.refresh}
          title={t.common.refresh}
          className="text-text-secondary hover:text-foreground"
        >
          <RefreshCw className={cn(loading && "animate-spin")} />
        </Button>
      </div>

      {onWorkspaceChange && (
        <ChatWorkspacePicker
          profile={profile}
          value={workspaceCwd ?? ""}
          onChange={onWorkspaceChange}
        />
      )}

      <Button
        outlined
        size="sm"
        onClick={startNew}
        prefix={<MessageSquarePlus />}
        className="mx-2 mb-2 justify-center"
      >
        {t.sessions.newChat}
      </Button>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-1 pb-1">
        {content}
      </div>
    </div>
  );
}
