/**
 * Transcript — the workbench's centre panel.
 *
 * Renders the messages the backend returned for the selected session, plus any
 * turn this page submitted in the current visit (marked, so a local append is
 * never mistaken for a persisted row). Message bodies go through the house
 * `Markdown` renderer — no `dangerouslySetInnerHTML` anywhere on this surface.
 *
 * Structure ported from the absorbed console's message list
 * (`static/ui.js:15782` role headers; `:16867-16960` the per-turn `Trace:` bar
 * and collapsible event rows; `style.css:4446-4520` their rhythm):
 *
 * - a role header with a disc, the role's name and, for assistants, the model
 *   that produced the row;
 * - a `Trace: N steps · Expand all / Collapse all` bar over the row's
 *   collapsible steps (its stored reasoning, and each tool call it made);
 * - user rows right-aligned in a bubble, assistant rows full width on a rail.
 *
 * **Why there is no percentage beside the name.** The absorbed console printed
 * `Name 66.7%` because ITS server reports a per-turn confidence. This backend
 * does not: `GET /api/workbench/sessions/<id>` returns `role`, `content`,
 * `timestamp` and `meta`, and `meta` carries ids, finish reasons, tool calls
 * and reasoning — never a score. A percentage here would be invented, so the
 * header renders the model name alone. If the seam ever adds `meta.confidence`
 * (0-1), `assistantDisplayName` is the single place the suffix would land.
 *
 * The empty state is reachable only when a read SUCCEEDED and returned no
 * messages. A missing or failed route renders its own state instead, because
 * "this session has no messages" and "I could not ask" are different claims.
 */

import { ChevronDown, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Markdown } from "@/components/Markdown";
import type {
  ApiResult,
  WorkbenchMessage,
  WorkbenchSessionDetail,
} from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { compactAge } from "./session-groups";
import { ResultGate } from "./WorkbenchStates";
import { ROLE_DISC } from "./workbench-theme";

/**
 * The label an assistant row is headed with. The store names no agent, so the
 * assistant is "Qubit" plus the model that produced the row.
 */
function assistantDisplayName(model: string | null): string {
  const bare = model?.split("/").pop()?.trim();
  return bare ? `Qubit · ${bare}` : "Qubit";
}

/** Absolute timestamp for a header tooltip. */
function absoluteTime(ts: number | null): string | undefined {
  if (ts === null) return undefined;
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return undefined;
  }
}

interface TraceStepData {
  id: string;
  kind: "thinking" | "tool";
  title: string;
  meta: string | null;
  body: string;
}

/** One collapsible step inside a turn (a reasoning block or a tool call). */
function TraceStep({
  step,
  open,
  onToggle,
}: {
  step: TraceStepData;
  open: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <div
      data-testid={`workbench-trace-step-${step.kind}`}
      data-expanded={open ? "1" : "0"}
      className={`mb-px rounded-md border transition-colors ${
        open ? "border-border/70 bg-secondary/30" : "border-transparent hover:border-border/60"
      }`}
    >
      <button
        type="button"
        onClick={() => onToggle(step.id)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-1.5 py-1 text-left text-[11px] text-text-tertiary hover:text-foreground"
      >
        <ChevronDown
          aria-hidden="true"
          className={`h-3 w-3 shrink-0 transition-transform ${open ? "" : "-rotate-90"}`}
        />
        {step.kind === "tool" ? <Wrench className="h-3 w-3 shrink-0" aria-hidden="true" /> : null}
        <span className="min-w-0 flex-1 truncate">{step.title}</span>
        {step.meta ? <span className="shrink-0 tabular-nums opacity-70">{step.meta}</span> : null}
      </button>
      {open ? (
        // Plain pre-formatted text: reasoning and tool arguments are model
        // output, not markdown to interpret.
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2 pb-2 font-mono-ui text-[11px] leading-snug text-text-secondary">
          {step.body}
        </pre>
      ) : null}
    </div>
  );
}

/** The `Trace:` bar above a turn's steps. */
function TraceBar({
  count,
  allOpen,
  onExpandAll,
  onCollapseAll,
}: {
  count: number;
  allOpen: boolean;
  onExpandAll: () => void;
  onCollapseAll: () => void;
}) {
  return (
    <div
      data-testid="workbench-trace-controls"
      className="mb-0.5 flex items-center gap-1.5 text-[11px] leading-tight text-text-tertiary"
    >
      <span className="font-semibold">Trace:</span>
      <span className="tabular-nums">
        {count} step{count === 1 ? "" : "s"}
      </span>
      <button
        type="button"
        onClick={onExpandAll}
        disabled={allOpen}
        className="underline-offset-[3px] transition-colors hover:text-foreground hover:underline disabled:opacity-40"
      >
        Expand all
      </button>
      <span aria-hidden="true" className="opacity-50">
        /
      </span>
      <button
        type="button"
        onClick={onCollapseAll}
        disabled={!allOpen}
        className="underline-offset-[3px] transition-colors hover:text-foreground hover:underline disabled:opacity-40"
      >
        Collapse all
      </button>
    </div>
  );
}

function RoleDisc({ role }: { role: string }) {
  const initial =
    role === "assistant" ? "Q" : role === "user" ? "U" : role.slice(0, 1).toUpperCase();
  const tone =
    role === "assistant"
      ? "border-border bg-secondary/50 text-text-secondary"
      : "border-primary/30 bg-primary/15 text-primary";
  return (
    <span className={`${ROLE_DISC} ${tone}`} aria-hidden="true">
      {initial}
    </span>
  );
}

/** One transcript row. `local` marks a reply this visit produced. */
function MessageRow({
  message,
  model,
  local,
  now,
  testId,
}: {
  message: WorkbenchMessage;
  model: string | null;
  local: boolean;
  now: number;
  testId: string;
}) {
  const [openSteps, setOpenSteps] = useState<ReadonlySet<string>>(() => new Set());

  const steps = useMemo<TraceStepData[]>(() => {
    const out: TraceStepData[] = [];
    if (message.reasoning) {
      out.push({
        id: "thinking",
        kind: "thinking",
        title: "Thinking",
        meta:
          message.reasoning.seconds !== null ? `${message.reasoning.seconds.toFixed(1)}s` : null,
        body: message.reasoning.text,
      });
    }
    message.toolCalls.forEach((call, index) => {
      out.push({
        id: `tool-${index}`,
        kind: "tool",
        title: call.name,
        meta: null,
        body: call.args ?? "(the backend stored no arguments for this call)",
      });
    });
    return out;
  }, [message.reasoning, message.toolCalls]);

  const allOpen = steps.length > 0 && steps.every((step) => openSteps.has(step.id));
  const toggleStep = (id: string) =>
    setOpenSteps((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const age = compactAge(message.timestamp, now);

  if (message.role === "user") {
    return (
      <div className="mb-3 flex justify-end" data-testid={testId}>
        <div className="flex max-w-[85%] min-w-0 flex-col items-end gap-1">
          <div className="min-w-0 rounded-2xl rounded-br-md border border-primary/25 bg-primary/[0.10] px-3 py-2">
            <Markdown content={message.content} />
          </div>
          {local || age ? (
            <span className="flex items-center gap-1.5 pr-1 text-[10px] text-text-tertiary">
              {local ? <span className="italic">not yet stored</span> : null}
              {age ? <span title={absoluteTime(message.timestamp)}>{age}</span> : null}
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  const isAssistant = message.role === "assistant";
  return (
    <div className="mb-3" data-testid={testId}>
      <div className="mb-1 flex items-center gap-1.5" title={absoluteTime(message.timestamp)}>
        <RoleDisc role={message.role} />
        <span
          className={`min-w-0 truncate text-[12px] font-medium ${
            isAssistant ? "text-text-secondary" : "text-text-tertiary"
          }`}
        >
          {isAssistant ? assistantDisplayName(model) : message.role}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 text-[10px] text-text-tertiary">
          {message.toolName ? <span className="font-mono-ui">{message.toolName}</span> : null}
          {local ? <span className="italic">not yet stored</span> : null}
          {age ? <span>{age}</span> : null}
        </span>
      </div>

      <div className="pl-[30px]">
        {steps.length > 0 ? (
          <>
            <TraceBar
              count={steps.length}
              allOpen={allOpen}
              onExpandAll={() => setOpenSteps(new Set(steps.map((step) => step.id)))}
              onCollapseAll={() => setOpenSteps(new Set())}
            />
            {steps.map((step) => (
              <TraceStep
                key={step.id}
                step={step}
                open={openSteps.has(step.id)}
                onToggle={toggleStep}
              />
            ))}
            <div className="h-1.5" aria-hidden="true" />
          </>
        ) : null}
        <Markdown content={message.content} />
      </div>
    </div>
  );
}

function TranscriptBody({
  detail,
  model,
  draftReply,
  now,
}: {
  detail: WorkbenchSessionDetail;
  model: string | null;
  draftReply: string | null;
  now: number;
}) {
  if (detail.messages.length === 0 && draftReply === null) {
    return (
      <p data-testid="workbench-transcript-empty" className="py-6 text-xs text-text-secondary">
        This session has no messages yet. Anything you send below starts the
        conversation.
      </p>
    );
  }
  return (
    <div aria-label="Conversation">
      {detail.messages.map((msg, index) => (
        <MessageRow
          key={`${msg.role}-${index}`}
          message={msg}
          model={msg.role === "assistant" ? model : null}
          local={false}
          now={now}
          testId={`workbench-message-${msg.role}`}
        />
      ))}
      {draftReply !== null ? (
        <MessageRow
          message={{
            role: "assistant",
            content: draftReply,
            timestamp: null,
            reasoning: null,
            toolCalls: [],
            toolName: null,
          }}
          model={model}
          local
          now={now}
          testId="workbench-message-local-reply"
        />
      ) : null}
    </div>
  );
}

/** The refresh arc in the transcript header. */
function RefreshGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

export function Transcript({
  title,
  model,
  result,
  draftReply,
  submitting,
  onReload,
  composer,
}: {
  title: string | null;
  /** The session's model, for the assistant header. */
  model: string | null;
  result: ApiResult<WorkbenchSessionDetail> | null;
  /** Reply produced by a POST /turn in this visit; not yet in the backend read. */
  draftReply: string | null;
  submitting: boolean;
  onReload: () => void;
  /** Composer, rendered by the page and docked to the footer of this panel. */
  composer: ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const now = Date.now();

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [result, draftReply]);

  // The count is a fact only when a read succeeded, so it is read off the
  // successful branch and shown in the header — where the console showed it.
  const data = result !== null && result.state === "ok" ? result.data : null;
  const count = data?.messageTotal ?? data?.messages.length ?? null;

  return (
    <section
      aria-label="Transcript"
      data-testid="workbench-transcript"
      className="flex min-h-0 min-w-0 flex-1 flex-col"
    >
      <div className="flex min-h-[2.5rem] shrink-0 items-center gap-2 border-b border-border px-2 pl-3">
        <div className="min-w-0 flex-1">
          <h2
            className="truncate text-[13px] font-medium text-foreground"
            title={title ?? undefined}
          >
            {title?.trim() || "Transcript"}
          </h2>
          <p
            className="truncate text-[10px] text-text-tertiary"
            data-testid="workbench-transcript-meta"
          >
            {count !== null ? `${count} message${count === 1 ? "" : "s"}` : "no session open"}
            {model ? <span className="font-mono-ui"> · {model}</span> : null}
          </p>
        </div>
        <button
          type="button"
          onClick={onReload}
          aria-label="Refresh transcript"
          title="Refresh transcript"
          data-testid="workbench-refresh-transcript"
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-secondary/50 hover:text-foreground [&>svg]:h-3.5 [&>svg]:w-3.5"
        >
          <RefreshGlyph />
        </button>
      </div>

      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {result !== null ? (
          <ResultGate
            result={result}
            copy={PANEL_COPY.transcript}
            loadingText="Reading transcript…"
            onRetry={onReload}
          >
            {(detail) => (
              <TranscriptBody detail={detail} model={model} draftReply={draftReply} now={now} />
            )}
          </ResultGate>
        ) : (
          <p data-testid="workbench-transcript-idle" className="py-6 text-xs text-text-secondary">
            No session open yet. Pick one on the left.
          </p>
        )}
        {submitting ? (
          <p className="text-xs text-text-secondary" role="status">
            Waiting for the reply…
          </p>
        ) : null}
      </div>

      <div className="shrink-0 border-t border-border">{composer}</div>
    </section>
  );
}

export default Transcript;
