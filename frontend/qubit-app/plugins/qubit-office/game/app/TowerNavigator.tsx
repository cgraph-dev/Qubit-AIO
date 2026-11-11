/* The 100-floor tower navigator for the War Room role/floor directory.
 *
 * WHAT THIS IS
 * ------------
 * A vertical, grouped index over the REAL `directory.floors` directory payload
 * (100 floor templates, 5 wings, 22 active / 78 dormant today). It is a
 * navigation layer: selecting a floor shows the template's wing, department
 * ids, role-template ids, planned rooms/desks and expansion limits.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not a rendered 3D scene, and it never invents a floor, a room or an
 * agent. A floor is a template until a Business OS record materialises it, so
 * role ids are labelled as templates, not staff.
 *
 * React and hooks arrive as arguments, exactly as the rest of the plugin does
 * it: the host injects one React and a second copy would split the runtime.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import type { FloorTemplate } from "./business-api";

export interface TowerNavigatorProps {
  /** The directory's own floors; the source of truth for every band. */
  floors: FloorTemplate[];
  /** For tests/host: start on this floor instead of the first. */
  initialFloorId?: string | null;
  /** Called after a floor becomes the selection. */
  onSelect?: (floor: FloorTemplate) => void;
}

function wingTitle(wing: string): string {
  if (!wing) return "Unassigned wing";
  return `${wing.charAt(0).toUpperCase()}${wing.slice(1)} Wing`;
}

function stateText(active: boolean): string {
  return active ? "active" : "dormant";
}

function capText(value: number | null | undefined, unit: string): string {
  return value === null || value === undefined
    ? `no ${unit} cap`
    : `max ${value} ${unit}`;
}

function floorNumber(index: number): string {
  return String(index).padStart(3, "0");
}

export function createTowerNavigator(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function TowerNavigator(props: TowerNavigatorProps) {
    const floors = props.floors ?? [];
    const [selectedId, setSelectedId] = useState<string | null>(
      props.initialFloorId ?? floors[0]?.floor_id ?? null);
    // Refs let a keyboard move put real DOM focus on the newly selected band.
    // The map is a ref (not state) so focus never causes a render.
    const refs = useRef<Record<string, HTMLElement | null>>({});

    // Keep the selection valid if the payload changes (or arrives empty first).
    useEffect(() => {
      if (!floors.length) return;
      if (selectedId
          && floors.some((floor) => floor.floor_id === selectedId)) {
        return;
      }
      setSelectedId(floors[0].floor_id);
    }, [floors, selectedId]);

    const total = floors.length;
    const wings: string[] = [];
    for (const floor of floors) {
      if (!wings.includes(floor.wing)) wings.push(floor.wing);
    }
    const activeCount = floors.filter((floor) => floor.active_by_default).length;
    const dormantCount = total - activeCount;
    const selected = floors.find((floor) => floor.floor_id === selectedId)
      ?? null;

    const select = (floor: FloorTemplate) => {
      setSelectedId(floor.floor_id);
      props.onSelect?.(floor);
    };

    const focusFloor = (floorId: string) => {
      const node = refs.current[floorId];
      if (node && typeof node.focus === "function") node.focus();
    };

    // Roving-tabindex keyboard model: arrows/home/end move the selection (and
    // focus), Enter/Space confirm the focused floor. All keys are handled on
    // the focused option so no global listener is needed.
    const onKeyDown = (event: { key: string;
                                 preventDefault?: () => void },
                       floor: FloorTemplate) => {
      const index = floors.findIndex(
        (item) => item.floor_id === floor.floor_id);
      if (index < 0) return;
      let nextIndex = index;
      if (event.key === "ArrowDown") {
        nextIndex = Math.min(total - 1, index + 1);
      } else if (event.key === "ArrowUp") {
        nextIndex = Math.max(0, index - 1);
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = total - 1;
      } else if (event.key === "Enter" || event.key === " ") {
        nextIndex = index;
      } else {
        return;
      }
      event.preventDefault?.();
      const next = floors[nextIndex];
      select(next);
      if (event.key !== "Enter" && event.key !== " ") focusFloor(next.floor_id);
    };

    const floorBand = (floor: FloorTemplate) => {
      const isSelected = selectedId === floor.floor_id;
      const active = floor.active_by_default;
      const className = "tower__floor"
        + (active ? " is-active" : " is-dormant")
        + (isSelected ? " is-selected" : "");
      return R.createElement("div", {
        key: floor.floor_id,
        ref: (node: HTMLElement | null) => {
          refs.current[floor.floor_id] = node;
        },
        className,
        role: "option",
        tabIndex: isSelected ? 0 : -1,
        "aria-selected": isSelected ? "true" : "false",
        "aria-label": `Floor ${floor.index}, ${floor.name}, `
          + `${stateText(active)}, ${floor.room_count} rooms, `
          + `${floor.desk_capacity} desks`,
        "data-testid": "tower-floor",
        "data-floor": floor.floor_id,
        "data-wing": floor.wing,
        "data-active": active ? "true" : "false",
        "data-selected": isSelected ? "true" : "false",
        onClick: () => select(floor),
        onKeyDown: (event: { key: string; preventDefault?: () => void }) =>
          onKeyDown(event, floor),
      }, [
        R.createElement("span", {
          key: "index", className: "tower__index",
        }, floorNumber(floor.index)),
        R.createElement("span", {
          key: "name", className: "tower__floorName",
        }, floor.name),
        R.createElement("span", {
          key: "meta", className: "tower__floorMeta",
        }, `${floor.room_count} rooms · ${floor.desk_capacity} desks`),
        R.createElement("span", {
          key: "state", className: "tower__floorState",
        }, stateText(active)),
      ]);
    };

    const detailRow = (key: string, label: string, value: string,
                       testId: string) =>
      R.createElement("div", {
        key, className: "tower__detailRow",
      }, [
        R.createElement("span", {
          key: "l", className: "tower__detailLabel",
        }, label),
        R.createElement("span", {
          key: "v", className: "tower__detailValue",
          "data-testid": testId,
        }, value),
      ]);

    const detail = selected
      ? R.createElement("aside", {
          key: "detail",
          className: "tower__detail" + (selected.active_by_default
            ? " is-active" : " is-dormant"),
          "data-testid": "tower-floor-detail",
          "data-floor": selected.floor_id,
          "data-active": selected.active_by_default ? "true" : "false",
          role: "region",
          "aria-live": "polite",
          "aria-label": `Floor ${selected.index} details`,
        }, [
          R.createElement("div", {
            key: "head", className: "tower__detailHead",
          }, [
            R.createElement("h4", {
              key: "t", className: "tower__detailTitle",
              "data-testid": "tower-detail-name",
            }, `Floor ${floorNumber(selected.index)} — ${selected.name}`),
            R.createElement("span", {
              key: "s",
              className: "tower__floorState",
              "data-testid": "tower-detail-state",
            }, stateText(selected.active_by_default)),
          ]),
          R.createElement("div", {
            key: "rows", className: "tower__detailRows",
          }, [
            detailRow("wing", "Wing", wingTitle(selected.wing),
                      "tower-detail-wing"),
            detailRow(
              "departments", "Departments",
              selected.department_ids.length
                ? selected.department_ids.join(", ") : "none",
              "tower-detail-departments"),
            detailRow(
              "roles", "Role templates",
              `${selected.role_ids.length} ids — `
                + (selected.role_ids.length
                  ? selected.role_ids.join(", ") : "none"),
              "tower-detail-roles"),
            detailRow("rooms", "Planned rooms",
                      `${selected.room_count}`, "tower-detail-rooms"),
            detailRow("desks", "Planned desks",
                      `${selected.desk_capacity}`, "tower-detail-desks"),
            detailRow(
              "expansion", "Expansion",
              `${selected.expansion_allowed ? "allowed" : "locked"} · `
                + `${capText(selected.max_rooms, "rooms")} · `
                + `${capText(selected.max_agents, "agents")}`,
              "tower-detail-expansion"),
          ]),
          R.createElement("p", {
            key: "note", className: "tower__detailNote",
            "data-testid": "tower-detail-note",
          }, "Role ids are templates, not staffed agents. A floor stays a "
            + "template until a Business OS record materialises it."),
        ])
      : R.createElement("div", {
          key: "detail",
          className: "tower__detail tower__detail--empty",
          "data-testid": "tower-detail-empty",
        }, "Select a floor to read its template.");

    const body = total === 0
      ? R.createElement("div", {
          key: "empty", className: "tower__empty",
          "data-testid": "tower-empty", role: "status",
        }, "The floor directory is empty — no floor templates were returned.")
      : R.createElement("div", {
          key: "body", className: "tower__body",
        }, [
          R.createElement("div", {
            key: "scroll", className: "tower__scroll",
            role: "listbox",
            "aria-label": "Tower floor directory",
            "aria-orientation": "vertical",
          }, wings.map((wing) => {
            const group = floors.filter((floor) => floor.wing === wing);
            const wingActive = group.filter(
              (floor) => floor.active_by_default).length;
            return R.createElement("div", {
              key: wing, className: "tower__wing", role: "group",
              "aria-label": `${wingTitle(wing)} (${group.length} floors)`,
              "data-testid": "tower-wing", "data-wing": wing,
            }, [
              R.createElement("div", {
                key: "head", className: "tower__wingHead",
              }, [
                R.createElement("span", {
                  key: "n", className: "tower__wingName",
                }, wingTitle(wing)),
                R.createElement("span", {
                  key: "c", className: "tower__wingCount",
                }, `${group.length} floors · ${wingActive} active / `
                  + `${group.length - wingActive} dormant`),
              ]),
              R.createElement("div", {
                key: "stack", className: "tower__stack",
              }, group.map(floorBand)),
            ]);
          })),
          detail,
        ]);

    return R.createElement("section", {
      className: "tower",
      "data-testid": "tower-navigator",
      "data-floor-count": total,
      "data-wing-count": wings.length,
      "data-active-count": activeCount,
      "data-dormant-count": dormantCount,
      "aria-label": "Tower directory",
    }, [
      R.createElement("p", {
        key: "summary", className: "tower__summary",
        "data-testid": "tower-summary",
      }, `${total} floors · ${wings.length} wings · `
        + `${activeCount} active / ${dormantCount} dormant`),
      R.createElement("p", {
        key: "honesty", className: "tower__honesty",
        "data-testid": "tower-honesty",
      }, "Tower directory — a navigation layer over the real floor templates, "
        + "not a rendered 3D scene."),
      body,
    ]);
  };
}
