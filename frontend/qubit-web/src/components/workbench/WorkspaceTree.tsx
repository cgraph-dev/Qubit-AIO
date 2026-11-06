/**
 * WorkspaceTree — the right panel's Files tab: a real directory tree.
 *
 * Ported from the absorbed console's file browser (`static/ui.js:27044-27420`
 * the tree rows, `:27201` the `5.6k` size and `:27311` the `(empty)` marker;
 * `style.css:3145-3160` its rhythm). Differences that are deliberate:
 *
 * - **Navigation is the `?path=` query on `GET /api/workbench/workspace/tree`,
 *   one call per folder.** The backend lists ONE directory at a time and
 *   confines every read to the registered project roots, so a folder is fetched
 *   when it is opened and its children are shown underneath it. Nothing is
 *   pre-fetched, and no path is ever composed that the backend did not hand
 *   back.
 * - **The checkbox affordance is a row-selection control, nothing more.** There
 *   is no bulk action on this seam (no delete, no move, no upload — the adapter
 *   is read-only by construction), so the checkbox selects rows for the panel's
 *   copy button; it never implies a write that does not exist.
 * - **Sizes are the backend's bytes**, rendered the way the console rendered
 *   them (`5.6k`). A directory shows no size because the backend reports none
 *   for one — it is not shown as `0`.
 * - **`(empty)` is used only when a folder was listed successfully and has no
 *   entries.** A folder whose read failed shows the failure; a workspace with
 *   no active project shows the backend's own reason.
 *
 * Folder listings live in this component's state, keyed by path, and every
 * branch is a pure render over that map: one request per path (the `pending`
 * marker is written before the call), no per-row effects, and a collapsed
 * folder's answer is kept for the next expansion.
 */

import { ChevronDown, ChevronRight, File as FileIcon, Folder, Link2, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";

import type {
  ApiResult,
  WorkspaceEntry,
  WorkspaceTree as WorkspaceTreeData,
} from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { EmptyStateWithReason, ResultGate } from "./WorkbenchStates";

/** Parent directory of a workspace-relative path; `""` is the root. */
export function parentPath(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "";
  const cut = clean.lastIndexOf("/");
  return cut <= 0 ? "" : clean.slice(0, cut);
}

/** The `5.6k` / `539.4k` form the absorbed tree used (bytes / 1024, one dp). */
export function compactSize(size: number | null): string | null {
  if (size === null || !Number.isFinite(size) || size <= 0) return null;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)}m`;
  return `${(size / 1024).toFixed(1)}k`;
}

/** `path -> listing`, or `"pending"` while its one request is in flight. */
type FolderListings = ReadonlyMap<string, ApiResult<WorkspaceTreeData> | "pending">;

/** Everything a row needs to render itself; threaded down, never re-created. */
interface TreeContext {
  selected: ReadonlySet<string>;
  onToggleSelected: (path: string) => void;
  onOpenFile: (path: string) => void;
  openFolders: ReadonlySet<string>;
  onToggleFolder: (path: string) => void;
  listings: FolderListings;
}

function EntryIcon({ entry }: { entry: WorkspaceEntry }) {
  if (entry.kind === "symlink") {
    return <Link2 className="h-3.5 w-3.5 shrink-0 text-sky-400/80" aria-hidden="true" />;
  }
  if (entry.isDir) {
    return <Folder className="h-3.5 w-3.5 shrink-0 text-amber-400/80" aria-hidden="true" />;
  }
  return <FileIcon className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden="true" />;
}

/** A folder's children, given its listing. Pure render. */
function FolderChildren({ path, depth, ctx }: { path: string; depth: number; ctx: TreeContext }) {
  const listing = ctx.listings.get(path);
  if (listing === undefined || listing === "pending") {
    return (
      <p
        className="py-0.5 text-[11px] text-text-tertiary"
        style={{ paddingLeft: 8 + (depth + 1) * 14 }}
      >
        Reading…
      </p>
    );
  }
  return (
    <ResultGate result={listing} copy={PANEL_COPY.workspace} loadingText="Reading folder…" compact>
      {(tree) =>
        tree.entries.length === 0 ? (
          <p
            className="py-0.5 text-[11px] italic text-text-tertiary opacity-60"
            data-testid={`workbench-folder-empty-${path}`}
            style={{ paddingLeft: 8 + (depth + 1) * 14 }}
          >
            (empty)
          </p>
        ) : (
          <ul className="flex flex-col">
            {tree.entries.map((child) => (
              <li key={child.path} className="min-w-0">
                <EntryRow entry={child} depth={depth + 1} ctx={ctx} />
              </li>
            ))}
          </ul>
        )
      }
    </ResultGate>
  );
}

function EntryRow({ entry, depth, ctx }: { entry: WorkspaceEntry; depth: number; ctx: TreeContext }) {
  const isOpen = ctx.openFolders.has(entry.path);
  const isSelected = ctx.selected.has(entry.path);
  const size = compactSize(entry.size);
  const canOpen = entry.isDir && !entry.targetOutsideWorkspace;

  const activate = () => {
    if (canOpen) ctx.onToggleFolder(entry.path);
    else if (!entry.isDir && !entry.targetOutsideWorkspace) ctx.onOpenFile(entry.path);
  };

  return (
    <>
      <div
        className={`group flex min-w-0 items-center gap-1 rounded-md py-1 pr-1.5 transition-colors hover:bg-secondary/40 ${
          isSelected ? "bg-primary/[0.08]" : ""
        }`}
        style={{ paddingLeft: 6 + depth * 14 }}
        data-testid={`workbench-entry-${entry.name}`}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => ctx.onToggleSelected(entry.path)}
          aria-label={`Select ${entry.name}`}
          className="h-3 w-3 shrink-0 accent-primary opacity-0 transition-opacity checked:opacity-100 group-hover:opacity-100 focus-visible:opacity-100"
        />
        <button
          type="button"
          onClick={activate}
          disabled={entry.targetOutsideWorkspace}
          title={
            entry.targetOutsideWorkspace
              ? "This symlink points outside the workspace — shown, not navigable"
              : entry.isDir
                ? `${entry.name} — folder`
                : entry.name
          }
          aria-expanded={entry.isDir ? isOpen : undefined}
          className="flex min-w-0 flex-1 items-center gap-1 text-left text-[12px] text-text-secondary hover:text-foreground disabled:cursor-not-allowed disabled:hover:text-text-secondary"
        >
          <span className="flex w-3 shrink-0 justify-center">
            {canOpen ? (
              isOpen ? (
                <ChevronDown className="h-3 w-3" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3 w-3" aria-hidden="true" />
              )
            ) : null}
          </span>
          <EntryIcon entry={entry} />
          <span className="min-w-0 flex-1 truncate">{entry.name}</span>
          {size ? (
            <span className="shrink-0 tabular-nums text-[10px] text-text-tertiary">{size}</span>
          ) : null}
        </button>
      </div>
      {canOpen && isOpen ? <FolderChildren path={entry.path} depth={depth} ctx={ctx} /> : null}
    </>
  );
}

export function WorkspaceTree({
  result,
  loadTree,
  onReload,
  selected,
  onToggleSelected,
  onOpenFile,
  openFolders,
  onToggleFolder,
  truncatedHint,
  compact = false,
}: {
  result: ApiResult<WorkspaceTreeData> | null;
  /** One directory listing. This component caches by path; this is the raw read. */
  loadTree: (path: string) => Promise<ApiResult<WorkspaceTreeData>>;
  onReload: () => void;
  selected: ReadonlySet<string>;
  onToggleSelected: (path: string) => void;
  onOpenFile: (path: string) => void;
  openFolders: ReadonlySet<string>;
  onToggleFolder: (path: string) => void;
  /** Set when the listing hit the backend's per-directory cap. */
  truncatedHint?: string | null;
  compact?: boolean;
}) {
  const [listings, setListings] = useState<FolderListings>(() => new Map());

  /**
   * Open or close a folder. Opening one with no listing starts exactly one
   * request: the `pending` marker is written before the call, and a second
   * click reads it and returns.
   */
  const toggleFolder = useCallback(
    (path: string) => {
      // The open/closed set is the page's; toggling it is unconditional.
      onToggleFolder(path);
      // Start the read unless this folder already has an answer in flight or
      // in hand. The functional updater is the authority on "already", so this
      // callback does not need `listings` in its deps — reading it from the
      // closure made the callback (and the whole tree below it) change on every
      // listing, which is what turned one folder read into an unbounded churn.
      setListings((prev) => {
        if (prev.has(path)) return prev;
        const started = path;
        void loadTree(started).then((next) => {
          setListings((cur) => new Map(cur).set(started, next));
        });
        return new Map(prev).set(started, "pending");
      });
    },
    [loadTree, onToggleFolder],
  );

  const ctx: TreeContext = {
    selected,
    onToggleSelected,
    onOpenFile,
    openFolders,
    onToggleFolder: toggleFolder,
    listings,
  };

  return (
    <div className={compact ? "flex min-h-0 flex-col" : "flex min-h-0 flex-1 flex-col"}>
      <div className="flex shrink-0 items-center justify-between gap-2 px-2 pb-1">
        <span
          className="min-w-0 truncate text-[10px] text-text-tertiary"
          title={truncatedHint ?? undefined}
        >
          {truncatedHint ?? "Read-only: this seam serves list and preview only."}
        </span>
        <button
          type="button"
          onClick={onReload}
          aria-label="Reload workspace"
          title="Reload workspace"
          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-text-tertiary hover:bg-secondary/50 hover:text-foreground [&>svg]:h-3 [&>svg]:w-3"
        >
          <RefreshCw aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2" data-testid="workbench-file-tree">
        <ResultGate
          result={result}
          copy={PANEL_COPY.workspace}
          loadingText="Reading workspace…"
          onRetry={onReload}
          compact
        >
          {(tree) => {
            if (result !== null && result.state === "empty") {
              return (
                <EmptyStateWithReason
                  testId="workbench-workspace-no-project"
                  title="No workspace to show."
                  detail={result.reason.detail}
                  code={result.reason.code}
                />
              );
            }
            if (tree.entries.length === 0) {
              return <p className="px-2 py-2 text-xs text-text-secondary">This folder is empty.</p>;
            }
            return (
              <ul className="flex flex-col" aria-label="Workspace entries">
                {tree.entries.map((entry) => (
                  <li key={entry.path} className="min-w-0">
                    <EntryRow entry={entry} depth={0} ctx={ctx} />
                  </li>
                ))}
              </ul>
            );
          }}
        </ResultGate>
      </div>
    </div>
  );
}

export default WorkspaceTree;
