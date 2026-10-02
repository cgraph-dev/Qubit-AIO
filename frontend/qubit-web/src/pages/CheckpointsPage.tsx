/**
 * CheckpointsPage — route `/checkpoints`.
 *
 * Separate Qubit page for the checkpoint journal: list, verify, create, and
 * prune (confirm, SystemPage pattern). Journal checkpoints are verify-only
 * by design — this page states that and renders no restore button for them.
 * Compaction branch/restore live on the session-ops page, linked below.
 */

import { useCallback, useEffect, useState } from "react";
import {
  createCheckpoint,
  listCheckpointJournal,
  pruneCheckpointJournal,
  verifyCheckpoint,
  type CheckpointSessionRow,
  type CheckpointVerifyResponse,
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

export default function CheckpointsPage() {
  const [journal, setJournal] = useState<CheckpointSessionRow[]>([]);
  const [totalBytes, setTotalBytes] = useState(0);
  const [newSessionId, setNewSessionId] = useState("");
  const [verifyId, setVerifyId] = useState("");
  const [verifyOut, setVerifyOut] = useState<CheckpointVerifyResponse | null>(null);
  const [pending, setPending] = useState<StagedOp | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultText, setResultText] = useState("");
  const [errorText, setErrorText] = useState("");

  const refresh = useCallback(async () => {
    try {
      const res = await listCheckpointJournal();
      setJournal(res.sessions);
      setTotalBytes(res.total_bytes);
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

  const runVerify = useCallback(async () => {
    if (!verifyId.trim()) return;
    setBusy(true);
    setErrorText("");
    try {
      const out = await verifyCheckpoint(verifyId.trim());
      setVerifyOut(out);
      setResultText(formatResult(out));
    } catch (e) {
      setErrorText(refusalOrError(e, `Verify failed: ${e}`));
    } finally {
      setBusy(false);
    }
  }, [verifyId]);

  return (
    <div className="flex flex-col gap-6 p-6" data-testid="checkpoints-page">
      <header>
        <h1 className="text-xl font-semibold">Checkpoints</h1>
        <p className="text-sm text-muted-foreground">
          Journal list, verify, create, and prune. Prune asks first.
        </p>
      </header>

      <section aria-label="verify-only-notice" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Verify-only by design</h2>
        <p className="mt-1 text-sm">
          Journal checkpoints are verify-only: this page lists and verifies
          them but offers no restore button for journal checkpoints.
          Compaction branch and restore live on the{" "}
          <a className="underline" href="/session-ops">
            session-ops page
          </a>
          , where every restore names its checkpoint id explicitly.
        </p>
      </section>

      <section aria-label="checkpoint-journal" className="rounded border p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">
            Journal ({journal.length} sessions · {totalBytes} bytes)
          </h2>
          <button
            type="button"
            aria-label="checkpoints-refresh"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
        <ul className="mt-2 flex flex-col gap-1 text-sm">
          {journal.map((row) => (
            <li key={row.session} className="flex items-center gap-2">
              <code>{row.session}</code>
              <span>
                {row.files} files · {row.bytes} bytes
              </span>
              <button
                type="button"
                aria-label={`checkpoint-verify-${row.session}`}
                disabled={busy}
                className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                onClick={() => {
                  setVerifyId(row.session);
                }}
              >
                Verify
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="checkpoint-verify" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Verify checkpoint</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="checkpoint-verify-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="checkpoint id"
            value={verifyId}
            onChange={(e) => setVerifyId(e.target.value)}
          />
          <button
            type="button"
            aria-label="checkpoint-verify-submit"
            disabled={busy || !verifyId.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void runVerify()}
          >
            Verify
          </button>
        </div>
        {verifyOut && (
          <p className="mt-1 text-sm" aria-label="checkpoint-verify-output">
            Verify {verifyOut.ok ? "passed" : "did not pass"}
            {verifyOut.message_count != null ? ` · ${verifyOut.message_count} messages` : ""}
          </p>
        )}
      </section>

      <section aria-label="checkpoint-create" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Create checkpoint</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="checkpoint-create-session"
            className="rounded border px-2 py-1 text-sm"
            placeholder="session id"
            value={newSessionId}
            onChange={(e) => setNewSessionId(e.target.value)}
          />
          <button
            type="button"
            aria-label="checkpoint-create-submit"
            disabled={busy || !newSessionId.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              setPending({
                title: "Create checkpoint",
                detail: `Record a checkpoint for session ${newSessionId.trim()}?`,
                run: () => createCheckpoint(newSessionId.trim(), () => true),
              })
            }
          >
            Create
          </button>
        </div>
      </section>

      <section aria-label="checkpoint-prune" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Prune journal</h2>
        <p className="mt-1 text-sm">
          Delete the rollback checkpoint shadow store? Existing rollback
          points will be lost.
        </p>
        <button
          type="button"
          aria-label="checkpoint-prune-submit"
          disabled={busy || journal.length === 0}
          className="mt-2 rounded border px-3 py-1 text-sm disabled:opacity-50"
          onClick={() =>
            setPending({
              title: "Prune checkpoints",
              detail: "Delete the rollback checkpoint shadow store?",
              run: () => pruneCheckpointJournal(() => true),
            })
          }
        >
          Prune checkpoints
        </button>
      </section>

      <section aria-label="compaction-links" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Compaction branch / restore</h2>
        <p className="mt-1 text-sm">
          Compaction branch and restore run on the{" "}
          <a className="underline" href="/session-ops">
            session-ops page
          </a>{" "}
          against an explicitly named checkpoint.
        </p>
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
