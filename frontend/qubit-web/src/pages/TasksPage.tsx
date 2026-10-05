/**
 * Background tasks — the background-tasks depth as its own page (route
 * `/qubit-tasks`).
 *
 * Separate from `/qubit-task-inbox` (which this page never extends): the
 * inbox covers ledger history + suggestions + mentions, while this page
 * mirrors the background-tasks surface (list with a state filter, inspect by
 * receipt/session id, cancel + retry, progress where the backend reports it).
 *
 * Structure mirrors the ported settings surfaces in this app
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`).
 *
 * Port mapping: list/get onto the read-only task ledger
 * (`voicebridge/task_ledger.py`: `list_tasks` / `show`); cancel/retry onto a
 * real action receipt or an in-flight chat session
 * (`voicebridge.actions.ActionStore.cancel` / `.retry`) through the shared
 * confirmation round-trip. `tasks.dismiss` keeps no delivery registry, so it
 * is disabled-with-reason and never wired.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
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

const INPUT_CLASS =
  "settings-input border border-border bg-background-base px-2 py-1 text-sm";

function DismissRefusalRow({ refusal }: { refusal: GatewayRefusal }) {
  return (
    <div
      className="settings-row settings-row--stacked"
      data-state="refused"
      data-testid={`refusal-${refusal.code}`}
    >
      <div className="settings-row__text">
        <div className="settings-row__title font-mono">{refusal.method}</div>
        <p className="settings-row__desc">{refusal.detail}</p>
        <p className="settings-row__desc">
          <span className="font-medium">Needs: </span>
          {refusal.prerequisite}
        </p>
        <p className="settings-row__desc">
          <span className="font-medium">Use instead: </span>
          <code className="font-mono text-xs">{refusal.counterpart}</code>
          {" — "}
          <Link className="underline" to="/qubit-task-inbox">
            Open the task inbox
          </Link>
        </p>
      </div>
      <div className="settings-row__control">
        <Badge tone="secondary">{refusal.code}</Badge>
        <Button disabled title={refusal.detail}>
          Run {refusal.method}
        </Button>
      </div>
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
    <div className="settings-page settings-page--wide">
      <p className="settings-page__intro">
        Detached runs (agent, subagent, cron, CLI) as a live list: filter by state,
        inspect by receipt/session id, cancel or retry against a real receipt or
        session. Dismiss stays disabled with its reason.
      </p>

      {loading ? (
        <p className="flex items-center gap-2 text-sm text-text-tertiary">
          <Spinner /> Loading background tasks…
        </p>
      ) : null}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <section className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Task list <span className="settings-count">{tasks.length}</span>
            </h2>
            <p className="settings-section__desc">
              Detached runs reported by the read-only task ledger.
            </p>
          </div>
          <div className="settings-section__actions">
            <Button outlined size="sm" onClick={() => load()} disabled={loading}>
              Reload
            </Button>
          </div>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="background-tasks-state" className="settings-row__title">
                State filter
              </label>
            </div>
            <div className="settings-row__control">
              <select
                id="background-tasks-state"
                className="settings-select border border-border bg-background-base px-2 py-1 text-sm"
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
          </div>
          {tasks.length === 0 && !loading ? (
            <div className="settings-empty">No background tasks yet.</div>
          ) : (
            tasks.map((t) => (
              <div key={t.id} className="settings-row">
                <div className="settings-row__text">
                  <div className="settings-row__title">{t.title}</div>
                  <p className="settings-row__desc font-mono text-xs">{t.id}</p>
                  {t.sessionKey ? (
                    <p className="settings-row__desc">session {t.sessionKey}</p>
                  ) : null}
                </div>
                <div className="settings-row__control">
                  {t.state ? <Badge tone="outline">{t.state}</Badge> : null}
                  {t.kind || t.runtime ? (
                    <Badge tone="secondary">{t.kind ?? t.runtime}</Badge>
                  ) : null}
                  {t.progressPercent !== undefined ? (
                    <span className="settings-row__value">
                      {t.progressPercent}%{t.progress ? ` — ${t.progress}` : ""}
                    </span>
                  ) : t.progress ? (
                    <span className="settings-row__value">{t.progress}</span>
                  ) : null}
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Task detail</h2>
          <p className="settings-section__desc">
            Look up one task by task id, run/receipt id, or session key.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="background-tasks-lookup" className="settings-row__title">
                Receipt / session id
              </label>
            </div>
            <div className="settings-row__control">
              <input
                id="background-tasks-lookup"
                className={INPUT_CLASS}
                value={lookup}
                onChange={(e) => setLookup(e.target.value)}
                placeholder="task, run, or session id"
              />
              <Button disabled={busy || !lookup.trim()} onClick={() => lookUp()}>
                Look up
              </Button>
            </div>
          </div>
          {detailMissing ? (
            <div className="settings-empty">No task matches that lookup.</div>
          ) : null}
          {detail ? (
            <dl className="settings-group" data-testid="background-task-detail">
              <div className="settings-row">
                <div className="settings-row__text">
                  <dt className="settings-row__title">Id</dt>
                </div>
                <div className="settings-row__control">
                  <dd className="settings-row__value settings-row__value--mono">{detail.id}</dd>
                </div>
              </div>
              {detail.state ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">State</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd>
                      <Badge tone="outline">{detail.state}</Badge>
                    </dd>
                  </div>
                </div>
              ) : null}
              {detail.title ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Title</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value">{detail.title}</dd>
                  </div>
                </div>
              ) : null}
              {detail.progressPercent !== undefined || detail.progress ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Progress</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value">
                      {detail.progressPercent !== undefined
                        ? `${detail.progressPercent}%${detail.progress ? ` — ${detail.progress}` : ""}`
                        : (detail.progress ?? "")}
                    </dd>
                  </div>
                </div>
              ) : null}
              {detail.sessionKey ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Session</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value settings-row__value--mono">
                      {detail.sessionKey}
                    </dd>
                  </div>
                </div>
              ) : null}
              {detail.runId ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Run</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value settings-row__value--mono">
                      {detail.runId}
                    </dd>
                  </div>
                </div>
              ) : null}
              {detail.error ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Error</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value text-destructive">{detail.error}</dd>
                  </div>
                </div>
              ) : null}
              {fmtStamp(detail.updatedAt) ? (
                <div className="settings-row">
                  <div className="settings-row__text">
                    <dt className="settings-row__title">Updated</dt>
                  </div>
                  <div className="settings-row__control">
                    <dd className="settings-row__value">{fmtStamp(detail.updatedAt)}</dd>
                  </div>
                </div>
              ) : null}
            </dl>
          ) : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Cancel / retry</h2>
          <p className="settings-section__desc">
            A bare ledger id maps to no live run — cancel and retry need a real action
            receipt or an in-flight chat session.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="background-tasks-receipt" className="settings-row__title">
                Receipt id
              </label>
            </div>
            <div className="settings-row__control">
              <input
                id="background-tasks-receipt"
                className={INPUT_CLASS}
                value={receiptId}
                onChange={(e) => setReceiptId(e.target.value)}
                placeholder="action receipt"
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="background-tasks-session" className="settings-row__title">
                Session
              </label>
            </div>
            <div className="settings-row__control">
              <input
                id="background-tasks-session"
                className={INPUT_CLASS}
                value={session}
                onChange={(e) => setSession(e.target.value)}
                placeholder="chat session"
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="background-tasks-task" className="settings-row__title">
                Ledger id
              </label>
              <p className="settings-row__desc">Read-only target.</p>
            </div>
            <div className="settings-row__control">
              <input
                id="background-tasks-task"
                className={INPUT_CLASS}
                value={taskId}
                onChange={(e) => setTaskId(e.target.value)}
                placeholder="task id"
              />
            </div>
          </div>
          <div className="settings-row settings-row--actions">
            <div className="settings-row__text">
              <div className="settings-row__title">Control</div>
              {!hasTarget ? (
                <p className="settings-row__desc" data-testid="task-control-hint">
                  Enter a receipt id, session, or ledger id to enable Cancel / Retry.
                </p>
              ) : null}
            </div>
            <div className="settings-row__control">
              <Button disabled={busy || !hasTarget} onClick={() => runControl("cancel")}>
                Cancel
              </Button>
              <Button outlined disabled={busy || !hasTarget} onClick={() => runControl("retry")}>
                Retry
              </Button>
            </div>
          </div>
          <DismissRefusalRow refusal={TASKS_DISMISS_REFUSAL} />
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">About dismiss</h2>
          <p className="settings-section__desc">
            Dismiss is refused by policy (no detached-task delivery registry). The
            ledger history view lives on the task inbox:{" "}
            <Link className="underline" to="/qubit-task-inbox">
              Open the task inbox
            </Link>
            .
          </p>
        </div>
      </section>

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
