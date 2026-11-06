/**
 * SessionsList — the workbench's conversation sidebar (second column).
 *
 * Structure ported from the absorbed console's chat panel
 * (`static/index.html:196-206` header + search; `sessions.js:10820-11470` the
 * list): a `CHAT` header with a `+`, a filter field, a two-way session-source
 * switch, a chip row, then PINNED and calendar-grouped sections.
 *
 * What is real here and what is not:
 *
 * - **Rows** come from `GET /api/workbench/sessions`. Nothing is invented.
 * - **The source switch** (`WebUI` / `CLI`) partitions on the store's own
 *   `source` column, which on this backend holds `tui`, `cron`, `voice`, … —
 *   so both sides are computed from the rows rather than assumed.
 * - **Chips** filter by that same recorded source. There is no project column
 *   on this seam, so there is no project chip: a chip filtering on a field the
 *   backend does not carry would be pure decoration.
 * - **Pinning** is this page's own preference (the seam has no pinned column);
 *   the button's tooltip says so instead of implying a stored pin.
 * - **Date groups** come from each row's `last_active` via `./session-groups`,
 *   never from a server-supplied string.
 */

import { ChevronDown, Pin, PinOff, Plus, RefreshCw, Search, X } from "lucide-react";

import type { ApiResult, WorkbenchSessionRow, WorkbenchSessionsPage } from "@/lib/workbench-api";

import { PANEL_COPY } from "./panel-copy";
import {
  compactAge,
  groupSessions,
  sessionLabel,
  type SessionGroup,
  type SessionGroupKey,
} from "./session-groups";
import { HeadIconButton, ResultGate } from "./WorkbenchStates";
import {
  PANEL_HEAD_LABEL,
  REGION,
  SOURCE_BADGE,
  SOURCE_TONE,
  SOURCE_TONE_FALLBACK,
} from "./workbench-theme";

/** Two-way source switch, the way the absorbed console partitioned its list. */
export type SourceFilter = "webui" | "cli";

/**
 * Which store `source` values belong to the CLI side of the switch. These are
 * the provenance labels the chat store writes for turns that did not come from
 * the browser surface; anything else (or nothing) is the WebUI side.
 */
const CLI_SOURCES = new Set(["tui", "cli", "cron", "voice"]);

function isCliSource(source: string | null): boolean {
  return source !== null && CLI_SOURCES.has(source.toLowerCase());
}

/**
 * The chip row: `All` plus the ACTUAL distinct `source` values on the loaded
 * page, in a stable order — so the row can never offer a filter that matches
 * nothing, and never hides a source that exists.
 */
function chips(rows: readonly WorkbenchSessionRow[]): { id: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const row of rows) {
    if (!row.source) continue;
    const key = row.source.toLowerCase();
    if (!seen.has(key)) seen.set(key, row.source);
  }
  const preferred = ["tui", "cron", "voice"];
  const sorted = [...seen.entries()].sort((a, b) => {
    const ai = preferred.indexOf(a[0]);
    const bi = preferred.indexOf(b[0]);
    if (ai !== bi) return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    return a[1].localeCompare(b[1]);
  });
  return [{ id: "all", label: "All" }, ...sorted.map(([id, label]) => ({ id, label }))];
}

function SourceBadge({ source }: { source: string }) {
  const tone = SOURCE_TONE[source.toLowerCase()] ?? SOURCE_TONE_FALLBACK;
  return (
    <span className={`${SOURCE_BADGE} ${tone}`} title={`Recorded source: ${source}`}>
      {source}
    </span>
  );
}

function SessionRow({
  row,
  selected,
  pinned,
  now,
  onSelect,
  onTogglePin,
}: {
  row: WorkbenchSessionRow;
  selected: boolean;
  pinned: boolean;
  now: number;
  onSelect: (id: string) => void;
  onTogglePin: (id: string) => void;
}) {
  const age = compactAge(row.last_active, now);
  return (
    <li className="group relative min-w-0">
      <button
        type="button"
        onClick={() => onSelect(row.id)}
        aria-current={selected ? "page" : undefined}
        data-testid={`workbench-session-${row.id}`}
        className={`flex w-full min-w-0 items-start gap-2 rounded-lg px-2 py-[7px] text-left transition-colors ${
          selected
            ? "bg-primary/[0.12] text-primary"
            : "text-text-secondary hover:bg-secondary/40 hover:text-foreground"
        }`}
      >
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-[13px] leading-snug">
              {sessionLabel(row)}
            </span>
            {row.source ? <SourceBadge source={row.source} /> : null}
            {age ? (
              <span className="shrink-0 text-[10px] tabular-nums text-text-tertiary group-hover:hidden">
                {age}
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[10px] text-text-tertiary opacity-80">
            {row.message_count !== null ? <span>{row.message_count} msg</span> : null}
            {row.tool_call_count !== null && row.tool_call_count > 0 ? (
              <span>{row.tool_call_count} tools</span>
            ) : null}
            {row.model ? (
              <span className="max-w-[9rem] truncate font-mono-ui">
                {row.model.split("/").pop()}
              </span>
            ) : null}
          </span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => onTogglePin(row.id)}
        aria-label={pinned ? `Unpin ${sessionLabel(row)}` : `Pin ${sessionLabel(row)}`}
        title={
          pinned
            ? "Unpin — pins are kept by this page, not by the session store"
            : "Pin to the top — pins are kept by this page, not by the session store"
        }
        data-testid={`workbench-pin-${row.id}`}
        className="absolute right-1 top-1.5 hidden h-6 w-6 items-center justify-center rounded-md text-text-tertiary hover:bg-secondary/60 hover:text-foreground group-hover:flex [&>svg]:h-3 [&>svg]:w-3"
      >
        {pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}
      </button>
    </li>
  );
}

function GroupHeader({
  group,
  collapsed,
  onToggle,
}: {
  group: SessionGroup;
  collapsed: boolean;
  onToggle: (key: SessionGroupKey) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onToggle(group.key)}
      aria-expanded={!collapsed}
      data-testid={`workbench-group-${group.key}`}
      className={`flex w-full items-center gap-1.5 px-2.5 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.08em] transition-opacity ${
        group.pinned ? "text-primary" : "text-text-tertiary opacity-80 hover:opacity-100"
      }`}
    >
      <ChevronDown
        aria-hidden="true"
        className={`h-3 w-3 shrink-0 transition-transform ${collapsed ? "-rotate-90" : ""}`}
      />
      <span>{group.label}</span>
      <span className="ml-auto font-normal tabular-nums opacity-70">{group.rows.length}</span>
    </button>
  );
}

export function SessionsList({
  result,
  selectedId,
  query,
  onQueryChange,
  sourceFilter,
  onSourceFilter,
  activeSource,
  onActiveSource,
  pinned,
  collapsed,
  onToggleGroup,
  onSelect,
  onTogglePin,
  onNewSession,
  onReload,
}: {
  result: ApiResult<WorkbenchSessionsPage> | null;
  selectedId: string | null;
  query: string;
  onQueryChange: (next: string) => void;
  sourceFilter: SourceFilter;
  onSourceFilter: (next: SourceFilter) => void;
  /** Chip id the list is filtered to: `all`, or a lower-cased source. */
  activeSource: string;
  onActiveSource: (next: string) => void;
  pinned: ReadonlySet<string>;
  collapsed: ReadonlySet<SessionGroupKey>;
  onToggleGroup: (key: SessionGroupKey) => void;
  onSelect: (id: string) => void;
  onTogglePin: (id: string) => void;
  onNewSession: () => void;
  onReload: () => void;
}) {
  const now = Date.now();
  const data =
    result !== null && (result.state === "ok" || result.state === "empty") ? result.data : null;
  const rows = data?.rows ?? [];
  const chipList = chips(rows);

  // Both sides of the switch are counted from the store's own `source` column,
  // over the loaded page. The footer states the page's share of the store so a
  // capped page never reads as the whole history.
  const webuiCount = rows.filter((row) => !isCliSource(row.source)).length;
  const cliCount = rows.filter((row) => isCliSource(row.source)).length;

  const needle = query.trim().toLowerCase();
  const visible = rows
    .filter((row) => (sourceFilter === "cli" ? isCliSource(row.source) : !isCliSource(row.source)))
    .filter((row) => activeSource === "all" || (row.source ?? "").toLowerCase() === activeSource)
    .filter((row) => {
      if (!needle) return true;
      return `${row.title ?? ""} ${row.preview ?? ""} ${row.id}`.toLowerCase().includes(needle);
    });

  const groups = groupSessions(visible, pinned, now);

  return (
    <section
      aria-label="Conversations"
      data-testid="workbench-sessions"
      className={`${REGION.sidebar} min-h-0`}
    >
      <div className={REGION.panelHead}>
        <h2 className={PANEL_HEAD_LABEL}>Chat</h2>
        <div className="flex shrink-0 items-center gap-0.5">
          <HeadIconButton
            label="New conversation — opens the CLI composer on /qubit-chat, which owns session creation"
            onClick={onNewSession}
            testId="workbench-new-session"
          >
            <Plus aria-hidden="true" />
          </HeadIconButton>
          <HeadIconButton label="Reload sessions" onClick={onReload} testId="workbench-reload-sessions">
            <RefreshCw aria-hidden="true" />
          </HeadIconButton>
        </div>
      </div>

      <div className="shrink-0 px-3 pt-2">
        <label className="relative flex items-center">
          <Search
            className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-text-tertiary opacity-70"
            aria-hidden="true"
          />
          <span className="sr-only">Filter conversations</span>
          <input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            placeholder="Filter conversations…"
            autoComplete="off"
            spellCheck={false}
            className="h-8 w-full rounded-lg border border-border bg-background-base pl-8 pr-7 text-[13px] text-foreground outline-none transition-colors placeholder:text-text-tertiary/70 focus:border-primary focus:ring-2 focus:ring-primary/15"
          />
          {query ? (
            <button
              type="button"
              onClick={() => onQueryChange("")}
              aria-label="Clear conversation filter"
              title="Clear conversation filter"
              className="absolute right-1.5 flex h-5 w-5 items-center justify-center rounded text-text-tertiary hover:bg-secondary/60 hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ) : null}
        </label>
      </div>

      <div className="flex shrink-0 gap-1 px-3 pt-2" role="group" aria-label="Session source">
        {[
          { id: "webui" as const, label: "WebUI sessions", count: webuiCount },
          { id: "cli" as const, label: "CLI sessions", count: cliCount },
        ].map((side) => (
          <button
            key={side.id}
            type="button"
            onClick={() => onSourceFilter(side.id)}
            aria-pressed={sourceFilter === side.id}
            data-testid={`workbench-source-${side.id}`}
            className={`min-w-0 flex-1 truncate rounded-[10px] border px-1.5 py-1 text-[10px] font-bold leading-tight transition-colors ${
              sourceFilter === side.id
                ? "border-primary/40 bg-primary/15 text-primary"
                : "border-border bg-background-base text-text-tertiary hover:text-foreground"
            }`}
          >
            {side.label} ({side.count})
          </button>
        ))}
      </div>

      {chipList.length > 1 ? (
        <div
          className="flex shrink-0 flex-wrap items-center gap-1 px-3 py-2"
          role="group"
          aria-label="Filter by recorded source"
        >
          {chipList.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => onActiveSource(chip.id)}
              aria-pressed={activeSource === chip.id}
              data-testid={`workbench-chip-${chip.id}`}
              className={`inline-flex items-center gap-1 whitespace-nowrap rounded-xl border px-2 py-[3px] text-[10px] font-semibold transition-colors ${
                activeSource === chip.id
                  ? "border-primary/40 bg-primary/15 text-primary"
                  : "border-border bg-background-base text-text-tertiary hover:text-foreground"
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2" data-testid="workbench-session-list">
        <ResultGate
          result={result}
          copy={PANEL_COPY.sessions}
          loadingText="Reading sessions…"
          onRetry={onReload}
          compact
        >
          {() => {
            if (rows.length === 0) {
              return (
                <p
                  data-testid="workbench-sessions-empty"
                  className="px-2 py-8 text-center text-[12px] text-text-tertiary"
                >
                  No sessions reported by the workbench backend.
                </p>
              );
            }
            if (visible.length === 0) {
              return (
                <p className="px-2 py-8 text-center text-[12px] text-text-tertiary">
                  Nothing matches these filters.
                </p>
              );
            }
            return (
              // `session-groups` decides the buckets; this maps them. Each
              // group is a listitem wrapper so the group header and its rows
              // stay one landmark for a screen reader.
              <div aria-label="Workbench sessions">
                {groups.map((group) => {
                  const isCollapsed = collapsed.has(group.key);
                  return (
                    <div key={group.key}>
                      <GroupHeader group={group} collapsed={isCollapsed} onToggle={onToggleGroup} />
                      {isCollapsed ? null : (
                        <ul className="flex flex-col gap-px">
                          {group.rows.map((row) => (
                            <SessionRow
                              key={row.id}
                              row={row}
                              selected={row.id === selectedId}
                              pinned={pinned.has(row.id)}
                              now={now}
                              onSelect={onSelect}
                              onTogglePin={onTogglePin}
                            />
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          }}
        </ResultGate>
      </div>

      {data ? (
        <div
          className="shrink-0 border-t border-border px-3 py-1.5 text-[10px] text-text-tertiary"
          data-testid="workbench-session-totals"
        >
          {data.has_more || data.total > data.shown
            ? `Showing ${data.shown} of ${data.total} sessions in the store.`
            : `${data.total} session${data.total === 1 ? "" : "s"} in the store.`}
        </div>
      ) : null}
    </section>
  );
}

export default SessionsList;
