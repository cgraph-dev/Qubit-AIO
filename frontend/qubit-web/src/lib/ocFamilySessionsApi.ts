/**
 * Qubit family API for the session-ops, worktrees, and checkpoints pages.
 *
 * Every entry here is a real backend call through the single `fetchJSON`
 * seam. Reads use `fetchJSON` directly; every mutation settles the staged
 * confirmation round-trip through `runMutationWithConfirm`, so a destructive
 * press first stages (the page shows its own confirm step) and only then
 * settles on an explicit human confirm.
 *
 * ROUTES manifest — every path + method used by this module:
 *
 *   Session picker + ops (`/api/qubit/session-ops/<action>`):
 *   - GET  /api/sessions                            (picker list, reused)
 *   - POST /api/qubit/session-ops/reset             (destructive, confirm)
 *   - POST /api/qubit/session-ops/rewind            (destructive, confirm)
 *   - POST /api/qubit/session-ops/compact           (destructive, confirm)
 *   - POST /api/qubit/session-ops/fork              (destructive, confirm)
 *   - POST /api/qubit/session-ops/branch-switch    (destructive, confirm)
 *   - POST /api/qubit/session-ops/compaction-branch (destructive, confirm)
 *   - POST /api/qubit/session-ops/compaction-restore (destructive, confirm)
 *   - POST /api/qubit/session-ops/cleanup-empty     (destructive, confirm)
 *   - POST /api/qubit/session-ops/goals-set         (destructive, confirm)
 *   - POST /api/qubit/session-ops/goals-clear       (destructive, confirm)
 *   - GET  /api/qubit/session-ops/suggestions       (read)
 *   - POST /api/qubit/session-ops/suggestions-add   (destructive, confirm)
 *   - POST /api/qubit/session-ops/suggestions-resolve (destructive, confirm)
 *   - GET  /api/qubit/session-ops/groups            (read)
 *   - POST /api/qubit/session-ops/groups-create     (destructive, confirm)
 *   - POST /api/qubit/session-ops/groups-rename     (destructive, confirm)
 *   - POST /api/qubit/session-ops/groups-delete     (destructive, confirm)
 *   - POST /api/qubit/session-ops/companion-ask     (read-only turn, direct)
 *   - GET  /api/qubit/session-ops/companion-state   (read)
 *   - POST /api/qubit/session-ops/companion-reset   (destructive, confirm)
 *
 *   Worktrees (existing `/api/git/worktrees*` reused, rest Qubit-owned):
 *   - GET  /api/git/worktrees                       (read, existing, reused)
 *   - GET  /api/git/branches                        (read, existing, reused)
 *   - POST /api/qubit/worktrees/create              (destructive, confirm)
 *   - POST /api/qubit/worktrees/remove              (destructive, confirm)
 *   - POST /api/qubit/worktrees/restore             (confirm; SAFE_EQUIVALENT
 *         WORKTREES_RESTORE_LOCAL_READD — confined local re-add, never an
 *         upstream snapshot restore)
 *   - POST /api/qubit/worktrees/prune               (confirm; SAFE_EQUIVALENT
 *         WORKTREES_GC_LOCAL_PRUNE — local prune, never a managed-cache sweep)
 *
 *   Checkpoints (existing `/api/ops/checkpoints*` reused, rest Qubit-owned):
 *   - GET  /api/ops/checkpoints                     (read, existing, reused)
 *   - POST /api/qubit/checkpoints/create            (destructive, confirm)
 *   - GET  /api/qubit/checkpoints/verify            (read, verify-only)
 *   - POST /api/ops/checkpoints/prune               (existing, reused;
 *         destructive, confirm)
 *
 * Journal checkpoints are verify-only by design: this module exposes no
 * restore call for them, and the Checkpoints page must not render one.
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

/** Human-confirm callback threaded into `runMutationWithConfirm`. */
export type FamilyConfirm = (
  detail: string,
) => boolean | Promise<boolean>;

/** Loose backend result: always honest, rendered verbatim by the pages. */
export interface FamilyResult {
  ok: boolean;
  [key: string]: unknown;
}

export interface SessionPickerRow {
  id: string;
  title?: string;
  message_count?: number;
  updated_at?: string;
}

export interface SessionPickerResponse {
  sessions: SessionPickerRow[];
  total: number;
}

export interface SuggestionRow {
  id: string;
  text: string;
  resolved?: boolean;
}

export interface SuggestionListResponse {
  suggestions: SuggestionRow[];
}

export interface SessionGroupRow {
  id: string;
  name: string;
  session_ids?: string[];
}

export interface SessionGroupListResponse {
  groups: SessionGroupRow[];
}

export interface CompanionStateResponse {
  active: boolean;
  companionSessionId?: string;
  messageCount?: number;
}

export interface WorktreeRow {
  path: string;
  branch?: string;
  commit?: string;
  is_main?: boolean;
}

export interface WorktreeListResponse {
  worktrees: WorktreeRow[];
}

/** A branch as the backend reports it — an object, not a bare name. */
export interface BranchEntry {
  name: string;
  checkedOut?: boolean;
  isDefault?: boolean;
  isRemote?: boolean;
  /** Set when the branch is checked out in a worktree. */
  worktreePath?: string;
}

export interface BranchListResponse {
  branches: BranchEntry[];
}

export interface CheckpointSessionRow {
  session: string;
  files: number;
  bytes: number;
}

export interface CheckpointJournalResponse {
  sessions: CheckpointSessionRow[];
  total_bytes: number;
}

export interface CheckpointVerifyResponse {
  ok: boolean;
  checkpoint?: string;
  session?: string;
  message_count?: number;
  detail?: string;
}

function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return fetchJSON<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function postConfirm<T>(
  path: string,
  body: Record<string, unknown>,
  confirm?: FamilyConfirm,
): Promise<T> {
  return runMutationWithConfirm(() => post<T>(path, body), confirm);
}

// ── Session picker ─────────────────────────────────────────────────

export function listPickerSessions(limit = 50): Promise<SessionPickerResponse> {
  return fetchJSON<SessionPickerResponse>(
    `/api/sessions?limit=${limit}&offset=0`,
  );
}

// ── Session ops ────────────────────────────────────────────────────

export function resetSession(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/reset",
    { session_id: sessionId },
    confirm,
  );
}

export function rewindSession(
  sessionId: string,
  messageId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/rewind",
    { session_id: sessionId, message_id: messageId },
    confirm,
  );
}

export function compactSession(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/compact",
    { session_id: sessionId },
    confirm,
  );
}

export function forkSession(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/fork",
    { session_id: sessionId },
    confirm,
  );
}

export function branchSwitchSession(
  sessionId: string,
  target: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/branch-switch",
    { session_id: sessionId, target },
    confirm,
  );
}

export function compactionBranch(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/compaction-branch",
    { session_id: sessionId },
    confirm,
  );
}

export function compactionRestore(
  sessionId: string,
  checkpointId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/compaction-restore",
    { session_id: sessionId, checkpoint_id: checkpointId },
    confirm,
  );
}

export function cleanupEmptySessions(
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/cleanup-empty",
    {},
    confirm,
  );
}

export function setSessionGoals(
  sessionId: string,
  goals: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/goals-set",
    { session_id: sessionId, goals },
    confirm,
  );
}

export function clearSessionGoals(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/goals-clear",
    { session_id: sessionId },
    confirm,
  );
}

export function listSuggestions(
  sessionId: string,
): Promise<SuggestionListResponse> {
  return fetchJSON<SuggestionListResponse>(
    `/api/qubit/session-ops/suggestions?session_id=${encodeURIComponent(sessionId)}`,
  );
}

export function addSuggestion(
  sessionId: string,
  text: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/suggestions-add",
    { session_id: sessionId, text },
    confirm,
  );
}

export function resolveSuggestion(
  sessionId: string,
  suggestionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/suggestions-resolve",
    { session_id: sessionId, suggestion_id: suggestionId },
    confirm,
  );
}

export function listSessionGroups(): Promise<SessionGroupListResponse> {
  return fetchJSON<SessionGroupListResponse>(
    "/api/qubit/session-ops/groups",
  );
}

export function createSessionGroup(
  name: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/groups-create",
    { name },
    confirm,
  );
}

export function renameSessionGroup(
  groupId: string,
  name: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/groups-rename",
    { group_id: groupId, name },
    confirm,
  );
}

export function deleteSessionGroup(
  groupId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/groups-delete",
    { group_id: groupId },
    confirm,
  );
}

export function companionAsk(
  sessionId: string,
  question: string,
): Promise<FamilyResult> {
  return post<FamilyResult>("/api/qubit/session-ops/companion-ask", {
    session_id: sessionId,
    question,
  });
}

export function companionState(
  sessionId: string,
): Promise<CompanionStateResponse> {
  return fetchJSON<CompanionStateResponse>(
    `/api/qubit/session-ops/companion-state?session_id=${encodeURIComponent(sessionId)}`,
  );
}

export function companionReset(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/session-ops/companion-reset",
    { session_id: sessionId },
    confirm,
  );
}

// ── Worktrees ──────────────────────────────────────────────────────

export function listWorktrees(): Promise<WorktreeListResponse> {
  return fetchJSON<WorktreeListResponse>("/api/git/worktrees");
}

export function listBranches(): Promise<BranchListResponse> {
  return fetchJSON<BranchListResponse>("/api/git/branches");
}

export function createWorktree(
  path: string,
  branch: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/worktrees/create",
    { path, branch },
    confirm,
  );
}

export function removeWorktree(
  path: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/worktrees/remove",
    { path },
    confirm,
  );
}

/** Typed honesty code for the restore counterpart (SAFE_EQUIVALENT). */
export const WORKTREES_RESTORE_CODE = "WORKTREES_RESTORE_LOCAL_READD";

export function restoreWorktree(
  path: string,
  branch: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/worktrees/restore",
    { path, branch },
    confirm,
  );
}

/** Typed honesty code for the prune counterpart (SAFE_EQUIVALENT). */
export const WORKTREES_PRUNE_CODE = "WORKTREES_GC_LOCAL_PRUNE";

export function pruneWorktrees(
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/worktrees/prune",
    {},
    confirm,
  );
}

// ── Checkpoints ────────────────────────────────────────────────────

export function listCheckpointJournal(): Promise<CheckpointJournalResponse> {
  return fetchJSON<CheckpointJournalResponse>("/api/ops/checkpoints");
}

export function createCheckpoint(
  sessionId: string,
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/qubit/checkpoints/create",
    { session: sessionId },
    confirm,
  );
}

export function verifyCheckpoint(
  checkpointId: string,
): Promise<CheckpointVerifyResponse> {
  return fetchJSON<CheckpointVerifyResponse>(
    `/api/qubit/checkpoints/verify?id=${encodeURIComponent(checkpointId)}`,
  );
}

export function pruneCheckpointJournal(
  confirm?: FamilyConfirm,
): Promise<FamilyResult> {
  return postConfirm<FamilyResult>(
    "/api/ops/checkpoints/prune",
    {},
    confirm,
  );
}
