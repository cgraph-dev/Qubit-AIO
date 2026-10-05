/**
 * Qubit Bot — the delegated-work bot as its own page (route `/qubit-bot`).
 *
 * The chat surface is a 1:1 port of the external upstream project
 * CopilotKit/openmuse (https://github.com/CopilotKit/openmuse, MIT) commit
 * `9ec439f`, `apps/mobile/src/chat.tsx`. Upstream drives that surface through
 * `@copilotkit/react-native/headless`; Qubit replaces that agent runtime with
 * `@/lib/botchat/runtime` (see that module for the full seam map) while keeping
 * the view identical: centered hero with starter prompts on an empty thread,
 * right/left bubbles, per-tool inline cards, typing indicator, expandable pill
 * composer with a circular send/stop button, a visible follow-up queue with
 * remove / hold / resume, jump-to-latest, and three distinct error states
 * (send retry, save retry, history-load). Below the chat: durable delegated
 * tasks, ideas with evidence, goals & watches with alerts, the agent-computer
 * console, and rich cards — all served by the Qubit family bridge.
 *
 * Styling ruling (owner): keep the Qubit DARK theme, adopt upstream's exact
 * component shapes/spacing/radii (single-sourced in `BOT_GEOMETRY` and the
 * `qb-*` classes in `src/styles/upstream/botchat.css`). Colours are Qubit
 * theme tokens only.
 */
import { ChevronDown, FileText, Plus, RotateCcw, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
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
  type BotTask,
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
import { Markdown } from "@/components/Markdown";
import {
  BOT_AGENT_CONTEXT,
  newQueuedId,
  toolCardKind,
  useBotAgent,
  type QueuedMessage,
} from "@/lib/botchat/runtime";
import { ArtifactCard, TaskThreadCard, ToolCard } from "@/components/botchat/ToolCards";

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Starter prompts — upstream `chat.tsx` empty-thread hero (lines 452-468).
 * Each item carries the visible label AND the exact action upstream wires:
 * the first two enqueue a (possibly different) message; the third navigates to
 * the goals screen. Qubit mirrors that split: the second button's label equals
 * the message it sends, and the third opens the durable-watch surface rather
 * than composing a chat message.
 */
export interface StarterPrompt {
  /** Visible button label (upstream `item.text`). */
  label: string;
  /** Message enqueued on press (upstream `item.action` → `enqueue(text)`). */
  sends?: string;
  /** Navigate-to surface instead of sending (upstream `navigate("goals")`). */
  navigatesTo?: "workspace";
}

export const STARTER_PROMPTS: StarterPrompt[] = [
  { label: "Find cool things on Hacker News", sends: "Check out Hacker News for cool stuff" },
  { label: "Summarize copilotkit.ai", sends: "Summarize copilotkit.ai" },
  { label: "Keep an eye on a website", navigatesTo: "workspace" },
];

/** Event roles, ported from the upstream run-event vocabulary. */
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

/** Upstream ScrollView geometry: gap 13, top 15, bottom 20 (see botchat.css). */

export default function QubitBotPage() {
  const [threads, setThreads] = useState<BotThread[]>([]);
  const [activeThread, setActiveThread] = useState("");

  // Visited chats stay mounted: drafts survive navigation between threads.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [composer, setComposer] = useState("");
  const [composerFocused, setComposerFocused] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [picking, setPicking] = useState(false);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [showResults, setShowResults] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [followLatest, setFollowLatest] = useState(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const [followupDraft, setFollowupDraft] = useState("");
  const [showTaskInline, setShowTaskInline] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const threadKey = activeThread || "main";
  const agent = useBotAgent({ threadId: threadKey });
  const {
    messages,
    running,
    error,
    loaded,
    setError,
    queue,
    snapshot,
    hydrate,
    sendTurn,
    stop,
    appendUser,
  } = agent;

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

  const fail = useCallback(
    (e: unknown, fallback: string) => {
      const message = refusalOrError(e, fallback);
      setError(message);
      showToast(message, "error");
    },
    [setError, showToast],
  );

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
      fail(e, "Failed to load the bot workspace.");
    } finally {
      setLoading(false);
    }
  }, [fail, taskFilter]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Attachment documents: the thread's file/artifact events become pickable.
  // Derived from the transcript — no extra state, so no cascading render.
  const documents = messages
    .filter((m) => m.kind === "file" || m.kind === "pdf" || m.kind === "artifact")
    .map((m, i) => ({ id: m.id ?? `doc-${i}`, name: m.title ?? m.text ?? "document" }));

  // ── transcript hydrate (upstream `hydrate`) ───────────────────────────
  useEffect(() => {
    let active = true;
    setHistoryError("");
    void hydrate().catch((e) => {
      if (!active) return;
      setHistoryError(
        `Could not load conversation. Your saved messages have not been changed. ${
          e instanceof Error ? e.message : String(e)
        }`,
      );
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadKey, historyAttempt]);

  // ── queue mechanics (upstream `run` / `flush` / `saveHistory`) ─────────
  const runQueued = useCallback(
    async (message: QueuedMessage) => {
      appendUser(message);
      try {
        await sendTurn(message.text);
      } catch (e) {
        // Preserve the transcript: the failed turn stays, the queue pauses, and
        // the failure surfaces as a retry — never an implicit resend.
        fail(e, "Send failed — the queue is paused, nothing was resent.");
        throw e instanceof Error ? e : new Error(String(e));
      }
    },
    [appendUser, fail, sendTurn],
  );

  const flush = useCallback(() => {
    if (running || snapshot.paused) return;
    void queue.flush(runQueued).catch((e) => {
      // A flush failure pauses the queue (inside ConversationQueue). Also
      // mirror it into the composer-level save error state for the retry row.
      setSaveError(`Conversation could not be saved: ${e instanceof Error ? e.message : String(e)}`);
    });
  }, [queue, runQueued, running, snapshot.paused]);

  const enqueue = useCallback(
    (text: string) => {
      queue.enqueue({ id: newQueuedId(), text });
      setFollowLatest(true);
      setAwayFromLatest(false);
      flush();
    },
    [flush, queue],
  );

  useEffect(() => {
    if (!running && snapshot.pending.length) flush();
  }, [flush, running, snapshot.pending.length]);

  // ── send / stop (upstream `send` / `stop`) ────────────────────────────
  const send = useCallback(() => {
    const text = composer.trim();
    if (!text) return;
    // A fresh submission resumes an idle, empty queue.
    if (!running && !snapshot.pending.length && !saveError) queue.resume();
    setShowResults(false);
    const files = documents.filter((f) => attachments.includes(f.id));
    enqueue(
      text +
        (files.length
          ? `\n\nAttached documents: ${files.map((f) => `${f.name} (artifact ID: ${f.id})`).join(", ")}`
          : ""),
    );
    setComposer("");
    setAttachments([]);
    setPicking(false);
    setDrafts((d) => ({ ...d, [threadKey]: "" }));
  }, [attachments, composer, documents, enqueue, queue, running, saveError, snapshot.pending.length, threadKey]);

  const stopTurn = useCallback(async () => {
    setStopping(true);
    try {
      await stop();
    } finally {
      setStopping(false);
    }
  }, [stop]);

  const retry = useCallback(() => {
    setError("");
    flush();
  }, [flush, setError]);

  // Queue a follow-up locally in order (upstream `enqueue`); it runs after the
  // current turn. Held (paused) queues need an explicit "Send queued messages".
  const queueFollowup = useCallback(() => {
    const text = followupDraft.trim();
    if (!text) return;
    queue.enqueue({ id: newQueuedId(), text });
    setFollowupDraft("");
  }, [followupDraft, queue]);

  const saveHistoryRetry = useCallback(() => {
    setSaveError("");
    flush();
  }, [flush]);

  const openThread = useCallback(
    async (id: string) => {
      if (busy || !id || id === activeThread) return;
      setDrafts((d) => ({ ...d, [threadKey]: composer }));
      setActiveThread(id);
      setComposer(drafts[id] ?? "");
      setFollowLatest(true);
      setAwayFromLatest(false);
      setHistoryError("");
    },
    [activeThread, busy, composer, drafts, threadKey],
  );

  const onThreadScroll = useCallback(
    (el: HTMLDivElement) => {
      const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
      setFollowLatest(nearEnd);
      setAwayFromLatest(messages.length > 0 && !nearEnd);
    },
    [messages.length],
  );

  useEffect(() => {
    if (followLatest && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [followLatest, messages.length, running]);

  // Auto-grow composer (upstream `onContentSizeChange`: height clamped to
  // [44, 140]). Reset first so the box shrinks when text is deleted, then
  // measure the content and clamp — the same formula upstream applies.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.max(44, Math.min(140, el.scrollHeight));
    el.style.height = `${next}px`;
  }, [composer]);

  const navigate = useNavigate();

  // The starter "Keep an eye on a website" and the workspace link both open the
  // durable-work surface, which upstream keeps off the chat screen.
  const openWorkspace = useCallback(() => {
    navigate("/qubit-bot/workspace");
  }, [navigate]);

  const jumpToLatest = useCallback(() => {
    setFollowLatest(true);
    setAwayFromLatest(false);
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, []);

  // ── task / idea / goal / watch / card actions ─────────────────────────
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
    [busy, fail, setError, taskLookup],
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
  }, [askBrokerConfirm, busy, fail, launchPrompt, launchTitle, loadAll, setError, showToast]);

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
        fail(e, `${title} failed.`);
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, setError, showToast, taskDetail, taskLookup],
  );

  const runIdea = useCallback(
    async (kind: "accept" | "dismiss", id: string) => {
      if (busy || !id) return;
      setBusy(true);
      setError("");
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
        fail(e, kind === "accept" ? "Accept failed." : "Dismiss failed.");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, setError, showToast],
  );

  const saveIdeaEdit = useCallback(
    async (idea: Idea) => {
      if (busy || !idea.id) return;
      setBusy(true);
      setError("");
      try {
        await editBotIdea(
          { idea: idea.id, title: idea.title, reason: idea.reason, prompt: idea.prompt },
          (d) => askBrokerConfirm("Save idea edit", d),
        );
        showToast("Idea updated", "success");
        await loadAll();
      } catch (e) {
        fail(e, "Idea edit failed.");
      } finally {
        setBusy(false);
      }
    },
    [askBrokerConfirm, busy, fail, loadAll, setError, showToast],
  );

  const saveGoal = useCallback(async () => {
    const title = goalTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    setError("");
    try {
      await setBotGoal({ title, description: goalDescription.trim() || undefined }, (d) =>
        askBrokerConfirm("Record goal", d),
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
  }, [askBrokerConfirm, busy, fail, goalDescription, goalTitle, loadAll, setError, showToast]);

  const saveWatch = useCallback(async () => {
    const title = watchTitle.trim();
    if (busy || !title) return;
    setBusy(true);
    setError("");
    try {
      await setBotWatch({ title, url: watchUrl.trim() || undefined }, (d) =>
        askBrokerConfirm("Record watch", d),
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
  }, [askBrokerConfirm, busy, fail, loadAll, setError, showToast, watchTitle, watchUrl]);

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
      if (!row) setCardMissing(true);
      else setCard(row);
    } catch (e) {
      fail(e, "Card lookup failed.");
    } finally {
      setBusy(false);
    }
  }, [busy, cardLookup, fail, setError]);

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
  const visible = messages.filter((m) => eventRole(m.kind) !== null);
  const outbox = snapshot;
  const replying = running || stopping;

  // Tool result for a tool event: the adjacent assistant/system result if any.
  const resultFor = useCallback(
    (ev: ThreadEvent): { result: unknown; task?: BotTask } => {
      if (ev.state && typeof ev.state === "object") {
        const obj = ev.state as Record<string, unknown>;
        const taskId = str(obj.taskId) || str(obj.id);
        const task = tasks.find((t) => t.id === taskId);
        return { result: obj, task };
      }
      return { result: undefined };
    },
    [tasks],
  );

  return (
    <div className="qb-chat qb-chat-page" data-testid="qubit-bot-page">
      <div className="qb-chat-scroll" ref={scrollRef} onScroll={(e) => onThreadScroll(e.currentTarget)}>
        {/* History-load error (distinct retry) */}
        {historyError ? (
          <>
            <div className="qb-error">
              <p>{historyError}</p>
            </div>
            <div className="qb-actions-row">
              <Button outlined size="sm" onClick={() => setHistoryAttempt((a) => a + 1)}>
                Retry loading conversation
              </Button>
            </div>
          </>
        ) : null}

        {!loading && !visible.length ? (
          /* Empty-thread hero (upstream centered hero + starter prompts) */
          <div className="qb-hero">
            <p className="qb-hero-title">A little help. A lot more room for life.</p>
            <p className="qb-hero-sub">
              Tell me what&apos;s on your mind. I can make a plan, work with your apps, and use my
              computer to help.
            </p>
            <div className="qb-starters">
              {STARTER_PROMPTS.map((prompt) => (
                <button
                  key={prompt.label}
                  type="button"
                  className="qb-starter-btn"
                  onClick={() => {
                    if (prompt.navigatesTo === "workspace") {
                      openWorkspace();
                      return;
                    }
                    if (prompt.sends) enqueue(prompt.sends);
                  }}
                >
                  {prompt.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          visible.map((ev, i) => {
            const role = eventRole(ev.kind);
            if (role === "user") {
              return (
                <div key={ev.id ?? i} className="qb-bubble-row-user">
                  <div className="qb-bubble-user">
                    {ev.title ? <p className="font-medium">{ev.title}</p> : null}
                    {ev.text ? <p>{ev.text}</p> : null}
                  </div>
                </div>
              );
            }
            if (role === "tool") {
              const { result, task } = resultFor(ev);
              return (
                <div key={ev.id ?? i} className="qb-tool-row">
                  <ToolCard
                    kind={toolCardKind(ev.kind)}
                    event={ev}
                    result={result}
                    loading={running}
                    task={task}
                  />
                </div>
              );
            }
            return (
              <div key={ev.id ?? i} className="qb-bubble-row-assistant">
                <div className="qb-bubble-assistant">
                  {ev.title ? <p className="font-medium">{ev.title}</p> : null}
                  {/* Upstream renders assistant content through `AssistantResponse`
                      (react-native-markdown-renderer + markdown-it: linkify,
                      breaks, fenced code, links). The DOM equivalent is the
                      shared <Markdown> renderer — ADAPTED since react-native's
                      Markdown component cannot exist in the DOM. */}
                  {ev.text ? (
                    <div className="qb-assistant-md">
                      <Markdown content={ev.text} />
                    </div>
                  ) : null}
                  {ev.state && typeof ev.state === "string" ? (
                    <p className="qb-bubble-meta">
                      <Badge tone="secondary">{ev.state}</Badge>
                    </p>
                  ) : null}
                </div>
              </div>
            );
          })
        )}

        {/* Recent results affordance (upstream, non-rich mode) */}
        {!visible.length && (tasks.length > 0 || card) ? (
          <div className="qb-actions-row">
            <Button outlined size="sm" onClick={() => setShowResults((v) => !v)}>
              {showResults ? "Hide recent results" : "Recent results"}
            </Button>
          </div>
        ) : null}
        {showResults ? (
          <div className="qb-recent-results">
            {card ? <ArtifactCard artifact={card} /> : null}
            {tasks.slice(0, 1).map((t) => (
              <TaskThreadCard key={t.id} task={t} />
            ))}
          </div>
        ) : null}

        {/* Typing indicator (three dots, staggered) */}
        {replying ? (
          <div className="qb-typing-wrap" aria-label="Agent is working">
            <div className="qb-typing">
              {[0.4, 0.75, 0.5].map((opacity) => (
                <span key={opacity} className="qb-typing-dot" style={{ opacity }} />
              ))}
            </div>
          </div>
        ) : null}

        {/* Send error + retry (upstream `ErrorNotice` + "Retry response") */}
        {error ? (
          <>
            <div className="qb-error">
              <p>{error}</p>
            </div>
            <div className="qb-actions-row">
              <Button outlined size="sm" disabled={replying || !loaded} onClick={retry}>
                <RotateCcw size={15} />
                Retry response
              </Button>
            </div>
          </>
        ) : null}
      </div>

      {/* Jump-to-latest */}
      {awayFromLatest ? (
        <div className="qb-jump-latest">
          <Button outlined size="sm" onClick={jumpToLatest}>
            <ChevronDown size={15} />
            Latest messages
          </Button>
        </div>
      ) : null}

      <div className="qb-composer-dock">
        {/* Save error + retry (distinct from send error) */}
        {saveError ? (
          <div className="qb-save-error">
            <div className="qb-error">
              <p>{saveError}</p>
            </div>
            <Button outlined size="sm" disabled={busy} onClick={saveHistoryRetry}>
              Retry saving conversation
            </Button>
          </div>
        ) : null}

        {/* Follow-up queue: "Up next" / "Messages on hold" */}
        {outbox.pending.length ? (
          <div className="qb-queue" data-testid="bot-queue">
            <span className="qb-queue-label">
              {outbox.paused ? "Messages on hold" : "Up next"} · Keep the page open until sent
            </span>
            <div className="qb-queue-list">
              {outbox.pending.map((message) => (
                <div key={message.id} className="qb-queue-item">
                  <span className="qb-queue-item-text" title={message.text}>
                    {message.text}
                  </span>
                  <button
                    type="button"
                    className="qb-queue-remove"
                    aria-label={`Remove queued message: ${message.text}`}
                    onClick={() => queue.remove(message.id)}
                  >
                    <X size={16} />
                  </button>
                </div>
              ))}
            </div>
            {outbox.paused ? (
              <Button
                outlined
                size="sm"
                disabled={replying || !!saveError}
                onClick={() => {
                  queue.resume();
                  flush();
                }}
              >
                Send queued messages
              </Button>
            ) : null}
          </div>
        ) : null}

        {/* Attachment picker (upstream "+" opens a document picker) */}
        {picking ? (
          <div className="qb-picker">
            <p className="qb-picker-heading">Add a document</p>
            <div className="qb-picker-list">
              {documents.length ? (
                documents.map((f) => (
                  <label key={f.id} className="qb-check-row">
                    <input
                      type="checkbox"
                      checked={attachments.includes(f.id)}
                      onChange={() =>
                        setAttachments((ids) =>
                          ids.includes(f.id) ? ids.filter((id) => id !== f.id) : [...ids, f.id],
                        )
                      }
                    />
                    <span>{f.name}</span>
                  </label>
                ))
              ) : (
                <p className="qb-tool-sub">
                  Documents the bot produces in this thread become available here.
                </p>
              )}
            </div>
            <div className="qb-actions-row">
              <Button outlined size="sm" onClick={() => setPicking(false)}>
                Done
              </Button>
            </div>
          </div>
        ) : null}

        {/* Composer pill: 44px -> 140px, "+" left, circular send/stop */}
        <div className={`qb-composer ${composerFocused ? "qb-composer-focused" : ""}`}>
          {attachments.length > 0 ? (
            <div className="qb-chip-row">
              {documents
                .filter((f) => attachments.includes(f.id))
                .map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className="qb-attach-chip"
                    aria-label={`Remove attachment: ${f.name}`}
                    onClick={() => setAttachments((ids) => ids.filter((id) => id !== f.id))}
                  >
                    <FileText size={14} />
                    <span className="qb-attach-chip-text">{f.name}</span>
                    <X size={13} />
                  </button>
                ))}
            </div>
          ) : null}
          <div className="qb-composer-row">
            <button
              type="button"
              className={`qb-plus-btn ${picking ? "qb-plus-btn-active" : ""}`}
              aria-label="Attach a document"
              aria-expanded={picking}
              onClick={() => setPicking((v) => !v)}
            >
              <Plus size={22} />
            </button>
            {/* Upstream's TextInput is `multiline`: a web <textarea> that grows
                from 44px to 140px with content (auto-resize on input), Enter
                sends, Shift+Enter inserts a newline, and it collapses back to
                44px after a send. A single-line <input> cannot grow, so it is
                the wrong control for this pill. */}
            <textarea
              id="bot-composer"
              ref={composerRef}
              className="qb-composer-input"
              aria-label="Message Qubit Bot"
              data-agent-context={JSON.stringify(BOT_AGENT_CONTEXT.value)}
              value={composer}
              rows={1}
              placeholder={
                !loaded
                  ? historyError
                    ? "Conversation unavailable"
                    : "Loading conversation…"
                  : "Message…"
              }
              onChange={(e) => {
                setComposer(e.target.value);
                setDrafts((d) => ({ ...d, [threadKey]: e.target.value }));
              }}
              onFocus={() => setComposerFocused(true)}
              onBlur={() => setComposerFocused(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <button
              type="button"
              className="qb-send-btn"
              aria-label={replying ? "Stop reply" : "Send message"}
              disabled={!replying && (!composer.trim() || !loaded)}
              onClick={replying ? () => void stopTurn() : send}
            >
              {replying ? <Square size={18} fill="currentColor" strokeWidth={0} /> : null}
              {!replying ? <ArrowUpGlyph /> : null}
            </button>
          </div>
        </div>
      </div>

      {/* ── Secondary: the bot workspace (tasks, ideas, goals, cards) ───── */}
      {/* Collapsed by default so the page opens as the pure chat surface;
          expanding reveals the durable-work panels and scrolls within itself. */}
      <div className="qb-below">
        <button
          type="button"
          className="qb-below-toggle"
          aria-expanded={showWorkspace}
          onClick={() => setShowWorkspace((v) => !v)}
        >
          <ChevronDown size={16} />
          {showWorkspace ? "Hide bot workspace" : "Show bot workspace"}
        </button>
        <div className="qb-below-body" hidden={!showWorkspace}>
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
                  {threads.map((t) => {
                    const pending = activeThread === t.id ? outbox.pending.length : 0;
                    const isActive = activeThread === t.id;
                    return (
                      <li
                        key={t.id}
                        title={t.id}
                        className={`qb-thread-item ${isActive ? "qb-thread-item-active" : ""}`}
                      >
                        <span className="truncate max-w-[140px]">{t.title ?? t.id}</span>
                        {t.messageCount !== undefined ? (
                          <span className="qb-bubble-meta">· {t.messageCount} messages</span>
                        ) : null}
                        {drafts[t.id] ? <Badge tone="secondary">draft</Badge> : null}
                        {pending > 0 ? <Badge tone="outline">{pending} queued</Badge> : null}
                        <Button outlined size="sm" disabled={busy || isActive} onClick={() => openThread(t.id)}>
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
              <h2 className="font-medium">{activeTitle ?? "Active thread"}</h2>
              <p className="qb-bubble-meta">
                Follow-ups run in order after the current turn. Stopping pauses the
                queue — it never cancels delegated tasks.
              </p>
              <div className="space-y-1">
                <Label htmlFor="bot-followup">Follow-up</Label>
                <div className="flex items-end gap-2 flex-wrap">
                  <Input
                    id="bot-followup"
                    value={followupDraft}
                    onChange={(e) => setFollowupDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        queueFollowup();
                      }
                    }}
                    placeholder="Queue a follow-up…"
                  />
                  <Button outlined disabled={!followupDraft.trim()} onClick={() => queueFollowup()}>
                    Queue
                  </Button>
                  <Button outlined disabled={outbox.pending.length === 0} onClick={() => {
                    queue.resume();
                    flush();
                  }}>
                    Send queued messages
                  </Button>
                </div>
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
                <Button
                  outlined
                  disabled={busy}
                  onClick={() => setShowTaskInline((v) => !v)}
                >
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
        </div>
      </div>

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

/** Small arrow glyph matching upstream's ArrowUp stroke (lucide ArrowUp). */
function ArrowUpGlyph() {
  return (
    <svg width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="m5 12 7-7 7 7" />
    </svg>
  );
}
