/**
 * Agent workspace browser (route /agent-workspace). Read-only file roster from
 * `GET /api/qubit/agent-workspace/list` (agents.workspace.list), single-file
 * read via `GET /api/qubit/agent-workspace/get` (agents.workspace.get), and
 * run status via `GET /api/qubit/agent-workspace/run-status` (agent.wait).
 * Workspace create/edit/delete is a gated capability, disclosed honestly below.
 *
 * Layout mirrors the upstream agents view hierarchy (toolbar + tab bar with
 * counts + files panel + status panel), translated into Qubit voice. Tabs are
 * presentational only: every backend call, confirm-gating, and
 * disabled-with-reason row is wired exactly as before.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  getAgentRunStatus,
  getWorkspaceFile,
  listWorkspaceFiles,
  type AgentRunStatus,
  type WorkspaceFile,
} from "@/lib/ocFamilyAgentsApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn } from "@/lib/utils";

type WorkspaceTab = "files" | "run";

export default function AgentWorkspacePage() {
  const [tab, setTab] = useState<WorkspaceTab>("files");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [authority, setAuthority] = useState<string>("");
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [openContent, setOpenContent] = useState<string | null>(null);
  const [openTruncated, setOpenTruncated] = useState(false);
  const [opening, setOpening] = useState(false);
  const [runId, setRunId] = useState("");
  const [runStatus, setRunStatus] = useState<AgentRunStatus | null>(null);
  const [runState, setRunState] = useState<"idle" | "loading" | "error">("idle");
  const [runError, setRunError] = useState<string | null>(null);

  const load = useCallback(() => {
    setState("loading");
    setError(null);
    listWorkspaceFiles()
      .then((res) => {
        setFiles(res.files ?? []);
        setAuthority(res.authority ?? "");
        setState("ready");
      })
      .catch((err: unknown) => {
        setError(refusalOrError(err, "Workspace list failed."));
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openFile = useCallback((path: string) => {
    setOpening(true);
    setOpenPath(path);
    setOpenContent(null);
    setOpenTruncated(false);
    getWorkspaceFile(path)
      .then((res) => {
        setOpenContent(res.content);
        setOpenTruncated(res.truncated === true);
      })
      .catch((err: unknown) => {
        setOpenContent(refusalOrError(err, "File open failed."));
      })
      .finally(() => setOpening(false));
  }, []);

  const checkRun = useCallback(() => {
    const id = runId.trim();
    if (!id) return;
    setRunState("loading");
    setRunError(null);
    setRunStatus(null);
    getAgentRunStatus(id)
      .then((res) => {
        setRunStatus(res);
        setRunState("idle");
      })
      .catch((err: unknown) => {
        setRunError(refusalOrError(err, "Run status failed."));
        setRunState("error");
      });
  }, [runId]);

  return (
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">Agent workspace</h2>
          <p className="text-xs text-text-tertiary">
            Browse the agent workspace files and check run status.
          </p>
        </div>
        <Button
          ghost
          size="sm"
          onClick={load}
          disabled={state === "loading"}
          aria-label="Refresh workspace"
        >
          {state === "loading" ? <Spinner /> : "refresh"}
        </Button>
      </div>

      <div role="tablist" aria-label="Workspace panels" className="flex items-center gap-1">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "files"}
          aria-label="Show workspace files panel"
          onClick={() => setTab("files")}
          className={cn(
            "border px-2 py-1 text-xs",
            tab === "files"
              ? "border-border bg-background-base text-text-secondary"
              : "border-transparent text-text-tertiary",
          )}
        >
          Files{state === "ready" ? ` (${files.length})` : ""}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "run"}
          aria-label="Show run status panel"
          onClick={() => setTab("run")}
          className={cn(
            "border px-2 py-1 text-xs",
            tab === "run"
              ? "border-border bg-background-base text-text-secondary"
              : "border-transparent text-text-tertiary",
          )}
        >
          Run status
        </button>
      </div>

      {tab === "files" ? (
        <section
          aria-label="Workspace files panel"
          className="border border-border bg-background-base/60 px-3 py-2"
        >
          <div className="flex items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-medium text-text-secondary">Workspace files</h3>
              <p className="text-[0.6875rem] text-text-tertiary">
                {authority || "Read-only file roster for this agent workspace."}
              </p>
            </div>
          </div>

          <div className="mt-2">
            {state === "loading" ? (
              <p className="flex items-center gap-2 text-sm text-text-tertiary">
                <Spinner className="shrink-0" /> Loading workspace…
              </p>
            ) : state === "error" ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : files.length === 0 ? (
              <p className="text-sm text-text-tertiary">No workspace files reported.</p>
            ) : (
              <ul className="flex flex-col gap-1" aria-label="Workspace files">
                {files.map((f) => (
                  <li
                    key={f.path}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border bg-background-base/60 px-3 py-2 text-xs"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-text-secondary">{f.path}</div>
                      {typeof f.size_bytes === "number" ? (
                        <div className="text-text-tertiary">{f.size_bytes} bytes</div>
                      ) : null}
                    </div>
                    <div className="ml-auto flex items-center gap-1">
                      {f.readable ? (
                        <Button
                          ghost
                          size="sm"
                          onClick={() => openFile(f.path)}
                          disabled={opening && openPath === f.path}
                          aria-label={`Open file ${f.path}`}
                        >
                          {opening && openPath === f.path ? <Spinner /> : "open"}
                        </Button>
                      ) : (
                        <button
                          type="button"
                          disabled
                          data-state="refused"
                          title={f.unreadable_reason ?? "This file cannot be opened from the dashboard."}
                          aria-label={`Open file ${f.path} unavailable`}
                          aria-disabled="true"
                          className="cursor-not-allowed border border-border px-2 py-1 text-xs text-text-tertiary opacity-60"
                        >
                          open unavailable
                        </button>
                      )}
                    </div>
                    {f.readable ? null : (
                      <span className="w-full text-text-tertiary">
                        {f.unreadable_reason ?? "This file cannot be opened from the dashboard."}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {openPath ? (
            <section
              aria-label="Open workspace file"
              className="mt-2 border border-border bg-background-base/60 px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-mono text-xs text-text-secondary">{openPath}</h3>
                {opening && openPath ? <Spinner className="shrink-0" /> : null}
              </div>
              {openContent === null ? (
                <p className="flex items-center gap-2 text-xs text-text-tertiary">
                  <Spinner className="shrink-0" /> Opening…
                </p>
              ) : (
                <>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-text-secondary">
                    {openContent}
                  </pre>
                  {openTruncated ? (
                    <p className="mt-1 text-[0.6875rem] text-text-tertiary">
                      Truncated by the backend read limit.
                    </p>
                  ) : null}
                </>
              )}
            </section>
          ) : null}
        </section>
      ) : (
        <section
          aria-label="Agent run status"
          className="border border-border bg-background-base/60 px-3 py-2"
        >
          <h3 className="text-xs font-medium text-text-secondary">Run status</h3>
          <p className="text-[0.6875rem] text-text-tertiary">
            Look up a single agent run by id.
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
              placeholder="run id"
              aria-label="Run id"
              className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
            />
            <Button
              ghost
              size="sm"
              onClick={checkRun}
              disabled={runState === "loading" || !runId.trim()}
              aria-label="Check run status"
            >
              {runState === "loading" ? <Spinner /> : "check"}
            </Button>
          </div>
          {runState === "error" ? (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {runError}
            </p>
          ) : runStatus ? (
            <p className="mt-1 font-mono text-xs text-text-secondary">
              {runStatus.run_id}: {runStatus.state}
              {runStatus.detail ? ` — ${runStatus.detail}` : ""}
            </p>
          ) : null}
        </section>
      )}

      {/* Workspace create / edit / delete is a gated capability: the dashboard
          owns no workspace-mutation API, so this is disclosed honestly rather
          than shown as dead controls. */}
      <div
        data-state="gated"
        className="border border-border bg-background-base/40 px-3 py-2 text-xs text-text-tertiary"
      >
        <p>
          Creating, editing, and deleting workspace files is managed through the
          Qubit agent session that owns the workspace.
        </p>
        <p className="mt-1">This dashboard exposes read-only browse and run status above.</p>
      </div>
    </div>
  );
}
