/**
 * Transcript — the workbench's centre panel.
 *
 * Renders the messages the backend returned for the selected session, plus any
 * turn this page has itself submitted in the current visit (marked, so a user
 * can tell a local append from a persisted row). Message bodies go through the
 * house `Markdown` renderer — no `dangerouslySetInnerHTML` anywhere on this
 * surface.
 *
 * The empty state is reachable only when a read SUCCEEDED and returned no
 * messages. A missing or failed route renders its own state instead, because
 * "this session has no messages" and "I could not ask" are different claims.
 */

import { useEffect, useRef, type ReactNode } from "react";

import { Markdown } from "@/components/Markdown";
import { timeAgo } from "@/lib/utils";
import type {
  ApiResult,
  WorkbenchMessage,
  WorkbenchSessionDetail,
} from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { PanelHeading, ResultGate } from "./WorkbenchStates";

const ROLE_STYLE: Record<string, string> = {
  user: "bg-primary/10 text-primary",
  assistant: "bg-success/10 text-success",
  system: "bg-muted text-muted-foreground",
  tool: "bg-warning/10 text-warning",
};

function roleClass(role: string): string {
  return ROLE_STYLE[role] ?? ROLE_STYLE.system;
}

function MessageRow({
  role,
  content,
  timestamp,
  note,
  testId,
}: {
  role: string;
  content: string;
  timestamp: number | null;
  /** Extra provenance tag, e.g. "this turn" for a local append. */
  note?: string;
  testId: string;
}) {
  return (
    <div className="mb-3 flex flex-col gap-1" data-testid={testId}>
      <span className="flex items-center gap-2">
        <span
          className={`px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${roleClass(role)}`}
        >
          {role}
        </span>
        {timestamp !== null ? (
          <span className="text-[11px] text-text-tertiary">{timeAgo(timestamp)}</span>
        ) : null}
        {note ? <span className="text-[11px] text-text-tertiary">{note}</span> : null}
      </span>
      <Markdown content={content} />
    </div>
  );
}

function TranscriptBody({
  detail,
  draftReply,
}: {
  detail: WorkbenchSessionDetail;
  draftReply: string | null;
}) {
  const messages: WorkbenchMessage[] = detail.messages;
  if (messages.length === 0) {
    return (
      <p
        data-testid="workbench-transcript-empty"
        className="py-6 text-xs text-text-secondary"
      >
        This session has no messages yet. Anything you send below starts the
        conversation.
      </p>
    );
  }
  return (
    <div role="list" aria-label="Conversation">
      {messages.map((msg, index) => (
        <MessageRow
          key={`${msg.role}-${index}`}
          role={msg.role}
          content={msg.content}
          timestamp={msg.timestamp}
          testId={`workbench-message-${msg.role}`}
        />
      ))}
      {draftReply !== null ? (
        <MessageRow
          role="assistant"
          content={draftReply}
          timestamp={null}
          note="this turn"
          testId="workbench-message-local-reply"
        />
      ) : null}
    </div>
  );
}

export function Transcript({
  title,
  result,
  draftReply,
  submitting,
  onReload,
  composer,
}: {
  title: string | null;
  result: ApiResult<WorkbenchSessionDetail> | null;
  /** Reply produced by a POST /turn in this visit; not yet in the backend read. */
  draftReply: string | null;
  submitting: boolean;
  onReload: () => void;
  /** Composer, rendered by the page and docked to the footer of this panel. */
  composer: ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [result, draftReply]);

  return (
    <section
      aria-label="Transcript"
      data-testid="workbench-transcript"
      className="flex h-full min-h-0 min-w-0 flex-col"
    >
      <PanelHeading title={title?.trim() || "Transcript"} />

      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {result !== null ? (
          <ResultGate
            result={result}
            copy={PANEL_COPY.transcript}
            loadingText="Reading transcript…"
            onRetry={onReload}
          >
            {(detail) => <TranscriptBody detail={detail} draftReply={draftReply} />}
          </ResultGate>
        ) : (
          <p
            data-testid="workbench-transcript-idle"
            className="py-6 text-xs text-text-secondary"
          >
            No session open yet. Pick one on the left, or send a message below to
            start a new conversation.
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
