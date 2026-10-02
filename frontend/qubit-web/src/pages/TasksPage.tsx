/**
 * Background tasks — the upstream background-tasks depth as its own page.
 *
 * Qubit-owned page (route `/qubit-tasks`; route registration in `App.tsx` is
 * a NEEDS-BRIDGE item — this file only exports the component).
 *
 * Separate from `/qubit-task-inbox` (which this page never extends): the
 * inbox covers ledger history + suggestions + mentions, while this page
 * mirrors the upstream tasks surface (`docs/automation/tasks.md`: list with a
 * state filter, inspect by receipt/session id, cancel + retry, progress where
 * the backend reports it).
 *
 * Port mapping: list/get onto the read-only task ledger
 * (`voicebridge/task_ledger.py`: `list_tasks` / `show`); cancel/retry onto a
 * real action receipt or an in-flight chat session
 * (`voicebridge.actions.ActionStore.cancel` / `.retry`) through the shared
 * confirmation round-trip. `tasks.dismiss` keeps no delivery registry, so it
 * is disabled-with-reason and never wired.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { Link } from "react-router";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import {
  QUBIT_TASK_STATES,
  TASKS_DISMISS_REFUSAL,
  cancelBackgroundTask,
  getBackgroundTask,
  listBackgroundTasks,
  normalizeBackgroundTaskDetail,
  normalizeBackgroundTasks,
  retryBackgroundTask,
  type BackgroundTaskDetail,
  type GatewayRefusal,
} from "@/lib/ocFamilyTasksApi";
import { refusalOrError } from "@/lib/policy-refusal";

function DismissRefusalRow({ refusal }: { refusal: GatewayRefusal }) {
  return (
    <div
      className="rounded-md border border-dashed p-3 space-y-1"
      data-testid={`refusal-${refusal.code}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <code className="text-xs font-mono">{refusal.method}</code>
        <Badge tone="secondary">{refusal.code}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{refusal.detail}</p>
      <p className="text-sm">
        <span className="font-medium">Needs: </span>
        {refusal.prerequisite}
      </p>
      <p className="text-sm">
        <span className="font-medium">Use instead: </span>
        <code className="text-xs font-mono">{refusal.counterpart}</code>
        {" — "}
        <Link className="underline" to="/qubit-task-inbox">
          Open the task inbox
        </Link>
      </p>
      <Button disabled title={refusal.detail}>
        Run {refusal.method}
      </Button>
    </div>
  );
}

function fmtStamp(value: string | number | undefined): string {
  if (value === undefined || value === "") return "";
  if (typeof value === "number") {
    const ms = value < 1e12 ? value * 1000 : value;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
  }
  return value;
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<ReturnType<typeof normalizeBackgroundTasks>>([]);
  const [stateFilter, setStateFilter] = useState("");
  const [lookup, setLookup] = useState("");
  const [detail, setDetail] = useState<BackgroundTaskDetail | null>(null);
  const [detailMissing, setDetailMissing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { toast, showToast } = useToast();

  const [receiptId, setReceiptId] = useState("");
  const [session, setSession] = useState("");
  const [taskId, setTaskId] = useState("");

  const [brokerConfirm, setBrokerConfirm] = useState<{
    title: string;
    description: string;
  } | null>(null);
  const confirmResolverRef = useRef<((ok: boolean) => void) | null>(null);

  const askBrokerConfirm = useCallback((title: string, description: string) => {
    return new Promise<boolean>((resolve) => {
      confirmResolverRef.current = resolve;
      setBrokerConfirm({ title, description });
    });
  }, []);

  const settleBrokerConfirm = useCallback((ok: boolean) => {
    const resolve = confirmResolverRef.current;
    confirmResolverRef.current = null;
    setBrokerConfirm(null);
    resolve?.(ok);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ledger = await listBackgroundTasks(
        stateFilter ? { state: stateFilter } : undefined,
      );
      setTasks(normalizeBackgroundTasks(ledger));
    } catch (e) {
      const message = refusalOrError(e, "Failed to load background tasks.");
      setError(message);
      showToast(message, "error");
    } finally {
      setLoading(false);
    }
  }, [showToast, stateFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const lookUp = useCallback(async () => {
    const q = lookup.trim();
    if (busy || !q) return;
    setBusy(true);
    setError("");
    setDetail(null);
    setDetailMissing(false);
    try {
      const payload = await getBackgroundTask(q);
      const row = normalizeBackgroundTaskDetail(payload);
      if (!row) {
        setDetailMissing(true);
      } else {
        setDetail(row);
      }
    } catch (e) {
      const message = refusalOrError(e, "Task lookup failed.");
      setError(message);
      showToast(message, "error");
    } finally {
      setBusy(false);
    }
  }, [busy, lookup, showToast]);

  const target = {
    receiptId: receiptId.trim(),
    session: session.trim(),
    taskId: taskId.trim(),
  };
  const hasTarget = Boolean(target.receiptId || target.session || target.taskId);

  const runControl = useCallback(
    async (kind: "cancel" | "retry") => {
      if (busy || !hasTarget) return;
      setBusy(true);
      setError("");
      const title = kind === "cancel" ? "Cancel" : "Retry";
      try {
        const call =
          kind === "cancel"
            ? cancelBackgroundTask(target, (detailText) =>
                askBrokerConfirm(`${title} background task`, detailText),
              )
            : retryBackgroundTask(target, (detailText) =>
                askBrokerConfirm(`${title} background task`, detailText),
              );
        await call;
        showToast(`${title} accepted`, "success");
        await load();
      } catch (e) {
        const message = refusalOrError(e, `${title} failed.`);
        setError(message);
        showToast(message, "error");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, hasTarget, load, receiptId, session, taskId],
  );

  return (
    <div className="space-y-6 p-4">
      <div>
        <h1 className="text-xl font-semibold">Background tasks</h1>
        <p className="text-sm text-muted-foreground">
          Detached runs (agent, subagent, cron, CLI) as a live list: filter by
          state, inspect by receipt/session id, cancel or retry against a real
          receipt or session. Dismiss stays disabled with its reason.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center gap-2">
          <Spinner /> <span className="text-sm">Loading background tasks…</span>
        </div>
      ) : null}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-4 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-medium">Task list</h2>
            <div className="space-y-1">
              <Label htmlFor="background-tasks-state">State filter</Label>
              <select
                id="background-tasks-state"
                className="rounded-md border bg-background px-2 py-1 text-sm"
                value={stateFilter}
                onChange={(e) => setStateFilter(e.target.value)}
              >
                <option value="">all states</option>
                {QUBIT_TASK_STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <Button outlined size="sm" onClick={() => load()} disabled={loading}>
              Reload
            </Button>
          </div>
          {tasks.length === 0 && !loading ? (
            <p className="text-sm text-muted-foreground">No background tasks yet.</p>
          ) : (
            <ul className="space-y-1">
              {tasks.map((t) => (
                <li key={t.id} className="flex items-center gap-2 text-sm flex-wrap">
                  <code className="text-xs font-mono">{t.id}</code>
                  {t.state ? <Badge tone="outline">{t.state}</Badge> : null}
                  {t.kind || t.runtime ? (
                    <Badge tone="secondary">{t.kind ?? t.runtime}</Badge>
                  ) : null}
                  <span>{t.title}</span>
                  {t.progressPercent !== undefined ? (
                    <span className="text-muted-foreground">
                      · {t.progressPercent}%{t.progress ? ` — ${t.progress}` : ""}
                    </span>
                  ) : t.progress ? (
                    <span className="text-muted-foreground">· {t.progress}</span>
                  ) : null}
                  {t.sessionKey ? (
                    <span className="text-muted-foreground">· {t.sessionKey}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Task detail</h2>
          <p className="text-sm text-muted-foreground">
            Look up one task by task id, run/receipt id, or session key.
          </p>
          <div className="flex items-end gap-2 flex-wrap">
            <div className="space-y-1">
              <Label htmlFor="background-tasks-lookup">Receipt / session id</Label>
              <Input
                id="background-tasks-lookup"
                value={lookup}
                onChange={(e) => setLookup(e.target.value)}
                placeholder="task, run, or session id"
              />
            </div>
            <Button disabled={busy || !lookup.trim()} onClick={() => lookUp()}>
              Look up
            </Button>
          </div>
          {detailMissing ? (
            <p className="text-sm text-muted-foreground">No task matches that lookup.</p>
          ) : null}
          {detail ? (
            <dl className="text-sm space-y-1" data-testid="background-task-detail">
              <div className="flex gap-2 flex-wrap">
                <dt className="font-medium">Id:</dt>
                <dd>
                  <code className="text-xs font-mono">{detail.id}</code>
                </dd>
              </div>
              {detail.state ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">State:</dt>
                  <dd>
                    <Badge tone="outline">{detail.state}</Badge>
                  </dd>
                </div>
              ) : null}
              {detail.title ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Title:</dt>
                  <dd>{detail.title}</dd>
                </div>
              ) : null}
              {detail.progressPercent !== undefined || detail.progress ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Progress:</dt>
                  <dd>
                    {detail.progressPercent !== undefined
                      ? `${detail.progressPercent}%${detail.progress ? ` — ${detail.progress}` : ""}`
                      : (detail.progress ?? "")}
                  </dd>
                </div>
              ) : null}
              {detail.sessionKey ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Session:</dt>
                  <dd>
                    <code className="text-xs font-mono">{detail.sessionKey}</code>
                  </dd>
                </div>
              ) : null}
              {detail.runId ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Run:</dt>
                  <dd>
                    <code className="text-xs font-mono">{detail.runId}</code>
                  </dd>
                </div>
              ) : null}
              {detail.error ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Error:</dt>
                  <dd className="text-muted-foreground">{detail.error}</dd>
                </div>
              ) : null}
              {fmtStamp(detail.updatedAt) ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Updated:</dt>
                  <dd>{fmtStamp(detail.updatedAt)}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Cancel / retry via receipt or session</h2>
          <p className="text-sm text-muted-foreground">
            A bare ledger id maps to no live run — cancel and retry need a real
            action receipt or an in-flight chat session.
          </p>
          <div className="flex items-end gap-2 flex-wrap">
            <div className="space-y-1">
              <Label htmlFor="background-tasks-receipt">Receipt id</Label>
              <Input
                id="background-tasks-receipt"
                value={receiptId}
                onChange={(e) => setReceiptId(e.target.value)}
                placeholder="action receipt"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="background-tasks-session">Session</Label>
              <Input
                id="background-tasks-session"
                value={session}
                onChange={(e) => setSession(e.target.value)}
                placeholder="chat session"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="background-tasks-task">Ledger id (read-only)</Label>
              <Input
                id="background-tasks-task"
                value={taskId}
                onChange={(e) => setTaskId(e.target.value)}
                placeholder="task id"
              />
            </div>
            <Button disabled={busy || !hasTarget} onClick={() => runControl("cancel")}>
              Cancel
            </Button>
            <Button outlined disabled={busy || !hasTarget} onClick={() => runControl("retry")}>
              Retry
            </Button>
          </div>
          <DismissRefusalRow refusal={TASKS_DISMISS_REFUSAL} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium flex items-center gap-2">
            <Info size={16} /> About dismiss
          </h2>
          <p className="text-sm text-muted-foreground">
            Dismiss is refused by policy (no detached-task delivery registry).
            The ledger history view lives on the task inbox:{" "}
            <Link className="underline" to="/qubit-task-inbox">
              Open the task inbox
            </Link>
            .
          </p>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={brokerConfirm !== null}
        onCancel={() => settleBrokerConfirm(false)}
        onConfirm={() => settleBrokerConfirm(true)}
        title={brokerConfirm?.title ?? "Confirm background task control"}
        description={
          brokerConfirm?.description ??
          "The task backend staged this change for confirmation."
        }
      />
      {toast ? <Toast toast={toast} /> : null}
    </div>
  );
}
