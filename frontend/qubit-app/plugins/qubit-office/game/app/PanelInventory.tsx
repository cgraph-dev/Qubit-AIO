/* P6-04 panel inventory catalog surface.
 *
 * Every row shows its real data authority, read/write scope, required states,
 * one real action or typed refusal, deep link and split/stack rule. The panel
 * never invents a row: it renders exactly the parsed backend inventory, and a
 * refused or malformed read is a named error/unavailable state with a retry.
 *
 * React comes from the HOST: `R.createElement`, never an import. There is no
 * shell mount here; the Office lead mounts the factory.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type InventoryRead,
  type PanelRow,
  actionSummary,
  fetchPanelInventory,
  inventoryFromRows,
  stateBadgeLabel,
} from "./panel-inventory";

export interface PanelInventoryPanelProps {
  /** Explicit rows for a host-provided catalog. When omitted, fetch the API. */
  rows?: unknown;
  base?: string;
  fetchImpl?: typeof fetch;
}

export function createPanelInventoryPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function PanelInventoryPanel(props: PanelInventoryPanelProps) {
    const [read, setRead] = useState<InventoryRead>({ kind: "loading" });
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
      let live = true;
      if (props.rows !== undefined) {
        try {
          setRead({
            kind: "available",
            inventory: inventoryFromRows(props.rows),
          });
        } catch (err) {
          setRead({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
        return () => { live = false; };
      }
      const controller = typeof AbortController === "undefined"
        ? undefined
        : new AbortController();
      setRead({ kind: "loading" });
      fetchPanelInventory({
        base: props.base,
        fetchImpl: props.fetchImpl,
        signal: controller?.signal,
      }).then((next) => {
        if (live) setRead(next);
      });
      return () => {
        live = false;
        controller?.abort();
      };
    }, [props.rows, props.base, props.fetchImpl, attempt]);

    const retry = () => setAttempt((value) => value + 1);

    if (read.kind === "loading") {
      return R.createElement("div", {
        "data-testid": "panel-inventory",
        "data-state": "loading",
        role: "status",
        "aria-live": "polite",
        className: "panel-inventory panel-inventory--state",
      }, "Loading panel inventory…");
    }

    if (read.kind === "error" || read.kind === "unavailable") {
      const code = read.kind === "unavailable" ? read.code : "INVENTORY_ERROR";
      const detail = read.kind === "unavailable" ? read.detail : read.message;
      return R.createElement("div", {
        "data-testid": "panel-inventory",
        "data-state": read.kind,
        "data-code": code,
        role: "alert",
        className: "panel-inventory panel-inventory--state",
      }, [
        R.createElement("strong", {
          key: "title",
        }, "Panel inventory unavailable"),
        R.createElement("p", {
          key: "detail",
          "data-testid": "panel-inventory-error-detail",
        }, `${code}: ${detail}`),
        R.createElement("button", {
          key: "retry",
          type: "button",
          "data-testid": "panel-inventory-retry",
          "aria-label": "Retry loading panel inventory",
          onClick: retry,
        }, "Retry"),
      ]);
    }

    const panels = read.inventory.panels;
    if (!panels.length) {
      return R.createElement("div", {
        "data-testid": "panel-inventory",
        "data-state": "empty",
        role: "status",
        "aria-live": "polite",
        className: "panel-inventory panel-inventory--state",
      }, "No panels are declared.");
    }

    const renderRow = (panel: PanelRow) => R.createElement("li", {
      key: panel.panel_id,
      role: "listitem",
      "data-testid": "panel-row",
      "data-panel-id": panel.panel_id,
      "data-authority": `${panel.authority.module}.${panel.authority.symbol}`,
      "data-action-kind": panel.action.kind,
      "aria-label": `${panel.title} panel`,
      className: "panel-inventory__row",
    }, [
      R.createElement("h3", {
        key: "title",
        "data-testid": "panel-title",
      }, panel.title),
      R.createElement("p", {
        key: "group",
        "data-testid": "panel-group",
        className: "panel-inventory__muted",
      }, `${panel.group} · ${panel.panel_id}`),
      panel.description
        ? R.createElement("p", {
          key: "description",
          "data-testid": "panel-description",
        }, panel.description)
        : null,
      R.createElement("p", {
        key: "authority",
        "data-testid": "panel-authority",
      }, `Authority: ${panel.authority.module}.${panel.authority.symbol}`
        + (panel.authority.route
          ? ` · ${panel.authority.route_method ?? "GET"} ${panel.authority.route}`
          : " · module only")),
      R.createElement("ul", {
        key: "permissions",
        "data-testid": "panel-permissions",
        "aria-label": `${panel.title} permissions`,
      }, [
        R.createElement("li", {
          key: "read",
          "data-testid": "panel-permission",
          "data-permission": "read",
        }, `Read: ${panel.permissions.read}`),
        R.createElement("li", {
          key: "write",
          "data-testid": "panel-permission",
          "data-permission": "write",
        }, `Write: ${panel.permissions.write}`),
      ]),
      R.createElement("ul", {
        key: "states",
        "data-testid": "panel-states",
        "aria-label": `${panel.title} required states`,
      }, panel.states.map((state) => R.createElement("li", {
        key: state,
        "data-testid": "panel-state-badge",
        "data-state-badge": state,
      }, stateBadgeLabel(state)))),
      R.createElement("p", {
        key: "action",
        "data-testid": "panel-action",
        "data-action-kind": panel.action.kind,
        "data-action-code": panel.action.kind === "typed_refusal"
          ? panel.action.code
          : undefined,
      }, `Action: ${actionSummary(panel.action)}`),
      R.createElement("p", {
        key: "layout",
        "data-testid": "panel-layout",
      }, `Split: ${panel.layout.split} · Stack: ${panel.layout.stack}`),
      R.createElement("p", {
        key: "source",
        "data-testid": "panel-source",
        className: "panel-inventory__muted",
      }, `Source: ${panel.source_command}`),
      R.createElement("a", {
        key: "link",
        href: panel.deep_link,
        "data-testid": "panel-deep-link",
        "aria-label": `Open ${panel.title}`,
      }, panel.deep_link),
    ]);

    return R.createElement("section", {
      "data-testid": "panel-inventory",
      "data-state": "available",
      "data-panel-count": String(panels.length),
      "aria-label": "Panel inventory",
      className: "panel-inventory",
    }, [
      R.createElement("p", {
        key: "count",
        role: "status",
        "aria-live": "polite",
        "data-testid": "panel-inventory-count",
      }, `${panels.length} panels declared`),
      R.createElement("ul", {
        key: "list",
        role: "list",
        "data-testid": "panel-inventory-list",
        "aria-label": "Panels",
        className: "panel-inventory__list",
      }, panels.map(renderRow)),
    ]);
  };
}
