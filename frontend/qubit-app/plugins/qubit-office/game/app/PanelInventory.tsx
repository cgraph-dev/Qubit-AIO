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

/** One readable section per declared group, in first-seen order. */
export interface PanelGroup {
  name: string;
  rows: PanelRow[];
}

export function groupPanels(panels: readonly PanelRow[]): PanelGroup[] {
  const order: string[] = [];
  const byName = new Map<string, PanelRow[]>();
  for (const panel of panels) {
    const raw = String(panel.group ?? "").trim();
    const name = raw || "General";
    let rows = byName.get(name);
    if (!rows) {
      rows = [];
      byName.set(name, rows);
      order.push(name);
    }
    rows.push(panel);
  }
  return order.map((name) => ({ name, rows: byName.get(name) ?? [] }));
}

/** Turn a machine group id (`work`, `business-os`) into a readable heading. */
export function groupTitle(name: string): string {
  const words = String(name || "General")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "General";
  return words
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
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
      R.createElement("div", {
        key: "head",
        className: "panel-inventory__rowHead",
      }, [
        R.createElement("h4", {
          key: "title",
          "data-testid": "panel-title",
          className: "panel-inventory__title",
        }, panel.title),
        R.createElement("p", {
          key: "group",
          "data-testid": "panel-group",
          className: "panel-inventory__groupTag",
        }, `${panel.group} · ${panel.panel_id}`),
      ]),
      panel.description
        ? R.createElement("p", {
          key: "description",
          "data-testid": "panel-description",
          className: "panel-inventory__description",
        }, panel.description)
        : null,
      R.createElement("p", {
        key: "authority",
        "data-testid": "panel-authority",
        className: "panel-inventory__authority",
      }, [
        R.createElement("span", {
          key: "label", className: "panel-inventory__label",
        }, "Authority "),
        `${panel.authority.module}.${panel.authority.symbol}`
          + (panel.authority.route
            ? ` · ${panel.authority.route_method ?? "GET"} ${panel.authority.route}`
            : " · module only"),
      ]),
      R.createElement("div", {
        key: "facts",
        className: "panel-inventory__facts",
      }, [
        R.createElement("div", {
          key: "permissions-fact",
          className: "panel-inventory__fact",
        }, [
          R.createElement("span", {
            key: "label", className: "panel-inventory__label",
          }, "Permissions"),
          R.createElement("ul", {
            key: "permissions",
            "data-testid": "panel-permissions",
            "aria-label": `${panel.title} permissions`,
            className: "panel-inventory__pills",
          }, [
            R.createElement("li", {
              key: "read",
              "data-testid": "panel-permission",
              "data-permission": "read",
              className: "panel-inventory__pill",
            }, `Read: ${panel.permissions.read}`),
            R.createElement("li", {
              key: "write",
              "data-testid": "panel-permission",
              "data-permission": "write",
              className: "panel-inventory__pill",
            }, `Write: ${panel.permissions.write}`),
          ]),
        ]),
        R.createElement("div", {
          key: "states-fact",
          className: "panel-inventory__fact",
        }, [
          R.createElement("span", {
            key: "label", className: "panel-inventory__label",
          }, "Required states"),
          R.createElement("ul", {
            key: "states",
            "data-testid": "panel-states",
            "aria-label": `${panel.title} required states`,
            className: "panel-inventory__pills",
          }, panel.states.map((state) => R.createElement("li", {
            key: state,
            "data-testid": "panel-state-badge",
            "data-state-badge": state,
            className: "panel-inventory__pill panel-inventory__pill--state",
          }, stateBadgeLabel(state)))),
        ]),
      ]),
      R.createElement("p", {
        key: "action",
        "data-testid": "panel-action",
        "data-action-kind": panel.action.kind,
        "data-action-code": panel.action.kind === "typed_refusal"
          ? panel.action.code
          : undefined,
        className: `panel-inventory__action panel-inventory__action--${panel.action.kind}`,
      }, [
        R.createElement("span", {
          key: "label", className: "panel-inventory__label",
        }, "Action "),
        actionSummary(panel.action),
      ]),
      R.createElement("p", {
        key: "layout",
        "data-testid": "panel-layout",
        className: "panel-inventory__muted",
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
        className: "panel-inventory__link",
      }, panel.deep_link),
    ]);

    const groups = groupPanels(panels);
    const listItems: unknown[] = [];
    for (const group of groups) {
      listItems.push(R.createElement("li", {
        key: `group-${group.name}`,
        role: "presentation",
        "data-testid": "panel-inventory-group",
        "data-group": group.name,
        className: "panel-inventory__groupHead",
      }, [
        R.createElement("h3", {
          key: "h", className: "panel-inventory__groupTitle",
        }, groupTitle(group.name)),
        R.createElement("span", {
          key: "c", className: "panel-inventory__groupCount",
          "aria-label": `${group.rows.length} panels`,
        }, String(group.rows.length)),
      ]));
      for (const panel of group.rows) listItems.push(renderRow(panel));
    }

    return R.createElement("section", {
      "data-testid": "panel-inventory",
      "data-state": "available",
      "data-panel-count": String(panels.length),
      "aria-label": "Panel inventory",
      className: "panel-inventory",
    }, [
      R.createElement("header", {
        key: "head",
        className: "panel-inventory__head",
      }, [
        R.createElement("p", {
          key: "eyebrow", className: "panel-inventory__eyebrow",
        }, "Qubit Office · Studio"),
        R.createElement("h2", {
          key: "title", className: "panel-inventory__heading",
        }, "Panel inventory"),
        R.createElement("p", {
          key: "lead", className: "panel-inventory__lead",
        }, "Every surface the office declares, grouped by the area it belongs "
          + "to. Each card names its real authority, permissions, required "
          + "states and one action."),
        R.createElement("p", {
          key: "count",
          role: "status",
          "aria-live": "polite",
          "data-testid": "panel-inventory-count",
          className: "panel-inventory__count",
        }, `${panels.length} panels declared`
          + ` · ${groups.length} ${groups.length === 1 ? "group" : "groups"}`),
      ]),
      R.createElement("ul", {
        key: "list",
        role: "list",
        "data-testid": "panel-inventory-list",
        "aria-label": "Panels",
        className: "panel-inventory__list",
      }, listItems),
    ]);
  };
}
