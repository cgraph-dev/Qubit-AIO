/**
 * Task inbox — ledger history, suggestions, mentions.
 *
 * Qubit-owned page (route `/qubit-task-inbox`).
 *
 * (a) The read-only task ledger plus the read-only suggestion list; cancel and
 * retry run via a real action receipt or an in-flight chat session (never a bare
 * task id, which maps to no live run) through the shared confirmation
 * round-trip. `tasks.dismiss` keeps no delivery registry, so it is
 * disabled-with-reason.
 * (b) `mentions.list` / `mentions.dismiss` run no inbox service, so both are
 * disabled-with-reason with the channels counterpart link.
 * (c) `taskSuggestions.create` / `accept` / `dismiss` keep no ephemeral
 * registry, so all three are disabled-with-reason with the `qubit cron add`
 * counterpart link.
 *
 * Look mirrors the upstream workboard UI 1:1 (Qubit voice, rebranded):
 * `ui/src/ui/views/workboard.ts` `renderWorkboard` —
 * `section.workboard > .workboard-main > .workboard-toolbar` (filters +
 * actions) `> .workboard-health` strip `> .callout` error `> .workboard-board`
 * of `section.workboard-column` (header with `h2` + count, then cards) `>`
 * `.workboard-empty-state`. Upstream ships no dedicated tasks/mentions inbox
 * view (closest patterns: `tasks.list`/`tasks.get` ledger refresh in
 * `ui/src/ui/controllers/workboard.ts`, channel mention gating in
 * `src/plugin-sdk/channel-mention-gating.ts`, and the `taskflow-inbox-triage`
 * skill), so the inbox columns below are the closest arrangement of those
 * patterns; every backend call, confirm-gating step, and verbatim refusal
 * reason is unchanged (look changes, behavior doesn't).
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
  MENTIONS_REFUSAL,
  TASKS_DISMISS_REFUSAL,
  TASK_SUGGESTIONS_REFUSAL,
  cancelTask,
  getTaskLedger,
  getTaskSuggestions,
  normalizeSuggestions,
  normalizeTasks,
  retryTask,
  type GatewayRefusal,
  type SuggestionRow,
  type TaskRow,
} from "@/lib/ocFamilyBoardApi";
import { refusalOrError } from "@/lib/policy-refusal";

type InboxViewPreset = "all" | "tasks" | "suggestions";

function RefusalRow({ refusal, counterpartTo }: { refusal: GatewayRefusal; counterpartTo?: string }) {
  return (
    <div className="rounded-md border border-dashed p-3 space-y-1" data-testid={`refusal-${refusal.code}`}>
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
        {counterpartTo ? (
          <>
            {" — "}
            <Link className="underline" to={counterpartTo}>
              Open it
            </Link>
          </>
        ) : null}
      </p>
      <Button disabled title={refusal.detail}>
        Run {refusal.method}
      </Button>
    </div>
  );
}

export default function TaskInboxPage() {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { toast, showToast } = useToast();

  // Toolbar-local view state only (mirrors the upstream workboard toolbar:
  // search, view preset, hide-empty, refresh status). Never sent to the
  // backend; every backend call below is unchanged.
  const [query, setQuery] = useState("");
  const [viewPreset, setViewPreset] = useState<InboxViewPreset>("all");
  const [hideEmpty, setHideEmpty] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null);

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
      const [ledger, sugg] = await Promise.all([getTaskLedger(), getTaskSuggestions()]);
      setTasks(normalizeTasks(ledger));
      setSuggestions(normalizeSuggestions(sugg));
      setLastRefreshAt(Date.now());
    } catch (e) {
      const message = refusalOrError(e, "Failed to load the task inbox.");
      setError(message);
      showToast(message, "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

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
            ? cancelTask(target, (detail) => askBrokerConfirm(`${title} task`, detail))
            : retryTask(target, (detail) => askBrokerConfirm(`${title} task`, detail));
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

  const q = query.trim().toLowerCase();
  const matches = (...parts: Array<string | undefined>) =>
    q === "" || parts.filter(Boolean).join(" ").toLowerCase().includes(q);
  const visibleTasks = tasks.filter((t) => matches(t.id, t.title, t.state, t.session));
  const visibleSuggestions = suggestions.filter((s) => matches(s.id, s.prompt, s.title, s.session));

  const showTasksColumn =
    (viewPreset === "all" || viewPreset === "tasks") && !(hideEmpty && visibleTasks.length === 0 && viewPreset === "all");
  const showSuggestionsColumn =
    (viewPreset === "all" || viewPreset === "suggestions") &&
    !(hideEmpty && visibleSuggestions.length === 0 && viewPreset === "all");
  const showFilteredEmpty =
    q !== "" && visibleTasks.length === 0 && visibleSuggestions.length === 0;

  const healthItems: Array<{ key: InboxViewPreset; label: string; count: number }> = [
    { key: "tasks", label: "Ledger entries", count: tasks.length },
    { key: "suggestions", label: "Suggestions", count: suggestions.length },
  ];

  return (
    <section className="workboard">
      <div className="workboard-main space-y-6 p-4">
        <div>
          <h1 className="text-xl font-semibold">Task inbox</h1>
          <p className="text-sm text-muted-foreground">
            Read-only ledger history and suggestions; cancel/retry run against a real
            receipt or session. Dismiss and inbox verbs stay disabled with their reason.
          </p>
        </div>

        <div className="workboard-toolbar">
          <div className="workboard-toolbar__filters">
            <div className="space-y-1">
              <Label htmlFor="task-inbox-search">Search</Label>
              <Input
                id="task-inbox-search"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search ledger and suggestions…"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="task-inbox-view">View</Label>
              <select
                id="task-inbox-view"
                className="input"
                value={viewPreset}
                onChange={(e) => setViewPreset(e.target.value as InboxViewPreset)}
              >
                <option value="all">All sections</option>
                <option value="tasks">Task ledger</option>
                <option value="suggestions">Suggestions</option>
              </select>
            </div>
            <label className="workboard-toggle" htmlFor="task-inbox-hide-empty">
              <input
                id="task-inbox-hide-empty"
                type="checkbox"
                checked={hideEmpty}
                onChange={(e) => setHideEmpty(e.target.checked)}
              />
              <span>Hide empty sections</span>
            </label>
            {lastRefreshAt ? (
              <span className="workboard-refresh-status">
                Last refreshed{" "}
                {new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
                  new Date(lastRefreshAt),
                )}
              </span>
            ) : null}
          </div>
          <div className="workboard-toolbar__actions">
            <Button outlined size="sm" onClick={() => load()} disabled={loading}>
              Reload
            </Button>
          </div>
        </div>

        <div className="workboard-health" aria-label="Inbox health">
          {healthItems.map((item) => (
            <button
              key={item.key}
              className={`workboard-health__item workboard-health__item--${item.key} ${
                viewPreset === item.key ? "workboard-health__item--active" : ""
              } ${item.count === 0 ? "workboard-health__item--empty" : ""}`}
              type="button"
              aria-pressed={viewPreset === item.key}
              aria-label={`${item.count} ${item.label}`}
              onClick={() => setViewPreset((prev) => (prev === item.key ? "all" : item.key))}
            >
              <strong>{item.count}</strong>
              {item.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center gap-2">
            <Spinner /> <span className="text-sm">Loading task inbox…</span>
          </div>
        ) : null}
        {error ? (
          <div className="callout danger" role="alert">
            {error}
          </div>
        ) : null}

        {showFilteredEmpty ? (
          <div className="workboard-empty-state" role="status">
            <strong>No inbox entries match this filter</strong>
            <span>Clear the search or show all sections.</span>
          </div>
        ) : null}

        <div className="workboard-board workboard-board--comfortable">
          {showTasksColumn ? (
            <section className="workboard-column workboard-column--tasks">
              <div className="workboard-column__header">
                <h2>Task ledger</h2>
                <span>{visibleTasks.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-2">
                    <div className="flex items-center gap-2">
                      <Button outlined size="sm" onClick={() => load()} disabled={loading}>
                        Reload
                      </Button>
                    </div>
                    {visibleTasks.length === 0 && !loading ? (
                      <p className="text-sm text-muted-foreground">No ledger entries yet.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleTasks.map((t) => (
                          <li key={t.id} className="flex items-center gap-2 text-sm flex-wrap">
                            <code className="text-xs font-mono">{t.id}</code>
                            {t.state ? <Badge tone="outline">{t.state}</Badge> : null}
                            <span>{t.title}</span>
                            {t.session ? (
                              <span className="text-muted-foreground">· {t.session}</span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}

          {showSuggestionsColumn ? (
            <section className="workboard-column workboard-column--suggestions">
              <div className="workboard-column__header">
                <h2>Suggestions (read-only)</h2>
                <span>{visibleSuggestions.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-2">
                    {visibleSuggestions.length === 0 && !loading ? (
                      <p className="text-sm text-muted-foreground">No suggestions.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleSuggestions.map((s) => (
                          <li key={s.id} className="flex items-center gap-2 text-sm flex-wrap">
                            <code className="text-xs font-mono">{s.id}</code>
                            <span>{s.prompt || s.title}</span>
                            {s.session ? (
                              <span className="text-muted-foreground">· {s.session}</span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}

          <section className="workboard-column workboard-column--controls">
            <div className="workboard-column__header">
              <h2>Cancel / retry via receipt or session</h2>
              <span>2</span>
            </div>
            <div className="workboard-column__cards">
              <Card>
                <CardContent className="pt-4 space-y-3">
                  <p className="text-sm text-muted-foreground">
                    A bare ledger id maps to no live run — cancel and retry need a real
                    action receipt or an in-flight chat session.
                  </p>
                  <div className="flex items-end gap-2 flex-wrap">
                    <div className="space-y-1">
                      <Label htmlFor="task-inbox-receipt">Receipt id</Label>
                      <Input
                        id="task-inbox-receipt"
                        value={receiptId}
                        onChange={(e) => setReceiptId(e.target.value)}
                        placeholder="action receipt"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="task-inbox-session">Session</Label>
                      <Input
                        id="task-inbox-session"
                        value={session}
                        onChange={(e) => setSession(e.target.value)}
                        placeholder="chat session"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="task-inbox-task">Ledger id (read-only)</Label>
                      <Input
                        id="task-inbox-task"
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
                  <RefusalRow refusal={TASKS_DISMISS_REFUSAL} />
                </CardContent>
              </Card>
            </div>
          </section>

          <section className="workboard-column workboard-column--mentions">
            <div className="workboard-column__header">
              <h2>Mentions (disabled with reason)</h2>
              <span>1</span>
            </div>
            <div className="workboard-column__cards">
              <Card>
                <CardContent className="pt-4 space-y-3">
                  <h3 className="font-medium flex items-center gap-2">
                    <Info size={16} /> Mentions (disabled with reason)
                  </h3>
                  <RefusalRow refusal={MENTIONS_REFUSAL} counterpartTo="/channels" />
                </CardContent>
              </Card>
            </div>
          </section>

          <section className="workboard-column workboard-column--suggestion-writes">
            <div className="workboard-column__header">
              <h2>Suggestion writes (disabled with reason)</h2>
              <span>1</span>
            </div>
            <div className="workboard-column__cards">
              <Card>
                <CardContent className="pt-4 space-y-3">
                  <h3 className="font-medium flex items-center gap-2">
                    <Info size={16} /> Suggestion writes (disabled with reason)
                  </h3>
                  <RefusalRow refusal={TASK_SUGGESTIONS_REFUSAL} counterpartTo="/cron" />
                  <p className="text-sm text-muted-foreground">
                    Durable scheduled work lives on the Schedules page:{" "}
                    <Link className="underline" to="/cron">
                      Open Schedules (`qubit cron add`)
                    </Link>
                    .
                  </p>
                </CardContent>
              </Card>
            </div>
          </section>
        </div>

        <ConfirmDialog
          open={brokerConfirm !== null}
          onCancel={() => settleBrokerConfirm(false)}
          onConfirm={() => settleBrokerConfirm(true)}
          title={brokerConfirm?.title ?? "Confirm task control"}
          description={brokerConfirm?.description ?? "The task backend staged this change for confirmation."}
        />
        {toast ? <Toast toast={toast} /> : null}
      </div>
    </section>
  );
}
