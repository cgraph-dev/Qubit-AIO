/**
 * WorkspacePanel — the right column: WORKSPACE header, workspace dropdown, and
 * the Files / Artifacts / Todos tabs.
 *
 * Header and tabs ported from the absorbed console's right rail
 * (`static/index.html:1754-1781`): a `Workspace` title with icon actions, a
 * workspace selector, then three tabs.
 *
 * The Files tab is real (`GET /api/workbench/workspace/tree`). The other two
 * are NOT: this backend has no artifacts collector and no per-session todo
 * projection, so both tabs render `GapNotice` naming the route that would have
 * to exist. They are not hidden — the absorbed layout has three tabs, and
 * hiding two would hide the gap rather than report it — and they never render
 * a count or a row that implies content exists.
 *
 * The header's icon actions are the ones this seam can actually serve:
 * reload (a re-read), copy paths (from the selection the tree's checkboxes
 * make), pin/close are the panel's own visibility, and "add file" is disabled
 * because every workspace route here is read-only by construction.
 */

import { Copy, Maximize2, Minimize2, Plus, RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { ApiResult, WorkspaceTree as WorkspaceTreeData } from "@/lib/workbench-api";
import { MISSING_WORKBENCH_ROUTES } from "@/lib/workbench-api";

import { FilePreview } from "./FilePreview";
import { GapNotice, HeadIconButton } from "./WorkbenchStates";
import { WorkspaceTree } from "./WorkspaceTree";
import { PANEL_HEAD_LABEL, REGION } from "./workbench-theme";

export type WorkspaceTab = "files" | "artifacts" | "todos";

const TABS: readonly { id: WorkspaceTab; label: string }[] = [
  { id: "files", label: "Files" },
  { id: "artifacts", label: "Artifacts" },
  { id: "todos", label: "Todos" },
];

export function WorkspacePanel({
  tab,
  onTab,
  tree,
  treePath,
  loadTree,
  onOpenFile,
  onReload,
  selected,
  onToggleSelected,
  selectedCount,
  openFolders,
  onToggleFolder,
  previewNode,
  expanded,
  onToggleExpanded,
  onHide,
  workspaceName,
  workspaceWhy,
}: {
  tab: WorkspaceTab;
  onTab: (next: WorkspaceTab) => void;
  tree: ApiResult<WorkspaceTreeData> | null;
  treePath: string;
  loadTree: (path: string) => Promise<ApiResult<WorkspaceTreeData>>;
  onOpenFile: (path: string) => void;
  onReload: () => void;
  selected: ReadonlySet<string>;
  onToggleSelected: (path: string) => void;
  selectedCount: number;
  openFolders: ReadonlySet<string>;
  onToggleFolder: (path: string) => void;
  /** FilePreview, rendered by the page. */
  previewNode: ReactNode;
  expanded: boolean;
  onToggleExpanded: () => void;
  onHide: () => void;
  /** The workspace root the backend named, or `null`. */
  workspaceName: string | null;
  /** The backend's own reason when it named none. */
  workspaceWhy: string | null;
}) {
  const [copied, setCopied] = useState(false);

  const copySelection = async () => {
    const paths = [...selected];
    const text = paths.length > 0 ? paths.join("\n") : treePath;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // A blocked clipboard is not an error the panel should invent a result
      // for; the button simply does not claim success.
      setCopied(false);
    }
  };

  const truncated =
    tree !== null && (tree.state === "ok" || tree.state === "empty") && tree.data.truncated
      ? "Listing hit the backend's per-directory cap."
      : null;

  return (
    <aside
      aria-label="Workspace"
      data-testid="workbench-workspace-panel"
      className={`${REGION.workspace} min-h-0`}
    >
      <div className={REGION.panelHead}>
        <h2 className={PANEL_HEAD_LABEL}>Workspace</h2>
        <div className="flex shrink-0 items-center gap-0.5">
          <HeadIconButton
            label="New file — refused: every workspace route on this seam is read-only"
            onClick={() => undefined}
            disabled
            testId="workbench-ws-new"
          >
            <Plus aria-hidden="true" />
          </HeadIconButton>
          <HeadIconButton
            label={copied ? "Copied" : selectedCount > 0 ? `Copy ${selectedCount} path(s)` : "Copy current folder path"}
            onClick={() => void copySelection()}
            testId="workbench-ws-copy"
          >
            <Copy aria-hidden="true" />
          </HeadIconButton>
          <HeadIconButton label="Reload workspace" onClick={onReload} testId="workbench-ws-reload">
            <RefreshCw aria-hidden="true" />
          </HeadIconButton>
          <HeadIconButton
            label={expanded ? "Narrow the workspace panel" : "Widen the workspace panel"}
            onClick={() => onToggleExpanded()}
            testId="workbench-ws-expand"
          >
            {expanded ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
          </HeadIconButton>
          <HeadIconButton label="Hide workspace panel" onClick={onHide} testId="workbench-ws-close">
            <span aria-hidden="true" className="text-[13px] leading-none">
              ×
            </span>
          </HeadIconButton>
        </div>
      </div>

      {/* Workspace dropdown — the seam lists ONE root per read, so this shows
          the active one and states the reason when there is none. */}
      <div className="shrink-0 px-2 pb-1.5">
        <button
          type="button"
          disabled
          data-testid="workbench-workspace-select"
          title={
            workspaceName
              ? `Active workspace: ${workspaceName}`
              : (workspaceWhy ?? "The workspace tree has not reported a root yet.")
          }
          aria-label={`Workspace: ${workspaceName ?? "none"}`}
          className="flex w-full min-w-0 cursor-not-allowed items-center gap-1.5 rounded-md border border-border bg-background-base px-2 py-1 text-left text-[11px] text-text-secondary opacity-90 [&>svg]:h-3 [&>svg]:w-3"
        >
          <span className="shrink-0 text-text-tertiary" aria-hidden="true">
            ⌂
          </span>
          <span className="min-w-0 flex-1 truncate">
            {workspaceName ? lastSegment(workspaceName) : "none"}
          </span>
          <span className="shrink-0 text-[9px] uppercase tracking-wide text-text-tertiary">main</span>
        </button>
      </div>

      {/* Tabs */}
      <div className="flex shrink-0 gap-1 border-b border-border px-2 pb-1.5" role="tablist" aria-label="Workspace panel views">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => onTab(entry.id)}
            data-testid={`workbench-ws-tab-${entry.id}`}
            className={`min-w-0 flex-1 rounded-[7px] border px-2 py-[5px] text-[12px] transition-colors ${
              tab === entry.id
                ? "border-border bg-secondary/50 text-foreground"
                : "border-transparent text-text-tertiary hover:text-foreground"
            }`}
          >
            {entry.label}
            {/* An artifact count is NOT rendered: the backend has no artifacts
                route, so any number here would be invented. */}
          </button>
        ))}
      </div>

      {tab === "files" ? (
        <>
          <WorkspaceTree
            result={tree}
            selected={selected}
            onToggleSelected={onToggleSelected}
            onOpenFile={onOpenFile}
            onReload={onReload}
            loadTree={loadTree}
            openFolders={openFolders}
            onToggleFolder={onToggleFolder}
            truncatedHint={truncated}
          />
          <div className="min-h-0 max-h-[45%] shrink-0 overflow-hidden border-t border-border">
            {previewNode}
          </div>
        </>
      ) : null}

      {tab === "artifacts" ? (
        <GapNotice
          label="Artifacts"
          route={MISSING_WORKBENCH_ROUTES.artifacts.route}
          why={MISSING_WORKBENCH_ROUTES.artifacts.why}
          testId="workbench-artifacts-gap"
        />
      ) : null}

      {tab === "todos" ? (
        <GapNotice
          label="Todos"
          route={MISSING_WORKBENCH_ROUTES.todos.route}
          why={MISSING_WORKBENCH_ROUTES.todos.why}
          testId="workbench-todos-gap"
        />
      ) : null}
    </aside>
  );
}

/** Last path segment, for the dropdown's label. */
function lastSegment(path: string): string {
  const clean = path.replace(/\/+$/, "");
  const cut = clean.lastIndexOf("/");
  return cut >= 0 ? clean.slice(cut + 1) || clean : clean;
}

export { FilePreview };
export default WorkspacePanel;
