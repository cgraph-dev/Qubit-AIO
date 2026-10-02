/**
 * WorktreesPage — route `/worktrees`.
 *
 * Separate Qubit page for confined git worktree lifecycle: list, branches,
 * create (confirm), remove (confirm), plus the two honest counterparts.
 * Restore and prune are SAFE_EQUIVALENT local operations and are labeled
 * with their typed codes — they are never presented as upstream restores.
 */

import { useCallback, useEffect, useState } from "react";
import {
  WORKTREES_PRUNE_CODE,
  WORKTREES_RESTORE_CODE,
  createWorktree,
  listBranches,
  listWorktrees,
  pruneWorktrees,
  removeWorktree,
  restoreWorktree,
  type WorktreeRow,
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

export default function WorktreesPage() {
  const [worktrees, setWorktrees] = useState<WorktreeRow[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [newPath, setNewPath] = useState("");
  const [newBranch, setNewBranch] = useState("");
  const [pending, setPending] = useState<StagedOp | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultText, setResultText] = useState("");
  const [errorText, setErrorText] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [wt, br] = await Promise.all([listWorktrees(), listBranches()]);
      setWorktrees(wt.worktrees);
      setBranches(br.branches);
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

  return (
    <div className="flex flex-col gap-6 p-6" data-testid="worktrees-page">
      <header>
        <h1 className="text-xl font-semibold">Worktrees</h1>
        <p className="text-sm text-muted-foreground">
          Confined git worktree lifecycle. Create and remove ask first;
          restore and prune are honest local counterparts, labeled as such.
        </p>
      </header>

      <section aria-label="worktree-list" className="rounded border p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Worktrees</h2>
          <button
            type="button"
            aria-label="worktrees-refresh"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
        <ul className="mt-2 flex flex-col gap-2">
          {worktrees.map((w) => (
            <li
              key={w.path}
              className="flex flex-wrap items-center gap-2 rounded border p-2 text-sm"
            >
              <code>{w.path}</code>
              {w.branch && <span>branch: {w.branch}</span>}
              {w.commit && <span>commit: {w.commit}</span>}
              {w.is_main && <span>(main)</span>}
              <button
                type="button"
                aria-label={`worktree-remove-${w.path}`}
                disabled={busy || w.is_main}
                className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
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
                className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
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
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="worktree-branches" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Branches</h2>
        <ul className="mt-2 flex flex-wrap gap-2 text-sm">
          {branches.map((b) => (
            <li key={b}>
              <code className="rounded border px-2 py-0.5">{b}</code>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="worktree-create" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Create worktree</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="worktree-new-path"
            className="rounded border px-2 py-1 text-sm"
            placeholder="path"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
          />
          <input
            aria-label="worktree-new-branch"
            className="rounded border px-2 py-1 text-sm"
            placeholder="branch"
            value={newBranch}
            onChange={(e) => setNewBranch(e.target.value)}
          />
          <button
            type="button"
            aria-label="worktree-create-submit"
            disabled={busy || !newPath.trim() || !newBranch.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
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
        </div>
      </section>

      <section aria-label="worktree-honest-counterparts" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Restore and prune (honest counterparts)</h2>
        <p className="mt-1 text-sm">
          Restore re-materializes a worktree with a confined local add (
          <code>{WORKTREES_RESTORE_CODE}</code>, SAFE_EQUIVALENT) — it is
          never presented as an upstream snapshot restore.
        </p>
        <p className="mt-1 text-sm">
          Prune runs a local worktree prune (
          <code>{WORKTREES_PRUNE_CODE}</code>, SAFE_EQUIVALENT) — it is
          never presented as a managed-cache sweep.
        </p>
        <div className="mt-2">
          <button
            type="button"
            aria-label="worktree-prune-submit"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
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
        </div>
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
