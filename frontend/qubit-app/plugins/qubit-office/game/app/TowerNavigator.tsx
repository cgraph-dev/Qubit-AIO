/* The five-floor tower navigator for the War Room role/floor directory.
 *
 * WHAT THIS IS
 * ------------
 * A vertical, grouped index over the REAL `directory.floors` directory payload
 * (5 floor templates, 5 wings, 5 active today). It is a
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
import { sakugaButton } from "./sakuga";
import type { OfficeGameShellProps } from "../OfficeGameShell";
import type { FloorTemplate } from "./business-api";
import {
  type WorldCell, type WorldFloor, type WorldOccupant, type WorldRoom,
  type WorldTower, templateFloorScene,
} from "./world-tower";

export interface TowerNavigatorProps {
  /** The directory's own floors; the source of truth for every band. */
  floors: FloorTemplate[];
  /** Optional endpoint scene. Absent/blank floors fall back to templates. */
  world?: WorldTower | null;
  /** For tests/host: start on this floor instead of the first. */
  initialFloorId?: string | null;
  /** Called after a floor becomes the selection. */
  onSelect?: (floor: FloorTemplate) => void;
  /** Called when the detail is closed. */
  onClose?: () => void;
  /**
   * Open one of the real live floors in the animated renderer. The directory
   * itself only has templates; the real rooms/characters live in the office
   * store's floors, so this is the bridge from a floor to what is actually
   * staffed. Scopes are ``live:<number>``.
   */
  onOpenLiveFloor?: (scope: string) => void;
  /**
   * Open the selected TEMPLATE floor in the animated renderer. It renders the
   * floor's rooms and furniture with no agents, which is the honest state of a
   * floor that has no Business OS occupants.
   */
  onOpenFloor?: (floorId: string) => void;
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

function occupiedSlots(room: WorldRoom): number {
  let occupied = 0;
  for (const cell of room.cells) {
    for (const slot of cell.desk_slots) {
      if (slot.occupied_by) occupied += 1;
    }
  }
  return occupied;
}

function roomOccupants(room: WorldRoom): WorldOccupant[] {
  const seen = new Set<string>();
  const people: WorldOccupant[] = [];
  for (const cell of room.cells) {
    for (const slot of cell.desk_slots) {
      const who = slot.occupied_by;
      if (!who) continue;
      const key = who.agent_id || who.name;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      people.push(who);
    }
  }
  return people;
}

function cellStyle(cell: WorldCell) {
  return {
    aspectRatio: `${cell.cols} / ${cell.rows}`,
  };
}

function deskStyle(slot: WorldCell["desk_slots"][number], cell: WorldCell) {
  const width = Math.max(1, slot.footprint.w) / Math.max(1, cell.cols);
  const height = Math.max(1, slot.footprint.h) / Math.max(1, cell.rows);
  return {
    left: `${(slot.tile.x / Math.max(1, cell.cols)) * 100}%`,
    top: `${(slot.tile.y / Math.max(1, cell.rows)) * 100}%`,
    width: `${width * 100}%`,
    height: `${height * 100}%`,
  };
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
    // True once the directory has produced a floor. An empty first payload
    // still selects a floor when it arrives; a user-closed detail does not
    // reopen itself on the next payload refresh.
    const hadFloors = useRef(floors.length > 0);

    // Keep the selection valid if the payload changes (or arrives empty first).
    useEffect(() => {
      const arrived = !hadFloors.current && floors.length > 0;
      hadFloors.current = floors.length > 0;
      if (!floors.length) return;
      if (!arrived && selectedId === null) return;
      if (selectedId
          && floors.some((floor) => floor.floor_id === selectedId)) {
        return;
      }
      setSelectedId(props.initialFloorId ?? floors[0].floor_id);
    }, [floors, selectedId, props.initialFloorId]);

    const total = floors.length;
    const wings: string[] = [];
    for (const floor of floors) {
      if (!wings.includes(floor.wing)) wings.push(floor.wing);
    }
    const activeCount = floors.filter((floor) => floor.active_by_default).length;
    const dormantCount = total - activeCount;
    const selected = floors.find((floor) => floor.floor_id === selectedId)
      ?? null;
    const world = props.world ?? null;
    const worldFloor = selected && world
      ? world.floors.find((floor) => floor.floor_id === selected.floor_id)
        ?? null
      : null;
    const scene: WorldFloor | null = selected
      ? (worldFloor ?? templateFloorScene(selected))
      : null;
    const sceneSource = worldFloor ? (world?.source ?? "endpoint") : "template";

    const select = (floor: FloorTemplate) => {
      setSelectedId(floor.floor_id);
      props.onSelect?.(floor);
    };

    const closeDetail = () => {
      setSelectedId(null);
      props.onClose?.();
    };

    const focusFloor = (floorId: string) => {
      const node = refs.current[floorId];
      if (node && typeof node.focus === "function") node.focus();
    };

    // Roving-tabindex keyboard model: arrow keys plus Home and End move the selection (and
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
      } else if (event.key === "Escape") {
        event.preventDefault?.();
        closeDetail();
        return;
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
        tabIndex: isSelected || (selectedId === null
          && floor.floor_id === floors[0]?.floor_id) ? 0 : -1,
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

    const cellCard = (room: WorldRoom, cell: WorldCell) =>
      R.createElement("div", {
        key: cell.cell_id,
        className: "tower__cell",
        style: cellStyle(cell),
        role: "group",
        "aria-label": `${room.name} ${cell.cell_id}, ${cell.cols}x${cell.rows}, `
          + `${cell.desk_slots.length} desk slots`,
        "data-testid": "tower-cell",
        "data-cell": cell.cell_id,
        "data-cols": cell.cols,
        "data-rows": cell.rows,
      }, [
        R.createElement("span", {
          key: "label", className: "tower__cellLabel",
        }, cell.cell_id.toUpperCase()),
        ...cell.desk_slots.map((slot) => R.createElement("span", {
          key: slot.desk_id,
          className: "tower__desk" + (slot.occupied_by ? " is-occupied" : ""),
          style: deskStyle(slot, cell),
          "data-testid": "tower-desk",
          "data-desk": slot.desk_id,
          "data-seat": slot.seat_id ?? "",
          "data-occupied": slot.occupied_by ? "true" : "false",
          "data-occupant-id": slot.occupied_by?.agent_id ?? "",
          "data-placement-source": slot.occupied_by?.source ?? "",
          title: slot.occupied_by
            ? `Desk ${slot.desk_id} — ${slot.occupied_by.name}`
              + ` (${slot.occupied_by.source})`
            : `Desk ${slot.desk_id} — empty`,
          "aria-label": slot.occupied_by
            ? `Desk ${slot.desk_id}, occupied by ${slot.occupied_by.name}`
            : `Desk ${slot.desk_id}, empty`,
        }, slot.occupied_by
          ? R.createElement("span", {
              key: "who", className: "tower__deskInitial",
            }, slot.occupied_by.emoji || slot.occupied_by.initials)
          : null)),
      ]);

    const roomCard = (room: WorldRoom) => {
      const occupied = occupiedSlots(room);
      return R.createElement("article", {
        key: room.room_id,
        className: "tower__room" + (room.active ? " is-active" : " is-dormant"),
        "data-testid": "tower-room",
        "data-room": room.room_id,
        "data-kind": room.kind,
        "data-archetype": room.archetype,
        "data-active": room.active ? "true" : "false",
        "data-template": room.template ? "true" : "false",
      }, [
        R.createElement("div", {
          key: "head", className: "tower__roomHead",
        }, [
          R.createElement("span", {
            key: "name", className: "tower__roomName",
          }, room.name),
          R.createElement("span", {
            key: "arch", className: "tower__roomArchetype",
          }, `${room.archetype} · ${room.kind}`),
          R.createElement("span", {
            key: "state", className: "tower__floorState",
          }, stateText(room.active)),
        ]),
        R.createElement("div", {
          key: "meta", className: "tower__roomMeta",
        }, [
          R.createElement("span", {
            key: "cap", "data-testid": "tower-room-capacity",
          }, `${room.capacity} desks`),
          R.createElement("span", { key: "cells" },
            `${room.cell_count} cells`),
          R.createElement("span", {
            key: "occ", "data-testid": "tower-room-occupancy",
          }, `${occupied} staffed · ${Math.max(0, room.capacity - occupied)} `
            + "empty"),
        ]),
        roomOccupants(room).length
          ? R.createElement("div", {
              key: "people", className: "tower__occupants",
              "data-testid": "tower-room-occupants",
            }, roomOccupants(room).map((who) => R.createElement("span", {
              key: who.agent_id || who.name,
              className: "tower__occupant",
              "data-testid": "tower-occupant",
              "data-agent": who.agent_id,
              "data-source": who.source,
              title: `${who.name} — ${who.source}`,
            }, [
              R.createElement("span", {
                key: "i", className: "tower__occupantInitial",
                "aria-hidden": "true",
              }, who.initials),
              who.emoji
                ? R.createElement("span", {
                    key: "e", className: "tower__occupantEmoji",
                    "aria-hidden": "true",
                  }, who.emoji)
                : null,
              R.createElement("span", {
                key: "n", className: "tower__occupantName",
              }, who.name),
            ])))
          : null,
        R.createElement("div", {
          key: "cells", className: "tower__cells",
        }, room.cells.map((cell) => cellCard(room, cell))),
      ]);
    };

    const roomScene = (floor: WorldFloor) => {
      const occupied = floor.rooms.reduce(
        (sum, room) => sum + occupiedSlots(room), 0);
      return R.createElement("section", {
        key: "scene",
        className: "tower__scene",
        "data-testid": "tower-room-scene",
        "data-floor": floor.floor_id,
        "data-source": sceneSource,
        "data-room-count": floor.rooms.length,
        "data-cell-count": floor.cells,
        "data-occupied": occupied > 0 ? "true" : "false",
        "aria-label": `Room scene for floor ${floor.index}`,
      }, [
        R.createElement("div", {
          key: "head", className: "tower__sceneHead",
        }, [
          R.createElement("span", {
            key: "title", className: "tower__sceneTitle",
            "data-testid": "tower-scene-title",
          }, `Floor ${floorNumber(floor.index)} · ${wingTitle(floor.wing)}`),
          R.createElement("span", {
            key: "cap", className: "tower__sceneMeta",
            "data-testid": "tower-scene-capacity",
          }, `${floor.rooms.length} rooms · ${floor.cells} cells · `
            + `${floor.desk_capacity} desks`),
          R.createElement("span", {
            key: "source", className: "tower__sceneSource",
            "data-testid": "tower-scene-source",
          }, sceneSource === "endpoint" ? "endpoint rooms" : "template rooms"),
          props.onOpenFloor
            ? sakugaButton(R, {
                key: "open", type: "button",
                className: "tower__liveFloor",
                "data-testid": "tower-open-floor",
                onClick: () => props.onOpenFloor?.(floor.floor_id),
              }, "Open floor scene")
            : null,
        ]),
        floor.rooms.length
          ? R.createElement("div", {
              key: "rooms", className: "tower__rooms",
              "data-testid": "tower-room-grid",
            }, floor.rooms.map(roomCard))
          : R.createElement("div", {
              key: "empty", className: "tower__emptyRooms",
              "data-testid": "tower-scene-empty", role: "status",
            }, "This floor has no rooms planned."),
        R.createElement("p", {
          key: "note", className: "tower__sceneNote",
          "data-testid": "tower-scene-note",
        }, world?.occupancy
          ? "Occupancy comes only from explicit Business OS links; empty "
            + "desks stay empty and unplaced agents are listed below with "
            + "their reason. A room-scene projection, not a rendered 3D world."
          : "Template rooms, not staffed agents. Empty desks stay empty until "
            + "a real Business OS presence is linked."),
      ]);
    };

    const occupancy = world?.occupancy ?? null;
    const unplaced = occupancy?.unplaced ?? [];
    const shownUnplaced = unplaced.slice(0, 12);
    const occupancySection = occupancy
      ? R.createElement("section", {
          key: "occupancy",
          className: "tower__occupancy",
          "data-testid": "tower-occupancy",
          "data-agents-source": occupancy.agents_source ?? "",
          "data-seat-source": occupancy.seat_source ?? "",
        }, [
          R.createElement("p", {
            key: "summary", className: "tower__occupancySummary",
            "data-testid": "tower-occupancy-summary",
          }, `${occupancy.counts.placed ?? 0} staffed · `
            + `${occupancy.counts.unplaced ?? 0} unplaced · `
            + `${occupancy.counts.occupied_desks ?? 0} occupied desks`),
          unplaced.length
            ? R.createElement("div", {
                key: "list", className: "tower__unplaced",
                "data-testid": "tower-unplaced",
              }, [
                R.createElement("span", {
                  key: "title", className: "tower__unplacedTitle",
                }, "Unplaced agents"),
                ...shownUnplaced.map((who) => R.createElement("span", {
                  key: who.agent_id,
                  className: "tower__unplacedAgent",
                  "data-testid": "tower-unplaced-agent",
                  "data-agent": who.agent_id,
                  "data-reason": who.reason,
                  title: who.placement_detail || who.reason,
                }, [
                  R.createElement("span", {
                    key: "n", className: "tower__unplacedName",
                  }, who.name),
                  R.createElement("span", {
                    key: "r", className: "tower__unplacedReason",
                  }, who.reason),
                ])),
                unplaced.length > shownUnplaced.length
                  ? R.createElement("span", {
                      key: "more", className: "tower__unplacedMore",
                      "data-testid": "tower-unplaced-more",
                    }, `+${unplaced.length - shownUnplaced.length} more`)
                  : null,
              ])
            : R.createElement("p", {
                key: "none", className: "tower__unplacedNone",
                "data-testid": "tower-unplaced-none",
              }, "Every Business OS agent has an explicit tower desk."),
        ])
      : null;

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
            sakugaButton(R, {
              key: "close", type: "button",
              className: "tower__close",
              "data-testid": "tower-detail-close",
              "aria-label": "Close floor detail",
              onClick: closeDetail,
            }, "Close"),
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
              "scene", "Room scene",
              `${scene?.rooms.length ?? 0} rooms · ${scene?.cells ?? 0} cells`
                + ` · ${scene?.desk_capacity ?? 0} desks`,
              "tower-detail-scene"),
            detailRow(
              "expansion", "Expansion",
              `${selected.expansion_allowed ? "allowed" : "locked"} · `
                + `${capText(selected.max_rooms, "rooms")} · `
                + `${capText(selected.max_agents, "agents")}`,
              "tower-detail-expansion"),
          ]),
          scene ? roomScene(scene) : null,
          occupancySection,
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
      }, "Tower directory and room scenes — a navigation layer over the real "
        + "floor templates, not a rendered 3D scene. Use a live floor to see "
        + "the animated office with its real characters; template floors have "
        + "no staff."),
      props.onOpenLiveFloor
        ? R.createElement("div", {
            key: "liveFloors", className: "tower__liveFloors",
            "data-testid": "tower-live-floors",
          }, [
            R.createElement("span", {
              key: "label", className: "tower__liveLabel",
            }, "Live office floors"),
            sakugaButton(R, {
              key: "f1", type: "button", className: "tower__liveFloor",
              "data-testid": "tower-open-live-1",
              onClick: () => props.onOpenLiveFloor?.("live:1"),
            }, "1 · Executive"),
            sakugaButton(R, {
              key: "f2", type: "button", className: "tower__liveFloor",
              "data-testid": "tower-open-live-2",
              onClick: () => props.onOpenLiveFloor?.("live:2"),
            }, "2 · Operations"),
            sakugaButton(R, {
              key: "f3", type: "button", className: "tower__liveFloor",
              "data-testid": "tower-open-live-3",
              onClick: () => props.onOpenLiveFloor?.("live:3"),
            }, "3 · Review"),
          ])
        : null,
      body,
    ]);
  };
}
