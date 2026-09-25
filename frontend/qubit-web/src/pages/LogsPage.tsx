import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  useRef,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  RefreshCw,
  Search,
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
  offset?: number;
  limit?: number;
  returned?: number;
  has_more?: boolean;
  next_offset?: number | null;
};
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { FilterGroup, Segmented } from "@nous-research/ui/ui/components/segmented";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Label } from "@nous-research/ui/ui/components/label";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
// Level classification is unit-tested in @/lib/log-classify; it prefers the
// structured level token and falls back to word-boundary matching so payload
// text like "parse_errors=0" can't render an INFO line red.
import { classifyLine } from "@/lib/log-classify";
import { rebrand } from "@/lib/rebrand";

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
  // `?file=gateway` deep link (System page "Open logs" next to a failed
  // gateway). Ported from the reference LogsPage.tsx:9,57-62.
  const [searchParams] = useSearchParams();
  const requestedFile = searchParams.get("file");
  const [file, setFile] = useState<LogFile>(() =>
    isLogFile(requestedFile) ? requestedFile : "agent",
  );
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("ALL");
  const [component, setComponent] =
    useState<(typeof COMPONENTS)[number]>("all");
  const [lineCount, setLineCount] = useState<(typeof LINE_COUNTS)[number]>(100);
  const [autoRefresh, setAutoRefresh] = useState(false);
  // Free-text filter, server-side post-filtered by the vendored status router
  // (`?search=`). Debounced so typing does not spam the tail reader.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Additive page contract (vendored status router `?offset=`): `offset` counts
  // matching lines skipped from the newest end; `hasMore`/`nextOffset` come from
  // the server so "Load older" can never invent a page that does not exist.
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  // Row detail view: the index of the expanded line (null = collapsed).
  const [detailIndex, setDetailIndex] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // A filter/file/page-size change starts a fresh newest-first page.
  useEffect(() => {
    setOffset(0);
  }, [file, lineCount, level, component, search]);

  const fetchLogs = useCallback(() => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams();
    qs.set("file", file);
    qs.set("lines", String(lineCount));
    if (level && level !== "ALL") qs.set("level", level);
    if (component && component !== "all") qs.set("component", component);
    if (search.trim()) qs.set("search", search.trim());
    if (offset > 0) qs.set("offset", String(offset));
    fetchJSON<LogsPageResponse>(`/api/logs?${qs.toString()}`)
      .then((resp) => {
        setLines(resp.lines);
        setHasMore(Boolean(resp.has_more));
        setNextOffset(
          typeof resp.next_offset === "number" ? resp.next_offset : null,
        );
        setDetailIndex(null);
        // Only follow the tail on the newest page; an older page must not yank
        // the viewport to the bottom.
        if (offset === 0) {
          setTimeout(() => {
            if (scrollRef.current) {
              scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
          }, 50);
        }
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [file, lineCount, level, component, search, offset]);

  // Download the exact lines currently shown (the server tail + filters are the
  // source of truth; no second backend fetch is invented).
  const exportLogs = useCallback(() => {
    const blob = new Blob([lines.join("\n")], {
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
      <span className="flex items-center gap-1.5">
        <Badge tone="secondary" className="text-xs">
          {formatFilterLabel(file)} · {formatFilterLabel(level)} ·{" "}
          {formatFilterLabel(component)}
        </Badge>
        <Button
          type="button"
          ghost
          size="icon"
          className="text-muted-foreground hover:text-foreground"
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
          ghost
          size="icon"
          className="text-muted-foreground hover:text-foreground"
          onClick={fetchLogs}
          disabled={loading}
          aria-label={t.common.refresh}
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
          <Switch
            checked={autoRefresh}
            onCheckedChange={setAutoRefresh}
            id="logs-auto-refresh"
          />
          {autoRefresh && (
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
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchLogs]);

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PluginSlot name="logs:top" />
      <div
        role="toolbar"
        aria-label={t.logs.title}
        className="flex min-w-0 max-w-full flex-col items-start gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:gap-x-6 sm:gap-y-3"
      >
        <FilterGroup label={t.logs.file} className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={file}
            onChange={setFile}
            options={toSegmentOptions(FILES)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.level} className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={level}
            onChange={setLevel}
            options={toSegmentOptions(LEVELS)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.component} className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={component}
            onChange={setComponent}
            options={toSegmentOptions(COMPONENTS)}
          />
        </FilterGroup>

        <FilterGroup label={t.logs.lines} className={filterGroupClass}>
          <Segmented
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
            <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-text-tertiary" />
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Filter lines…"
              aria-label="Search log lines"
              className="h-9 min-w-0 pl-7 pr-7 font-mono text-xs"
            />
            {searchInput && (
              <Button
                type="button"
                ghost
                size="icon"
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
                ghost
                size="sm"
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
                ghost
                size="sm"
                disabled={!hasMore || loading}
                onClick={() => {
                  if (nextOffset !== null) setOffset(nextOffset);
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
              <p className="text-sm text-destructive">{error}</p>
            </div>
          )}

          <div
            ref={scrollRef}
            className="max-w-full min-h-[400px] max-h-[calc(100vh-220px)] overflow-auto p-4 font-mono-ui text-xs leading-5 break-words"
          >
            {lines.length === 0 && !loading && (
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
                  <button
                    type="button"
                    onClick={() => setDetailIndex(expanded ? null : i)}
                    aria-expanded={expanded}
                    aria-label={`Log line ${offset + i + 1} detail`}
                    className={`${LINE_COLORS[cls]} block w-full cursor-pointer text-left hover:bg-secondary/20 px-1 -mx-1`}
                  >
                    {rebrand(line)}
                  </button>
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
