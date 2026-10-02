/**
 * Qubit Bot — the delegated-work bot as its own page (route `/qubit-bot`).
 *
 * Chat UX ported from `CopilotKit/openmuse` mobile chat
 * (https://github.com/CopilotKit/openmuse) commit `9ec439f` — reimplemented
 * for web in the Qubit theme (no verbatim upstream code): centered hero with
 * starter prompts on an empty thread, right/left chat bubbles, typing
 * indicator, pill composer with circular send/stop, visible follow-up queue
 * with hold/resume, jump-to-latest, retry on failure. Below the chat: durable
 * delegated tasks with plan/progress/pause/resume/cancel/retry + receipts,
 * Ideas with evidence, Goals & watches with alerts, agent-computer console,
 * rich cards — all served by the Qubit family bridge
 * (`voicebridge/family_bot_harness.py`).
 */
import { ArrowUp, Square, X } from "lucide-react";
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
  resumeBotTask,
  retryBotTask,
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

interface QueuedMessage {
  id: string;
  text: string;
}

function newMessageId(): string {
  return `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Upstream queue key: the open thread, falling back to the main chat. */
function queueKey(thread: string): string {
  return thread || "main";
}

const TOOL_KINDS = new Set([
  "tool",
  "tool_call",
  "browser",
  "browse",
  "mail",
  "task",
  "delegate_task",
  "plan",
  "step",
  "artifact",
  "approval",
  "result",
  "error",
  "status",
  "observation",
  "computer",
  "pdf",
  "file",
  "choice",
  "agent_status",
  "memory",
  "goal",
  "tracking",
]);

function eventRole(kind: string): "user" | "tool" | "assistant" {
  const k = kind.toLowerCase();
  if (k === "user" || k === "user_message" || k === "human") return "user";
  if (TOOL_KINDS.has(k)) return "tool";
  return "assistant";
}

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

const STARTER_PROMPTS = [
  "Summarize what needs my attention",
  "Draft a plan for the week",
  "Watch a page for changes",
];

export default function QubitBotPage() {
  const [threads, setThreads] = useState<BotThread[]>([]);
  const [activeThread, setActiveThread] = useState("");

  // Visited chats stay mounted: transcripts, drafts, and queues are keyed by
  // thread and survive navigation between threads (upstream ThreadsProvider
  // `visited` behavior). Refs are the source of truth; ticks re-render.
  const transcriptsRef = useRef<Record<string, ThreadEvent[]>>({});
  const draftsRef = useRef<Record<string, string>>({});
  const queuesRef = useRef<Record<string, QueuedMessage[]>>({});
  const pausedRef = useRef<Record<string, boolean>>({});
  const flushingRef = useRef<Record<string, boolean>>({});
  const [storeTick, setStoreTick] = useState(0);
  const bumpStore = useCallback(() => setStoreTick((t) => t + 1), []);

  const key = queueKey(activeThread);
  const visibleEvents = transcriptsRef.current[key] ?? [];
  const queued: QueuedMessage[] = queuesRef.current[key] ?? [];
  const queuePaused = pausedRef.current[key] ?? false;

  const [composer, setComposer] = useState("");
  const [followup, setFollowup] = useState("");
  const [turnRunning, setTurnRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followLatestRef = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);

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

  const appendEvents = useCallback(
    (threadKey: string, evs: ThreadEvent[]) => {
      if (evs.length === 0) return;
      transcriptsRef.current[threadKey] = [...(transcriptsRef.current[threadKey] ?? []), ...evs];
      bumpStore();
    },
    [bumpStore],
  );

  // One turn at a time per thread; follow-ups run in order after the current
  // turn. A failure pauses the queue — the failed message is already in the
  // transcript and is never resent implicitly (upstream ConversationQueue).
  const flushQueue = useCallback(
    async (threadKey: string, threadId: string) => {
      if (flushingRef.current[threadKey]) return;
      if (pausedRef.current[threadKey]) return;
      if ((queuesRef.current[threadKey] ?? []).length === 0) return;
      flushingRef.current[threadKey] = true;
      setTurnRunning(true);
      setError("");
      try {
        while (
          (queuesRef.current[threadKey] ?? []).length > 0 &&
          !pausedRef.current[threadKey]
        ) {
          const [message, ...rest] = queuesRef.current[threadKey] ?? [];
          if (!message) break;
          queuesRef.current[threadKey] = rest;
          bumpStore();
          appendEvents(threadKey, [{ kind: "user", text: message.text }]);
          try {
            const payload = await sendBotTurn(
              { text: message.text, thread: threadId || undefined },
              (detailText) => askBrokerConfirm("Send bot turn", detailText),
            );
            appendEvents(threadKey, normalizeThreadEvents(payload));
          } catch (e) {
            pausedRef.current[threadKey] = true;
            bumpStore();
            fail(e, "Send failed — the queue is paused, nothing was resent.");
            break;
          }
        }
      } finally {
        flushingRef.current[threadKey] = false;
        setTurnRunning(false);
      }
    },
    [appendEvents, askBrokerConfirm, bumpStore, fail],
  );

  const openThread = useCallback(
    async (id: string) => {
      if (busy || !id || id === activeThread) return;
      // The open draft stays mounted on the thread being left.
      draftsRef.current[queueKey(activeThread)] = composer;
      setBusy(true);
      setError("");
      try {
        const payload = await getBotThread(id);
        const k = queueKey(id);
        transcriptsRef.current[k] = normalizeThreadEvents(payload);
        setActiveThread(id);
        setComposer(draftsRef.current[k] ?? threads.find((t) => t.id === id)?.draft ?? "");
        followLatestRef.current = true;
        setAwayFromLatest(false);
        bumpStore();
      } catch (e) {
        fail(e, "Thread read failed.");
      } finally {
        setBusy(false);
      }
    },
    [activeThread, busy, bumpStore, composer, fail, threads],
  );

  // Composer send: a fresh submission resumes an idle, empty queue (upstream
  // `send`), otherwise it joins the visible queue behind the running turn.
  const send = useCallback(() => {
    const text = composer.trim();
    if (!text || stopping) return;
    const k = queueKey(activeThread);
    if (!turnRunning && (queuesRef.current[k] ?? []).length === 0) {
      pausedRef.current[k] = false;
    }
    queuesRef.current[k] = [...(queuesRef.current[k] ?? []), { id: newMessageId(), text }];
    setComposer("");
    draftsRef.current[k] = "";
    followLatestRef.current = true;
    setAwayFromLatest(false);
    bumpStore();
    void flushQueue(k, activeThread);
  }, [activeThread, bumpStore, composer, flushQueue, stopping, turnRunning]);

  // Stop: pause the follow-up queue first, then cancel the turn. The draft is
  // preserved and delegated tasks are never cancelled (upstream `stop`).
  const stopTurn = useCallback(async () => {
    if (!turnRunning || stopping) return;
    const k = queueKey(activeThread);
    pausedRef.current[k] = true;
    bumpStore();
    setStopping(true);
    setError("");
    try {
      await stopBotTurn(activeThread || "main", (detailText) =>
        askBrokerConfirm("Stop bot turn", detailText),
      );
      showToast("Turn stopped — draft kept, queue paused", "success");
    } catch (e) {
      fail(e, "Stop failed.");
    } finally {
      setStopping(false);
      if (!flushingRef.current[k]) setTurnRunning(false);
    }
  }, [activeThread, askBrokerConfirm, bumpStore, fail, showToast, stopping, turnRunning]);

  // Follow-ups are held in the open page (no server inbox): queue locally in
  // order, removable, resumed explicitly via "Send queued messages".
  const queueFollowup = useCallback(() => {
    const text = followup.trim();
    if (!text) return;
    const k = queueKey(activeThread);
    queuesRef.current[k] = [...(queuesRef.current[k] ?? []), { id: newMessageId(), text }];
    setFollowup("");
    bumpStore();
  }, [activeThread, bumpStore, followup]);

  const removeQueued = useCallback(
    (index: number) => {
      const k = queueKey(activeThread);
      queuesRef.current[k] = (queuesRef.current[k] ?? []).filter((_, i) => i !== index);
      bumpStore();
    },
    [activeThread, bumpStore],
  );

  const sendQueued = useCallback(() => {
    const k = queueKey(activeThread);
    if ((queuesRef.current[k] ?? []).length === 0) return;
    // Held follow-ups need an explicit resume (upstream: stopping pauses the
    // queue; "Send queued messages" resumes it).
    pausedRef.current[k] = false;
    followLatestRef.current = true;
    setAwayFromLatest(false);
    bumpStore();
    void flushQueue(k, activeThread);
  }, [activeThread, bumpStore, flushQueue]);

  const enqueueStarter = useCallback(
    (text: string) => {
      const k = queueKey(activeThread);
      if (!turnRunning && (queuesRef.current[k] ?? []).length === 0) {
        pausedRef.current[k] = false;
      }
      queuesRef.current[k] = [...(queuesRef.current[k] ?? []), { id: newMessageId(), text }];
      bumpStore();
      void flushQueue(k, activeThread);
    },
    [activeThread, bumpStore, flushQueue, turnRunning],
  );

  const onThreadScroll = useCallback(
    (el: HTMLDivElement) => {
      const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      followLatestRef.current = nearEnd;
      setAwayFromLatest(visibleEvents.length > 0 && !nearEnd);
    },
    [visibleEvents.length],
  );

  useEffect(() => {
    if (followLatestRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [visibleEvents.length, turnRunning, storeTick]);

  const jumpToLatest = useCallback(() => {
    followLatestRef.current = true;
    setAwayFromLatest(false);
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  const lookUpTask = useCallback(
    async (id?: string) => {
      const q = (id ?? taskLookup).trim();
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
    },
    [busy, fail, taskLookup],
  );

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
  const activeTitle = threads.find((t) => t.id === activeThread)?.title;

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
      {error ? (
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm text-destructive">{error}</p>
          {queuePaused && queued.length > 0 ? (
            <Button outlined size="sm" disabled={busy} onClick={() => sendQueued()}>
              Retry response
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-medium">Threads</h2>
              <Button outlined size="sm" onClick={() => loadAll()} disabled={loading}>
                Reload
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              Open chats stay mounted while navigating; drafts and queued
              follow-ups are saved on the server too.
            </p>
            {threads.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No threads yet.</p>
            ) : (
              <ul className="space-y-1">
                {threads.map((t) => {
                  const k = queueKey(t.id);
                  const pending = (queuesRef.current[k] ?? []).length;
                  const isActive = activeThread === t.id;
                  return (
                    <li
                      key={t.id}
                      title={t.id}
                      className={`flex items-center gap-2 text-sm flex-wrap rounded-full px-3 py-2 ${isActive ? "bg-muted font-medium" : ""}`}
                    >
                      <span className="truncate max-w-[140px]">{t.title ?? t.id}</span>
                      {t.messageCount !== undefined ? (
                        <span className="text-muted-foreground">· {t.messageCount} messages</span>
                      ) : null}
                      {(draftsRef.current[k] ?? t.draft) ? <Badge tone="secondary">draft</Badge> : null}
                      {pending > 0 ? <Badge tone="outline">{pending} queued</Badge> : null}
                      {pausedRef.current[k] && pending > 0 ? (
                        <Badge tone="secondary">paused</Badge>
                      ) : null}
                      <Button
                        outlined
                        size="sm"
                        disabled={busy || isActive}
                        onClick={() => openThread(t.id)}
                      >
                        Open
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-medium">
                {activeTitle ?? (activeThread || "Thread")}
              </h2>
              {turnRunning ? <Badge tone="outline">replying…</Badge> : null}
              {queuePaused && queued.length > 0 ? (
                <Badge tone="secondary">queue paused</Badge>
              ) : null}
            </div>

            <div
              ref={scrollRef}
              onScroll={(e) => onThreadScroll(e.currentTarget)}
              className="relative max-h-[420px] space-y-3 overflow-y-auto rounded-md bg-background p-3"
              data-testid="bot-thread-events"
            >
              {visibleEvents.length === 0 ? (
                <div className="flex flex-col items-center gap-3 py-8 text-center">
                  <p className="text-lg font-medium">
                    A little help. A lot more room for life.
                  </p>
                  <p className="max-w-sm text-sm text-muted-foreground">
                    Tell me what&apos;s on your mind. I can make a plan, track
                    work across your apps, and report back here.
                  </p>
                  <div className="flex flex-col gap-2 w-full max-w-xs">
                    {STARTER_PROMPTS.map((prompt) => (
                      <Button key={prompt} outlined size="sm" onClick={() => enqueueStarter(prompt)}>
                        {prompt}
                      </Button>
                    ))}
                  </div>
                </div>
              ) : (
                visibleEvents.map((ev, i) => {
                  const role = eventRole(ev.kind);
                  if (role === "user") {
                    return (
                      <div key={ev.id ?? i} className="flex justify-end">
                        <div className="max-w-[85%] rounded-[22px] rounded-br-[7px] bg-sky-200 px-4 py-3 text-sm text-sky-950">
                          {ev.title ? <p className="font-medium">{ev.title}</p> : null}
                          {ev.text ? <p>{ev.text}</p> : null}
                        </div>
                      </div>
                    );
                  }
                  if (role === "tool") {
                    return (
                      <div
                        key={ev.id ?? i}
                        className="mr-auto max-w-[85%] space-y-1 rounded-md border p-2"
                      >
                        <div className="flex items-center gap-2 flex-wrap">
                          <Badge tone="outline">{ev.kind}</Badge>
                          {ev.state ? <Badge tone="secondary">{ev.state}</Badge> : null}
                        </div>
                        {ev.title ? <p className="text-sm font-medium">{ev.title}</p> : null}
                        {ev.text ? (
                          <p className="text-sm text-muted-foreground">{ev.text}</p>
                        ) : null}
                      </div>
                    );
                  }
                  return (
                    <div key={ev.id ?? i} className="flex justify-start">
                      <div className="max-w-[85%] rounded-[22px] rounded-bl-[7px] bg-muted px-4 py-3 text-sm">
                        {ev.title ? <p className="font-medium">{ev.title}</p> : null}
                        {ev.text ? <p>{ev.text}</p> : null}
                        {ev.state ? (
                          <p className="mt-1">
                            <Badge tone="secondary">{ev.state}</Badge>
                          </p>
                        ) : null}
                      </div>
                    </div>
                  );
                })
              )}
              {turnRunning ? (
                <div className="flex justify-start" aria-label="Agent is working">
                  <div className="flex items-center gap-[7px] rounded-[28px] bg-muted px-[19px] py-[18px]">
                    {[0.4, 0.75, 0.5].map((opacity) => (
                      <span
                        key={opacity}
                        className="h-2 w-2 rounded-full bg-muted-foreground"
                        style={{ opacity }}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
              {awayFromLatest ? (
                <div className="sticky bottom-2 flex justify-center">
                  <Button outlined size="sm" onClick={() => jumpToLatest()}>
                    Jump to latest
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="bot-composer">Message Qubit Bot</Label>
              <div className="flex items-end gap-[7px] rounded-[32px] border bg-card p-2">
                <Input
                  id="bot-composer"
                  value={composer}
                  className="border-0 bg-transparent shadow-none focus-visible:border-0 focus-visible:ring-0"
                  onChange={(e) => {
                    setComposer(e.target.value);
                    draftsRef.current[key] = e.target.value;
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder={
                    turnRunning ? "Reply… (composer stays editable)" : "Message…"
                  }
                />
                {turnRunning ? (
                  <button
                    type="button"
                    aria-label="Stop reply"
                    disabled={stopping}
                    onClick={() => stopTurn()}
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-sky-200 text-sky-950 disabled:opacity-50"
                  >
                    <Square size={18} fill="currentColor" strokeWidth={0} />
                  </button>
                ) : (
                  <button
                    type="button"
                    aria-label="Send message"
                    disabled={!composer.trim()}
                    onClick={() => send()}
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-sky-200 text-sky-950 disabled:bg-muted disabled:text-muted-foreground"
                  >
                    <ArrowUp size={25} strokeWidth={1.8} />
                  </button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Drafts are saved on this thread — stopping keeps the draft.
              </p>
            </div>

            <div className="space-y-1">
              <Label htmlFor="bot-followup">Follow-ups (run in order after this turn)</Label>
              <div className="flex items-end gap-2 flex-wrap">
                <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-[32px] border bg-card p-2">
                  <Input
                    id="bot-followup"
                    value={followup}
                    className="border-0 bg-transparent shadow-none focus-visible:border-0 focus-visible:ring-0"
                    onChange={(e) => setFollowup(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        queueFollowup();
                      }
                    }}
                    placeholder="Queue a follow-up…"
                  />
                  <Button outlined disabled={!followup.trim()} onClick={() => queueFollowup()}>
                    Queue
                  </Button>
                </div>
                <Button outlined disabled={queued.length === 0} onClick={() => sendQueued()}>
                  Send queued messages
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Queued follow-ups run in order after this turn — stopping
                pauses the queue, it never cancels delegated tasks.
              </p>
              {queued.length > 0 ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    {queuePaused ? "Messages on hold" : "Up next"} ·{" "}
                    {queued.length} queued — keep the page open until sent
                  </p>
                  {queuePaused ? (
                    <Button outlined size="sm" disabled={queued.length === 0} onClick={() => sendQueued()}>
                      Send queued messages
                    </Button>
                  ) : null}
                </>
              ) : null}
              {queued.length > 0 ? (
                <ul className="space-y-1">
                  {queued.map((q, i) => (
                    <li key={q.id} className="flex items-center gap-2 text-sm flex-wrap rounded-full bg-muted px-3 py-1">
                      <Badge tone="outline">{i + 1}</Badge>
                      <span className="min-w-0 flex-1">{q.text}</span>
                      <button
                        type="button"
                        aria-label={`Remove queued message: ${q.text}`}
                        onClick={() => removeQueued(i)}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:text-foreground"
                      >
                        <X size={16} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
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
            Durable server work: launching survives navigation; plan, progress,
            and receipts report back here. Tap a task to see its activity and
            receipts.
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
            {visibleAlerts.length === 0 && !loading ? (
              <p className="text-sm text-muted-foreground">No alerts.</p>
            ) : (
              <ul className="space-y-1">
                {visibleAlerts.map((a) => (
                  <li key={a.id} className="flex items-center gap-2 text-sm flex-wrap rounded-full bg-muted px-3 py-1">
                    {!a.read ? <Badge tone="secondary">new</Badge> : null}
                    <span className="font-medium">{a.title}</span>
                    {a.body ? <span className="text-muted-foreground">{a.body}</span> : null}
                    {fmtStamp(a.createdAt) ? (
                      <span className="text-muted-foreground">{fmtStamp(a.createdAt)}</span>
                    ) : null}
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
