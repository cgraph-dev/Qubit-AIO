/**
 * Qubit Workbench (route `/qubit-workbench`).
 *
 * The three-panel chat workspace: sessions on the left, transcript + composer in
 * the centre, workspace files on the right. It is a NEW surface and a sibling of
 * `/qubit-chat`, never a replacement: the voice path (push-to-talk, the
 * Right-Ctrl hotkey, the desktop-helper hand-off, read-aloud, the live voice
 * session follow) stays on `/qubit-chat` and nothing here references it.
 *
 * Data comes only from `@/lib/workbench-api`, and only in one of three typed
 * states. A 404 from the `/api/workbench/*` seam renders "Backend not
 * available" — never an empty list, which would report "you have no sessions",
 * a different and false claim.
 *
 * This file is layout and wiring. Panels are small components under
 * `components/workbench/`; the composer's control cluster and the ring come from
 * there too. Layout is responsive: one panel at a time below `md`, all three
 * side by side from `md` up.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { usePageHeader } from "@/contexts/usePageHeader";
import { contextPercent } from "@/components/chat/contextRingMath";
import { ControlCenter } from "@/components/workbench/ControlCenter";
import { retire, startFetch, useFetchGuard } from "@/components/workbench/fetch-guard";
import { Composer } from "@/components/workbench/Composer";
import { FilePreview } from "@/components/workbench/FilePreview";
import { SessionsList } from "@/components/workbench/SessionsList";
import { Transcript } from "@/components/workbench/Transcript";
import { WorkspaceTree } from "@/components/workbench/WorkspaceTree";
import {
  WORKBENCH_ROUTES,
  getWorkbenchSession,
  getWorkbenchSessions,
  getWorkspaceTree,
  postWorkbenchTurn,
  readWorkspaceFile,
  type ApiResult,
  type RouteProbe,
  type WorkspaceFile,
  type WorkspaceTree as WorkspaceTreeData,
  type WorkbenchSessionDetail,
  type WorkbenchSessionRow,
  type WorkbenchTurn,
} from "@/lib/workbench-api";

/** Which panel a narrow viewport is showing. */
type NarrowPane = "sessions" | "chat" | "workspace";

const PANE_TAB: ReadonlyArray<{ id: NarrowPane; label: string }> = [
  { id: "sessions", label: "Sessions" },
  { id: "chat", label: "Chat" },
  { id: "workspace", label: "Files" },
];

/**
 * Which pane a narrow viewport starts on. The conversation is the reason the
 * page exists; the sessions list and the workspace are one tap away.
 */
const INITIAL_PANE: NarrowPane = "chat";

/**
 * Whether the composer may send. Before any turn has been attempted this page
 * does not know whether `POST /turn` exists, so it lets the operator try and
 * reports what the route said; only a confirmed 404 turns sending off, with the
 * reason stated next to the input.
 */
function composerGate(turn: ApiResult<WorkbenchTurn> | null): {
  disabled: boolean;
  reason: string | null;
} {
  if (turn?.state === "not_available") {
    return { disabled: true, reason: `Sending is off: ${WORKBENCH_ROUTES.turn} is not mounted yet.` };
  }
  if (turn === null) {
    return { disabled: false, reason: `Sending has not been exercised in this visit yet; the reply arrives from ${WORKBENCH_ROUTES.turn}.` };
  }
  return { disabled: false, reason: null };
}

/** Per-pane width, applied from `md` up (the three-panel layout). */
const PANE_WIDTH: Record<NarrowPane, string> = {
  sessions: "md:w-[16rem] md:border-r",
  chat: "",
  workspace: "md:w-[20rem] md:border-l",
};

/**
 * One pane's visibility. Above `md` all three sit side by side; below it the
 * switch decides which single pane is mounted, so a phone never has to scroll
 * three columns. (`hidden md:flex` is written literally — a computed class
 * list of competing flex variants is a coin flip on which rule wins.)
 */
function paneVisibility(id: NarrowPane, pane: NarrowPane): string {
  const base =
    id === "chat"
      ? "min-h-0 min-w-0 flex-1 flex-col"
      : `min-h-0 w-full shrink-0 flex-col border-border ${PANE_WIDTH[id]}`;
  return `${base} ${pane === id ? "flex" : "hidden"} md:flex`;
}

export default function WorkbenchPage() {
  const { setTitle } = usePageHeader();
  const sessionsGuard = useFetchGuard<ApiResult<WorkbenchSessionRow[]>>();
  const detailGuard = useFetchGuard<ApiResult<WorkbenchSessionDetail>>();
  const treeGuard = useFetchGuard<ApiResult<WorkspaceTreeData>>();
  const fileGuard = useFetchGuard<ApiResult<WorkspaceFile>>();
  const turnGuard = useFetchGuard<ApiResult<WorkbenchTurn>>();

  const [sessions, setSessions] = useState<ApiResult<WorkbenchSessionRow[]> | null>(null);
  const [detail, setDetail] = useState<ApiResult<WorkbenchSessionDetail> | null>(null);
  const [tree, setTree] = useState<ApiResult<WorkspaceTreeData> | null>(null);
  const [file, setFile] = useState<ApiResult<WorkspaceFile> | null>(null);
  const [turn, setTurn] = useState<ApiResult<WorkbenchTurn> | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [treePath, setTreePath] = useState("");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [pane, setPane] = useState<NarrowPane>(INITIAL_PANE);
  const [reloadKey, setReloadKey] = useState(0);

  // The route's own identity, so the header reads correctly even before the
  // owner registers the path in resolve-page-title.ts.
  useEffect(() => {
    setTitle("Qubit Workbench");
    return () => setTitle(null);
  }, [setTitle]);

  // Sessions (left panel).
  useEffect(() => {
    startFetch(sessionsGuard, getWorkbenchSessions, (next) => {
      if (next !== undefined) setSessions(next);
    });
    return () => retire(sessionsGuard);
  }, [sessionsGuard, reloadKey]);

  // Workspace tree (right panel), keyed on the browsed path.
  useEffect(() => {
    startFetch(treeGuard, () => getWorkspaceTree(treePath), (next) => {
      if (next !== undefined) setTree(next);
    });
    return () => retire(treeGuard);
  }, [treeGuard, reloadKey, treePath]);

  // Selected session transcript (centre panel).
  useEffect(() => {
    if (selectedId === null) return;
    startFetch(detailGuard, () => getWorkbenchSession(selectedId), (next) => {
      if (next === undefined) return;
      setDetail(next);
      setTurn(null);
    });
    return () => retire(detailGuard);
  }, [detailGuard, reloadKey, selectedId]);

  // Previewed file (right panel, below the listing).
  useEffect(() => {
    if (filePath === null) return;
    startFetch(fileGuard, () => readWorkspaceFile(filePath), (next) => {
      if (next !== undefined) setFile(next);
    });
    return () => retire(fileGuard);
  }, [fileGuard, filePath, reloadKey]);

  const refreshAll = useCallback(() => {
    setReloadKey((key) => key + 1);
  }, []);

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

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || submitting) return;
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

  const detailForRing = detail?.state === "ok" ? detail.data : null;
  const ringPercent = detailForRing
    ? contextPercent(detailForRing.prompt_tokens, detailForRing.context_window)
    : null;

  const turnUnavailable = turn?.state === "not_available";
  const turnError = turn?.state === "error" ? turn.error : null;
  const gate = composerGate(turn);
  const turnNotMounted = turnUnavailable ? (
    <p role="note" data-testid="workbench-turn-not-available" className="px-3 pb-2 text-[11px] text-warning">
      {WORKBENCH_ROUTES.turn} is not mounted yet — the message was not sent and no
      reply is being shown.
    </p>
  ) : null;

  const probes: RouteProbe[] = useMemo(
    () => [
      { label: "Sessions list", path: WORKBENCH_ROUTES.sessions, result: sessions },
      {
        label: "Session detail",
        path: selectedId ? WORKBENCH_ROUTES.session(selectedId) : "/api/workbench/sessions/<id>",
        result: selectedId ? detail : null,
      },
      { label: "Workspace tree", path: `${WORKBENCH_ROUTES.workspaceTree}?path=${treePath}`, result: tree },
      {
        label: "Workspace file",
        path: filePath ? WORKBENCH_ROUTES.workspaceFile(filePath) : "/api/workbench/workspace/file?path=<path>",
        result: filePath ? file : null,
      },
      { label: "Send turn", path: WORKBENCH_ROUTES.turn, result: turn },
    ],
    [detail, file, filePath, selectedId, sessions, tree, treePath, turn],
  );

  const selectedTitle =
    sessions?.state === "ok"
      ? (sessions.data.find((row) => row.id === selectedId)?.title ?? null)
      : null;

  return (
    <div
      data-testid="workbench-page"
      className="flex min-h-[70vh] w-full min-w-0 flex-col border border-border bg-background-base/40"
    >
      {/* Narrow-viewport panel switch. Hidden once all three fit. */}
      <nav
        aria-label="Workbench panels"
        className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2 md:hidden"
      >
        {PANE_TAB.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setPane(tab.id)}
            aria-pressed={pane === tab.id}
            data-testid={`workbench-pane-tab-${tab.id}`}
            className={`px-2 py-1 text-xs transition-colors ${
              pane === tab.id
                ? "bg-primary/10 text-primary"
                : "text-text-secondary hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="flex min-h-0 flex-1">
        {/* Left: sessions */}
        <div className={paneVisibility("sessions", pane)} data-testid="workbench-sessions-pane">
          <SessionsList
            result={sessions}
            selectedId={selectedId}
            query={query}
            onQueryChange={setQuery}
            onSelect={selectSession}
            onReload={refreshAll}
          />
        </div>

        {/* Centre: transcript + composer */}
        <div className={paneVisibility("chat", pane)} data-testid="workbench-chat-pane">
          <Transcript
            title={selectedTitle}
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
                  workspaceOpen={workspaceOpen}
                  onToggleWorkspace={() => setWorkspaceOpen((open) => !open)}
                  controlsOpen={controlsOpen}
                  onToggleControls={() => setControlsOpen((open) => !open)}
                  contextPercent={ringPercent}
                  usedTokens={detailForRing?.prompt_tokens ?? null}
                  windowTokens={detailForRing?.context_window ?? null}
                />
                {turnNotMounted}
                {turnError ? (
                  <p role="alert" className="px-3 pb-2 text-[11px] text-destructive">
                    Turn failed{turnError.status !== null ? ` (HTTP ${turnError.status})` : ""}:{" "}
                    {turnError.message}
                  </p>
                ) : null}
              </>
            }
          />
        </div>

        {/* Right: workspace browser, or the control center in its place */}
        <div
          data-testid="workbench-files-pane"
          className={paneVisibility("workspace", pane)}
        >
          {controlsOpen ? (
            <ControlCenter
              probes={probes}
              workspaceOpen={workspaceOpen}
              onToggleWorkspace={() => setWorkspaceOpen((open) => !open)}
              onRefresh={refreshAll}
            />
          ) : workspaceOpen ? (
            <WorkspaceTree
              path={treePath}
              result={tree}
              selectedFile={filePath}
              onNavigate={(next) => {
                setTreePath(next);
                setFilePath(null);
                setFile(null);
              }}
              onOpenFile={openFile}
              onReload={refreshAll}
              preview={
                <FilePreview path={filePath} result={file} onReload={refreshAll} />
              }
            />
          ) : (
            <div className="flex h-full flex-col items-start gap-2 px-3 py-3">
              <p className="text-xs text-text-secondary">The workspace panel is hidden.</p>
              <button
                type="button"
                onClick={() => setWorkspaceOpen(true)}
                className="text-xs text-primary underline-offset-2 hover:underline"
              >
                Show workspace files
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
