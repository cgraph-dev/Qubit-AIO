/**
 * Agent workspace browser (route /agent-workspace). Read-only file roster from
 * `GET /api/qubit/agent-workspace/list` (agents.workspace.list), single-file
 * read via `GET /api/qubit/agent-workspace/get` (agents.workspace.get), and
 * run status via `GET /api/qubit/agent-workspace/run-status` (agent.wait).
 * Workspace create/edit/delete is a gated capability, disclosed honestly below.
 *
 * Backend shape reality (verified live): `list` answers
 * `{ state, agentId, path, entries: [{ path, name, kind, size }] }` — the
 * canonical entry roster — while the family client historically typed a
 * `files: [{ path, size_bytes, readable }]` shape. `normalizeWorkspaceFiles`
 * accepts BOTH so the roster renders against the live backend and against the
 * typed family contract, without dropping a row.
 *
 * Layout carries the shipped upstream console vocabulary: a `settings-page`
 * root, a `settings-section` files panel and run panel switched by a tab bar,
 * `settings-group`/`settings-row` rows, and `settings-row__control` emitted
 * ONLY when a control exists.
 */
import { useCallback, useEffect, useState } from "react";

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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/**
 * Normalize the workspace list envelope into the typed roster.
 *
 * Accepts the live backend roster (`entries: [{ path, name, kind, size }]`)
 * as well as the typed family contract (`files: [{ path, size_bytes, readable }]`).
 * `readable` defaults to true; only an explicit `readable: false` (or a
 * directory entry) marks a row unreadable, with its stated reason.
 */
export function normalizeWorkspaceFiles(payload: unknown): WorkspaceFile[] {
  const env = asRecord(payload);
  const inner = asRecord(env.result ?? env);
  const raw = Array.isArray(inner.entries)
    ? inner.entries
    : Array.isArray(inner.files)
      ? inner.files
      : [];
  return raw.map((item) => {
    const row = asRecord(item);
    const kind = typeof row.kind === "string" ? row.kind : "";
    const isDir = kind === "dir" || kind === "directory";
    const path =
      typeof row.path === "string" && row.path.length > 0
        ? row.path
        : typeof row.name === "string"
          ? row.name
          : "";
    const size =
      typeof row.size_bytes === "number"
        ? row.size_bytes
        : typeof row.size === "number"
          ? row.size
          : undefined;
    const readable = isDir ? false : row.readable === false ? false : true;
    const reason =
      typeof row.unreadable_reason === "string" && row.unreadable_reason.length > 0
        ? row.unreadable_reason
        : isDir
          ? "Directories cannot be opened from the dashboard."
          : undefined;
    return { path, size_bytes: size, readable, unreadable_reason: reason };
  });
}

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
        const env = res as unknown as Record<string, unknown>;
        setFiles(normalizeWorkspaceFiles(env));
        setAuthority(typeof env.authority === "string" ? env.authority : "");
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
    <main className="qb-upstream settings-page settings-page--wide" aria-label="Agent workspace">
      <section aria-label="Workspace toolbar" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Agent workspace</h2>
            <p className="settings-section__desc">
              Browse the agent workspace files and check run status.
            </p>
          </div>
          <div className="settings-section__actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={load}
              disabled={state === "loading"}
              aria-label="Refresh workspace"
            >
              {state === "loading" ? <Spinner /> : "refresh"}
            </button>
          </div>
        </div>
        <div className="settings-section__actions" role="tablist" aria-label="Workspace panels">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "files"}
            aria-label="Show workspace files panel"
            onClick={() => setTab("files")}
            className={cn("btn btn--ghost btn--sm", tab === "files" && "active")}
          >
            Files{state === "ready" ? ` (${files.length})` : ""}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "run"}
            aria-label="Show run status panel"
            onClick={() => setTab("run")}
            className={cn("btn btn--ghost btn--sm", tab === "run" && "active")}
          >
            Run status
          </button>
        </div>
      </section>

      {tab === "files" ? (
        <section aria-label="Workspace files panel" className="settings-section">
          <div className="settings-section__header">
            <div className="settings-section__copy">
              <h2 className="settings-section__heading">
                Workspace files
                {state === "ready" ? <span className="settings-count">{files.length}</span> : null}
              </h2>
              <p className="settings-section__desc">
                {authority || "Read-only file roster for this agent workspace."}
              </p>
            </div>
          </div>
          <div className="settings-group" role="group">
            {state === "loading" ? (
              <div className="settings-loading-skeleton" aria-hidden="true">
                <div className="settings-loading-skeleton__rows">
                  {[0, 1, 2].map((i) => (
                    <div className="settings-loading-skeleton__row" key={i}>
                      <div className="settings-loading-skeleton__title" />
                      <div className="settings-loading-skeleton__description" />
                    </div>
                  ))}
                </div>
              </div>
            ) : state === "error" ? (
              <div role="alert" className="callout danger">
                {error}
              </div>
            ) : files.length === 0 ? (
              <div className="settings-empty" aria-label="Workspace files">
                No workspace files reported.
              </div>
            ) : (
              <ul className="settings-subrows" aria-label="Workspace files">
                {files.map((f) => (
                  <li key={f.path} className="settings-row">
                    <div className="settings-row__text">
                      <span className="settings-row__title">
                        <span className="settings-row__value settings-row__value--mono">{f.path}</span>
                      </span>
                      {typeof f.size_bytes === "number" ? (
                        <span className="settings-row__desc">{f.size_bytes} bytes</span>
                      ) : null}
                      {f.readable ? null : (
                        <span className="settings-row__desc">
                          {f.unreadable_reason ?? "This file cannot be opened from the dashboard."}
                        </span>
                      )}
                    </div>
                    <div className="settings-row__control">
                      {f.readable ? (
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => openFile(f.path)}
                          disabled={opening && openPath === f.path}
                          aria-label={`Open file ${f.path}`}
                        >
                          {opening && openPath === f.path ? <Spinner /> : "open"}
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled
                          data-state="refused"
                          title={f.unreadable_reason ?? "This file cannot be opened from the dashboard."}
                          aria-label={`Open file ${f.path} unavailable`}
                          aria-disabled="true"
                          className="btn btn--ghost btn--sm"
                        >
                          open unavailable
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {openPath ? (
            <div className="settings-group">
              <div className="settings-row settings-row--stacked">
                <div className="settings-row__text">
                  <span className="settings-row__title settings-row__value--mono">{openPath}</span>
                  {opening && openPath ? (
                    <span className="settings-row__desc flex items-center gap-2">
                      <Spinner className="shrink-0" /> Opening…
                    </span>
                  ) : (
                    <span className="settings-row__desc">
                      {openTruncated ? "Truncated by the backend read limit." : "Read-only preview."}
                    </span>
                  )}
                </div>
                <div className="settings-row__control">
                  <pre className="settings-kv w-full whitespace-pre-wrap">
                    {openContent === null ? "Opening…" : openContent}
                  </pre>
                </div>
              </div>
            </div>
          ) : null}
        </section>
      ) : (
        <section aria-label="Agent run status" className="settings-section">
          <div className="settings-section__header">
            <div className="settings-section__copy">
              <h2 className="settings-section__heading">Run status</h2>
              <p className="settings-section__desc">Look up a single agent run by id.</p>
            </div>
          </div>
          <div className="settings-group">
            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <span className="settings-row__title">Run id</span>
                <span className="settings-row__desc">Query the backend for one run's state.</span>
              </div>
              <div className="settings-row__control">
                <input
                  type="text"
                  value={runId}
                  onChange={(e) => setRunId(e.target.value)}
                  placeholder="run id"
                  aria-label="Run id"
                  className="settings-input"
                />
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={checkRun}
                  disabled={runState === "loading" || !runId.trim()}
                  aria-label="Check run status"
                >
                  {runState === "loading" ? <Spinner /> : "check"}
                </button>
              </div>
            </div>
            {runState === "error" ? (
              <div role="alert" className="callout danger">
                {runError}
              </div>
            ) : runStatus ? (
              <div className="settings-row">
                <div className="settings-row__text">
                  <span className="settings-row__title">
                    <span className="settings-row__value settings-row__value--mono">
                      {runStatus.run_id}
                    </span>
                    {`: ${runStatus.state}`}
                  </span>
                  {runStatus.detail ? (
                    <span className="settings-row__desc">{runStatus.detail}</span>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </section>
      )}

      {/* Workspace create / edit / delete is a gated capability: the dashboard
          owns no workspace-mutation API, so this is disclosed honestly rather
          than shown as dead controls. */}
      <section data-state="gated" aria-label="Workspace mutations" className="settings-section">
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <span className="settings-row__title">Workspace mutations</span>
              <span className="settings-row__desc">
                Creating, editing, and deleting workspace files is managed through the Qubit
                agent session that owns the workspace. This dashboard exposes read-only browse
                and run status above.
              </span>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
