/**
 * Open Viking console (`/open-viking`).
 *
 * Qubit-owned three-pane console over our LOCAL context engine:
 * left workspace/resources nav + context tree, center file
 * Preview/Source viewer, right Quick start panel + status strip.
 * All data comes from `@/lib/ocFamilyVikingApi` (bridge paths under
 * `/api/qubit/viking/<action>`); the local engine is always present, so
 * there is no unconfigured state. A bridge row that still 404s renders
 * its control honestly disabled with reason — never faked content.
 * Sub-pages (wired by the integrator): `/open-viking/retrieval`,
 * `/open-viking/uploads`, `/open-viking/agent-hub`, `/open-viking/resources`.
 *
 * Panels use the shared settings contract
 * (`.settings-section` > `.settings-group` > `.settings-row`) so this page
 * matches the ported console hierarchy; the layout owns the page title
 * (see ROUTE_TITLES), so this page renders no `h1`.
 */

import { useCallback, useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router";
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
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { PageHeaderContext } from "@/contexts/page-header-context";
import {
  SettingsEmpty,
  SettingsPageIntro,
  SettingsRow,
  SettingsSection,
  SettingsStatus,
} from "@/components/viking-settings";
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

  // Console-style connection badge next to the layout title.
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
    <div
      className="qb-upstream viking-settings settings-page settings-page--wide"
      data-testid="open-viking-page"
    >
      <SettingsPageIntro currentPath="/open-viking" showSubnav>
        Console over the local context engine ·{" "}
        <span data-testid="viking-connection">{connectionLabel}</span>
      </SettingsPageIntro>

      {missingRows.length > 0 && (
        <div
          className="callout danger flex items-center gap-2"
          role="note"
          data-testid="viking-missing-rows"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
          <span>
            Bridge row{missingRows.length === 1 ? "" : "s"} not yet served:{" "}
            {missingRows.join(", ")} — those controls stay disabled with the
            reason until the row lands.
          </span>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)_280px]">
        {/* Left: workspace/resources nav + context tree */}
        <aside className="flex flex-col gap-4" aria-label="Open Viking navigation">
          <SettingsSection title="Workspace" ariaLabel="Workspace">
            {WORKSPACE_NAV.map((item) => (
              <SettingsRow
                key={item.label}
                as="button"
                title={
                  <span className="flex items-center gap-2">
                    <item.icon className="h-4 w-4" aria-hidden /> {item.label}
                  </span>
                }
                onClick={() => void navigate(item.path)}
              />
            ))}
          </SettingsSection>

          <SettingsSection title="Resources" ariaLabel="Resources">
            {RESOURCES_NAV.map((item) => (
              <SettingsRow
                key={item.label}
                as="button"
                title={
                  <span className="flex items-center gap-2">
                    <item.icon className="h-4 w-4" aria-hidden /> {item.label}
                  </span>
                }
                onClick={() => void navigate(item.path)}
              />
            ))}
          </SettingsSection>

          <SettingsSection title="Context tree" ariaLabel="Context tree">
            <div data-testid="viking-context-tree">
              {treeLoading && <SettingsEmpty>Loading context tree…</SettingsEmpty>}
              {!treeLoading && treeError && (
                <div className="callout danger" role="alert">
                  {treeError}
                </div>
              )}
              {!treeLoading && treeMissing && (
                <SettingsEmpty testId="viking-tree-unavailable">
                  Context tree couldn't be loaded — try again.
                </SettingsEmpty>
              )}
              {!treeLoading && !treeMissing && !treeError && treeGroupCount === 0 && (
                <SettingsEmpty>
                  No context entries yet — add a resource or a memory to light up
                  this tree.
                </SettingsEmpty>
              )}
              {VIKING_CONTEXT_GROUPS.map(({ key, label }) => {
                const nodes = tree[key] ?? [];
                if (nodes.length === 0) return null;
                return (
                  <details key={key} open data-testid={`viking-group-${key}`}>
                    <summary className="settings-row__desc cursor-pointer px-3 py-2">
                      {label} ({nodes.length})
                    </summary>
                    <div>
                      {nodes.map((node) => {
                        const path = firstPath(node);
                        return path ? (
                          <SettingsRow
                            key={node.id || node.label}
                            as="button"
                            title={node.label}
                            onClick={() => void openPath(path)}
                            ariaLabel={`Preview ${node.label}`}
                          />
                        ) : (
                          <SettingsRow key={node.id || node.label} title={node.label} />
                        );
                      })}
                    </div>
                  </details>
                );
              })}
            </div>
          </SettingsSection>
        </aside>

        {/* Center: file Preview/Source viewer */}
        <SettingsSection title="File viewer" ariaLabel="File viewer">
          <SettingsRow
            title={
              <span className="flex min-w-0 items-center gap-2">
                <FileText className="h-4 w-4 shrink-0" aria-hidden />
                <span className="truncate" data-testid="viking-selected-path">
                  {selectedPath || "No file selected"}
                </span>
              </span>
            }
            description={
              selectedPath ? (
                <span className="truncate" data-testid="viking-uri">
                  {fileUri || toVikingUri(selectedPath)}
                </span>
              ) : (
                "Pick a node in the context tree to preview it here."
              )
            }
            control={
              <span className="flex gap-1" role="tablist" aria-label="Viewer mode">
                {(["preview", "source"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    role="tab"
                    aria-selected={viewerMode === mode}
                    onClick={() => setViewerMode(mode)}
                    className={`btn btn--xs uppercase ${viewerMode === mode ? "active" : "btn--ghost"}`}
                  >
                    {mode}
                  </button>
                ))}
              </span>
            }
          />
          <div className="p-4" data-testid="viking-viewer-body">
            {fileLoading && (
              <p className="settings-row__desc flex items-center gap-2">
                <Spinner className="shrink-0" /> Loading…
              </p>
            )}
            {!fileLoading && fileError && (
              <div className="callout danger" role="alert">
                {fileError}
              </div>
            )}
            {!fileLoading && !fileError && !selectedPath && (
              <SettingsEmpty>
                Pick a node in the context tree to preview it here.
              </SettingsEmpty>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "source" && (
              <pre className="whitespace-pre-wrap font-courier text-sm">{fileContent}</pre>
            )}
            {!fileLoading && !fileError && selectedPath && viewerMode === "preview" && (
              <article className="whitespace-pre-wrap text-sm">{fileContent}</article>
            )}
          </div>
        </SettingsSection>

        {/* Right: quick start + status strip */}
        <aside className="flex flex-col gap-4" aria-label="Quick start">
          <SettingsSection title="Quick start" ariaLabel="Quick start">
            <SettingsRow
              title="Add resource"
              description="Ingest a document or URI into the context store."
              control={
                <Button
                  size="sm"
                  outlined
                  className="uppercase"
                  onClick={() => void navigate("/open-viking/uploads")}
                  prefix={<Plus className="h-4 w-4" />}
                >
                  Add
                </Button>
              }
            />
            <SettingsRow
              title="Add memory"
              description="Stage a note into user memory (confirm-gated)."
              control={
                <Button
                  size="sm"
                  outlined
                  className="uppercase"
                  onClick={() => void navigate("/open-viking/uploads?tab=memory")}
                  prefix={<Database className="h-4 w-4" />}
                >
                  Add
                </Button>
              }
            />
            <SettingsRow
              stacked
              title="Find related context"
              description={
                relatedState === "unavailable"
                  ? "Related-find bridge row missing — the local engine isn't serving this lookup yet."
                  : !selectedPath
                    ? "Select a file in the context tree first."
                    : "Look up siblings and keyword kin for the selected file."
              }
              control={
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
                  {relatedState === "loading" ? "Finding…" : "Find related"}
                </Button>
              }
              testId={relatedState === "unavailable" ? "viking-related-unavailable" : undefined}
            />
            {relatedState === "error" && (
              <div className="callout danger" role="alert">
                {relatedError || "Related lookup failed."}
              </div>
            )}
            {related.length > 0 && (
              <div data-testid="viking-related">
                {related.map((row) => (
                  <SettingsRow
                    key={`${row.title}-${row.path ?? ""}`}
                    as="button"
                    title={row.title}
                    description={row.path}
                    onClick={row.path ? () => void openPath(row.path as string) : undefined}
                    disabled={!row.path}
                  />
                ))}
              </div>
            )}
          </SettingsSection>

          <SettingsSection
            title="Engine status"
            ariaLabel="Engine status"
          >
            <div data-testid="viking-terminal">
              <SettingsRow
                title={
                  <span className="flex items-center gap-2">
                    <Globe className="h-4 w-4" aria-hidden /> Local engine
                  </span>
                }
                description="Read-only status view — run commands from the Terminal page or a harness task instead."
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
                    {connectionLabel}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                title={
                  <span className="flex items-center gap-1">
                    <Plug className="h-3 w-3" aria-hidden /> Local only
                  </span>
                }
                description="Nothing runs in this panel; the engine is on this host."
              />
            </div>
          </SettingsSection>
        </aside>
      </div>
    </div>
  );
}
