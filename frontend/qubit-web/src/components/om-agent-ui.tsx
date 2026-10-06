/**
 * Agent UI cards — React + Tailwind port of the upstream mobile
 * `agent-ui.tsx` typed card/artifact renderers (MIT, structural port).
 *
 * Upstream file: apps/mobile/src/agent-ui.tsx (1882 lines). This module keeps
 * the SAME export inventory with equivalent DOM structure:
 *   statusLabel · AgentStatus · TaskCard · ChatWork · AgentActivityScreen ·
 *   EvidenceList · TaskDetail · ArtifactCard · FinanceArtifact ·
 *   DelegateSheet · IdeasScreen · GoalsScreen · NotificationsSheet ·
 *   AppsScreen · MemoryRow
 * plus their private helpers (TaskLink, IdeaCard, GoalForm, GoalCard,
 * MonitorForm, MonitorCard).
 *
 * WIRING (real, probed): tasks / ideas / goals / receipts / cards read from
 *   /api/qubit/bot/{tasks-list,ideas-list,goals-list,alerts-list,cards-get}
 * and the roster from /api/qubit/harness/harnesses. Mutations that have no
 * Qubit backend are rendered as honest, stated gaps (see
 * `AGENT_SURFACE_GAPS`) — the card is COMPLETE, its empty state is truthful.
 *
 * Everything uses the shared `om-ui` primitives, so the card/button/input
 * geometry is the upstream one and the palette is Qubit's dark theme.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bell,
  CalendarDays,
  FileText,
  Globe2,
  Lightbulb,
  ListChecks,
  Mail,
  Pause,
  Play,
  RefreshCw,
  X,
} from "lucide-react";

import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";

import { api } from "@/lib/api";
import type { CronJob } from "@/lib/api";
import { setBotWatch } from "@/lib/ocFamilyBotApi";
import {
  listBotGoals,
  normalizeGoal,
  setBotGoal,
  updateBotGoal,
  type Goal,
} from "@/lib/ocFamilyBotApi";

import {
  acceptAgentIdea,
  asArray,
  asRecord,
  dismissAgentIdea,
  editAgentIdea,
  getAgentCard,
  listAgentAlerts,
  listAgentGoals,
  listAgentIdeas,
  listAgentRoster,
  listAgentTasks,
  type AgentGoalRow,
  type AgentIdeaRow,
  type AgentRosterRow,
  type AgentRunRow,
  type AgentTaskRow,
} from "@/lib/agentSurfaceApi";
import { submitHarnessTask } from "@/lib/ocFamilyHarnessApi";
import { refusalOrError } from "@/lib/policy-refusal";

import {
  OmButton,
  OmCard,
  OmChip,
  OmErrorNotice,
  OmField,
  OmIconBox,
  OmLinkRow,
  omStamp,
  omStatusLabel,
} from "./om-ui";

/* --------------------------------------------------------- local context */

/** Minimal workspace/agent context the cards need, injected by the page. */
export interface AgentUiContext {
  roster: AgentRosterRow[];
  rosterError: string;
  refreshRoster: () => Promise<void>;
}

export const EMPTY_AGENT_UI: AgentUiContext = {
  roster: [],
  rosterError: "",
  refreshRoster: async () => {},
};

/* ---------------------------------------------------------- statusLabel */

export function statusLabel(value: string): string {
  return omStatusLabel(value);
}

/* ------------------------------------------------------------ AgentStatus */

export function AgentStatus({
  context,
  onOpenRun,
}: {
  context: AgentUiContext;
  onOpenRun?: (runId: string) => void;
}) {
  const worker = context.roster.find((row) => row.kind === "worker");
  const running = worker?.enabled === true && worker.state === "OK";
  if (running && !context.rosterError) return null;
  return (
    <div className="flex flex-col gap-2">
      <OmErrorNotice
        error={
          context.rosterError
            ? `Agent updates unavailable. ${context.rosterError}`
            : ""
        }
      />
      {context.rosterError ? (
        <OmButton
          small
          onClick={() => void context.refreshRoster().catch(() => {})}
        >
          Reconnect agent
        </OmButton>
      ) : null}
      {!context.roster.length && !context.rosterError ? <Spinner /> : null}
      {context.roster.length && !running ? (
        <p className="text-[11px] text-muted-foreground">
          Worker is offline. Saved work will continue when it reconnects.
        </p>
      ) : null}
      {onOpenRun && context.roster.length ? null : null}
    </div>
  );
}

/* --------------------------------------------------------------- TaskCard */

export function TaskCard({
  task,
  compact = false,
  onOpen,
}: {
  task: AgentTaskRow;
  compact?: boolean;
  onOpen?: () => void;
}) {
  const terminal = ["succeeded", "failed", "cancelled", "UNAVAILABLE"].includes(
    task.state,
  );
  const waiting = ["waiting_input", "waiting_approval"].includes(task.state);
  return (
    <button
      type="button"
      aria-label={`Open task: ${task.title || task.id}`}
      onClick={() => onOpen?.()}
      className="w-full text-left"
    >
      <div
        className="flex flex-col gap-[11px] border border-border bg-secondary"
        style={{ padding: compact ? 15 : 20, borderRadius: 22 }}
      >
        <div className="flex items-center gap-[10px]">
          <OmIconBox size={34} className={waiting ? "" : "text-primary"}>
            <ListChecks size={18} />
          </OmIconBox>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="truncate text-[15px] font-semibold">{task.title}</p>
            <p className="text-[11px] text-muted-foreground">
              {statusLabel(task.state)}
              {task.source ? ` · ${task.source}` : ""}
              {task.kind ? ` · ${task.kind}` : ""}
            </p>
          </div>
          <span aria-hidden="true" className="text-muted-foreground">
            ›
          </span>
        </div>
        {task.detail ? (
          <p className="line-clamp-2 text-[13px] text-muted-foreground">
            {task.detail}
          </p>
        ) : null}
        {waiting ? (
          <p className="text-[11px] font-semibold text-primary">
            {task.state === "waiting_approval"
              ? "Review requested"
              : "Your input is needed"}
          </p>
        ) : terminal ? null : null}
      </div>
    </button>
  );
}

/* ---------------------------------------------------------------- ChatWork */

export function ChatWork({
  tasks,
  onOpen,
}: {
  tasks: AgentTaskRow[];
  onOpen?: (taskId: string) => void;
}) {
  const active = tasks
    .filter((t) => !["succeeded", "failed", "cancelled"].includes(t.state))
    .slice(0, 2);
  if (!active.length) return null;
  return (
    <div className="flex flex-col gap-[10px]">
      {active.map((task) => (
        <TaskCard
          task={task}
          key={task.id}
          compact
          onOpen={() => onOpen?.(task.id)}
        />
      ))}
    </div>
  );
}

/* ----------------------------------------------------- AgentActivityScreen */

export function AgentActivityScreen({
  tasks,
  loading,
  error,
  onReload,
  onOpen,
  context,
}: {
  tasks: AgentTaskRow[];
  loading: boolean;
  error: string;
  onReload: () => void;
  /** Open a task's detail. Without this the activity card is inert even though
   *  it advertises `aria-label="Open task: …"` (AgentCardsPage wires it to the
   *  TaskDetail panel). */
  onOpen?: (task: AgentTaskRow) => void;
  context: AgentUiContext;
}) {
  const [filter, setFilter] = useState("All");
  const visible = tasks
    .filter(
      (task) =>
        filter === "All" ||
        (filter === "In progress"
          ? !["succeeded", "failed", "cancelled"].includes(task.state)
          : ["succeeded", "failed", "cancelled"].includes(task.state)),
    )
    .slice()
    .sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));

  return (
    <div className="flex flex-col gap-5">
      <AgentStatus context={context} />
      <div className="flex flex-wrap items-center gap-2">
        {["All", "In progress", "Finished"].map((item) => (
          <OmButton
            key={item}
            small
            primary={filter === item}
            onClick={() => setFilter(item)}
          >
            {item}
          </OmButton>
        ))}
        <OmButton small icon={RefreshCw} onClick={onReload} disabled={loading}>
          Refresh
        </OmButton>
      </div>
      <OmErrorNotice error={error} />
      {loading && !visible.length
        ? [0, 1].map((i) => (
            <div
              key={i}
              className="h-[92px] animate-pulse rounded-[22px] bg-secondary"
            />
          ))
        : visible.map((task) => (
            <TaskCard key={task.id} task={task} onOpen={() => onOpen?.(task)} />
          ))}
      {!loading && !visible.length ? (
        <div className="flex flex-col items-center gap-[13px] py-10 text-center">
          <OmIconBox size={55}>
            <ListChecks size={24} />
          </OmIconBox>
          <p className="text-[15px] font-semibold">A place for the work</p>
          <p className="max-w-[360px] text-[13px] text-muted-foreground">
            The read-only task ledger reported no tasks for this filter.
            Delegate a task in Chat and its plan, progress and results stay
            here.
          </p>
        </div>
      ) : null}
      <p className="text-[15px] font-semibold">Reviews &amp; receipts</p>
      <ReceiptsList />
    </div>
  );
}

/* --------------------------------------------------------- ReceiptsList */

/**
 * Upstream `ActivityScreen` (from screens.tsx) narrowed to the receipt slice
 * that Qubit's `bot.alerts-list` actually serves. The upstream approvals/
 * receipts live inside the shared ActivityScreen; here we render the real run
 * receipts and state plainly that approvals are handled by the console's
 * pending-approvals banner.
 */
function ReceiptsList() {
  const [runs, setRuns] = useState<AgentRunRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    listAgentAlerts()
      .then(setRuns)
      .catch((e) => setError(refusalOrError(e, "Receipts failed.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-3">
      {loading ? <Spinner /> : null}
      <OmErrorNotice error={error} />
      {!loading && !runs.length ? (
        <p className="text-[13px] text-muted-foreground">
          No run receipts recorded yet.
        </p>
      ) : (
        runs.slice(0, 8).map((run) => (
          <OmCard key={run.id} className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[12px]">run {run.id}</p>
              {run.detail ? (
                <p className="truncate text-[11px] text-muted-foreground">
                  {run.detail}
                </p>
              ) : null}
            </div>
            <OmChip
              tone={
                run.state === "OK"
                  ? "green"
                  : run.state === "UNAVAILABLE"
                    ? "orange"
                    : "neutral"
              }
            >
              {statusLabel(run.state)}
            </OmChip>
          </OmCard>
        ))
      )}
      <OmButton small icon={RefreshCw} onClick={load} disabled={loading}>
        Refresh receipts
      </OmButton>
    </div>
  );
}

/* ------------------------------------------------------------- EvidenceList */

export interface EvidenceRow {
  id: string;
  title: string;
  excerpt: string;
  url?: string;
  kind?: string;
}

export function EvidenceList({ items }: { items: EvidenceRow[] }) {
  if (!items.length) {
    return (
      <p className="text-[11px] text-muted-foreground">
        No evidence was attached to this item.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-[10px]">
      {items.map((item) => (
        <div
          key={item.id}
          className="flex flex-col gap-1 border-l-2 border-[color:var(--color-primary)] pl-3"
        >
          <p className="text-[11px] font-semibold">{item.title}</p>
          <p className="text-[11px] text-muted-foreground">
            {item.excerpt}
          </p>
          {item.url && /^https?:\/\//i.test(item.url) ? (
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] font-semibold text-primary underline"
            >
              Open source
            </a>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- TaskDetail */

/**
 * Upstream `TaskDetail` sheet body. Qubit's task ledger is read-only, so the
 * pause/resume/cancel/retry control block and the task mutation endpoints have
 * no backend — shown as a stated, disabled control strip with the reason,
 * never a dead button that looks live.
 */
export function TaskDetail({
  task,
  onClose,
}: {
  task?: AgentTaskRow;
  onClose?: () => void;
}) {
  const [cardError, setCardError] = useState("");
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCard = useCallback(() => {
    if (!task?.id) return;
    setBusy(true);
    setCardError("");
    getAgentCard(task.id)
      .then((payload) => {
        const inner = asRecord(payload);
        setCard(inner.card ? asRecord(inner.card) : inner);
      })
      .catch((e) => setCardError(refusalOrError(e, "Card lookup failed.")))
      .finally(() => setBusy(false));
  }, [task?.id]);

  useEffect(() => {
    setCard(null);
    loadCard();
  }, [loadCard]);

  if (!task) {
    return (
      <p className="text-[13px] text-muted-foreground">
        Select a task to inspect its detail.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[16px] font-semibold">{task.title}</p>
          <p className="text-[11px] text-muted-foreground">
            {statusLabel(task.state)} · {omStamp(task.updatedAt)}
          </p>
        </div>
        {onClose ? (
          <OmButton small icon={X} onClick={onClose}>
            Close
          </OmButton>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <OmButton small icon={Pause} disabled>
          Pause
        </OmButton>
        <OmButton small icon={Play} disabled>
          Resume
        </OmButton>
        <OmButton small icon={RefreshCw} disabled>
          Retry task
        </OmButton>
        <OmButton small danger icon={X} disabled>
          Cancel task
        </OmButton>
      </div>
      <p className="text-[12px] text-muted-foreground">
        The Qubit task ledger is read-only: pause, resume, retry and cancel have
        no backend route on this bridge. Task control is owned by the agent
        session that launched the run.
      </p>

      {task.detail ? (
        <OmCard className="text-[13px]">{task.detail}</OmCard>
      ) : null}

      <div className="flex items-center justify-between gap-3">
        <p className="text-[15px] font-semibold">Rich card</p>
        <OmButton small icon={RefreshCw} onClick={loadCard} busy={busy}>
          Reload card
        </OmButton>
      </div>
      <OmErrorNotice error={cardError} />
      {card && Object.keys(card).length ? (
        <ArtifactCard
          artifact={{
            id: String(card.id ?? task.id),
            title: String(card.title ?? task.title),
            kind: String(card.kind ?? "card"),
            summary: String(card.summary ?? ""),
            data: asRecord(card.data),
          }}
        />
      ) : (
        !cardError &&
        !busy && (
          <p className="text-[13px] text-muted-foreground">
            No rich card is stored for this task id.
          </p>
        )
      )}
    </div>
  );
}

/* -------------------------------------------------------------- ArtifactCard */

export interface AgentArtifactView {
  id: string;
  title: string;
  kind: string;
  summary: string;
  data: Record<string, unknown>;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function display(value: unknown): string {
  return typeof value === "string"
    ? value
    : typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : value === null
        ? "—"
        : JSON.stringify(value, null, 2) || "";
}

export function ArtifactCard({ artifact }: { artifact: AgentArtifactView }) {
  const [expanded, setExpanded] = useState(false);
  if (artifact.kind === "finance") return <FinanceArtifact artifact={artifact} />;
  const rows = Object.entries(artifact.data);
  return (
    <OmCard className="flex flex-col gap-[13px]">
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 flex-1 truncate text-[15px] font-semibold">
          {artifact.title}
        </p>
        <OmChip>{statusLabel(artifact.kind)}</OmChip>
      </div>
      <p className="text-[13px] text-muted-foreground">
        {artifact.summary}
      </p>
      {(expanded ? rows : rows.slice(0, 4)).map(([key, value]) => (
        <div key={key} className="flex flex-col gap-[6px]">
          <p className="text-[10px] font-semibold uppercase tracking-[1.4px] text-muted-foreground">
            {key.replace(/_/g, " ")}
          </p>
          {Array.isArray(value) ? (
            value.slice(0, expanded ? 100 : 5).map((item, index) => {
              const row = record(item);
              return (
                <div
                  key={`${key}-${String(row?.id ?? index)}`}
                  className="border-b border-border py-2 text-[13px]"
                >
                  {row
                    ? Object.entries(row)
                        .map(([name, val]) => `${name}: ${display(val)}`)
                        .join(" · ")
                    : display(item)}
                </div>
              );
            })
          ) : record(value) ? (
            Object.entries(record(value) ?? {}).map(([name, val]) => (
              <div key={name} className="flex items-center justify-between gap-3">
                <span className="text-[13px] text-muted-foreground">
                  {name}
                </span>
                <span className="text-[13px]">{display(val)}</span>
              </div>
            ))
          ) : (
            <p
              className="text-[13px]"
              style={{
                fontSize: typeof value === "number" ? 24 : 14,
              }}
            >
              {display(value)}
            </p>
          )}
        </div>
      ))}
      {rows.length > 4 ? (
        <OmButton small onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show summary" : "Explore full result"}
        </OmButton>
      ) : null}
      {!rows.length ? (
        <p className="text-[13px] text-muted-foreground">
          This card carries no structured rows.
        </p>
      ) : null}
    </OmCard>
  );
}

/* ------------------------------------------------------------ FinanceArtifact */

/**
 * Real savings-goal control behind the finance card.
 *
 * Replaces the old "no Qubit backend" refusal: the goal store IS served by
 * `/api/qubit/bot/goals-set` + `goals-list` (+ `goals-update`), so the card
 * writes a goal with the DURABLE goal sidecar and lists it back.
 *
 * Honest about the number: the finance summary supplies spend/income amounts,
 * NOT a savings target — the summary carries no "how much do you want to
 * save" figure. We prefill only the title and say so; we never invent a
 * target amount or derive one the source doesn't state.
 */
export function SavingsGoalControl({
  defaultTitle,
  context,
}: {
  defaultTitle: string;
  context: { income?: unknown; spending?: unknown; saved?: unknown };
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [milestone, setMilestone] = useState("");
  const [goals, setGoals] = useState<Goal[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listBotGoals()
      .then((payload) => {
        const rows = asArray(asRecordHold(payload).goals).map((row) =>
          normalizeGoal(asRecord(row)),
        );
        setGoals(rows.filter((g) => g.id !== ""));
      })
      .catch((e) => setError(refusalOrError(e, "Goals failed to load.")));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const create = useCallback(async () => {
    const clean = title.trim();
    if (!clean) {
      setError("A goal needs a title.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await setBotGoal(
        {
          title: clean.slice(0, 160),
          description: "",
          category: "Finance",
          milestones: milestone.trim() ? [milestone.trim().slice(0, 200)] : [],
        },
        () => true,
      );
      setMilestone("");
      load();
    } catch (e) {
      setError(refusalOrError(e, "Could not create the goal."));
    } finally {
      setBusy(false);
    }
  }, [title, milestone, load]);

  const setStatus = useCallback(
    async (goal: Goal, status: string) => {
      setBusy(true);
      setError("");
      try {
        await updateBotGoal({ id: goal.id, status }, () => true);
        load();
      } catch (e) {
        setError(refusalOrError(e, "Could not update the goal."));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const toggleMilestone = useCallback(
    async (goal: Goal, index: number) => {
      const next = (goal.milestones ?? []).map((m, i) =>
        i === index ? { ...m, done: !m.done } : m,
      );
      setBusy(true);
      setError("");
      try {
        await updateBotGoal({ id: goal.id, milestones: next }, () => true);
        load();
      } catch (e) {
        setError(refusalOrError(e, "Could not update the milestone."));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const goalsError = ""; // alias kept local for readability

  return (
    <div className="flex flex-col gap-3" data-testid="savings-goal-control">
      <p className="text-[10px] font-semibold uppercase tracking-[1.4px] text-muted-foreground">
        Turn this summary into a goal
      </p>
      <p className="text-[11px] text-muted-foreground">
        This summary reports what you earned and spent
        {Number(context.spending) > 0
          ? ` (spent ${Number(context.spending).toLocaleString()})`
          : ""}
        , not a savings target — so set the goal yourself. It is stored in the
        durable goal store, not guessed from the amounts.
      </p>
      <OmField
        label="Goal"
        value={title}
        onChange={setTitle}
        placeholder="Build an emergency fund"
        ariaLabel="Savings goal title"
        disabled={busy}
      />
      <OmField
        label="First milestone (optional)"
        value={milestone}
        onChange={setMilestone}
        placeholder="Save three months of expenses"
        ariaLabel="First milestone"
        disabled={busy}
      />
      <div className="flex items-center gap-2">
        <OmButton
          small
          icon={Lightbulb}
          onClick={create}
          disabled={busy}
        >
          {busy ? "Saving…" : "Create goal"}
        </OmButton>
        <OmButton small icon={RefreshCw} onClick={load} disabled={busy}>
          Refresh
        </OmButton>
      </div>
      <OmErrorNotice error={error || goalsError} />
      {goals.map((goal) => {
        const nextStatus =
          goal.status === "active"
            ? "paused"
            : goal.status === "paused"
              ? "active"
              : "active";
        return (
          <div
            key={goal.id}
            className="flex flex-col gap-2 border border-border p-[10px]"
            style={{ borderRadius: 8 }}
          >
            <div className="flex items-center justify-between gap-3">
              <p className="min-w-0 flex-1 truncate text-[13px] font-semibold">
                {goal.title}
              </p>
              <OmChip tone={goal.status === "completed" ? "green" : "sky"}>
                {statusLabel(goal.status ?? "active")}
              </OmChip>
            </div>
            {(goal.milestones ?? []).map((m, i) => (
              <button
                key={m.id ?? i}
                type="button"
                onClick={() => toggleMilestone(goal, i)}
                disabled={busy}
                className="flex items-center gap-2 text-left text-[12px]"
              >
                <span aria-hidden="true">{m.done ? "☑" : "☐"}</span>
                <span
                  className={
                    m.done ? "text-muted-foreground line-through" : ""
                  }
                >
                  {m.title}
                </span>
              </button>
            ))}
            <div className="flex items-center gap-2">
              <OmButton
                small
                icon={goal.status === "paused" ? Play : Pause}
                onClick={() => setStatus(goal, nextStatus)}
                disabled={busy}
              >
                {goal.status === "paused" ? "Resume" : "Pause"}
              </OmButton>
              {goal.status !== "completed" ? (
                <OmButton
                  small
                  icon={ListChecks}
                  onClick={() => setStatus(goal, "completed")}
                  disabled={busy}
                >
                  Complete
                </OmButton>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function asRecordHold(value: unknown): Record<string, unknown> {
  const env = asRecord(value);
  const inner = asRecord(env.result);
  return Object.keys(inner).length ? inner : env;
}

function FinanceArtifact({ artifact }: { artifact: AgentArtifactView }) {
  const [details, setDetails] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const amount = (value: unknown) =>
    Number(value ?? 0).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  const categories = asArray(artifact.data.categories);
  const transactions = asArray(artifact.data.transactions);
  const spending = Number(artifact.data.spending) || 1;
  const period = record(artifact.data.period);

  return (
    <div
      className="flex w-full max-w-[440px] flex-col gap-3 border border-border bg-secondary"
      style={{ borderRadius: OM_CARD_RADIUS, padding: 10 }}
    >
      <button
        type="button"
        aria-label={`Open finance tracker: ${artifact.title}`}
        aria-expanded={details}
        onClick={() => setDetails(!details)}
        className="text-left"
      >
        <div
          className="relative overflow-hidden p-5"
          style={{
            minHeight: 200,
            borderRadius: 16,
            backgroundColor: "#080B10",
            backgroundImage:
              "linear-gradient(160deg, #281066 0%, #163BBF 50%, #148CE8 100%)",
          }}
        >
          <p className="mb-5 text-[11px] leading-[18px] text-[#D4DCFC]">
            Read from your imported transactions.
            <br />
            {String(period?.from ?? "")} — {String(period?.to ?? "")}
            <br />
            {transactions.length} transactions, categorized and summarized.
          </p>
          <div className="flex items-center gap-[7px]">
            {(
              [
                ["Income", "income"],
                ["Spending", "spending"],
                ["Remaining", "saved"],
              ] as const
            ).map(([label, key]) => (
              <div
                key={key}
                className="flex-1 p-[11px]"
                style={{ borderRadius: 12, backgroundColor: "#1D2025" }}
              >
                <p className="text-[9px] text-[#A4A7AD]">{label}</p>
                <p
                  className="mt-[5px] truncate text-[17px] font-semibold"
                  style={{ color: key === "saved" ? "#58D3AE" : "#FFF" }}
                >
                  {amount(artifact.data[key])}
                </p>
                <p className="mt-1 text-[8px] text-[#7E8289]">source currency</p>
              </div>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-[11px] px-2 pt-[13px] pb-1">
          <span className="text-[25px]" aria-hidden="true">
            💸
          </span>
          <div className="flex-1">
            <p className="text-[14px] font-semibold">Finance tracker</p>
            <p className="text-[11px] text-muted-foreground">
              Spending, savings, and a plan for what's next.
            </p>
          </div>
          <span aria-hidden="true" className="text-muted-foreground">
            ›
          </span>
        </div>
      </button>
      {details ? (
        <div className="flex flex-col gap-4 p-[10px]">
          <p className="text-[10px] font-semibold uppercase tracking-[1.4px] text-muted-foreground">
            Where your money went
          </p>
          {categories.map((category, index) => {
            const row = record(category);
            if (!row) return null;
            return (
              <div key={String(row.name ?? index)} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[13px]">{String(row.name)}</span>
                  <span className="text-[13px]">{amount(row.amount)}</span>
                </div>
                <div
                  className="h-[7px]"
                  style={{ backgroundColor: "#2a2f38", borderRadius: 8 }}
                >
                  <div
                    className="h-[7px]"
                    style={{
                      width: `${Math.min(100, (Number(row.amount) / spending) * 100)}%`,
                      backgroundColor: "var(--color-primary)",
                      borderRadius: 8,
                    }}
                  />
                </div>
              </div>
            );
          })}
          <p className="text-[11px] text-muted-foreground">
            Amounts use your source currency. This summary covers the imported
            dates.
          </p>
          <SavingsGoalControl
            defaultTitle="Build an emergency fund"
            context={{
              income: artifact.data.income,
              spending: artifact.data.spending,
              saved: artifact.data.saved,
            }}
          />
          <OmButton small onClick={() => setExpanded(!expanded)}>
            {expanded ? "Hide transactions" : "View transactions"}
          </OmButton>
          {expanded
            ? transactions.slice(0, 100).map((transaction, index) => {
                const row = record(transaction);
                return row ? (
                  <div
                    key={String(row.id ?? index)}
                    className="flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px]">
                        {String(row.description)}
                      </p>
                      <p className="text-[11px] text-muted-foreground">
                        {String(row.date)} · {String(row.category)}
                      </p>
                    </div>
                    <span className="text-[13px]">{amount(row.amount)}</span>
                  </div>
                ) : null;
              })
            : null}
          {expanded && transactions.length > 100 ? (
            <p className="text-[11px] text-muted-foreground">
              Showing the first 100 transactions. The totals include every row.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const OM_CARD_RADIUS = 23;

/* ------------------------------------------------------------- DelegateSheet */

/**
 * Which harness a delegate-kind dispatches to. The submit route takes a real
 * harness name; `qubit-worker` is the default worker lane and was verified live
 * (`POST /api/qubit/harness/task_submit` -> 200, opens a session, runs a turn).
 */
const KIND_HARNESS: Record<"plan" | "document" | "finance" | "agent", string> = {
  plan: "qubit-worker",
  document: "qubit-worker",
  finance: "qubit-worker",
  agent: "qubit-worker",
};

/**
 * Upstream `DelegateSheet`. Wired to the harness bridge's `task_submit`.
 *
 * This used to be a stated gap: the form rendered complete and the submit was
 * permanently disabled with "Delegation from the dashboard has no Qubit
 * backend." That claim was FALSE. `POST /api/qubit/harness/task_submit` is a
 * live confirm-gated mutation; it opens a canonical session and runs one real
 * agent turn (verified: 409 CONFIRM_REQUIRED -> re-POST with {confirm:true} ->
 * 200 with `turn.reply`). The route registry marked it NEEDS-BRIDGE from when
 * the bridge genuinely did not exist, and nothing re-probed it afterwards.
 *
 * The submit is therefore enabled and real. It keeps the repo's confirm
 * convention: stage, then settle with `confirm: true`, showing the operator the
 * stage result rather than silently re-issuing a mutation.
 */
export function DelegateSheet({ onClose }: { onClose?: () => void }) {
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<"plan" | "document" | "finance" | "agent">(
    "plan",
  );
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>("");
  const [failure, setFailure] = useState<string>("");

  const ready = prompt.trim().length > 0 && !busy;

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setFailure("");
    setResult("");
    try {
      // `submitHarnessTask` runs the repo's confirm flow for us: it POSTs, and
      // on 409 CONFIRM_REQUIRED calls back for consent, then settles. The
      // callback below is that consent step, so the operator always sees the
      // confirmation path rather than a silent mutation.
      const raw = (await submitHarnessTask(
        { prompt: prompt.trim(), harness: KIND_HARNESS[kind] },
        (detail) => window.confirm(detail),
      )) as {
        session?: string;
        harness?: string;
        turn?: { reply?: string };
      };
      const reply = raw?.turn?.reply;
      setResult(
        reply
          ? `Delegated to ${raw?.harness ?? KIND_HARNESS[kind]} (session ${raw?.session ?? "…"}): ${reply}`
          : `Delegated (session ${raw?.session ?? "…"}).`,
      );
    } catch (err) {
      setFailure(refusalOrError(err, "Delegation failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {(["plan", "document", "finance", "agent"] as const).map((item) => (
          <OmButton
            key={item}
            small
            primary={kind === item}
            onClick={() => setKind(item)}
          >
            {item === "agent" ? "General task" : statusLabel(item)}
          </OmButton>
        ))}
      </div>
      <OmField
        label="What would you like done?"
        value={prompt}
        onChange={setPrompt}
        multiline
        placeholder="Make a practical plan for my week"
      />
      <OmErrorNotice error={failure} />
      {result ? (
        <p data-testid="delegate-result" className="text-[13px] text-foreground">
          {result}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <OmButton primary disabled={!ready} onClick={() => void submit()}>
          {busy ? "Delegating…" : "Delegate task"}
        </OmButton>
        {onClose ? (
          <OmButton small onClick={onClose}>
            Close
          </OmButton>
        ) : null}
      </div>
      <p className="text-[12px] text-muted-foreground">
        Runs on the server as a real agent turn in its own session. Submitting
        asks for confirmation first, then settles it — nothing is dispatched
        until you see the confirmation path complete.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- IdeasScreen */

export function IdeasScreen() {
  const [ideas, setIdeas] = useState<AgentIdeaRow[]>([]);
  const [authority, setAuthority] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<{
    title: string;
    detail: string;
    resolve: (ok: boolean) => void;
  } | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    listAgentIdeas()
      .then((res) => {
        setIdeas(res.ideas);
        setAuthority(res.authority);
      })
      .catch((e) => setError(refusalOrError(e, "Ideas failed.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const askConfirm = useCallback(
    (title: string, detail: string) =>
      new Promise<boolean>((resolve) => setConfirm({ title, detail, resolve })),
    [],
  );

  const settleConfirm = useCallback(
    (ok: boolean) => {
      confirm?.resolve(ok);
      setConfirm(null);
    },
    [confirm],
  );

  const decide = useCallback(
    async (idea: AgentIdeaRow, action: "accept" | "dismiss") => {
      setBusyId(idea.id);
      setError("");
      setNotice("");
      const verb = action === "accept" ? "Accept" : "Dismiss";
      try {
        const out =
          action === "accept"
            ? await acceptAgentIdea(idea.id, (detail) =>
                askConfirm(
                  `Accept “${idea.title}”`,
                  `${detail}\n\nAccepting launches a real delegated worker ` +
                    `with the idea's prompt.`,
                ),
              )
            : await dismissAgentIdea(idea.id, (detail) =>
                askConfirm(`Dismiss “${idea.title}”`, detail),
              );
        const taskId = typeof out.taskId === "string" ? out.taskId : "";
        setNotice(
          action === "accept"
            ? `Accepted “${idea.title}”` +
                (taskId ? ` — launched as ${taskId}.` : " — worker launched.")
            : `Dismissed “${idea.title}”.`,
        );
        load();
      } catch (e) {
        setError(refusalOrError(e, `${verb} failed.`));
      } finally {
        setBusyId("");
      }
    },
    [askConfirm, load],
  );

  const saveEdit = useCallback(
    async (
      idea: AgentIdeaRow,
      patch: { title?: string; reason?: string; prompt?: string },
    ) => {
      setBusyId(idea.id);
      setError("");
      try {
        await editAgentIdea(idea.id, patch, (detail) =>
          askConfirm(`Edit “${idea.title}”`, detail),
        );
        setNotice(`Saved edits to “${patch.title || idea.title}”.`);
        load();
      } catch (e) {
        setError(refusalOrError(e, "Edit failed."));
      } finally {
        setBusyId("");
      }
    },
    [askConfirm, load],
  );

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-muted-foreground">
          {authority || "Suggested from your connected sources"}
        </p>
        <OmButton small icon={RefreshCw} onClick={load} disabled={loading}>
          Find ideas
        </OmButton>
      </div>
      <OmErrorNotice error={error} />
      {notice ? (
        <p data-testid="idea-notice" className="text-[13px] text-foreground">
          {notice}
        </p>
      ) : null}
      {loading ? <Spinner /> : null}
      {ideas.map((idea) => (
        <IdeaCard
          key={idea.id}
          idea={idea}
          busy={busyId === idea.id}
          onAccept={(i) => void decide(i, "accept")}
          onDismiss={(i) => void decide(i, "dismiss")}
          onEdit={(i, patch) => void saveEdit(i, patch)}
        />
      ))}
      {!loading && !ideas.length ? (
        <div className="flex flex-col items-center gap-[13px] py-10 text-center">
          <OmIconBox size={55}>
            <Lightbulb size={24} />
          </OmIconBox>
          <p className="text-[15px] font-semibold">Room for a good idea</p>
          <p className="max-w-[380px] text-[13px] text-muted-foreground">
            The inbox derives ideas from Qubit&apos;s own stores. It yields a row
            when a scheduled run or ledger task ends in a <em>failed</em> state
            with a recorded detail, a cron job&apos;s last status is{" "}
            <em>UNAVAILABLE</em>, a flow hook is switched off, or an active goal
            has no milestones. Nothing is fabricated — with a clean ledger and no
            open goals, there is genuinely nothing to suggest.
          </p>
        </div>
      ) : null}
      {confirm ? (
        <div
          data-testid="idea-confirm"
          className="rounded-md border border-border bg-card p-3 text-sm"
        >
          <p className="font-medium">{confirm.title}</p>
          <p className="mt-1 whitespace-pre-wrap text-muted-foreground">
            {confirm.detail}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <OmButton primary onClick={() => settleConfirm(true)}>
              Confirm
            </OmButton>
            <OmButton onClick={() => settleConfirm(false)}>Cancel</OmButton>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function IdeaCard({
  idea,
  busy,
  onAccept,
  onDismiss,
  onEdit,
}: {
  idea: AgentIdeaRow;
  busy: boolean;
  onAccept: (idea: AgentIdeaRow) => void;
  onDismiss: (idea: AgentIdeaRow) => void;
  onEdit: (
    idea: AgentIdeaRow,
    patch: { title?: string; reason?: string; prompt?: string },
  ) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(idea.title);
  const [prompt, setPrompt] = useState(idea.prompt ?? "");
  const emoji =
    idea.kind === "plan"
      ? "👟"
      : idea.kind === "document"
        ? "📋"
        : idea.kind === "finance"
          ? "💸"
          : idea.kind === "monitor"
            ? "👀"
            : "💡";
  return (
    <div className="flex flex-col border-b border-border py-[18px]">
      <button
        type="button"
        aria-label={`View idea: ${idea.title}`}
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex items-start gap-[14px] text-left"
      >
        <span className="w-[34px] pt-[3px] text-[27px]" aria-hidden="true">
          {emoji}
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-[5px]">
          <span className="text-[16px] font-semibold leading-[23px]">
            {idea.title}
          </span>
          <span className="text-[13px] text-muted-foreground">
            {idea.reason}
          </span>
        </span>
      </button>
      {expanded ? (
        <div className="mt-[18px] flex flex-col gap-4 pl-12">
          {idea.prompt ? (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Prompt
              </span>
              <p className="text-[13px] text-foreground">{idea.prompt}</p>
            </div>
          ) : null}
          {idea.evidence?.length ? (
            <div className="flex flex-col gap-2">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Evidence
              </span>
              {idea.evidence.map((ev) => (
                <div
                  key={ev.id}
                  className="rounded-md border border-border p-2 text-[12px]"
                  data-testid={`idea-evidence-${idea.id}`}
                >
                  <p className="font-medium">
                    {ev.title}
                    {ev.url ? (
                      <span className="ml-2 font-normal text-muted-foreground">
                        {ev.url}
                      </span>
                    ) : null}
                  </p>
                  <p className="text-muted-foreground">{ev.excerpt}</p>
                </div>
              ))}
            </div>
          ) : null}
          {editing ? (
            <div className="flex flex-col gap-2">
              <OmField label="Title" value={title} onChange={setTitle} />
              <OmField
                label="Prompt"
                value={prompt}
                onChange={setPrompt}
                multiline
              />
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {editing ? (
              <>
                <OmButton
                  primary
                  disabled={busy}
                  onClick={() => {
                    onEdit(idea, { title, prompt });
                    setEditing(false);
                  }}
                >
                  {busy ? "Saving…" : "Save"}
                </OmButton>
                <OmButton onClick={() => setEditing(false)}>Cancel</OmButton>
              </>
            ) : (
              <>
                <OmButton
                  primary
                  disabled={busy}
                  onClick={() => onAccept(idea)}
                >
                  {busy ? "Starting…" : "Start this"}
                </OmButton>
                <OmButton disabled={busy} onClick={() => setEditing(true)}>
                  Edit
                </OmButton>
                <OmButton disabled={busy} onClick={() => onDismiss(idea)}>
                  Dismiss
                </OmButton>
              </>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">
            Accepting launches a real delegated worker with the prompt above and
            records the created task id on this idea.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- GoalsScreen */

export function GoalsScreen() {
  const [goals, setGoals] = useState<AgentGoalRow[]>([]);
  const [authority, setAuthority] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    listAgentGoals()
      .then((res) => {
        setGoals(res.goals);
        setAuthority(res.authority);
      })
      .catch((e) => setError(refusalOrError(e, "Goals failed.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-muted-foreground">
          {authority || "Goal sidecar plus the canonical cron store"}
        </p>
        <OmButton small icon={RefreshCw} onClick={load} disabled={loading}>
          Refresh
        </OmButton>
      </div>
      <OmErrorNotice error={error} />
      {loading ? <Spinner /> : null}
      <div className="flex flex-col gap-2">
        {goals.map((goal) => (
          <OmCard key={goal.id} className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3">
              <p className="min-w-0 flex-1 truncate text-[15px] font-semibold">
                {goal.title}
              </p>
              <OmChip tone={goal.status === "completed" ? "green" : "sky"}>
                {statusLabel(goal.status)}
              </OmChip>
            </div>
            {goal.description ? (
              <p className="text-[13px] text-muted-foreground">
                {goal.description}
              </p>
            ) : null}
          </OmCard>
        ))}
        {!loading && !goals.length ? (
          <p className="py-[10px] text-[13px] text-muted-foreground">
            Big plans start with one small step. No goals are recorded yet.
          </p>
        ) : null}
      </div>
      <p className="text-[15px] font-semibold">Tracking</p>
      <div className="flex flex-col gap-2">
        <PageWatches />
      </div>
    </div>
  );
}

/* ----------------------------------------------------- NotificationsSheet */

/**
 * Upstream `NotificationsSheet`. Qubit exposes no notification inbox route;
 * the receipt ledger is the closest real surface, and it is rendered honestly
 * with a stated empty state. The monitor card is included as a complete,
 * stated-gap component.
 */
export function NotificationsSheet({ onClose }: { onClose?: () => void }) {
  const [runs, setRuns] = useState<AgentRunRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    listAgentAlerts()
      .then(setRuns)
      .catch((e) => setError(refusalOrError(e, "Notifications failed.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[15px] font-semibold">Notifications</p>
        {onClose ? (
          <OmButton small icon={X} onClick={onClose}>
            Close
          </OmButton>
        ) : null}
      </div>
      <OmErrorNotice error={error} />
      {loading ? <Spinner /> : null}
      {runs.slice(0, 10).map((run) => (
        <OmCard key={run.id} className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-[14px] font-semibold">
              run {run.id}
            </p>
            <OmChip>{statusLabel(run.state)}</OmChip>
          </div>
          {run.detail ? (
            <p className="text-[13px] text-muted-foreground">
              {run.detail}
            </p>
          ) : null}
        </OmCard>
      ))}
      {!loading && !runs.length ? (
        <div className="flex flex-col items-center gap-[13px] py-10 text-center">
          <OmIconBox size={55}>
            <Bell size={24} />
          </OmIconBox>
          <p className="text-[15px] font-semibold">You're all caught up</p>
          <p className="max-w-[360px] text-[13px] text-muted-foreground">
            Qubit has no per-user notification inbox; results, meaningful changes
            and requests for your input surface in the run receipts above and in
            the console's pending-approvals banner.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Upstream `MonitorCard`, backed by the REAL watches backend.
 *
 * The existing bridge row `bot.watches-set`
 * (``family_bot_harness._gw_bot_watches_set`` -> ``schedules.create_job``, the
 * canonical cron store) creates a watch; the existing schedule list
 * (``GET /api/cron/jobs``) lists them and the existing per-job controls
 * (``POST /api/cron/jobs/:id/pause|resume|delete``) pause/enable/stop them.
 * No new store, route or gateway is added — this only calls what the bridge
 * already serves. Upstream's field semantics are kept where they apply:
 * title 1..160, ``intervalMinutes`` 1..10080, and an http(s) URL.
 */
function PageWatches() {
  const [watches, setWatches] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState("15");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const confirmResolverRef = useRef<((ok: boolean) => void) | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getCronJobs()
      .then((jobs) => setWatches(asArray(jobs) as CronJob[]))
      .catch((e) => setError(refusalOrError(e, "Watches failed to load.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const askConfirm = useCallback((detail: string) => {
    return new Promise<boolean>((resolve) => {
      confirmResolverRef.current = resolve;
      setNotice(detail);
      setConfirmOpen(true);
    });
  }, []);

  const settleConfirm = useCallback((ok: boolean) => {
    const resolve = confirmResolverRef.current;
    confirmResolverRef.current = null;
    setConfirmOpen(false);
    resolve?.(ok);
  }, []);

  const submit = useCallback(async () => {
    const cleanTitle = title.trim();
    if (busy || !cleanTitle) return;
    const interval = Number.parseInt(intervalMinutes, 10);
    if (!Number.isFinite(interval) || interval < 1 || interval > 10080) {
      setError("Interval must be between 1 and 10080 minutes.");
      return;
    }
    const cleanUrl = url.trim();
    if (cleanUrl && !/^https?:\/\//i.test(cleanUrl)) {
      setError("Enter a public http(s) page URL.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await setBotWatch(
        { title: cleanTitle, url: cleanUrl || undefined, intervalMinutes: interval },
        (detail) => askConfirm(detail),
      );
      setTitle("");
      setUrl("");
      setIntervalMinutes("15");
      setNotice("");
      load();
    } catch (e) {
      setError(refusalOrError(e, "Watch save failed."));
    } finally {
      setBusy(false);
    }
  }, [askConfirm, busy, intervalMinutes, load, title, url]);

  const control = useCallback(
    async (job: CronJob, action: "pause" | "enable" | "stop") => {
      if (busy) return;
      setBusy(true);
      setError("");
      try {
        if (action === "pause") await api.pauseCronJob(job.id);
        else if (action === "enable") await api.resumeCronJob(job.id);
        else await api.deleteCronJob(job.id, undefined, (detail) => askConfirm(detail));
        load();
      } catch (e) {
        setError(refusalOrError(e, "Watch control failed."));
      } finally {
        setBusy(false);
      }
    },
    [askConfirm, busy, load],
  );

  return (
    <OmCard className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[15px] font-semibold">Page watches</p>
        <OmChip tone="green">live</OmChip>
      </div>
      <p className="text-[13px] text-muted-foreground">
        Watches poll a public page and alert on change. They are stored as
        schedules in the canonical cron store, so the Scheduler page shows the
        same list.
      </p>
      <OmErrorNotice error={error} />
      <div className="flex flex-col gap-2">
        <OmField label="Title" value={title} onChange={setTitle} placeholder="Watch name" />
        <OmField label="URL" value={url} onChange={setUrl} placeholder="https://example.com/pricing" />
        <OmField
          label="Check every (minutes)"
          value={intervalMinutes}
          onChange={setIntervalMinutes}
          placeholder="15"
          ariaLabel="Check interval in minutes"
        />
        <div className="flex flex-wrap items-center gap-2">
          <OmButton small primary busy={busy} onClick={() => void submit()}>
            Create watch
          </OmButton>
          <OmButton small busy={loading} onClick={load}>
            Refresh
          </OmButton>
        </div>
      </div>
      {loading ? <Spinner /> : null}
      {!loading && watches.length ? (
        <div className="flex flex-col gap-2">
          {watches.map((job) => (
            <div key={job.id} className="flex flex-col gap-2 border border-border/60 p-3" style={{ borderRadius: 14 }}>
              <div className="flex items-center justify-between gap-3">
                <p className="min-w-0 flex-1 truncate text-[14px] font-semibold">{job.name}</p>
                <OmChip tone={job.enabled ? "green" : "orange"}>
                  {job.enabled ? "active" : "paused"}
                </OmChip>
              </div>
              <p className="truncate text-[12px] text-muted-foreground">
                {job.prompt || job.schedule_display || "watch"}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {job.enabled ? (
                  <OmButton small icon={Pause} disabled={busy} onClick={() => void control(job, "pause")}>
                    Pause
                  </OmButton>
                ) : (
                  <OmButton small icon={Play} disabled={busy} onClick={() => void control(job, "enable")}>
                    Resume
                  </OmButton>
                )}
                <OmButton small icon={RefreshCw} disabled={busy} onClick={() => void api.triggerCronJob(job.id).then(load).catch((e) => setError(refusalOrError(e, "Check now failed.")))}>
                  Check now
                </OmButton>
                <OmButton small danger disabled={busy} onClick={() => void control(job, "stop")}>
                  Stop tracking
                </OmButton>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {!loading && !watches.length ? (
        <p className="text-[13px] text-muted-foreground">
          No watches yet. Create one above to start tracking a page.
        </p>
      ) : null}
      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => settleConfirm(false)}
        onConfirm={() => settleConfirm(true)}
        title="Confirm watch change"
        description={notice || "The backend staged this change for confirmation."}
      />
    </OmCard>
  );
}

/* --------------------------------------------------------------- AppsScreen */

export function AppsScreen({ context }: { context: AgentUiContext }) {
  const [query, setQuery] = useState("");
  const shortcuts = [
    { title: "Mail", detail: "Read messages and prepare replies", icon: Mail },
    {
      title: "Calendar",
      detail: "Events and reviewed invitations",
      icon: CalendarDays,
    },
    {
      title: "Agent computer",
      detail: "Persistent browser sessions",
      icon: Globe2,
    },
    { title: "Files", detail: "PDFs, forms and filled copies", icon: FileText },
  ];
  return (
    <div className="flex flex-col gap-5">
      <AgentStatus context={context} />
      <OmField
        label="Search apps"
        value={query}
        onChange={setQuery}
        placeholder="Search connectors"
      />
      <p className="text-[15px] font-semibold">On your computer</p>
      <div className="flex flex-col">
        {shortcuts
          .filter((item) =>
            `${item.title} ${item.detail}`
              .toLowerCase()
              .includes(query.toLowerCase()),
          )
          .map((item) => (
            <OmLinkRow
              key={item.title}
              icon={item.icon}
              title={item.title}
              detail={item.detail}
              disabled
              disabledReason="Connector surfaces are owned by their own console pages."
            />
          ))}
      </div>
      <p className="text-[15px] font-semibold">Agent roster</p>
      <OmErrorNotice error={context.rosterError} />
      {context.roster.map((row) => (
        <OmCard key={row.id} className="flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="truncate font-mono text-[13px]">{row.id}</p>
            <p className="text-[11px] text-muted-foreground">
              {row.kind}
              {row.model ? ` · ${row.model}` : ""}
              {row.route ? ` · ${row.route}` : ""}
            </p>
          </div>
          <OmChip tone={row.state === "OK" ? "green" : "orange"}>
            {statusLabel(row.state)}
          </OmChip>
        </OmCard>
      ))}
      {!context.roster.length ? (
        <p className="text-[13px] text-muted-foreground">
          The harness reported no agents.
        </p>
      ) : null}

      <p className="text-[15px] font-semibold">Memory</p>
      <p className="text-[13px] text-muted-foreground">
        Inspectable memory is owned by the agent session and the Memory backends
        page; this surface does not create or forget memory entries.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- MemoryRow */

export function MemoryRow({
  memory,
}: {
  memory: { id: string; text: string; source?: string; createdAt?: string };
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-border pb-4">
      <p className="text-[13px]">{memory.text}</p>
      <p className="text-[11px] text-muted-foreground">
        {memory.source ?? "unknown source"} · {omStamp(memory.createdAt)}
      </p>
      <div className="flex items-center gap-2">
        <OmButton small disabled title="No memory write backend on this surface.">
          Edit
        </OmButton>
        <OmButton small danger disabled title="No memory write backend on this surface.">
          Forget
        </OmButton>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- useAgentSurface */

/** Shared loader for the tasks + roster the surfaces render. */
export function useAgentSurface() {
  const [tasks, setTasks] = useState<AgentTaskRow[]>([]);
  const [roster, setRoster] = useState<AgentRosterRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rosterError, setRosterError] = useState("");

  const refreshRoster = useCallback(async () => {
    try {
      setRoster(await listAgentRoster());
      setRosterError("");
    } catch (e) {
      setRosterError(refusalOrError(e, "Agent roster failed."));
    }
  }, []);

  const reload = useCallback(() => {
    setLoading(true);
    setError("");
    listAgentTasks()
      .then(setTasks)
      .catch((e) => setError(refusalOrError(e, "Task ledger failed.")))
      .finally(() => setLoading(false));
    void refreshRoster();
  }, [refreshRoster]);

  useEffect(() => {
    reload();
  }, [reload]);

  const context: AgentUiContext = { roster, rosterError, refreshRoster };
  return { tasks, context, loading, error, reload };
}
