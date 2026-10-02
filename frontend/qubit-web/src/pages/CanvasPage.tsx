/**
 * Canvas page (intended route /canvas — route registration is bridge work
 * outside this file). Separate Qubit-branded canvas surface ported from the
 * upstream canvas extension: canvas-root file list, open/preview through the
 * mirrored entry-URL shape, confirm-gated add/remove (brokered,
 * path-confined), node presentation over capability URLs, and a live-reload
 * indicator. Reads go through `fetchJSON`; every mutation settles through
 * the shared staged-confirmation round-trip (`runMutationWithConfirm`).
 * Traversal names are refused client-side before anything is sent.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
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
import { cn } from "@/lib/utils";

interface PendingConfirm {
  detail: string;
  resolve: (value: boolean) => void;
}

const BRIDGE_PENDING =
  "Canvas bridge pending: the backend serves no canvas route yet (every /api/qubit/canvas/* path is NEEDS-BRIDGE). Nothing was changed.";

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
      setError(refusalOrError(e, BRIDGE_PENDING));
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
      setPreviewError(refusalOrError(e, BRIDGE_PENDING));
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
      setFormError(refusalOrError(e, `Add failed (${BRIDGE_PENDING})`));
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
      setNotice(refusalOrError(e, `Remove failed (${BRIDGE_PENDING})`));
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
      setNotice(refusalOrError(e, `Present failed (${BRIDGE_PENDING})`));
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
      setNotice(refusalOrError(e, `Hide failed (${BRIDGE_PENDING})`));
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
      setNotice(refusalOrError(e, `Snapshot failed (${BRIDGE_PENDING})`));
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
      setNotice(refusalOrError(e, `Navigate failed (${BRIDGE_PENDING})`));
    } finally {
      setBusy(null);
    }
  }

  const previewSrc = selectedPath
    ? (resolveCanvasPreviewUrl(canvasEntryPath(selectedPath), scopedBase.trim() || undefined) ?? undefined)
    : undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Canvas</h1>
        <span
          aria-label="Live reload state"
          title={liveReload === null ? "live-reload unknown (bridge pending)" : `live reload ${liveReload ? "on" : "off"} (server-reported)`}
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
            liveReload ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600",
          )}
        >
          <span
            aria-hidden="true"
            className={cn("h-2 w-2 rounded-full", liveReload ? "bg-green-500" : "bg-gray-400")}
          />
          {liveReload === null ? "live reload unknown" : liveReload ? "live reload on" : "live reload off"}
        </span>
        <Button outlined size="sm" onClick={() => void refresh()} aria-label="Refresh canvas files">
          Refresh
        </Button>
      </div>

      <p className="text-sm text-gray-500">
        Root: {root} Every write is confirm-gated and path-confined. {authority ? `Authority: ${authority}` : null}
      </p>

      {state === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Loading canvas files…
        </p>
      ) : null}

      {state === "error" ? (
        <p role="alert" className="text-sm text-amber-700">
          {error}
        </p>
      ) : null}

      {state === "ready" ? (
        <section aria-label="Canvas file roster card">
          <h2 className="mb-2 text-sm font-medium">Files</h2>
          {files.length === 0 ? (
            <p className="text-sm text-gray-500">No canvas files yet — add one below.</p>
          ) : (
            <ul aria-label="Canvas file roster" className="divide-y rounded border">
              {files.map((f) => (
                <li key={f.path || f.name} className="flex items-center gap-2 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{f.name || f.path}</span>
                  {typeof f.size_bytes === "number" ? (
                    <span className="text-xs text-gray-500">{f.size_bytes} B</span>
                  ) : null}
                  <Button
                    outlined
                    size="sm"
                    aria-label={`Open canvas file ${f.name || f.path}`}
                    disabled={busy === `open:${f.path}`}
                    onClick={() => void openPreview(f.path || f.name)}
                  >
                    {busy === `open:${f.path}` ? <Spinner /> : "Open"}
                  </Button>
                  <Button
                    outlined
                    size="sm"
                    aria-label={`Remove canvas file ${f.name || f.path}`}
                    disabled={busy === `remove:${f.path}`}
                    onClick={() => void handleRemove(f.path || f.name)}
                  >
                    {busy === `remove:${f.path}` ? <Spinner /> : "Remove"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <section aria-label="Add canvas file form" className="space-y-2 rounded border p-3">
        <h2 className="text-sm font-medium">Add file</h2>
        <label className="block text-sm">
          Name (canvas-root relative, no traversal)
          <input
            aria-label="New canvas file name"
            className="mt-1 block w-full rounded border px-2 py-1 font-mono text-sm"
            value={addName}
            placeholder="index.html"
            onChange={(e) => setAddName(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          Content
          <textarea
            aria-label="New canvas file content"
            className="mt-1 block w-full rounded border px-2 py-1 font-mono text-sm"
            rows={4}
            value={addContent}
            placeholder="<!doctype html>…"
            onChange={(e) => setAddContent(e.target.value)}
          />
        </label>
        {formError ? (
          <p role="alert" className="text-sm text-red-700">
            {formError}
          </p>
        ) : null}
        <Button
          size="sm"
          aria-label="Add canvas file"
          disabled={busy === "add"}
          onClick={() => void handleAdd()}
        >
          {busy === "add" ? <Spinner /> : "Add (confirm)"}
        </Button>
      </section>

      {selectedPath ? (
        <section aria-label="Canvas preview card" className="space-y-2 rounded border p-3">
          <h2 className="text-sm font-medium">Preview: {selectedPath}</h2>
          {previewError ? (
            <p role="alert" className="text-sm text-amber-700">
              {previewError}
            </p>
          ) : null}
          {previewSrc ? (
            <iframe
              aria-label="Canvas preview"
              title={`Canvas preview of ${selectedPath}`}
              src={previewSrc}
              sandbox="allow-scripts"
              className="h-64 w-full rounded border bg-white"
            />
          ) : null}
          {previewContent !== null ? (
            <details className="text-sm">
              <summary>Stored content</summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-gray-50 p-2 font-mono text-xs">
                {previewContent}
              </pre>
            </details>
          ) : null}
        </section>
      ) : null}

      <section aria-label="Node presentation form" className="space-y-2 rounded border p-3">
        <h2 className="text-sm font-medium">Show on node</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm">
            Node{" "}
            <select
              aria-label="Presentation node"
              className="rounded border px-2 py-1 text-sm"
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
          </label>
          <label className="text-sm">
            Scoped base URL (optional, bridge-supplied){" "}
            <input
              aria-label="Scoped base URL"
              className="rounded border px-2 py-1 font-mono text-sm"
              value={scopedBase}
              placeholder="https://…"
              onChange={(e) => setScopedBase(e.target.value)}
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" aria-label="Present on node" disabled={busy === "present"} onClick={() => void handlePresent()}>
            {busy === "present" ? <Spinner /> : "Present (confirm)"}
          </Button>
          <Button size="sm" outlined aria-label="Hide node canvas" disabled={busy === "hide"} onClick={() => void handleHide()}>
            {busy === "hide" ? <Spinner /> : "Hide (confirm)"}
          </Button>
          <Button
            size="sm"
            outlined
            aria-label="Snapshot node canvas"
            disabled={busy === "snapshot"}
            onClick={() => void handleSnapshot()}
          >
            {busy === "snapshot" ? <Spinner /> : "Snapshot (confirm)"}
          </Button>
          {selectedPath ? (
            <Button
              size="sm"
              outlined
              aria-label="Navigate node canvas to open file"
              disabled={busy === "navigate"}
              onClick={() => void handleNavigate(canvasEntryPath(selectedPath))}
            >
              {busy === "navigate" ? <Spinner /> : "Navigate to open file (confirm)"}
            </Button>
          ) : null}
        </div>
      </section>

      {notice ? (
        <p role="status" className="text-sm text-gray-700">
          {notice}
        </p>
      ) : null}

      {pendingConfirm ? (
        <div role="dialog" aria-label="Confirm canvas action" className="rounded border bg-white p-3 shadow">
          <p className="text-sm">{pendingConfirm.detail}</p>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              onClick={() => {
                const p = pendingConfirm;
                setPendingConfirm(null);
                p.resolve(true);
              }}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              outlined
              onClick={() => {
                const p = pendingConfirm;
                setPendingConfirm(null);
                p.resolve(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <p className="text-xs text-gray-400">
        Canvas files live under a server-owned state-home subdirectory (never outside approved roots).
        Preview URLs mirror the served entry shape; node scoping applies a bridge-supplied capability
        base when present.
      </p>
    </div>
  );
}
