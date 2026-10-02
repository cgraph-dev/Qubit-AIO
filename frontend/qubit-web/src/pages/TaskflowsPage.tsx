/**
 * TaskflowsPage — route `/qubit-taskflows`.
 *
 * SEPARATE Qubit-branded page for OpenClaw Taskflow/standing-orders/hooks
 * semantics (upstream `docs/automation/{taskflow,standing-orders,hooks}.md`),
 * ported onto the existing `schedules.*` backend. This page does NOT extend
 * `/qubit-scheduler` (owner's separation rule): the scheduler stays the
 * media/cron surface; this page is the durable-flows surface.
 *
 * - Flows list: standing orders (recurring schedules) + one-shots (`run_at`),
 *   read from the real cron backend. Create flow (schedule expression +
 *   action + confirm), enable/disable toggle (honest: the backend's
 *   `cron_update` takes the full mutation, so the row's fields ride along
 *   unchanged with only `enabled` flipped), trigger-now, delete (confirm).
 * - Hooks: the 5 upstream bundled hooks as catalog data with enable/disable
 *   rendered disabled-with-reason (NEEDS-BRIDGE — no backend route).
 * - Honest scope: durable flows only. No ephemeral-suggestion registry —
 *   that refusal lives on `/qubit-task-inbox`.
 *
 * NOTE: router wiring (App.tsx) is intentionally NOT in this file — this
 * change adds new files only. Wire `/qubit-taskflows` to this component
 * separately.
 */

import { useCallback, useEffect, useState } from "react";
import {
  DISABLED_FLOW_ACTIONS,
  HOOK_CATALOG,
  createFlow,
  deleteFlow,
  listFlows,
  setFlowEnabled,
  triggerFlowNow,
  type FlowRow,
} from "@/lib/ocFamilyFlowsApi";
import { refusalOrError } from "@/lib/policy-refusal";

interface StagedOp {
  title: string;
  detail: string;
  run: () => Promise<unknown>;
}

function formatResult(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function TaskflowsPage() {
  const [flows, setFlows] = useState<FlowRow[]>([]);
  const [newName, setNewName] = useState("");
  const [newSchedule, setNewSchedule] = useState("");
  const [newPrompt, setNewPrompt] = useState("");
  const [pending, setPending] = useState<StagedOp | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultText, setResultText] = useState("");
  const [errorText, setErrorText] = useState("");

  const refresh = useCallback(async () => {
    try {
      setFlows(await listFlows());
    } catch (e) {
      setErrorText(refusalOrError(e, `Load failed: ${e}`));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const settle = useCallback(
    async (op: StagedOp) => {
      setBusy(true);
      setErrorText("");
      setResultText("");
      try {
        const out = await op.run();
        setResultText(formatResult(out));
        await refresh();
      } catch (e) {
        setErrorText(refusalOrError(e, `Operation failed: ${e}`));
      } finally {
        setBusy(false);
        setPending(null);
      }
    },
    [refresh],
  );

  const standingOrders = flows.filter((f) => f.kind === "standing-order");
  const oneShots = flows.filter((f) => f.kind === "one-shot");
  const canCreate = newName.trim() && newSchedule.trim() && newPrompt.trim();

  return (
    <div className="flex flex-col gap-6 p-6" data-testid="taskflows-page">
      <header>
        <h1 className="text-xl font-semibold">Taskflows</h1>
        <p className="text-sm text-muted-foreground">
          Durable flows (standing orders + one-shots + hooks) on the Qubit schedules backend.
        </p>
      </header>

      <section aria-label="taskflows-scope-notice" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Durable flows only</h2>
        <p className="mt-1 text-sm">
          This page lists cron-backed durable flows. There is no ephemeral-suggestion
          registry here — suggestions live on the{" "}
          <a className="underline" href="/qubit-task-inbox">
            task-inbox page
          </a>
          . Scheduling media stays on the{" "}
          <a className="underline" href="/qubit-scheduler">
            scheduler page
          </a>
          .
        </p>
      </section>

      <section aria-label="standing-orders" className="rounded border p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Standing orders ({standingOrders.length})</h2>
          <button
            type="button"
            aria-label="taskflows-refresh"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
        {standingOrders.length === 0 ? (
          <p className="mt-2 text-sm">No standing orders. Create one below.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {standingOrders.map((flow) => (
              <li key={flow.id} aria-label={`flow-row-${flow.id}`} className="rounded border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{flow.name}</span>
                  <span className="text-xs">{flow.schedule || "no schedule"}</span>
                  <span className="text-xs">{flow.enabled ? "enabled" : "disabled"}</span>
                  {flow.lastStatus && <span className="text-xs">last: {flow.lastStatus}</span>}
                </div>
                {flow.prompt && <p className="mt-1 text-xs">{flow.prompt}</p>}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    aria-label={`flow-toggle-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: flow.enabled ? "Disable flow" : "Enable flow",
                        detail: `${flow.enabled ? "Disable" : "Enable"} flow ${flow.name}?`,
                        run: () => setFlowEnabled(flow, !flow.enabled, () => true),
                      })
                    }
                  >
                    {flow.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    type="button"
                    aria-label={`flow-trigger-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Trigger flow now",
                        detail: `Run flow ${flow.name} now?`,
                        run: () => triggerFlowNow(flow.id, () => true),
                      })
                    }
                  >
                    Trigger now
                  </button>
                  <button
                    type="button"
                    aria-label={`flow-delete-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Delete flow",
                        detail: `Delete flow ${flow.name}? This cannot be undone.`,
                        run: () => deleteFlow(flow.id, () => true),
                      })
                    }
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="one-shots" className="rounded border p-4">
        <h2 className="text-sm font-semibold">One-shots ({oneShots.length})</h2>
        {oneShots.length === 0 ? (
          <p className="mt-2 text-sm">No one-shot flows.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {oneShots.map((flow) => (
              <li key={flow.id} aria-label={`flow-row-${flow.id}`} className="rounded border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{flow.name}</span>
                  <span className="text-xs">{flow.schedule || "no schedule"}</span>
                  <span className="text-xs">{flow.enabled ? "enabled" : "disabled"}</span>
                </div>
                {flow.prompt && <p className="mt-1 text-xs">{flow.prompt}</p>}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    aria-label={`flow-toggle-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: flow.enabled ? "Disable flow" : "Enable flow",
                        detail: `${flow.enabled ? "Disable" : "Enable"} flow ${flow.name}?`,
                        run: () => setFlowEnabled(flow, !flow.enabled, () => true),
                      })
                    }
                  >
                    {flow.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    type="button"
                    aria-label={`flow-trigger-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Trigger flow now",
                        detail: `Run flow ${flow.name} now?`,
                        run: () => triggerFlowNow(flow.id, () => true),
                      })
                    }
                  >
                    Trigger now
                  </button>
                  <button
                    type="button"
                    aria-label={`flow-delete-${flow.id}`}
                    disabled={busy}
                    className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Delete flow",
                        detail: `Delete flow ${flow.name}? This cannot be undone.`,
                        run: () => deleteFlow(flow.id, () => true),
                      })
                    }
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="flow-create" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Create flow</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="flow-create-name"
            className="rounded border px-2 py-1 text-sm"
            placeholder="flow name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            aria-label="flow-create-schedule"
            className="rounded border px-2 py-1 text-sm"
            placeholder="schedule (0 8 * * 1-5)"
            value={newSchedule}
            onChange={(e) => setNewSchedule(e.target.value)}
          />
          <input
            aria-label="flow-create-prompt"
            className="rounded border px-2 py-1 text-sm"
            placeholder="action to run"
            value={newPrompt}
            onChange={(e) => setNewPrompt(e.target.value)}
          />
          <button
            type="button"
            aria-label="flow-create-submit"
            disabled={busy || !canCreate}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              setPending({
                title: "Create flow",
                detail: `Create flow ${newName.trim()} on schedule ${newSchedule.trim()}?`,
                run: () =>
                  createFlow(
                    { name: newName.trim(), schedule: newSchedule.trim(), prompt: newPrompt.trim() },
                    () => true,
                  ),
              })
            }
          >
            Create
          </button>
        </div>
      </section>

      <section aria-label="hooks" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Hooks ({HOOK_CATALOG.length})</h2>
        <p className="mt-1 text-sm">
          Event-driven hooks have no backend route yet — enable/disable is
          disabled with its reason below.
        </p>
        <ul className="mt-2 flex flex-col gap-2">
          {HOOK_CATALOG.map((hook) => (
            <li key={hook.name} aria-label={`hook-row-${hook.name}`} className="rounded border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{hook.name}</span>
                <span className="text-xs">{hook.events.join(", ")}</span>
              </div>
              <p className="mt-1 text-xs">{hook.description}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  aria-label={`hook-enable-${hook.name}`}
                  disabled
                  title="NEEDS-BRIDGE: no backend hooks route (/api/qubit/flows/hook_enable)"
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                >
                  Enable (needs bridge)
                </button>
                <button
                  type="button"
                  aria-label={`hook-disable-${hook.name}`}
                  disabled
                  title="NEEDS-BRIDGE: no backend hooks route (/api/qubit/flows/hook_disable)"
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                >
                  Disable (needs bridge)
                </button>
              </div>
            </li>
          ))}
        </ul>
        <ul className="mt-2 flex flex-col gap-1">
          {DISABLED_FLOW_ACTIONS.map((row) => (
            <li key={row.action} aria-label={`disabled-flow-${row.action}`} className="text-xs">
              {row.action}: {row.code} — {row.reason} Counterpart: {row.counterpart}
            </li>
          ))}
        </ul>
      </section>

      {resultText && (
        <pre aria-label="op-result" className="overflow-auto rounded border p-3 text-xs">
          {resultText}
        </pre>
      )}
      {errorText && (
        <p role="alert" aria-label="op-error" className="rounded border p-3 text-sm">
          {errorText}
        </p>
      )}

      {pending && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="staged-op-confirm"
          className="fixed inset-0 flex items-center justify-center bg-black/40"
        >
          <div className="rounded border bg-white p-4">
            <h2 className="text-sm font-semibold">{pending.title}</h2>
            <p className="mt-1 text-sm">{pending.detail}</p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                aria-label="confirm-staged-op"
                disabled={busy}
                className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                onClick={() => void settle(pending)}
              >
                Confirm
              </button>
              <button
                type="button"
                aria-label="cancel-staged-op"
                disabled={busy}
                className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                onClick={() => setPending(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
