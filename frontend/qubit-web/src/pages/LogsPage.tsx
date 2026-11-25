import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback, useContext,
  useRef,
} from "react";
import { ProfileContext } from "@/contexts/profile-context";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  RefreshCw,
  X,
} from "lucide-react";
import { useSearchParams } from "react-router";
// `fetchJSON` is the exported request seam from `@/lib/api`. The Logs page
// needs the additive `offset` page param, which `api.getLogs` does not yet
// thread through (lib/api.ts is owned by another agent) — calling the same
// exported function keeps one auth/prefix path instead of a hand-rolled fetch.
import { fetchJSON, type LogsResponse } from "@/lib/api";

// The vendored status router's additive page fields (see
// `qubit_cli/web_routers/status.py` get_logs). Declared here because
// `LogsResponse` in lib/api.ts (owned by another agent) only carries
// `{file, lines}`; the base shape is unchanged and fully backwards-compatible.
type LogsPageResponse = LogsResponse & {
  snapshot?: string | null;
  offset?: number;
  limit?: number;
  returned?: number;
  has_more?: boolean;
  next_offset?: number | null;
};
import { Badge } from "@nous-research/ui/ui/components/badge";
import { FilterGroup } from "@nous-research/ui/ui/components/segmented";
import { SakugaSegmented } from "@/components/sakuga-segmented";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Label } from "@nous-research/ui/ui/components/label";
import { Button, Input, ListItem, Toggle } from "@/sakuga/ui";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
// Level classification is unit-tested in @/lib/log-classify; it prefers the
// structured level token and falls back to word-boundary matching so payload
// text like "parse_errors=0" can't render an INFO line red.
import { classifyLine } from "@/lib/log-classify";
import { rebrand } from "@/lib/rebrand";
import "./LogsPage.css";

const FILES = ["agent", "errors", "gateway"] as const;
const LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"] as const;
const COMPONENTS = ["all", "gateway", "agent", "tools", "cli", "cron"] as const;
const LINE_COUNTS = [50, 100, 200, 500] as const;

const LINE_COLORS: Record<string, string> = {
  error: "text-destructive",
  warning: "text-warning",
  info: "text-foreground",
  debug: "text-text-tertiary",
};

// Timestamp prefix as emitted by qubit_logging; the detail view shows it
// separately from the message body. No timestamp => null (traceback lines).
const LINE_TS_RE = /^(\d{4}-\d{2}-\d{2}[ T][\d:,.]{8,})/;

function lineTimestamp(line: string): string | null {
  return LINE_TS_RE.exec(line)?.[1] ?? null;
}

const formatFilterLabel = (value: string) => value.toUpperCase();

const toSegmentOptions = <T extends string>(values: readonly T[]) =>
  values.map((v) => ({ value: v, label: formatFilterLabel(v) }));

const filterGroupClass =
  "flex min-w-0 w-full flex-col items-start gap-1.5 sm:w-auto sm:max-w-full sm:flex-row sm:items-center";

const segmentedClass =
  "w-fit max-w-full flex-wrap justify-start self-start";

type LogFile = (typeof FILES)[number];

function isLogFile(value: string | null): value is LogFile {
  return (FILES as readonly string[]).includes(value ?? "");
}

export default function LogsPage() {
  const { profile } = useContext(ProfileContext);
  // `?file=gateway` deep link (System page "Open logs" next to a failed
  // gateway). Ported from the reference LogsPage.tsx:9,57-62.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedFile = searchParams.get("file");
  const file = isLogFile(requestedFile) ? requestedFile : "agent";
  const selectFile = (next: LogFile) => {
    const params = new URLSearchParams(searchParams);
    if (next === "agent") params.delete("file");
    else params.set("file", next);
    setSearchParams(params, { replace: true });
  };
  // Default to ALL (Hermes parity: the reference LogsPage defaults to ALL).
  // A narrower default silently drops a level class AND breaks paging — the
  // backend over-reads only `offset+cap+1` raw lines before filtering, so an
  // INFO default returned ~29 lines with `has_more=false` and "Load older"
  // greyed out, i.e. the retained log was unreachable. ALL renders the full
  // tail and pages correctly; a level is one click away. The old INFO default
  // was justified by a DEBUG flood that the row rendering now absorbs the same
  // way Hermes does.
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("ALL");
  const [component, setComponent] =
    useState<(typeof COMPONENTS)[number]>("all");
  const [lineCount, setLineCount] = useState<(typeof LINE_COUNTS)[number]>(100);
  // Live tail by default: a logs page that needs a manual refresh to show new
  // lines is the wrong surface for an operator watching a running gateway.
  // Owner directive (2026-10-04): "shouldn't logs show live and not need to
  // refresh to see new logs?" — upstream Hermes ships this OFF, but the
  // checkbox remains so the 5s poll can be stopped (e.g. while reading a
  // specific line that keeps scrolling away).
  const [autoRefresh, setAutoRefresh] = useState(true);
  // Free-text filter, server-side post-filtered by the vendored status router
  // (`?search=`). Debounced so typing does not spam the tail reader.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState<{ query: string; data?: LogsPageResponse; error?: string }>();
  const [pagination, setPagination] = useState<{ filters: string; offset: number; snapshot?: string }>();
  const requestRef = useRef<AbortController | null>(null);
  const filters = new URLSearchParams({
    file,
    lines: String(lineCount),
    ...(profile ? { profile } : {}),
    ...(level !== "ALL" ? { level } : {}),
    ...(component !== "all" ? { component } : {}),
    ...(search ? { search } : {}),
  }).toString();
  const history = pagination?.filters === filters ? pagination : undefined;
  const offset = history?.offset ?? 0;
  const query = offset > 0
    ? `${filters}&offset=${offset}${history?.snapshot ? `&snapshot=${encodeURIComponent(history.snapshot)}` : ""}`
    : filters;
  const activePage = page?.query === query ? page : undefined;
  const lines = activePage?.data?.lines ?? [];
  const error = activePage?.error;
  const nextOffset = activePage?.data?.has_more ? activePage.data.next_offset : null;
  const setOffset = (next: number) => {
    if (next === 0) setPagination(undefined);
    else setPagination({
      filters, offset: next, snapshot: activePage?.data?.snapshot ?? history?.snapshot,
    });
  };
  // Row detail view: the index of the expanded line (null = collapsed).
  const [detailIndex, setDetailIndex] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();

  useEffect(() => {
    setPagination((current) => current?.filters === filters ? current : undefined);
  }, [filters]);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const fetchLogs = useCallback(() => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    fetchJSON<LogsPageResponse>(`/api/logs?${query}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setPage({ query, data });
        const el = scrollRef.current;
        if (offset === 0 && el && el.scrollHeight - el.scrollTop - el.clientHeight < 48) {
          requestAnimationFrame(() => {
            if (!controller.signal.aborted) el.scrollTop = el.scrollHeight;
          });
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setPage({ query, error: String(err) });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          requestRef.current = null;
          setLoading(false);
        }
      });
  }, [query, offset]);

  // Live tail: while auto-refresh is on, re-fetch every 5s (same cadence as
  // Hermes). Skipped while a line is expanded and while the tab is hidden, so
  // an open detail view is never closed under the operator and a backgrounded
  // tab is not polled.
  useEffect(() => {
    if (!autoRefresh || offset !== 0) return;
    const tick = () => {
      if (document.visibilityState === "hidden") return;
      if (detailIndex !== null || requestRef.current) return;
      fetchLogs();
    };
    const interval = setInterval(tick, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchLogs, detailIndex, offset]);

  // Download the exact lines currently shown (the server tail + filters are the
  // source of truth; no second backend fetch is invented).
  const exportLogs = useCallback(() => {
    const contents = lines.map((line, index) =>
      index < lines.length - 1 && !line.endsWith("\n") ? line + "\n" : line,
    );
    const blob = new Blob(contents, {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${file}${search ? `-${search.replace(/[^a-z0-9._-]+/gi, "_")}` : ""}.log`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [lines, file, search]);

  useLayoutEffect(() => {
    setAfterTitle(
      <span className="qm-logs-header-actions flex items-center gap-1.5">
        <Badge tone="secondary" className="qm-header-chip qm-logs-meta text-xs">
          {formatFilterLabel(file)} · {formatFilterLabel(level)} ·{" "}
          {formatFilterLabel(component)}
        </Badge>
        <Button
          type="button"
          variant="secondary"
          className="qm-logs-header-btn text-muted-foreground hover:text-foreground"
          onClick={exportLogs}
          disabled={lines.length === 0}
          aria-label="Export log lines"
          title={
            lines.length === 0
              ? "Nothing to export — no log lines are loaded"
              : "Download the lines currently shown"
          }
        >
          <Download />
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={fetchLogs}
          disabled={loading}
          aria-label={t.common.refresh}
          className="qm-logs-header-btn text-muted-foreground hover:text-foreground"
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </span>,
    );
    setEnd(
      <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2 sm:justify-end sm:gap-3">
        <div className="flex items-center gap-2">
          <Label htmlFor="logs-auto-refresh" className="text-xs cursor-pointer">
            {t.logs.autoRefresh}
          </Label>
          <Toggle
            label={t.logs.autoRefresh}
            checked={autoRefresh}
            onChange={setAutoRefresh}
            id="logs-auto-refresh"
          />
          {autoRefresh && offset === 0 && (
            <Badge tone="success" className="text-xs">
              <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
              {t.common.live}
            </Badge>
          )}
        </div>
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [
    autoRefresh,
    component,
    file,
    level,
    loading,
    offset,
    setAfterTitle,
    setEnd,
    t.common.live,
    t.common.refresh,
    t.logs.autoRefresh,
    fetchLogs,
    exportLogs,
    lines.length,
  ]);

  useEffect(() => {
    setDetailIndex(null);
    fetchLogs();
    return () => requestRef.current?.abort();
  }, [fetchLogs]);

  // NOTE: the live-tail interval lives with `fetchLogs` above (it skips while a
  // row is expanded or the tab is hidden). Do not add a second one here — two
  // 5s polls would double-fetch and race each other's state writes.

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PluginSlot name="logs:top" />
      <div
        role="toolbar"
        aria-label={t.logs.title}
        className="flex min-w-0 max-w-full flex-col items-start gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:gap-x-6 sm:gap-y-3"
      >
        <FilterGroup label={t.logs.file} className={filterGroupClass}>
          <SakugaSegmented
            className={segmentedClass}
            value={file}
            onChange={selectFile}
            options={toSegmentOptions(FILES)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.level} className={filterGroupClass}>
          <SakugaSegmented
            className={segmentedClass}
            value={level}
            onChange={setLevel}
            options={toSegmentOptions(LEVELS)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.component} className={filterGroupClass}>
          <SakugaSegmented
            className={segmentedClass}
            value={component}
            onChange={setComponent}
            options={toSegmentOptions(COMPONENTS)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.lines} className={filterGroupClass}>
          <SakugaSegmented
            className={segmentedClass}
            value={String(lineCount)}
            onChange={(v) =>
              setLineCount(Number(v) as (typeof LINE_COUNTS)[number])
            }
            options={LINE_COUNTS.map((n) => ({
              value: String(n),
              label: String(n),
            }))}
          />
        </FilterGroup>

        <FilterGroup label="Search" className={filterGroupClass}>
          <div className="relative flex w-full min-w-0 items-center sm:w-64">
            <Input
              label="Search log lines"
              value={searchInput}
              onChange={(value) => setSearchInput(value)}
              placeholder="Filter lines…"
            />
            {searchInput && (
              <Button
                type="button"
                variant="secondary"
                aria-label="Clear search"
                onClick={() => setSearchInput("")}
                className="absolute right-0 h-8 w-8 text-text-tertiary hover:text-foreground"
              >
                <X />
              </Button>
            )}
          </div>
        </FilterGroup>
      </div>

      <Card className="min-w-0 max-w-full overflow-hidden">
        <CardHeader className="py-3 px-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="h-4 w-4" />
              {file}.log
              {offset > 0 && (
                <span className="text-xs font-normal text-text-tertiary">
                  (older page · offset {offset})
                </span>
              )}
            </CardTitle>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="secondary"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - lineCount))}
                aria-label="Load newer lines"
                title="Newer lines"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Newer
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={nextOffset == null || loading}
                onClick={() => {
                  if (nextOffset != null) setOffset(nextOffset);
                }}
                aria-label="Load older lines"
                title="Older lines"
              >
                Older
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {error && (
            <div className="bg-destructive/10 border-b border-destructive/20 p-3">
              <p className="text-sm text-destructive">
                {error.includes("LOG_SNAPSHOT_EXPIRED") ? "The log changed while you were paging." : error}
              </p>
              {error.includes("LOG_SNAPSHOT_EXPIRED") && (
                <Button variant="secondary" onClick={() => setOffset(0)}>Load latest logs</Button>
              )}
            </div>
          )}

          <div
            ref={scrollRef}
            className="max-w-full min-h-[400px] max-h-[calc(100vh-220px)] overflow-auto p-4 font-mono-ui text-xs leading-5 break-words"
          >
            {lines.length === 0 && !loading && !error && (
              <p className="text-muted-foreground text-center py-8">
                {t.logs.noLogLines}
              </p>
            )}
            {lines.map((line, i) => {
              const cls = classifyLine(line);
              const ts = lineTimestamp(line);
              const expanded = detailIndex === i;
              return (
                <div key={i}>
                  <ListItem
                    onClick={() => setDetailIndex(expanded ? null : i)}
                    aria-expanded={expanded}
                    aria-label={`Log line ${offset + i + 1} detail`}
                    className={`qsk-log-line ${LINE_COLORS[cls]} block w-full cursor-pointer text-left hover:bg-secondary/20`}
                  >
                    {rebrand(line)}
                  </ListItem>
                  {expanded && (
                    <div className="my-1 border-l-2 border-secondary bg-secondary/20 px-3 py-2">
                      <div className="mb-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] uppercase text-text-tertiary">
                        <span>Line {offset + i + 1}</span>
                        <span>{cls}</span>
                        {ts && <span>{ts}</span>}
                        <span>{line.length} chars</span>
                      </div>
                      <pre className="whitespace-pre-wrap break-words text-foreground">
                        {rebrand(line)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
      <PluginSlot name="logs:bottom" />
    </div>
  );
}
