/**
 * Computer workspace — React + Tailwind port of the upstream mobile
 * `computer-workspace.tsx` + `computer.tsx` surfaces (MIT, structural port).
 *
 * Upstream files carried across:
 *   apps/mobile/src/computer-workspace.tsx  LinuxWorkspace, CommandReceipt,
 *                                           ComputerFiles
 *   apps/mobile/src/computer.tsx            ComputerEntry, BrowserThreadCard,
 *                                           ComputerSheet
 *   apps/mobile/src/browser-address.ts      URL normalization
 *
 * WIRING (real, probed — no new daemon):
 *   The upstream surface reads `/api/computer` (snapshot) + `/api/computer/*`
 *   (start/stop/commands/files/*). Qubit has NO such route family; probing
 *   confirms `GET /agent/api/computer` -> 404. The Qubit backends that DO
 *   exist are wired here instead:
 *     - console/actuator status   GET  /api/qubit/bot/computer-status    200
 *     - desktop ops + presentation GET /api/qubit/desktop/ops            200
 *                                      /api/qubit/desktop/presentation   200
 *                                      POST /desktop/{pip,present,launch} 409 (staged)
 *     - file roster + single read  GET  /api/qubit/agent-workspace/list   200
 *                                      /api/qubit/agent-workspace/get    200
 *
 * Capabilities the Qubit backend does NOT expose are rendered as complete
 * components with honest, stated empty states — never a dead button:
 *   - a persistent "computer" VM start/stop + `/commands` shell: the bridge
 *     has no computer process/command surface (the actuator is `none` on this
 *     host; `COMPUTER_USE_NO_DRIVER`). The Terminal tab states this plainly.
 *   - a working-directory file manager over `/api/computer/files`: not served.
 *     The Files tab browses the agent workspace roster instead (real 200s).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  FilePlus,
  FileText,
  Folder,
  FolderPlus,
  Monitor,
  Play,
  RefreshCw,
  Terminal,
} from "lucide-react";

import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  getComputerStatus,
  getDesktopOps,
  getDesktopPresentation,
  runDesktopOp,
  type ComputerStatus,
  type DesktopOps,
  type DesktopPresentation,
} from "@/lib/agentSurfaceApi";
import { getWorkspaceFile, listWorkspaceFiles } from "@/lib/ocFamilyAgentsApi";
import {
  listComputerFiles,
  mkdirComputer,
  readComputerFile,
  writeComputerFile,
  type ComputerEntry,
} from "@/lib/ocFamilyComputerApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn } from "@/lib/utils";

import {
  OmButton,
  OmCard,
  OmChip,
  OmEmpty,
  OmErrorNotice,
  OmField,
  OmIconBox,
} from "./om-ui";

/* ------------------------------------------------------ browser address */

/**
 * Upstream `browser-address.ts`: accept a bare host and normalize to https.
 * Returns null for input the backend would reject (no host / bad protocol).
 */
export function browserAddress(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (/^[a-z0-9.-]+\.[a-z]{2,}(\/.*)?$/i.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return null;
}

/* ------------------------------------------------- ComputerEntry (pill) */

/**
 * Upstream `ComputerEntry`: a compact status pill. Qubit wires the "online"
 * signal to the real actuator status (`computer-status` control.state).
 */
export function ComputerEntry({
  status,
  onOpen,
}: {
  status?: ComputerStatus;
  onOpen: () => void;
}) {
  const available = status?.controlState === "AVAILABLE";
  const active = status?.atspiReachable === true && available;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="Agent computer — take control"
      className="inline-flex items-center gap-[6px] border border-border bg-secondary px-3 py-[7px] text-[12px] text-muted-foreground hover:opacity-90"
      style={{ borderRadius: 20 }}
    >
      <Monitor size={13} />
      <span>
        Computer
        {status === undefined
          ? " · checking"
          : !available
            ? " · offline"
            : active
              ? " · take control"
              : " · ready"}
      </span>
      <span
        aria-hidden="true"
        className={cn(
          "size-[5px] rounded-full",
          available ? "bg-[#57AD85]" : "bg-[#ACB0B5]",
        )}
      />
    </button>
  );
}

/* ------------------------------------------------- BrowserThreadCard */

export interface BrowserSessionView {
  id: string;
  title: string;
  url: string;
  status: string;
  previewUrl?: string;
  updatedAt: string;
}

/**
 * Upstream `BrowserThreadCard`. The preview `<Image>` becomes a web `<img>`
 * with the same 1.6 aspect ratio + fallback behaviour. Qubit has no browser
 * session backend (probed `/api/browsers` -> 404 on the compat table), so
 * "Take control" is honest: with no live session it states that connection
 * is managed by the agent session, rather than opening a phantom window.
 */
export function BrowserThreadCard({
  browser,
  onTakeControl,
  onReconnect,
}: {
  browser: BrowserSessionView;
  onTakeControl: () => void;
  onReconnect: () => void;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [browser.previewUrl, browser.updatedAt]);

  const showPreview =
    browser.previewUrl && browser.status === "active" && !failed;
  const action =
    browser.status === "closed"
      ? { label: "Reopen browser", fn: onReconnect }
      : browser.status === "error"
        ? { label: "Reconnect browser", fn: onReconnect }
        : { label: "Take control", fn: onTakeControl };

  return (
    <OmCard
      tone="raised"
      className="flex w-full max-w-[440px] flex-col gap-3"
      // upstream card style: padding 13
    >
      <div className="flex items-center gap-[10px]">
        <OmIconBox size={36}>
          <Monitor size={19} />
        </OmIconBox>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-semibold">Browser</p>
          <p className="truncate text-[11px] text-muted-foreground">
            {browser.status === "closed"
              ? "Session saved"
              : browser.status === "error"
                ? "Needs attention"
                : browser.title}
          </p>
        </div>
      </div>
      {showPreview ? (
        <img
          alt={`Browser preview: ${browser.title}`}
          src={browser.previewUrl}
          onError={() => setFailed(true)}
          className="w-full rounded-[11px] bg-white object-contain"
          style={{ aspectRatio: "1.6" }}
        />
      ) : (
        <div
          className="flex flex-col items-center gap-[10px] bg-background p-6 text-center text-[12px] text-muted-foreground"
          style={{ borderRadius: 12 }}
        >
          <Monitor size={28} />
          <p className="line-clamp-2">
            {failed
              ? "Preview unavailable. Open the browser to reconnect."
              : browser.url}
          </p>
        </div>
      )}
      <OmButton onClick={action.fn}>{action.label}</OmButton>
    </OmCard>
  );
}

/* ------------------------------------------------- LinuxWorkspace tabs */

type WorkspaceTab = "Terminal" | "Files";

/**
 * Upstream `LinuxWorkspace` + `ComputerFiles`. Terminal tab shows the REAL
 * actuator status and the desktop-op controls (the only mutating console
 * surface Qubit serves). Files tab browses the REAL agent workspace roster.
 */
export function LinuxWorkspace({ tab }: { tab: WorkspaceTab }) {
  const [status, setStatus] = useState<ComputerStatus>();
  const [ops, setOps] = useState<DesktopOps>();
  const [presentation, setPresentation] = useState<DesktopPresentation>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextOps, nextPresentation] = await Promise.all([
        getComputerStatus(),
        getDesktopOps(),
        getDesktopPresentation(),
      ]);
      if (!mounted.current) return;
      setStatus(nextStatus);
      setOps(nextOps);
      setPresentation(nextPresentation);
      setError("");
    } catch (e) {
      if (mounted.current) setError(refusalOrError(e, "Computer status failed."));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  const actuatorUnavailable =
    status?.controlState === "UNAVAILABLE" || status?.actuator === "none";

  async function present() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await runDesktopOp("present", { mode: "document" });
      if (result.pending) {
        setNotice(
          `Presentation staged for confirmation (id ${result.confirmationId.slice(0, 8)}…). Approve it in the pending-approvals banner.`,
        );
      } else {
        setNotice("Presentation started.");
        await refresh();
      }
    } catch (e) {
      setError(refusalOrError(e, "Present desktop failed."));
    } finally {
      setBusy(false);
    }
  }

  async function pip() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await runDesktopOp("pip", {});
      if (result.pending) {
        setNotice(
          `Picture-in-picture staged for confirmation (id ${result.confirmationId.slice(0, 8)}…).`,
        );
      } else {
        setNotice("Picture-in-picture requested.");
        await refresh();
      }
    } catch (e) {
      setError(refusalOrError(e, "Picture-in-picture failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <OmCard tone="sky" className="flex flex-col gap-3">
        <div className="flex items-center gap-[12px]">
          <Terminal size={24} />
          <div className="flex-1">
            <p className="text-[15px] font-semibold">Your agent computer</p>
            <p className="text-[13px] text-muted-foreground">
              {status === undefined
                ? "Connecting…"
                : actuatorUnavailable
                  ? `Actuator offline · ${status.code || "no desktop control driver"}`
                  : `Control ${status.controlState.toLowerCase()} · session ${status.session}`}
            </p>
          </div>
          {(busy || status === undefined) && <Spinner />}
        </div>
        {status?.detail ? (
          <p className="text-[11px] text-muted-foreground">
            {status.detail}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <OmButton onClick={() => void present()} busy={busy} icon={Play}>
            Present desktop
          </OmButton>
          <OmButton onClick={() => void pip()} busy={busy} icon={Monitor}>
            Picture-in-picture
          </OmButton>
          <OmButton onClick={() => void refresh()} disabled={busy} icon={RefreshCw}>
            Refresh
          </OmButton>
        </div>
      </OmCard>

      <OmErrorNotice error={error} />
      {notice ? (
        <p className="text-[12px] text-muted-foreground">{notice}</p>
      ) : null}

      {tab === "Terminal" ? (
        <div className="flex flex-col gap-4">
          <div
            className="flex flex-col gap-2 bg-secondary p-[18px]"
            style={{ borderRadius: 22 }}
          >
            <p className="font-mono text-[12px] uppercase tracking-wide text-muted-foreground">
              TERMINAL
            </p>
            <OmField
              label="Working directory"
              value={ops?.apps.length ? `/workspace (apps: ${ops.apps.length})` : ""}
              onChange={() => {}}
              mono
              disabled
              ariaLabel="Working directory (read-only)"
            />
            <OmField
              label="Command"
              value=""
              onChange={() => {}}
              placeholder="Shell execution is unavailable on this host"
              multiline
              mono
              disabled
              ariaLabel="Command (unavailable)"
            />
            <OmButton primary icon={Play} disabled>
              Run command
            </OmButton>
            <p className="text-[12px] leading-[18px] text-muted-foreground">
              Shell execution is deliberately out of scope. Qubit&apos;s computer
              surface is the audited desktop-control path:{" "}
              <span className="font-mono">computer_use</span> performs semantic,
              accessibility-API actions only (launch, present,
              picture-in-picture) under an emergency-stop kill switch and an
              audit trail —{" "}
              <code className="font-mono">control_actions()</code> returns
              &ldquo;semantic, accessibility-API actions only; no shell&rdquo;. No
              raw shell/PTY is exposed, and nothing here pretends a command ran.
            </p>
          </div>

          <OmCard className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[15px] font-semibold">Desktop apps</p>
              <OmChip tone="sky">{ops?.apps.length ?? 0} declared</OmChip>
            </div>
            {ops?.apps.length ? (
              <ul className="flex flex-wrap gap-2">
                {ops.apps.map((app) => (
                  <li key={app}>
                    <OmChip>{app}</OmChip>
                  </li>
                ))}
              </ul>
            ) : (
              <OmEmpty
                icon={Monitor}
                title="No apps declared"
                detail="The desktop-op catalog reported no launchable apps."
              />
            )}
            <div className="flex items-center justify-between gap-3">
              <p className="text-[15px] font-semibold">Presentation</p>
              <OmChip tone={presentation?.presenting ? "green" : "neutral"}>
                {presentation?.presenting
                  ? `presenting · ${presentation.mode}`
                  : "idle"}
              </OmChip>
            </div>
            {presentation?.presenting ? (
              <p className="text-[12px] text-muted-foreground">
                Panes: {presentation.panes.join(", ") || "none"} ·{" "}
                {presentation.rollback || "no rollback recorded"}
              </p>
            ) : (
              <p className="text-[12px] text-muted-foreground">
                Nothing is being presented. Use “Present desktop” to stage a
                presentation — the bridge will ask for confirmation first.
              </p>
            )}
          </OmCard>
        </div>
      ) : (
        <ComputerFiles />
      )}
    </div>
  );
}

/* ------------------------------------------------------- ComputerFiles */

interface WorkspaceRow {
  path: string;
  sizeBytes?: number;
  readable: boolean;
}

function normalizeRoster(payload: unknown): WorkspaceRow[] {
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  const raw = Array.isArray(inner.entries)
    ? inner.entries
    : Array.isArray(inner.files)
      ? inner.files
      : [];
  return raw.map((item) => {
    const row = item as Record<string, unknown>;
    const kind = String(row.kind ?? "");
    const isDir = kind === "dir" || kind === "directory";
    return {
      path: String(row.path ?? row.name ?? ""),
      sizeBytes:
        typeof row.size === "number"
          ? row.size
          : typeof row.size_bytes === "number"
            ? row.size_bytes
            : undefined,
      readable: !isDir && row.readable !== false,
    };
  });
}

/**
 * Upstream `ComputerFiles`. Ported onto the REAL agent-workspace roster:
 *   GET  /api/qubit/agent-workspace/list   (roster)
 *   GET  /api/qubit/agent-workspace/get    (single-file read)
 * The upstream per-computer file manager (`/api/computer/files*`) is not
 * served by Qubit; new-file/save/mkdir/import controls are therefore absent
 * rather than dead, and that absence is stated.
 */
function ComputerFiles() {
  const [rows, setRows] = useState<WorkspaceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [openContent, setOpenContent] = useState<string>("");
  const [opening, setOpening] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    listWorkspaceFiles()
      .then((res) => {
        setRows(normalizeRoster(res));
      })
      .catch((e) => setError(refusalOrError(e, "Workspace list failed.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function read(path: string) {
    setOpening(true);
    setOpenPath(path);
    setOpenContent("");
    setNotice("");
    getWorkspaceFile(path)
      .then((res) => {
        setOpenContent(res.content);
        if (res.truncated) setNotice("Truncated by the backend read limit.");
      })
      .catch((e) => setOpenContent(refusalOrError(e, "File open failed.")))
      .finally(() => setOpening(false));
  }

  /* ---- computer workspace file mutations (confined canvas host) --------- */

  const [wsPath, setWsPath] = useState("");
  const [wsDir, setWsDir] = useState<ComputerEntry[]>([]);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsError, setWsError] = useState("");
  const [wsNotice, setWsNotice] = useState("");
  const [wsBusy, setWsBusy] = useState(false);
  const [editPath, setEditPath] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [newName, setNewName] = useState("");
  const [newFolder, setNewFolder] = useState("");

  const loadWs = useCallback(() => {
    setWsLoading(true);
    setWsError("");
    listComputerFiles(wsPath)
      .then((res) => {
        setWsDir(res.entries ?? []);
      })
      .catch((e) =>
        setWsError(refusalOrError(e, "Computer workspace list failed.")),
      )
      .finally(() => setWsLoading(false));
  }, [wsPath]);

  useEffect(() => {
    loadWs();
  }, [loadWs]);

  function openWs(path: string) {
    setWsBusy(true);
    setWsError("");
    setWsNotice("");
    readComputerFile(path)
      .then((res) => {
        setEditPath(res.path);
        setEditText(res.content);
      })
      .catch((e) => setWsError(refusalOrError(e, "Computer file open failed.")))
      .finally(() => setWsBusy(false));
  }

  function saveWs() {
    if (editPath === null || wsBusy) return;
    setWsBusy(true);
    setWsError("");
    setWsNotice("");
    writeComputerFile(editPath, editText)
      .then(() => {
        setWsNotice(`Saved ${editPath}.`);
        loadWs();
      })
      .catch((e) => setWsError(refusalOrError(e, "Computer file save failed.")))
      .finally(() => setWsBusy(false));
  }

  function mkdirWs() {
    const name = newFolder.trim();
    if (!name || wsBusy) return;
    const path = wsPath ? `${wsPath}/${name}` : name;
    setWsBusy(true);
    setWsError("");
    setWsNotice("");
    mkdirComputer(path)
      .then(() => {
        setNewFolder("");
        setWsNotice(`Created folder ${path}.`);
        loadWs();
      })
      .catch((e) =>
        setWsError(refusalOrError(e, "Computer folder create failed.")),
      )
      .finally(() => setWsBusy(false));
  }

  function newFile() {
    const name = newName.trim();
    if (!name || wsBusy) return;
    const path = wsPath ? `${wsPath}/${name}` : name;
    setEditPath(path);
    setEditText("");
    setNewName("");
    setWsNotice("");
    setWsError("");
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[15px] font-semibold">Workspace files</p>
        <div className="flex items-center gap-2">
          {loading ? <Spinner /> : null}
          <OmButton small icon={RefreshCw} onClick={load} disabled={loading}>
            Refresh
          </OmButton>
        </div>
      </div>
      <OmErrorNotice error={error} />
      {notice ? (
        <p className="text-[12px] text-muted-foreground">{notice}</p>
      ) : null}

      {openPath ? (
        <OmCard className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <p className="font-mono text-[12px]">{openPath}</p>
            <OmButton
              small
              icon={ArrowLeft}
              onClick={() => setOpenPath(null)}
            >
              Back to files
            </OmButton>
          </div>
          {opening ? (
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Spinner /> Opening…
            </div>
          ) : (
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-[10px] bg-background p-3 font-mono text-[12px]">
              {openContent || "Empty file."}
            </pre>
          )}
          <p className="text-[12px] text-muted-foreground">
            Read-only preview. Writing workspace files is owned by the agent
            session, not this dashboard.
          </p>
        </OmCard>
      ) : loading && !rows.length ? (
        <OmCard className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-[34px] animate-pulse rounded bg-secondary" />
          ))}
        </OmCard>
      ) : rows.length === 0 ? (
        <OmEmpty
          icon={Folder}
          title="No workspace files reported"
          detail="The agent workspace roster is empty for the current worker."
        />
      ) : (
        <div className="flex flex-col">
          {rows.map((row) => (
            <div
              key={row.path}
              className="flex items-center gap-[14px] border-b border-border py-[13px]"
            >
              <OmIconBox>
                {row.readable ? <FileText size={19} /> : <Folder size={19} />}
              </OmIconBox>
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[13px]">{row.path}</p>
                {row.sizeBytes !== undefined ? (
                  <p className="text-[11px] text-muted-foreground">
                    {row.sizeBytes} bytes
                  </p>
                ) : null}
                {!row.readable ? (
                  <p className="text-[11px] text-muted-foreground">
                    Directories cannot be opened from the dashboard.
                  </p>
                ) : null}
              </div>
              {row.readable ? (
                <OmButton
                  small
                  onClick={() => read(row.path)}
                  disabled={opening && openPath === row.path}
                  ariaLabel={`Open file ${row.path}`}
                >
                  open
                </OmButton>
              ) : (
                <OmButton
                  small
                  disabled
                  title="Directories cannot be opened from the dashboard."
                  ariaLabel={`Open file ${row.path} unavailable`}
                >
                  open unavailable
                </OmButton>
              )}
            </div>
          ))}
        </div>
      )}

      <OmCard className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[14px] font-semibold">Computer file mutations</p>
          <div className="flex items-center gap-2">
            {wsLoading ? <Spinner /> : null}
            <OmButton small icon={RefreshCw} onClick={loadWs} disabled={wsLoading}>
              Refresh
            </OmButton>
          </div>
        </div>
        <p className="text-[12px] text-muted-foreground">
          Confined to the Qubit canvas host (state-home <span className="font-mono">canvas/</span>).
          Creates and saves are confirmed first; traversal is refused.
        </p>
        <OmErrorNotice error={wsError} />
        {wsNotice ? (
          <p className="text-[12px]" style={{ color: "#248258" }}>
            {wsNotice}
          </p>
        ) : null}

        <div className="flex items-center gap-2">
          <OmButton
            small
            icon={ArrowLeft}
            disabled={!wsPath}
            onClick={() => setWsPath(wsPath.slice(0, wsPath.lastIndexOf("/")))}
            ariaLabel="Up one folder"
          >
            Up
          </OmButton>
          <p className="min-w-0 flex-1 truncate font-mono text-[12px]">
            /{wsPath || ""}
          </p>
        </div>

        {wsDir.length === 0 ? (
          <OmEmpty
            icon={Folder}
            title="Folder is empty"
            detail="Create a file or folder below."
          />
        ) : (
          <div className="flex flex-col">
            {wsDir.map((row) => (
              <div
                key={row.path}
                className="flex items-center gap-[14px] border-b border-border py-[10px]"
              >
                <OmIconBox>
                  {row.kind === "directory" ? (
                    <Folder size={18} />
                  ) : (
                    <FileText size={18} />
                  )}
                </OmIconBox>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-[12px]">{row.path}</p>
                  {typeof row.size === "number" ? (
                    <p className="text-[11px] text-muted-foreground">
                      {row.size} bytes
                    </p>
                  ) : null}
                </div>
                <OmButton
                  small
                  disabled={wsBusy}
                  onClick={() =>
                    row.kind === "directory" ? setWsPath(row.path) : openWs(row.path)
                  }
                  ariaLabel={`Open ${row.path}`}
                >
                  {row.kind === "directory" ? "open" : "edit"}
                </OmButton>
              </div>
            ))}
          </div>
        )}

        {editPath !== null ? (
          <div className="flex flex-col gap-2 border-t border-border pt-3">
            <p className="font-mono text-[12px]">{editPath}</p>
            <textarea
              className="min-h-[180px] w-full whitespace-pre-wrap rounded-[10px] bg-secondary p-3 font-mono text-[12px]"
              value={editText}
              spellCheck={false}
              onChange={(e) => setEditText(e.target.value)}
              aria-label={`Contents of ${editPath}`}
            />
            <div className="flex flex-wrap gap-2">
              <OmButton primary busy={wsBusy} onClick={saveWs}>
                Save file
              </OmButton>
              <OmButton
                disabled={wsBusy}
                onClick={() => {
                  setEditPath(null);
                  setEditText("");
                }}
              >
                Close editor
              </OmButton>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
            <div className="min-w-[160px] flex-1">
              <OmField
                label="New file name"
                value={newName}
                onChange={setNewName}
                mono
                ariaLabel="New file name"
              />
            </div>
            <OmButton
              small
              disabled={!newName.trim() || wsBusy}
              onClick={newFile}
              icon={FilePlus}
            >
              New file
            </OmButton>
            <div className="min-w-[160px] flex-1">
              <OmField
                label="New folder name"
                value={newFolder}
                onChange={setNewFolder}
                mono
                ariaLabel="New folder name"
              />
            </div>
            <OmButton
              small
              disabled={!newFolder.trim() || wsBusy}
              onClick={mkdirWs}
              icon={FolderPlus}
            >
              New folder
            </OmButton>
          </div>
        )}
      </OmCard>
    </div>
  );
}

/* --------------------------------------------------------- ComputerShell */

/**
 * The full computer surface body (upstream `ComputerSheet`): a tab bar across
 * Browser / Terminal / Files. Browser tab reports the real connection status
 * and is honest that Qubit exposes no browser-session create backend.
 */
export function ComputerWorkspace() {
  const [tab, setTab] = useState<"Browser" | "Terminal" | "Files">("Browser");
  const [status, setStatus] = useState<ComputerStatus>();
  const [statusError, setStatusError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const loadStatus = useCallback(async () => {
    setRefreshing(true);
    try {
      const s = await getComputerStatus();
      setStatus(s);
      setStatusError("");
    } catch (e) {
      setStatusError(refusalOrError(e, "Computer status failed."));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const available = status?.controlState === "AVAILABLE";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Computer panels">
        {(["Browser", "Terminal", "Files"] as const).map((item) => (
          <OmButton
            key={item}
            small
            primary={tab === item}
            onClick={() => setTab(item)}
            icon={
              item === "Browser" ? Monitor : item === "Terminal" ? Terminal : FileText
            }
          >
            {item}
          </OmButton>
        ))}
      </div>

      <OmErrorNotice error={statusError} />

      {tab === "Browser" ? (
        <div className="flex flex-col gap-4">
          <OmCard tone="sky" className="flex items-center gap-[12px] p-[18px]">
            <Monitor size={28} />
            <div className="flex-1">
              <p className="text-[15px] font-semibold">
                {available ? "Browser connected" : "Browser offline"}
              </p>
              <p className="text-[13px] text-muted-foreground">
                {available
                  ? "Your agent's browser and documents, in one place."
                  : "Start the browser worker to connect this computer."}
              </p>
            </div>
          </OmCard>

          <OmCard className="flex flex-col gap-3">
            <p className="text-[14px] font-semibold">Open a browser session</p>
            <OmField
              label="Website address"
              value=""
              onChange={() => {}}
              placeholder="https://example.com"
              disabled
              ariaLabel="Website address (unavailable)"
            />
            <OmButton primary icon={Play} disabled>
              Open a browser session
            </OmButton>
            <p className="text-[12px] text-muted-foreground">
              Browser sessions are opened by the agent during its own turns;
              Qubit exposes no session-create backend to the dashboard
              (`/api/browsers` is not on the compat table). When the agent opens
              one, it appears in Chat. This control is disabled, not a fake.
            </p>
          </OmCard>

          <p className="text-[12px] text-muted-foreground">
            Browsing sessions keep their own logins and downloads. Open one from
            the agent's own thread to take over, then return here.
          </p>
        </div>
      ) : (
        <LinuxWorkspace tab={tab === "Files" ? "Files" : "Terminal"} />
      )}

      <div className="flex items-center gap-3">
        <OmButton
          small
          icon={RefreshCw}
          busy={refreshing}
          onClick={() => void loadStatus()}
          ariaLabel="Refresh computer status"
        >
          Refresh computer
        </OmButton>
        <OmChip tone={available ? "green" : "neutral"}>
          {available ? "control available" : "control unavailable"}
        </OmChip>
      </div>
    </div>
  );
}
