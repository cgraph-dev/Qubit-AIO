/**
 * FilePreview — what the right panel shows below the workspace listing.
 *
 * Two guards worth stating, because both are honesty rules rather than style:
 * - a file the backend marked `truncated` says so above the text, so a cut-off
 *   read never looks like the whole file;
 * - a binary/oversized body is rendered as pre-formatted text with no attempt
 *   to interpret it — no `dangerouslySetInnerHTML`, ever, on this surface.
 */

import { FileText } from "lucide-react";

import { Button } from "@nous-research/ui/ui/components/button";
import type { ApiResult, WorkspaceFile } from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { ResultGate } from "./WorkbenchStates";

export function FilePreview({
  path,
  result,
  onReload,
}: {
  /** Path currently being previewed; `null` renders the idle hint. */
  path: string | null;
  result: ApiResult<WorkspaceFile> | null;
  onReload: () => void;
}) {
  if (path === null) {
    return (
      <div
        data-testid="workbench-file-idle"
        className="flex h-full items-start gap-2 px-3 py-3 text-xs text-text-tertiary"
      >
        <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        Pick a file above to preview it here.
      </div>
    );
  }

  return (
    <div
      data-testid="workbench-file-preview"
      className="flex h-full min-h-0 flex-col"
      aria-label={`Preview of ${path}`}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-1">
        <span className="min-w-0 truncate font-mono-ui text-[11px] text-text-secondary" title={path}>
          {path}
        </span>
        <Button ghost size="icon" onClick={onReload} aria-label="Reload file" title="Reload file">
          <FileText />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        <ResultGate result={result} copy={PANEL_COPY.file} loadingText="Reading file…" onRetry={onReload} compact>
          {(file) => (
            <>
              {file.truncated ? (
                <p role="note" className="mb-1 text-[11px] text-warning">
                  Preview truncated by the backend — showing the first part only.
                </p>
              ) : null}
              {file.content.length === 0 ? (
                <p className="text-xs text-text-secondary">This file is empty.</p>
              ) : (
                <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-foreground">
                  {file.content}
                </pre>
              )}
            </>
          )}
        </ResultGate>
      </div>
    </div>
  );
}

export default FilePreview;
