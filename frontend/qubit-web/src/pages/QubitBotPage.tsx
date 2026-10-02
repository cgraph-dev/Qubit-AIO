/**
 * Qubit Bot — the delegated-work bot as its own page (route `/qubit-bot`;
 * route registration in `App.tsx` is a NEEDS-BRIDGE item — this file only
 * exports the component).
 *
 * Port attribution (MIT, license notice preserved): behavior ported from
 * `CopilotKit/openmuse` (https://github.com/CopilotKit/openmuse)
 * commit `9ec439fbaa878197d9d44c2aa982cca55676dd68` — headless-chat thread
 * UI with streamed events + stop/follow-ups/drafts, durable delegated tasks
 * with plan/progress/pause/resume/cancel/retry + receipts, Ideas with
 * evidence, Goals & watches with alerts, agent-computer console, rich cards.
 * The hosted thread-persistence service is NOT ported (separately configured
 * service): thread state lives in the canonical Qubit session store and its
 * rows render disabled-with-reason, never called.
 *
 * Port mapping: thread list/get onto the canonical session store; turns onto
 * the canonical chat path (composer stays editable while a turn runs; the
 * send control becomes stop; stopping preserves the draft and pauses the
 * follow-up queue but never cancels delegated tasks); follow-ups onto the
 * in-app queue (held in the open page, not a server inbox); tasks onto the
 * read-only ledger + the durable worker chain (launch/pause/resume/cancel/
 * retry through the shared confirmation round-trip, receipts read-only);
 * ideas onto memory notes + skill suggestions (accept launches a worker,
 * edit/dismiss record the note); goals + watches onto the schedules store;
 * alerts onto the durable inbox; the computer console onto the read-only
 * computer status; rich cards onto task artifacts.
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
  getBotThread,
  launchBotTask,
  listBotAlerts,
  listBotGoals,
  listBotIdeas,
  listBotTasks,
  listBotThreads,
  normalizeAlerts,
  normalizeBotTaskDetail,
  normalizeBotTasks,
  normalizeBotThreads,
  normalizeGoals,
  normalizeIdeas,
  normalizeRichCard,
  normalizeThreadEvents,
  normalizeWatches,
  pauseBotTask,
  queueBotFollowups,
  resumeBotTask,
  retryBotTask,
  saveBotDraft,
  sendBotTurn,
  setBotGoal,
  setBotWatch,
  stopBotTurn,
  type Alert,
  type BotTaskDetail,
  type BotThread,
  type ComputerStatus,
  type DisabledBotAction,
  type Goal,
  type Idea,
  type RichCard,
  type ThreadEvent,
  type Watch,
} from "@/lib/ocFamilyBotApi";
import { refusalOrError } from "@/lib/policy-refusal";

function DisabledRow({ entry }: { entry: DisabledBotAction }) {
  return (
    <div
      className="rounded-md border border-dashed p-3 space-y-1"
      data-testid={`disabled-${entry.code}`}
    >
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

function fmtStamp(value: string | number | undefined): string {
  if (value === undefined || value === "") return "";
  if (typeof value === "number") {
    const ms = value < 1e12 ? value * 1000 : value;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
  }
  return value;
}

export default function QubitBotPage() {
  const [threads, setThreads] = useState<BotThread[]>([]);
  const [activeThread, setActiveThread] = useState("");
  const [events, setEvents] = useState<ThreadEvent[]>([]);
  const [composer, setComposer] = useState("");
  const [followup, setFollowup] = useState("");
  const [queued, setQueued] = useState<string[]>([]);
  const [turnRunning, setTurnRunning] = useState(false);

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
  const [error, setError] = useState("");
  const { toast, showToast } = useToast();

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

  const fail = useCallback(
    (e: unknown, fallback: string) => {
      const message = refusalOrError(e, fallback);
      setError(message);
      showToast(message, "error");
    },
    [showToast],
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
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
      setComputer(
        inner && typeof inner === "object" ? (inner as ComputerStatus) : { state: "unknown" },
      );
    } catch (e) {
      fail(e, "Failed to load the bot workspace.");
    } finally {
      setLoading(false);
    }
  }, [fail, taskFilter]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const openThread = useCallback(
    async (id: string) => {
      if (busy || !id) return;
      setBusy(true);
      setError("");
      try {
        const payload = await getBotThread(id);
        setActiveThread(id);
        setEvents(normalizeThreadEvents(payload));
        const match = threads.find((t) => t.id === id);
        if (match?.draft) setComposer(match.draft);
      } catch (e) {
        fail(e, "Thread read failed.");
      } finally {
        setBusy(false);
      }
    },
    [busy, fail, threads],
  );

  const sendTurn = useCallback(async () => {
    const text = composer.trim();
    if (busy || turnRunning || !text) return;
    setBusy(true);
    setTurnRunning(true);
    setError("");
    try {
      const payload = await sendBotTurn(
        { text, thread: activeThread || undefined },
        (detailText) => askBrokerConfirm("Send bot turn", detailText),
      );
      setEvents(normalizeThreadEvents(payload));
      setComposer("");
      showToast("Turn accepted", "success");
    } catch (e) {
      fail(e, "Send failed.");
    } finally {
      setBusy(false);
      setTurnRunning(false);
    }
  }, [activeThread, askBrokerConfirm, busy, composer, fail, showToast, turnRunning]);

  const stopTurn = useCallback(async () => {
    if (busy || !turnRunning) return;
    setBusy(true);
    setError("");
    try {
      await stopBotTurn(activeThread || "main", (detailText) =>
        askBrokerConfirm("Stop bot turn", detailText),
      );
      showToast("Turn stopped — draft kept, queue paused", "success");
    } catch (e) {
      fail(e, "Stop failed.");
    } finally {
      setBusy(false);
      setTurnRunning(false);
    }
  }, [activeThread, askBrokerConfirm, busy, fail, showToast, turnRunning]);

  const queueFollowup = useCallback(async () => {
    const text = followup.trim();
    if (busy || !text) return;
    const next = [...queued, text];
    setQueued(next);
    setFollowup("");
    setError("");
    try {
      await queueBotFollowups(
        { thread: activeThread || "main", messages: next },
        (detailText) => askBrokerConfirm("Queue follow-ups", detailText),
      );
      showToast("Follow-up queued", "success");
    } catch (e) {
      fail(e, "Queue failed.");
    }
  }, [activeThread, askBrokerConfirm, busy, fail, followup, queued, showToast]);

  const removeQueued = useCallback((index: number) => {
    setQueued((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const sendQueued = useCallback(async () => {
    if (busy || turnRunning || queued.length === 0) return;
    const [first, ...rest] = queued;
    if (first === undefined) return;
    setQueued(rest);
    setComposer(first);
  }, [busy, queued, turnRunning]);

  const saveDraft = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await saveBotDraft(
        { thread: activeThread || "main", text: composer },
        (detailText) => askBrokerConfirm("Save draft", detailText),
      );
      showToast("Draft saved", "success");
    } catch (e) {
      fail(e, "Draft save failed.");
    } finally {
      setBusy(false);
    }
  }, [activeThread, askBrokerConfirm, busy, composer, fail, showToast]);

  const lookUpTask = useCallback(async () => {
    const q = taskLookup.trim();
    if (busy || !q) return;
    setBusy(true);
    setError("");
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
      fail(e, "Task lookup failed.");
    } finally {
      setBusy(false);
    }
  }, [busy, fail, taskLookup]);

  const launchTask = useCallback(async () => {
    const title = launchTitle.trim();
    const prompt = launchPrompt.trim();
    if (busy || !title || !prompt) return;
    setBusy(true);
    setError("");
    try {
      await launchBotTask({ title, prompt }, (detailText) =>
        askBrokerConfirm("Launch delegated task", detailText),
      );
      showToast("Task launched", "success");
      setLaunchTitle("");
      setLaunchPrompt("");
      await loadAll();
    } catch (e) {
      fail(e, "Launch failed.");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, fail, launchPrompt, launchTitle, loadAll, showToast]);

  const runTaskControl = useCallback(
    async (kind: "pause" | "resume" | "cancel" | "retry") => {
      const id = (taskDetail?.id ?? taskLookup.trim()).trim();
      if (busy || !id) return;
      setBusy(true);
      setError("");
      const title = kind === "pause" ? "Pause" : kind === "resume" ? "Resume" : kind === "cancel" ? "Cancel" : "Retry";
      try {
        const call =
          kind === "pause"
            ? pauseBotTask(id, (detailText) => askBrokerConfirm(`${title} delegated task`, detailText))
            : kind === "resume"
              ? resumeBotTask(id, (detailText) => askBrokerConfirm(`${title} delegated task`, detailText))
              : kind === "cancel"
                ? cancelBotTask({ taskId: id }, (detailText) =>
                    askBrokerConfirm(`${title} delegated task`, detailText),
                  )
                : retryBotTask({ taskId: id }, (detailText) =>
                    askBrokerConfirm(`${title} delegated task`, detailText),
                  );
        await call;
        showToast(`${title} accepted`, "success");
        await loadAll();
      } catch (e) {
        fail(e, `${title} failed.`);
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, showToast, taskDetail, taskLookup],
  );

  const runIdea = useCallback(
    async (kind: "accept" | "dismiss", id: string) => {
      if (busy || !id) return;
      setBusy(true);
      setError("");
      try {
        if (kind === "accept") {
          await acceptBotIdea(id, (detailText) => askBrokerConfirm("Accept idea", detailText));
          showToast("Idea accepted — worker launched", "success");
        } else {
          await dismissBotIdea(id, (detailText) => askBrokerConfirm("Dismiss idea", detailText));
          showToast("Idea dismissed", "success");
        }
        await loadAll();
      } catch (e) {
        fail(e, kind === "accept" ? "Accept failed." : "Dismiss failed.");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, showToast],
  );

  const saveIdeaEdit = useCallback(
    async (idea: Idea) => {
      if (busy || !idea.id) return;
      setBusy(true);
      setError("");
      try {
        await editBotIdea(
          { idea: idea.id, title: idea.title, reason: idea.reason, prompt: idea.prompt },
          (detailText) => askBrokerConfirm("Save idea edit", detailText),
        );
        showToast("Idea updated", "success");
        await loadAll();
      } catch (e) {
        fail(e, "Idea edit failed.");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, showToast],
  );

  const saveGoal = useCallback(async () => {
    const title = goalTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    setError("");
    try {
      await setBotGoal(
        { title, description: goalDescription.trim() || undefined },
        (detailText) => askBrokerConfirm("Record goal", detailText),
      );
      showToast("Goal recorded", "success");
      setGoalTitle("");
      setGoalDescription("");
      await loadAll();
    } catch (e) {
      fail(e, "Goal save failed.");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, fail, goalDescription, goalTitle, loadAll, showToast]);

  const saveWatch = useCallback(async () => {
    const title = watchTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    setError("");
    try {
      await setBotWatch(
        { title, url: watchUrl.trim() || undefined },
        (detailText) => askBrokerConfirm("Record watch", detailText),
      );
      showToast("Watch recorded", "success");
      setWatchTitle("");
      setWatchUrl("");
      await loadAll();
    } catch (e) {
      fail(e, "Watch save failed.");
    } finally {
      setBusy(false);
    }
  }, [askBrokerConfirm, busy, fail, loadAll, showToast, watchTitle, watchUrl]);

  const lookUpCard = useCallback(async () => {
    const q = cardLookup.trim();
    if (busy || !q) return;
    setBusy(true);
    setError("");
    setCard(null);
    setCardMissing(false);
    try {
      const payload = await getBotCard(q);
      const row = normalizeRichCard(payload);
      if (!row) {
        setCardMissing(true);
      } else {
        setCard(row);
      }
    } catch (e) {
      fail(e, "Card lookup failed.");
    } finally {
      setBusy(false);
    }
  }, [busy, cardLookup, fail]);

  const unread = alerts.filter((a) => !a.read);

  return (
    <div className="space-y-6 p-4">
      <div>
        <h1 className="text-xl font-semibold">Qubit Bot</h1>
        <p className="text-sm text-muted-foreground">
          One workspace for conversation and delegated work: thread turns with
          streamed events, durable tasks with plan and receipts, ideas with
          evidence, goals and watches with alerts, the computer console, and
          rich cards.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center gap-2">
          <Spinner /> <span className="text-sm">Loading the bot workspace…</span>
        </div>
      ) : null}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-4 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-medium">Thread</h2>
            <Button outlined size="sm" onClick={() => loadAll()} disabled={loading}>
              Reload
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Open chats stay mounted while navigating; drafts and queued
            follow-ups are held in the open page, not a server inbox.
          </p>
          {threads.length === 0 && !loading ? (
            <p className="text-sm text-muted-foreground">No threads yet.</p>
          ) : (
            <ul className="space-y-1">
              {threads.map((t) => (
                <li key={t.id} className="flex items-center gap-2 text-sm flex-wrap">
                  <code className="text-xs font-mono">{t.id}</code>
                  {t.title ? <span>{t.title}</span> : null}
                  {t.messageCount !== undefined ? (
                    <span className="text-muted-foreground">· {t.messageCount} messages</span>
                  ) : null}
                  {t.draft ? <Badge tone="secondary">draft</Badge> : null}
                  <Button
                    outlined
                    size="sm"
                    disabled={busy || activeThread === t.id}
                    onClick={() => openThread(t.id)}
                  >
                    Open
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {events.length > 0 ? (
            <ul className="space-y-1" data-testid="bot-thread-events">
              {events.map((ev, i) => (
                <li key={ev.id ?? i} className="flex items-center gap-2 text-sm flex-wrap">
                  <Badge tone="outline">{ev.kind}</Badge>
                  {ev.title ? <span className="font-medium">{ev.title}</span> : null}
                  {ev.text ? <span className="text-muted-foreground">{ev.text}</span> : null}
                  {ev.state ? <Badge tone="secondary">{ev.state}</Badge> : null}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="space-y-1">
            <Label htmlFor="bot-composer">Message</Label>
            <div className="flex items-end gap-2 flex-wrap">
              <Input
                id="bot-composer"
                value={composer}
                onChange={(e) => setComposer(e.target.value)}
                placeholder="Ask the bot…"
              />
              {turnRunning ? (
                <Button disabled={busy} onClick={() => stopTurn()}>
                  Stop
                </Button>
              ) : (
                <Button disabled={busy || !composer.trim()} onClick={() => sendTurn()}>
                  Send
                </Button>
              )}
              <Button outlined disabled={busy} onClick={() => saveDraft()}>
                Save draft
              </Button>
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="bot-followup">Follow-ups (run in order after this turn)</Label>
            <div className="flex items-end gap-2 flex-wrap">
              <Input
                id="bot-followup"
                value={followup}
                onChange={(e) => setFollowup(e.target.value)}
                placeholder="Queue a follow-up…"
              />
              <Button outlined disabled={busy || !followup.trim()} onClick={() => queueFollowup()}>
                Queue
              </Button>
              <Button outlined disabled={busy || turnRunning || queued.length === 0} onClick={() => sendQueued()}>
                Send queued messages
              </Button>
            </div>
            {queued.length > 0 ? (
              <ul className="space-y-1">
                {queued.map((q, i) => (
                  <li key={`${i}-${q}`} className="flex items-center gap-2 text-sm flex-wrap">
                    <Badge tone="outline">{i + 1}</Badge>
                    <span>{q}</span>
                    <Button outlined size="sm" onClick={() => removeQueued(i)}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </CardContent>
      </Card>

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
            Durable server work: launching survives navigation; plan, progress,
            and receipts report back here.
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
                  <span>{t.title}</span>
                  {t.progressPercent !== undefined ? (
                    <span className="text-muted-foreground">
                      · {t.progressPercent}%{t.progress ? ` — ${t.progress}` : ""}
                    </span>
                  ) : t.progress ? (
                    <span className="text-muted-foreground">· {t.progress}</span>
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
          </div>
          {taskMissing ? (
            <p className="text-sm text-muted-foreground">No task matches that lookup.</p>
          ) : null}
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
                          {s.detail ? (
                            <span className="text-muted-foreground">— {s.detail}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              ) : null}
              {taskDetail.progressPercent !== undefined || taskDetail.progress ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Progress:</dt>
                  <dd>
                    {taskDetail.progressPercent !== undefined
                      ? `${taskDetail.progressPercent}%${taskDetail.progress ? ` — ${taskDetail.progress}` : ""}`
                      : (taskDetail.progress ?? "")}
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
              {taskDetail.receiptId ? (
                <div className="flex gap-2 flex-wrap">
                  <dt className="font-medium">Receipt:</dt>
                  <dd>
                    <code className="text-xs font-mono">{taskDetail.receiptId}</code>
                  </dd>
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
            Suggested work with evidence: accept to launch a worker, edit the
            record, or dismiss it.
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
                          {ev.url ? (
                            <code className="text-xs font-mono">{ev.url}</code>
                          ) : null}
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
            Milestones, recurring watches with alert conditions, and the durable
            alert inbox{unread.length > 0 ? ` — ${unread.length} unread` : ""}.
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
                  {w.condition ? (
                    <span className="text-muted-foreground">
                      · {w.condition}
                      {w.value ? ` ${w.value}` : ""}
                    </span>
                  ) : null}
                  {fmtStamp(w.nextCheckAt) ? (
                    <span className="text-muted-foreground">· next {fmtStamp(w.nextCheckAt)}</span>
                  ) : null}
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
            {alerts.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No alerts.</p>
            ) : (
              <ul className="space-y-1">
                {alerts.map((a) => (
                  <li key={a.id} className="flex items-center gap-2 text-sm flex-wrap">
                    {!a.read ? <Badge tone="secondary">new</Badge> : null}
                    <span className="font-medium">{a.title}</span>
                    {a.body ? <span className="text-muted-foreground">{a.body}</span> : null}
                    {fmtStamp(a.createdAt) ? (
                      <span className="text-muted-foreground">{fmtStamp(a.createdAt)}</span>
                    ) : null}
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
            The agent computer state, read-only. Interactive takeover stays
            operator-scoped — this surface reports state only and opens no new
            client.
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
            Plans, comparisons, finance summaries, and reports produced by
            delegated tasks.
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
          {card ? (
            <div className="rounded-md border p-3 space-y-1" data-testid="bot-rich-card">
              <div className="flex items-center gap-2 flex-wrap">
                <code className="text-xs font-mono">{card.id}</code>
                {card.kind ? <Badge tone="outline">{card.kind}</Badge> : null}
              </div>
              {card.title ? <p className="text-sm font-medium">{card.title}</p> : null}
              {card.summary ? <p className="text-sm text-muted-foreground">{card.summary}</p> : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Hosted persistence (not used)</h2>
          <p className="text-sm text-muted-foreground">
            The upstream hosted thread-persistence service is a separately
            configured service and is never called. Thread state lives in the
            canonical Qubit session store instead.
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
