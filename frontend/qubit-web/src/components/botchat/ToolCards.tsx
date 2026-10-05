/**
 * Inline tool cards for the Qubit Bot transcript.
 *
 * Replacement for upstream `apps/mobile/src/*-tool-card.tsx` +
 * `agent-ui.tsx`'s `TaskCard`/`ArtifactCard` renderers that CopilotKit wired
 * through `useRenderTool`. The chat surface renders one of these per tool call
 * in a turn. Shapes/radii come from `BOT_GEOMETRY`; colours are Qubit theme
 * tokens via the `qb-*` classes, never upstream's light palette.
 *
 * Ported cards (upstream source in brackets):
 *   - SearchMailCard / ReadMailCard   [mail-tool-card.tsx]
 *   - BrowserToolCard                 [browser-tool-card.tsx]
 *   - ServerToolCard                  [chat.tsx `ServerToolCard`]
 *   - GenericToolCard                 [chat.tsx `useRenderToolCall` fallback]
 *   - TaskThreadCard / FileThreadCard [thread-artifacts.tsx]
 *   - ArtifactCard                    [agent-ui.tsx `ArtifactCard`]
 */

import { useState } from "react";
import {
  Check,
  ChevronRight,
  FileText,
  Globe2,
  ListChecks,
  Mail,
  Search,
} from "lucide-react";

import type { BotToolCardKind } from "@/lib/botchat/runtime";
import type { BotTask, RichCard, ThreadEvent } from "@/lib/ocFamilyBotApi";

/** Best-effort parse of a tool result that may be a JSON string or an object. */
function parseValue(result: unknown): Record<string, unknown> | undefined {
  if (result && typeof result === "object") return result as Record<string, unknown>;
  if (typeof result === "string") {
    try {
      const parsed = JSON.parse(result);
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : undefined;
    } catch {
      return undefined;
    }
  }
  return undefined;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Upstream `siteLabel`: a hostname, or a neutral fallback. */
function siteLabel(url: unknown): string {
  if (typeof url !== "string" || !url) return "Opening a page";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Opening a page";
  }
}

function TypingDots() {
  return (
    <span className="qb-tool-spinner" aria-hidden="true">
      <span className="qb-tool-spinner-dot" />
      <span className="qb-tool-spinner-dot" />
      <span className="qb-tool-spinner-dot" />
    </span>
  );
}

/** mail-tool-card.tsx — `search_mail` result summary. */
function SearchMailCard({ result }: { result: unknown }) {
  const value = parseValue(result);
  const error = str(value?.error);
  if (error) return <div className="qb-tool-error">{error}</div>;
  const matches = asArray(value?.matches);
  const truncated = value?.truncated === true;
  const count = matches.length;
  return (
    <div className="qb-tool-card qb-tool-card-inline" data-tool="search_mail">
      <span className="qb-tool-icon">
        <Search size={16} />
      </span>
      <span className="qb-tool-text">
        {count
          ? `Found ${truncated ? "at least " : ""}${count} ${count === 1 ? "email" : "emails"}`
          : "No matching emails"}
      </span>
    </div>
  );
}

/** mail-tool-card.tsx — `read_mail_thread` card. */
function ReadMailCard({ result }: { result: unknown }) {
  const value = parseValue(result);
  const error = str(value?.error);
  if (error) return <div className="qb-tool-error">{error}</div>;
  const messages = asArray(value?.messages);
  if (!messages.length) return <div className="qb-tool-text qb-tool-text-muted">No messages in this thread.</div>;
  const message = messages[messages.length - 1];
  const sender = str(message?.sender ?? message?.from) || "Email";
  const subject = str(message?.subject);
  const body = str(message?.body);
  const truncated = value?.truncated === true;
  return (
    <div className="qb-tool-card" data-tool="read_mail_thread">
      <div className="qb-tool-head">
        <span className="qb-tool-icon">
          <Mail size={20} />
        </span>
        <div className="qb-tool-head-main">
          <span className="qb-tool-title">{sender}</span>
          <span className="qb-tool-sub">
            Email · {messages.length === 1 ? "1 message" : `${messages.length} messages`}
          </span>
        </div>
      </div>
      {subject ? <p className="qb-tool-heading">{subject}</p> : null}
      {body ? <p className="qb-tool-body">{body}</p> : null}
      {truncated ? <p className="qb-tool-sub">Showing an excerpt of this thread.</p> : null}
    </div>
  );
}

/** browser-tool-card.tsx — `browse_web` card (no live preview: state only). */
function BrowserToolCard({ url, result, loading }: { url?: string; result: unknown; loading: boolean }) {
  const value = parseValue(result);
  const failure = str(value?.error);
  const visitedUrl = str(value?.url) || url || "";
  const visitedTitle = str(value?.title);
  return (
    <div className="qb-tool-card" data-tool="browse_web">
      <div className="qb-tool-head">
        <span className="qb-tool-icon qb-tool-icon-sm">
          <Globe2 size={21} />
        </span>
        <div className="qb-tool-head-main">
          <span className="qb-tool-title">Browser</span>
          <span className="qb-tool-sub">
            {loading
              ? "Reading the page…"
              : failure
                ? "Couldn’t read the page"
                : siteLabel(visitedUrl)}
          </span>
        </div>
        {loading ? <TypingDots /> : visitedUrl ? <Check size={17} className="qb-tool-check" /> : null}
      </div>
      <div className="qb-tool-preview">
        <p className="qb-tool-preview-title">{visitedTitle || siteLabel(url)}</p>
        {loading ? (
          <div className="qb-tool-skeleton">
            <span style={{ width: "90%" }} />
            <span style={{ width: "74%" }} />
            <span style={{ width: "84%" }} />
          </div>
        ) : (
          <p className="qb-tool-sub">The page was read on the server. Open the workspace for the full result.</p>
        )}
      </div>
    </div>
  );
}

/**
 * chat.tsx `ServerToolCard` — saved-work cards (delegate_task, agent_status,
 * remember_fact, create_goal, watch_page). When the result names a task we know
 * about, the task thread card is shown instead of the generic row.
 */
function ServerToolCard({
  name,
  result,
  loading,
  task,
}: {
  name: string;
  result: unknown;
  loading: boolean;
  task?: BotTask;
}) {
  if (task) return <TaskThreadCard task={task} />;
  const value = parseValue(result);
  const error = str(value?.error);
  return (
    <div className="qb-tool-card" data-tool={name.toLowerCase()}>
      <p className="qb-tool-heading">{loading ? `Saving ${name.toLowerCase()}…` : name}</p>
      <p className="qb-tool-body">
        {error
          ? error
          : loading
            ? "Waiting for the server."
            : "Open the workspace to see the saved result."}
      </p>
    </div>
  );
}

/** chat.tsx `useRenderToolCall` fallback — the generic tool-call card. */
function GenericToolCard({ event, loading }: { event: ThreadEvent; loading: boolean }) {
  return (
    <div className="qb-tool-card" data-tool={event.kind}>
      <div className="qb-tool-head">
        <span className="qb-call-badge">{event.kind}</span>
        {event.state ? <span className="qb-call-badge qb-call-badge-state">{event.state}</span> : null}
        {loading ? <TypingDots /> : null}
      </div>
      {event.title ? <p className="qb-tool-heading">{event.title}</p> : null}
      {event.text ? <p className="qb-tool-body">{event.text}</p> : null}
    </div>
  );
}

/** thread-artifacts.tsx `TaskThreadCard` — compact task card (plan progress). */
export function TaskThreadCard({ task }: { task: BotTask }) {
  const done = task.plan.filter((step) => step.status === "succeeded").length;
  const next = task.plan.find((step) => step.status === "running" || step.status === "waiting");
  const waiting = task.status === "waiting_input" || task.status === "waiting_approval";
  const percent = task.plan.length ? Math.round((done / task.plan.length) * 100) : 0;
  return (
    <div className="qb-thread-card" data-testid={`task-thread-card-${task.id}`}>
      <div className="qb-thread-card-head">
        <span className="qb-thread-icon">
          <ListChecks size={18} />
        </span>
        <div className="qb-thread-card-main">
          <span className="qb-tool-title">{task.title ?? task.id}</span>
          <span className="qb-tool-sub">
            {task.status ?? "task"}
            {task.plan.length ? ` · ${done}/${task.plan.length} steps` : ""}
          </span>
        </div>
        <ChevronRight size={17} className="qb-tool-check" />
      </div>
      {task.plan.length ? (
        <div className="qb-progress-track">
          <span className="qb-progress-fill" style={{ width: `${percent}%` }} />
        </div>
      ) : null}
      {task.result || task.error || next?.title ? (
        <p className="qb-tool-body">{task.error ?? task.result ?? next?.title}</p>
      ) : null}
      {waiting ? (
        <p className="qb-thread-waiting">
          {task.status === "waiting_approval" ? "Review requested" : "Your input is needed"}
        </p>
      ) : null}
    </div>
  );
}

/** thread-artifacts.tsx `FileThreadCard` — a produced document. */
export function FileThreadCard({ name, fields }: { name: string; fields?: Array<{ name: string; value?: string }> }) {
  return (
    <div className="qb-thread-card qb-file-card" data-testid="file-thread-card">
      <div className="qb-file-inner">
        <p className="qb-file-title">{name.replace(/\.pdf$/i, "")}</p>
        {fields?.length ? (
          fields.slice(0, 4).map((field) => (
            <div key={field.name} className="qb-file-field">
              <span className="qb-file-label">{field.name.replace(/_/g, " ").toUpperCase()}</span>
              <span className="qb-file-value">{field.value || "—"}</span>
            </div>
          ))
        ) : (
          <p className="qb-tool-sub">Tap to read the document</p>
        )}
      </div>
      <div className="qb-file-foot">
        <span className="qb-file-badge">
          <FileText size={23} />
        </span>
        <span className="qb-file-name">{name}</span>
        <span className="qb-tool-sub">PDF</span>
        <ChevronRight size={18} className="qb-tool-check" />
      </div>
    </div>
  );
}

/** agent-ui.tsx `ArtifactCard` — expandable rich result card. */
export function ArtifactCard({ artifact }: { artifact: RichCard }) {
  const [expanded, setExpanded] = useState(false);
  const rows = Object.entries((artifact.data as Record<string, unknown>) ?? {});
  const shown = expanded ? rows : rows.slice(0, 4);
  return (
    <div className="qb-artifact-card" data-testid={`artifact-card-${artifact.id}`}>
      <div className="qb-artifact-head">
        <p className="qb-tool-heading">{artifact.title ?? artifact.id}</p>
        {artifact.kind ? <span className="qb-chip">{artifact.kind}</span> : null}
      </div>
      {artifact.summary ? <p className="qb-tool-body">{artifact.summary}</p> : null}
      {shown.length ? (
        <div className="qb-artifact-rows">
          {shown.map(([key, value]) => (
            <div key={key} className="qb-artifact-row">
              <span className="qb-artifact-label">{key.replace(/_/g, " ")}</span>
              <span className="qb-artifact-value">
                {Array.isArray(value)
                  ? `${value.length} item${value.length === 1 ? "" : "s"}`
                  : typeof value === "object" && value !== null
                    ? JSON.stringify(value)
                    : String(value ?? "")}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {rows.length ? (
        <button type="button" className="qb-tool-button" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show summary" : "Explore full result"}
        </button>
      ) : null}
    </div>
  );
}

export interface ToolCardProps {
  kind: BotToolCardKind;
  event: ThreadEvent;
  result: unknown;
  loading: boolean;
  task?: BotTask;
}

/** Dispatch one transcript tool event to the registered card. */
export function ToolCard({ kind, event, result, loading, task }: ToolCardProps) {
  switch (kind) {
    case "mail-search":
      return <SearchMailCard result={result} />;
    case "mail-read":
      return <ReadMailCard result={result} />;
    case "browser":
      return <BrowserToolCard url={event.title} result={result} loading={loading} />;
    case "delegate":
      return <ServerToolCard name="Task" result={result} loading={loading} task={task} />;
    case "agent-status":
      return <ServerToolCard name="Agent progress" result={result} loading={loading} task={task} />;
    case "memory":
      return <ServerToolCard name="Memory" result={result} loading={loading} />;
    case "goal":
      return <ServerToolCard name="Goal" result={result} loading={loading} />;
    case "tracking":
      return <ServerToolCard name="Tracking" result={result} loading={loading} />;
    default:
      return <GenericToolCard event={event} loading={loading} />;
  }
}
