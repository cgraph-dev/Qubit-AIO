/* Office Approvals: the pending records on the SHARED approval journal.
 *
 * WHAT IT SHOWS, AND WHAT IT WILL NOT
 * -----------------------------------
 * It lists exactly the records ``GET /agent/api/ops/approvals`` serves — the
 * same journal CLI `qubit approvals`, voice yes/no and the classic
 * pending-approvals banner settle. For each record it shows the summary, the
 * capability (tool), the action/risk the staging channel recorded, who asked,
 * the age and the exact pending id.
 *
 * Approve and reject call the EXISTING shared-journal routes
 * (``POST /api/ops/confirm`` / ``POST /api/ops/cancel``). There is no new
 * approval authority, no auto-approve, and no optimistic mutation: a stage only
 * happens on an explicit click, and a typed refusal is rendered as the server
 * sent it. After every attempt the list is re-read from the journal, so the
 * panel can never show a record it merely believes it settled.
 *
 * WHY IT LIVES OUTSIDE THE VENDORED IFRAME
 * ----------------------------------------
 * The embedded Habblaud document has no Qubit hook and its api-bridge refuses
 * every write (see api-bridge.ts). Like the Agents/HUD/War Room docks, this
 * panel is part of the Qubit-owned shell AROUND the frame and reads/writes over
 * real HTTP with the host session token.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import { officeFx, sakugaButton, sakugaProgress } from "./sakuga";
import { OfficeTooltip } from "./sakuga-form";
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  ApprovalApiError,
  type ApprovalDecision,
  type ApprovalsState,
  type PendingApproval,
  approvalAction,
  approvalRequester,
  approvalRisk,
  approvalTool,
  decideApproval,
  fetchApprovals,
  formatAge,
} from "./approvals-panel";

export interface ApprovalsPanelProps {
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
  /** Injected for tests; defaults to the page's own fetch. */
  fetchImpl?: typeof fetch;
  /** Report the real pending count to the shell's badge. */
  onCountChange?: (count: number) => void;
  /** Close the dock (the shell owns the open state). */
  onClose?: () => void;
  /** Test clock for the age labels (epoch seconds). */
  now?: () => number;
}

type ActionError = { message: string; code?: string };

function actionError(exc: unknown): ActionError {
  if (exc instanceof ApprovalApiError) {
    return { message: exc.message, code: exc.code };
  }
  return { message: exc instanceof Error ? exc.message : String(exc) };
}

function chip(
  R: OfficeGameShellProps["React"],
  key: string,
  label: string,
  value: string,
  testId: string,
) {
  return R.createElement("span", {
    key,
    className: "approvals-panel__chip",
    "data-testid": testId,
    "data-value": value,
  }, [
    R.createElement("span", {
      key: "k", className: "approvals-panel__chipKey",
    }, `${label} `),
    R.createElement("span", {
      key: "v", className: "approvals-panel__chipValue",
    }, value),
  ]);
}

function metaLine(R: OfficeGameShellProps["React"], item: PendingApproval,
                  nowValue: number) {
  const parts: unknown[] = [
    R.createElement("span", {
      key: "tool", className: "approvals-panel__tool",
      "data-testid": "approval-tool",
    }, approvalTool(item)),
  ];
  const action = approvalAction(item);
  if (action) {
    parts.push(chip(R, "action", "action", action, "approval-action"));
  }
  const risk = approvalRisk(item);
  if (risk) {
    parts.push(chip(R, "risk", "risk", risk, "approval-risk"));
  }
  const requester = approvalRequester(item);
  parts.push(R.createElement("span", {
    key: "by", className: "approvals-panel__requester",
    "data-testid": "approval-requester",
  }, `requested by ${requester || "unknown"}`));
  parts.push(R.createElement("span", {
    key: "age", className: "approvals-panel__age",
    "data-testid": "approval-age",
  }, formatAge(item.created_at, nowValue)));
  return R.createElement("div", {
    key: "meta", className: "approvals-panel__meta",
  }, parts);
}

function row(
  R: OfficeGameShellProps["React"],
  item: PendingApproval,
  nowValue: number,
  busyId: string | null,
  onDecide: (item: PendingApproval, decision: ApprovalDecision) => void,
) {
  const busy = busyId === item.id;
  const label = approvalTool(item);
  return R.createElement("li", {
    key: item.id,
    className: "approvals-panel__row",
    "data-testid": "approval-row",
    "data-approval-id": item.id,
    "data-approval-capability": item.capability,
    role: "listitem",
  }, [
    R.createElement("p", {
      key: "summary", className: "approvals-panel__summary",
      "data-testid": "approval-summary",
    }, String(item.summary || "").trim() || label),
    metaLine(R, item, nowValue),
    R.createElement("div", {
      key: "id", className: "approvals-panel__idLine",
    }, [
      R.createElement("span", {
        key: "label", className: "approvals-panel__idLabel",
      }, "id "),
      R.createElement("code", {
        key: "id", className: "approvals-panel__id",
        "data-testid": "approval-id",
      }, item.id),
    ]),
    R.createElement("div", {
      key: "actions", className: "approvals-panel__actions",
    }, [
      sakugaButton(R, {
        key: "approve", type: "button",
        className: "office-game__btn approvals-panel__approve",
        "data-testid": "approval-approve",
        "data-approval-id": item.id,
        "aria-label": `Approve ${label} (${item.id})`,
        disabled: busy,
        onClick: () => onDecide(item, "approve"),
      }, busy ? "Approving\u2026" : "Approve"),
      sakugaButton(R, {
        key: "reject", type: "button",
        className: "office-game__btn approvals-panel__reject",
        "data-testid": "approval-reject",
        "data-approval-id": item.id,
        "aria-label": `Reject ${label} (${item.id})`,
        disabled: busy,
        onClick: () => onDecide(item, "deny"),
      }, busy ? "Rejecting\u2026" : "Reject"),
    ]),
  ]);
}

export function createApprovalsPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function ApprovalsPanel(props: ApprovalsPanelProps) {
    const [state, setState] = useState<ApprovalsState>({ kind: "loading" });
    const [busyId, setBusyId] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [failure, setFailure] = useState<ActionError | null>(null);
    // A counter, not a boolean: re-reading after an action must run even when
    // the previous read already completed.
    const [refresh, setRefresh] = useState(0);

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      fetchApprovals({
        base: props.base,
        fetchImpl: props.fetchImpl,
        signal: ctl?.signal,
      }).then((next) => {
        if (!live) return;
        setState(next);
        if (next.kind === "ready") props.onCountChange?.(next.items.length);
        if (next.kind === "error") officeFx("error");
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [props.base, refresh]);

    const onDecide = (item: PendingApproval, decision: ApprovalDecision) => {
      if (busyId) return;
      setBusyId(item.id);
      setNotice(null);
      setFailure(null);
      decideApproval(item.id, decision, {
        base: props.base,
        fetchImpl: props.fetchImpl,
      }).then(() => {
        officeFx(decision === "approve" ? "success" : "warning");
        setNotice(decision === "approve"
          ? `Approved ${approvalTool(item)}.`
          : `Rejected ${approvalTool(item)}.`);
      }).catch((exc) => {
        officeFx("error");
        setFailure(actionError(exc));
      }).finally(() => {
        setBusyId(null);
        // Re-read the journal after EVERY attempt, including a typed refusal:
        // an id can expire or be settled by another surface between the read
        // and the click, and the list must show what the journal now says.
        setRefresh((n: number) => n + 1);
      });
    };

    const nowValue = (props.now ? props.now() : Date.now() / 1000);
    const items = state.kind === "ready" ? state.items : [];
    const count = items.length;

    const head = R.createElement("div", {
      key: "head", className: "approvals-panel__head",
    }, [
      R.createElement("h3", {
        key: "title", className: "approvals-panel__title",
        "data-testid": "approvals-title",
      }, "Approvals"),
      R.createElement("span", {
        key: "count", className: "approvals-panel__count",
        "data-testid": "approvals-count",
        "data-count": String(count),
        "aria-label": `${count} pending approval${count === 1 ? "" : "s"}`,
      }, `${count} pending`),
      R.createElement(OfficeTooltip, {
        key: "refresh-tip",
        content: "Re-read the approval journal; settled ids disappear.",
      }, sakugaButton(R, {
        key: "refresh", type: "button",
        className: "office-game__btn approvals-panel__refresh",
        "data-testid": "approvals-refresh",
        "aria-label": "Refresh pending approvals",
        disabled: state.kind === "loading",
        onClick: () => setRefresh((n: number) => n + 1),
      }, "Refresh")),
    ]);

    const body: unknown[] = [];
    if (state.kind === "loading") {
      body.push(R.createElement("p", {
        key: "loading", className: "approvals-panel__status",
        "data-testid": "approvals-loading",
        role: "status", "aria-live": "polite",
      }, [
        "Loading pending approvals\u2026",
        sakugaProgress(R, { label: "Loading pending approvals", value: 0.08 }),
      ]));
    } else if (state.kind === "error") {
      body.push(R.createElement("div", {
        key: "error", className: "approvals-panel__error",
        "data-testid": "approvals-error",
        role: "alert",
      }, [
        R.createElement("p", { key: "m" }, state.message),
        state.code
          ? R.createElement("p", {
              key: "c", className: "approvals-panel__code",
            }, state.code)
          : null,
      ]));
    } else if (items.length === 0) {
      body.push(R.createElement("p", {
        key: "empty", className: "approvals-panel__empty",
        "data-testid": "approvals-empty",
        role: "status",
      }, "No pending approvals."));
    } else {
      body.push(R.createElement("ul", {
        key: "list", className: "approvals-panel__list",
        "data-testid": "approvals-list",
        role: "list",
        "aria-label": "Pending approvals",
      }, items.map((item) => row(R, item, nowValue, busyId, onDecide))));
    }

    if (notice) {
      body.push(R.createElement("p", {
        key: "notice", className: "approvals-panel__notice",
        "data-testid": "approvals-notice",
        role: "status", "aria-live": "polite",
      }, notice));
    }
    if (failure) {
      body.push(R.createElement("div", {
        key: "failure", className: "approvals-panel__actionError",
        "data-testid": "approvals-action-error",
        role: "alert",
      }, [
        R.createElement("p", { key: "m" },
          failure.code
            ? `${failure.code}: ${failure.message}`
            : failure.message),
      ]));
    }

    const children: unknown[] = [head, ...body];
    if (props.onClose) {
      children.unshift(R.createElement("div", {
        key: "closeRow", className: "approvals-panel__closeRow",
      }, sakugaButton(R, {
        key: "close", type: "button",
        className: "office-game__btn approvals-panel__close",
        "data-testid": "approvals-close",
        "aria-label": "Close approvals",
        onClick: () => props.onClose?.(),
      }, "Close")));
    }

    return R.createElement("div", {
      className: "approvals-panel",
      "data-testid": "approvals-panel",
      "data-state": state.kind,
      "data-count": String(count),
      role: "region",
      "aria-label": "Office approvals",
      "aria-busy": state.kind === "loading" ? "true" : "false",
    }, children);
  };
}
