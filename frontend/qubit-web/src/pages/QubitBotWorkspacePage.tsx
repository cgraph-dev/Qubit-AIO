/**
 * Qubit Bot — the delegated-work workspace, off `/qubit-bot`.
 *
 * Upstream openmuse keeps this content on its own screens (Threads sheet,
 * Activity, Goals, Apps) and its CHAT screen (`apps/mobile/src/chat.tsx`)
 * carries none of it. Qubit mirrors that split: `/qubit-bot` is the pure chat
 * surface; this route owns durable delegated work — threads, tasks, ideas,
 * goals, watches, alerts, the read-only agent-computer console, and rich cards.
 *
 * Ported actions and geometry are shared with the chat surface via
 * `@/lib/botchat/runtime` and the `qb-*` classes in
 * `src/styles/upstream/botchat.css`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
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
  DISABLED_BOT_ACTIONS,
  QUBIT_BOT_TASK_STATES,
  acceptBotIdea,
  cancelBotTask,
  dismissBotIdea,
  editBotIdea,
  getBotCard,
  getBotComputerStatus,
  getBotTask,
  getBotTaskReceipts,
  listBotAlerts,
  listBotGoals,
  listBotIdeas,
  listBotTasks,
  listBotThreads,
  launchBotTask,
  normalizeAlerts,
  normalizeBotTaskDetail,
  normalizeBotTasks,
  normalizeBotThreads,
  normalizeGoals,
  normalizeIdeas,
  normalizeRichCard,
  normalizeWatches,
  pauseBotTask,
  resumeBotTask,
  retryBotTask,
  setBotGoal,
  setBotWatch,
  type Alert,
  type BotTaskDetail,
  type BotThread,
  type ComputerStatus,
  type DisabledBotAction,
  type Goal,
  type Idea,
  type RichCard,
  type Watch,
} from "@/lib/ocFamilyBotApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { ArtifactCard, TaskThreadCard } from "@/components/botchat/ToolCards";

function DisabledRow({ entry }: { entry: DisabledBotAction }) {
  return (
    <div className="rounded-md border border-dashed p-3 space-y-1" data-testid={`disabled-${entry.code}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <code className="text-xs font-mono">{entry.action}</code>
        <Badge tone="secondary">{entry.code}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{entry.reason}</p>
      <p className="text-sm">
        <span className="font-medium">Use instead: </span>
        <code className="text-xs font-mono">{entry.counterpart}</code>
      </p>
      <Button disabled title={entry.reason}>
        Run {entry.action}
      </Button>
    </div>
  );
}

export default function QubitBotWorkspacePage() {
  const [threads, setThreads] = useState<BotThread[]>([]);
  const [tasks, setTasks] = useState<ReturnType<typeof normalizeBotTasks>>([]);
  const [taskFilter, setTaskFilter] = useState("");
  const [taskLookup, setTaskLookup] = useState("");
  const [taskDetail, setTaskDetail] = useState<BotTaskDetail | null>(null);
  const [taskMissing, setTaskMissing] = useState(false);
  const [receipts, setReceipts] = useState("");
  const [launchTitle, setLaunchTitle] = useState("");
  const [launchPrompt, setLaunchPrompt] = useState("");

  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [watches, setWatches] = useState<Watch[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [dismissedAlerts, setDismissedAlerts] = useState<string[]>([]);
  const [goalTitle, setGoalTitle] = useState("");
  const [goalDescription, setGoalDescription] = useState("");
  const [watchTitle, setWatchTitle] = useState("");
  const [watchUrl, setWatchUrl] = useState("");
  const [computer, setComputer] = useState<ComputerStatus | null>(null);
  const [cardLookup, setCardLookup] = useState("");
  const [card, setCard] = useState<RichCard | null>(null);
  const [cardMissing, setCardMissing] = useState(false);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showTaskInline, setShowTaskInline] = useState(false);
  const { toast, showToast } = useToast();

  const [brokerConfirm, setBrokerConfirm] = useState<{ title: string; description: string } | null>(null);
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

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [threadPayload, taskPayload, ideaPayload, goalPayload, alertPayload, computerPayload] =
        await Promise.all([
          listBotThreads(),
          listBotTasks(taskFilter ? { status: taskFilter } : undefined),
          listBotIdeas(),
          listBotGoals(),
          listBotAlerts(),
          getBotComputerStatus(),
        ]);
      setThreads(normalizeBotThreads(threadPayload));
      setTasks(normalizeBotTasks(taskPayload));
      setIdeas(normalizeIdeas(ideaPayload));
      setGoals(normalizeGoals(goalPayload));
      setWatches(normalizeWatches(goalPayload));
      setAlerts(normalizeAlerts(alertPayload));
      const inner =
        computerPayload && typeof computerPayload === "object"
          ? ((computerPayload as Record<string, unknown>).result ?? computerPayload)
          : null;
      setComputer(inner && typeof inner === "object" ? (inner as ComputerStatus) : { state: "unknown" });
    } catch (e) {
      showToast(refusalOrError(e, "Failed to load the bot workspace."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast, taskFilter]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const lookUpTask = useCallback(
    async (id?: string) => {
      const q = (id ?? taskLookup).trim();
      if (busy || !q) return;
      setBusy(true);
      setTaskDetail(null);
      setTaskMissing(false);
      setReceipts("");
      try {
        const payload = await getBotTask(q);
        const row = normalizeBotTaskDetail(payload);
        if (!row) {
          setTaskMissing(true);
        } else {
          setTaskDetail(row);
          try {
            const receiptPayload = await getBotTaskReceipts(row.id);
            setReceipts(JSON.stringify(receiptPayload));
          } catch {
            setReceipts("");
          }
        }
      } catch (e) {
        showToast(refusalOrError(e, "Task lookup failed."), "error");
      } finally {
        setBusy(false);
      }
    },
    [busy, showToast, taskLookup],
  );

  const launchTask = useCallback(async () => {
    const title = launchTitle.trim();
    const prompt = launchPrompt.trim();
    if (busy || !title || !prompt) return;
    setBusy(true);
    try {
      await launchBotTask({ title, prompt }, (detailText) =>
        askBrokerConfirm("Launch delegated task", detailText),
      );
      showToast("Task launched", "success");
      setLaunchTitle("");
      setLaunchPrompt("");
      await loadAll();
    } catch (e) {
      showToast(refusalOrError(e, "Launch failed."), "error");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, launchPrompt, launchTitle, loadAll, showToast]);

  const runTaskControl = useCallback(
    async (kind: "pause" | "resume" | "cancel" | "retry") => {
      const id = (taskDetail?.id ?? taskLookup.trim()).trim();
      if (busy || !id) return;
      setBusy(true);
      const title = kind === "pause" ? "Pause" : kind === "resume" ? "Resume" : kind === "cancel" ? "Cancel" : "Retry";
      try {
        const call =
          kind === "pause"
            ? pauseBotTask(id, (d) => askBrokerConfirm(`${title} delegated task`, d))
            : kind === "resume"
              ? resumeBotTask(id, (d) => askBrokerConfirm(`${title} delegated task`, d))
              : kind === "cancel"
                ? cancelBotTask({ taskId: id }, (d) => askBrokerConfirm(`${title} delegated task`, d))
                : retryBotTask({ taskId: id }, (d) => askBrokerConfirm(`${title} delegated task`, d));
        await call;
        showToast(`${title} accepted`, "success");
        await loadAll();
      } catch (e) {
        showToast(refusalOrError(e, `${title} failed.`), "error");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, loadAll, showToast, taskDetail, taskLookup],
  );

  const runIdea = useCallback(
    async (kind: "accept" | "dismiss", id: string) => {
      if (busy || !id) return;
      setBusy(true);
      try {
        if (kind === "accept") {
          await acceptBotIdea(id, (d) => askBrokerConfirm("Accept idea", d));
          showToast("Idea accepted — worker launched", "success");
        } else {
          await dismissBotIdea(id, (d) => askBrokerConfirm("Dismiss idea", d));
          showToast("Idea dismissed", "success");
        }
        await loadAll();
      } catch (e) {
        showToast(refusalOrError(e, kind === "accept" ? "Accept failed." : "Dismiss failed."), "error");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, loadAll, showToast],
  );

  const saveIdeaEdit = useCallback(
    async (idea: Idea) => {
      if (busy || !idea.id) return;
      setBusy(true);
      try {
        await editBotIdea(
          { idea: idea.id, title: idea.title, reason: idea.reason, prompt: idea.prompt },
          (d) => askBrokerConfirm("Save idea edit", d),
        );
        showToast("Idea updated", "success");
        await loadAll();
      } catch (e) {
        showToast(refusalOrError(e, "Idea edit failed."), "error");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, loadAll, showToast],
  );

  const saveGoal = useCallback(async () => {
    const title = goalTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    try {
      await setBotGoal({ title, description: goalDescription.trim() || undefined }, (d) =>
        askBrokerConfirm("Record goal", d),
      );
      showToast("Goal recorded", "success");
      setGoalTitle("");
      setGoalDescription("");
      await loadAll();
    } catch (e) {
      showToast(refusalOrError(e, "Goal save failed."), "error");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, goalDescription, goalTitle, loadAll, showToast]);

  const saveWatch = useCallback(async () => {
    const title = watchTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    try {
      await setBotWatch({ title, url: watchUrl.trim() || undefined }, (d) =>
        askBrokerConfirm("Record watch", d),
      );
      showToast("Watch recorded", "success");
      setWatchTitle("");
      setWatchUrl("");
      await loadAll();
    } catch (e) {
      showToast(refusalOrError(e, "Watch save failed."), "error");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, loadAll, showToast, watchTitle, watchUrl]);

  const lookUpCard = useCallback(async () => {
    const q = cardLookup.trim();
    if (busy || !q) return;
    setBusy(true);
    setCard(null);
    setCardMissing(false);
    try {
      const payload = await getBotCard(q);
      const row = normalizeRichCard(payload);
      if (!row) setCardMissing(true);
      else setCard(row);
    } catch (e) {
      showToast(refusalOrError(e, "Card lookup failed."), "error");
    } finally {
      setBusy(false);
    }
  }, [busy, cardLookup, showToast]);

  const viewAlertTask = useCallback(
    (taskId: string) => {
      setTaskLookup(taskId);
      void lookUpTask(taskId);
    },
    [lookUpTask],
  );

  const dismissAlert = useCallback((id: string) => {
    setDismissedAlerts((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }, []);

  const visibleAlerts = alerts.filter((a) => !dismissedAlerts.includes(a.id));
  const unread = visibleAlerts.filter((a) => !a.read);

  return (
    <div className="qb-workspace-page" data-testid="qubit-bot-workspace">
        <div className="qb-thread-bar">
          <h1 className="text-xl font-semibold">Qubit Bot workspace</h1>
          <div className="qb-actions-row">
            <Button outlined size="sm" onClick={() => loadAll()} disabled={loading}>
              Reload
            </Button>
          </div>
        </div>
        {loading ? (
          <div className="flex items-center gap-2">
            <Spinner /> <span className="text-sm">Loading the bot workspace…</span>
          </div>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <Card>
            <CardContent className="pt-4 space-y-3">
              <h2 className="font-medium">Threads</h2>
              {threads.length === 0 && !loading ? (
                <p className="qb-bubble-meta">No threads yet.</p>
              ) : (
                <ul className="qb-thread-list">
                  {threads.map((t) => (
                    <li key={t.id} title={t.id} className="qb-thread-item">
                      <span className="truncate max-w-[140px]">{t.title ?? t.id}</span>
                      {t.messageCount !== undefined ? (
                        <span className="qb-bubble-meta">· {t.messageCount} messages</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="pt-4 space-y-3">
              <h2 className="font-medium">Delegated tasks</h2>
              <p className="text-sm text-muted-foreground">
                Follow-ups run in order after the current turn. Stopping the chat pauses the queue —
                it never cancels delegated tasks.
              </p>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-medium">Delegated tasks</h2>
              <div className="space-y-1">
                <Label htmlFor="bot-task-state">State filter</Label>
                <select
                  id="bot-task-state"
                  className="rounded-md border bg-background px-2 py-1 text-sm"
                  value={taskFilter}
                  onChange={(e) => setTaskFilter(e.target.value)}
                >
                  <option value="">all states</option>
                  {QUBIT_BOT_TASK_STATES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              Durable server work: launching survives navigation; plan, progress, and receipts
              report back here.
            </p>
            {tasks.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No delegated tasks yet.</p>
            ) : (
              <ul className="space-y-1">
                {tasks.map((t) => (
                  <li key={t.id} className="flex items-center gap-2 text-sm flex-wrap">
                    <code className="text-xs font-mono">{t.id}</code>
                    {t.status ? <Badge tone="outline">{t.status}</Badge> : null}
                    {t.kind ? <Badge tone="secondary">{t.kind}</Badge> : null}
                    <button
                      type="button"
                      className="underline underline-offset-2"
                      onClick={() => {
                        setTaskLookup(t.id);
                        void lookUpTask(t.id);
                      }}
                    >
                      {t.title ?? t.id}
                    </button>
                    {t.progressPercent !== undefined ? (
                      <span className="text-muted-foreground">· {t.progressPercent}%</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-end gap-2 flex-wrap">
              <div className="space-y-1">
                <Label htmlFor="bot-task-lookup">Task lookup</Label>
                <Input
                  id="bot-task-lookup"
                  value={taskLookup}
                  onChange={(e) => setTaskLookup(e.target.value)}
                  placeholder="task, receipt, or session id"
                />
              </div>
              <Button disabled={busy || !taskLookup.trim()} onClick={() => lookUpTask()}>
                Look up
              </Button>
              {taskDetail ? (
                <Button outlined disabled={busy} onClick={() => setShowTaskInline((v) => !v)}>
                  {showTaskInline ? "Hide detail" : "Show detail"}
                </Button>
              ) : null}
            </div>
            {taskMissing ? (
              <p className="text-sm text-muted-foreground">No task matches that lookup.</p>
            ) : null}
            {taskDetail && showTaskInline ? <TaskThreadCard task={taskDetail} /> : null}
            {taskDetail ? (
              <dl className="text-sm space-y-1" data-testid="bot-task-detail">
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Id:</dt>
                  <dd>
                    <code className="text-xs font-mono">{taskDetail.id}</code>
                  </dd>
                </div>
                {taskDetail.status ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Status:</dt>
                    <dd>
                      <Badge tone="outline">{taskDetail.status}</Badge>
                    </dd>
                  </div>
                ) : null}
                {taskDetail.plan.length > 0 ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Plan:</dt>
                    <dd>
                      <ul className="space-y-1">
                        {taskDetail.plan.map((s) => (
                          <li key={s.id} className="flex items-center gap-2 flex-wrap">
                            <Badge tone="secondary">{s.status}</Badge>
                            <span>{s.title}</span>
                            {s.detail ? <span className="text-muted-foreground">— {s.detail}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                ) : null}
                {taskDetail.result ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Result:</dt>
                    <dd>{taskDetail.result}</dd>
                  </div>
                ) : null}
                {taskDetail.error ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Error:</dt>
                    <dd className="text-muted-foreground">{taskDetail.error}</dd>
                  </div>
                ) : null}
                {receipts ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Receipts:</dt>
                    <dd>
                      <code className="text-xs font-mono">{receipts}</code>
                    </dd>
                  </div>
                ) : null}
              </dl>
            ) : null}
            <div className="flex items-end gap-2 flex-wrap">
              <Button outlined disabled={busy || (!taskDetail && !taskLookup.trim())} onClick={() => runTaskControl("pause")}>
                Pause
              </Button>
              <Button outlined disabled={busy || (!taskDetail && !taskLookup.trim())} onClick={() => runTaskControl("resume")}>
                Resume
              </Button>
              <Button disabled={busy || (!taskDetail && !taskLookup.trim())} onClick={() => runTaskControl("cancel")}>
                Cancel
              </Button>
              <Button outlined disabled={busy || (!taskDetail && !taskLookup.trim())} onClick={() => runTaskControl("retry")}>
                Retry
              </Button>
            </div>
            <div className="space-y-1">
              <h3 className="font-medium text-sm">Launch a delegated task</h3>
              <div className="flex items-end gap-2 flex-wrap">
                <div className="space-y-1">
                  <Label htmlFor="bot-launch-title">Title</Label>
                  <Input
                    id="bot-launch-title"
                    value={launchTitle}
                    onChange={(e) => setLaunchTitle(e.target.value)}
                    placeholder="Task title"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="bot-launch-prompt">Prompt</Label>
                  <Input
                    id="bot-launch-prompt"
                    value={launchPrompt}
                    onChange={(e) => setLaunchPrompt(e.target.value)}
                    placeholder="What should the worker do…"
                  />
                </div>
                <Button disabled={busy || !launchTitle.trim() || !launchPrompt.trim()} onClick={() => launchTask()}>
                  Launch
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium">Ideas</h2>
            <p className="text-sm text-muted-foreground">
              Suggested work with evidence: accept to launch a worker, edit the record, or dismiss it.
            </p>
            {ideas.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No ideas yet.</p>
            ) : (
              <ul className="space-y-3">
                {ideas.map((idea) => (
                  <li key={idea.id} className="rounded-md border p-3 space-y-2" data-testid={`idea-${idea.id}`}>
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="text-xs font-mono">{idea.id}</code>
                      {idea.status ? <Badge tone="outline">{idea.status}</Badge> : null}
                      {idea.kind ? <Badge tone="secondary">{idea.kind}</Badge> : null}
                    </div>
                    {idea.title ? <p className="text-sm font-medium">{idea.title}</p> : null}
                    {idea.reason ? <p className="text-sm text-muted-foreground">{idea.reason}</p> : null}
                    {idea.evidence.length > 0 ? (
                      <ul className="space-y-1">
                        {idea.evidence.map((ev, i) => (
                          <li key={ev.id ?? `${idea.id}-${i}`} className="text-sm flex items-center gap-2 flex-wrap">
                            {ev.kind ? <Badge tone="outline">{ev.kind}</Badge> : null}
                            <span className="font-medium">{ev.title}</span>
                            <span className="text-muted-foreground">{ev.excerpt}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <div className="flex items-center gap-2 flex-wrap">
                      <Button outlined size="sm" disabled={busy} onClick={() => runIdea("accept", idea.id)}>
                        Accept
                      </Button>
                      <Button outlined size="sm" disabled={busy} onClick={() => saveIdeaEdit(idea)}>
                        Save edit
                      </Button>
                      <Button outlined size="sm" disabled={busy} onClick={() => runIdea("dismiss", idea.id)}>
                        Dismiss
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium">Goals, watches &amp; alerts</h2>
            <p className="text-sm text-muted-foreground">
              Milestones, recurring watches with alert conditions, and the durable alert inbox
              {unread.length > 0 ? ` — ${unread.length} unread` : ""}.
            </p>
            {goals.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No goals yet.</p>
            ) : (
              <ul className="space-y-2">
                {goals.map((g) => (
                  <li key={g.id} className="flex items-center gap-2 text-sm flex-wrap">
                    <code className="text-xs font-mono">{g.id}</code>
                    {g.status ? <Badge tone="outline">{g.status}</Badge> : null}
                    <span className="font-medium">{g.title}</span>
                    {g.milestones.length > 0 ? (
                      <span className="text-muted-foreground">
                        · {g.milestones.filter((m) => m.done).length}/{g.milestones.length} milestones
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {watches.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No watches yet.</p>
            ) : (
              <ul className="space-y-1">
                {watches.map((w) => (
                  <li key={w.id} className="flex items-center gap-2 text-sm flex-wrap">
                    <code className="text-xs font-mono">{w.id}</code>
                    {w.status ? <Badge tone="outline">{w.status}</Badge> : null}
                    <span>{w.title}</span>
                    {w.condition ? <span className="text-muted-foreground">· {w.condition}</span> : null}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-end gap-2 flex-wrap">
              <div className="space-y-1">
                <Label htmlFor="bot-goal-title">Goal title</Label>
                <Input
                  id="bot-goal-title"
                  value={goalTitle}
                  onChange={(e) => setGoalTitle(e.target.value)}
                  placeholder="Goal title"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="bot-goal-description">Description</Label>
                <Input
                  id="bot-goal-description"
                  value={goalDescription}
                  onChange={(e) => setGoalDescription(e.target.value)}
                  placeholder="What does done look like…"
                />
              </div>
              <Button outlined disabled={busy || !goalTitle.trim()} onClick={() => saveGoal()}>
                Record goal
              </Button>
            </div>
            <div className="flex items-end gap-2 flex-wrap">
              <div className="space-y-1">
                <Label htmlFor="bot-watch-title">Watch title</Label>
                <Input
                  id="bot-watch-title"
                  value={watchTitle}
                  onChange={(e) => setWatchTitle(e.target.value)}
                  placeholder="Watch title"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="bot-watch-url">Address</Label>
                <Input
                  id="bot-watch-url"
                  value={watchUrl}
                  onChange={(e) => setWatchUrl(e.target.value)}
                  placeholder="https://…"
                />
              </div>
              <Button outlined disabled={busy || !watchTitle.trim()} onClick={() => saveWatch()}>
                Record watch
              </Button>
            </div>
            <div className="space-y-1">
              <h3 className="font-medium text-sm">Alerts</h3>
              {visibleAlerts.length === 0 && !loading ? (
                <p className="text-sm text-muted-foreground">No alerts.</p>
              ) : (
                <ul className="space-y-1">
                  {visibleAlerts.map((a) => (
                    <li key={a.id} className="flex items-center gap-2 text-sm flex-wrap rounded-full bg-muted px-3 py-1">
                      {!a.read ? <Badge tone="secondary">new</Badge> : null}
                      <span className="font-medium">{a.title}</span>
                      {a.body ? <span className="text-muted-foreground">{a.body}</span> : null}
                      {a.taskId ? (
                        <Button outlined size="sm" onClick={() => viewAlertTask(a.taskId as string)}>
                          View task
                        </Button>
                      ) : null}
                      <Button outlined size="sm" onClick={() => dismissAlert(a.id)}>
                        Dismiss
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium">Computer console</h2>
            <p className="text-sm text-muted-foreground">
              The agent computer state, read-only. Interactive takeover stays operator-scoped.
            </p>
            {computer ? (
              <dl className="text-sm space-y-1" data-testid="bot-computer-status">
                {computer.state ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">State:</dt>
                    <dd>
                      <Badge tone="outline">{computer.state}</Badge>
                    </dd>
                  </div>
                ) : null}
                {computer.detail ? (
                  <div className="flex gap-2 flex-wrap">
                    <dt className="font-medium">Detail:</dt>
                    <dd className="text-muted-foreground">{computer.detail}</dd>
                  </div>
                ) : null}
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">No computer status yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium">Rich cards</h2>
            <p className="text-sm text-muted-foreground">
              Plans, comparisons, finance summaries, and reports produced by delegated tasks.
            </p>
            <div className="flex items-end gap-2 flex-wrap">
              <div className="space-y-1">
                <Label htmlFor="bot-card-lookup">Card id</Label>
                <Input
                  id="bot-card-lookup"
                  value={cardLookup}
                  onChange={(e) => setCardLookup(e.target.value)}
                  placeholder="card id"
                />
              </div>
              <Button disabled={busy || !cardLookup.trim()} onClick={() => lookUpCard()}>
                Look up
              </Button>
            </div>
            {cardMissing ? (
              <p className="text-sm text-muted-foreground">No card matches that lookup.</p>
            ) : null}
            {card ? <ArtifactCard artifact={card} /> : null}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium">Hosted persistence (not used)</h2>
            <p className="text-sm text-muted-foreground">
              The upstream hosted thread-persistence service is a separately configured service and
              is never called. Thread state lives in the canonical Qubit session store instead.
            </p>
            <div className="space-y-2">
              {DISABLED_BOT_ACTIONS.map((entry) => (
                <DisabledRow key={entry.action} entry={entry} />
              ))}
            </div>
          </CardContent>
        </Card>

      <ConfirmDialog
        open={brokerConfirm !== null}
        onCancel={() => settleBrokerConfirm(false)}
        onConfirm={() => settleBrokerConfirm(true)}
        title={brokerConfirm?.title ?? "Confirm bot action"}
        description={
          brokerConfirm?.description ?? "The bot backend staged this change for confirmation."
        }
      />
      {toast ? <Toast toast={toast} /> : null}
    </div>
  );
}
