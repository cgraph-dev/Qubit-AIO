import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { ChevronRight, ExternalLink, RefreshCw, Search } from "lucide-react";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

// Qubit-owned OpenAPI reference. Vendor provenance lives in
// THIRD_PARTY_NOTICES.md (the legal-attribution surface), never on the rendered
// product surface.
export const UPSTREAM_DOCS_URL = "/agent/openapi.json";
export const UPSTREAM_DOCS_LABEL =
  "Qubit API reference";

const DS_BUTTON_OUTLINED_LINK_CN = cn(
  "group relative inline-grid grid-cols-[auto_1fr_auto] items-center",
  "px-[.9em_.75em] py-[1.25em] gap-2",
  "leading-0 font-bold tracking-[0.2em] uppercase",
  "text-midground bg-transparent shadow-midground",
  "shadow-[inset_-1px_-1px_0_0_#00000080,inset_1px_1px_0_0_#ffffff80]",
);

/** Minimal OpenAPI 3.1 shape — only the fields the reference renders. */
interface OasParameter {
  name?: string;
  in?: string;
  required?: boolean;
  description?: string;
}

interface OasResponse {
  description?: string;
}

interface OasOperation {
  tags?: string[];
  summary?: string;
  description?: string;
  deprecated?: boolean;
  /** Qubit extension: legacy-named compatibility alias (route still resolves,
   *  but it is not a current Qubit-branded path). */
  "x-qubit-compat-alias"?: boolean;
  parameters?: OasParameter[];
  responses?: Record<string, OasResponse>;
}

interface OasDocument {
  openapi?: string;
  info?: { title?: string; version?: string; description?: string };
  paths?: Record<string, Record<string, OasOperation>>;
}

interface OperationEntry {
  id: string;
  method: string;
  path: string;
  summary: string;
  description?: string;
  deprecated: boolean;
  compatAlias: boolean;
  parameters: OasParameter[];
  responses: [string, OasResponse][];
}

const HTTP_METHODS = ["get", "post", "put", "patch", "delete", "options", "head"];
const METHOD_TONES: Record<string, string> = {
  get: "text-emerald-400",
  post: "text-sky-400",
  put: "text-amber-400",
  patch: "text-amber-400",
  delete: "text-destructive",
  options: "text-text-tertiary",
  head: "text-text-tertiary",
};

/** Group an operation path by its most meaningful namespace segment. */
function groupKey(path: string): string {
  const segments = path.split("/").filter(Boolean);
  if (segments[0] === "api") return segments[1] ?? "api";
  return segments[0] ?? "root";
}

function flattenOperations(doc: OasDocument): OperationEntry[] {
  const entries: OperationEntry[] = [];
  for (const [path, item] of Object.entries(doc.paths ?? {})) {
    for (const method of HTTP_METHODS) {
      const op = item[method];
      if (!op) continue;
      entries.push({
        id: `${method}-${path}`,
        method,
        path,
        summary: op.summary ?? `${method.toUpperCase()} ${path}`,
        description: op.description,
        deprecated: op.deprecated === true,
        compatAlias: op["x-qubit-compat-alias"] === true,
        parameters: op.parameters ?? [],
        responses: Object.entries(op.responses ?? {}),
      });
    }
  }
  return entries.sort((a, b) => a.path.localeCompare(b.path));
}

/** Read the OpenAPI document from the canonical Qubit API namespace. */
async function fetchOpenApi(): Promise<OasDocument> {
  const prefix =
    (typeof window !== "undefined" && window.__QUBIT_API_PREFIX__) || "/agent";
  const token =
    typeof window !== "undefined" ? window.__HERMES_SESSION_TOKEN__ : undefined;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["X-Hermes-Session-Token"] = token;
  const res = await fetch(`${prefix}/openapi.json`, {
    credentials: "include",
    headers,
  });
  if (!res.ok) {
    throw new Error(`${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as OasDocument;
}

export default function DocsPage() {
  const { t } = useI18n();
  const { setEnd } = usePageHeader();
  const [doc, setDoc] = useState<OasDocument | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  const load = useCallback(() => {
    setState("loading");
    setError(null);
    fetchOpenApi()
      .then((next) => {
        setDoc(next);
        setState("ready");
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useLayoutEffect(() => {
    setEnd(
      <a
        href={UPSTREAM_DOCS_URL}
        target="_blank"
        rel="noopener noreferrer"
        title={UPSTREAM_DOCS_LABEL}
        aria-label={UPSTREAM_DOCS_LABEL}
        className={DS_BUTTON_OUTLINED_LINK_CN}
      >
        <ExternalLink className="size-3.5" />
        {t.app.openDocumentation}
      </a>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t]);

  // Legacy-named compatibility aliases (routes the served document marks
  // `x-qubit-compat-alias`) must not be presented as current Qubit API
  // surfaces, per BRAND.md: the shipped product shows zero legacy brand. They
  // are retained for wire compatibility, so they stay reachable — but only
  // inside one clearly-labelled, collapsed "Compatibility aliases" group
  // instead of leaking their legacy path/group names into the product surface.
  const COMPAT_ALIAS_GROUP = "compatibility aliases";
  const operations = useMemo(
    () => (doc ? flattenOperations(doc) : []),
    [doc],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return operations;
    return operations.filter(
      (op) =>
        op.path.toLowerCase().includes(query) ||
        op.method.includes(query) ||
        op.summary.toLowerCase().includes(query),
    );
  }, [operations, search]);

  const groups = useMemo(() => {
    const map = new Map<string, OperationEntry[]>();
    for (const op of filtered) {
      const key = op.compatAlias ? COMPAT_ALIAS_GROUP : groupKey(op.path);
      const list = map.get(key);
      if (list) list.push(op);
      else map.set(key, [op]);
    }
    return [...map.entries()].sort((a, b) => {
      // Keep the alias group last: it is de-emphasised reference material.
      if (a[0] === COMPAT_ALIAS_GROUP) return 1;
      if (b[0] === COMPAT_ALIAS_GROUP) return -1;
      return a[0].localeCompare(b[0]);
    });
  }, [filtered]);

  // Alias groups start collapsed so the product surface leads with the current
  // Qubit-branded operations; regular groups default open.
  const isGroupOpen = (group: string) =>
    openGroups[group] ?? group !== COMPAT_ALIAS_GROUP;

  const title = doc?.info?.title ?? "Qubit Agent";
  const version = doc?.info?.version;

  return (
    <div
      className={cn(
        "flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3",
        "pt-1 sm:pt-2",
      )}
    >
      <PluginSlot name="docs:top" />

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium tracking-[0.08em]">
            {title} API — OpenAPI {doc?.openapi ?? "3.1"}
            {version ? ` · v${version}` : ""}
          </h2>
          <p className="text-xs text-text-tertiary">
            OpenAPI reference for the running Qubit Agent backend.{" "}
            <a
              className="underline"
              href={UPSTREAM_DOCS_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              {UPSTREAM_DOCS_LABEL}
            </a>
          </p>
        </div>

        <div className="relative min-w-0 w-full sm:w-64">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-8 pl-8 pr-2 text-xs"
            placeholder="Filter operations"
            aria-label="Filter API operations"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <Button
          ghost
          size="icon"
          onClick={load}
          disabled={state === "loading"}
          aria-label="Refresh API reference"
          title="Refresh API reference"
        >
          {state === "loading" ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>

      {state === "loading" && !doc ? (
        <div
          data-state="loading"
          aria-busy="true"
          className="flex items-center justify-center gap-2 py-24 text-sm text-text-tertiary"
        >
          <Spinner className="shrink-0" />
          <span>Loading API reference…</span>
        </div>
      ) : state === "error" ? (
        <div
          role="alert"
          data-state="error"
          className="flex flex-wrap items-center gap-2 border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          <span className="min-w-0 flex-1 break-words">{error}</span>
          <Button
            size="sm"
            outlined
            onClick={load}
            prefix={<RefreshCw className="h-3.5 w-3.5" />}
            aria-label="Retry loading the API reference"
          >
            Retry
          </Button>
        </div>
      ) : filtered.length === 0 ? (
        <p data-state="empty" className="py-12 text-center text-sm text-text-tertiary">
          No API operations match “{search}”.
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {groups.map(([group, ops]) => {
            const isOpen = isGroupOpen(group);
            return (
              <section key={group} className="mb-3 border border-border/50">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-controls={`docs-group-${group}`}
                  onClick={() =>
                    setOpenGroups((prev) => ({ ...prev, [group]: !isOpen }))
                  }
                  className="flex w-full items-center gap-2 bg-muted/20 px-3 py-2 text-left hover:bg-muted/40"
                >
                  <ChevronRight
                    className={cn(
                      "h-3.5 w-3.5 shrink-0 transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                  <span className="font-mondwest text-display text-xs tracking-[0.12em] text-text-secondary">
                    {group}
                  </span>
                  <span className="text-xs tabular-nums text-text-tertiary">
                    {ops.length}
                  </span>
                </button>

                {isOpen && (
                  <ul
                    id={`docs-group-${group}`}
                    className="list-none divide-y divide-border/40"
                  >
                    {ops.map((op) => (
                      <li key={op.id}>
                        <details className="group">
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5 hover:bg-muted/20">
                            <span
                              className={cn(
                                "w-14 shrink-0 font-mono text-xs font-bold uppercase",
                                METHOD_TONES[op.method] ?? "text-text-tertiary",
                              )}
                            >
                              {op.method}
                            </span>
                            <span className="min-w-0 flex-1 truncate font-mono text-xs">
                              {op.path}
                            </span>
                            {op.deprecated && (
                              <span className="shrink-0 text-xs text-destructive">
                                deprecated
                              </span>
                            )}
                            {op.compatAlias && (
                              <span
                                data-testid="docs-compat-alias"
                                title="Legacy-named compatibility alias — retained for wire compatibility, not a current Qubit path"
                                className="shrink-0 text-xs text-amber-400"
                              >
                                compat alias
                              </span>
                            )}
                            <span className="hidden min-w-0 max-w-[40%] truncate text-xs text-text-tertiary sm:inline">
                              {op.summary}
                            </span>
                          </summary>

                          <div className="border-t border-border/40 bg-background-base/40 px-3 py-2 text-xs">
                            <p className="text-text-secondary">{op.summary}</p>
                            {op.description && (
                              <p className="mt-1 whitespace-pre-wrap text-text-tertiary">
                                {op.description}
                              </p>
                            )}

                            {op.parameters.length > 0 && (
                              <div className="mt-2">
                                <span className="font-medium text-text-secondary">
                                  Parameters
                                </span>
                                <ul className="mt-1 list-none space-y-0.5">
                                  {op.parameters.map((param, index) => (
                                    <li
                                      key={`${param.name ?? "param"}-${index}`}
                                      className="font-mono text-text-tertiary"
                                    >
                                      {param.name}
                                      {param.in ? ` (${param.in})` : ""}
                                      {param.required ? " *" : ""}
                                      {param.description
                                        ? ` — ${param.description}`
                                        : ""}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {op.responses.length > 0 && (
                              <div className="mt-2">
                                <span className="font-medium text-text-secondary">
                                  Responses
                                </span>
                                <ul className="mt-1 list-none space-y-0.5">
                                  {op.responses.map(([code, response]) => (
                                    <li
                                      key={code}
                                      className="font-mono text-text-tertiary"
                                    >
                                      <span className="text-text-secondary">
                                        {code}
                                      </span>
                                      {response.description
                                        ? ` — ${response.description}`
                                        : ""}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        </details>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}

      <PluginSlot name="docs:bottom" />
    </div>
  );
}
