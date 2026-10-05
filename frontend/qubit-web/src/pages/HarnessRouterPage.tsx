/**
 * Harness Router — the Unified Harness Protocol console pattern as a
 * Qubit-owned page (route `/qubit-harness-router`, registered in `App.tsx`).
 *
 * The console UX: harness list across backends, task submit with a per-task
 * harness switch, sessions create/resume/inspect, streaming snapshots,
 * files/artifacts, cancellation, and structured failures; console sections
 * Agent harnesses / Starter Kits / Plugins — Qubit-only, onto existing Qubit
 * backends (WorkerGuard `agent.session`, the task ledger, the planner tool
 * backends, the `codex-runtime` port, brokered file ops).
 *
 * The reference harness is deliberately NOT rendered or routed here (no row,
 * no path, no fallback). Streaming here is a polling snapshot over the
 * existing `fetchJSON` transport, never a new WS/SSE/CDP client.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelHarnessTask,
  createHarness,
  createHarnessSession,
  getHarnessArtifact,
  inspectHarnessSession,
  listCustomHarnesses,
  listHarnessPlugins,
  listHarnessFiles,
  listHarnessTasks,
  listHarnesses,
  listStarterKits,
  normalizeFailure,
  normalizeHarnessFiles,
  normalizeHarnessSession,
  normalizeHarnessTasks,
  normalizeHarnesses,
  resumeHarnessSession,
  streamHarnessSession,
  submitHarnessTask,
  type QubitHarness,
  type QubitHarnessFailure,
  type QubitHarnessFile,
  type QubitHarnessSession,
  type QubitHarnessTask,
} from "@/lib/ocFamilyHarnessApi";
import { refusalOrError } from "@/lib/policy-refusal";

function errText(e: unknown): string {
  if (e instanceof Error && e.message) return e.message;
  return refusalOrError(e, "Request failed");
}

/** localStorage key for the sidebar group fold state. */
const HX_GROUPS_KEY = "qubit.hx.groups";

function Section({ title, testid, children }: { title: string; testid: string; children: React.ReactNode }) {
  return (
    <section className="hx-card" aria-label={title} data-testid={testid}>
      <div className="hx-row-name">
        <b>{title}</b>
      </div>
      <div className="hx-bases">{children}</div>
    </section>
  );
}

function FailureRow({ failure }: { failure: QubitHarnessFailure }) {
  return (
    <div data-testid={`failure-${failure.code}`} style={{ marginTop: 4 }}>
      <code style={{ fontSize: 12 }}>{failure.code}</code>
      <span style={{ fontSize: 12 }}> — {failure.detail}</span>
      <span style={{ fontSize: 12 }}> ({failure.retryable ? "retryable" : "not retryable"})</span>
    </div>
  );
}

/**
 * One sidebar folder group (the console's `.hx-group`): a header button that
 * folds the group, the rows themselves, and the console's empty message when
 * the group has none. Every control here does one real thing: the header
 * toggles the fold; each row opens its harness; the row's `.hx-ic` starts a
 * fresh task in it. Ported structure (`Your harnesses` / `System harnesses`).
 */
function HarnessGroup({
  label,
  count,
  open,
  empty,
  rows,
  openId,
  openedIds,
  onToggle,
  onOpen,
  onNewTask,
  healthOf,
}: {
  label: string;
  count: number;
  open: boolean;
  empty: string;
  rows: QubitHarness[];
  openId: string;
  /** Ids currently listed in the main pane's "Your harnesses" section. */
  openedIds: Set<string>;
  onToggle: () => void;
  onOpen: (id: string) => void;
  onNewTask: (id: string) => void;
  healthOf: (h: QubitHarness) => "healthy" | "degraded" | "idle";
}) {
  return (
    <div className={"hx-group" + (open ? " is-open" : "")}>
      <div className="hx-group-row">
        <button type="button" className="hx-group-btn" aria-expanded={open} onClick={onToggle}>
          <span className="hx-caret" aria-hidden="true">
            {open ? "\u2304" : "\u203a"}
          </span>
          <span className="hx-group-label">{label}</span>
          <span className="hx-group-count">{count}</span>
        </button>
      </div>
      {open
        ? rows.length === 0
          ? empty
            ? <div className="hx-group-empty">{empty}</div>
            : null
          : rows.map((h) => (
              <div key={h.id} className={"hx-proj" + (openId === h.id ? " is-current" : "")}>
                <div className="hx-proj-row">
                  <button
                    type="button"
                    className={"hx-proj-btn" + (openId === h.id ? " is-on" : "")}
                    data-testid={`harness-${h.id}`}
                    aria-current={openId === h.id ? "page" : undefined}
                    onClick={() => onOpen(h.id)}
                  >
                    <span className="hx-caret" aria-hidden="true">{openId === h.id ? "\u2304" : "\u203a"}</span>
                    <span className={"hx-dot" + (healthOf(h) === "healthy" ? " is-ok" : "")} aria-hidden="true" />
                    <span className="hx-proj-name">{h.id}</span>
                    {h.state ? (
                      <span className="hx-proj-badge" title={`roster state: ${h.state}`}>
                        {h.state}
                      </span>
                    ) : null}
                    {openedIds.has(h.id) ? (
                      <span className="hx-proj-badge" title="listed in Your harnesses">
                        {h.backend || "backend"}
                      </span>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    className="hx-ic"
                    title={`New task in ${h.id}`}
                    aria-label={`New task in ${h.id}`}
                    onClick={() => onNewTask(h.id)}
                  >
                    &#43;
                  </button>
                </div>
              </div>
            ))
        : null}
    </div>
  );
}

export default function HarnessRouterPage() {
  const [harnesses, setHarnesses] = useState<QubitHarness[]>([]);
  const [customHarnesses, setCustomHarnesses] = useState<QubitHarness[]>([]);
  const [kits, setKits] = useState<QubitHarness[]>([]);
  const [plugins, setPlugins] = useState<QubitHarness[]>([]);
  const [tasks, setTasks] = useState<QubitHarnessTask[]>([]);
  const [prompt, setPrompt] = useState("");
  const [harness, setHarness] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [session, setSession] = useState<QubitHarnessSession | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [live, setLive] = useState(false);
  const [files, setFiles] = useState<QubitHarnessFile[]>([]);
  const [artifact, setArtifact] = useState("");
  // The "New harness" create form: opens inline in the index view, posts to
  // the durable custom-harness backend (harness.create), then reloads the list.
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBase, setNewBase] = useState("");
  const [newBusy, setNewBusy] = useState(false);
  // Console shell state: which harness is open ("" = the Dashboard/index view)
  // and the sidebar search needle. Mirrors the console's `view`/`q`.
  const [openHarnessId, setOpenHarnessId] = useState<string>("");
  const [query, setQuery] = useState<string>("");
  /** Main-pane view: the harness task console, or a collection section. */
  const [panelView, setPanelView] = useState<"console" | "kits" | "plugins">("console");
  // Sidebar group open/close state, one flag per group; collapse is a
  // per-viewer convenience persisted across reloads (the same way the console
  // keeps its tree fold). `yours` is the user-created harness folder group,
  // `system` the runtime roster group the console keeps below it; `kits` and
  // `plugins` are the collection groups.
  const [groupsOpen, setGroupsOpen] = useState<{ yours: boolean; system: boolean; kits: boolean; plugins: boolean }>(() => {
    const fallback = { yours: true, system: true, kits: true, plugins: true };
    try {
      const raw = typeof window !== "undefined" ? window.localStorage.getItem(HX_GROUPS_KEY) : null;
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<typeof fallback>;
        return { ...fallback, ...parsed };
      }
    } catch {
      /* private mode / malformed: fall through to the open default */
    }
    return fallback;
  });
  const toggleGroup = useCallback((key: keyof typeof groupsOpen) => {
    setGroupsOpen((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      try {
        window.localStorage.setItem(HX_GROUPS_KEY, JSON.stringify(next));
      } catch {
        /* private mode: the fold is per-session */
      }
      return next;
    });
  }, []);
  const liveTimer = useRef<number | null>(null);

  /** Sidebar rows filtered by the search needle (the console `panel`). */
  const matchHarness = useCallback((h: QubitHarness) => {
    if (!query.trim()) return true;
    const needle = query.trim().toLowerCase();
    return h.id.toLowerCase().includes(needle)
      || (h.backend || "").toLowerCase().includes(needle)
      || (h.description || "").toLowerCase().includes(needle);
  }, [query]);
  // The console's two folder groups: the user-created harnesses ("Your
  // harnesses") and the runtime roster ("System harnesses").
  const sidebarHarnesses = [...customHarnesses, ...harnesses];
  const visibleHarnesses = sidebarHarnesses.filter(matchHarness);
  const visibleCustom = customHarnesses.filter(matchHarness);
  const visibleSystem = harnesses.filter(matchHarness);

  /**
   * Health bucket for a harness row — the console's `.hx-health is-*`.
   * Derived only from the roster's own `state`/enabled flags; when the roster
   * reports nothing the row is `idle` (never a fabricated "healthy").
   */
  const healthOf = (h: QubitHarness): "healthy" | "degraded" | "idle" => {
    const s = (h.state || "").toLowerCase();
    if (!s) return "idle";
    if (s === "ok" || s === "ready" || s === "enabled" || s === "running") return "healthy";
    return "degraded";
  };

  /** Open a harness into the task console (the console `openNew(hid)`). */
  const openHarness = useCallback((id: string) => {
    setPanelView("console");
    setOpenHarnessId(id);
    setHarness(id);
    setNotice("");
    setError("");
  }, []);

  /**
   * Start a fresh task in a harness (the console `openNew(hid)`): open it and
   * put the caret back in the prompt box. Same behaviour whether the click
   * came from a sidebar row's `.hx-ic` or the header's "New task".
   */
  const openHarnessTask = useCallback((id: string) => {
    setPanelView("console");
    setOpenHarnessId(id);
    setHarness(id);
    setPrompt("");
    setNotice("");
    setError("");
    window.setTimeout(() => {
      document
        .querySelector<HTMLInputElement>('[data-testid="harness-task-prompt"]')
        ?.focus();
    }, 0);
  }, []);

  const refreshLists = useCallback(async () => {
    try {
      const [h, k, p, t, c] = await Promise.all([
        listHarnesses(),
        listStarterKits(),
        listHarnessPlugins(),
        listHarnessTasks(),
        listCustomHarnesses().catch(() => ({ harnesses: [] })),
      ]);
      const hs = normalizeHarnesses(h);
      setHarnesses(hs.filter((r) => r.kind === "agent"));
      setKits(normalizeHarnesses(k));
      setPlugins(normalizeHarnesses(p));
      setTasks(normalizeHarnessTasks(t));
      // User-created harnesses are their own group (the console's "Your
      // harnesses" folder); they run on the runtime roster named in `backend`.
      setCustomHarnesses(
        normalizeHarnesses(c).map((row) => ({ ...row, kind: "agent" as const })),
      );
      setHarness((prev) => prev || hs.filter((r) => r.kind === "agent")[0]?.id || "");
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    void refreshLists();
  }, [refreshLists]);

  useEffect(() => {
    if (liveTimer.current !== null) {
      window.clearInterval(liveTimer.current);
      liveTimer.current = null;
    }
    if (!live || !sessionId) return;
    liveTimer.current = window.setInterval(() => {
      void streamHarnessSession(sessionId)
        .then((payload) => {
          const next = normalizeHarnessSession(payload);
          if (next) {
            setSession(next);
            setSessionId(next.id);
          }
        })
        .catch((e: unknown) => setError(errText(e)));
    }, 2000);
    return () => {
      if (liveTimer.current !== null) window.clearInterval(liveTimer.current);
      liveTimer.current = null;
    };
  }, [live, sessionId]);

  async function onSubmit() {
    setNotice("");
    setError("");
    try {
      const created = await submitHarnessTask({ prompt, harness });
      const row = normalizeHarnessTasks({ tasks: [created] })[0];
      setNotice(row?.id ? `Submitted ${row.id} on ${harness}` : `Submitted on ${harness}`);
      setPrompt("");
      await refreshLists();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onCancel(taskId: string) {
    setNotice("");
    setError("");
    try {
      await cancelHarnessTask({ taskId });
      setNotice(`Cancelled ${taskId}`);
      await refreshLists();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onCreateSession() {
    setNotice("");
    setError("");
    try {
      const created = await createHarnessSession({ harness });
      const next = normalizeHarnessSession(created);
      if (next) {
        setSession(next);
        setSessionId(next.id);
        setNotice(`Session ${next.id} created on ${harness}`);
      }
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onResume() {
    setNotice("");
    setError("");
    try {
      const resumed = await resumeHarnessSession(sessionId);
      const next = normalizeHarnessSession(resumed);
      if (next) {
        setSession(next);
        setSessionId(next.id);
        setNotice(`Session ${next.id} resumed`);
      }
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onInspect() {
    setNotice("");
    setError("");
    try {
      const found = await inspectHarnessSession(sessionId);
      const next = normalizeHarnessSession(found);
      if (next) {
        setSession(next);
        setSessionId(next.id);
      }
      const fl = await listHarnessFiles({ session: sessionId });
      setFiles(normalizeHarnessFiles(fl));
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onArtifact(path: string) {
    setError("");
    try {
      const body = await getHarnessArtifact(path);
      const text =
        typeof body === "string"
          ? body
          : JSON.stringify((body as { result?: unknown }).result ?? body, null, 2);
      setArtifact(text);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onCreateHarness() {
    setNotice("");
    setError("");
    if (!newName.trim()) return;
    setNewBusy(true);
    try {
      await createHarness({ name: newName.trim(), base: newBase || undefined });
      setNotice(`Created harness ${newName.trim()}`);
      setAdding(false);
      setNewName("");
      setNewBase("");
      await refreshLists();
    } catch (e) {
      setError(errText(e));
    } finally {
      setNewBusy(false);
    }
  }

  return (
    <section className="hx-root" id="view-harnesses" aria-label="Harness router" data-testid="harness-router-page">
      {/* ---- Sidebar: the console's own index (`.hx-side`) ---- */}
      <aside className="hx-side" aria-label="Harnesses and their tasks">
        <div className="hx-side-head">
          <span className="hx-side-title">Agent harnesses</span>
          <label className="hx-search">
            <span aria-hidden="true">&#9906;</span>
            <input
              type="search"
              placeholder="Search harnesses and tasks"
              aria-label="Search harnesses and tasks"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
            />
          </label>
        </div>
        <div className="hx-side-fixed">
          <button
            type="button"
            className={"hx-index" + (openHarnessId ? "" : " is-on")}
            aria-current={openHarnessId ? undefined : "page"}
            onClick={() => {
              setOpenHarnessId("");
              setPanelView("console");
            }}
          >
            <span>Dashboard</span>
            {sidebarHarnesses.length > 0 ? <b>{sidebarHarnesses.length}</b> : null}
          </button>
          <div className="hx-div" />
        </div>
        <div className="hx-side-scroll">
          {visibleHarnesses.length === 0 ? (
            <div className="hx-empty">
              {query ? "Nothing matches that." : "No harnesses yet."}
            </div>
          ) : (
            <>
              <HarnessGroup
                label="Your harnesses"
                count={visibleCustom.length}
                open={groupsOpen.yours}
                empty="None yet. Create one from the index with New harness."
                rows={visibleCustom}
                openId={openHarnessId}
                openedIds={new Set(customHarnesses.map((h) => h.id))}
                onToggle={() => toggleGroup("yours")}
                onOpen={openHarness}
                onNewTask={openHarnessTask}
                healthOf={healthOf}
              />
              <HarnessGroup
                label="System harnesses"
                count={visibleSystem.length}
                open={groupsOpen.system}
                empty=""
                rows={visibleSystem}
                openId={openHarnessId}
                openedIds={new Set()}
                onToggle={() => toggleGroup("system")}
                onOpen={openHarness}
                onNewTask={openHarnessTask}
                healthOf={healthOf}
              />
            </>
          )}
          {/* Collection groups: each opens its section in the main pane, the
              same way the Dashboard row and a harness row do. */}
          <div className={"hx-group" + (groupsOpen.kits ? " is-open" : "")}>
            <div className="hx-group-row">
              <button
                type="button"
                className="hx-group-btn"
                aria-expanded={groupsOpen.kits}
                onClick={() => {
                  toggleGroup("kits");
                  setPanelView("kits");
                }}
              >
                <span className="hx-caret" aria-hidden="true">
                  {groupsOpen.kits ? "\u2304" : "\u203a"}
                </span>
                <span className="hx-group-label">Starter Kits</span>
                <span className="hx-group-count">{kits.length}</span>
              </button>
            </div>
            {groupsOpen.kits ? (
              <div className="hx-proj-row">
                <button
                  type="button"
                  className={"hx-proj-btn" + (panelView === "kits" ? " is-on" : "")}
                  aria-current={panelView === "kits" ? "page" : undefined}
                  onClick={() => setPanelView("kits")}
                >
                  <span className="hx-caret" aria-hidden="true">{"\u203a"}</span>
                  <span className="hx-dot" aria-hidden="true" />
                  <span className="hx-proj-name">Browse starter kits</span>
                  <span className="hx-group-count">{kits.length}</span>
                </button>
              </div>
            ) : null}
          </div>
          <div className={"hx-group" + (groupsOpen.plugins ? " is-open" : "")}>
            <div className="hx-group-row">
              <button
                type="button"
                className="hx-group-btn"
                aria-expanded={groupsOpen.plugins}
                onClick={() => {
                  toggleGroup("plugins");
                  setPanelView("plugins");
                }}
              >
                <span className="hx-caret" aria-hidden="true">
                  {groupsOpen.plugins ? "\u2304" : "\u203a"}
                </span>
                <span className="hx-group-label">Plugins</span>
                <span className="hx-group-count">{plugins.length}</span>
              </button>
            </div>
            {groupsOpen.plugins ? (
              <div className="hx-proj-row">
                <button
                  type="button"
                  className={"hx-proj-btn" + (panelView === "plugins" ? " is-on" : "")}
                  aria-current={panelView === "plugins" ? "page" : undefined}
                  onClick={() => setPanelView("plugins")}
                >
                  <span className="hx-caret" aria-hidden="true">{"\u203a"}</span>
                  <span className="hx-dot" aria-hidden="true" />
                  <span className="hx-proj-name">Browse plugins</span>
                  <span className="hx-group-count">{plugins.length}</span>
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </aside>

      {/* ---- Console body (`.hx-main`) ---- */}
      <div className="hx-main">
        <header className="hx-head">
          <div className="hx-head-row">
            <div className="hx-head-lead">
              <div className="hx-crumb">
                <span>Harnesses</span>
                {openHarnessId ? (
                  <>
                    <span className="hx-crumb-sep">/</span>
                    <span className="hx-crumb-cfg">{openHarnessId}</span>
                  </>
                ) : null}
              </div>
              <div className="hx-title-row">
                <h1 className="hx-title">{openHarnessId || "Harness Router"}</h1>
                <span className={"hx-pill is-" + (openHarnessId ? "ok" : "neutral")}>
                  {openHarnessId ? "agent" : "index"}
                </span>
                {sidebarHarnesses.length > 0 ? (
                  <span className="hx-health">
                    <span className="hx-dot is-ok" aria-hidden="true" />
                    {sidebarHarnesses.length} harnesses
                  </span>
                ) : null}
              </div>
            </div>
            <div className="hx-head-actions">
              {openHarnessId ? (
                <>
                  <button type="button" className="hx-secondary" onClick={() => setOpenHarnessId("")}>
                    Back to index
                  </button>
                  <button
                    type="button"
                    className="hx-primary"
                    data-testid="harness-new-task"
                    onClick={() => {
                      // Start a fresh task in the open harness: clear the box
                      // and return focus to it (the same "caret in the box"
                      // the console does when a folder opens).
                      setPrompt("");
                      setNotice("");
                      setError("");
                      window.setTimeout(() => {
                        document
                          .querySelector<HTMLInputElement>('[data-testid="harness-task-prompt"]')
                          ?.focus();
                      }, 0);
                    }}
                  >
                    New task
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="hx-primary"
                  data-testid="harness-new"
                  onClick={() => setAdding((v) => !v)}
                >
                  New harness
                </button>
              )}
            </div>
          </div>
        </header>

        <div className="hx-body">
          {notice ? <p data-testid="harness-notice" className="hx-index-note">{notice}</p> : null}
          {error ? (
            <p data-testid="harness-error" role="alert" className="hx-empty-main">
              {error}
            </p>
          ) : null}

          {panelView === "kits" ? (
            <div className="hx-index-wrap">
              <Section title="Starter Kits" testid="section-starter-kits">
                {kits.length === 0 ? (
                  <p className="hx-empty-main">No starter kits reported.</p>
                ) : (
                  <div className="hx-bases" aria-label="Starter kit list">
                    {kits.map((k) => (
                      <div key={k.id} className="hx-base" data-testid={`kit-${k.id}`}>
                        <span className="hx-dot" aria-hidden="true" />
                        <span className="hx-base-copy">
                          <b>{k.id}</b>
                          {k.description ? <small>{k.description}</small> : null}
                        </span>
                        <span className="hx-base-def">starter-kit</span>
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            </div>
          ) : panelView === "plugins" ? (
            <div className="hx-index-wrap">
              <Section title="Plugins" testid="section-plugins">
                {plugins.length === 0 ? (
                  <p className="hx-empty-main">No harness plugins reported.</p>
                ) : (
                  <div className="hx-bases" aria-label="Harness plugin list">
                    {plugins.map((p) => (
                      <div key={p.id} className="hx-base" data-testid={`plugin-${p.id}`}>
                        <span className="hx-dot" aria-hidden="true" />
                        <span className="hx-base-copy">
                          <b>{p.id}</b>
                          {p.description ? <small>{p.description}</small> : null}
                        </span>
                        <span className="hx-base-def">{p.state || "plugin"}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            </div>
          ) : openHarnessId === "" ? (
            /* ---- Index view: harness table (`.hx-index-wrap`) ---- */
            <div className="hx-index-wrap">
              {adding ? (
                <Section title="New harness" testid="section-new-harness">
                  <div className="hx-task-row">
                    <input
                      id="harness-new-name"
                      data-testid="harness-new-name"
                      className="hx-add-select"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="Harness name"
                      aria-label="Harness name"
                    />
                    <select
                      id="harness-new-base"
                      data-testid="harness-new-base"
                      className="hx-add-select"
                      value={newBase}
                      onChange={(e) => setNewBase(e.target.value)}
                      aria-label="Base runtime"
                    >
                      <option value="">Default runtime</option>
                      {harnesses.map((h) => (
                        <option key={h.id} value={h.backend || h.id}>
                          {h.backend || h.id}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="hx-primary"
                      data-testid="harness-create"
                      disabled={newBusy || !newName.trim()}
                      onClick={() => void onCreateHarness()}
                    >
                      {newBusy ? "Creating…" : "Create"}
                    </button>
                    <button
                      type="button"
                      className="hx-secondary"
                      data-testid="harness-create-cancel"
                      disabled={newBusy}
                      onClick={() => setAdding(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </Section>
              ) : null}
              {customHarnesses.length > 0 ? (
                <Section title="Your harnesses" testid="section-custom-harnesses">
                  <div className="hx-index-note">
                    {customHarnesses.length} created harnesses
                  </div>
                  <div className="hx-bases" aria-label="Created harnesses">
                    {customHarnesses.map((h) => (
                      <button
                        key={h.id}
                        type="button"
                        className="hx-base"
                        data-testid={`custom-${h.id}`}
                        onClick={() => openHarness(h.id)}
                      >
                        <span className="hx-dot is-ok" aria-hidden="true" />
                        <span className="hx-base-copy">
                          <b>{h.id}</b>
                          {h.backend ? <small>{h.backend}</small> : null}
                        </span>
                        <span className="hx-base-def">{h.state || "ready"}</span>
                      </button>
                    ))}
                  </div>
                </Section>
              ) : null}
              <Section title="Agent harnesses" testid="section-agent-harnesses">
                {harnesses.length === 0 ? (
                  <p className="hx-empty-main">
                    No harnesses yet. Pick a harness on the left to give an agent a name,
                    instructions and tools.
                  </p>
                ) : (
                  <>
                    <div className="hx-index-note">
                      {harnesses.length} {harnesses.length === 1 ? "harness" : "harnesses"} · runtime roster
                    </div>
                    <div className="hx-thead" role="row">
                      <span>Harness</span>
                      <span className="hx-th-id">Harness ID</span>
                      <span>Health</span>
                      <span className="hx-th-rt">Runtime</span>
                      <span className="hx-th-n">Tasks</span>
                      <span className="hx-th-n hx-th-success">Success</span>
                      <span className="hx-th-n hx-th-p95">P95</span>
                      <span className="hx-th-n hx-th-cpt">Cr / task</span>
                      <span className="hx-th-last">Last activity</span>
                      <span />
                    </div>
                    {harnesses.map((h) => {
                      const health = healthOf(h);
                      return (
                        <button
                          key={h.id}
                          type="button"
                          className="hx-row"
                          data-testid={`row-${h.id}`}
                          onClick={() => openHarness(h.id)}
                        >
                          <span className="hx-row-name">
                            <b>{h.id}</b>
                            {h.description ? <small>{h.description}</small> : null}
                          </span>
                          <span className="hx-row-id hx-th-id">
                            <span className="mono-id">{h.id}</span>
                          </span>
                          <span className={"hx-health is-" + health} title={`roster state: ${h.state || "not reported"}`}>
                            <i aria-hidden="true" />
                            {health}
                          </span>
                          <span className="hx-row-rt hx-th-rt">
                            <span>
                              <b>{h.backend || "—"}</b>
                              <small>backend</small>
                            </span>
                          </span>
                          {/* Per-harness task counts and statistics are not reported by the
                              roster read (`agents.list`); show the console's honest "—"
                              rather than a fabricated number. */}
                          <span className="hx-num hx-th-n" title="the roster read reports no per-harness task counts">—</span>
                          <span className="hx-num hx-th-n hx-th-success" title="the roster read reports no per-harness success rate">—</span>
                          <span className="hx-num hx-th-n hx-th-p95" title="the roster read reports no per-harness latency">—</span>
                          <span className="hx-num hx-th-n hx-th-cpt" title="the roster read reports no per-harness cost">—</span>
                          <span className="hx-num hx-th-last" title="the roster read reports no per-harness activity time">—</span>
                          <span className="hx-row-chev" aria-hidden="true">&#8250;</span>
                        </button>
                      );
                    })}
                  </>
                )}
              </Section>
            </div>
          ) : (
            /* ---- Task console for the open harness ---- */
            <div className="hx-body">
              <Section title="Submit task" testid="section-submit-task">
                <div className="hx-search">
                  <span aria-hidden="true">&#9906;</span>
                  <input
                    id="harness-task-prompt"
                    data-testid="harness-task-prompt"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="What should the harness do?"
                    aria-label="Task prompt"
                  />
                </div>
                <div className="hx-task-row">
                  <select
                    id="harness-switch"
                    data-testid="harness-switch"
                    className="hx-add-select"
                    value={harness}
                    onChange={(e) => setHarness(e.target.value)}
                  >
                    {[...customHarnesses, ...harnesses].map((h) => (
                      <option key={h.id} value={h.id}>
                        {h.id}
                        {h.backend ? ` (${h.backend})` : ""}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="hx-primary"
                    data-testid="harness-submit"
                    disabled={!prompt.trim() || !harness}
                    onClick={() => void onSubmit()}
                  >
                    Submit
                  </button>
                </div>
              </Section>

              <Section title="Tasks" testid="section-tasks">
                {tasks.length === 0 ? (
                  <p className="hx-tasks-empty">No tasks yet.</p>
                ) : (
                  <div className="hx-tasks" aria-label="Harness task list">
                    {tasks.map((t) => (
                      <div key={t.id} className="hx-task-row" data-testid={`task-${t.id}`}>
                        <span className={"hx-dot" + (t.state === "running" ? " is-on" : "")} aria-hidden="true" />
                        <span className="hx-row-name">
                          <b>{t.id}</b>
                          {t.title ? <small>{t.title}</small> : null}
                        </span>
                        {t.harness ? <span className="hx-row-id">{t.harness}</span> : null}
                        {t.state ? <span className="hx-pill">{t.state}</span> : null}
                        <button
                          type="button"
                          className="hx-task-x"
                          data-testid={`cancel-${t.id}`}
                          aria-label={`Cancel ${t.id}`}
                          onClick={() => void onCancel(t.id)}
                        >
                          &#215;
                        </button>
                        {t.failure ? <FailureRow failure={t.failure} /> : null}
                      </div>
                    ))}
                  </div>
                )}
              </Section>

              <Section title="Sessions" testid="section-sessions">
                <div className="hx-task-row">
                  <button
                    type="button"
                    className="hx-primary"
                    data-testid="session-create"
                    disabled={!harness}
                    onClick={() => void onCreateSession()}
                  >
                    Create session
                  </button>
                  <input
                    id="harness-session-id"
                    data-testid="harness-session-id"
                    className="hx-add-select"
                    value={sessionId}
                    onChange={(e) => setSessionId(e.target.value)}
                    placeholder="session id"
                    aria-label="Session"
                  />
                  <button
                    type="button"
                    className="hx-secondary"
                    data-testid="session-resume"
                    disabled={!sessionId}
                    onClick={() => void onResume()}
                  >
                    Resume
                  </button>
                  <button
                    type="button"
                    className="hx-secondary"
                    data-testid="session-inspect"
                    disabled={!sessionId}
                    onClick={() => void onInspect()}
                  >
                    Inspect
                  </button>
                  <span className="hx-add-next">
                    <input
                      type="checkbox"
                      data-testid="session-live"
                      checked={live}
                      onChange={(e) => setLive(e.target.checked)}
                      aria-label="Follow live"
                    />{" "}
                    Follow live
                  </span>
                </div>
                {session ? (
                  <div data-testid="session-detail" className="hx-bases">
                    <div className="hx-base-copy">
                      <b>{session.id}</b>
                      <small>
                        {[session.harness, session.state, session.title].filter(Boolean).join(" · ")}
                      </small>
                    </div>
                    {session.transcript ? (
                      <pre data-testid="session-transcript" className="hx-empty-main">
                        {session.transcript}
                      </pre>
                    ) : null}
                    {(() => {
                      const direct = session.failure;
                      if (direct) return <FailureRow failure={direct} />;
                      return null;
                    })()}
                  </div>
                ) : null}
              </Section>

              <Section title="Files and artifacts" testid="section-files">
                {files.length === 0 ? (
                  <p className="hx-tasks-empty">No files listed — inspect a session first.</p>
                ) : (
                  <div className="hx-bases" aria-label="Session files">
                    {files.map((f) => (
                      <button
                        key={f.path}
                        type="button"
                        className="hx-base"
                        data-testid={`artifact-${f.path}`}
                        onClick={() => void onArtifact(f.path)}
                      >
                        <span className="hx-dot" aria-hidden="true" />
                        <span className="hx-base-copy">
                          <b>{f.path}</b>
                          {f.sizeBytes !== undefined ? <small>{f.sizeBytes} bytes</small> : null}
                        </span>
                        <span className="hx-base-def">file</span>
                      </button>
                    ))}
                  </div>
                )}
                {artifact ? (
                  <pre data-testid="artifact-body" className="hx-empty-main">
                    {artifact}
                  </pre>
                ) : null}
              </Section>
            </div>
          )}

          <div data-testid="harness-failure-probe" hidden>
            {normalizeFailure(null) === null ? "failure-probe-ok" : "failure-probe-bad"}
          </div>
        </div>
      </div>
    </section>
  );
}
