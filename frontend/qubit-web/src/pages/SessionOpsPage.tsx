/**
 * SessionOpsPage — route `/session-ops`.
 *
 * Separate Qubit page for session operations (lineage, compaction, goals,
 * suggestions, groups, companion). The Sessions page stays a read/manage
 * list; everything that rewrites a transcript lives here, every destructive
 * press staged behind an explicit confirm step before it settles.
 *
 * Honesty contract (rendered on the page, not just in code):
 * - Fork = "copy into NEW session" (the source session is untouched).
 * - Branch-switch = "restore in place" (rewrites the current session).
 * - Journal checkpoints are verify-only: this page never restores a journal
 *   checkpoint blindly — compaction restore names its checkpoint id and the
 *   Checkpoints page offers verify, never restore.
 */

import { useCallback, useEffect, useState } from "react";
import {
  addSuggestion,
  branchSwitchSession,
  cleanupEmptySessions,
  clearSessionGoals,
  compactionBranch,
  compactionRestore,
  companionAsk,
  companionReset,
  companionState,
  compactSession,
  createSessionGroup,
  deleteSessionGroup,
  forkSession,
  listPickerSessions,
  listSessionGroups,
  listSuggestions,
  renameSessionGroup,
  resetSession,
  resolveSuggestion,
  rewindSession,
  setSessionGoals,
  type CompanionStateResponse,
  type SessionGroupRow,
  type SuggestionRow,
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

export default function SessionOpsPage() {
  const [sessions, setSessions] = useState<{ id: string; title?: string }[]>([]);
  const [pickerInput, setPickerInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [pending, setPending] = useState<StagedOp | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultText, setResultText] = useState("");
  const [errorText, setErrorText] = useState("");

  const [messageId, setMessageId] = useState("");
  const [branchTarget, setBranchTarget] = useState("");
  const [checkpointId, setCheckpointId] = useState("");
  const [goals, setGoals] = useState("");
  const [suggestions, setSuggestions] = useState<SuggestionRow[]>([]);
  const [suggestionText, setSuggestionText] = useState("");
  const [groups, setGroups] = useState<SessionGroupRow[]>([]);
  const [groupName, setGroupName] = useState("");
  const [companionQuestion, setCompanionQuestion] = useState("");
  const [companionInfo, setCompanionInfo] = useState<CompanionStateResponse | null>(null);

  const refreshPicker = useCallback(async () => {
    try {
      const res = await listPickerSessions();
      setSessions(res.sessions.map((s) => ({ id: s.id, title: s.title })));
    } catch {
      setSessions([]);
    }
  }, []);

  const refreshGroups = useCallback(async () => {
    try {
      const res = await listSessionGroups();
      setGroups(res.groups);
    } catch {
      setGroups([]);
    }
  }, []);

  useEffect(() => {
    void refreshPicker();
    void refreshGroups();
  }, [refreshPicker, refreshGroups]);

  const settle = useCallback(async (op: StagedOp) => {
    setBusy(true);
    setErrorText("");
    setResultText("");
    try {
      // The staged dialog already captured the human confirm, so the
      // button press settles without a second prompt.
      const out = await op.run();
      setResultText(formatResult(out));
    } catch (e) {
      setErrorText(refusalOrError(e, `Operation failed: ${e}`));
    } finally {
      setBusy(false);
      setPending(null);
    }
  }, []);

  const stage = useCallback(
    (title: string, detail: string, run: () => Promise<unknown>) => {
      setPending({ title, detail, run });
    },
    [],
  );

  const needSession = !sessionId;

  const runDirect = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      setErrorText("");
      setResultText("");
      try {
        setResultText(formatResult(await fn()));
      } catch (e) {
        setErrorText(refusalOrError(e, `Operation failed: ${e}`));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <div className="flex flex-col gap-6 p-6" data-testid="session-ops-page">
      <header>
        <h1 className="text-xl font-semibold">Session operations</h1>
        <p className="text-sm text-muted-foreground">
          Lineage, compaction, goals, suggestions, groups, and companion for
          one selected session. Every destructive press asks first.
        </p>
      </header>

      <section aria-label="fork-versus-branch-guidance" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Fork vs branch-switch vs checkpoints</h2>
        <ul className="list-disc pl-5 text-sm">
          <li>
            <strong>Fork</strong> copies the session into a NEW session — the
            source session is untouched.
          </li>
          <li>
            <strong>Branch-switch</strong> restores in place — it rewrites the
            current session transcript.
          </li>
          <li>
            <strong>Journal checkpoints are verify-only.</strong> Compaction
            restore below names its checkpoint id explicitly; the Checkpoints
            page offers verify, never a blind journal restore.
          </li>
        </ul>
      </section>

      <section aria-label="session-picker" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Session picker</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="session-picker-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="session id"
            value={pickerInput}
            onChange={(e) => setPickerInput(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-picker-use"
            className="rounded border px-3 py-1 text-sm"
            onClick={() => setSessionId(pickerInput.trim())}
          >
            Use session
          </button>
          <button
            type="button"
            aria-label="session-picker-refresh"
            className="rounded border px-3 py-1 text-sm"
            onClick={() => void refreshPicker()}
          >
            Refresh list
          </button>
        </div>
        <p className="mt-2 text-sm">
          Selected: <code aria-label="session-picker-selected">{sessionId || "(none)"}</code>
        </p>
        <ul className="mt-2 flex max-h-40 flex-col gap-1 overflow-auto">
          {sessions.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                aria-label={`pick-session-${s.id}`}
                className="rounded border px-2 py-1 text-left text-sm"
                onClick={() => {
                  setSessionId(s.id);
                  setPickerInput(s.id);
                }}
              >
                {s.id}
                {s.title ? ` — ${s.title}` : ""}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="ops-panel" className="rounded border p-4">
        <h2 className="text-sm font-semibold">Ops panel</h2>

        <h3 className="mt-3 text-sm font-semibold">Transcript</h3>
        <div className="mt-1 flex flex-wrap gap-2">
          <button
            type="button"
            aria-label="session-op-reset"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Reset session",
                `Reset session ${sessionId}? The transcript is cleared.`,
                () => resetSession(sessionId, () => true),
              )
            }
          >
            Reset
          </button>
          <button
            type="button"
            aria-label="session-op-compact"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Compact session",
                `Compact session ${sessionId}? Older turns are summarized.`,
                () => compactSession(sessionId, () => true),
              )
            }
          >
            Compact
          </button>
          <button
            type="button"
            aria-label="session-op-cleanup-empty"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Cleanup empty sessions",
                "Delete sessions with no messages?",
                () => cleanupEmptySessions(() => true),
              )
            }
          >
            Cleanup-empty
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            aria-label="rewind-message-id"
            className="rounded border px-2 py-1 text-sm"
            placeholder="message id"
            value={messageId}
            onChange={(e) => setMessageId(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-rewind"
            disabled={needSession || busy || !messageId.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Rewind to message",
                `Rewind session ${sessionId} to message ${messageId.trim()}? Later messages are dropped.`,
                () => rewindSession(sessionId, messageId.trim(), () => true),
              )
            }
          >
            Rewind-to-message
          </button>
        </div>

        <h3 className="mt-4 text-sm font-semibold">Lineage</h3>
        <div className="mt-1 flex flex-wrap gap-2">
          <button
            type="button"
            aria-label="session-op-fork"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Fork session",
                `Copy session ${sessionId} into NEW session? The source is untouched.`,
                () => forkSession(sessionId, () => true),
              )
            }
          >
            Fork (copy into NEW session)
          </button>
          <input
            aria-label="branch-switch-target"
            className="rounded border px-2 py-1 text-sm"
            placeholder="branch target"
            value={branchTarget}
            onChange={(e) => setBranchTarget(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-branch-switch"
            disabled={needSession || busy || !branchTarget.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Branch-switch",
                `Restore session ${sessionId} in place onto ${branchTarget.trim()}? The current transcript is rewritten.`,
                () => branchSwitchSession(sessionId, branchTarget.trim(), () => true),
              )
            }
          >
            Branch-switch (restore in place)
          </button>
        </div>

        <h3 className="mt-4 text-sm font-semibold">Compaction branch / restore</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-label="session-op-compaction-branch"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Compaction branch",
                `Branch a compaction of session ${sessionId}?`,
                () => compactionBranch(sessionId, () => true),
              )
            }
          >
            Compaction branch
          </button>
          <input
            aria-label="compaction-restore-checkpoint"
            className="rounded border px-2 py-1 text-sm"
            placeholder="checkpoint id"
            value={checkpointId}
            onChange={(e) => setCheckpointId(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-compaction-restore"
            disabled={needSession || busy || !checkpointId.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Compaction restore",
                `Restore session ${sessionId} from compaction checkpoint ${checkpointId.trim()}? The current transcript is rewritten.`,
                () => compactionRestore(sessionId, checkpointId.trim(), () => true),
              )
            }
          >
            Compaction restore
          </button>
          <a className="text-sm underline" href="/checkpoints">
            Verify checkpoints
          </a>
        </div>

        <h3 className="mt-4 text-sm font-semibold">Goals</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            aria-label="session-goals-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="goal text"
            value={goals}
            onChange={(e) => setGoals(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-goals-set"
            disabled={needSession || busy || !goals.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Set goals",
                `Set goals on session ${sessionId}?`,
                () => setSessionGoals(sessionId, goals.trim(), () => true),
              )
            }
          >
            Goals set
          </button>
          <button
            type="button"
            aria-label="session-op-goals-clear"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Clear goals",
                `Clear goals on session ${sessionId}?`,
                () => clearSessionGoals(sessionId, () => true),
              )
            }
          >
            Goals clear
          </button>
        </div>

        <h3 className="mt-4 text-sm font-semibold">Suggestions</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-label="session-op-suggestions-list"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              runDirect(async () => {
                const res = await listSuggestions(sessionId);
                setSuggestions(res.suggestions);
                return res;
              })
            }
          >
            List suggestions
          </button>
          <input
            aria-label="suggestion-text-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="suggestion text"
            value={suggestionText}
            onChange={(e) => setSuggestionText(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-suggestions-add"
            disabled={needSession || busy || !suggestionText.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Add suggestion",
                `Add suggestion to session ${sessionId}?`,
                () => addSuggestion(sessionId, suggestionText.trim(), () => true),
              )
            }
          >
            Add suggestion
          </button>
        </div>
        <ul className="mt-2 flex flex-col gap-1">
          {suggestions.map((s) => (
            <li key={s.id} className="flex items-center gap-2 text-sm">
              <span>
                {s.text} {s.resolved ? "(resolved)" : ""}
              </span>
              {!s.resolved && (
                <button
                  type="button"
                  aria-label={`suggestion-resolve-${s.id}`}
                  disabled={needSession || busy}
                  className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                  onClick={() =>
                    stage(
                      "Resolve suggestion",
                      `Mark suggestion ${s.id} resolved?`,
                      () => resolveSuggestion(sessionId, s.id, () => true),
                    )
                  }
                >
                  Resolve
                </button>
              )}
            </li>
          ))}
        </ul>

        <h3 className="mt-4 text-sm font-semibold">Groups</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            aria-label="group-name-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="group name"
            value={groupName}
            onChange={(e) => setGroupName(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-groups-create"
            disabled={busy || !groupName.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage("Create group", `Create group ${groupName.trim()}?`, () =>
                createSessionGroup(groupName.trim(), () => true),
              )
            }
          >
            Create group
          </button>
          <button
            type="button"
            aria-label="session-op-groups-refresh"
            disabled={busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void refreshGroups()}
          >
            Refresh groups
          </button>
        </div>
        <ul className="mt-2 flex flex-col gap-1">
          {groups.map((g) => (
            <li key={g.id} className="flex items-center gap-2 text-sm">
              <span>{g.name}</span>
              <button
                type="button"
                aria-label={`group-rename-${g.id}`}
                disabled={busy || !groupName.trim()}
                className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                onClick={() =>
                  stage("Rename group", `Rename group ${g.name}?`, () =>
                    renameSessionGroup(g.id, groupName.trim(), () => true),
                  )
                }
              >
                Rename
              </button>
              <button
                type="button"
                aria-label={`group-delete-${g.id}`}
                disabled={busy}
                className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                onClick={() =>
                  stage("Delete group", `Delete group ${g.name}?`, () =>
                    deleteSessionGroup(g.id, () => true),
                  )
                }
              >
                Delete
              </button>
            </li>
          ))}
        </ul>

        <h3 className="mt-4 text-sm font-semibold">Companion</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            aria-label="companion-question-input"
            className="rounded border px-2 py-1 text-sm"
            placeholder="side question"
            value={companionQuestion}
            onChange={(e) => setCompanionQuestion(e.target.value)}
          />
          <button
            type="button"
            aria-label="session-op-companion-ask"
            disabled={needSession || busy || !companionQuestion.trim()}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => void runDirect(() => companionAsk(sessionId, companionQuestion.trim()))}
          >
            Companion ask
          </button>
          <button
            type="button"
            aria-label="session-op-companion-state"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              runDirect(async () => {
                const st = await companionState(sessionId);
                setCompanionInfo(st);
                return st;
              })
            }
          >
            Companion state
          </button>
          <button
            type="button"
            aria-label="session-op-companion-reset"
            disabled={needSession || busy}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() =>
              stage(
                "Companion reset",
                `Reset the companion of session ${sessionId}? The parent session is untouched.`,
                () => companionReset(sessionId, () => true),
              )
            }
          >
            Companion reset
          </button>
        </div>
        {companionInfo && (
          <p className="mt-1 text-sm" aria-label="companion-state-output">
            Companion {companionInfo.active ? "active" : "inactive"}
            {typeof companionInfo.messageCount === "number"
              ? ` · ${companionInfo.messageCount} messages`
              : ""}
          </p>
        )}
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
