import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  KeyRound,
  Package,
  Play,
  Power,
  RefreshCw,
  Server,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  McpCapabilityResult,
  McpCatalogDiagnostic,
  McpCatalogEntry,
  McpHttpAuth,
  McpServer,
  McpTestResult,
} from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { usePageHeader } from "@/contexts/usePageHeader";
import { rebrand } from "@/lib/rebrand";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn, themedBody } from "@/lib/utils";
import {
  buildMcpServerCreate,
  type McpTransport,
} from "@/lib/mcp-server-create";
import { completeMcpDashboardOAuth } from "@/lib/mcp-dashboard-oauth";

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value.trim());
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength ? value.slice(0, maxLength) + "..." : value;
}

const TRANSPORT_TONE: Record<string, "success" | "warning" | "secondary"> = {
  http: "success",
  stdio: "warning",
  unknown: "secondary",
};

/** Pull the drained list out of a capability payload, accepting both the
 *  canonical `resource_templates` key and the `resourceTemplates` wire alias.
 *  Null when the payload is absent (not yet loaded). */
function capabilityItems(
  data: McpCapabilityResult | undefined,
  kind: "resources" | "prompts" | "templates",
): Array<Record<string, unknown>> | null {
  if (!data) return null;
  if (kind === "resources") return Array.isArray(data.resources) ? data.resources : [];
  if (kind === "prompts") return Array.isArray(data.prompts) ? data.prompts : [];
  const snake = (data as McpCapabilityResult).resource_templates;
  const camel = (data as McpCapabilityResult).resourceTemplates;
  if (Array.isArray(snake)) return snake;
  if (Array.isArray(camel)) return camel;
  // Fall back to the raw drained payload's list key.
  const raw = (data.result ?? {}) as Record<string, unknown>;
  for (const k of ["resource_templates", "resourceTemplates", "templates"]) {
    if (Array.isArray(raw[k])) return raw[k] as Array<Record<string, unknown>>;
  }
  return [];
}

/** Primary label + secondary detail for one drained entry. Unknown shapes
 *  render as compact JSON — never dropped, never invented. */
function capabilityLabel(item: unknown): { primary: string; secondary: string | null } {
  if (item && typeof item === "object" && !Array.isArray(item)) {
    const rec = item as Record<string, unknown>;
    const str = (v: unknown): string | null =>
      typeof v === "string" && v.trim() ? v.trim() : null;
    const primary =
      str(rec.name) ?? str(rec.uri) ?? str(rec.uriTemplate) ??
      str(rec.uri_template) ?? str(rec.id);
    if (primary) {
      const parts = [
        str(rec.description),
        str(rec.uri) === primary ? null : str(rec.uri),
        str(rec.uriTemplate) === primary ? null : str(rec.uriTemplate),
        str(rec.uri_template) === primary ? null : str(rec.uri_template),
        str(rec.mimeType),
        str(rec.mime_type),
      ].filter((v): v is string => !!v);
      return {
        primary,
        secondary: parts.length > 0 ? parts.join(" · ") : null,
      };
    }
    try {
      return { primary: JSON.stringify(rec).slice(0, 160), secondary: null };
    } catch {
      return { primary: String(item).slice(0, 160), secondary: null };
    }
  }
  return { primary: String(item ?? "").slice(0, 160), secondary: null };
}

export default function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [catalog, setCatalog] = useState<McpCatalogEntry[]>([]);
  const [diagnostics, setDiagnostics] = useState<McpCatalogDiagnostic[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  // Add server modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<McpTransport>("http");
  const [url, setUrl] = useState("");
  const [httpAuth, setHttpAuth] = useState<McpHttpAuth>("none");
  const [bearerToken, setBearerToken] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [creating, setCreating] = useState(false);
  const closeCreateModal = useCallback(() => {
    setBearerToken("");
    setCreateModalOpen(false);
  }, []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
  });

  // Test results keyed by server name
  const [testing, setTesting] = useState<string | null>(null);
  const [authenticating, setAuthenticating] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>(
    {},
  );

  // Honest per-server capability drain (tools are already served on the
  // server row; resources/prompts/resource-templates come from the real
  // compat reads `GET /api/mcp/servers/{name}/resources|prompts|
  // resource-templates`). Lazy: nothing is fetched until the user expands a
  // server's Capabilities panel. A typed disposition is rendered verbatim and
  // is retryable — never a fabricated list.
  const [capOpen, setCapOpen] = useState<Record<string, boolean>>({});
  const [capLoading, setCapLoading] = useState<Record<string, boolean>>({});
  const [capData, setCapData] = useState<
    Record<string, Partial<Record<"resources" | "prompts" | "templates", McpCapabilityResult>>>
  >({});
  const [capErrors, setCapErrors] = useState<
    Record<string, Partial<Record<"resources" | "prompts" | "templates", string>>>
  >({});

  const loadCapabilities = useCallback(async (serverName: string) => {
    setCapLoading((prev) => ({ ...prev, [serverName]: true }));
    setCapErrors((prev) => ({ ...prev, [serverName]: {} }));
    const settled = await Promise.allSettled([
      api.getMcpServerResources(serverName),
      api.getMcpServerPrompts(serverName),
      api.getMcpServerResourceTemplates(serverName),
    ]);
    const keys = ["resources", "prompts", "templates"] as const;
    setCapData((prev) => {
      const next = { ...(prev[serverName] ?? {}) };
      settled.forEach((r, i) => {
        if (r.status === "fulfilled") next[keys[i]] = r.value;
      });
      return { ...prev, [serverName]: next };
    });
    setCapErrors((prev) => {
      const next: Partial<Record<"resources" | "prompts" | "templates", string>> = {};
      settled.forEach((r, i) => {
        if (r.status === "rejected") {
          next[keys[i]] = r.reason instanceof Error ? r.reason.message : String(r.reason);
        }
      });
      return { ...prev, [serverName]: next };
    });
    setCapLoading((prev) => ({ ...prev, [serverName]: false }));
  }, []);

  const toggleCapabilities = useCallback(
    (serverName: string) => {
      setCapOpen((prev) => {
        const next = !prev[serverName];
        if (next) void loadCapabilities(serverName);
        return { ...prev, [serverName]: next };
      });
    },
    [loadCapabilities],
  );

  // Tool invocation (the canonical confirm-gated `POST /invoke`). The modal
  // offers the server's declared capabilities as tools; args are a JSON object.
  const [invokeFor, setInvokeFor] = useState<McpServer | null>(null);
  const [invokeTool, setInvokeTool] = useState("");
  const [invokeArgs, setInvokeArgs] = useState("{}");
  const [invoking, setInvoking] = useState(false);
  const [invokeResult, setInvokeResult] = useState<string | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const closeInvokeModal = useCallback(() => {
    setInvokeFor(null);
    setInvokeResult(null);
    setInvokeError(null);
  }, []);
  const invokeModalRef = useModalBehavior({
    open: invokeFor !== null,
    onClose: closeInvokeModal,
  });

  // Enable/disable state
  const [togglingName, setTogglingName] = useState<string | null>(null);
  const [restartNote, setRestartNote] = useState<string | null>(null);

  // Catalog install modal state
  const [installEntry, setInstallEntry] = useState<McpCatalogEntry | null>(
    null,
  );
  const [installEnv, setInstallEnv] = useState<Record<string, string>>({});
  const [installingName, setInstallingName] = useState<string | null>(null);
  const closeInstallModal = useCallback(() => setInstallEntry(null), []);
  const installModalRef = useModalBehavior({
    open: installEntry !== null,
    onClose: closeInstallModal,
  });

  const loadServers = useCallback(() => {
    return api
      .getMcpServers()
      .then((res) => setServers(res.servers))
      .catch((e) => showToast(`Error: ${e}`, "error"));
  }, [showToast]);

  const loadCatalog = useCallback(() => {
    return api
      .getMcpCatalog()
      .then((res) => {
        setCatalog(res.entries);
        setDiagnostics(res.diagnostics);
      })
      .catch((e) => showToast(`Error: ${e}`, "error"));
  }, [showToast]);

  useEffect(() => {
    Promise.all([loadServers(), loadCatalog()]).finally(() =>
      setLoading(false),
    );
  }, [loadServers, loadCatalog]);

  const handleCreate = async () => {
    let body;
    try {
      body = buildMcpServerCreate({
        name,
        transport,
        url,
        httpAuth,
        bearerToken,
        command,
        args,
        env,
      });
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Invalid MCP server",
        "error",
      );
      return;
    }

    setCreating(true);
    try {
      await api.addMcpServer(body);
      showToast(
        transport === "http" && httpAuth === "oauth"
          ? "Added — authenticate with OAuth"
          : "Add ✓",
        "success",
      );
      setName("");
      setUrl("");
      setHttpAuth("none");
      setBearerToken("");
      setCommand("");
      setArgs("");
      setEnv("");
      setTransport("http");
      setCreateModalOpen(false);
      loadServers();
    } catch (e) {
      showToast(`Failed to add: ${e}`, "error");
    } finally {
      setCreating(false);
    }
  };

  const handleTest = async (server: McpServer) => {
    setTesting(server.name);
    try {
      const result = await api.testMcpServer(server.name);
      setTestResults((prev) => ({ ...prev, [server.name]: result }));
      if (result.ok) {
        showToast(`${server.name}: ${result.tools.length} tool(s)`, "success");
      } else {
        showToast(`${server.name}: ${result.error ?? "Failed"}`, "error");
      }
    } catch (e) {
      showToast(`Error: ${e}`, "error");
    } finally {
      setTesting(null);
    }
  };

  const openInvoke = useCallback((server: McpServer) => {
    setInvokeFor(server);
    setInvokeTool(server.capabilities?.[0] ?? "");
    setInvokeArgs("{}");
    setInvokeResult(null);
    setInvokeError(null);
  }, []);

  const handleInvoke = async () => {
    if (!invokeFor) return;
    const tool = invokeTool.trim();
    if (!tool) {
      showToast("Tool name required", "error");
      return;
    }
    let parsedArgs: Record<string, unknown>;
    try {
      const parsed = invokeArgs.trim() ? JSON.parse(invokeArgs) : {};
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("args must be a JSON object");
      }
      parsedArgs = parsed as Record<string, unknown>;
    } catch (e) {
      showToast(
        `Invalid args JSON: ${e instanceof Error ? e.message : e}`,
        "error",
      );
      return;
    }
    setInvoking(true);
    setInvokeError(null);
    setInvokeResult(null);
    try {
      const res = await api.invokeMcpTool(
        invokeFor.name,
        tool,
        parsedArgs,
        () => true,
      );
      if (res.ok) {
        setInvokeResult(
          typeof res.result === "string"
            ? res.result
            : JSON.stringify(res.result ?? res, null, 2),
        );
        showToast(`${invokeFor.name}/${tool}: ${res.state ?? "OK"}`, "success");
      } else {
        const detail = `${res.code ?? "FAILED"}: ${res.detail ?? "invocation refused"}`;
        setInvokeError(detail);
        showToast(detail, "error");
      }
    } catch (e) {
      setInvokeError(String(e));
      showToast(`Invoke failed: ${e}`, "error");
    } finally {
      setInvoking(false);
    }
  };

  const handleAuthenticate = async (server: McpServer) => {
    setAuthenticating(server.name);
    try {
      const result = await completeMcpDashboardOAuth({
        serverName: server.name,
        start: api.authMcpServer,
        status: api.getMcpOAuthFlow,
        open: window.open.bind(window),
      });
      setTestResults((prev) => ({
        ...prev,
        [server.name]: { ok: true, tools: result.tools ?? [] },
      }));
      showToast(`${server.name}: OAuth authentication complete`, "success");
    } catch (e) {
      showToast(refusalOrError(e, `OAuth error: ${e}`), "error");
    } finally {
      setAuthenticating(null);
    }
  };

  const handleToggleEnabled = async (server: McpServer) => {
    const next = !server.enabled;
    setTogglingName(server.name);
    try {
      await api.setMcpServerEnabled(server.name, next);
      setServers((prev) =>
        prev.map((s) => (s.name === server.name ? { ...s, enabled: next } : s)),
      );
      // The invoke path reads the store per call, so the change is immediate
      // for client invocation; only the agent's in-session tool list refreshes
      // on the next session (AI5 D-8 — the old "needs a gateway restart" note
      // was false).
      setRestartNote(
        "Applies immediately to client invocation; the agent tool list refreshes on the next session.",
      );
    } catch (e) {
      showToast(refusalOrError(e, `Error: ${e}`), "error");
    } finally {
      setTogglingName(null);
    }
  };

  const serverDelete = useConfirmDelete({
    onDelete: useCallback(
      async (serverName: string) => {
        try {
          await api.removeMcpServer(serverName);
          showToast(`Delete: "${truncateText(serverName, 30)}"`, "success");
          setTestResults((prev) => {
            const next = { ...prev };
            delete next[serverName];
            return next;
          });
          loadServers();
        } catch (e) {
          showToast(`Error: ${e}`, "error");
          throw e;
        }
      },
      [loadServers, showToast],
    ),
  });

  // ── Catalog install ──────────────────────────────────────────────────
  const runInstall = useCallback(
    async (entry: McpCatalogEntry, envMap: Record<string, string>) => {
      setInstallingName(entry.name);
      try {
        const res = await api.installMcpCatalogEntry(entry.name, envMap, true);
        if (res.background) {
          showToast("Installing in background…", "success");
        } else {
          showToast(`Installed: "${truncateText(entry.name, 30)}"`, "success");
        }
        setInstallEntry(null);
        setInstallEnv({});
        await Promise.all([loadServers(), loadCatalog()]);
      } catch (e) {
        showToast(refusalOrError(e, `Failed to install: ${e}`), "error");
      } finally {
        setInstallingName(null);
      }
    },
    [loadServers, loadCatalog, showToast],
  );

  const handleInstallClick = (entry: McpCatalogEntry) => {
    if (entry.required_env.length > 0) {
      const initial: Record<string, string> = {};
      entry.required_env.forEach((item) => {
        initial[item.name] = "";
      });
      setInstallEnv(initial);
      setInstallEntry(entry);
    } else {
      void runInstall(entry, {});
    }
  };

  const handleInstallSubmit = () => {
    if (!installEntry) return;
    const missing = installEntry.required_env.filter(
      (item) => item.required && !(installEnv[item.name] ?? "").trim(),
    );
    if (missing.length > 0) {
      showToast(`${missing[0].prompt} required`, "error");
      return;
    }
    const envMap: Record<string, string> = {};
    Object.entries(installEnv).forEach(([k, v]) => {
      if (v.trim()) envMap[k] = v.trim();
    });
    void runInstall(installEntry, envMap);
  };

  // Put "Add Server" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={() => setCreateModalOpen(true)}
      >
        Add Server
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, loading]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const diagnosticsByName: Record<string, McpCatalogDiagnostic[]> = {};
  diagnostics.forEach((d) => {
    (diagnosticsByName[d.name] ??= []).push(d);
  });
  const installedCount = catalog.filter((e) => e.installed).length;

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={serverDelete.isOpen}
        onCancel={serverDelete.cancel}
        onConfirm={serverDelete.confirm}
        title="Remove MCP server"
        description={
          serverDelete.pendingId
            ? `"${truncateText(serverDelete.pendingId, 40)}" — this will remove the server.`
            : "This will remove the server."
        }
        loading={serverDelete.isDeleting}
      />

      {/* Add server modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && closeCreateModal()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-mcp-title"
        >
          <div
            className={cn(
              themedBody,
              "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col",
            )}
          >
            <Button
              ghost
              size="icon"
              onClick={closeCreateModal}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-mcp-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                Add MCP server
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="mcp-name">Name</Label>
                <Input
                  id="mcp-name"
                  autoFocus
                  placeholder="my-server"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="mcp-transport">Transport</Label>
                <Select
                  id="mcp-transport"
                  value={transport}
                  onValueChange={(value) => {
                    const nextTransport = value as McpTransport;
                    setTransport(nextTransport);
                    if (nextTransport === "stdio") setBearerToken("");
                  }}
                >
                  <SelectOption value="http">HTTP/SSE</SelectOption>
                  <SelectOption value="stdio">stdio</SelectOption>
                </Select>
              </div>

              {transport === "http" ? (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor="mcp-url">URL</Label>
                    <Input
                      id="mcp-url"
                      placeholder="https://example.com/mcp"
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mcp-auth">Authentication</Label>
                    <Select
                      id="mcp-auth"
                      value={httpAuth}
                      onValueChange={(value) => {
                        const nextAuth = value as McpHttpAuth;
                        setHttpAuth(nextAuth);
                        if (nextAuth !== "header") setBearerToken("");
                      }}
                    >
                      <SelectOption value="none">None</SelectOption>
                      <SelectOption value="header">Bearer token</SelectOption>
                      <SelectOption value="oauth">OAuth</SelectOption>
                    </Select>
                  </div>
                  {httpAuth === "header" && (
                    <div className="grid gap-2">
                      <Label htmlFor="mcp-bearer-token">Bearer token</Label>
                      <Input
                        id="mcp-bearer-token"
                        type="password"
                        autoComplete="new-password"
                        placeholder="Token or Bearer token"
                        value={bearerToken}
                        onChange={(e) => setBearerToken(e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">
                        Stored in this profile&apos;s .env; config.yaml keeps
                        only an environment-variable reference.
                      </p>
                    </div>
                  )}
                  {httpAuth === "oauth" && (
                    <p className="text-xs text-muted-foreground">
                      Add the server, then use Authenticate. Qubit opens the
                      OAuth browser on the machine running the Dashboard
                      backend.
                    </p>
                  )}
                </>
              ) : (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor="mcp-command">Command</Label>
                    <Input
                      id="mcp-command"
                      placeholder="npx"
                      value={command}
                      onChange={(e) => setCommand(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mcp-args">Args</Label>
                    <Input
                      id="mcp-args"
                      placeholder="-y @modelcontextprotocol/server-foo"
                      value={args}
                      onChange={(e) => setArgs(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="mcp-env">
                      Environment (KEY=VALUE per line)
                    </Label>
                    <textarea
                      id="mcp-env"
                      className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
                      placeholder={"API_KEY=secret\nDEBUG=1"}
                      value={env}
                      onChange={(e) => setEnv(e.target.value)}
                    />
                  </div>
                </>
              )}

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleCreate}
                  disabled={creating}
                  prefix={creating ? <Spinner /> : undefined}
                >
                  {creating ? "Adding..." : "Add"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Catalog install modal (required env vars) */}
      {installEntry && (
        <div
          ref={installModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && setInstallEntry(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="install-mcp-title"
        >
          <div
            className={cn(
              themedBody,
              "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col",
            )}
          >
            <Button
              ghost
              size="icon"
              onClick={() => setInstallEntry(null)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="install-mcp-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                Install {installEntry.name}
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <p className="text-xs text-muted-foreground">
                This MCP requires the following values to be configured.
              </p>
              {installEntry.required_env.map((item) => (
                <div className="grid gap-2" key={item.name}>
                  <Label htmlFor={`install-env-${item.name}`}>
                    {item.prompt}
                    {item.required ? " *" : ""}
                  </Label>
                  <Input
                    id={`install-env-${item.name}`}
                    type="password"
                    placeholder={item.name}
                    value={installEnv[item.name] ?? ""}
                    onChange={(e) =>
                      setInstallEnv((prev) => ({
                        ...prev,
                        [item.name]: e.target.value,
                      }))
                    }
                  />
                </div>
              ))}

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleInstallSubmit}
                  disabled={installingName === installEntry.name}
                  prefix={
                    installingName === installEntry.name ? (
                      <Spinner />
                    ) : undefined
                  }
                >
                  {installingName === installEntry.name
                    ? "Installing..."
                    : "Install"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tool invocation modal (canonical confirm-gated POST /invoke) */}
      {invokeFor && (
        <div
          ref={invokeModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && closeInvokeModal()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="invoke-mcp-title"
        >
          <div
            className={cn(
              themedBody,
              "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col max-h-[90vh]",
            )}
          >
            <Button
              ghost
              size="icon"
              onClick={closeInvokeModal}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="invoke-mcp-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                Invoke — {invokeFor.name}
              </h2>
            </header>

            <div className="p-5 grid gap-4 overflow-y-auto">
              {!invokeFor.invocable && (
                <p className="text-xs text-warning">
                  This server is not currently invocable
                  {invokeFor.invocation_code
                    ? ` (${invokeFor.invocation_code})`
                    : ""}
                  . A call may be refused by policy.
                </p>
              )}

              <div className="grid gap-2">
                <Label htmlFor="invoke-tool">Tool</Label>
                {invokeFor.capabilities && invokeFor.capabilities.length > 0 ? (
                  <Select
                    id="invoke-tool"
                    value={invokeTool}
                    onValueChange={(value) => setInvokeTool(value)}
                  >
                    {invokeFor.capabilities.map((cap) => (
                      <SelectOption key={cap} value={cap}>
                        {cap}
                      </SelectOption>
                    ))}
                  </Select>
                ) : (
                  <Input
                    id="invoke-tool"
                    placeholder="tool name"
                    value={invokeTool}
                    onChange={(e) => setInvokeTool(e.target.value)}
                  />
                )}
              </div>

              <div className="grid gap-2">
                <Label htmlFor="invoke-args">Arguments (JSON object)</Label>
                <textarea
                  id="invoke-args"
                  className="flex min-h-[100px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
                  placeholder="{}"
                  value={invokeArgs}
                  onChange={(e) => setInvokeArgs(e.target.value)}
                />
              </div>

              {invokeError && (
                <p className="text-xs text-destructive break-words">
                  {invokeError}
                </p>
              )}
              {invokeResult && (
                <pre className="max-h-48 overflow-auto border border-border bg-background/40 p-2 text-xs font-mono whitespace-pre-wrap break-words">
                  {invokeResult}
                </pre>
              )}

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleInvoke}
                  disabled={invoking}
                  prefix={invoking ? <Spinner /> : <Play />}
                >
                  {invoking ? "Invoking…" : "Invoke"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Your MCP servers ── */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <H2
            variant="sm"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <Server className="h-4 w-4" />
            Your MCP servers ({servers.length})
          </H2>
        </div>

        {restartNote && <p className="text-xs text-warning">{restartNote}</p>}

        {servers.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No MCP servers configured.
            </CardContent>
          </Card>
        )}

        {servers.map((server) => {
          const envCount = Object.keys(server.env ?? {}).length;
          const result = testResults[server.name];

          return (
            <Card key={server.name}>
              <CardContent
                className={cn(
                  "flex items-start gap-4 py-4",
                  !server.enabled && "opacity-60",
                )}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm truncate">
                      {server.name}
                    </span>
                    <Badge
                      tone={TRANSPORT_TONE[server.transport] ?? "secondary"}
                    >
                      {server.transport}
                    </Badge>
                    {server.auth && (
                      <Badge tone="outline">
                        auth:{" "}
                        {server.auth === "header" ? "bearer" : server.auth}
                      </Badge>
                    )}
                    {!server.enabled && <Badge tone="outline">disabled</Badge>}
                    {server.invocable ? (
                      <Badge tone="success">invocable</Badge>
                    ) : (
                      server.invocation_code && (
                        <Badge tone="warning">{server.invocation_code}</Badge>
                      )
                    )}
                    {server.capabilities && server.capabilities.length > 0 && (
                      <Badge tone="outline">
                        {server.capabilities.length} tool
                        {server.capabilities.length === 1 ? "" : "s"}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    {server.transport === "http" ? (
                      <span className="font-mono truncate">
                        {server.url ?? "—"}
                      </span>
                    ) : (
                      <span className="font-mono truncate">
                        {[server.command, ...(server.args ?? [])]
                          .filter(Boolean)
                          .join(" ") || "—"}
                      </span>
                    )}
                    {envCount > 0 && (
                      <span>
                        {envCount} env var{envCount === 1 ? "" : "s"}
                      </span>
                    )}
                  </div>
                  {result && (
                    <div className="mt-2 text-xs">
                      {result.ok ? (
                        <p className="text-success">
                          {result.tools.length === 0
                            ? "Connected — no tools"
                            : `Tools: ${result.tools
                                .map((tool) => tool.name)
                                .join(", ")}`}
                        </p>
                      ) : (
                        <p className="text-destructive">
                          {result.error ?? "Connection failed"}
                        </p>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  {server.auth === "oauth" && (
                    <Button
                      ghost
                      size="sm"
                      title="Authenticate with OAuth"
                      onClick={() => handleAuthenticate(server)}
                      disabled={authenticating === server.name}
                      prefix={
                        authenticating === server.name ? (
                          <Spinner />
                        ) : (
                          <KeyRound />
                        )
                      }
                    >
                      Authenticate
                    </Button>
                  )}

                  <Button
                    ghost
                    size="sm"
                    title={server.enabled ? "Disable" : "Enable"}
                    aria-label={server.enabled ? "Disable" : "Enable"}
                    onClick={() => handleToggleEnabled(server)}
                    disabled={togglingName === server.name}
                    prefix={
                      togglingName === server.name ? <Spinner /> : <Power />
                    }
                    className={server.enabled ? "text-success" : undefined}
                  >
                    {server.enabled ? "Disable" : "Enable"}
                  </Button>

                  <Button
                    ghost
                    size="sm"
                    title={capOpen[server.name] ? "Hide capabilities" : "Show capabilities"}
                    aria-label={`Capabilities for ${server.name}`}
                    aria-expanded={!!capOpen[server.name]}
                    onClick={() => toggleCapabilities(server.name)}
                  >
                    Capabilities
                  </Button>

                  <Button
                    ghost
                    size="icon"
                    title="Invoke a tool"
                    aria-label="Invoke a tool"
                    onClick={() => openInvoke(server)}
                  >
                    <Play />
                  </Button>

                  <Button
                    ghost
                    size="icon"
                    title="Test connection"
                    aria-label="Test connection"
                    onClick={() => handleTest(server)}
                    disabled={testing === server.name}
                  >
                    {testing === server.name ? <Spinner /> : <Zap />}
                  </Button>

                  <Button
                    ghost
                    destructive
                    size="icon"
                    title="Delete"
                    aria-label="Delete"
                    onClick={() => serverDelete.requestDelete(server.name)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </CardContent>
              {capOpen[server.name] && (
                <CardContent className="border-t border-border py-4">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Capabilities — {server.name}
                    </p>
                    <Button
                      ghost
                      size="sm"
                      title="Reload capabilities"
                      aria-label={`Reload capabilities for ${server.name}`}
                      onClick={() => loadCapabilities(server.name)}
                      disabled={!!capLoading[server.name]}
                      prefix={
                        capLoading[server.name] ? <Spinner /> : <RefreshCw />
                      }
                    >
                      Reload
                    </Button>
                  </div>

                  {/* Tools: already served on the registry row + last test. */}
                  <div className="mt-3">
                    <p className="text-xs font-medium">Tools</p>
                    {server.capabilities && server.capabilities.length > 0 ? (
                      <ul className="mt-1 ml-3 list-disc space-y-0.5 text-xs">
                        {server.capabilities.map((tool) => (
                          <li key={`${server.name}-cap-tool-${tool}`} className="font-mono break-all">
                            {tool}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1 text-xs text-muted-foreground">
                        No tools declared on the registry row
                        {server.invocation_code
                          ? ` (${server.invocation_code})`
                          : ""}
                        . Run Test to probe the live transport.
                      </p>
                    )}
                    {result && result.ok && result.tools.length > 0 && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Last test:{" "}
                        {result.tools.map((t) => t.name).join(", ")}
                      </p>
                    )}
                  </div>

                  {(
                    [
                      { kind: "resources" as const, title: "Resources" },
                      { kind: "prompts" as const, title: "Prompts" },
                      { kind: "templates" as const, title: "Resource templates" },
                    ]
                  ).map(({ kind, title }) => {
                    const err = capErrors[server.name]?.[kind];
                    const payload = capData[server.name]?.[kind];
                    const items = capabilityItems(
                      payload,
                      kind,
                    );
                    return (
                      <div className="mt-3" key={`${server.name}-cap-${kind}`}>
                        <p className="text-xs font-medium">{title}</p>
                        {capLoading[server.name] && !payload && !err ? (
                          <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                            <Spinner /> Loading…
                          </p>
                        ) : err ? (
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            <p className="text-xs text-destructive break-words">
                              {err}
                            </p>
                            <Button
                              ghost
                              size="sm"
                              aria-label={`Retry ${title} for ${server.name}`}
                              onClick={() => loadCapabilities(server.name)}
                            >
                              Retry
                            </Button>
                          </div>
                        ) : payload && !payload.ok ? (
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            <p className="text-xs text-warning break-words">
                              {payload.code ? `${payload.code}: ` : ""}
                              {payload.detail ??
                                "server exposes none via this read"}
                              {payload.qubit_counterpart
                                ? ` — ${payload.qubit_counterpart}`
                                : ""}
                            </p>
                            <Button
                              ghost
                              size="sm"
                              aria-label={`Retry ${title} for ${server.name}`}
                              onClick={() => loadCapabilities(server.name)}
                            >
                              Retry
                            </Button>
                          </div>
                        ) : items !== null && items.length === 0 ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {payload?.code === "MCP_READ_OK"
                              ? "Connected — exposes none."
                              : "None exposed."}
                          </p>
                        ) : items !== null ? (
                          <ul className="mt-1 ml-3 list-disc space-y-0.5 text-xs">
                            {items.map((item, i) => {
                              const { primary, secondary } =
                                capabilityLabel(item);
                              return (
                                <li
                                  key={`${server.name}-cap-${kind}-${i}`}
                                  className="break-all"
                                >
                                  <code className="font-mono">{primary}</code>
                                  {secondary && (
                                    <span className="text-muted-foreground">
                                      {" "}
                                      — {secondary}
                                    </span>
                                  )}
                                </li>
                              );
                            })}
                          </ul>
                        ) : null}
                      </div>
                    );
                  })}
                </CardContent>
              )}
            </Card>
          );
        })}
      </div>

      {/* ── Catalog ── */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <H2
            variant="sm"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <Package className="h-4 w-4" />
            Catalog ({catalog.length})
          </H2>
        </div>

        <p className="text-xs text-muted-foreground">
          Browse Qubit-approved MCP servers and install them with one click.
          {" "}
          {installedCount} installed ·{" "}
          {Math.max(0, catalog.length - installedCount)} available.
        </p>

        {catalog.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No catalog entries available.
            </CardContent>
          </Card>
        )}

        {catalog.map((entry) => {
          const entryDiags = diagnosticsByName[entry.name] ?? [];
          const isInstalling = installingName === entry.name;

          return (
            <Card key={entry.name}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-medium text-sm truncate">
                      {entry.name}
                    </span>
                    <Badge
                      tone={TRANSPORT_TONE[entry.transport] ?? "secondary"}
                    >
                      {entry.transport}
                    </Badge>
                    <Badge tone="outline">auth: {entry.auth_type}</Badge>
                    {isHttpUrl(entry.source) ? (
                      <a
                        href={entry.source}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-primary underline underline-offset-2 hover:opacity-80"
                      >
                        source ↗
                      </a>
                    ) : (
                      entry.source && (
                        <Badge tone="outline">{entry.source}</Badge>
                      )
                    )}
                    {entry.installed && <Badge tone="success">Installed</Badge>}
                    {entry.installed && !entry.enabled && (
                      <Badge tone="outline">disabled</Badge>
                    )}
                  </div>
                  {entry.description && (
                    <p className="text-xs text-muted-foreground">
                      {rebrand(entry.description)}
                    </p>
                  )}
                  {/* Connection detail: what the agent actually talks to. */}
                  {entry.transport === "http" && entry.url && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      <span className="font-medium">Endpoint:</span>{" "}
                      <code className="font-mono">{entry.url}</code>
                    </p>
                  )}
                  {entry.transport === "stdio" && entry.command && (
                    <p className="mt-1 text-xs text-muted-foreground break-all">
                      <span className="font-medium">Runs:</span>{" "}
                      <code className="font-mono">
                        {[entry.command, ...entry.args].join(" ")}
                      </code>
                    </p>
                  )}
                  {/* Git bootstrap — surfaced so users see what gets cloned/run
                      before they install (matches the docs trust model). */}
                  {entry.install_url && (
                    <p className="mt-1 text-xs text-muted-foreground break-all">
                      <span className="font-medium">Installs from:</span>{" "}
                      {isHttpUrl(entry.install_url) ? (
                        <a
                          href={entry.install_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline underline-offset-2 hover:opacity-80"
                        >
                          {entry.install_url}
                        </a>
                      ) : (
                        <code className="font-mono">{entry.install_url}</code>
                      )}
                      {entry.install_ref && <span> @ {entry.install_ref}</span>}
                    </p>
                  )}
                  {entry.bootstrap.length > 0 && (
                    <details className="mt-1 text-xs text-muted-foreground">
                      <summary className="cursor-pointer select-none">
                        Bootstrap commands ({entry.bootstrap.length})
                      </summary>
                      <ul className="mt-1 ml-3 list-disc space-y-0.5">
                        {entry.bootstrap.map((cmd, i) => (
                          <li
                            key={`${entry.name}-bs-${i}`}
                            className="break-all"
                          >
                            <code className="font-mono">{cmd}</code>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {entry.post_install && (
                    <details className="mt-1 text-xs text-muted-foreground">
                      <summary className="cursor-pointer select-none">
                        Setup notes
                      </summary>
                      <p className="mt-1 whitespace-pre-wrap">
                        {rebrand(entry.post_install.trim())}
                      </p>
                    </details>
                  )}
                  {entryDiags.map((d, i) => (
                    <p
                      key={`${entry.name}-diag-${i}`}
                      className="text-xs text-warning mt-1"
                    >
                      {rebrand(d.message)}
                    </p>
                  ))}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  {entry.installed ? (
                    <Badge tone="success">Installed</Badge>
                  ) : (
                    <Button
                      className="uppercase"
                      size="sm"
                      onClick={() => handleInstallClick(entry)}
                      disabled={isInstalling}
                      prefix={isInstalling ? <Spinner /> : undefined}
                    >
                      {isInstalling ? "Installing..." : "Install"}
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
