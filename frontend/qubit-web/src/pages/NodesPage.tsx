/**
 * Nodes page (route /nodes). Enrolled-node console: list/describe, confirm-gated
 * enroll / approve / revoke / remove / dispatch / rename, and per-node
 * capability refresh. Unmanageable rows render disabled with their reason and
 * never fire. There are no message-gateway controls on this page.
 *
 * Backend handlers (already registered in `voicebridge/`, verified live):
 *   GET  /api/qubit/nodes/list          (nodes.list)
 *   GET  /api/qubit/nodes/describe      (nodes.describe, ?node_id=)
 *   POST /api/qubit/nodes/enroll        (nodes.enroll, confirm-gated)
 *   POST /api/qubit/nodes/approve       (nodes.approve, confirm-gated)
 *   POST /api/qubit/nodes/revoke        (nodes.revoke, confirm-gated)
 *   POST /api/qubit/nodes/remove        (nodes.remove, confirm-gated)
 *   GET  /api/qubit/nodes/capabilities  (nodes.capabilities, ?node_id=)
 *   POST /api/qubit/nodes/dispatch      (nodes.dispatch, confirm-gated)
 *   POST /api/qubit/nodes/rename        (nodes.rename, confirm-gated)
 *
 * Built on the shipped openclaw render-helper contract
 * (`@/components/console-ui`): a `SettingsPage` root holding a
 * `SettingsPageHeader`, a summary tile strip, and `SettingsSection` groups
 * (enroll / roster / detail). `.settings-row__control` is emitted by the helper
 * ONLY when a control is passed — a control-free row never renders an empty
 * wrapper.
 */
import { useCallback, useEffect, useState } from "react";

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
import {
  SettingsEmpty,
  SettingsLoadingSkeleton,
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsSummary,
  SettingsValue,
} from "@/components/console-ui";

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

  const manageReason = (n: NodeRow) =>
    n.manage_reason ?? "This node cannot be managed from the dashboard.";

  const manageableCount = nodes.filter((n) => n.manageable).length;

  return (
    <SettingsPage wide>
      <div className="contents" aria-label="Nodes">
        <SettingsPageHeader
          title="Nodes"
          description="Enrolled nodes: describe, approve, dispatch, rename, revoke, remove."
          actions={
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={load}
              disabled={state === "loading"}
              aria-label="Refresh nodes"
            >
              {state === "loading" ? <Spinner /> : "refresh"}
            </button>
          }
        />

        <SettingsSummary
          items={[
            { label: "Enrolled", value: state === "ready" ? nodes.length : "…" },
            { label: "Manageable", value: state === "ready" ? manageableCount : "…" },
            { label: "Authority", value: authority || "—" },
          ]}
        />

        <SettingsSection
          ariaLabel="Enroll node form"
          title="Add a node"
          description="Enroll a new node by id; enrollment is confirm-gated."
        >
          <SettingsRow
            stacked
            title="Enroll node"
            description="Add a node by id with an optional label."
            control={
              <>
                <input
                  type="text"
                  value={enrollId}
                  onChange={(e) => setEnrollId(e.target.value)}
                  placeholder="node id"
                  aria-label="Enroll node id"
                  className="settings-input"
                />
                <input
                  type="text"
                  value={enrollLabel}
                  onChange={(e) => setEnrollLabel(e.target.value)}
                  placeholder="label (optional)"
                  aria-label="Enroll node label"
                  className="settings-input"
                />
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
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
                </button>
              </>
            }
          />
        </SettingsSection>

        <SettingsSection
          ariaLabel="Node roster card"
          title="Roster"
          count={state === "ready" ? nodes.length : undefined}
          description={authority || "Enrolled nodes and live links."}
        >
          {state === "loading" ? (
            <SettingsLoadingSkeleton rows={3} />
          ) : state === "error" ? (
            <SettingsRow role="alert" title="Node list failed" description={error} />
          ) : nodes.length === 0 ? (
            <SettingsEmpty title="No nodes enrolled yet">
              Use the enroll row above to add one.
            </SettingsEmpty>
          ) : (
            <div className="settings-subrows" role="group" aria-label="Node roster">
              {nodes.map((n) => (
                <SettingsRow
                  key={n.id}
                  title={
                    <>
                      <SettingsValue mono>{n.id}</SettingsValue>
                      {n.name ? ` · ${n.name}` : ""}
                    </>
                  }
                  description={
                    <>
                      <span className="settings-row__desc">
                        {n.state ? `state: ${n.state} · ` : ""}
                        {n.manageable ? "manageable" : "not manageable"}
                      </span>
                      {n.manageable ? null : (
                        <span className="settings-row__desc">{manageReason(n)}</span>
                      )}
                    </>
                  }
                  control={
                    <>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => describe(n.id)}
                        aria-label={`Describe node ${n.id}`}
                      >
                        describe
                      </button>
                      {n.manageable ? (
                        <>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() =>
                              mutate("Approve", `approve:${n.id}`, (confirm) =>
                                approveNode(n.id, confirm),
                              )
                            }
                            disabled={busy === `approve:${n.id}`}
                            aria-label={`Approve node ${n.id}`}
                          >
                            {busy === `approve:${n.id}` ? <Spinner /> : "approve"}
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() =>
                              mutate("Revoke", `revoke:${n.id}`, (confirm) =>
                                revokeNode(n.id, confirm),
                              )
                            }
                            disabled={busy === `revoke:${n.id}`}
                            aria-label={`Revoke node ${n.id}`}
                          >
                            {busy === `revoke:${n.id}` ? <Spinner /> : "revoke"}
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() =>
                              mutate("Remove", `remove:${n.id}`, (confirm) =>
                                removeNode(n.id, confirm),
                              )
                            }
                            disabled={busy === `remove:${n.id}`}
                            aria-label={`Remove node ${n.id}`}
                          >
                            {busy === `remove:${n.id}` ? <Spinner /> : "remove"}
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          disabled
                          data-state="refused"
                          title={manageReason(n)}
                          aria-label={`Manage node ${n.id} unavailable`}
                          aria-disabled="true"
                          className="btn btn--ghost btn--sm"
                        >
                          manage unavailable
                        </button>
                      )}
                    </>
                  }
                />
              ))}
            </div>
          )}
        </SettingsSection>

        {selectedId ? (
          <SettingsSection
            ariaLabel="Node detail"
            title={`Node ${selectedId}`}
            description="Detail, capabilities, dispatch, and rename."
          >
            <SettingsRow
              title="Detail"
              description={
                detailError ? (
                  <span role="alert">{detailError}</span>
                ) : detail ? (
                  <span className="settings-row__desc">
                    {detail.name ? `name: ${detail.name} · ` : ""}
                    {detail.state ? `state: ${detail.state} · ` : ""}
                    {detail.last_seen ? `last seen: ${detail.last_seen} · ` : ""}
                    capabilities:{" "}
                    {detail.capabilities && detail.capabilities.length > 0
                      ? detail.capabilities.join(", ")
                      : "none reported"}
                  </span>
                ) : (
                  <span className="settings-row__desc flex items-center gap-2">
                    <Spinner className="shrink-0" /> Describing…
                  </span>
                )
              }
              control={
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => refreshCapabilities(selectedId)}
                  disabled={busy === `caps:${selectedId}`}
                  aria-label={`Refresh capabilities for ${selectedId}`}
                >
                  {busy === `caps:${selectedId}` ? <Spinner /> : "refresh capabilities"}
                </button>
              }
            />

            {capabilities ? (
              <SettingsRow
                title="Capabilities"
                description={
                  capabilities.length > 0
                    ? `capabilities: ${capabilities.join(", ")}`
                    : "none reported"
                }
              />
            ) : null}

            <SettingsRow
              stacked
              title="Dispatch"
              description="Send a task to this node (confirm-gated)."
              control={
                <>
                  <input
                    type="text"
                    value={dispatchTask}
                    onChange={(e) => setDispatchTask(e.target.value)}
                    placeholder="task to dispatch"
                    aria-label={`Dispatch task for ${selectedId}`}
                    className="settings-input"
                  />
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() =>
                      mutate("Dispatch", `dispatch:${selectedId}`, (confirm) =>
                        dispatchNode({ node_id: selectedId, task: dispatchTask.trim() }, confirm),
                      )
                    }
                    disabled={busy === `dispatch:${selectedId}` || !dispatchTask.trim()}
                    aria-label={`Dispatch to ${selectedId}`}
                  >
                    {busy === `dispatch:${selectedId}` ? <Spinner /> : "dispatch"}
                  </button>
                </>
              }
            />

            <SettingsRow
              stacked
              title="Rename"
              description="Renaming a node may require re-enrollment before it accepts new work."
              control={
                <>
                  <input
                    type="text"
                    value={renameName}
                    onChange={(e) => setRenameName(e.target.value)}
                    placeholder="new node name"
                    aria-label={`Rename ${selectedId}`}
                    className="settings-input"
                  />
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() =>
                      mutate("Rename", `rename:${selectedId}`, (confirm) =>
                        renameNode({ node_id: selectedId, name: renameName.trim() }, confirm),
                      )
                    }
                    disabled={busy === `rename:${selectedId}` || !renameName.trim()}
                    aria-label={`Rename node ${selectedId}`}
                  >
                    {busy === `rename:${selectedId}` ? <Spinner /> : "rename"}
                  </button>
                </>
              }
            />
          </SettingsSection>
        ) : (
          <SettingsSection title="Node detail">
            <SettingsEmpty title="No node selected">
              Describe a node from the roster to inspect its capabilities, dispatch a task,
              or rename it.
            </SettingsEmpty>
          </SettingsSection>
        )}

        {notice ? (
          <p role="status" className="settings-status settings-status--accent">
            {notice}
          </p>
        ) : null}

        {pendingConfirm ? (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Confirm node action"
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
                    onClick={() => settleConfirm(true)}
                    aria-label="Confirm"
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => settleConfirm(false)}
                    aria-label="Cancel"
                  >
                    Cancel
                  </button>
                </>
              }
            />
          </div>
        ) : null}
      </div>
    </SettingsPage>
  );
}
