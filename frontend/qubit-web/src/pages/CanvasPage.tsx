/**
 * Canvas page (route /canvas). Qubit-branded canvas surface: canvas-root file
 * list, open/preview through the mirrored entry-URL shape, confirm-gated
 * add/remove (brokered, path-confined), node presentation over capability URLs,
 * and a live-reload indicator. Reads go through `fetchJSON`; every mutation
 * settles through the shared staged-confirmation round-trip
 * (`runMutationWithConfirm`). Traversal names are refused client-side before
 * anything is sent.
 *
 * Backend handlers (registered in `voicebridge/files/canvas_host.py`, verified
 * live): GET /api/qubit/canvas/list · get · live, and the confirm-gated POSTs
 * add · remove · present · hide · navigate · snapshot. `list` answers
 * `{ files: [], root, live_reload, authority }`; a missing file is a typed
 * `CANVAS_NOT_FOUND`, never a fake page.
 *
 * Built on the shipped openclaw render-helper contract
 * (`@/components/console-ui`): a `SettingsPage` root holding a
 * `SettingsPageHeader`, a summary tile strip, and `SettingsSection` groups.
 * `.settings-row__control` is emitted by the helper ONLY when a control is
 * passed — a control-free row never renders an empty wrapper.
 */
import { useCallback, useEffect, useState } from "react";

import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  CANVAS_ROOT_LABEL,
  addCanvasFile,
  canvasEntryPath,
  canvasNameRefusal,
  getCanvasFile,
  hideCanvas,
  isCanvasNameSafe,
  listCanvasFiles,
  navigateCanvas,
  normalizeCanvasList,
  presentCanvas,
  removeCanvasFile,
  resolveCanvasPreviewUrl,
  sanitizeCanvasEntryUrl,
  snapshotCanvas,
  refusalOrError,
  type CanvasFile,
  type ConfirmPrompt,
} from "@/lib/ocFamilyCanvasApi";
import { listNodes, type NodeRow } from "@/lib/ocFamilyAgentsApi";
import {
  SettingsEmpty,
  SettingsLoadingSkeleton,
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsStatus,
  SettingsSummary,
  SettingsValue,
} from "@/components/console-ui";

interface PendingConfirm {
  detail: string;
  resolve: (value: boolean) => void;
}

const CANVAS_READ_FALLBACK =
  "Canvas list unavailable: the backend canvas route refused the read. Nothing was changed.";

export default function CanvasPage() {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<CanvasFile[]>([]);
  const [root, setRoot] = useState<string>(CANVAS_ROOT_LABEL);
  const [liveReload, setLiveReload] = useState<boolean | null>(null);
  const [authority, setAuthority] = useState<string>("");

  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [nodeId, setNodeId] = useState<string>("");
  const [scopedBase, setScopedBase] = useState<string>("");

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [addName, setAddName] = useState<string>("");
  const [addContent, setAddContent] = useState<string>("");
  const [formError, setFormError] = useState<string | null>(null);

  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  const confirmPrompt = useCallback(
    (detailText: string) =>
      new Promise<boolean>((resolve) => {
        setPendingConfirm({ detail: detailText, resolve });
      }),
    [],
  );

  const confirm: ConfirmPrompt = confirmPrompt;

  const refresh = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const payload = await listCanvasFiles();
      const normalized = normalizeCanvasList(payload);
      setFiles(normalized.files);
      setRoot(normalized.root || CANVAS_ROOT_LABEL);
      setLiveReload(normalized.live_reload ?? null);
      setAuthority(normalized.authority || "");
      setState("ready");
    } catch (e) {
      setError(refusalOrError(e, CANVAS_READ_FALLBACK));
      setState("error");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    // Node identity reuses the EXISTING node roster surface (no new client).
    // Best-effort: a roster failure leaves presentation target-less, never fatal.
    listNodes()
      .then((res) => {
        const rows = Array.isArray(res.nodes) ? res.nodes : [];
        setNodes(rows);
        if (rows.length > 0) setNodeId((prev) => prev || rows[0].id);
      })
      .catch(() => {
        setNodes([]);
      });
  }, []);

  async function openPreview(path: string) {
    setSelectedPath(path);
    setPreviewContent(null);
    setPreviewError(null);
    setBusy(`open:${path}`);
    try {
      const payload = await getCanvasFile(path);
      const env = (payload ?? {}) as Record<string, unknown>;
      const inner = (env.result ?? env) as Record<string, unknown>;
      const content = typeof inner.content === "string" ? inner.content : "";
      setPreviewContent(content);
    } catch (e) {
      setPreviewError(refusalOrError(e, "Canvas open refused: this file is not served."));
    } finally {
      setBusy(null);
    }
  }

  async function handleAdd() {
    setFormError(null);
    setNotice(null);
    const name = addName.trim();
    if (!isCanvasNameSafe(name)) {
      setFormError(canvasNameRefusal(addName));
      return;
    }
    setBusy("add");
    try {
      await addCanvasFile({ path: name, content: addContent }, confirm);
      setNotice("Add completed.");
      setAddName("");
      setAddContent("");
      await refresh();
    } catch (e) {
      setFormError(refusalOrError(e, "Add failed."));
    } finally {
      setBusy(null);
    }
  }

  async function handleRemove(path: string) {
    if (!isCanvasNameSafe(path)) {
      setNotice(canvasNameRefusal(path));
      return;
    }
    setBusy(`remove:${path}`);
    setNotice(null);
    try {
      await removeCanvasFile(path, confirm);
      setNotice("Remove completed.");
      if (selectedPath === path) {
        setSelectedPath(null);
        setPreviewContent(null);
      }
      await refresh();
    } catch (e) {
      setNotice(refusalOrError(e, "Remove failed."));
    } finally {
      setBusy(null);
    }
  }

  async function handlePresent() {
    if (!nodeId) {
      setNotice("Pick a node first: no presentation target.");
      return;
    }
    if (!selectedPath) {
      setNotice("Open a file first: nothing to present.");
      return;
    }
    setBusy("present");
    setNotice(null);
    try {
      await presentCanvas(
        {
          node_id: nodeId,
          entry: canvasEntryPath(selectedPath),
          scoped_base_url: scopedBase.trim() || undefined,
        },
        confirm,
      );
      setNotice("Present completed.");
    } catch (e) {
      setNotice(refusalOrError(e, "Present failed."));
    } finally {
      setBusy(null);
    }
  }

  async function handleHide() {
    if (!nodeId) {
      setNotice("Pick a node first: no hide target.");
      return;
    }
    setBusy("hide");
    setNotice(null);
    try {
      await hideCanvas(nodeId, confirm);
      setNotice("Hide completed.");
    } catch (e) {
      setNotice(refusalOrError(e, "Hide failed."));
    } finally {
      setBusy(null);
    }
  }

  async function handleSnapshot() {
    if (!nodeId) {
      setNotice("Pick a node first: no snapshot target.");
      return;
    }
    setBusy("snapshot");
    setNotice(null);
    try {
      await snapshotCanvas(nodeId, confirm);
      setNotice("Snapshot completed.");
    } catch (e) {
      setNotice(refusalOrError(e, "Snapshot failed."));
    } finally {
      setBusy(null);
    }
  }

  async function handleNavigate(entry: string) {
    if (!nodeId) {
      setNotice("Pick a node first: no navigate target.");
      return;
    }
    const safe = sanitizeCanvasEntryUrl(entry.trim());
    if (!safe) {
      setNotice(`Refused client-side: "${entry}" is not a canvas entry URL.`);
      return;
    }
    setBusy("navigate");
    setNotice(null);
    try {
      await navigateCanvas(nodeId, safe, confirm);
      setNotice("Navigate completed.");
    } catch (e) {
      setNotice(refusalOrError(e, "Navigate failed."));
    } finally {
      setBusy(null);
    }
  }

  const previewSrc = selectedPath
    ? (resolveCanvasPreviewUrl(canvasEntryPath(selectedPath), scopedBase.trim() || undefined) ?? undefined)
    : undefined;

  return (
    <SettingsPage wide>
      <div className="contents" aria-label="Canvas">
        <SettingsPageHeader
          title="Canvas"
          description={
            <>
              Root: {root} Every write is confirm-gated and path-confined.
              {authority ? ` Authority: ${authority}` : ""}
            </>
          }
          actions={
            <>
              <span
                aria-label="Live reload state"
                title={
                  liveReload === null
                    ? "live-reload unknown (not reported by the host)"
                    : `live reload ${liveReload ? "on" : "off"} (server-reported)`
                }
                className={`settings-status ${
                  liveReload ? "settings-status--ok" : "settings-status--warn"
                }`}
              >
                <span aria-hidden="true" className="settings-status__dot" />
                {liveReload === null
                  ? "live reload unknown"
                  : liveReload
                    ? "live reload on"
                    : "live reload off"}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void refresh()}
                aria-label="Refresh canvas files"
              >
                Refresh
              </button>
            </>
          }
        />

        <SettingsSummary
          items={[
            { label: "Files", value: state === "ready" ? files.length : "…" },
            { label: "Root", value: root },
            { label: "Live reload", value: liveReload === null ? "unknown" : liveReload ? "on" : "off" },
            { label: "Nodes", value: nodes.length },
          ]}
        />

        <SettingsSection title="Files" count={state === "ready" ? files.length : undefined}>
          {state === "loading" ? (
            <SettingsLoadingSkeleton rows={2} />
          ) : state === "error" ? (
            <SettingsRow role="alert" title="Canvas list unavailable" description={error} />
          ) : files.length === 0 ? (
            <SettingsEmpty title="No canvas files yet" >
              Add one in the form below — files are served from the canvas root.
            </SettingsEmpty>
          ) : (
            <div className="settings-subrows" role="group" aria-label="Canvas file roster">
              {files.map((f) => (
                <SettingsRow
                  key={f.path || f.name}
                  title={
                    <SettingsValue mono>{f.name || f.path}</SettingsValue>
                  }
                  description={
                    typeof f.size_bytes === "number" ? `${f.size_bytes} B` : undefined
                  }
                  control={
                    <>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        aria-label={`Open canvas file ${f.name || f.path}`}
                        disabled={busy === `open:${f.path}`}
                        onClick={() => void openPreview(f.path || f.name)}
                      >
                        {busy === `open:${f.path}` ? <Spinner /> : "Open"}
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        aria-label={`Remove canvas file ${f.name || f.path}`}
                        disabled={busy === `remove:${f.path}`}
                        onClick={() => void handleRemove(f.path || f.name)}
                      >
                        {busy === `remove:${f.path}` ? <Spinner /> : "Remove"}
                      </button>
                    </>
                  }
                />
              ))}
            </div>
          )}
        </SettingsSection>

        <SettingsSection
          title="Add file"
          description="Canvas-root-relative name (no traversal) and content."
        >
          <SettingsRow
            stacked
            title="Name"
            description="Canvas-root relative, no traversal."
            control={
              <input
                aria-label="New canvas file name"
                className="settings-input"
                value={addName}
                placeholder="index.html"
                onChange={(e) => setAddName(e.target.value)}
              />
            }
          />
          <SettingsRow
            stacked
            title="Content"
            description="The file body to write."
            control={
              <textarea
                aria-label="New canvas file content"
                className="settings-input"
                rows={4}
                value={addContent}
                placeholder="<!doctype html>…"
                onChange={(e) => setAddContent(e.target.value)}
              />
            }
          />
          {formError ? (
            <SettingsRow role="alert" title="Write refused" description={formError} />
          ) : null}
          <SettingsRow
            title="Write file"
            description="The write is confirm-gated."
            control={
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                aria-label="Add canvas file"
                disabled={busy === "add"}
                onClick={() => void handleAdd()}
              >
                {busy === "add" ? <Spinner /> : "Add (confirm)"}
              </button>
            }
          />
        </SettingsSection>

        {selectedPath ? (
          <SettingsSection
            title="Preview"
            description={<SettingsValue mono>{selectedPath}</SettingsValue>}
          >
            <SettingsRow
              stacked
              title="Entry"
              description={
                previewError ? (
                  <span role="alert">{previewError}</span>
                ) : (
                  "Served through the mirrored canvas host path."
                )
              }
              control={
                <>
                  {previewSrc ? (
                    <iframe
                      aria-label="Canvas preview"
                      title={`Canvas preview of ${selectedPath}`}
                      src={previewSrc}
                      sandbox="allow-scripts"
                      className="w-full"
                    />
                  ) : null}
                  {previewContent !== null ? (
                    <details className="settings-row__desc">
                      <summary>Stored content</summary>
                      <pre className="settings-kv w-full whitespace-pre-wrap">{previewContent}</pre>
                    </details>
                  ) : null}
                </>
              }
            />
          </SettingsSection>
        ) : null}

        <SettingsSection
          title="Show on node"
          description="Present, hide, snapshot, or navigate the node canvas (each confirm-gated)."
        >
          <SettingsRow
            stacked
            title="Target node"
            description="Node identity comes from the existing node roster."
            control={
              <>
                <select
                  aria-label="Presentation node"
                  className="settings-select"
                  value={nodeId}
                  onChange={(e) => setNodeId(e.target.value)}
                >
                  <option value="">— pick a node —</option>
                  {nodes.map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.name ? `${n.name} (${n.id})` : n.id}
                    </option>
                  ))}
                </select>
                <input
                  aria-label="Scoped base URL"
                  className="settings-input"
                  value={scopedBase}
                  placeholder="scoped base URL (optional)"
                  onChange={(e) => setScopedBase(e.target.value)}
                />
              </>
            }
          />
          <SettingsRow
            title="Canvas actions"
            description={
              selectedPath
                ? `Open file: ${selectedPath}`
                : "Open a file to enable present/navigate."
            }
            control={
              <>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Present on node"
                  disabled={busy === "present"}
                  onClick={() => void handlePresent()}
                >
                  {busy === "present" ? <Spinner /> : "Present (confirm)"}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Hide node canvas"
                  disabled={busy === "hide"}
                  onClick={() => void handleHide()}
                >
                  {busy === "hide" ? <Spinner /> : "Hide (confirm)"}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Snapshot node canvas"
                  disabled={busy === "snapshot"}
                  onClick={() => void handleSnapshot()}
                >
                  {busy === "snapshot" ? <Spinner /> : "Snapshot (confirm)"}
                </button>
                {selectedPath ? (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    aria-label="Navigate node canvas to open file"
                    disabled={busy === "navigate"}
                    onClick={() => void handleNavigate(canvasEntryPath(selectedPath))}
                  >
                    {busy === "navigate" ? <Spinner /> : "Navigate to open file (confirm)"}
                  </button>
                ) : null}
              </>
            }
          />
        </SettingsSection>

        {notice ? (
          <p role="status" className="settings-status settings-status--accent">
            {notice}
          </p>
        ) : null}

        {pendingConfirm ? (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Confirm canvas action"
            className="settings-group"
          >
            <SettingsRow
              title="Confirm action"
              description={pendingConfirm.detail}
              control={
                <>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      const p = pendingConfirm;
                      setPendingConfirm(null);
                      p.resolve(true);
                    }}
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      const p = pendingConfirm;
                      setPendingConfirm(null);
                      p.resolve(false);
                    }}
                  >
                    Cancel
                  </button>
                </>
              }
            />
          </div>
        ) : null}

        <SettingsSection title="Host notes">
          <SettingsRow
            title="Canvas host"
            description="Canvas files live under a server-owned state-home subdirectory (never outside approved roots). Preview URLs mirror the served entry shape; node scoping applies a bridge-supplied capability base when present."
            control={
              <SettingsStatus tone={liveReload ? "ok" : "warn"}>
                {liveReload === null ? "live reload unknown" : liveReload ? "live" : "static"}
              </SettingsStatus>
            }
          />
        </SettingsSection>
      </div>
    </SettingsPage>
  );
}
