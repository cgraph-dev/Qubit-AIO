/**
 * SessionsList — the workbench's left panel.
 *
 * A filtered list over `GET /api/workbench/sessions`. Filtering is local and
 * case-insensitive over the title and preview only; it never invents rows and
 * never hides the "no sessions" fact behind a search box (the empty state says
 * which of the two it is).
 */

import { Search, X } from "lucide-react";

import { Badge } from "@nous-research/ui/ui/components/badge";
import { Input } from "@nous-research/ui/ui/components/input";

import { timeAgo } from "@/lib/utils";
import type { ApiResult, WorkbenchSessionRow } from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import { PanelHeading, ResultGate } from "./WorkbenchStates";

function rowLabel(row: WorkbenchSessionRow): string {
  const title = row.title?.trim();
  if (title && title !== "Untitled") return title;
  const preview = row.preview?.trim();
  if (preview) return preview.slice(0, 60);
  return row.id;
}

function filterSessions(
  rows: readonly WorkbenchSessionRow[],
  query: string,
): WorkbenchSessionRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...rows];
  return rows.filter((row) => {
    const haystack = `${row.title ?? ""} ${row.preview ?? ""}`.toLowerCase();
    return haystack.includes(needle);
  });
}

export function SessionsList({
  result,
  selectedId,
  query,
  onQueryChange,
  onSelect,
  onReload,
}: {
  result: ApiResult<WorkbenchSessionRow[]> | null;
  selectedId: string | null;
  query: string;
  onQueryChange: (next: string) => void;
  onSelect: (id: string) => void;
  onReload: () => void;
}) {
  return (
    <section
      aria-label="Sessions"
      data-testid="workbench-sessions"
      className="flex h-full min-h-0 flex-col border-border md:border-r"
    >
      <PanelHeading title="Sessions" />
      <div className="shrink-0 p-2">
        <label className="relative flex items-center">
          <Search
            className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-text-tertiary"
            aria-hidden="true"
          />
          <span className="sr-only">Filter sessions</span>
          <Input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            placeholder="Filter sessions…"
            autoComplete="off"
            className="h-7 w-full pl-7 pr-7 text-xs"
          />
          {query ? (
            <button
              type="button"
              onClick={() => onQueryChange("")}
              aria-label="Clear session filter"
              className="absolute right-1.5 text-text-tertiary hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ) : null}
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ResultGate
          result={result}
          copy={PANEL_COPY.sessions}
          loadingText="Reading sessions…"
          onRetry={onReload}
          compact
        >
          {(rows) => {
            const visible = filterSessions(rows, query);
            if (rows.length === 0) {
              return (
                <p
                  data-testid="workbench-sessions-empty"
                  className="px-1 py-4 text-xs text-text-secondary"
                >
                  No sessions reported by the workbench backend.
                </p>
              );
            }
            if (visible.length === 0) {
              return (
                <p className="px-1 py-4 text-xs text-text-secondary">
                  No session matches “{query.trim()}”.
                </p>
              );
            }
            return (
              <ul className="flex flex-col gap-1" aria-label="Workbench sessions">
                {visible.map((row) => {
                  const selected = row.id === selectedId;
                  return (
                    <li key={row.id} className="min-w-0">
                      <button
                        type="button"
                        onClick={() => onSelect(row.id)}
                        aria-current={selected ? "true" : undefined}
                        data-testid={`workbench-session-${row.id}`}
                        className={`flex w-full min-w-0 flex-col gap-0.5 border px-2 py-1.5 text-left transition-colors ${
                          selected
                            ? "border-primary/40 bg-primary/[0.08]"
                            : "border-transparent hover:bg-secondary/30"
                        }`}
                      >
                        <span className="min-w-0 truncate text-xs font-medium text-foreground">
                          {rowLabel(row)}
                        </span>
                        <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-text-tertiary">
                          {row.model ? (
                            <span className="max-w-[8rem] truncate font-mono-ui">
                              {row.model.split("/").pop()}
                            </span>
                          ) : null}
                          {row.message_count !== null ? (
                            <span className="shrink-0">{row.message_count} msgs</span>
                          ) : null}
                          {row.last_active !== null ? (
                            <span className="shrink-0">{timeAgo(row.last_active)}</span>
                          ) : null}
                          {row.source ? (
                            <Badge tone="outline" className="shrink-0 text-[10px]">
                              {row.source}
                            </Badge>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            );
          }}
        </ResultGate>
      </div>
    </section>
  );
}

export default SessionsList;
