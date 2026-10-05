/**
 * WorktreesPage — route `/worktrees`.
 *
 * Separate Qubit page for confined git worktree lifecycle: list, branches,
 * create (confirm), remove (confirm), plus the two honest counterparts.
 * Restore and prune are SAFE_EQUIVALENT local operations and are labeled
 * with their typed codes — they are never presented as upstream restores.
 *
 * Structural contract mirrors the upstream Lit page
 * (upstream/ui/src/pages/worktrees/worktrees-page.ts) which composes
 * `renderSettingsPage` > `renderSettingsSection` > `renderSettingsGroup` >
 * `renderSettingsRow` from upstream/ui/src/components/settings-ui.ts:
 *   .settings-page
 *     .settings-section
 *       .settings-section__header > .settings-section__copy
 *         > h2.settings-section__heading (+ .settings-count) / __desc
 *         > .settings-section__actions        (only when actions exist)
 *       .settings-group > .settings-row
 *         > .settings-row__text > __title / __desc
 *         > .settings-row__control            (ONLY when a control exists)
 * A row with no control never renders an empty `.settings-row__control`.
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  createWorktree,
  listBranches,
  listWorktrees,
  pruneWorktrees,
  removeWorktree,
  restoreWorktree,
  WORKTREES_PRUNE_CODE,
  WORKTREES_RESTORE_CODE,
} from "@/lib/ocFamilySessionsApi";
import type {
  BranchEntry,
  WorktreeRow,
} from "@/lib/ocFamilySessionsApi";
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

/** Upstream `renderSettingsRowText`: title + optional description spans. */
function RowText({ title, description }: { title: string; description?: string }) {
  return (
    <div className="settings-row__text">
      <span className="settings-row__title">{title}</span>
      {description ? (
        <span className="settings-row__desc">{description}</span>
      ) : null}
    </div>
  );
}

/**
 * Upstream `renderSettingsRow`: the `.settings-row__control` wrapper is emitted
 * ONLY when a control exists — an empty control div is the phantom panel.
 */
function SettingsRow({
  title,
  description,
  control,
}: {
  title: string;
  description?: string;
  control?: ReactNode;
}) {
  return (
    <div className="settings-row">
      <RowText title={title} description={description} />
      {control !== undefined && control !== null ? (
        <div className="settings-row__control">{control}</div>
      ) : null}
    </div>
  );
}

export default function WorktreesPage() {
  const [worktrees, setWorktrees] = useState<WorktreeRow[]>([]);
  const [branches, setBranches] = useState<BranchEntry[]>([]);
  const [newPath, setNewPath] = useState("");
  const [newBranch, setNewBranch] = useState("");
  const [pending, setPending] = useState<StagedOp | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultText, setResultText] = useState("");
  const [errorText, setErrorText] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [wt, br] = await Promise.all([listWorktrees(), listBranches()]);
      setWorktrees(wt?.worktrees ?? []);
      setBranches(br?.branches ?? []);
    } catch (e) {
      setErrorText(refusalOrError(e, `Load failed: ${e}`));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const settle = useCallback(async (op: StagedOp) => {
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
  }, [refresh]);

  const createDisabledReason =
    !newPath.trim() || !newBranch.trim()
      ? "Enter both a path and a branch to enable create."
      : null;

  return (
    <div className="settings-page settings-page--wide" data-testid="worktrees-page">
      {/* ── Worktrees ─────────────────────────────────────────────── */}
      <section className="settings-section" aria-label="worktree-list">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Worktrees <span className="settings-count">{worktrees.length}</span>
            </h2>
            <p className="settings-section__desc">
              Confined git worktree lifecycle. Create and remove ask first;
              restore and prune are honest local counterparts, labeled as such.
            </p>
          </div>
          <div className="settings-section__actions">
            <button
              type="button"
              aria-label="worktrees-refresh"
              disabled={busy}
              title={busy ? "Refreshing the worktree listing." : undefined}
              className="btn"
              onClick={() => void refresh()}
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="settings-group">
          {worktrees.length === 0 ? (
            <div className="settings-empty">No worktrees reported.</div>
          ) : (
            worktrees.map((w) => (
              <SettingsRow
                key={w.path}
                title={w.branch || w.path}
                description={
                  [w.path, w.commit ? `commit: ${w.commit}` : "", w.is_main ? "(main)" : ""]
                    .filter(Boolean)
                    .join(" · ")
                }
                control={
                  <>
                    <button
                      type="button"
                      aria-label={`worktree-remove-${w.path}`}
                      disabled={busy || w.is_main}
                      title={
                        w.is_main
                          ? "The main worktree cannot be removed."
                          : busy
                            ? "Another worktree operation is running."
                            : undefined
                      }
                      className="btn danger"
                      onClick={() =>
                        setPending({
                          title: "Remove worktree",
                          detail: `Remove worktree at ${w.path}?`,
                          run: () => removeWorktree(w.path, () => true),
                        })
                      }
                    >
                      Remove
                    </button>
                    <button
                      type="button"
                      aria-label={`worktree-restore-${w.path}`}
                      disabled={busy}
                      title={busy ? "Another worktree operation is running." : undefined}
                      className="btn"
                      onClick={() =>
                        setPending({
                          title: "Restore worktree (local re-add)",
                          detail: `Re-materialize ${w.path} with a confined local add? This is not an upstream snapshot restore (${WORKTREES_RESTORE_CODE}).`,
                          run: () => restoreWorktree(w.path, w.branch ?? "", () => true),
                        })
                      }
                    >
                      Restore (local re-add)
                    </button>
                  </>
                }
              />
            ))
          )}
        </div>
      </section>

      {/* ── Branches ──────────────────────────────────────────────── */}
      <section className="settings-section" aria-label="worktree-branches">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Branches <span className="settings-count">{branches.length}</span>
            </h2>
          </div>
        </div>
        <div className="settings-group">
          {branches.length === 0 ? (
            <div className="settings-empty">No branches reported.</div>
          ) : (
            branches.map((b) => (
              <SettingsRow
                key={b.name}
                title={b.name}
                description={
                  [
                    b.checkedOut ? (b.worktreePath ? `checked out in ${b.worktreePath}` : "checked out") : "",
                    b.isDefault ? "default" : "",
                    b.isRemote ? "remote" : "",
                  ]
                    .filter(Boolean)
                    .join(" · ") || undefined
                }
              />
            ))
          )}
        </div>
      </section>

      {/* ── Create worktree ───────────────────────────────────────── */}
      <section className="settings-section" aria-label="worktree-create">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Create worktree</h2>
          </div>
        </div>
        <div className="settings-group">
          <SettingsRow
            title="Path"
            description="Filesystem path for the new worktree."
            control={
              <input
                aria-label="worktree-new-path"
                className="settings-input"
                placeholder="path"
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
              />
            }
          />
          <SettingsRow
            title="Branch"
            description="Branch to check out in the worktree."
            control={
              <input
                aria-label="worktree-new-branch"
                className="settings-input"
                placeholder="branch"
                value={newBranch}
                onChange={(e) => setNewBranch(e.target.value)}
              />
            }
          />
          <SettingsRow
            title="New worktree"
            control={
              <button
                type="button"
                aria-label="worktree-create-submit"
                disabled={busy || !newPath.trim() || !newBranch.trim()}
                title={busy ? "Another worktree operation is running." : createDisabledReason ?? undefined}
                className="btn"
                onClick={() =>
                  setPending({
                    title: "Create worktree",
                    detail: `Create worktree at ${newPath.trim()} on ${newBranch.trim()}?`,
                    run: () => createWorktree(newPath.trim(), newBranch.trim(), () => true),
                  })
                }
              >
                Create
              </button>
            }
          />
        </div>
      </section>

      {/* ── Restore and prune (honest counterparts) ───────────────── */}
      <section className="settings-section" aria-label="worktree-honest-counterparts">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Restore and prune (honest counterparts)
            </h2>
            <p className="settings-section__desc">
              Restore re-materializes a worktree with a confined local add (
              {WORKTREES_RESTORE_CODE}, SAFE_EQUIVALENT) — it is never presented
              as an upstream snapshot restore. Prune runs a local worktree prune (
              {WORKTREES_PRUNE_CODE}, SAFE_EQUIVALENT) — it is never presented as
              a managed-cache sweep.
            </p>
          </div>
        </div>
        <div className="settings-group">
          <SettingsRow
            title="Prune (local prune)"
            description={`Runs a local worktree prune (${WORKTREES_PRUNE_CODE}).`}
            control={
              <button
                type="button"
                aria-label="worktree-prune-submit"
                disabled={busy}
                title={busy ? "Another worktree operation is running." : undefined}
                className="btn"
                onClick={() =>
                  setPending({
                    title: "Prune worktrees",
                    detail: `Run a local worktree prune (${WORKTREES_PRUNE_CODE})?`,
                    run: () => pruneWorktrees(() => true),
                  })
                }
              >
                Prune (local prune)
              </button>
            }
          />
        </div>
      </section>

      {resultText && (
        <pre aria-label="op-result" className="settings-group overflow-auto p-3 text-xs">
          {resultText}
        </pre>
      )}
      {errorText && (
        <p role="alert" aria-label="op-error" className="callout danger">
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
                title={busy ? "The operation is already running." : undefined}
                className="rounded border px-3 py-1 text-sm disabled:opacity-50"
                onClick={() => void settle(pending)}
              >
                Confirm
              </button>
              <button
                type="button"
                aria-label="cancel-staged-op"
                className="rounded border px-3 py-1 text-sm"
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
