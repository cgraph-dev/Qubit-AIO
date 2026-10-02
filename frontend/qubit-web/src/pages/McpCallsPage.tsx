/**
 * MCP calls (`/admin/mcp-calls`).
 *
 * The direct-call half of the MCP surface the main MCP page does not
 * duplicate: invoke one tool on a configured server (server + tool +
 * args, confirm-gated), list resources, and read one resource by URI.
 * Resource templates and model-context updates are policy-refused backend
 * operations: they render disabled with the exact prerequisite.
 */
import { useState } from "react";
import {
  DISABLED_MCP_ACTIONS,
  callMcpTool,
  listMcpResources,
  readMcpResource,
  type McpCallResponse,
  type McpResourceReadResponse,
  type McpResourcesResponse,
} from "@/lib/ocFamilyAdminApi";
import { refusalOrError } from "@/lib/policy-refusal";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, `${fallback}: ${e instanceof Error ? e.message : String(e)}`);
}

export default function McpCallsPage() {
  const [server, setServer] = useState("");
  const [tool, setTool] = useState("");
  const [argsText, setArgsText] = useState("{}");
  const [invoking, setInvoking] = useState(false);
  const [callResult, setCallResult] = useState<McpCallResponse | null>(null);

  const [resServer, setResServer] = useState("");
  const [resources, setResources] = useState<McpResourcesResponse | null>(null);
  const [resLoading, setResLoading] = useState(false);

  const [readUri, setReadUri] = useState("");
  const [readResult, setReadResult] = useState<McpResourceReadResponse | null>(null);
  const [readLoading, setReadLoading] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const parseArgs = (): Record<string, unknown> | null => {
    try {
      const v: unknown = JSON.parse(argsText.trim() || "{}");
      if (v && typeof v === "object" && !Array.isArray(v)) {
        return v as Record<string, unknown>;
      }
      return null;
    } catch {
      return null;
    }
  };

  const handleCall = async () => {
    const args = parseArgs();
    if (!server.trim() || !tool.trim()) {
      setError("Call needs a server and a tool name");
      return;
    }
    if (!args) {
      setError("Args must be a JSON object");
      return;
    }
    setInvoking(true);
    setError(null);
    setNotice(null);
    setCallResult(null);
    try {
      const res = await callMcpTool(
        { server: server.trim(), tool: tool.trim(), args },
        CONFIRM_YES,
      );
      setCallResult(res);
      setNotice(`${server.trim()}/${tool.trim()}: ${res.state ?? "OK"}`);
    } catch (e) {
      setError(errText(e, "Tool call failed"));
    } finally {
      setInvoking(false);
    }
  };

  const handleListResources = async () => {
    if (!resServer.trim()) {
      setError("Resource list needs a server name");
      return;
    }
    setResLoading(true);
    setError(null);
    setResources(null);
    try {
      setResources(await listMcpResources(resServer.trim()));
    } catch (e) {
      setError(errText(e, "Resource list failed"));
    } finally {
      setResLoading(false);
    }
  };

  const handleReadResource = async () => {
    if (!resServer.trim() || !readUri.trim()) {
      setError("Resource read needs a server and a URI");
      return;
    }
    setReadLoading(true);
    setError(null);
    setReadResult(null);
    try {
      setReadResult(await readMcpResource(resServer.trim(), readUri.trim()));
    } catch (e) {
      setError(errText(e, "Resource read failed"));
    } finally {
      setReadLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4" data-testid="mcp-calls-page">
      <header>
        <h1 className="text-base font-semibold">MCP calls</h1>
        <p className="text-xs text-muted-foreground">
          Call one tool on a configured server, list resources, read one
          resource. Tool calls are confirm-gated; button press confirms.
        </p>
      </header>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-success">{notice}</p>}

      {/* ── Call tool ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Call tool</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="mc-server" className="text-xs text-muted-foreground">
            Server
          </label>
          <input
            id="mc-server"
            data-testid="mc-server"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            placeholder="server name"
            value={server}
            onChange={(e) => setServer(e.target.value)}
          />
          <label htmlFor="mc-tool" className="text-xs text-muted-foreground">
            Tool
          </label>
          <input
            id="mc-tool"
            data-testid="mc-tool"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            placeholder="tool name"
            value={tool}
            onChange={(e) => setTool(e.target.value)}
          />
        </div>
        <label htmlFor="mc-args" className="text-xs text-muted-foreground">
          Arguments (JSON object)
        </label>
        <textarea
          id="mc-args"
          data-testid="mc-args"
          className="min-h-[80px] border border-border bg-background px-2 py-1 font-mono text-xs"
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
        />
        <div>
          <button
            type="button"
            data-testid="mc-invoke"
            className="border border-border px-3 py-1 text-xs uppercase"
            disabled={invoking}
            onClick={() => void handleCall()}
          >
            {invoking ? "Invoking…" : "Invoke"}
          </button>
        </div>
        {callResult && (
          <pre
            data-testid="mc-result"
            className="max-h-64 overflow-auto border border-border bg-background/40 p-2 font-mono text-xs"
          >
            {JSON.stringify(callResult, null, 2)}
          </pre>
        )}
      </section>

      {/* ── List / read resources ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Resources</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="mc-res-server" className="text-xs text-muted-foreground">
            Server
          </label>
          <input
            id="mc-res-server"
            data-testid="mc-res-server"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            placeholder="server name"
            value={resServer}
            onChange={(e) => setResServer(e.target.value)}
          />
          <button
            type="button"
            data-testid="mc-list-resources"
            className="border border-border px-2 py-1 text-xs"
            disabled={resLoading}
            onClick={() => void handleListResources()}
          >
            List resources
          </button>
        </div>
        {resLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
        {resources && (
          <ul data-testid="mc-resources-list" className="flex flex-col gap-1">
            {(resources.resources ?? []).length === 0 ? (
              <li className="text-xs text-muted-foreground">
                {resources.code ? `${resources.code}: ` : ""}
                {typeof resources.state === "string" ? `${resources.state} — ` : ""}
                No resources exposed.
              </li>
            ) : (
              (resources.resources ?? []).map((r, i) => (
                <li key={i} className="border border-border px-2 py-1 font-mono text-xs">
                  {JSON.stringify(r)}
                </li>
              ))
            )}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="mc-read-uri" className="text-xs text-muted-foreground">
            URI
          </label>
          <input
            id="mc-read-uri"
            data-testid="mc-read-uri"
            className="h-8 flex-1 border border-border bg-background px-2 font-mono text-xs"
            placeholder="resource URI"
            value={readUri}
            onChange={(e) => setReadUri(e.target.value)}
          />
          <button
            type="button"
            data-testid="mc-read-resource"
            className="border border-border px-2 py-1 text-xs"
            disabled={readLoading}
            onClick={() => void handleReadResource()}
          >
            Read resource
          </button>
        </div>
        {readLoading && <p className="text-xs text-muted-foreground">Reading…</p>}
        {readResult && (
          <pre
            data-testid="mc-read-result"
            className="max-h-64 overflow-auto border border-border bg-background/40 p-2 font-mono text-xs"
          >
            {JSON.stringify(readResult.contents ?? readResult, null, 2)}
          </pre>
        )}
      </section>

      {/* ── Policy-refused operations ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Unavailable by policy</h2>
        <p className="text-xs text-muted-foreground">
          No in-tree capability exposes resource templates, and Qubit runs
          no MCP App sandbox. These stay disabled; the reason names the
          exact prerequisite.
        </p>
        <ul className="flex flex-col gap-2">
          {DISABLED_MCP_ACTIONS.map((d) => (
            <li
              key={d.action}
              data-testid={`mc-disabled-${d.action}`}
              className="flex flex-col gap-1 border border-border px-2 py-1.5 opacity-70"
            >
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-xs">{d.action}</code>
                <button
                  type="button"
                  disabled
                  title={d.reason}
                  aria-disabled="true"
                  className="cursor-not-allowed border border-border px-2 py-1 text-xs"
                >
                  Disabled
                </button>
              </div>
              <p className="text-xs text-muted-foreground" data-testid={`mc-disabled-reason-${d.action}`}>
                Prerequisite: {d.reason}. Counterpart: {d.counterpart}.
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
