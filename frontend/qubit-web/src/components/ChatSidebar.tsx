/**
 * ChatSidebar — structured-events panel that sits next to the dashboard Chat
 * tab.
 *
 * One event transport, no sockets: Qubit refuses every WebSocket event
 * subscribe with HTTP 426 ``WS_SINGLE_PATH`` (single event path policy). The
 * supported event feed is the read-only SSE stream served by
 * ``voicebridge/webui.py:_events_stream`` at ``/api/events/stream``, consumed
 * by :mod:`@/lib/events-sse` (``fetch`` + a stream frame parser, because
 * ``EventSource`` cannot send the dashboard auth header). Transient drops
 * auto-reconnect with exponential backoff, resuming from the journal via
 * ``?last_seq=``; auth rejections are terminal. See ``lib/events-reconnect``.
 *
 * The former ``GatewayClient`` JSON-RPC sidecar (``/api/ws``) is retired: the
 * gateway answers the same 426 ``WS_SINGLE_PATH`` refusal, so opening it only
 * produced console noise. The connection badge now reflects the SSE reader's
 * own state, and the model badge/capabilities come from REST
 * (``/api/model/info``) as before.
 *
 * Best-effort throughout: feed failures show in the badge / banner, the chat
 * surface keeps working unimpaired.
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Card } from "@nous-research/ui/ui/components/card";

import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import { ReasoningPicker } from "@/components/ReasoningPicker";
import { api, QUBIT_BASE_PATH } from "@/lib/api";
import { maybeReloadForLoopbackWsAuthFailure } from "@/lib/dashboard-auth-reload";
import { openEventsStream, parseSseChunk } from "@/lib/events-sse";
import type { ConnectionState } from "@/lib/gatewayClient";
import {
  EVENTS_CONNECT_TIMEOUT_MS,
  EVENTS_MAX_RECONNECT_ATTEMPTS,
  eventsGaveUpMessage,
  eventsReconnectDelayMs,
  eventsReconnectingMessage,
  eventsRejectedMessage,
  isEventsFeedMessage,
} from "@/lib/events-reconnect";
import { titleFromSessionInfoPayload } from "@/lib/chat-title";
import { chatLiveFrame } from "@/lib/chatLiveEvents";

import { cn } from "@/lib/utils";
import { AlertCircle, ChevronDown, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

interface RpcEnvelope {
  method?: string;
  type?: string;
  params?: { type?: string; payload?: unknown };
}

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "idle",
  connecting: "connecting",
  open: "live",
  closed: "closed",
  error: "error",
};

const STATE_TONE: Record<
  ConnectionState,
  "secondary" | "warning" | "success" | "destructive"
> = {
  idle: "secondary",
  connecting: "warning",
  open: "success",
  closed: "secondary",
  error: "destructive",
};

interface ChatSidebarProps {
  channel: string;
  /** Chat profile from the dashboard switcher / URL scope. */
  profile?: string;
  /**
   * The model the `/chat` terminal actually resolved for this session (from
   * the PTY grant). When present it wins the MODEL card, because `/chat` runs
   * the terminal profile (`terminal.profile`), not config.yaml's
   * `model.default` role. `/qubit-chat` omits it and keeps the default-role
   * read from `/api/model/info`.
   */
  terminalModel?: { provider?: string; model: string; contextTokens?: number } | null;
  /**
   * The chat session this panel belongs to. When present, a model pick writes
   * the PER-SESSION model (``PATCH /api/qubit/chat/sessions/:id {model}``) —
   * the session's own stored model, never the global ``model.default`` — so
   * two surfaces keep independent models (the owner's requirement). ``/chat``
   * omits it and keeps its current global ``/api/model/set`` behaviour
   * unchanged, byte-identically.
   */
  sessionId?: string;
  /** Notified with the session's new model after a per-session pick succeeds. */
  onSessionModelChange?: (model: string) => void;
  className?: string;
  onDashboardNewSessionRequest?: () => void;
  onSessionTitleChange?: (title: string | null) => void;
}

/** Build the ``session.create`` params for the retired JSON-RPC sidecar.
 *
 * Retained (and unit-tested in ``chat-sidebar-session-params.test.ts``) as the
 * typed contract for the sidecar request shape. The transport itself is
 * refused by Qubit with HTTP 426 ``WS_SINGLE_PATH``, so ChatSidebar no longer
 * opens it; nothing in the running app calls this helper.
 */
export function sidecarSessionCreateParams(profile?: string): Record<string, unknown> {
  return {
    close_on_disconnect: true,
    source: "tool",
    ...(profile ? { profile } : {}),
  };
}

export function ChatSidebar({
  channel,
  profile,
  terminalModel,
  sessionId,
  onSessionModelChange,
  className,
  onDashboardNewSessionRequest,
  onSessionTitleChange,
}: ChatSidebarProps) {
  // `version` bumps on reconnect; it is the dependency that rebuilds the SSE
  // reader on a profile/channel switch or the manual Reconnect button.
  const [version, setVersion] = useState(0);

  const [state, setState] = useState<ConnectionState>("idle");
  const [modelOpen, setModelOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Fallback badge: config.yaml's main model (`model.default`) via
  // `/api/model/info` — the value a fresh default-role chat boots from. Pass
  // the chat profile explicitly so this card stays scoped to the chat even if
  // the global dashboard switcher changes while the chat is open. A caller
  // that runs a resolved terminal profile passes `terminalModel`, which wins.
  const [effectiveModel, setEffectiveModel] = useState("");
  // Whether the effective model supports reasoning effort — gates the
  // ReasoningPicker. Read from the same `/api/model/info` capabilities the
  // ModelInfoCard surfaces, so the dashboard exposes a control to *set* the
  // level, not just a read-only "Reasoning" badge.
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  // Bumped on model change/save so ReasoningPicker re-reads the saved effort
  // (config is profile-scoped the same way the model badge is).
  const [modelRefreshKey, setModelRefreshKey] = useState(0);
  // Set after the picker saves a model, to confirm the swap to the operator.
  const [modelNotice, setModelNotice] = useState<string | null>(null);

  const refreshEffectiveModel = useCallback(() => {
    // When the terminal grant names the model this session actually runs, read
    // ITS capabilities (the reasoning gate must match the displayed model, not
    // the default role).
    const target = terminalModel?.model
      ? { provider: terminalModel.provider, model: terminalModel.model }
      : undefined;
    void api
      .getModelInfo(profile, target)
      .then((r) => {
        if (r?.model) setEffectiveModel(String(r.model));
        setSupportsReasoning(!!r?.capabilities?.supports_reasoning);
        // Bump so ReasoningPicker re-reads the saved effort for the new model.
        setModelRefreshKey((k) => k + 1);
      })
      .catch(() => {
        // Best-effort: keep the last known label rather than blanking it.
      });
  }, [profile, terminalModel?.model, terminalModel?.provider]);

  // The SSE effect below is long-lived (it only rebuilds on channel/version),
  // so it must read the LATEST refresh callback rather than a stale closure
  // captured at subscribe time. A ref keeps the model card honest after a live
  // session.updated / model.changed without re-dialing the stream.
  const refreshEffectiveModelRef = useRef(refreshEffectiveModel);
  refreshEffectiveModelRef.current = refreshEffectiveModel;

  // Profile or chat channel change tears down the stream. Bump `version` (same
  // path as the manual Reconnect button) so the SSE reader resubscribes —
  // otherwise the old reader's close handler can leave a stale error banner
  // after a switch.
  const scopeKey = `${channel}\0${profile ?? ""}`;
  const prevScopeKey = useRef<string | null>(null);
  useEffect(() => {
    if (prevScopeKey.current === null) {
      prevScopeKey.current = scopeKey;
      return;
    }
    if (prevScopeKey.current === scopeKey) return;
    prevScopeKey.current = scopeKey;
    setError(null);
    setVersion((v) => v + 1);
  }, [scopeKey]);

  // SSE event subscriber — the single supported event path. Receives the
  // typed/AG-UI events published to the durable journal hub. The old
  // `/api/events` + `/api/ws` WebSockets are refused 426 by Qubit.
  //
  // Failures (auth rejection, transient drop, server restart) surface in the
  // banner so the user always has a reconnect affordance. `lastSeq` resumes
  // replay from the journal after a drop; a `resync` sentinel drops it.
  useEffect(() => {
    if (!channel) {
      return;
    }
    let unmounting = false;
    let controller: AbortController | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let connectTimer: ReturnType<typeof setTimeout> | null = null;
    let connectGeneration = 0;
    let attempt = 0;
    let lastSeq: number | null = null;

    // A server resync (genuine journal cursor gap) must not restart the feed
    // cold. The client already tracks `lastSeq`, so it resumes from the
    // retained cursor after a short debounce — the journal replay fills the
    // gap. Debouncing coalesces a burst of resyncs (a flood is exactly when
    // they appear) into one reconnect instead of a per-frame reconnect storm.
    const RESYNC_RECONNECT_MS = 1500;
    let resyncTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleResyncReconnect = () => {
      if (unmounting || resyncTimer || reconnectTimer) {
        return;
      }
      // A resync is server-driven recovery, not a hard failure; it must not
      // count against the failure budget nor surface a "reconnecting" banner.
      clearConnectTimer();
      resyncTimer = setTimeout(() => {
        resyncTimer = null;
        void connect();
      }, RESYNC_RECONNECT_MS);
    };

    const clearConnectTimer = () => {
      if (connectTimer) {
        clearTimeout(connectTimer);
        connectTimer = null;
      }
    };

    // The banner is shared with the model/credential surfaces, and `error` is
    // their only home. So the events feed may only write over an empty banner
    // or one of its own messages, and may only clear its own.
    const surface = (msg: string) =>
      !unmounting &&
      setError((current) =>
        isEventsFeedMessage(current) ? msg : (current ?? msg),
      );

    const clearEventsBanner = () =>
      !unmounting &&
      setError((current) => (isEventsFeedMessage(current) ? null : current));

    // Single scheduling path: queue at most one retry.
    const scheduleReconnect = () => {
      if (unmounting || reconnectTimer) {
        return;
      }
      if (attempt >= EVENTS_MAX_RECONNECT_ATTEMPTS) {
        surface(eventsGaveUpMessage());
        setState("error");
        return;
      }

      const delay = eventsReconnectDelayMs(attempt);
      attempt += 1;
      surface(eventsReconnectingMessage(delay));

      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, delay);
    };

    const handleEventData = (data: string) => {
      let frame: RpcEnvelope;

      try {
        frame = JSON.parse(data);
      } catch {
        return;
      }

      // Accept both the legacy JSON-RPC event envelope and a bare AG-UI event.
      let type: string | undefined;
      let payload: unknown;
      if (frame.method === "event" && frame.params) {
        type = frame.params.type;
        payload = frame.params.payload;
      } else {
        type = frame.type;
        payload = frame.params?.payload ?? frame;
      }

      if (type === "session.info") {
        const title = titleFromSessionInfoPayload(payload);
        if (title !== undefined) {
          onSessionTitleChange?.(title);
        }
        return;
      }
      if (type === "dashboard.new_session_requested") {
        onDashboardNewSessionRequest?.();
        return;
      }

      // Chat-session live-update frames (the server's producer half). A
      // session create / model pick / rename / delete and a global model
      // assignment all arrive as a CUSTOM envelope — reading `type` alone is
      // the literal "CUSTOM", so unwrap `name`/`value` via chatLiveFrame.
      // Without this the MODEL card kept a stale label until a page reload
      // (the reported defect).
      const live = chatLiveFrame(frame as unknown as Record<string, unknown>);
      if (!live) return;
      if (live.isModelChange) {
        refreshEffectiveModelRef.current();
        return;
      }
      if (live.isSession) {
        // Our own session changed (a per-session model pick writes
        // session.updated): relay the new model to the host and re-read the
        // effective model so the card repaints immediately.
        if (live.sessionId && sessionId && live.sessionId === sessionId) {
          const m = live.session ? String(live.session.model || "") : "";
          if (m) onSessionModelChange?.(m);
        }
        const title = live.session
          ? titleFromSessionInfoPayload(live.session)
          : undefined;
        if (title !== undefined) {
          onSessionTitleChange?.(title);
        }
        refreshEffectiveModelRef.current();
      }
    };

    const connect = async () => {
      if (unmounting) {
        return;
      }

      const generation = ++connectGeneration;
      setState("connecting");

      // Cover the whole connection attempt (fetch + headers). A failed or
      // hanging request otherwise emits no close event and strands the retry
      // loop at its last "reconnecting in ..." banner.
      clearConnectTimer();
      const attemptController = new AbortController();
      controller = attemptController;
      connectTimer = setTimeout(() => {
        connectTimer = null;
        if (unmounting || generation !== connectGeneration) {
          return;
        }
        // Invalidate any late result from this attempt before scheduling its
        // replacement.
        connectGeneration += 1;
        attemptController.abort();
        scheduleReconnect();
      }, EVENTS_CONNECT_TIMEOUT_MS);

      const conn = await openEventsStream({
        lastSeq,
        signal: attemptController.signal,
        basePath: QUBIT_BASE_PATH,
      });
      if (unmounting || generation !== connectGeneration) {
        return;
      }
      clearConnectTimer();

      if (!conn.ok || !conn.reader) {
        if (conn.authRequired) {
          // Loopback: a stale `_SESSION_TOKEN` after a server restart looks
          // exactly like this. Reload once to pick up the fresh token; gated
          // mode surfaces the terminal banner below.
          if (maybeReloadForLoopbackWsAuthFailure(4401)) {
            return;
          }
          setState("error");
          surface(eventsRejectedMessage(conn.status));
          return;
        }
        setState("closed");
        scheduleReconnect();
        return;
      }

      attempt = 0;
      clearEventsBanner();
      setState("open");

      const reader = conn.reader;
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          buffer += decoder.decode(value, { stream: true });

          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;

          if (parsed.resync) {
            // Server-side cursor gap: the journal holds the missed frames. Keep
            // `lastSeq` (resume from it — do NOT blank the cursor), abort this
            // now-stale connection, and reconnect once after a debounce. The
            // old handler dropped the cursor and fell through to a cold
            // `setState("closed") + scheduleReconnect()` on every resync, which
            // is what produced the connect→closed→reconnect flap.
            try {
              await reader.cancel();
            } catch {
              /* already released */
            }
            attemptController.abort();
            scheduleResyncReconnect();
            break;
          }

          for (const frame of parsed.frames) {
            if (frame.event === "resync") {
              continue;
            }
            if (frame.id != null) {
              lastSeq = frame.id;
            }
            if (frame.data) {
              handleEventData(frame.data);
            }
          }
        }
      } catch {
        // Aborted (unmount / resync / timeout) or the connection dropped —
        // the reconnect path below decides what happens next.
      }

      if (unmounting || generation !== connectGeneration) {
        return;
      }
      // A resync already scheduled the next attempt — don't also run the cold
      // failure path, which would flip the badge to "closed" and double-connect.
      if (resyncTimer) {
        return;
      }
      if (!reconnectTimer) {
        setState("closed");
        scheduleReconnect();
      }
    };

    void connect();

    return () => {
      unmounting = true;
      connectGeneration += 1;
      clearConnectTimer();
      if (resyncTimer) {
        clearTimeout(resyncTimer);
        resyncTimer = null;
      }
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      controller?.abort();
    };
  }, [channel, onDashboardNewSessionRequest, onSessionTitleChange, version]);

  // Seed the badge on mount and re-read it whenever the reader is rebuilt
  // (a profile/channel switch bumps `version`).
  useEffect(() => {
    refreshEffectiveModel();
  }, [refreshEffectiveModel, version]);

  const reconnect = useCallback(() => {
    setError(null);
    setModelNotice(null);
    setVersion((v) => v + 1);
  }, []);

  // The picker writes config.yaml over REST and reloads — it doesn't ride a
  // gateway session, so it's available whenever the sidebar is mounted.
  //
  // A `/chat` terminal resolves its own profile (`terminal.profile`), which can
  // differ from config.yaml's `model.default` role (the source of
  // `effectiveModel`). When the caller passes the resolved grant, that model is
  // the truth for this session: show it instead of the default-role label, so
  // the card never says `qubit-brain4/ollama` while the terminal runs the
  // high-context profile. `/qubit-chat` passes nothing and keeps the role read.
  const terminalModelName = terminalModel?.model
    ? `${terminalModel.provider ? `${terminalModel.provider}/` : ""}${terminalModel.model}`
    : "";
  const modelName = terminalModelName || effectiveModel || "—";
  // The pill next to the model name reports the MODEL, not the events feed:
  // a resolved PTY grant means the terminal session runs that model, even
  // while the SSE reader is still (re)connecting. Without a grant (qubit-chat)
  // the reader state is all we have.
  const badgeLabel = terminalModelName ? "live" : STATE_LABEL[state];
  const badgeTone = terminalModelName ? "success" : STATE_TONE[state];
  const modelLabel = modelName.split("/").slice(-1)[0] ?? "—";
  const banner = error;

  return (
    // Not an <aside>: ChatPage's #chat-side-panel already exposes the single
    // complementary landmark for this rail. A nested landmark would be an
    // unlabeled duplicate (drift: /chat exposed three <aside> landmarks).
    <div
      className={cn(
        "flex h-full w-full min-w-0 shrink-0 flex-col gap-3 overflow-y-auto overflow-x-hidden pr-1",
        className,
      )}
    >
      <Card className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="text-display text-xs tracking-wider text-text-tertiary">
            model
          </div>

          <Button
            ghost
            size="sm"
            onClick={() => setModelOpen(true)}
            className={cn(
              "max-w-full min-w-0 px-0 py-0",
              "self-start normal-case tracking-normal text-sm font-medium",
              "hover:underline disabled:no-underline",
            )}
            title={modelName === "—" ? "switch model" : modelName}
          >
            <span className="flex min-w-0 max-w-full items-center gap-1">
              <span className="truncate">{modelLabel}</span>

              <ChevronDown className="size-3.5 shrink-0 text-text-secondary" />
            </span>
          </Button>
        </div>

        <Badge tone={badgeTone} className="shrink-0">
          {badgeLabel}
        </Badge>
      </Card>

      {supportsReasoning && (
        <Card className="py-0">
          <ReasoningPicker
            currentModel={modelName}
            profile={profile}
            refreshKey={modelRefreshKey}
            onChanged={(effort) =>
              setModelNotice(
                `Reasoning effort set to ${effort}. Run /new or refresh the page to apply it to this chat.`,
              )
            }
          />
        </Card>
      )}

      {modelNotice && (
        <Card className="flex items-start gap-2 border-warning/40 bg-warning/5 px-3 py-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />

          <div className="wrap-break-word min-w-0 flex-1 text-text-secondary">
            {modelNotice}
          </div>
        </Card>
      )}

      {banner && (
        <Card className="flex items-start gap-2 border-destructive/40 bg-destructive/5 px-3 py-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />

          <div className="min-w-0 flex-1">
            <div className="wrap-break-word text-destructive">{banner}</div>

            {error && (
              <Button
                size="sm"
                outlined
                className="mt-1"
                onClick={reconnect}
                prefix={<RefreshCw />}
              >
                reconnect events feed
              </Button>
            )}
          </div>
        </Card>
      )}

      {modelOpen && (
        <ModelPickerDialog
          // Same path the Models page uses (REST /api/model/set), not the
          // sidecar config.set RPC, which didn't reliably land in the
          // config.yaml the agent boots from. Always persisted (alwaysGlobal).
          loader={() => api.getModelOptions(profile)}
          alwaysGlobal
          // Header shows what this terminal session actually runs (the PTY
          // grant), not the global picker context — otherwise the dialog
          // disagrees with the MODEL card above it.
          currentOverride={
            terminalModel?.model
              ? {
                  provider: terminalModel.provider,
                  model: terminalModel.model,
                }
              : null
          }
          onApply={async ({ provider, model, confirmExpensiveModel }) => {
            setModelNotice(null);
            // PER-SESSION pick when this panel is bound to a session (the
            // /qubit-chat sidebar always is). Write THIS session's stored
            // model through the canonical store — never the global
            // ``model.default`` — so two surfaces keep independent models and
            // the pick applies to the conversation actually open, immediately,
            // with no reload. Falling through to the global branch here is
            // why a session model could not be switched.
            if (sessionId) {
              try {
                const res = await api.patchChatSession(sessionId, { model });
                const applied = String(res?.session?.model || model);
                onSessionModelChange?.(applied);
                setModelNotice(
                  `Model set to ${applied.split("/").slice(-1)[0]} for this session.`,
                );
                // Re-read the card from the server's authoritative row.
                refreshEffectiveModel();
              } catch (e) {
                setError(
                  e instanceof Error
                    ? e.message
                    : "failed to set this session's model",
                );
              }
              return undefined;
            }
            const result = await api.setModelAssignment(
              {
                confirm_expensive_model: confirmExpensiveModel,
                scope: "main",
                provider,
                model,
              },
              profile,
            );
            // confirm_required => the dialog shows the expensive-model prompt
            // and calls back; don't announce until the user confirms.
            if (!result.confirm_required) {
              refreshEffectiveModel();
              // The backend reports whether /chat follows the pick: when it
              // kept its pinned model (pick below the /chat context floor),
              // say so instead of claiming a switch the terminal did not take.
              if (result.terminal_follows === false) {
                setModelNotice(
                  result.terminal_detail ||
                    "Main model set. /chat keeps its current model.",
                );
              } else {
                // No reload: `h_model_set` publishes a `model.changed` frame on
                // the single SSE feed, and every surface (this card, /chat's
                // model state, other tabs) repaints from it. A full-page reload
                // here raced the SSE socket + session-token rotation and left
                // the page blank until a manual refresh — the reported
                // "switching a model blacks out /chat" defect. The frame is the
                // supported apply path; just confirm the write landed.
                setModelNotice(
                  `Main model set to ${model.split("/").slice(-1)[0]}.`,
                );
                refreshEffectiveModelRef.current();
              }
            }
            return result;
          }}
          onClose={() => {
            setModelOpen(false);
            refreshEffectiveModel();
          }}
        />
      )}
    </div>
  );
}
