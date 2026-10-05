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
      setJournal(res?.sessions ?? []);
      setTotalBytes(res?.total_bytes ?? 0);
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
    <div
      className="settings-page settings-page--wide"
      data-testid="checkpoints-page"
    >
      <section className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h1 className="settings-section__heading">Checkpoints</h1>
            <p className="settings-section__desc">
              Journal list, verify, create, and prune. Prune asks first.
            </p>
          </div>
          <div className="settings-section__actions">
            <button
              type="button"
              aria-label="checkpoints-refresh"
              disabled={busy}
              title={busy ? "Refreshing the checkpoint journal." : undefined}
              className="btn btn--sm"
              onClick={() => void refresh()}
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      <section aria-label="verify-only-notice" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Verify-only by design</h2>
        </div>
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__desc">
                Journal checkpoints are verify-only: this page lists and verifies
                them but offers no restore button for journal checkpoints.
                Compaction branch and restore live on the{" "}
                <a className="settings-row__value" href="/session-ops">
                  session-ops page
                </a>
                , where every restore names its checkpoint id explicitly.
              </span>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="checkpoint-journal" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Journal{" "}
            <span className="settings-count">
              {journal.length} sessions · {totalBytes} bytes
            </span>
          </h2>
        </div>
        <div className="settings-group">
          {journal.length === 0 ? (
            <div className="settings-empty" data-testid="checkpoints-empty">
              No journal checkpoints yet.
            </div>
          ) : (
            <div className="settings-subrows" role="group" aria-label="Journal checkpoints">
              {journal.map((row) => (
                <div className="settings-row" key={row.session}>
                  <div className="settings-row__text">
                    <span className="settings-row__title font-mono">
                      {row.session}
                    </span>
                    <span className="settings-row__desc">
                      {row.files} files · {row.bytes} bytes · Journal
                      checkpoint · verify-only (no restore).
                    </span>
                  </div>
                  <div className="settings-row__control">
                    <button
                      type="button"
                      aria-label={`checkpoint-verify-${row.session}`}
                      disabled={busy}
                      title={busy ? "A checkpoint operation is running." : undefined}
                      className="btn btn--sm"
                      onClick={() => {
                        setVerifyId(row.session);
                      }}
                    >
                      Verify
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section aria-label="checkpoint-verify" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Verify checkpoint</h2>
        </div>
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__title">Checkpoint id</span>
              <span className="settings-row__desc">
                Verify a journal checkpoint by id; verification never restores.
              </span>
            </div>
            <div className="settings-row__control">
              <input
                aria-label="checkpoint-verify-input"
                className="settings-input"
                placeholder="checkpoint id"
                value={verifyId}
                onChange={(e) => setVerifyId(e.target.value)}
              />
              <button
                type="button"
                aria-label="checkpoint-verify-submit"
                disabled={busy || !verifyId.trim()}
                title={busy ? "A checkpoint operation is running." : undefined}
                className="btn btn--sm"
                onClick={() => void runVerify()}
              >
                Verify
              </button>
            </div>
          </div>
          {verifyOut && (
            <div className="settings-row" aria-label="checkpoint-verify-output">
              <div className="settings-row__text">
                <span className="settings-row__desc">
                  Verify {verifyOut.ok ? "passed" : "did not pass"}
                  {verifyOut.message_count != null
                    ? ` · ${verifyOut.message_count} messages`
                    : ""}
                </span>
              </div>
            </div>
          )}
        </div>
      </section>

      <section aria-label="checkpoint-create" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Create checkpoint</h2>
        </div>
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__title">Session id</span>
              <span className="settings-row__desc">
                Record a checkpoint for the named session. Creating asks first.
              </span>
            </div>
            <div className="settings-row__control">
              <input
                aria-label="checkpoint-create-session"
                className="settings-input"
                placeholder="session id"
                value={newSessionId}
                onChange={(e) => setNewSessionId(e.target.value)}
              />
              <button
                type="button"
                aria-label="checkpoint-create-submit"
                disabled={busy || !newSessionId.trim()}
                title={busy ? "A checkpoint operation is running." : undefined}
                className="btn btn--sm"
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
          </div>
        </div>
      </section>

      <section aria-label="checkpoint-prune" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Prune journal</h2>
          <p className="settings-section__desc">
            Delete the rollback checkpoint shadow store? Existing rollback
            points will be lost.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row settings-row--actions">
            <div className="settings-row__text">
              <span className="settings-row__title">Prune checkpoints</span>
            </div>
            <div className="settings-row__control">
              <button
                type="button"
                aria-label="checkpoint-prune-submit"
                disabled={busy || journal.length === 0}
                title={
                  journal.length === 0
                    ? "Nothing to prune — the journal is empty."
                    : busy
                      ? "A checkpoint operation is running."
                      : undefined
                }
                className="btn btn--sm btn--ghost"
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
            </div>
          </div>
        </div>
      </section>

      <section aria-label="compaction-links" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Compaction branch / restore
          </h2>
        </div>
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__desc">
                Compaction branch and restore run on the{" "}
                <a className="settings-row__value" href="/session-ops">
                  session-ops page
                </a>{" "}
                against an explicitly named checkpoint.
              </span>
            </div>
          </div>
        </div>
      </section>

      {resultText && (
        <pre
          aria-label="op-result"
          className="settings-group overflow-auto p-3 text-xs"
        >
          {resultText}
        </pre>
      )}
      {errorText && (
        <section aria-label="op-error" className="settings-section">
          <div role="alert" className="callout danger">
            {errorText}
          </div>
        </section>
      )}

      {pending && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="staged-op-confirm"
          className="fixed inset-0 flex items-center justify-center bg-black/40"
        >
          <div className="settings-page bg-card p-4" style={{ borderRadius: 23 }}>
            <h2 className="settings-section__heading">{pending.title}</h2>
            <p className="settings-section__desc">{pending.detail}</p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                aria-label="confirm-staged-op"
                disabled={busy}
                title={busy ? "The operation is already running." : undefined}
                className="btn btn--sm"
                onClick={() => void settle(pending)}
              >
                Confirm
              </button>
              <button
                type="button"
                aria-label="cancel-staged-op"
                className="btn btn--sm btn--ghost"
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
