/* Agent control drawer: one Business OS agent, opened from the office roster.
 *
 * The drawer is a READ+CONTROL surface over durable rows only. It resolves the
 * clicked office seat to a Business OS agent by an explicit link (see
 * agent-control.ts); a seat with no control record says so and offers the real
 * managed agents instead of inventing one. Every action calls the owner-scoped
 * bridge and then re-reads the record, so the status shown afterwards is the
 * store's answer — never a local guess.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import type { AgentSeat } from "./agents-panel";
import {
  type AgentDetail, type AgentRecord, type AuditEvent,
  BusinessApiError, archiveAgent, bootstrapOfficeSeats, fetchAgent, fetchAgents,
  fetchOverview, pauseAgent, resumeAgent, revokeAgent, stopAgent, takeoverAgent,
} from "./business-api";
import {
  autonomyLabel, budgetSummary, canArchive, canPause, canResume, canRevoke,
  matchAgentToSeat, permissionRows, permissionSummary, rateSummary,
  scheduleSummary,
} from "./agent-control";

export interface AgentControlDrawerProps {
  seat?: AgentSeat | null;
  base?: string;
  fetchImpl?: typeof fetch;
  onClose?: () => void;
}

type LoadState =
  | { kind: "loading"; message?: string }
  | { kind: "ready"; tenantId: string; businessId: string | null;
      agents: AgentRecord[]; linked: AgentRecord | null }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string; code?: string };

function errText(exc: unknown): { message: string; code?: string } {
  if (exc instanceof BusinessApiError) {
    return { message: exc.message, code: exc.code };
  }
  return { message: exc instanceof Error ? exc.message : String(exc) };
}

function section(R: OfficeGameShellProps["React"], title: string,
                 key: string, children: unknown[]) {
  return R.createElement("section", {
    key, className: "agent-control__section",
    "data-testid": `agent-control-section-${key}`,
  }, [
    R.createElement("h4", { key: "h", className: "agent-control__sectionTitle" },
      title),
    ...children,
  ]);
}

function kv(R: OfficeGameShellProps["React"], label: string, value: string,
            key: string) {
  return R.createElement("div", {
    key, className: "agent-control__kv",
    "data-testid": `agent-control-${key}`,
  }, [
    R.createElement("span", { key: "l", className: "agent-control__k" }, label),
    R.createElement("span", { key: "v", className: "agent-control__v" }, value),
  ]);
}

function auditRow(R: OfficeGameShellProps["React"], event: AuditEvent,
                  index: number) {
  return R.createElement("li", {
    key: `${event.event_id}:${index}`, className: "agent-control__auditRow",
    "data-testid": "agent-control-audit-row",
    "data-action": event.action,
  }, [
    R.createElement("span", { key: "a", className: "agent-control__auditAction" },
      event.action),
    R.createElement("span", { key: "o", className: "agent-control__auditMeta" },
      `${event.outcome} · ${event.actor} · ${event.created_at}`),
  ]);
}

export function createAgentControlDrawer(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function AgentControlDrawer(props: AgentControlDrawerProps) {
    const [load, setLoad] = useState<LoadState>({ kind: "loading" });
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [detail, setDetail] = useState<AgentDetail | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    const seatSlug = props.seat?.slug ?? "";
    const seatName = props.seat?.name ?? "";
    // The office roster is imported at most once per open drawer. A repeat is
    // idempotent on the backend, but there is no reason to ask twice.
    const bootstrapped = useRef(false);

    // Resolve the scope and the seat's linked agent once per seat. A clicked
    // seat with no tenant or no linked agent triggers ONE owner-gated import of
    // the office roster, then the overview/agents are re-read. A failed import
    // surfaces its typed code; the drawer never invents an agent.
    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      const opts = {
        base: props.base, fetchImpl: props.fetchImpl, signal: ctl?.signal,
      };
      (async () => {
        try {
          let overview = await fetchOverview(opts);
          let tenantId = overview.default_tenant_id;
          let businessId = overview.default_business_id;
          let listed = tenantId
            ? await fetchAgents(
              { tenant_id: tenantId, business_id: businessId ?? undefined },
              opts)
            : { agents: [] as AgentRecord[] };
          let linked = matchAgentToSeat(listed.agents, props.seat);

          if (!linked && !bootstrapped.current) {
            bootstrapped.current = true;
            if (live) setLoad({
              kind: "loading",
              message: "Importing the office roster into the Business OS…",
            });
            await bootstrapOfficeSeats({}, opts);
            overview = await fetchOverview(opts);
            tenantId = overview.default_tenant_id;
            businessId = overview.default_business_id;
            listed = tenantId
              ? await fetchAgents(
                { tenant_id: tenantId, business_id: businessId ?? undefined },
                opts)
              : { agents: [] as AgentRecord[] };
            linked = matchAgentToSeat(listed.agents, props.seat);
          }

          if (!live) return;
          if (!tenantId) {
            setLoad({
              kind: "empty",
              message: "The Business OS still reports no tenant after the "
                + "seat import. Check the Business OS store and retry.",
            });
            return;
          }
          setLoad({
            kind: "ready", tenantId, businessId,
            agents: listed.agents, linked,
          });
          setSelectedId(linked ? linked.agent_id : null);
        } catch (exc) {
          if (!live) return;
          const { message, code } = errText(exc);
          setLoad({ kind: "error", message, code });
        }
      })();
      return () => { live = false; ctl?.abort(); };
      // The seat identity and the host seam are the effect's dependencies.
    }, [props.base, props.fetchImpl, seatSlug, seatName]);

    // Read the selected agent's full record (including its audit trail).
    useEffect(() => {
      if (!selectedId || load.kind !== "ready") {
        setDetail(null);
        return;
      }
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      (async () => {
        try {
          const next = await fetchAgent(selectedId, { tenant_id: load.tenantId }, {
            base: props.base, fetchImpl: props.fetchImpl, signal: ctl?.signal,
          });
          if (live) setDetail(next);
        } catch (exc) {
          if (!live) return;
          const { message, code } = errText(exc);
          setActionError(code ? `${code}: ${message}` : message);
        }
      })();
      return () => { live = false; ctl?.abort(); };
    }, [selectedId, load.kind, props.base, props.fetchImpl,
        load.kind === "ready" ? load.tenantId : ""]);

    const selected = detail?.agent
      ?? (load.kind === "ready"
        ? load.agents.find((a) => a.agent_id === selectedId) ?? null
        : null);

    const refresh = async () => {
      if (!selectedId || load.kind !== "ready") return;
      const next = await fetchAgent(selectedId, { tenant_id: load.tenantId }, {
        base: props.base, fetchImpl: props.fetchImpl,
      });
      setDetail(next);
    };

    const run = async (label: string, fn: () => Promise<unknown>) => {
      setBusy(label);
      setActionError(null);
      setNotice(null);
      try {
        await fn();
        await refresh();
        setNotice(`${label} recorded by the Business OS.`);
      } catch (exc) {
        const { message, code } = errText(exc);
        setActionError(code ? `${code}: ${message}` : message);
      } finally {
        setBusy(null);
      }
    };

    const close = () => props.onClose?.();
    const header = R.createElement("div", {
      key: "head", className: "office-game__dockHead office-theme-dock-head",
    }, [
      R.createElement("span", { key: "t", className: "office-theme-dock-title" },
        seatName ? `Agent controls — ${seatName}` : "Agent controls"),
      R.createElement("button", {
        key: "x", type: "button",
        className: "office-game__btn office-theme-dock-close",
        "data-testid": "agent-control-close", "aria-label": "Close agent controls",
        onClick: close,
      }, "Close"),
    ]);

    const children: unknown[] = [header];

    if (load.kind === "loading") {
      children.push(R.createElement("div", {
        key: "loading", className: "agent-control__state",
        "data-testid": "agent-control-loading", role: "status",
      }, load.message ?? "Loading the agent control record…"));
    } else if (load.kind === "error" || load.kind === "empty") {
      children.push(R.createElement("div", {
        key: "state",
        className: "agent-control__state agent-control__state--error",
        "data-testid": "agent-control-state", role: "alert",
        "data-code": load.kind === "error" ? load.code : undefined,
      }, load.message));
    } else if (load.agents.length === 0) {
      children.push(R.createElement("div", {
        key: "noagents", className: "agent-control__state",
        "data-testid": "agent-control-empty", role: "status",
      }, "No managed agents exist in the Business OS yet. The office roster is "
        + "not a control record; create an agent through the Business OS "
        + "service before controlling one here."));
    } else {
      if (!selected) {
        children.push(R.createElement("div", {
          key: "unlinked", className: "agent-control__state",
          "data-testid": "agent-control-unlinked", role: "status",
        }, "This office seat has no linked Business OS agent. Choose a managed "
          + "agent below; nothing is linked automatically."));
        children.push(R.createElement("label", {
          key: "picker", className: "agent-control__picker",
        }, "Managed agent",
          R.createElement("select", {
            key: "select",
            "data-testid": "agent-control-picker",
            value: selectedId ?? "",
            onChange: (event: { target: { value: string } }) =>
              setSelectedId(event.target.value || null),
          }, [
            R.createElement("option", { key: "", value: "" }, "Choose…"),
            ...load.agents.map((agent) => R.createElement("option", {
              key: agent.agent_id, value: agent.agent_id,
            }, `${agent.display_name} — ${agent.role_id} (${agent.status})`)),
          ])));
      }
    }

    if (selected) {
      const identity = [
        kv(R, "Role", `${selected.role_id} · ${selected.department}`, "role"),
        kv(R, "Room", selected.room || "unassigned", "room"),
        kv(R, "Desk", selected.desk || "unassigned", "desk"),
        kv(R, "Status", selected.status, "status"),
        kv(R, "Revision", String(selected.revision), "revision"),
      ];
      children.push(section(R, "Identity", "identity", identity));

      children.push(section(R, "Brain", "brain", [
        kv(R, "Provider", selected.provider || "unset", "provider"),
        kv(R, "Model", selected.model || "unset", "model"),
        kv(R, "Reasoning", selected.reasoning_effort || "unset", "reasoning"),
        kv(R, "Fallback", selected.fallback_provider && selected.fallback_model
          ? `${selected.fallback_provider} / ${selected.fallback_model}`
          : "none", "fallback"),
        kv(R, "Max tokens", String(selected.max_tokens), "max-tokens"),
      ]));

      const permRows = permissionRows(selected).map(([category, scopes]) =>
        kv(R, category, scopes.length ? scopes.join(", ") : "deny", `perm-${category}`));
      children.push(section(R, "Permissions", "permissions", [
        R.createElement("p", {
          key: "summary", className: "agent-control__muted",
          "data-testid": "agent-control-permission-summary",
        }, permissionSummary(selected)),
        ...permRows,
      ]));

      children.push(section(R, "Limits", "limits", [
        kv(R, "Spend", budgetSummary(selected), "budget"),
        kv(R, "Rate", rateSummary(selected), "rate"),
        kv(R, "Approvals", selected.safety.approval_required.join(", ") || "none",
          "approvals"),
        kv(R, "Escalation", selected.safety.escalation_target, "escalation"),
      ]));

      children.push(section(R, "Autonomy & schedule", "autonomy", [
        kv(R, "Autonomy", autonomyLabel(selected.safety.autonomy), "autonomy-level"),
        kv(R, "Schedule", scheduleSummary(selected), "schedule"),
        kv(R, "Concurrent turns", String(selected.max_concurrent_turns),
          "concurrency"),
      ]));

      children.push(section(R, "Audit", "audit", [
        R.createElement("ul", {
          key: "rows", className: "agent-control__audit",
          "data-testid": "agent-control-audit",
        }, (detail?.audit ?? []).map((event, index) =>
          auditRow(R, event, index))),
      ]));

      const buttons: unknown[] = [];
      if (canPause(selected)) {
        buttons.push(R.createElement("button", {
          key: "pause", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-pause",
          disabled: busy !== null,
          onClick: () => run("Pause", () => pauseAgent(selected.agent_id, {
            base: props.base, fetchImpl: props.fetchImpl })),
        }, "Pause"));
      }
      if (canResume(selected)) {
        buttons.push(R.createElement("button", {
          key: "resume", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-resume",
          disabled: busy !== null,
          onClick: () => run("Resume", () => resumeAgent(selected.agent_id, {
            base: props.base, fetchImpl: props.fetchImpl })),
        }, "Resume"));
      }
      buttons.push(R.createElement("button", {
        key: "stop", type: "button", className: "office-game__btn",
        "data-testid": "agent-control-stop",
        disabled: busy !== null,
        onClick: () => run("Stop", () => stopAgent(selected.agent_id, {}, {
          base: props.base, fetchImpl: props.fetchImpl })),
      }, "Stop turn"));
      buttons.push(R.createElement("button", {
        key: "takeover", type: "button", className: "office-game__btn",
        "data-testid": "agent-control-takeover",
        disabled: busy !== null,
        onClick: () => run("Takeover", () => takeoverAgent(selected.agent_id, {
          base: props.base, fetchImpl: props.fetchImpl })),
      }, "Take over"));
      if (canRevoke(selected)) {
        buttons.push(R.createElement("button", {
          key: "revoke", type: "button",
          className: "office-game__btn agent-control__danger",
          "data-testid": "agent-control-revoke",
          disabled: busy !== null,
          onClick: () => run("Revoke", () => revokeAgent(
            selected.agent_id, undefined, {
              base: props.base, fetchImpl: props.fetchImpl })),
        }, "Revoke all scopes"));
      }
      if (canArchive(selected)) {
        buttons.push(R.createElement("button", {
          key: "archive", type: "button",
          className: "office-game__btn agent-control__danger",
          "data-testid": "agent-control-archive",
          disabled: busy !== null,
          onClick: () => run("Archive", () => archiveAgent(selected.agent_id, {
            base: props.base, fetchImpl: props.fetchImpl })),
        }, "Archive"));
      }
      children.push(R.createElement("div", {
        key: "actions", className: "agent-control__actions",
        "data-testid": "agent-control-actions",
      }, buttons));
    }

    if (busy) {
      children.push(R.createElement("div", {
        key: "busy", className: "agent-control__state",
        "data-testid": "agent-control-busy", role: "status",
      }, `${busy}…`));
    }
    if (actionError) {
      children.push(R.createElement("div", {
        key: "error", className: "agent-control__state agent-control__state--error",
        "data-testid": "agent-control-error", role: "alert",
      }, actionError));
    }
    if (notice) {
      children.push(R.createElement("div", {
        key: "notice", className: "agent-control__state",
        "data-testid": "agent-control-notice", role: "status",
      }, notice));
    }

    return R.createElement("div", {
      className: "agent-control",
      "data-testid": "agent-control",
      "data-state": load.kind,
      "data-seat-slug": seatSlug || undefined,
    }, children);
  };
}
