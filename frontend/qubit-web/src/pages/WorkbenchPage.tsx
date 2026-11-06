/**
 * Qubit Workbench (route `/qubit-workbench`).
 *
 * A four-region console, ported in structure from the absorbed upstream surface:
 *
 *   [ icon rail ][ conversation sidebar ][ chat ][ workspace panel ]
 *
 * It is a NEW surface and a sibling of `/qubit-chat`, never a replacement: the
 * voice path (push-to-talk, the Right-Ctrl hotkey, the desktop-helper hand-off,
 * read-aloud, the live voice session follow) stays on `/qubit-chat` and nothing
 * here references it. The composer's mic button is drawn DISABLED with a title
 * pointing there, rather than duplicated.
 *
 * Data comes only from `@/lib/workbench-api`, and only in one of four typed
 * states (see that module). A 404 from the `/api/workbench/*` seam renders
 * "Backend not available" — never an empty list, which would report "you have
 * no sessions", a different and false claim. A mounted backend that says "no
 * active project" renders that reason, not an empty folder.
 *
 * **Responsive order.** The workspace collapses first (its own close button,
 * and automatically below `xl`), then the sidebar (below `md`), down to a
 * single column whose header switches between them.
 *
 * This file is layout and wiring only. Panels live under
 * `components/workbench/`, the grouping arithmetic under `session-groups.ts`,
 * and the visual vocabulary under `workbench-theme.ts`.
 */

import { PanelLeft, PanelRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { usePageHeader } from "@/contexts/usePageHeader";
import { contextPercent } from "@/components/chat/contextRingMath";
import { api } from "@/lib/api";
import { Composer } from "@/components/workbench/Composer";
import { retire, startFetch, useFetchGuard } from "@/components/workbench/fetch-guard";
import { FilePreview } from "@/components/workbench/FilePreview";
import { ControlCenter } from "@/components/workbench/ControlCenter";
import { SessionsList, type SourceFilter } from "@/components/workbench/SessionsList";
import { Transcript } from "@/components/workbench/Transcript";
import { WorkbenchRail } from "@/components/workbench/WorkbenchRail";
import type { SessionGroupKey } from "@/components/workbench/session-groups";
import { WorkspacePanel, type WorkspaceTab } from "@/components/workbench/WorkspacePanel";
import {
  WORKBENCH_ROUTES,
  getWorkspaceTree,
  getWorkbenchSession,
  getWorkbenchSessions,
  postWorkbenchTurn,
  readWorkspaceFile,
  resultData,
  type ApiResult,
  type RouteProbe,
  type WorkspaceFile,
  type WorkspaceTree as WorkspaceTreeData,
  type WorkbenchSessionDetail,
  type WorkbenchSessionsPage,
  type WorkbenchTurn,
} from "@/lib/workbench-api";

/** Which single column a narrow viewport is showing. */
type NarrowPane = "sessions" | "chat";

/**
 * Whether the composer may send. Before any turn has been attempted this page
 * does not know whether `POST /turn` exists, so it lets the operator try and
 * reports what the route said; only a confirmed 404 turns sending off, with the
 * reason stated next to the input.
 */
function composerGate(
  turn: ApiResult<WorkbenchTurn> | null,
  hasSession: boolean,
): { disabled: boolean; reason: string | null } {
  if (turn?.state === "not_available") {
    return { disabled: true, reason: `Sending is off: ${WORKBENCH_ROUTES.turn} is not mounted yet.` };
  }
  if (turn?.state === "error") {
    return {
      disabled: false,
      reason: `The last turn failed${turn.error.code ? ` (${turn.error.code})` : ""} — sending is still enabled.`,
    };
  }
  if (!hasSession) {
    // A turn needs a session id on this seam: `POST /turn` refuses one without
    // it rather than silently starting a second store entry.
    return { disabled: true, reason: "Open a conversation on the left to send a message here." };
  }
  return { disabled: false, reason: null };
}

export default function WorkbenchPage() {
  const { setTitle } = usePageHeader();
  const sessionsGuard = useFetchGuard<ApiResult<WorkbenchSessionsPage>>();
  const detailGuard = useFetchGuard<ApiResult<WorkbenchSessionDetail>>();
  const treeGuard = useFetchGuard<ApiResult<WorkspaceTreeData>>();
  const fileGuard = useFetchGuard<ApiResult<WorkspaceFile>>();
  const turnGuard = useFetchGuard<ApiResult<WorkbenchTurn>>();

  const [sessions, setSessions] = useState<ApiResult<WorkbenchSessionsPage> | null>(null);
  const [detail, setDetail] = useState<ApiResult<WorkbenchSessionDetail> | null>(null);
  const [tree, setTree] = useState<ApiResult<WorkspaceTreeData> | null>(null);
  const [file, setFile] = useState<ApiResult<WorkspaceFile> | null>(null);
  const [turn, setTurn] = useState<ApiResult<WorkbenchTurn> | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The tree lists one folder per call and starts at the workspace root (`""`);
  // the value lives here so the Control Center can print the exact path read
  // and the composer's workspace chip can name the root it resolved to.
  const [treePath] = useState("");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("webui");
  const [activeSource, setActiveSource] = useState("all");
  const [pinned, setPinned] = useState<ReadonlySet<string>>(() => new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<ReadonlySet<SessionGroupKey>>(
    () => new Set(),
  );
  const [openFolders, setOpenFolders] = useState<ReadonlySet<string>>(() => new Set());
  const [fileSelection, setFileSelection] = useState<ReadonlySet<string>>(() => new Set());
  const [tab, setTab] = useState<WorkspaceTab>("files");
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [workspaceWide, setWorkspaceWide] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [pane, setPane] = useState<NarrowPane>("chat");
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [profileName, setProfileName] = useState<string | null>(null);

  // The route's own identity, so the header reads correctly even before the
  // owner registers the path in resolve-page-title.ts.
  useEffect(() => {
    setTitle("Qubit Workbench");
    return () => setTitle(null);
  }, [setTitle]);

  // Sessions (sidebar).
  useEffect(() => {
    startFetch(sessionsGuard, getWorkbenchSessions, (next) => {
      if (next !== undefined) setSessions(next);
    });
    return () => retire(sessionsGuard);
  }, [sessionsGuard, reloadKey]);

  // Workspace root listing (right panel).
  useEffect(() => {
    startFetch(treeGuard, () => getWorkspaceTree(treePath), (next) => {
      if (next !== undefined) setTree(next);
    });
    return () => retire(treeGuard);
  }, [treeGuard, reloadKey, treePath]);

  // Selected session transcript (centre).
  useEffect(() => {
    if (selectedId === null) return;
    startFetch(detailGuard, () => getWorkbenchSession(selectedId), (next) => {
      if (next === undefined) return;
      setDetail(next);
      setTurn(null);
    });
    return () => retire(detailGuard);
  }, [detailGuard, reloadKey, selectedId]);

  // Previewed file.
  useEffect(() => {
    if (filePath === null) return;
    startFetch(fileGuard, () => readWorkspaceFile(filePath), (next) => {
      if (next !== undefined) setFile(next);
    });
    return () => retire(fileGuard);
  }, [fileGuard, filePath, reloadKey]);

  // The active dashboard profile names the composer's profile chip. A failure
  // leaves it at "—"; the chip is read-only, so a missing read costs nothing.
  useEffect(() => {
    let alive = true;
    void api
      .getActiveProfile()
      .then((res) => {
        if (alive) setProfileName(res.active ?? null);
      })
      .catch(() => {
        if (alive) setProfileName(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  const refreshAll = useCallback(() => setReloadKey((key) => key + 1), []);

  const selectSession = useCallback((id: string) => {
    setSelectedId(id);
    setDetail(null);
    setTurn(null);
    setPane("chat");
  }, []);

  const openFile = useCallback((path: string) => {
    setFilePath(path);
    setFile(null);
  }, []);

  /**
   * One directory listing for an expanded folder. The tree component calls
   * this on first open; it deliberately does NOT route through the page's
   * fetch guard, because the tree owns that result and a page-level guard would
   * make every folder's answer overwrite the last one's.
   */
  const loadTree = useCallback((path: string) => getWorkspaceTree(path), []);

  const togglePin = useCallback((id: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleGroup = useCallback((key: SessionGroupKey) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleFolder = useCallback((path: string) => {
    setOpenFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const toggleFileSelection = useCallback((path: string) => {
    setFileSelection((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || submitting || selectedId === null) return;
    setSubmitting(true);
    setTurn(null);
    startFetch(turnGuard, () => postWorkbenchTurn({ session_id: selectedId, text }), (next) => {
      // A superseded attempt leaves `submitting` alone: a newer one owns it.
      if (next === undefined) return;
      setTurn(next);
      setSubmitting(false);
      if (next.state === "ok") {
        setDraft("");
        // The turn is persisted by the backend; re-read so the transcript shows
        // the stored row rather than trusting the local copy.
        setReloadKey((key) => key + 1);
      }
    });
  }, [draft, selectedId, submitting, turnGuard]);

  const detailData = resultData(detail);
  const ringPercent = detailData
    ? contextPercent(detailData.prompt_tokens, detailData.context_window)
    : null;

  const treeData = resultData(tree);
  const treeDataEmpty = tree?.state === "empty" ? tree : null;
  const sessionsData = resultData(sessions);

  const selectedRow = useMemo(
    () => sessionsData?.rows.find((row) => row.id === selectedId) ?? null,
    [sessionsData, selectedId],
  );
  const selectedTitle = selectedRow?.title ?? detailData?.title ?? null;
  const selectedModel = selectedRow?.model ?? detailData?.model ?? null;

  const workspaceName =
    treeData?.root.trim() || treeDataEmpty?.data.root.trim() || null;
  const workspaceWhy = treeDataEmpty?.reason.detail ?? null;

  const gate = composerGate(turn, selectedId !== null);
  const turnUnavailable = turn?.state === "not_available";

  const probes: RouteProbe[] = useMemo(
    () => [
      { label: "Sessions list", path: WORKBENCH_ROUTES.sessions, result: sessions },
      {
        label: "Session detail",
        path: selectedId ? WORKBENCH_ROUTES.session(selectedId) : "/api/workbench/sessions/<id>",
        result: selectedId ? detail : null,
      },
      { label: "Timeline", path: "/api/workbench/sessions/<id>/timeline", result: null },
      { label: "Workspace tree", path: `${WORKBENCH_ROUTES.workspaceTree}?path=${treePath}`, result: tree },
      {
        label: "Workspace file",
        path: filePath
          ? WORKBENCH_ROUTES.workspaceFile(filePath)
          : "/api/workbench/workspace/file?path=<path>",
        result: filePath ? file : null,
      },
      { label: "Send turn", path: WORKBENCH_ROUTES.turn, result: turn },
    ],
    [detail, file, filePath, selectedId, sessions, tree, treePath, turn],
  );

  const turnNotMounted = turnUnavailable ? (
    <p
      role="note"
      data-testid="workbench-turn-not-available"
      className="px-3 pb-2 text-[11px] text-warning"
    >
      {WORKBENCH_ROUTES.turn} is not mounted yet — the message was not sent and no
      reply is being shown.
    </p>
  ) : null;
  const turnError = turn?.state === "error" ? turn.error : null;

  return (
    <div
      data-testid="workbench-page"
      className="flex h-full min-h-[70vh] w-full min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-background-base"
    >
      {/* Narrow-viewport switch. Hidden once the two columns fit side by side. */}
      <nav
        aria-label="Workbench panels"
        className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2 md:hidden"
      >
        <button
          type="button"
          onClick={() => setPane("sessions")}
          aria-pressed={pane === "sessions"}
          className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${
            pane === "sessions" ? "bg-primary/10 text-primary" : "text-text-secondary"
          }`}
        >
          <PanelLeft className="h-3.5 w-3.5" aria-hidden="true" /> Conversations
        </button>
        <button
          type="button"
          onClick={() => setPane("chat")}
          aria-pressed={pane === "chat"}
          className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${
            pane === "chat" ? "bg-primary/10 text-primary" : "text-text-secondary"
          }`}
        >
          <PanelRight className="h-3.5 w-3.5" aria-hidden="true" /> Chat
        </button>
      </nav>

      <div className="flex min-h-0 flex-1">
        {/* The rail is the second collapse: above `md` it is always present
            (icons only), and below `md` the header switch above is the way
            between the two columns. */}
        <WorkbenchRail />

        <div className={pane === "sessions" ? "flex min-h-0 w-full md:w-auto" : "hidden min-h-0 md:flex"}>
          <SessionsList
            result={sessions}
            selectedId={selectedId}
            query={query}
            onQueryChange={setQuery}
            sourceFilter={sourceFilter}
            onSourceFilter={setSourceFilter}
            activeSource={activeSource}
            onActiveSource={setActiveSource}
            pinned={pinned}
            collapsed={collapsedGroups}
            onToggleGroup={toggleGroup}
            onSelect={selectSession}
            onTogglePin={togglePin}
            onNewSession={() => setPane("chat")}
            onReload={refreshAll}
          />
        </div>

        <div
          data-testid="workbench-chat-pane"
          className={`min-h-0 min-w-0 flex-1 flex-col ${pane === "chat" ? "flex" : "hidden md:flex"}`}
        >
          <Transcript
            title={selectedTitle}
            model={selectedModel}
            result={detail}
            draftReply={turn?.state === "ok" ? turn.data.reply : null}
            submitting={submitting}
            onReload={refreshAll}
            composer={
              <>
                <Composer
                  value={draft}
                  onChange={setDraft}
                  onSubmit={submit}
                  disabled={gate.disabled}
                  disabledReason={gate.reason}
                  submitting={submitting}
                  contextPercent={ringPercent}
                  usedTokens={detailData?.prompt_tokens ?? null}
                  windowTokens={detailData?.context_window ?? null}
                  profileName={profileName}
                  workspaceName={workspaceName}
                  workspaceWhy={workspaceWhy}
                  modelName={selectedModel}
                  reasoningName={null}
                  workspaceOpen={workspaceOpen}
                  onToggleWorkspace={() => setWorkspaceOpen((open) => !open)}
                />
                {turnNotMounted}
                {turnError ? (
                  <p role="alert" className="px-3 pb-2 text-[11px] text-destructive">
                    Turn failed{turnError.status !== null ? ` (HTTP ${turnError.status})` : ""}:
                    {turnError.code ? ` ${turnError.code} —` : ""} {turnError.message}
                  </p>
                ) : null}
              </>
            }
          />
        </div>

        {/* Top-right toggle, shown when the panel is closed. */}
        {!workspaceOpen ? (
          <button
            type="button"
            onClick={() => setWorkspaceOpen(true)}
            aria-label="Show workspace panel"
            title="Show workspace panel"
            data-testid="workbench-workspace-reopen"
            className="hidden h-full w-7 shrink-0 items-center justify-center border-l border-border text-text-tertiary hover:bg-secondary/40 hover:text-foreground md:flex"
          >
            <PanelRight className="h-4 w-4" aria-hidden="true" />
          </button>
        ) : null}

        {workspaceOpen ? (
          <div
            className={
              workspaceWide ? "flex min-h-0 xl:w-[28rem]" : "flex min-h-0 xl:w-[20rem]"
            }
          >
          {controlsOpen ? (
            <ControlCenter
              probes={probes}
              workspaceOpen={workspaceOpen}
              onToggleWorkspace={() => setWorkspaceOpen((open) => !open)}
              onRefresh={refreshAll}
              onClose={() => setControlsOpen(false)}
            />
          ) : (
            <WorkspacePanel
              tab={tab}
              onTab={setTab}
              tree={tree}
              treePath={treePath}
              loadTree={loadTree}
              onOpenFile={openFile}
              onReload={refreshAll}
              selected={fileSelection}
              onToggleSelected={toggleFileSelection}
              selectedCount={fileSelection.size}
              openFolders={openFolders}
              onToggleFolder={toggleFolder}
              previewNode={
                <FilePreview path={filePath} result={file} onReload={refreshAll} />
              }
              expanded={workspaceWide}
              onToggleExpanded={() => setWorkspaceWide((wide) => !wide)}
              onHide={() => setWorkspaceOpen(false)}
              workspaceName={workspaceName}
              workspaceWhy={workspaceWhy}
            />
          )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

