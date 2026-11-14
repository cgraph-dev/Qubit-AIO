/* P2-07 effective-state panel: what an agent can actually do, from the stores.
 *
 * Explicit vs inherited grants, deny wins, revoked/expired grants, the pinned
 * model route, memory scopes, leases, budget, approvals, audit and links. The
 * panel never invents an "allowed" state: a refused read is a named error or
 * unavailable state, and a missing field renders its typed unknown reason.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type EffectiveKnown,
  type EffectiveProjection,
  type EffectiveRead,
  fetchEffectiveState,
  formatMinor,
  grantLabel,
  grantState,
  leaseCounts,
  modelRoute,
  statusLabel,
} from "./effective-state";

export interface EffectiveStatePanelProps {
  agentId: string;
  base?: string;
  fetchImpl?: typeof fetch;
  tenantId?: string;
}

export function createEffectiveStatePanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function EffectiveStatePanel(props: EffectiveStatePanelProps) {
    const [read, setRead] = useState<EffectiveRead>({ kind: "loading" });

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      setRead({ kind: "loading" });
      fetchEffectiveState({
        agentId: props.agentId,
        base: props.base,
        fetchImpl: props.fetchImpl,
        tenantId: props.tenantId,
        signal: ctl?.signal,
      }).then((next) => {
        if (live) setRead(next);
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [props.agentId, props.base, props.fetchImpl, props.tenantId]);

    if (read.kind === "loading") {
      return R.createElement("div", {
        className: "agent-control__state",
        "data-testid": "effective-state",
        "data-state": "loading",
        role: "status",
        "aria-live": "polite",
      }, "Loading effective state…");
    }

    if (read.kind === "error" || read.kind === "unavailable") {
      const code = read.kind === "unavailable" ? read.code : "EFFECTIVE_READ_FAILED";
      const detail = read.kind === "unavailable" ? read.detail : read.message;
      return R.createElement("div", {
        className: "agent-control__state agent-control__state--error",
        "data-testid": "effective-state",
        "data-state": read.kind,
        "data-code": code,
        role: "alert",
      }, [
        R.createElement("strong", { key: "t" }, "Effective state unavailable"),
        R.createElement("div", {
          key: "d", className: "agent-control__muted",
          "data-testid": "effective-error-detail",
        }, `${code}: ${detail}`),
      ]);
    }

    const projection = read.projection;
    const counts = leaseCounts(projection);
    const currency = projection.budget?.currency || "USD";
    const effective = projection.capabilities?.effective ?? [];
    const denied = projection.capabilities?.denied ?? [];
    const profileApplied = projection.capabilities?.profile_applied ?? [];
    const profileDenied = projection.capabilities?.profile_denied ?? [];

    const grantRow = (
      row: { capability?: string; resource?: string; action?: string; label?: string },
      state: string, index: number,
    ) => R.createElement("li", {
      key: `${state}-${index}-${grantLabel(row)}`,
      className: "agent-control__chip",
      "data-testid": "effective-grant",
      "data-state": state,
      "data-grant": grantLabel(row),
    }, `${grantLabel(row)} · ${state}`);

    const known = (
      id: string, label: string, value: EffectiveKnown<{ route?: string }> | undefined,
    ) => R.createElement("div", {
      key: id, "data-testid": id,
      "data-present": String(Boolean(value?.present)),
    }, [
      R.createElement("span", { key: "k", className: "agent-control__k" }, label),
      R.createElement("span", { key: "v" }, value?.present
        ? modelRoute(value)
        : (value?.reason || "unknown")),
    ]);

    return R.createElement("div", {
      className: "agent-control__effective",
      "data-testid": "effective-state",
      "data-state": "available",
      "data-status": projection.status?.value,
      "data-agent-id": projection.agent_id,
      "aria-label": `Effective state for ${projection.agent_id}`,
    }, [
      R.createElement("div", {
        key: "status", className: "agent-control__muted",
        "data-testid": "effective-status",
        "data-status": projection.status?.value,
        role: "status",
      }, `Status: ${statusLabel(projection)}`
        + (projection.status?.reason ? ` — ${projection.status.reason}` : "")),

      R.createElement("section", { key: "grants", "aria-label": "Capabilities" }, [
        R.createElement("h4", { key: "h" }, "Effective capabilities"),
        R.createElement("p", {
          key: "counts", className: "agent-control__muted",
          "data-testid": "effective-grant-counts",
        }, `${effective.length} effective · ${denied.length} denied · `
          + `${projection.capabilities?.revoked?.length ?? 0} revoked`),
        effective.length
          ? R.createElement("ul", { key: "list", "data-testid": "effective-grants" },
            effective.map((row, i) => grantRow(row, grantState(row, "effective"), i)))
          : R.createElement("p", {
            key: "none", "data-testid": "effective-grants-empty",
            className: "agent-control__muted",
          }, projection.capabilities?.reason || "no effective capabilities"),
        denied.length
          ? R.createElement("ul", { key: "denied", "data-testid": "effective-denied" },
            denied.map((row, i) => grantRow(row, "denied", i)))
          : null,
        profileApplied.length || profileDenied.length
          ? R.createElement("p", {
            key: "profile", className: "agent-control__muted",
            "data-testid": "effective-profile",
          }, `profile selectors: ${profileApplied.length} applied, `
            + `${profileDenied.length} denied`)
          : null,
      ]),

      R.createElement("section", { key: "identity", "aria-label": "Grant origin" }, [
        R.createElement("h4", { key: "h" }, "Grant origin"),
        R.createElement("p", {
          key: "counts", "data-testid": "effective-grant-origin",
        }, `${projection.identity?.explicit?.length ?? 0} explicit · `
          + `${projection.identity?.inherited?.length ?? 0} inherited · `
          + `${projection.identity?.revoked?.length ?? 0} revoked · `
          + `${projection.identity?.expired?.length ?? 0} expired`),
        (projection.identity?.inherited?.length ?? 0)
          ? R.createElement("ul", { key: "inh", "data-testid": "effective-inherited" },
            projection.identity.inherited.map(
              (row, i) => grantRow(row, "inherited", i)))
          : null,
      ]),

      R.createElement("section", { key: "model", "aria-label": "Model route" }, [
        R.createElement("h4", { key: "h" }, "Model route"),
        known("effective-model-primary", "primary", projection.model?.primary),
        known("effective-model-fallback", "fallback", projection.model?.fallback),
      ]),

      R.createElement("section", { key: "leases", "aria-label": "Leases" }, [
        R.createElement("h4", { key: "h" }, "Leases"),
        R.createElement("p", {
          key: "c", "data-testid": "effective-lease-counts",
          "data-active": String(counts.active),
          "data-expired": String(counts.expired),
          "data-released": String(counts.released),
        }, `${counts.active} active · ${counts.expired} expired · `
          + `${counts.released} released`
          + (projection.leases?.reason ? ` — ${projection.leases.reason}` : "")),
      ]),

      R.createElement("section", { key: "budget", "aria-label": "Budget" }, [
        R.createElement("h4", { key: "h" }, "Budget"),
        R.createElement("p", {
          key: "c", "data-testid": "effective-budget",
          "data-currency": currency,
        }, `day ${formatMinor(projection.budget?.day_spend_minor, currency)}`
          + ` · month ${formatMinor(projection.budget?.month_spend_minor, currency)}`
          + ` · daily limit ${formatMinor(
            projection.budget?.limits?.daily_spend_minor, currency)}`),
      ]),

      R.createElement("section", { key: "approvals", "aria-label": "Approvals" }, [
        R.createElement("h4", { key: "h" }, "Approvals"),
        R.createElement("p", {
          key: "c", "data-testid": "effective-approvals",
          "data-pending": String(projection.approvals?.pending?.length ?? 0),
          "data-expired": String(projection.approvals?.expired?.length ?? 0),
        }, `${projection.approvals?.pending?.length ?? 0} pending · `
          + `${projection.approvals?.expired?.length ?? 0} expired`),
      ]),

      R.createElement("section", { key: "memory", "aria-label": "Memory" }, [
        R.createElement("h4", { key: "h" }, "Memory scopes"),
        R.createElement("p", {
          key: "c", "data-testid": "effective-memory",
          "data-scopes": String(projection.memory?.scopes?.length ?? 0),
        }, (projection.memory?.scopes?.length ?? 0)
          ? `${projection.memory.scopes!.length} scope(s)`
          : (projection.memory?.reason || "no memory scope recorded")),
      ]),

      R.createElement("p", {
        key: "source", className: "agent-control__muted",
        "data-testid": "effective-source",
      }, `projected ${projection.generated_at} from the Business OS stores`),
    ]);
  };
}
