/**
 * Open Viking console (`/open-viking`).
 *
 * Qubit-owned three-pane console for an external Open Viking server:
 * left workspace/resources nav + context tree, center file
 * Preview/Source viewer, right Quick start panel + terminal stub.
 * All data comes from `@/lib/ocFamilyVikingApi` (bridge paths under
 * `/api/qubit/viking/<action>`); with no server configured the page
 * shows the typed SERVER_UNCONFIGURED state instead of faked content.
 * Sub-pages (wired by the integrator): `/open-viking/retrieval`,
 * `/open-viking/uploads`, `/open-viking/agent-hub`, `/open-viking/resources`.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  Bot,
  Database,
  FileText,
  FolderUp,
  Globe,
  Plug,
  Search,
  BookOpen,
  Info,
  Code,
  Blocks,
  Play,
  Plus,
  Link2,
  TerminalSquare,
} from "lucide-react";
import {
  VIKING_CONTEXT_GROUPS,
  findVikingRelated,
  getVikingContextTree,
  getVikingFile,
  getVikingStatus,
  isServerUnconfiguredPayload,
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
  const [connection, setConnection] = useState<"connected" | "unconfigured" | "unknown" | "loading">("loading");
  const [unconfiguredDetail, setUnconfiguredDetail] = useState("");
  const [tree, setTree] = useState<Partial<Record<VikingContextGroup, VikingContextNode[]>>>({});
  const [treeError, setTreeError] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [viewerMode, setViewerMode] = useState<ViewerMode>("preview");
  const [fileContent, setFileContent] = useState("");
  const [fileUri, setFileUri] = useState("");
  const [fileError, setFileError] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [related, setRelated] = useState<{ title: string; path?: string }[]>([]);
  const [relatedState, setRelatedState] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await getVikingStatus();
        if (cancelled) return;
        if (isServerUnconfiguredPayload(payload)) {
          setConnection("unconfigured");
          setUnconfiguredDetail("Configure an Open Viking server to light up this console.");
          return;
        }
        setConnection(normalizeVikingStatus(payload).connection);
      } catch {
        if (!cancelled) setConnection("unknown");
      }
    })();
    (async () => {
      try {
        const payload = await getVikingContextTree();
        if (cancelled) return;
        if (isServerUnconfiguredPayload(payload)) {
          setConnection("unconfigured");
          return;
        }
        setTree(normalizeContextTree(payload));
      } catch (e) {
        if (!cancelled) setTreeError(e instanceof Error ? e.message : "context tree unavailable");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openPath = useCallback(async (path: string) => {
    setSelectedPath(path);
    setFileError("");
    setFileLoading(true);
    try {
      const payload = await getVikingFile(path);
      if (isServerUnconfiguredPayload(payload)) {
        setConnection("unconfigured");
        setFileContent("");
        setFileUri(toVikingUri(path));
        return;
      }
      const file = normalizeVikingFile(payload);
      setFileContent(file?.content ?? "");
      setFileUri(file?.vikingUri ?? toVikingUri(path));
      if (!file) setFileError("empty file response");
    } catch (e) {
      setFileContent("");
      setFileUri(toVikingUri(path));
      setFileError(e instanceof Error ? e.message : "file load failed");
    } finally {
      setFileLoading(false);
    }
  }, []);

  const onFindRelated = useCallback(async () => {
    if (!selectedPath) return;
    setRelatedState("loading");
    try {
      const payload = await findVikingRelated(selectedPath);
      if (isServerUnconfiguredPayload(payload)) {
        setConnection("unconfigured");
        setRelatedState("error");
        return;
      }
      setRelated(
        normalizeSearchHits(payload).map((h) => ({ title: h.title || h.id, path: h.path })),
      );
      setRelatedState("idle");
    } catch {
      setRelatedState("error");
    }
  }, [selectedPath]);

  const connectionLabel =
    connection === "connected"
      ? "Connected"
      : connection === "unconfigured"
        ? "Server unconfigured"
        : connection === "loading"
          ? "Checking…"
          : "Unknown";

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="open-viking-page">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">Open Viking</h1>
          <p className="text-sm text-muted-foreground">
            Console for the external VikingBot server ·{" "}
            <span data-testid="viking-connection">{connectionLabel}</span>
          </p>
        </div>
      </header>

      {connection === "unconfigured" && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
          role="note"
          data-testid="viking-unconfigured"
        >
          <strong>SERVER_UNCONFIGURED</strong> — {unconfiguredDetail || "no Open Viking server is configured."}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)_280px]">
        {/* Left: workspace/resources nav + context tree */}
        <aside className="flex flex-col gap-4" aria-label="Open Viking navigation">
          <nav className="rounded-md border p-3" aria-label="Workspace">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Workspace
            </h2>
            <ul className="flex flex-col gap-1">
              {WORKSPACE_NAV.map((item) => (
                <li key={item.label}>
                  <Link
                    to={item.path}
                    className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
                  >
                    <item.icon className="h-4 w-4" aria-hidden />
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
            <h2 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Resources
            </h2>
            <ul className="flex flex-col gap-1">
              {RESOURCES_NAV.map((item) => (
                <li key={item.label}>
                  <Link
                    to={item.path}
                    className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
                  >
                    <item.icon className="h-4 w-4" aria-hidden />
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <section className="rounded-md border p-3" aria-label="Context tree" data-testid="viking-context-tree">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Context tree
            </h2>
            {treeError && <p className="text-sm text-destructive">{treeError}</p>}
            {VIKING_CONTEXT_GROUPS.map(({ key, label }) => {
              const nodes = tree[key] ?? [];
              if (nodes.length === 0) return null;
              return (
                <details key={key} className="mb-1" data-testid={`viking-group-${key}`}>
                  <summary className="cursor-pointer rounded px-1 py-1 text-sm font-medium hover:bg-accent">
                    {label} ({nodes.length})
                  </summary>
                  <ul className="ml-3 flex flex-col gap-0.5 border-l pl-2">
                    {nodes.map((node) => {
                      const path = firstPath(node);
                      return (
                        <li key={node.id || node.label}>
                          {path ? (
                            <button
                              type="button"
                              className="w-full truncate rounded px-1 py-0.5 text-left text-sm hover:bg-accent"
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
          </section>
        </aside>

        {/* Center: file Preview/Source viewer */}
        <section className="flex min-h-[24rem] flex-col rounded-md border" aria-label="File viewer">
          <div className="flex flex-wrap items-center gap-2 border-b p-3">
            <FileText className="h-4 w-4" aria-hidden />
            <span className="truncate font-mono text-sm" data-testid="viking-selected-path">
              {selectedPath || "No file selected"}
            </span>
            {selectedPath && (
              <span className="truncate font-mono text-xs text-muted-foreground" data-testid="viking-uri">
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
                  className={`rounded px-3 py-1 text-sm capitalize ${
                    viewerMode === mode ? "bg-primary text-primary-foreground" : "hover:bg-accent"
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-auto p-3" data-testid="viking-viewer-body">
            {fileLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
            {!fileLoading && fileError && <p className="text-sm text-destructive">{fileError}</p>}
            {!fileLoading && !fileError && !selectedPath && (
              <p className="text-sm text-muted-foreground">
                Pick a node in the context tree to preview it here.
              </p>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "source" && (
              <pre className="whitespace-pre-wrap font-mono text-sm">{fileContent}</pre>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "preview" && (
              <article className="prose-sm max-w-none whitespace-pre-wrap text-sm">{fileContent}</article>
            )}
          </div>
        </section>

        {/* Right: quick start + terminal stub */}
        <aside className="flex flex-col gap-4" aria-label="Quick start">
          <section className="rounded-md border p-3">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Quick start
            </h2>
            <div className="flex flex-col gap-2">
              <Link
                to="/open-viking/uploads"
                className="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:bg-accent"
              >
                <Plus className="h-4 w-4" aria-hidden /> Add resource
              </Link>
              <Link
                to="/open-viking/uploads?tab=memory"
                className="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:bg-accent"
              >
                <Database className="h-4 w-4" aria-hidden /> Add memory
              </Link>
              <button
                type="button"
                disabled={!selectedPath}
                onClick={() => void onFindRelated()}
                className="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:bg-accent disabled:opacity-50"
              >
                <Link2 className="h-4 w-4" aria-hidden /> Find related context
              </button>
            </div>
            {relatedState === "loading" && (
              <p className="mt-2 text-sm text-muted-foreground">Finding related context…</p>
            )}
            {relatedState === "error" && (
              <p className="mt-2 text-sm text-destructive">Related lookup failed.</p>
            )}
            {related.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1" data-testid="viking-related">
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
          </section>

          <section className="rounded-md border p-3" aria-label="Terminal" data-testid="viking-terminal">
            <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <TerminalSquare className="h-4 w-4" aria-hidden /> Terminal
            </h2>
            <div className="rounded bg-black/80 p-2 font-mono text-xs text-green-300">
              <p>
                <Globe className="mr-1 inline h-3 w-3" aria-hidden />
                state: {connectionLabel}
              </p>
              <p className="mt-1 text-green-300/70">
                Stub — shows connection state only; no live shell is attached here.
              </p>
            </div>
            <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
              <Plug className="h-3 w-3" aria-hidden /> External server only; nothing runs in this panel.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}
