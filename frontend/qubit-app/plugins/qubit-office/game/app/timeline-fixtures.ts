/* Shared P4-07 timeline fixtures for unit and component tests. */
import {
  TIMELINE_SCHEMA,
  type TimelineItem,
  type TimelineProjection,
} from "./timeline";

export function item(overrides: Partial<TimelineItem> = {}): TimelineItem {
  return {
    item_id: "run:job-1",
    kind: "run",
    at: "2026-11-14T00:00:00Z",
    status: "executing",
    claimed_status: "executing",
    title: "Run job-1",
    summary: "report · executing",
    actor: "worker-1",
    source: "bos_job",
    run_id: "job-1",
    task_id: "task-1",
    agent_id: "agent-1",
    session_id: "ses-1",
    approval_id: null,
    artifact_id: null,
    ledger_id: null,
    checkpoint_id: null,
    correlation_id: null,
    causation_id: null,
    sequence: 0,
    evidence: false,
    evidence_refs: [],
    completed: false,
    unknown: false,
    pending: false,
    reason: null,
    cost: null,
    tokens: null,
    replay: false,
    effect: null,
    simulated: false,
    ...overrides,
  };
}

export function projection(
  overrides: Partial<TimelineProjection> = {},
): TimelineProjection {
  return {
    schema: TIMELINE_SCHEMA,
    tenant_id: "ten-1",
    generated_at: "2026-11-14T00:00:00Z",
    mode: "live",
    state: "ok",
    query: {
      run: null, task: null, agent: null, session: null, limit: 100,
      replay: false, filtered: false,
    },
    counts: {
      total: 1, runs: 1, events: 0, tasks: 0, tools: 0, approvals: 0,
      artifacts: 0, receipts: 0, checkpoints: 0, reconciliations: 0,
      completed: 0, completed_with_evidence: 0, pending: 0, unknown: 0,
      replay: 0, matched: 1, returned: 1, truncated: false,
    },
    cost: {
      debit_minor: 0, credit_minor: 0, net_minor: 0, entries: 0,
      by_currency: {}, source: "bos_ledger_entry",
    },
    tokens: { prompt: 0, completion: 0, total: 0, runs: 0 },
    items: [item()],
    sources: {
      events: { state: "empty", count: 0, code: null, reason: null },
      jobs: { state: "ok", count: 1, code: null, reason: null },
      tasks: { state: "empty", count: 0, code: null, reason: null },
      approvals: { state: "empty", count: 0, code: null, reason: null },
      artifacts: { state: "empty", count: 0, code: null, reason: null },
      ledger: { state: "empty", count: 0, code: null, reason: null },
      tools: { state: "empty", count: 0, code: null, reason: null },
      checkpoints: { state: "empty", count: 0, code: null, reason: null },
    },
    replay: {
      enabled: false, effects: "none", real_calls: false,
      label: "Live projection of durable records", note: "",
    },
    unavailable_sources: [],
    ...overrides,
  };
}
