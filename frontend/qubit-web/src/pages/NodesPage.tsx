/**
 * Nodes page (route /nodes). List/describe enrolled nodes, enroll + approve
 * with confirmation, revoke/remove with confirmation, per-node capability
 * refresh, dispatch with confirmation, and rename with confirmation plus a
 * re-enroll warning. Unmanageable rows render disabled with their reason and
 * never fire. There are no message-gateway controls on this page.
 *
 * Layout mirrors the upstream nodes view hierarchy (toolbar + form card +
 * roster card with live-link rows + detail card), translated into Qubit voice.
 * Every backend call, confirm-gating, and disabled-with-reason row is wired
 * exactly as before.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  approveNode,
  describeNode,
  dispatchNode,
  enrollNode,
  listNodes,
  refreshNodeCapabilities,
  removeNode,
  renameNode,
  revokeNode,
  type ConfirmPrompt,
  type NodeDetail,
  type NodeRow,
} from "@/lib/ocFamilyAgentsApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn } from "@/lib/utils";

interface PendingConfirm {
  detail: string;
  resolve: (value: boolean) => void;
}

export default function NodesPage() {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [authority, setAuthority] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  const [enrollId, setEnrollId] = useState("");
  const [enrollLabel, setEnrollLabel] = useState("");
  const [dispatchTask, setDispatchTask] = useState("");
  const [renameName, setRenameName] = useState("");

  const confirmPrompt = useCallback(
    (detailText: string) =>
      new Promise<boolean>((resolve) => {
        setPendingConfirm({ detail: detailText, resolve });
      }),
    [],
  );

  const load = useCallback(() => {
    setState("loading");
    setError(null);
    listNodes()
      .then((res) => {
        setNodes(res.nodes ?? []);
        setAuthority(res.authority ?? "");
        setState("ready");
      })
      .catch((err: unknown) => {
        setError(refusalOrError(err, "Node list failed."));
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const mutate = useCallback(
    async (label: string, key: string, fn: (confirm: ConfirmPrompt) => Promise<unknown>) => {
      setBusy(key);
      setNotice(null);
      try {
        await fn(confirmPrompt);
        setNotice(`${label} completed.`);
        load();
      } catch (err: unknown) {
        setNotice(refusalOrError(err, `${label} failed.`));
      } finally {
        setBusy(null);
      }
    },
    [confirmPrompt, load],
  );

  const describe = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
    setDetail(null);
    setDetailError(null);
    setCapabilities(null);
    describeNode(nodeId)
      .then((res) => setDetail(res.node))
      .catch((err: unknown) => {
        setDetailError(refusalOrError(err, "Node describe failed."));
      });
  }, []);

  const refreshCapabilities = useCallback((nodeId: string) => {
    setBusy(`caps:${nodeId}`);
    setNotice(null);
    refreshNodeCapabilities(nodeId)
      .then((res) => setCapabilities(res.capabilities ?? []))
      .catch((err: unknown) => {
        setNotice(refusalOrError(err, "Capability refresh failed."));
      })
      .finally(() => setBusy(null));
  }, []);

  const settleConfirm = useCallback(
    (value: boolean) => {
      const pending = pendingConfirm;
      setPendingConfirm(null);
      pending?.resolve(value);
    },
    [pendingConfirm],
  );

  return (
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">Nodes</h2>
          <p className="text-xs text-text-tertiary">
            Enrolled nodes: describe, approve, dispatch, rename, revoke, remove.
          </p>
        </div>
        <Button
          ghost
          size="sm"
          onClick={load}
          disabled={state === "loading"}
          aria-label="Refresh nodes"
        >
          {state === "loading" ? <Spinner /> : "refresh"}
        </Button>
      </div>

      <section
        aria-label="Enroll node form"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Enroll node</h3>
        <p className="text-[0.6875rem] text-text-tertiary">
          Add a node by id with an optional label.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={enrollId}
            onChange={(e) => setEnrollId(e.target.value)}
            placeholder="node id"
            aria-label="Enroll node id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={enrollLabel}
            onChange={(e) => setEnrollLabel(e.target.value)}
            placeholder="label (optional)"
            aria-label="Enroll node label"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Enroll", "enroll", (confirm) =>
                enrollNode(
                  { node_id: enrollId.trim(), label: enrollLabel.trim() || undefined },
                  confirm,
                ),
              )
            }
            disabled={busy === "enroll" || !enrollId.trim()}
            aria-label="Enroll node"
          >
            {busy === "enroll" ? <Spinner /> : "enroll"}
          </Button>
        </div>
      </section>

      <section
        aria-label="Node roster card"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-medium text-text-secondary">Nodes</h3>
            <p className="text-[0.6875rem] text-text-tertiary">
              {authority || "Enrolled nodes and live links."}
            </p>
          </div>
        </div>

        <div className="mt-2">
          {state === "loading" ? (
            <p className="flex items-center gap-2 text-sm text-text-tertiary">
              <Spinner className="shrink-0" /> Loading nodes…
            </p>
          ) : state === "error" ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : nodes.length === 0 ? (
            <p className="text-sm text-text-tertiary">No nodes found.</p>
          ) : (
            <ul className="flex flex-col gap-1" aria-label="Node roster">
              {nodes.map((n) => (
                <li
                  key={n.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border bg-background-base/60 px-3 py-2 text-xs"
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-text-secondary">{n.id}</div>
                    <div className="text-text-tertiary">
                      {[n.name, n.state ? `state: ${n.state}` : ""]
                        .filter((part): part is string => typeof part === "string" && part.length > 0)
                        .join(" · ") || "—"}
                    </div>
                    {n.state ? (
                      <div className="mt-1 flex flex-wrap gap-1">
                        <span className="border border-border px-1.5 py-0.5 text-[0.6875rem] text-text-tertiary">
                          {n.state}
                        </span>
                        <span
                          className={cn(
                            "border border-border px-1.5 py-0.5 text-[0.6875rem]",
                            n.manageable ? "text-text-secondary" : "text-text-tertiary",
                          )}
                        >
                          {n.manageable ? "manageable" : "not manageable"}
                        </span>
                      </div>
                    ) : null}
                  </div>
                  <div className="ml-auto flex flex-wrap items-center gap-1">
                    <Button
                      ghost
                      size="sm"
                      onClick={() => describe(n.id)}
                      aria-label={`Describe node ${n.id}`}
                    >
                      describe
                    </Button>
                    {n.manageable ? (
                      <>
                        <Button
                          ghost
                          size="sm"
                          onClick={() =>
                            mutate("Approve", `approve:${n.id}`, (confirm) =>
                              approveNode(n.id, confirm),
                            )
                          }
                          disabled={busy === `approve:${n.id}`}
                          aria-label={`Approve node ${n.id}`}
                        >
                          {busy === `approve:${n.id}` ? <Spinner /> : "approve"}
                        </Button>
                        <Button
                          ghost
                          size="sm"
                          onClick={() =>
                            mutate("Revoke", `revoke:${n.id}`, (confirm) =>
                              revokeNode(n.id, confirm),
                            )
                          }
                          disabled={busy === `revoke:${n.id}`}
                          aria-label={`Revoke node ${n.id}`}
                        >
                          {busy === `revoke:${n.id}` ? <Spinner /> : "revoke"}
                        </Button>
                        <Button
                          ghost
                          size="sm"
                          onClick={() =>
                            mutate("Remove", `remove:${n.id}`, (confirm) =>
                              removeNode(n.id, confirm),
                            )
                          }
                          disabled={busy === `remove:${n.id}`}
                          aria-label={`Remove node ${n.id}`}
                        >
                          {busy === `remove:${n.id}` ? <Spinner /> : "remove"}
                        </Button>
                      </>
                    ) : (
                      <button
                        type="button"
                        disabled
                        data-state="refused"
                        title={n.manage_reason ?? "This node cannot be managed from the dashboard."}
                        aria-label={`Manage node ${n.id} unavailable`}
                        aria-disabled="true"
                        className="cursor-not-allowed border border-border px-2 py-1 text-xs text-text-tertiary opacity-60"
                      >
                        manage unavailable
                      </button>
                    )}
                  </div>
                  {n.manageable ? null : (
                    <span className="w-full text-text-tertiary">
                      {n.manage_reason ?? "This node cannot be managed from the dashboard."}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {selectedId ? (
        <section
          aria-label="Node detail"
          className="border border-border bg-background-base/60 px-3 py-2"
        >
          <h3 className="font-mono text-xs text-text-secondary">{selectedId}</h3>
          <p className="text-[0.6875rem] text-text-tertiary">
            Detail, capabilities, dispatch, and rename.
          </p>
          {detailError ? (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {detailError}
            </p>
          ) : detail ? (
            <div className="mt-1 text-xs text-text-tertiary">
              {detail.name ? <p>name: {detail.name}</p> : null}
              {detail.state ? <p>state: {detail.state}</p> : null}
              {detail.last_seen ? <p>last seen: {detail.last_seen}</p> : null}
              {detail.capabilities ? <p>capabilities: {detail.capabilities.join(", ")}</p> : null}
            </div>
          ) : (
            <p className="mt-1 flex items-center gap-2 text-xs text-text-tertiary">
              <Spinner className="shrink-0" /> Describing…
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              ghost
              size="sm"
              onClick={() => refreshCapabilities(selectedId)}
              disabled={busy === `caps:${selectedId}`}
              aria-label={`Refresh capabilities for ${selectedId}`}
            >
              {busy === `caps:${selectedId}` ? <Spinner /> : "refresh capabilities"}
            </Button>
          </div>
          {capabilities ? (
            <p className="mt-1 text-xs text-text-tertiary">
              capabilities: {capabilities.length > 0 ? capabilities.join(", ") : "none reported"}
            </p>
          ) : null}

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={dispatchTask}
              onChange={(e) => setDispatchTask(e.target.value)}
              placeholder="task to dispatch"
              aria-label={`Dispatch task for ${selectedId}`}
              className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
            />
            <Button
              ghost
              size="sm"
              onClick={() =>
                mutate("Dispatch", `dispatch:${selectedId}`, (confirm) =>
                  dispatchNode({ node_id: selectedId, task: dispatchTask.trim() }, confirm),
                )
              }
              disabled={busy === `dispatch:${selectedId}` || !dispatchTask.trim()}
              aria-label={`Dispatch to ${selectedId}`}
            >
              {busy === `dispatch:${selectedId}` ? <Spinner /> : "dispatch"}
            </Button>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              placeholder="new node name"
              aria-label={`Rename ${selectedId}`}
              className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
            />
            <Button
              ghost
              size="sm"
              onClick={() =>
                mutate("Rename", `rename:${selectedId}`, (confirm) =>
                  renameNode({ node_id: selectedId, name: renameName.trim() }, confirm),
                )
              }
              disabled={busy === `rename:${selectedId}` || !renameName.trim()}
              aria-label={`Rename ${selectedId}`}
            >
              {busy === `rename:${selectedId}` ? <Spinner /> : "rename"}
            </Button>
          </div>
          <p className="mt-1 text-[0.6875rem] text-text-tertiary">
            Renaming a node may require re-enrollment before it accepts new work.
          </p>
        </section>
      ) : null}

      {notice ? (
        <p role="status" className="text-xs text-text-secondary">
          {notice}
        </p>
      ) : null}

      {pendingConfirm ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm node action"
          className="border border-border bg-background-base px-3 py-2 text-xs"
        >
          <p className="text-text-secondary">{pendingConfirm.detail}</p>
          <div className="mt-2 flex gap-2">
            <Button ghost size="sm" onClick={() => settleConfirm(true)} aria-label="Confirm">
              Confirm
            </Button>
            <Button ghost size="sm" onClick={() => settleConfirm(false)} aria-label="Cancel">
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
