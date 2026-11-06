/**
 * WorkspaceTree — the right panel's file browser.
 *
 * Two panes in one panel: a directory listing at the top, and (when a file is
 * picked) `FilePreview` filling the rest. Directory navigation is the
 * `?path=` query on `GET /api/workbench/workspace/tree`, whose response carries
 * the entries for that directory.
 *
 * No path-escape grant model is ported: the backend decides what is listable,
 * and the frontend passes the path it was given back verbatim.
 */

import { ArrowUp, File, FolderOpen, RefreshCw } from "lucide-react";

import { Button } from "@nous-research/ui/ui/components/button";

import type { ApiResult, WorkspaceTree } from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { PanelHeading, ResultGate } from "./WorkbenchStates";

/** Parent directory of a workspace-relative path; "" is the root. */
function parentPath(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "";
  const cut = clean.lastIndexOf("/");
  return cut <= 0 ? "" : clean.slice(0, cut);
}

function joinPath(base: string, name: string): string {
  return base ? `${base.replace(/\/+$/, "")}/${name}` : name;
}

function formatSize(size: number | null): string | null {
  if (size === null || !Number.isFinite(size) || size < 0) return null;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
}

export function WorkspaceTree({
  path,
  result,
  selectedFile,
  onNavigate,
  onOpenFile,
  onReload,
  preview,
}: {
  path: string;
  result: ApiResult<WorkspaceTree> | null;
  selectedFile: string | null;
  onNavigate: (path: string) => void;
  onOpenFile: (path: string) => void;
  onReload: () => void;
  /** FilePreview, rendered by the page under the listing. */
  preview: React.ReactNode;
}) {
  return (
    <section
      aria-label="Workspace files"
      data-testid="workbench-workspace"
      className="flex h-full min-h-0 flex-col"
    >
      <PanelHeading
        title="Workspace"
        actions={
          <>
            <Button
              ghost
              size="icon"
              onClick={() => onNavigate(parentPath(path))}
              disabled={!path}
              aria-label="Go to parent folder"
              title="Parent folder"
            >
              <ArrowUp />
            </Button>
            <Button
              ghost
              size="icon"
              onClick={onReload}
              aria-label="Reload workspace"
              title="Reload"
            >
              <RefreshCw />
            </Button>
          </>
        }
      />
      <p
        className="shrink-0 truncate border-b border-border px-3 py-1 font-mono-ui text-[11px] text-text-tertiary"
        title={path || "/"}
      >
        /{path}
      </p>

      <div className="max-h-[45%] min-h-0 shrink-0 overflow-y-auto px-2 py-2">
        <ResultGate
          result={result}
          copy={PANEL_COPY.workspace}
          loadingText="Reading workspace…"
          onRetry={onReload}
          compact
        >
          {(tree) => {
            if (tree.entries.length === 0) {
              return (
                <p className="px-1 py-2 text-xs text-text-secondary">
                  This folder is empty.
                </p>
              );
            }
            return (
              <ul className="flex flex-col" aria-label="Workspace entries">
                {tree.entries.map((entry) => {
                  const full = entry.path || joinPath(path, entry.name);
                  const isDir = entry.kind === "directory";
                  const active = !isDir && full === selectedFile;
                  const size = formatSize(entry.size);
                  return (
                    <li key={`${entry.kind}:${full}`} className="min-w-0">
                      <button
                        type="button"
                        onClick={() => (isDir ? onNavigate(full) : onOpenFile(full))}
                        aria-current={active ? "true" : undefined}
                        data-testid={`workbench-entry-${entry.name}`}
                        className={`flex w-full min-w-0 items-center gap-1.5 px-1 py-1 text-left text-xs transition-colors ${
                          active
                            ? "bg-primary/[0.08] text-primary"
                            : "text-foreground hover:bg-secondary/30"
                        }`}
                      >
                        {isDir ? (
                          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
                        ) : (
                          <File className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden="true" />
                        )}
                        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                        {size ? (
                          <span className="shrink-0 text-[10px] text-text-tertiary">{size}</span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            );
          }}
        </ResultGate>
      </div>

      <div className="min-h-0 flex-1 border-t border-border">{preview}</div>
    </section>
  );
}

export default WorkspaceTree;
