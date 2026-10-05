/**
 * TaskflowsPage — route `/qubit-taskflows`.
 *
 * SEPARATE Qubit-branded page for taskflow/standing-orders/hooks
 * semantics, ported onto the existing `schedules.*` backend. This page does
 * NOT extend `/qubit-scheduler` (owner's separation rule): the scheduler stays
 * the media/cron surface; this page is the durable-flows surface.
 *
 * Structure mirrors the ported settings surfaces in this app
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`).
 *
 * - Flows list: standing orders (recurring schedules) + one-shots (`run_at`),
 *   read from the real cron backend. Create flow (schedule expression +
 *   action + confirm), enable/disable toggle (honest: the backend's
 *   `cron_update` takes the full mutation, so the row's fields ride along
 *   unchanged with only `enabled` flipped), trigger-now, delete (confirm).
 * - Hooks: the bundled hooks with live guard states and
 *   enable/disable/cancel on the family bridge (staged-confirm).
 * - Honest scope: durable flows only. No ephemeral-suggestion registry —
 *   that refusal lives on `/qubit-task-inbox`.
 */

import { useCallback, useEffect, useState } from "react";
import {
  DISABLED_FLOW_ACTIONS,
  HOOK_CATALOG,
  cancelFlowHook,
  createFlow,
  deleteFlow,
  disableHook,
  enableHook,
  listFlows,
  listHookStates,
  setFlowEnabled,
  triggerFlowNow,
  type FlowRow,
  type HookState,
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

const INPUT_CLASS =
  "settings-input min-w-0 border border-border bg-background-base px-2 py-1 text-sm";

function FlowActions({
  flow,
  busy,
  onStage,
}: {
  flow: FlowRow;
  busy: boolean;
  onStage: (op: StagedOp) => void;
}) {
  return (
    <div className="settings-row__control">
      <button
        type="button"
        aria-label={`flow-toggle-${flow.id}`}
        disabled={busy}
        className="btn btn--sm disabled:opacity-50"
        onClick={() =>
          onStage({
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
        className="btn btn--sm btn--ghost disabled:opacity-50"
        onClick={() =>
          onStage({
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
        className="btn btn--sm btn--ghost disabled:opacity-50"
        onClick={() =>
          onStage({
            title: "Delete flow",
            detail: `Delete flow ${flow.name}? This cannot be undone.`,
            run: () => deleteFlow(flow.id, () => true),
          })
        }
      >
        Delete
      </button>
    </div>
  );
}

function FlowRowView({
  flow,
  busy,
  onStage,
}: {
  flow: FlowRow;
  busy: boolean;
  onStage: (op: StagedOp) => void;
}) {
  return (
    <div className="settings-row" aria-label={`flow-row-${flow.id}`}>
      <div className="settings-row__text">
        <div className="settings-row__title">
          {flow.name}
          <span className="settings-status settings-status--ok ml-2" role="note">
            <span className="settings-status__dot" aria-hidden="true" />
            {flow.enabled ? "enabled" : "disabled"}
          </span>
        </div>
        <p className="settings-row__desc">
          {flow.schedule || "no schedule"}
          {flow.lastStatus ? ` · last: ${flow.lastStatus}` : ""}
        </p>
        {flow.prompt ? <p className="settings-row__desc">{flow.prompt}</p> : null}
      </div>
      <FlowActions flow={flow} busy={busy} onStage={onStage} />
    </div>
  );
}

export default function TaskflowsPage() {
  const [flows, setFlows] = useState<FlowRow[]>([]);
  const [hookStates, setHookStates] = useState<HookState[]>([]);
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
    try {
      setHookStates(await listHookStates());
    } catch (e) {
      setErrorText(refusalOrError(e, `Hooks load failed: ${e}`));
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
    <div className="settings-page settings-page--wide" data-testid="taskflows-page">
      <p className="settings-page__intro">
        Durable flows (standing orders + one-shots + hooks) on the Qubit schedules
        backend. Every control here has a live route behind it.
      </p>

      <section aria-label="taskflows-scope-notice" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Durable flows only</h2>
          <p className="settings-section__desc">
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
        </div>
      </section>

      <section aria-label="standing-orders" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Standing orders <span className="settings-count">{standingOrders.length}</span>
            </h2>
            <p className="settings-section__desc">Recurring schedules that keep running.</p>
          </div>
          <div className="settings-section__actions">
            <button
              type="button"
              aria-label="taskflows-refresh"
              disabled={busy}
              className="btn btn--sm disabled:opacity-50"
              onClick={() => void refresh()}
            >
              Refresh
            </button>
          </div>
        </div>
        <div className="settings-group">
          {standingOrders.length === 0 ? (
            <div className="settings-empty">No standing orders. Create one below.</div>
          ) : (
            standingOrders.map((flow) => (
              <FlowRowView key={flow.id} flow={flow} busy={busy} onStage={setPending} />
            ))
          )}
        </div>
      </section>

      <section aria-label="one-shots" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            One-shots <span className="settings-count">{oneShots.length}</span>
          </h2>
          <p className="settings-section__desc">One-time schedules keyed on a run time.</p>
        </div>
        <div className="settings-group">
          {oneShots.length === 0 ? (
            <div className="settings-empty">No one-shot flows.</div>
          ) : (
            oneShots.map((flow) => (
              <FlowRowView key={flow.id} flow={flow} busy={busy} onStage={setPending} />
            ))
          )}
        </div>
      </section>

      <section aria-label="flow-create" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Create flow</h2>
          <p className="settings-section__desc">
            Name, schedule expression, and the action to run. Confirms before writing.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Name</div>
            </div>
            <div className="settings-row__control">
              <input
                aria-label="flow-create-name"
                className={INPUT_CLASS}
                placeholder="flow name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Schedule</div>
              <p className="settings-row__desc">Cron expression, e.g. 0 8 * * 1-5.</p>
            </div>
            <div className="settings-row__control">
              <input
                aria-label="flow-create-schedule"
                className={INPUT_CLASS}
                placeholder="schedule (0 8 * * 1-5)"
                value={newSchedule}
                onChange={(e) => setNewSchedule(e.target.value)}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Action</div>
              <p className="settings-row__desc">The prompt/action the flow runs.</p>
            </div>
            <div className="settings-row__control">
              <input
                aria-label="flow-create-prompt"
                className={INPUT_CLASS}
                placeholder="action to run"
                value={newPrompt}
                onChange={(e) => setNewPrompt(e.target.value)}
              />
            </div>
          </div>
          <div className="settings-row settings-row--actions">
            <div className="settings-row__text">
              <div className="settings-row__title">Create</div>
              {!canCreate ? (
                <p className="settings-row__desc" data-testid="flow-create-hint">
                  Fill name, schedule, and action to enable Create.
                </p>
              ) : null}
            </div>
            <div className="settings-row__control">
              <button
                type="button"
                aria-label="flow-create-submit"
                disabled={busy || !canCreate}
                title={canCreate ? "Create this flow" : "Name, schedule, and action are required"}
                className="btn btn--sm disabled:opacity-50"
                onClick={() =>
                  setPending({
                    title: "Create flow",
                    detail: `Create flow ${newName.trim()} on schedule ${newSchedule.trim()}?`,
                    run: () =>
                      createFlow(
                        {
                          name: newName.trim(),
                          schedule: newSchedule.trim(),
                          prompt: newPrompt.trim(),
                        },
                        () => true,
                      ),
                  })
                }
              >
                Create
              </button>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="hooks" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Hooks <span className="settings-count">{HOOK_CATALOG.length}</span>
          </h2>
          <p className="settings-section__desc">
            Event-driven hooks on the live bridge — enable/disable flips the guard
            immediately (staged-confirm).
          </p>
        </div>
        <div className="settings-group">
          {HOOK_CATALOG.map((hook) => {
            const live = hookStates.find((s) => s.name === hook.name);
            const enabled = live?.enabled === true;
            return (
              <div className="settings-row" key={hook.name} aria-label={`hook-row-${hook.name}`}>
                <div className="settings-row__text">
                  <div className="settings-row__title">
                    {hook.name}
                    <span
                      className="settings-status settings-status--ok ml-2"
                      role="note"
                      aria-label={`hook-state-${hook.name}`}
                    >
                      <span className="settings-status__dot" aria-hidden="true" />
                      {live ? (enabled ? "enabled" : "disabled") : "state unknown"}
                    </span>
                  </div>
                  <p className="settings-row__desc">{hook.events.join(", ")}</p>
                  <p className="settings-row__desc">{hook.description}</p>
                </div>
                <div className="settings-row__control">
                  <button
                    type="button"
                    aria-label={`hook-enable-${hook.name}`}
                    disabled={busy || enabled}
                    title={enabled ? "Hook is already enabled" : "Enable this hook"}
                    className="btn btn--sm disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Enable hook",
                        detail: `Enable hook ${hook.name}?`,
                        run: () => enableHook(hook.name, () => true),
                      })
                    }
                  >
                    Enable
                  </button>
                  <button
                    type="button"
                    aria-label={`hook-disable-${hook.name}`}
                    disabled={busy || !enabled}
                    title={!enabled ? "Hook is already disabled" : "Disable this hook"}
                    className="btn btn--sm btn--ghost disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Disable hook",
                        detail: `Disable hook ${hook.name}?`,
                        run: () => disableHook(hook.name, () => true),
                      })
                    }
                  >
                    Disable
                  </button>
                  <button
                    type="button"
                    aria-label={`hook-cancel-${hook.name}`}
                    disabled={busy || !enabled}
                    title="Cancel this flow (disables the hook)"
                    className="btn btn--sm btn--ghost disabled:opacity-50"
                    onClick={() =>
                      setPending({
                        title: "Cancel flow",
                        detail: `Cancel flow for hook ${hook.name}? This disables the hook.`,
                        run: () => cancelFlowHook(hook.name, () => true),
                      })
                    }
                  >
                    Cancel flow
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section aria-label="flow-scope-notes" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Out of scope on this page</h2>
        </div>
        <div className="settings-group">
          {DISABLED_FLOW_ACTIONS.map((row) => (
            <div
              className="settings-row settings-row--stacked"
              key={row.action}
              aria-label={`disabled-flow-${row.action}`}
              data-state="refused"
            >
              <div className="settings-row__text">
                <div className="settings-row__title font-mono">{row.action}</div>
                <p className="settings-row__desc">
                  {row.code} — {row.reason} Counterpart: {row.counterpart}
                </p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {resultText && (
        <pre
          aria-label="op-result"
          className="max-h-64 overflow-auto border border-border bg-background/40 p-3 text-xs"
        >
          {resultText}
        </pre>
      )}
      {errorText && (
        <p role="alert" aria-label="op-error" className="border border-border p-3 text-sm text-destructive">
          {errorText}
        </p>
      )}

      {pending && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="staged-op-confirm"
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
        >
          <div className="border border-border bg-card p-4 shadow-2xl">
            <h2 className="text-sm font-semibold">{pending.title}</h2>
            <p className="mt-1 text-sm">{pending.detail}</p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                aria-label="confirm-staged-op"
                disabled={busy}
                className="btn btn--sm disabled:opacity-50"
                onClick={() => void settle(pending)}
              >
                Confirm
              </button>
              <button
                type="button"
                aria-label="cancel-staged-op"
                disabled={busy}
                className="btn btn--sm btn--ghost disabled:opacity-50"
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
