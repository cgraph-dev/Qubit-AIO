/**
 * Open Viking console (`/open-viking`).
 *
 * Qubit-owned three-pane console over our LOCAL context engine:
 * left workspace/resources nav + context tree, center file
 * Preview/Source viewer, right Quick start panel + terminal stub.
 * All data comes from `@/lib/ocFamilyVikingApi` (bridge paths under
 * `/api/qubit/viking/<action>`); the local engine is always present, so
 * there is no unconfigured state. A bridge row that still 404s renders
 * its control honestly disabled with reason — never faked content.
 * Sub-pages (wired by the integrator): `/open-viking/retrieval`,
 * `/open-viking/uploads`, `/open-viking/agent-hub`, `/open-viking/resources`.
 *
 * Visual language matches SessionsPage/ChannelsPage: the layout owns the
 * page title (see ROUTE_TITLES), so this page renders no `h1`; sections
 * are `Card`s, actions are `Button`s, loading is a centered `Spinner`.
 */

import { useCallback, useContext, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  AlertTriangle,
  Blocks,
  BookOpen,
  Bot,
  Code,
  Database,
  FileText,
  FolderUp,
  Globe,
  Info,
  Link2,
  Play,
  Plug,
  Plus,
  Search,
  TerminalSquare,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { PageHeaderContext } from "@/contexts/page-header-context";
import {
  VIKING_CONTEXT_GROUPS,
  findVikingRelated,
  getVikingContextTree,
  getVikingFile,
  getVikingStatus,
  isBridgeRowMissing,
  normalizeContextTree,
  normalizeSearchHits,
  normalizeVikingFile,
  normalizeVikingStatus,
  toVikingUri,
  type VikingContextNode,
  type VikingContextGroup,
} from "@/lib/ocFamilyVikingApi";

const WORKSPACE_NAV = [
  { path: "/open-viking", label: "Playground", icon: Play },
  { path: "/open-viking/retrieval", label: "Retrieval", icon: Search },
  { path: "/open-viking/uploads", label: "Uploads", icon: FolderUp },
  { path: "/open-viking/agent-hub", label: "Agent Hub", icon: Bot },
];

const RESOURCES_NAV = [
  { path: "/open-viking/resources", label: "Documentation", icon: BookOpen },
  { path: "/open-viking/resources", label: "About", icon: Info },
  { path: "/open-viking/resources", label: "SDK & API", icon: Code },
  { path: "/open-viking/resources", label: "Agent Integrations", icon: Blocks },
];

type ViewerMode = "preview" | "source";

function firstPath(node: VikingContextNode): string | undefined {
  if (node.path) return node.path;
  for (const child of node.children ?? []) {
    const found = firstPath(child);
    if (found) return found;
  }
  return undefined;
}

export default function OpenVikingPage() {
  const navigate = useNavigate();
  // The layout may not provide a page-header host (e.g. in tests) — the
  // context value is null there, so every call below is guarded.
  const pageHeader = useContext(PageHeaderContext);
  const [connection, setConnection] = useState<
    "connected" | "unavailable" | "unknown" | "loading"
  >("loading");
  const [missingRows, setMissingRows] = useState<string[]>([]);
  const [tree, setTree] = useState<
    Partial<Record<VikingContextGroup, VikingContextNode[]>>
  >({});
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState("");
  const [treeMissing, setTreeMissing] = useState(false);
  const [selectedPath, setSelectedPath] = useState("");
  const [viewerMode, setViewerMode] = useState<ViewerMode>("preview");
  const [fileContent, setFileContent] = useState("");
  const [fileUri, setFileUri] = useState("");
  const [fileError, setFileError] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [related, setRelated] = useState<{ title: string; path?: string }[]>([]);
  const [relatedState, setRelatedState] = useState<
    "idle" | "loading" | "error" | "unavailable"
  >("idle");
  const [relatedError, setRelatedError] = useState("");

  function markMissing(row: string) {
    setMissingRows((prev) => (prev.includes(row) ? prev : [...prev, row]));
  }

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
        if (isBridgeRowMissing(e)) {
          markMissing("status");
          setConnection("unavailable");
        } else {
          setConnection("unknown");
        }
      }
    })();
    (async () => {
      try {
        const payload = await getVikingContextTree();
        if (cancelled) return;
        setTree(normalizeContextTree(payload));
      } catch (e) {
        if (cancelled) return;
        if (isBridgeRowMissing(e)) {
          setTreeMissing(true);
          markMissing("context-tree");
        } else {
          setTreeError(e instanceof Error ? e.message : "context tree unavailable");
        }
      } finally {
        if (!cancelled) setTreeLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Sessions-style connection badge next to the layout title.
  useEffect(() => {
    if (!pageHeader || connection === "loading") return;
    pageHeader.setAfterTitle(
      <Badge tone={connection === "connected" ? "success" : "outline"}>
        {connection === "connected"
          ? "LOCAL ENGINE"
          : connection === "unavailable"
            ? "ROW MISSING"
            : "UNKNOWN"}
      </Badge>,
    );
    return () => {
      pageHeader.setAfterTitle(null);
    };
  }, [connection, pageHeader]);

  const openPath = useCallback(async (path: string) => {
    setSelectedPath(path);
    setFileError("");
    setFileLoading(true);
    try {
      const payload = await getVikingFile(path);
      const file = normalizeVikingFile(payload);
      setFileContent(file?.content ?? "");
      setFileUri(file?.vikingUri ?? toVikingUri(path));
      if (!file) setFileError("empty file response");
    } catch (e) {
      setFileContent("");
      setFileUri(toVikingUri(path));
      if (isBridgeRowMissing(e)) {
        markMissing("file");
        setFileError("This file couldn't be loaded — try again.");
      } else {
        setFileError(e instanceof Error ? e.message : "file load failed");
      }
    } finally {
      setFileLoading(false);
    }
  }, []);

  const onFindRelated = useCallback(async () => {
    if (!selectedPath) return;
    setRelatedState("loading");
    setRelatedError("");
    try {
      const payload = await findVikingRelated(selectedPath);
      setRelated(
        normalizeSearchHits(payload).map((h) => ({ title: h.title || h.id, path: h.path })),
      );
      setRelatedState("idle");
    } catch (e) {
      if (isBridgeRowMissing(e)) {
        markMissing("related-find");
        setRelatedState("unavailable");
      } else {
        setRelatedError(e instanceof Error ? e.message : "related lookup failed");
        setRelatedState("error");
      }
    }
  }, [selectedPath]);

  const connectionLabel =
    connection === "connected"
      ? "Connected (local engine)"
      : connection === "unavailable"
        ? "Bridge row missing"
        : connection === "loading"
          ? "Checking…"
          : "Unknown";

  const treeGroupCount = VIKING_CONTEXT_GROUPS.reduce(
    (n, { key }) => n + (tree[key]?.length ?? 0),
    0,
  );

  return (
    <div className="flex flex-col gap-6" data-testid="open-viking-page">
      <p className="text-xs text-muted-foreground">
        Console over the local context engine ·{" "}
        <span data-testid="viking-connection">{connectionLabel}</span>
      </p>

      {missingRows.length > 0 && (
        <Card className="border-warning/50">
          <CardContent
            className="flex items-center gap-2 p-4 text-sm"
            role="note"
            data-testid="viking-missing-rows"
          >
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
            <span>
              Bridge row{missingRows.length === 1 ? "" : "s"} not yet served:{" "}
              {missingRows.join(", ")} — those controls stay disabled until the
              row lands.
            </span>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)_280px]">
        {/* Left: workspace/resources nav + context tree */}
        <aside className="flex flex-col gap-4" aria-label="Open Viking navigation">
          <Card>
            <CardContent className="flex flex-col gap-4 p-4">
              <nav aria-label="Workspace">
                <h2 className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                  Workspace
                </h2>
                <ul className="flex flex-col gap-1">
                  {WORKSPACE_NAV.map((item) => (
                    <li key={item.label}>
                      <Link
                        to={item.path}
                        className="flex items-center gap-2 px-2 py-1.5 text-sm hover:bg-midground/10"
                      >
                        <item.icon className="h-4 w-4" aria-hidden />
                        {item.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </nav>
              <nav aria-label="Resources">
                <h2 className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                  Resources
                </h2>
                <ul className="flex flex-col gap-1">
                  {RESOURCES_NAV.map((item) => (
                    <li key={item.label}>
                      <Link
                        to={item.path}
                        className="flex items-center gap-2 px-2 py-1.5 text-sm hover:bg-midground/10"
                      >
                        <item.icon className="h-4 w-4" aria-hidden />
                        {item.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </nav>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Context tree</CardTitle>
            </CardHeader>
            <CardContent
              className="pt-0"
              aria-label="Context tree"
              data-testid="viking-context-tree"
            >
              {treeLoading && (
                <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                  <Spinner /> Loading context tree…
                </div>
              )}
              {!treeLoading && treeError && (
                <p className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {treeError}
                </p>
              )}
              {!treeLoading && treeMissing && (
                <p
                  className="text-sm text-muted-foreground"
                  data-testid="viking-tree-unavailable"
                >
                  Context tree couldn't be loaded — try Refresh.
                </p>
              )}
              {!treeLoading && !treeMissing && !treeError && treeGroupCount === 0 && (
                <p className="text-sm text-muted-foreground">
                  No context entries yet — add a resource or a memory to light
                  up this tree.
                </p>
              )}
              {VIKING_CONTEXT_GROUPS.map(({ key, label }) => {
                const nodes = tree[key] ?? [];
                if (nodes.length === 0) return null;
                return (
                  <details key={key} className="mb-1" data-testid={`viking-group-${key}`}>
                    <summary className="cursor-pointer px-1 py-1 text-sm font-medium hover:bg-midground/10">
                      {label} ({nodes.length})
                    </summary>
                    <ul className="ml-3 flex flex-col gap-0.5 border-l border-border pl-2">
                      {nodes.map((node) => {
                        const path = firstPath(node);
                        return (
                          <li key={node.id || node.label}>
                            {path ? (
                              <button
                                type="button"
                                className="w-full truncate px-1 py-0.5 text-left text-sm hover:bg-midground/10"
                                title={path}
                                onClick={() => void openPath(path)}
                              >
                                {node.label}
                              </button>
                            ) : (
                              <span className="px-1 py-0.5 text-sm">{node.label}</span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                );
              })}
            </CardContent>
          </Card>
        </aside>

        {/* Center: file Preview/Source viewer */}
        <Card className="flex min-h-[24rem] flex-col" aria-label="File viewer">
          <CardContent className="flex flex-wrap items-center gap-2 border-b border-border p-4">
            <FileText className="h-4 w-4 shrink-0" aria-hidden />
            <span
              className="truncate font-courier text-sm"
              data-testid="viking-selected-path"
            >
              {selectedPath || "No file selected"}
            </span>
            {selectedPath && (
              <span
                className="truncate font-courier text-xs text-muted-foreground"
                data-testid="viking-uri"
              >
                {fileUri || toVikingUri(selectedPath)}
              </span>
            )}
            <div className="ml-auto flex gap-1" role="tablist" aria-label="Viewer mode">
              {(["preview", "source"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  role="tab"
                  aria-selected={viewerMode === mode}
                  onClick={() => setViewerMode(mode)}
                  className={`px-3 py-1 text-xs uppercase tracking-[0.12em] ${
                    viewerMode === mode
                      ? "bg-midground text-background"
                      : "text-muted-foreground hover:bg-midground/10"
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
          </CardContent>
          <div className="flex-1 overflow-auto p-4" data-testid="viking-viewer-body">
            {fileLoading && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner /> Loading…
              </div>
            )}
            {!fileLoading && fileError && (
              <p className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {fileError}
              </p>
            )}
            {!fileLoading && !fileError && !selectedPath && (
              <p className="text-sm text-muted-foreground">
                Pick a node in the context tree to preview it here.
              </p>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "source" && (
              <pre className="whitespace-pre-wrap font-courier text-sm">
                {fileContent}
              </pre>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "preview" && (
              <article className="whitespace-pre-wrap text-sm">{fileContent}</article>
            )}
          </div>
        </Card>

        {/* Right: quick start + terminal stub */}
        <aside className="flex flex-col gap-4" aria-label="Quick start">
          <Card>
            <CardHeader>
              <CardTitle>Quick start</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 pt-0">
              <Button
                size="sm"
                outlined
                className="uppercase"
                onClick={() => void navigate("/open-viking/uploads")}
                prefix={<Plus className="h-4 w-4" />}
              >
                Add resource
              </Button>
              <Button
                size="sm"
                outlined
                className="uppercase"
                onClick={() => void navigate("/open-viking/uploads?tab=memory")}
                prefix={<Database className="h-4 w-4" />}
              >
                Add memory
              </Button>
              <Button
                size="sm"
                outlined
                className="uppercase"
                disabled={!selectedPath || relatedState === "unavailable"}
                onClick={() => void onFindRelated()}
                prefix={
                  relatedState === "loading" ? (
                    <Spinner />
                  ) : (
                    <Link2 className="h-4 w-4" />
                  )
                }
              >
                {relatedState === "loading" ? "Finding…" : "Find related context"}
              </Button>
              {relatedState === "error" && (
                <p className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {relatedError || "Related lookup failed."}
                </p>
              )}
              {relatedState === "unavailable" && (
                <p
                  className="text-sm text-muted-foreground"
                  data-testid="viking-related-unavailable"
                >
                  Related-find bridge row missing — the J1 lane has not landed
                  it yet.
                </p>
              )}
              {related.length > 0 && (
                <ul className="mt-1 flex flex-col gap-1" data-testid="viking-related">
                  {related.map((row) => (
                    <li key={`${row.title}-${row.path ?? ""}`} className="text-sm">
                      {row.path ? (
                        <button
                          type="button"
                          className="text-left underline-offset-2 hover:underline"
                          onClick={() => void openPath(row.path as string)}
                        >
                          {row.title}
                        </button>
                      ) : (
                        row.title
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card aria-label="Terminal" data-testid="viking-terminal">
            <CardHeader>
              <CardTitle>
                <span className="flex items-center gap-2">
                  <TerminalSquare className="h-4 w-4" aria-hidden /> Terminal
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 pt-0">
              <div className="bg-black/80 p-2 font-courier text-xs text-green-300">
                <p>
                  <Globe className="mr-1 inline h-3 w-3" aria-hidden />
                  state: {connectionLabel}
                </p>
                <p className="mt-1 text-green-300/70">
                  Read-only status view — run commands from the Terminal page
                  or a harness task instead.
                </p>
              </div>
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Plug className="h-3 w-3" aria-hidden /> Local engine only;
                nothing runs in this panel.
              </p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
