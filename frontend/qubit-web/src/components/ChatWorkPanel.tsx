/**
 * Chat work-status panel (C1).
 *
 * `/chat` shows live agent/todo/token readouts because it renders the real TUI.
 * `/qubit-chat` renders the native React page, so that activity is invisible
 * there. This panel surfaces it as a bounded strip above the composer and links
 * straight into the `/chat` view of the same session.
 *
 * Deliberately NOT a second chat surface (web/AGENTS.md): it never mounts a
 * terminal, never owns a session, and never submits a turn. It reads the
 * session's activity over the same authenticated JSON-RPC the page already
 * uses, and a failure degrades to a quiet line — it can never break the
 * transcript or the composer.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentActivity } from "@/hooks/useChatActivity";
import "./ChatWorkPanel.css";

export interface ChatWorkPanelProps {
  /** Live activity for the current session (already polled by the page). */
  activity: AgentActivity;
  /** True while the current turn is running. */
  busy: boolean;
  /** Telemetry already parsed by the page (model / elapsed / tokens). */
  model?: string;
  elapsedMs?: number;
  tokens?: number;
}

/**
 * Animation frame sequence for the "working" indicator. A rotating glyph is
 * deliberately cheap: no layout thrash, no dependency, and it reads as motion
 * even when the numbers are static.
 */
const FRAMES = ["\u280B", "\u2819", "\u2839", "\u2838", "\u283C", "\u2834", "\u2826", "\u2827", "\u2807", "\u280F"];

const TICK_MS = 120;

export function ChatWorkPanel({
  activity,
  busy,
  model,
  elapsedMs,
  tokens,
}: ChatWorkPanelProps) {
  const [frame, setFrame] = useState(0);
  const [minimised, setMinimised] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Only animate while there is something in flight — an idle page must not
  // burn a timer.
  useEffect(() => {
    if (!busy) return;
    const id = window.setInterval(() => {
      if (mountedRef.current) setFrame((f) => (f + 1) % FRAMES.length);
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [busy]);

  const agents = activity.subagents ?? [];
  const todos = activity.todos ?? [];

  const summary = useMemo(() => {
    if (busy) {
      const active = agents.filter((a) => a.status === "running").length;
      if (active > 1) return `${active} agents working`;
      if (active === 1) return "1 agent working";
      return "Qubit is working";
    }
    if (agents.length > 0) return `${agents.length} agent${agents.length === 1 ? "" : "s"} finished`;
    return "Idle";
  }, [busy, agents]);

  // A collapse control that expands into nothing is worse than no control: the
  // owner clicked it and the panel "did nothing". Offer the toggle only when
  // there is a body to reveal.
  const hasDetail = agents.length > 0 || todos.length > 0;
  const canExpand = hasDetail;
  const expanded = canExpand && !minimised;

  // Nothing to show and nothing running: stay out of the way entirely.
  if (!busy && !hasDetail) return null;

  return (
    <div
      className="qubit-work-panel"
      data-state={busy ? "working" : "idle"}
      data-minimised={minimised ? "true" : "false"}
    >
      <div className="qubit-work-panel__head">
        {canExpand ? (
          <button
            type="button"
            className="qubit-work-panel__toggle"
            onClick={() => setMinimised((m) => !m)}
            aria-expanded={expanded}
            aria-controls="qubit-work-panel-body"
            title={expanded ? "Minimise work status" : "Expand work status"}
          >
            {expanded ? "\u25BE" : "\u25B8"}
          </button>
        ) : (
          <span className="qubit-work-panel__toggle qubit-work-panel__toggle--static" aria-hidden="true">
            {busy ? "\u25BE" : "\u25B8"}
          </span>
        )}
        <span className="qubit-work-panel__glyph" aria-hidden="true">
          {busy ? FRAMES[frame] : "\u25CF"}
        </span>
        <span className="qubit-work-panel__summary">{summary}</span>
        <span className="qubit-work-panel__spacer" />
        {model ? (
          <span className="qubit-work-panel__meta" title="model">
            {model}
          </span>
        ) : null}
        {elapsedMs != null ? (
          <span className="qubit-work-panel__meta" title="elapsed">
            {(elapsedMs / 1000).toFixed(1)}s
          </span>
        ) : null}
        {tokens != null ? (
          <span className="qubit-work-panel__meta" title="tokens">
            {tokens.toLocaleString()} tok
          </span>
        ) : null}
      </div>

      {expanded ? (
        <div className="qubit-work-panel__body" id="qubit-work-panel-body">
          {agents.length > 0 ? (
            <ul className="qubit-work-panel__list" aria-label="Agents">
              {agents.map((a) => (
                <li key={a.id} className="qubit-work-panel__row">
                  <span
                    className="qubit-work-panel__dot"
                    data-status={a.status}
                    aria-hidden="true"
                  />
                  <span className="qubit-work-panel__name">{a.goal || a.id}</span>
                  {a.lastTool ? (
                    <span className="qubit-work-panel__meta">{a.lastTool}</span>
                  ) : null}
                  <span className="qubit-work-panel__meta">{a.status}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {todos.length > 0 ? (
            <ul className="qubit-work-panel__list" aria-label="Todos">
              {todos.map((t) => (
                <li key={t.id} className="qubit-work-panel__row">
                  <span className="qubit-work-panel__check" aria-hidden="true">
                    {t.done ? "\u2611" : "\u2610"}
                  </span>
                  <span className={t.done ? "qubit-work-panel__done" : undefined}>
                    {t.text}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ChatWorkPanel;
