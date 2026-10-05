/**
 * Open Viking retrieval (`/open-viking/retrieval`).
 *
 * Qubit-owned find/search console over the local context engine's
 * retrieval API via `@/lib/ocFamilyVikingApi` (`retrieval_search`).
 * A missing bridge row renders honestly disabled with reason; results,
 * sources, and empty/loading/error states are all real.
 *
 * Structure follows the shared settings contract
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`)
 * and the uniform Open Viking shell (`SettingsPageIntro` with a parent
 * breadcrumb + sibling view switcher). The layout owns the page title
 * (see ROUTE_TITLES) — no `h1` here.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Database, FileText, FolderUp, Search, Wrench } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  SettingsEmpty,
  SettingsLoadingSkeleton,
  SettingsPageIntro,
  SettingsRow,
  SettingsSection,
  SettingsStatus,
} from "@/components/viking-settings";
import {
  getVikingStatus,
  isBridgeRowMissing,
  normalizeVikingStatus,
  normalizeSearchHits,
  searchVikingRetrieval,
  type VikingSearchHit,
} from "@/lib/ocFamilyVikingApi";

type SearchState = "idle" | "loading" | "done" | "unavailable" | "error";

/** The context groups a retrieval query fans out across. Each is a real
 *  engine section; the list is shown so the operator knows what is searched. */
const SEARCH_SCOPES = [
  { key: "memories", label: "User memories", icon: Database },
  { key: "bot", label: "Bot notes", icon: Bot },
  { key: "skills", label: "Skills", icon: Wrench },
  { key: "resources", label: "Uploaded resources", icon: FolderUp },
] as const;

function sourceOf(hit: VikingSearchHit): string {
  const path = hit.path ?? "";
  if (path.includes("/")) return path.split("/")[0];
  return path || hit.id || "context";
}

export default function OpenVikingRetrievalPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [hits, setHits] = useState<VikingSearchHit[]>([]);
  const [state, setState] = useState<SearchState>("idle");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<"connected" | "unknown" | "checking" | "unavailable">(
    "checking",
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await getVikingStatus();
        if (cancelled) return;
        const status = normalizeVikingStatus(payload);
        setConnection(status.connection === "connected" ? "connected" : "unknown");
      } catch (e) {
        if (cancelled) return;
        setConnection(isBridgeRowMissing(e) ? "unavailable" : "unknown");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setState("loading");
    setError("");
    setSubmitted(q);
    try {
      const payload = await searchVikingRetrieval(q, 25);
      setHits(normalizeSearchHits(payload));
      setState("done");
    } catch (e) {
      if (isBridgeRowMissing(e)) {
        setHits([]);
        setState("unavailable");
      } else {
        setState("error");
        setError(e instanceof Error ? e.message : "search failed");
      }
    }
  }, [query]);

  // Source breakdown for the result set (real data only — no invented rows).
  const sources = useMemo(() => {
    const counts = new Map<string, number>();
    for (const hit of hits) {
      const key = sourceOf(hit);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [hits]);

  return (
    <div className="qb-upstream viking-settings settings-page settings-page--wide" data-testid="open-viking-retrieval-page">
      <SettingsPageIntro currentPath="/open-viking/retrieval" showSubnav>
        Find related context across the local engine — user memory, bot notes,
        skills, and uploaded resources. One-shot keyword search; nothing leaves
        this machine.
      </SettingsPageIntro>

      <SettingsSection
        title="Retrieval query"
        description="Enter keywords to search the context store; results include their source file."
        ariaLabel="Retrieval query"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onSearch();
          }}
        >
          <SettingsRow
            stacked
            title="Query"
            description="Matches file paths and contents in the context store."
            control={
              <input
                id="viking-retrieval-q"
                className="settings-input font-courier"
                placeholder="Find related context…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search the context store"
              />
            }
          />
          <SettingsRow
            className="settings-row--actions"
            control={
              <Button
                type="submit"
                size="sm"
                className="uppercase"
                prefix={<Search className="h-4 w-4" />}
              >
                Find
              </Button>
            }
          />
        </form>
      </SettingsSection>

      <SettingsSection
        title="Search scopes"
        description="The engine sections a query fans out across."
        count={SEARCH_SCOPES.length}
        ariaLabel="Search scopes"
      >
        {SEARCH_SCOPES.map((scope) => (
          <SettingsRow
            key={scope.key}
            title={
              <span className="flex items-center gap-2">
                <scope.icon className="h-4 w-4" aria-hidden /> {scope.label}
              </span>
            }
            description={
              state === "done"
                ? "Searched in this run."
                : "Searched when a query runs."
            }
            control={
              state === "done" ? (
                <span className="settings-row__value settings-row__value--mono">
                  {sources.find(([s]) => s === scope.key)?.[1] ?? 0}
                </span>
              ) : undefined
            }
            testId={`viking-scope-${scope.key}`}
          />
        ))}
      </SettingsSection>

      <SettingsSection
        title="Results"
        description={
          state === "done"
            ? `Search results for “${submitted}”.`
            : "Run a query to list matching context entries."
        }
        count={state === "done" ? hits.length : undefined}
        ariaLabel="Retrieval results"
      >
        {state === "idle" && (
          <SettingsEmpty>No query yet — enter keywords above to search.</SettingsEmpty>
        )}

        {state === "loading" && <SettingsLoadingSkeleton rows={3} />}

        {state === "unavailable" && (
          <SettingsRow
            title="Retrieval unavailable"
            description="Retrieval-search bridge row missing — the local engine isn't serving this operation yet."
            testId="viking-retrieval-unavailable"
          />
        )}

        {state === "error" && (
          <SettingsRow
            title="Search failed"
            description={error || "The context store couldn't be queried."}
            className="settings-row--stacked"
          />
        )}

        {state === "done" && hits.length === 0 && (
          <SettingsEmpty>No matches for “{submitted}”. Try a narrower term.</SettingsEmpty>
        )}

        {hits.length > 0 && (
          <div data-testid="viking-retrieval-hits">
            {hits.map((hit) => (
              <SettingsRow
                key={`${hit.id}-${hit.title}`}
                title={hit.title || hit.id}
                description={
                  <span className="flex flex-wrap items-center gap-2">
                    {hit.snippet && <span className="min-w-0">{hit.snippet}</span>}
                    {hit.path && (
                      <span className="settings-row__value settings-row__value--mono">{hit.path}</span>
                    )}
                  </span>
                }
                control={
                  typeof hit.score === "number" ? (
                    <Badge tone="secondary" className="text-xs tabular-nums">
                      {hit.score.toFixed(2)}
                    </Badge>
                  ) : undefined
                }
              />
            ))}
          </div>
        )}
      </SettingsSection>

      <SettingsSection
        title="Sources"
        description="Context groups that contributed to the current result set."
        count={state === "done" ? sources.length : undefined}
        ariaLabel="Result sources"
      >
        {sources.length === 0 ? (
          <SettingsEmpty>Sources appear here once a query returns matches.</SettingsEmpty>
        ) : (
          sources.map(([source, count]) => (
            <SettingsRow
              key={source}
              title={
                <span className="flex items-center gap-2">
                  <FileText className="h-4 w-4" aria-hidden /> {source}
                </span>
              }
              control={
                <span className="settings-row__value settings-row__value--mono">{count}</span>
              }
            />
          ))
        )}
      </SettingsSection>

      <SettingsSection
        title="Engine"
        description="Live status of the local context engine backing this search."
        ariaLabel="Engine status"
      >
        {connection === "checking" ? (
          <SettingsEmpty>Checking the local engine…</SettingsEmpty>
        ) : (
          <SettingsRow
            title="Local context engine"
            description={
              connection === "connected"
                ? "Connected — retrieval runs against local state only."
                : connection === "unavailable"
                  ? "Status bridge row missing — the engine isn't reachable from this dashboard yet."
                  : "Reachable but reported an unknown state."
            }
            control={
              <SettingsStatus
                tone={
                  connection === "connected"
                    ? "ok"
                    : connection === "unavailable"
                      ? "danger"
                      : "warn"
                }
              >
                {connection === "connected"
                  ? "connected"
                  : connection === "unavailable"
                    ? "unavailable"
                    : "unknown"}
              </SettingsStatus>
            }
          />
        )}
      </SettingsSection>
    </div>
  );
}
