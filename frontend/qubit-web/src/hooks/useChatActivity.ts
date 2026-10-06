/**
 * Live agent/todo activity for one chat session, read from the Qubit SSE feed.
 *
 * Why SSE and not a socket: Qubit deliberately keeps exactly ONE event path.
 * Every WebSocket subscribe (`/api/ws`, `/api/events`) answers HTTP 426
 * ``WS_SINGLE_PATH``, so the same `openEventsStream` client `ChatSidebar` uses
 * is the only supported reader. Reusing it means one parser, one reconnect
 * policy, one auth path — not a second event stack.
 *
 * Everything here is FAIL-SOFT by contract (web/AGENTS.md: panels must never
 * break the transcript). A stream failure clears activity and returns; it never
 * throws into React.
 */
import { useEffect, useRef, useState } from "react";
import {
  openEventsStream,
  parseSseChunk,
  type SseFrame,
} from "@/lib/events-sse";

export interface AgentActivityEntry {
  id: string;
  goal: string;
  status: string;
  lastTool?: string;
  toolCount?: number;
  depth?: number;
}

export interface TodoActivityEntry {
  id: string;
  text: string;
  done: boolean;
}

export interface AgentActivity {
  subagents: AgentActivityEntry[];
  todos: TodoActivityEntry[];
}

const EMPTY: AgentActivity = { subagents: [], todos: [] };

/** Reconnect backoff, capped — an idle page must not hammer the feed. */
const RECONNECT_MS = 3000;
const MAX_RECONNECT_MS = 30000;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/**
 * Normalize a `todo.updated` payload into panel rows.
 *
 * The wire shape is ``{todos: JsonValue[], revision: number}``; individual rows
 * are produced by the vendored `_normalize_todo_state`, whose field names are
 * producer-owned. Read the common spellings defensively rather than pinning one
 * — a shape change degrades to a text-only row instead of throwing.
 */
export function normalizeTodoState(payload: unknown): TodoActivityEntry[] {
  const rec = asRecord(payload);
  if (!rec) return [];
  const raw = rec.todos;
  if (!Array.isArray(raw)) return [];
  const out: TodoActivityEntry[] = [];
  raw.forEach((entry, index) => {
    const row = asRecord(entry);
    if (!row) return;
    const text =
      str(row.text) || str(row.content) || str(row.task) || str(row.title);
    if (!text) return;
    const status = str(row.status).toLowerCase();
    out.push({
      id: str(row.id) || str(row.task_id) || `todo-${index}`,
      text,
      done: status === "completed" || status === "done" || row.done === true,
    });
  });
  return out;
}

/**
 * Fold one `subagent.*` frame into the roster.
 *
 * `subagent.complete` removes the child (the roster is a LIVE view, matching
 * `subagent.list`; finished work belongs to the transcript, not this strip).
 */
export function reduceSubagentFrame(
  current: AgentActivityEntry[],
  eventName: string,
  payload: unknown,
): AgentActivityEntry[] {
  const rec = asRecord(payload);
  if (!rec) return current;
  const id = str(rec.subagent_id) || str(rec.delegation_id);
  if (!id) return current;
  if (eventName === "subagent.complete") {
    return current.filter((a) => a.id !== id);
  }
  const next: AgentActivityEntry = {
    id,
    goal: str(rec.goal),
    status: str(rec.status) || (eventName === "subagent.start" ? "running" : "running"),
    lastTool: str(rec.tool_name) || undefined,
    toolCount: num(rec.tool_count),
    depth: num(rec.depth),
  };
  const existing = current.findIndex((a) => a.id === id);
  if (existing < 0) return [...current, next];
  const copy = current.slice();
  // Never let a sparse later frame erase a field an earlier frame filled in
  // (progress frames omit `goal` for older emitters).
  copy[existing] = {
    ...copy[existing],
    ...Object.fromEntries(
      Object.entries(next).filter(([, v]) => v !== undefined && v !== ""),
    ),
  } as AgentActivityEntry;
  return copy;
}

/**
 * Subscribe to the session's live agent/todo activity.
 *
 * `session` scopes the panel; changing it resets state so one session's agents
 * never bleed into another's strip.
 */
export function useChatActivity(session: string, enabled = true): AgentActivity {
  const [activity, setActivity] = useState<AgentActivity>(EMPTY);
  const sessionRef = useRef(session);

  useEffect(() => {
    sessionRef.current = session;
    setActivity(EMPTY);
  }, [session]);

  useEffect(() => {
    if (!enabled || !session) return;
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastSeq: number | null = null;
    let backoff = RECONNECT_MS;

    const handleFrame = (frame: SseFrame) => {
      if (frame.id != null) lastSeq = frame.id;
      if (!frame.data) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(frame.data);
      } catch {
        return; // a non-JSON frame is not ours; never throw into React
      }
      const rec = asRecord(parsed);
      if (!rec) return;
      // The AG-UI envelope carries the event name; fall back to the SSE field.
      const name = str(rec.type) || str(rec.event) || frame.event;
      const payload = rec.data ?? rec.payload ?? rec;
      if (name === "todo.updated") {
        const todos = normalizeTodoState(payload);
        setActivity((prev) => ({ ...prev, todos }));
      } else if (name.startsWith("subagent.")) {
        setActivity((prev) => ({
          ...prev,
          subagents: reduceSubagentFrame(prev.subagents, name, payload),
        }));
      }
    };

    const connect = async () => {
      if (disposed) return;
      controller = new AbortController();
      const conn = await openEventsStream({ lastSeq, signal: controller.signal });
      if (disposed) return;
      if (!conn.ok) {
        // Auth failures cannot recover by retrying — stop rather than spin.
        if (conn.authRequired) return;
        timer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, MAX_RECONNECT_MS);
        return;
      }
      backoff = RECONNECT_MS;
      const reader = conn.reader;
      if (!reader) return;
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done || disposed) break;
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest, resync } = parseSseChunk(buffer);
          buffer = rest;
          if (resync) lastSeq = null;
          for (const frame of frames) handleFrame(frame);
        }
      } catch {
        // Abort (unmount) or a dropped connection — fall through to retry.
      }
      if (!disposed) timer = setTimeout(connect, backoff);
    };

    void connect();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
    };
  }, [session, enabled]);

  return activity;
}

export default useChatActivity;
