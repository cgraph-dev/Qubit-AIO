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
import { createEffectiveStatePanel } from "./EffectiveStatePanel";
import type { AgentSeat } from "./agents-panel";
import {
  type AgentDetail, type AgentRecord, type AgentTurnPlan, type AuditEvent,
  BusinessApiError, archiveAgent, bootstrapOfficeSeats, fetchAgent, fetchAgents,
  fetchOverview, pauseAgent, previewAgentTurn, resumeAgent, revokeAgent,
  stopAgent, takeoverAgent, updateAgent,
} from "./business-api";
import {
  ACTION_SCOPES, AUTONOMY_LEVELS, PERMISSION_CATEGORIES,
  REASONING_EFFORTS, type AgentDraft,
  addPermissionScope, autonomyLabel, budgetSummary, buildAgentChanges,
  canArchive, canPause, canResume, canRevoke, draftFromAgent, formatMinor,
  isDraftDirty, matchAgentToSeat, permissionRows, permissionSummary,
  rateSummary, removePermissionScope, scheduleSummary, validateDraft,
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
  const EffectiveStatePanel = createEffectiveStatePanel(R, hooks);

  return function AgentControlDrawer(props: AgentControlDrawerProps) {
    const [load, setLoad] = useState<LoadState>({ kind: "loading" });
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [detail, setDetail] = useState<AgentDetail | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [actionError, setActionError] = useState<
      { message: string; code?: string } | null>(null);
    // Edit mode is local to this drawer: the durable record is only written by
    // the explicit Save action, and the store's answer replaces the draft.
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<AgentDraft | null>(null);
    const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
    const [permInput, setPermInput] = useState<Record<string, string>>({});
    // Turn preview is a read-only plan. It never starts a turn, spends, or
    // runs a tool; a refusal replaces the plan with the typed backend reason.
    const [previewPrompt, setPreviewPrompt] = useState("");
    const [previewTools, setPreviewTools] = useState("");
    const [previewSpend, setPreviewSpend] = useState("");
    const [previewPlan, setPreviewPlan] = useState<AgentTurnPlan | null>(null);
    const [previewError, setPreviewError] = useState<
      { message: string; code?: string } | null>(null);
    const [previewBusy, setPreviewBusy] = useState(false);

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
      // A preview belongs to one selected agent. Switching records discards
      // the previous plan rather than showing it beside another agent.
      setPreviewPlan(null);
      setPreviewError(null);
      setPreviewPrompt("");
      setPreviewTools("");
      setPreviewSpend("");
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
          setActionError(errText(exc));
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
        setActionError(errText(exc));
      } finally {
        setBusy(null);
      }
    };

    const beginEdit = () => {
      if (!selected) return;
      setDraft(draftFromAgent(selected));
      setFieldErrors({});
      setPermInput({});
      setActionError(null);
      setNotice(null);
      setEditing(true);
    };

    const cancelEdit = () => {
      // Discard the draft; the record below is still the store's answer.
      setEditing(false);
      setDraft(null);
      setFieldErrors({});
      setPermInput({});
      setActionError(null);
      setNotice("Edit cancelled; the stored record is unchanged.");
    };

    const resetDraft = () => {
      if (!selected) return;
      setDraft(draftFromAgent(selected));
      setFieldErrors({});
      setPermInput({});
      setNotice("Draft reset to the stored record.");
    };

    const patchDraft = (patch: Partial<AgentDraft>) => {
      setDraft((current) => (current ? { ...current, ...patch } : current));
    };

    const saveEdit = async () => {
      if (!selected || !draft) return;
      const errors = validateDraft(draft);
      setFieldErrors(errors);
      setActionError(null);
      setNotice(null);
      if (Object.keys(errors).length > 0) {
        setNotice("Fix the highlighted fields before saving.");
        return;
      }
      const changes = buildAgentChanges(draft, selected);
      if (Object.keys(changes).length === 0) {
        setNotice("No changes to save.");
        return;
      }
      setBusy("Save");
      try {
        await updateAgent(selected.agent_id, changes, {
          base: props.base, fetchImpl: props.fetchImpl,
          query: { tenant_id: load.kind === "ready" ? load.tenantId : undefined },
        });
        // Re-read the record and its audit trail so the panel shows the store's
        // answer (new revision, scopes, budgets and the agent.update event).
        await refresh();
        setEditing(false);
        setDraft(null);
        setFieldErrors({});
        setPermInput({});
        setNotice("Changes saved and recorded in the audit trail.");
      } catch (exc) {
        setActionError(errText(exc));
      } finally {
        setBusy(null);
      }
    };

    const addScope = (category: string) => {
      if (!draft) return;
      const raw = permInput[category] ?? "";
      const { permissions, check } = addPermissionScope(
        draft.permissions, category, raw);
      if (!check.ok) {
        setFieldErrors({ ...fieldErrors, [`perm:${category}`]: check.error });
        return;
      }
      const nextErrors = { ...fieldErrors };
      delete nextErrors[`perm:${category}`];
      setFieldErrors(nextErrors);
      setPermInput({ ...permInput, [category]: "" });
      patchDraft({ permissions });
    };

    const removeScope = (category: string, scope: string) => {
      if (!draft) return;
      patchDraft({
        permissions: removePermissionScope(draft.permissions, category, scope),
      });
    };

    const runPreview = async () => {
      if (!selected) return;
      setPreviewBusy(true);
      setPreviewError(null);
      setPreviewPlan(null);
      const body: {
        prompt: string; requested_tools?: string[]; spend_minor?: number;
      } = { prompt: previewPrompt };
      const tools = previewTools
        .split(/[\s,]+/).map((value) => value.trim()).filter(Boolean);
      if (tools.length) body.requested_tools = tools;
      const spendText = previewSpend.trim();
      if (spendText) {
        const amount = Number(spendText);
        if (!Number.isFinite(amount) || amount < 0) {
          setPreviewError({
            message: "Estimated spend must be a non-negative number of USD.",
          });
          setPreviewBusy(false);
          return;
        }
        body.spend_minor = Math.round(amount * 100);
      }
      try {
        const result = await previewAgentTurn(selected.agent_id, body, {
          base: props.base, fetchImpl: props.fetchImpl,
          query: { tenant_id: load.kind === "ready"
            ? load.tenantId : undefined },
        });
        setPreviewPlan(result.plan);
      } catch (exc) {
        setPreviewError(errText(exc));
      } finally {
        setPreviewBusy(false);
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
      const lock = busy !== null;
      const read = editing ? draft : null;
      const fieldError = (key: string) => (fieldErrors[key]
        ? R.createElement("span", {
          key: `err-${key}`, className: "agent-control__error",
          "data-testid": `agent-control-field-error-${key.replace(":", "-")}`,
        }, fieldErrors[key])
        : null);
      const textField = (
        key: string, label: string, value: string,
        onChange: (next: string) => void,
      ) => R.createElement("label", {
        key: `field-${key}`, className: "agent-control__field",
        "data-testid": `agent-control-field-${key}`,
      }, [
        R.createElement("span", { key: "l", className: "agent-control__k" },
          label),
        R.createElement("input", {
          key: "i", type: "text", className: "agent-control__input",
          "data-testid": `agent-control-input-${key}`,
          value, disabled: lock || !editing,
          onChange: (event: { target: { value: string } }) =>
            onChange(event.target.value),
        }),
        fieldError(key),
      ]);
      const selectField = (
        key: string, label: string, value: string,
        options: readonly string[], onChange: (next: string) => void,
        render?: (option: string) => string,
      ) => R.createElement("label", {
        key: `field-${key}`, className: "agent-control__field",
        "data-testid": `agent-control-field-${key}`,
      }, [
        R.createElement("span", { key: "l", className: "agent-control__k" },
          label),
        R.createElement("select", {
          key: "s", className: "agent-control__input",
          "data-testid": `agent-control-input-${key}`,
          value, disabled: lock || !editing,
          onChange: (event: { target: { value: string } }) =>
            onChange(event.target.value),
        }, options.map((option) => R.createElement("option", {
          key: option, value: option,
        }, render ? render(option) : option))),
        fieldError(key),
      ]);

      const identity = [
        kv(R, "Role", `${selected.role_id} · ${selected.department}`, "role"),
        kv(R, "Room", selected.room || "unassigned", "room"),
        kv(R, "Desk", selected.desk || "unassigned", "desk"),
        kv(R, "Status", selected.status, "status"),
        kv(R, "Revision", String(selected.revision), "revision"),
      ];
      children.push(section(R, "Identity", "identity", identity));

      children.push(section(R, "Brain", "brain", read ? [
        textField("provider", "Provider", read.provider,
          (next) => patchDraft({ provider: next })),
        textField("model", "Model", read.model,
          (next) => patchDraft({ model: next })),
        selectField("reasoning", "Reasoning", read.reasoning_effort,
          REASONING_EFFORTS, (next) => patchDraft({ reasoning_effort: next })),
        textField("fallback-provider", "Fallback provider",
          read.fallback_provider,
          (next) => patchDraft({ fallback_provider: next })),
        textField("fallback-model", "Fallback model", read.fallback_model,
          (next) => patchDraft({ fallback_model: next })),
        textField("max-tokens", "Max tokens", read.max_tokens,
          (next) => patchDraft({ max_tokens: next })),
      ] : [
        kv(R, "Provider", selected.provider || "unset", "provider"),
        kv(R, "Model", selected.model || "unset", "model"),
        kv(R, "Reasoning", selected.reasoning_effort || "unset", "reasoning"),
        kv(R, "Fallback", selected.fallback_provider && selected.fallback_model
          ? `${selected.fallback_provider} / ${selected.fallback_model}`
          : "none", "fallback"),
        kv(R, "Max tokens", String(selected.max_tokens), "max-tokens"),
      ]));

      if (read) {
        const granted = PERMISSION_CATEGORIES.filter(
          (category) => (read.permissions[category] ?? []).length > 0);
        const draftPermissionSummary = granted.length
          ? granted.map((category) =>
            `${category}: ${(read.permissions[category] ?? []).length}`).join(" · ")
          : "deny by default (no scopes granted)";
        const permEditor = PERMISSION_CATEGORIES.map((category) => {
          const scopes = read.permissions[category] ?? [];
          const allowed = ACTION_SCOPES[category];
          const chips = scopes.length
            ? scopes.map((scope) => R.createElement("span", {
              key: `chip-${scope}`, className: "agent-control__chip",
              "data-testid": `agent-control-perm-chip-${category}`,
              "data-scope": scope,
            }, [
              R.createElement("span", { key: "s" }, scope),
              R.createElement("button", {
                key: "r", type: "button",
                "data-testid": "agent-control-perm-remove",
                "data-category": category, "data-scope": scope,
                disabled: lock,
                "aria-label": `Remove ${category} scope ${scope}`,
                onClick: () => removeScope(category, scope),
              }, "×"),
            ]))
            : [R.createElement("span", {
              key: "deny", className: "agent-control__muted",
              "data-testid": `agent-control-perm-deny-${category}`,
            }, "deny")];
          return R.createElement("div", {
            key: `perm-${category}`, className: "agent-control__permRow",
            "data-testid": `agent-control-perm-row-${category}`,
          }, [
            R.createElement("span", { key: "c", className: "agent-control__k" },
              category),
            R.createElement("div", { key: "chips", className: "agent-control__chips" },
              chips),
            R.createElement("input", {
              key: "i", type: "text", className: "agent-control__input",
              "data-testid": "agent-control-perm-input",
              "data-category": category,
              value: permInput[category] ?? "",
              placeholder: allowed ? allowed.join(", ")
                : category === "files" ? "/absolute/path or ~/relative" : "scope",
              disabled: lock,
              onChange: (event: { target: { value: string } }) =>
                setPermInput({ ...permInput, [category]: event.target.value }),
            }),
            R.createElement("button", {
              key: "a", type: "button", className: "office-game__btn",
              "data-testid": "agent-control-perm-add",
              "data-category": category, disabled: lock,
              onClick: () => addScope(category),
            }, "Add"),
            allowed
              ? R.createElement("span", {
                key: "allowed", className: "agent-control__muted",
                "data-testid": `agent-control-perm-allowed-${category}`,
              }, `Known scopes: ${allowed.join(", ")}`)
              : null,
            fieldError(`perm:${category}`),
          ]);
        });
        children.push(section(R, "Permissions", "permissions", [
          R.createElement("p", {
            key: "summary", className: "agent-control__muted",
            "data-testid": "agent-control-permission-summary",
          }, draftPermissionSummary),
          R.createElement("p", {
            key: "note", className: "agent-control__muted",
            "data-testid": "agent-control-permission-note",
          }, "Permissions stay explicit and deny-by-default: an empty category "
            + "grants nothing, every scope is named, and a wildcard is refused "
            + "by the Business OS."),
          ...permEditor,
        ]));
      } else {
        const permRows = permissionRows(selected).map(([category, scopes]) =>
          kv(R, category, scopes.length ? scopes.join(", ") : "deny",
            `perm-${category}`));
        children.push(section(R, "Permissions", "permissions", [
          R.createElement("p", {
            key: "summary", className: "agent-control__muted",
            "data-testid": "agent-control-permission-summary",
          }, permissionSummary(selected)),
          ...permRows,
        ]));
      }

      const approvals = selected.safety.approval_required.join(", ") || "none";
      children.push(section(R, "Limits", "limits", read ? [
        textField("daily-spend", "Daily spend (USD)", read.daily_spend,
          (next) => patchDraft({ daily_spend: next })),
        textField("monthly-spend", "Monthly spend (USD)", read.monthly_spend,
          (next) => patchDraft({ monthly_spend: next })),
        textField("per-action-limit", "Per-action limit (USD)",
          read.per_action_limit,
          (next) => patchDraft({ per_action_limit: next })),
        textField("max-turns-per-day", "Turns per day", read.max_turns_per_day,
          (next) => patchDraft({ max_turns_per_day: next })),
        textField("max-tokens-per-turn", "Tokens per turn",
          read.max_tokens_per_turn,
          (next) => patchDraft({ max_tokens_per_turn: next })),
        textField("rate-limit", "Requests per minute",
          read.rate_limit_per_minute,
          (next) => patchDraft({ rate_limit_per_minute: next })),
        kv(R, "Approvals", approvals, "approvals"),
        R.createElement("p", {
          key: "approval-note", className: "agent-control__muted",
          "data-testid": "agent-control-approval-note",
        }, "Spend, legal, publish, contact and irreversible actions keep their "
          + "human approval requirement; this form cannot remove them."),
        kv(R, "Escalation", selected.safety.escalation_target, "escalation"),
      ] : [
        kv(R, "Spend", budgetSummary(selected), "budget"),
        kv(R, "Rate", rateSummary(selected), "rate"),
        kv(R, "Approvals", approvals, "approvals"),
        kv(R, "Escalation", selected.safety.escalation_target, "escalation"),
      ]));

      children.push(section(R, "Autonomy & schedule", "autonomy", read ? [
        selectField("autonomy", "Autonomy", read.autonomy, AUTONOMY_LEVELS,
          (next) => patchDraft({ autonomy: next }), autonomyLabel),
        textField("schedule", "Schedule", read.schedule,
          (next) => patchDraft({ schedule: next })),
        textField("timezone", "Timezone", read.timezone,
          (next) => patchDraft({ timezone: next })),
        kv(R, "Concurrent turns", String(selected.max_concurrent_turns),
          "concurrency"),
      ] : [
        kv(R, "Autonomy", autonomyLabel(selected.safety.autonomy),
          "autonomy-level"),
        kv(R, "Schedule", scheduleSummary(selected), "schedule"),
        kv(R, "Concurrent turns", String(selected.max_concurrent_turns),
          "concurrency"),
      ]));

      const previewGranted = previewPlan
        ? Object.entries(previewPlan.tool_scopes)
          .filter(([, scopes]) => scopes.length > 0)
          .map(([category, scopes]) => `${category}: ${scopes.length}`)
          .join(" · ") || "deny by default"
        : "";
      const previewPlanning = previewPlan ? [
        R.createElement("div", {
          key: "plan", className: "agent-control__previewPlan",
          "data-testid": "agent-control-preview-plan",
          "data-allowed": String(previewPlan.allowed),
        }, [
          R.createElement("p", {
            key: "allowed", className: "agent-control__previewVerdict",
            "data-testid": "agent-control-preview-allowed", role: "status",
          }, previewPlan.allowed
            ? "ALLOWED — preview only"
            : "REFUSED — see the reason"),
          kv(R, "Provider", previewPlan.provider, "preview-provider"),
          kv(R, "Model", previewPlan.model, "preview-model"),
          kv(R, "Reasoning", previewPlan.reasoning_effort,
            "preview-reasoning"),
          kv(R, "Autonomy", autonomyLabel(previewPlan.autonomy),
            "preview-autonomy"),
          kv(R, "Max tokens", String(previewPlan.max_tokens),
            "preview-max-tokens"),
          kv(R, "Prompt estimate",
            `${previewPlan.estimated_prompt_tokens} tokens`,
            "preview-prompt-tokens"),
          kv(R, "Projected",
            `${previewPlan.projected_tokens} tokens`, "preview-projected"),
          kv(R, "Approvals",
            previewPlan.approval_actions_required.join(", ") || "none",
            "preview-approvals"),
          kv(R, "Approvals satisfied",
            previewPlan.approvals_satisfied ? "yes" : "no",
            "preview-approvals-satisfied"),
          kv(R, "Requested tools",
            previewPlan.requested_tools.join(", ") || "none",
            "preview-tools"),
          kv(R, "Granted scopes", previewGranted, "preview-scopes"),
          kv(R, "Estimated spend",
            formatMinor(previewPlan.estimated.spend_minor),
            "preview-spend"),
          kv(R, "Daily spend left",
            formatMinor(
              previewPlan.estimated.remaining.daily_spend_remaining_minor),
            "preview-daily-left"),
          kv(R, "Turns left today",
            String(previewPlan.estimated.remaining.turns_remaining_today),
            "preview-turns-left"),
          R.createElement("p", {
            key: "reason", className: "agent-control__muted",
            "data-testid": "agent-control-preview-reason",
          }, previewPlan.reason),
        ]),
      ] : [];

      children.push(section(R, "Preview turn", "preview", [
        R.createElement("p", {
          key: "note", className: "agent-control__muted",
          "data-testid": "agent-control-preview-note",
        }, "Plan a turn without running it. Preview validates the model, "
          + "permissions, budgets, limits and approvals and writes nothing: no "
          + "model call, no spend, no job, no tool side effect."),
        R.createElement("label", {
          key: "prompt-field", className: "agent-control__field",
          "data-testid": "agent-control-field-preview-prompt",
        }, [
          R.createElement("span", { key: "l", className: "agent-control__k" },
            "Prompt"),
          R.createElement("textarea", {
            key: "i", className: "agent-control__input",
            "data-testid": "agent-control-preview-prompt",
            rows: 3, value: previewPrompt,
            disabled: lock || previewBusy,
            placeholder: "What should this agent do next?",
            onChange: (event: { target: { value: string } }) => {
              setPreviewPrompt(event.target.value);
              setPreviewPlan(null);
              setPreviewError(null);
            },
          }),
        ]),
        R.createElement("label", {
          key: "tools-field", className: "agent-control__field",
          "data-testid": "agent-control-field-preview-tools",
        }, [
          R.createElement("span", { key: "l", className: "agent-control__k" },
            "Requested tools (optional)"),
          R.createElement("input", {
            key: "i", type: "text", className: "agent-control__input",
            "data-testid": "agent-control-preview-tools",
            value: previewTools, disabled: lock || previewBusy,
            placeholder: "comma-separated granted tool names",
            onChange: (event: { target: { value: string } }) => {
              setPreviewTools(event.target.value);
              setPreviewPlan(null);
              setPreviewError(null);
            },
          }),
        ]),
        R.createElement("label", {
          key: "spend-field", className: "agent-control__field",
          "data-testid": "agent-control-field-preview-spend",
        }, [
          R.createElement("span", { key: "l", className: "agent-control__k" },
            "Estimated spend (USD, optional)"),
          R.createElement("input", {
            key: "i", type: "text", className: "agent-control__input",
            "data-testid": "agent-control-preview-spend",
            value: previewSpend, disabled: lock || previewBusy,
            placeholder: "0.00",
            onChange: (event: { target: { value: string } }) => {
              setPreviewSpend(event.target.value);
              setPreviewPlan(null);
              setPreviewError(null);
            },
          }),
        ]),
        R.createElement("div", {
          key: "preview-actions", className: "agent-control__actions",
          "data-testid": "agent-control-preview-actions",
        }, [
          R.createElement("button", {
            key: "run", type: "button", className: "office-game__btn",
            "data-testid": "agent-control-preview-run",
            disabled: lock || previewBusy,
            onClick: () => { void runPreview(); },
          }, previewBusy ? "Planning…" : "Preview turn"),
        ]),
        previewError
          ? R.createElement("div", {
            key: "preview-error",
            className: "agent-control__state agent-control__state--error",
            "data-testid": "agent-control-preview-error", role: "alert",
            "data-code": previewError.code,
          }, previewError.code
            ? `${previewError.code}: ${previewError.message}`
            : previewError.message)
          : null,
        ...previewPlanning,
      ]));

      children.push(section(R, "Audit", "audit", [
        R.createElement("ul", {
          key: "rows", className: "agent-control__audit",
          "data-testid": "agent-control-audit",
        }, (detail?.audit ?? []).map((event, index) =>
          auditRow(R, event, index))),
      ]));

      const buttons: unknown[] = [];
      if (editing) {
        const dirty = isDraftDirty(draft, selected);
        buttons.push(R.createElement("button", {
          key: "save", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-save", disabled: lock,
          onClick: () => { void saveEdit(); },
        }, "Save changes"));
        buttons.push(R.createElement("button", {
          key: "cancel", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-cancel", disabled: lock,
          onClick: cancelEdit,
        }, "Cancel"));
        buttons.push(R.createElement("button", {
          key: "reset", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-reset", disabled: lock || !dirty,
          onClick: resetDraft,
        }, "Reset"));
        if (dirty) {
          buttons.push(R.createElement("span", {
            key: "dirty", className: "agent-control__dirty",
            "data-testid": "agent-control-dirty", role: "status",
          }, "Unsaved changes"));
        }
      } else {
        buttons.push(R.createElement("button", {
          key: "edit", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-edit", disabled: lock,
          onClick: beginEdit,
        }, "Edit controls"));
        if (canPause(selected)) {
          buttons.push(R.createElement("button", {
            key: "pause", type: "button", className: "office-game__btn",
            "data-testid": "agent-control-pause",
            disabled: lock,
            onClick: () => run("Pause", () => pauseAgent(selected.agent_id, {
              base: props.base, fetchImpl: props.fetchImpl })),
          }, "Pause"));
        }
        if (canResume(selected)) {
          buttons.push(R.createElement("button", {
            key: "resume", type: "button", className: "office-game__btn",
            "data-testid": "agent-control-resume",
            disabled: lock,
            onClick: () => run("Resume", () => resumeAgent(selected.agent_id, {
              base: props.base, fetchImpl: props.fetchImpl })),
          }, "Resume"));
        }
        buttons.push(R.createElement("button", {
          key: "stop", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-stop",
          disabled: lock,
          onClick: () => run("Stop", () => stopAgent(selected.agent_id, {}, {
            base: props.base, fetchImpl: props.fetchImpl })),
        }, "Stop turn"));
        buttons.push(R.createElement("button", {
          key: "takeover", type: "button", className: "office-game__btn",
          "data-testid": "agent-control-takeover",
          disabled: lock,
          onClick: () => run("Takeover", () => takeoverAgent(selected.agent_id, {
            base: props.base, fetchImpl: props.fetchImpl })),
        }, "Take over"));
        if (canRevoke(selected)) {
          buttons.push(R.createElement("button", {
            key: "revoke", type: "button",
            className: "office-game__btn agent-control__danger",
            "data-testid": "agent-control-revoke",
            disabled: lock,
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
            disabled: lock,
            onClick: () => run("Archive", () => archiveAgent(
              selected.agent_id, {
                base: props.base, fetchImpl: props.fetchImpl })),
          }, "Archive"));
        }
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
    if (selectedId) {
      children.push(section(R, "Effective state", "effective", [
        R.createElement(EffectiveStatePanel, {
          key: "effective", agentId: selectedId,
          base: props.base, fetchImpl: props.fetchImpl,
        }),
      ]));
    }
    if (actionError) {
      children.push(R.createElement("div", {
        key: "error", className: "agent-control__state agent-control__state--error",
        "data-testid": "agent-control-error", role: "alert",
        "data-code": actionError.code,
      }, actionError.code
        ? `${actionError.code}: ${actionError.message}`
        : actionError.message));
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
