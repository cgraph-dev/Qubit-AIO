/* P6-02 Room Builder panel.
 *
 * Draft/version/preview/undo/apply/approve/rollback/publish controls for a
 * department floor: room and cell editing (fixed 16x12), desk add/remove,
 * style/palette/light/signage/seed controls, business/role/agent/capability/
 * memory assignment, and a visible validation/status surface.
 *
 * The panel is not mounted by the shell here; the lead wires it in. It holds
 * no second database: a publish is one injected Business OS call, and every
 * local edit is a draft. Paid art never appears in the payload.
 *
 * React and hooks come from the HOST, exactly like the other office panels.
 */
import { sakugaButton } from "./sakuga";
import {
  type BuilderRoom,
  type BuilderState,
  DEPARTMENT_ZONES,
  PALETTES,
  type PublishResult,
  ARCHETYPES,
  LIGHTING,
  addCell,
  addDesk,
  addRoom,
  approveState,
  assignRoom,
  cloneDepartment,
  createBuilderState,
  liveText,
  mergeDepartments,
  previewState,
  publishState,
  redo,
  removeCell,
  removeDesk,
  removeRoom,
  rollbackState,
  setDepartmentStyle,
  setRoomStyle,
  splitDepartment,
  undo,
} from "./room-builder";

type ReactLike = {
  createElement: (...args: unknown[]) => unknown;
};
type HooksLike = {
  useState: <T>(value: T) => [T, (next: T | ((prev: T) => T)) => void];
};

export interface RoomBuilderProps {
  initialState?: Partial<BuilderState> | null;
  /** The one Business OS publish intent. Absent keeps publish local. */
  onPublish?: (state: BuilderState) => Promise<{
    ok: boolean;
    version?: number;
    problems?: string[];
  }>;
  /** Optional hook after a successful rollback. */
  onRollback?: (version: number) => void;
}

type PublishState =
  | { kind: "idle" }
  | { kind: "publishing" }
  | { kind: "published"; version: number }
  | { kind: "error"; problems: string[] };

interface KeyEventLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  preventDefault?: () => void;
}

const FLOOR_STYLE_OPTIONS = [
  "operations", "finance_navy", "executive_warm", "creative_coral",
  "control_room_amber",
];

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function createRoomBuilder(
  R: ReactLike,
  hooks: HooksLike,
) {
  const { useState } = hooks;

  return function RoomBuilder(props: RoomBuilderProps) {
    const [state, setState] = useState<BuilderState>(
      createBuilderState(props.initialState));
    const [selectedRoomId, setSelectedRoomId] = useState<string | null>(
      state.rooms[0]?.room_id ?? null);
    const [editError, setEditError] = useState<string>("");
    const [publish, setPublish] = useState<PublishState>({ kind: "idle" });
    const [previewOpen, setPreviewOpen] = useState(false);

    const [newFloorId, setNewFloorId] = useState(
      state.floors[0]?.floor_id ?? "floor-001");
    const [newRoomId, setNewRoomId] = useState("extra-1");
    const [newRoomName, setNewRoomName] = useState("Extra Room");
    const [newRoomKind, setNewRoomKind] = useState<string>("storage");
    const [newRoomDesks, setNewRoomDesks] = useState("2");

    const [assignBusiness, setAssignBusiness] = useState("");
    const [assignDepartment, setAssignDepartment] = useState("");
    const [assignRoles, setAssignRoles] = useState("");
    const [assignAgents, setAssignAgents] = useState("");
    const [assignCapability, setAssignCapability] = useState("");
    const [assignMemory, setAssignMemory] = useState("");
    const [departmentStyle, setDepartmentStyleValue] = useState(
      state.floors[0]?.department_style ?? "operations");

    const room: BuilderRoom | null = selectedRoomId
      ? state.rooms.find((row) => row.room_id === selectedRoomId) ?? null
      : null;
    const preview = previewState(state);

    const edit = (fn: (current: BuilderState) => BuilderState) => {
      try {
        const next = fn(state);
        setState(next);
        setEditError("");
        setPublish({ kind: "idle" });
      } catch (err) {
        setEditError(messageOf(err));
      }
    };

    const selectRoom = (roomId: string) => {
      setSelectedRoomId(roomId);
      const found = state.rooms.find((row) => row.room_id === roomId);
      if (found) {
        setAssignBusiness(found.assignment.business_id);
        setAssignDepartment(found.assignment.department_id);
        setAssignRoles(found.assignment.role_ids.join(", "));
        setAssignAgents(found.assignment.agent_ids.join(", "));
        setAssignCapability(found.assignment.capability_manifest_id);
        setAssignMemory(found.assignment.memory_scope);
        setDepartmentStyleValue(found.style.department_style);
      }
    };

    const onKeyDown = (event: KeyEventLike) => {
      const key = String(event.key || "").toLowerCase();
      const modifier = Boolean(event.ctrlKey || event.metaKey);
      if (!modifier) return;
      if (key === "z") {
        event.preventDefault?.();
        edit(event.shiftKey ? redo : undo);
      } else if (key === "y") {
        event.preventDefault?.();
        edit(redo);
      }
    };

    const onPublishClick = () => {
      setPublish({ kind: "publishing" });
      void publishState(state, { onPublish: props.onPublish })
        .then(({ state: next, result }) => {
          setState(next);
          if (result.kind === "published") {
            setPublish({ kind: "published", version: result.version });
          } else {
            setPublish({ kind: "error", problems: result.problems });
          }
        })
        .catch((err: unknown) => {
          setPublish({ kind: "error", problems: [messageOf(err)] });
        });
    };

    const onRollbackClick = () => {
      if (state.published_version === null) return;
      const version = state.published_version;
      edit((current) => rollbackState(current, version));
      props.onRollback?.(version);
    };

    const styleSelect = (
      label: string, value: string, options: readonly string[],
      testId: string, onPick: (value: string) => void,
    ) => R.createElement("label", {
      key: `label-${testId}`,
      className: "room-builder__field",
    }, [
      R.createElement("span", { key: "text" }, label),
      R.createElement("select", {
        key: "select",
        "data-testid": testId,
        "aria-label": label,
        value,
        onChange: (event: { target: { value: string } }) =>
          onPick(event.target.value),
      }, options.map((option) => R.createElement(
        "option", { key: option, value: option }, option))),
    ]);

    const field = (
      label: string, value: string, testId: string,
      onChange: (value: string) => void,
    ) => R.createElement("label", {
      key: `label-${testId}`,
      className: "room-builder__field",
    }, [
      R.createElement("span", { key: "text" }, label),
      R.createElement("input", {
        key: "input",
        type: "text",
        "data-testid": testId,
        "aria-label": label,
        value,
        onChange: (event: { target: { value: string } }) =>
          onChange(event.target.value),
      }),
    ]);

    const roomButton = (row: BuilderRoom) => sakugaButton(R, {
      key: row.room_id,
      type: "button",
      "data-testid": "room-builder-room",
      "data-room": row.room_id,
      "aria-pressed": selectedRoomId === row.room_id,
      "aria-label": `Select room ${row.name} (${row.room_id})`,
      onClick: () => selectRoom(row.room_id),
    }, `${row.name} · ${row.kind} · ${row.capacity} desks`);

    const cellRow = (cell: BuilderRoom["cells"][number]) => R.createElement(
      "li", {
        key: cell.cell_id,
        "data-testid": "room-builder-cell",
        "data-cell": cell.cell_id,
      }, [
        R.createElement("span", { key: "label" },
          `${cell.cell_id} · ${cell.cols}x${cell.rows} · ${cell.desks.length} desks`),
        sakugaButton(R, {
          key: "remove",
          type: "button",
          "data-testid": "room-builder-remove-cell",
          "data-cell": cell.cell_id,
          "aria-label": `Remove cell ${cell.cell_id}`,
          disabled: !room || room.cells.length <= 1,
          onClick: () => room && edit((current) =>
            removeCell(current, room.room_id, cell.cell_id)),
        }, "Remove cell"),
      ]);

    const deskRow = (cellId: string,
                     desk: BuilderRoom["cells"][number]["desks"][number]) =>
      R.createElement("li", {
        key: desk.desk_id,
        "data-testid": "room-builder-desk",
        "data-desk": desk.desk_id,
      }, [
        R.createElement("span", { key: "label" },
          `${desk.desk_id} (${desk.x},${desk.y}) ${desk.w}x${desk.h}`),
        sakugaButton(R, {
          key: "remove",
          type: "button",
          "data-testid": "room-builder-remove-desk",
          "data-desk": desk.desk_id,
          "aria-label": `Remove desk ${desk.desk_id}`,
          onClick: () => edit((current) => removeDesk(current, desk.desk_id)),
        }, "Remove desk"),
      ]);

    const problems = state.problems.length ? state.problems : preview.problems;
    const problemsBlock = R.createElement("div", {
      key: "problems",
      "data-testid": "room-builder-problems",
      className: "room-builder__problems",
      role: problems.length ? "alert" : "status",
      "aria-live": "polite",
    }, problems.length
      ? [
        R.createElement("strong", { key: "title" },
          `${problems.length} validation problem(s)`),
        R.createElement("ul", { key: "list" }, problems.map((problem, index) =>
          R.createElement("li", {
            key: `${index}-${problem}`,
            "data-testid": "room-builder-problem",
          }, problem))),
      ]
      : "All room, door, path, capacity and asset checks pass.");

    const publishStatus = () => {
      if (publish.kind === "publishing") {
        return R.createElement("div", {
          key: "publish", "data-testid": "room-builder-publish-status",
          "data-state": "publishing", role: "status", "aria-live": "polite",
        }, "Publishing…");
      }
      if (publish.kind === "published") {
        return R.createElement("div", {
          key: "publish", "data-testid": "room-builder-publish-status",
          "data-state": "published", role: "status", "aria-live": "polite",
        }, `Published version ${publish.version}.`);
      }
      if (publish.kind === "error") {
        return R.createElement("div", {
          key: "publish", "data-testid": "room-builder-publish-status",
          "data-state": "error", role: "alert",
        }, `Publish refused: ${publish.problems.join("; ")}`);
      }
      return R.createElement("div", {
        key: "publish", "data-testid": "room-builder-publish-status",
        "data-state": "idle", role: "status", "aria-live": "polite",
      }, state.published_version === null
        ? "Not published yet."
        : `Published version ${state.published_version}.`);
    };

    return R.createElement("section", {
      className: "room-builder",
      "data-testid": "room-builder",
      "aria-label": "Room Builder",
      onKeyDown,
    }, [
      R.createElement("header", { key: "head" }, [
        R.createElement("h2", { key: "title" }, "Room Builder"),
        R.createElement("p", {
          key: "live",
          "data-testid": "room-builder-live",
          role: "status",
          "aria-live": "polite",
        }, liveText(state)),
      ]),

      R.createElement("div", { key: "toolbar", role: "toolbar",
        "aria-label": "Draft controls" }, [
        sakugaButton(R, {
          key: "preview", type: "button", "data-testid": "room-builder-preview",
          "aria-label": "Preview the draft",
          "aria-pressed": previewOpen,
          onClick: () => setPreviewOpen((open) => !open),
        }, previewOpen ? "Hide preview" : "Preview"),
        sakugaButton(R, {
          key: "undo", type: "button", "data-testid": "room-builder-undo",
          "aria-label": "Undo the last edit",
          disabled: state.history.length === 0,
          onClick: () => edit(undo),
        }, "Undo"),
        sakugaButton(R, {
          key: "redo", type: "button", "data-testid": "room-builder-redo",
          "aria-label": "Redo the last undone edit",
          disabled: state.future.length === 0,
          onClick: () => edit(redo),
        }, "Redo"),
        sakugaButton(R, {
          key: "approve", type: "button", "data-testid": "room-builder-approve",
          "aria-label": "Approve the valid draft",
          onClick: () => edit(approveState),
        }, "Approve"),
        sakugaButton(R, {
          key: "rollback", type: "button",
          "data-testid": "room-builder-rollback",
          "aria-label": "Roll back to the last published version",
          disabled: state.published_version === null,
          onClick: onRollbackClick,
        }, "Rollback"),
        sakugaButton(R, {
          key: "publish", type: "button", "data-testid": "room-builder-publish",
          "aria-label": "Publish the approved draft",
          disabled: publish.kind === "publishing",
          onClick: onPublishClick,
        }, "Publish"),
      ]),

      R.createElement("div", {
        key: "summary",
        "data-testid": "room-builder-status",
        "data-status": state.status,
        "data-problems": problems.length,
        role: "status",
        "aria-live": "polite",
      }, [
        `status ${state.status}; revision ${state.draft_revision}; `
          + `${preview.counts.floors} floors · ${preview.counts.rooms} rooms · `
          + `${preview.counts.cells} cells · ${preview.counts.desks} desks · `
          + `${preview.counts.portals} portals; digest ${preview.digest}`,
      ]),

      previewOpen ? R.createElement("div", {
        key: "preview",
        "data-testid": "room-builder-preview-panel",
        role: "region",
        "aria-label": "Draft preview",
      }, [
        R.createElement("p", { key: "counts" },
          `Valid: ${preview.valid ? "yes" : "no"} · capacity ${preview.capacity}`
          + ` · egress ${preview.egress} · digest ${preview.digest}`),
      ]) : null,

      problemsBlock,

      editError ? R.createElement("div", {
        key: "edit-error",
        "data-testid": "room-builder-error",
        role: "alert",
      }, editError) : null,

      R.createElement("div", {
        key: "rooms", role: "group", "aria-label": "Rooms",
        "data-testid": "room-builder-rooms",
      }, state.rooms.map(roomButton)),

      room ? R.createElement("div", {
        key: "room-panel",
        "data-testid": "room-builder-room-panel",
        "data-room": room.room_id,
        role: "region",
        "aria-label": `Room ${room.name}`,
      }, [
        R.createElement("h3", { key: "name" },
          `${room.name} · ${room.room_id}`),
        R.createElement("p", { key: "meta" },
          `${room.kind} · ${room.archetype} · capacity ${room.capacity}`
          + ` · cells ${room.cells.length}`),
        R.createElement("p", {
          key: "assignment-live",
          "data-testid": "room-builder-assignment",
          "aria-live": "polite",
        }, `business ${room.assignment.business_id || "none"}`
          + ` · roles ${room.assignment.role_ids.join(",") || "none"}`
          + ` · agents ${room.assignment.agent_ids.join(",") || "none"}`
          + ` · memory ${room.assignment.memory_scope || "none"}`),

        R.createElement("div", { key: "cell-actions", role: "group",
          "aria-label": "Cell and desk actions" }, [
          sakugaButton(R, {
            key: "add-cell", type: "button",
            "data-testid": "room-builder-add-cell",
            "aria-label": "Add a fixed 16 by 12 cell",
            onClick: () => edit((current) => addCell(current, room.room_id, 2)),
          }, "Add cell"),
          sakugaButton(R, {
            key: "add-desk", type: "button",
            "data-testid": "room-builder-add-desk",
            "aria-label": "Add a desk",
            onClick: () => edit((current) => addDesk(current, room.room_id)),
          }, "Add desk"),
          sakugaButton(R, {
            key: "remove-room", type: "button",
            "data-testid": "room-builder-remove-room",
            "aria-label": `Remove room ${room.name}`,
            onClick: () => edit((current) => {
              const next = removeRoom(current, room.room_id);
              setSelectedRoomId(next.rooms[0]?.room_id ?? null);
              return next;
            }),
          }, "Remove room"),
        ]),

        R.createElement("div", { key: "styles", role: "group",
          "aria-label": "Style controls" }, [
          styleSelect("Archetype", room.style.archetype, ARCHETYPES,
            "room-builder-archetype", (value) =>
              edit((current) => setRoomStyle(
                current, room.room_id, { archetype: value }))),
          styleSelect("Palette", room.style.palette, PALETTES,
            "room-builder-palette", (value) =>
              edit((current) => setRoomStyle(
                current, room.room_id, { palette: value }))),
          styleSelect("Light", room.style.lighting, LIGHTING,
            "room-builder-light", (value) =>
              edit((current) => setRoomStyle(
                current, room.room_id, { lighting: value }))),
          R.createElement("label", {
            key: "signage-label", className: "room-builder__field",
          }, [
            R.createElement("span", { key: "text" }, "Signage"),
            R.createElement("input", {
              key: "input", type: "text",
              "data-testid": "room-builder-signage",
              "aria-label": "Signage slug",
              value: room.style.signage,
              onChange: (event: { target: { value: string } }) =>
                edit((current) => setRoomStyle(
                  current, room.room_id, { signage: event.target.value })),
            }),
          ]),
          R.createElement("label", {
            key: "seed-label", className: "room-builder__field",
          }, [
            R.createElement("span", { key: "text" }, "Seed"),
            R.createElement("input", {
              key: "input", type: "number", min: "0",
              "data-testid": "room-builder-seed",
              "aria-label": "Style seed",
              value: String(room.style.seed),
              onChange: (event: { target: { value: string } }) => {
                const seed = Number(event.target.value);
                if (Number.isFinite(seed) && seed >= 0) {
                  edit((current) => setRoomStyle(
                    current, room.room_id, { seed }));
                }
              },
            }),
          ]),
          R.createElement("label", {
            key: "dept-style-label", className: "room-builder__field",
          }, [
            R.createElement("span", { key: "text" }, "Department style"),
            R.createElement("input", {
              key: "input", type: "text",
              "data-testid": "room-builder-department-style",
              "aria-label": "Department style signature",
              value: departmentStyle,
              onChange: (event: { target: { value: string } }) =>
                setDepartmentStyleValue(event.target.value),
              onBlur: () => {
                try {
                  setState(setDepartmentStyle(
                    state, room.floor_id, departmentStyle));
                  setEditError("");
                } catch (err) {
                  setEditError(messageOf(err));
                }
              },
            }),
          ]),
        ]),

        R.createElement("div", { key: "assignment", role: "group",
          "aria-label": "Room assignment" }, [
          field("Business", assignBusiness, "room-builder-business",
            setAssignBusiness),
          field("Department", assignDepartment,
            "room-builder-department", setAssignDepartment),
          field("Roles (comma separated)", assignRoles,
            "room-builder-roles", setAssignRoles),
          field("Agents (comma separated)", assignAgents,
            "room-builder-agents", setAssignAgents),
          field("Capability manifest", assignCapability,
            "room-builder-capability", setAssignCapability),
          field("Memory scope", assignMemory,
            "room-builder-memory", setAssignMemory),
          sakugaButton(R, {
            key: "assign", type: "button",
            "data-testid": "room-builder-assign",
            "aria-label": "Apply the room assignment",
            onClick: () => edit((current) => assignRoom(current, room.room_id, {
              business_id: assignBusiness,
              department_id: assignDepartment,
              role_ids: splitList(assignRoles),
              agent_ids: splitList(assignAgents),
              capability_manifest_id: assignCapability,
              memory_scope: assignMemory,
            })),
          }, "Assign"),
        ]),

        R.createElement("ul", {
          key: "cells", "data-testid": "room-builder-cells",
          "aria-label": "Cells",
        }, room.cells.map((cell) => [
          cellRow(cell),
          R.createElement("ul", {
            key: `${cell.cell_id}-desks`,
            "aria-label": `Desks in ${cell.cell_id}`,
          }, cell.desks.map((desk) => deskRow(cell.cell_id, desk))),
        ])),
      ]) : null,

      R.createElement("div", {
        key: "add-room", role: "group", "aria-label": "Create a room",
      }, [
        styleSelect("Floor", newFloorId,
          state.floors.map((floor) => floor.floor_id),
          "room-builder-new-floor", setNewFloorId),
        field("New room id", newRoomId, "room-builder-new-room-id",
          setNewRoomId),
        field("New room name", newRoomName,
          "room-builder-new-room-name", setNewRoomName),
        styleSelect("Room kind", newRoomKind, DEPARTMENT_ZONES,
          "room-builder-new-room-kind", setNewRoomKind),
        field("Desks", newRoomDesks, "room-builder-new-room-desks",
          setNewRoomDesks),
        sakugaButton(R, {
          key: "add", type: "button", "data-testid": "room-builder-add-room",
          "aria-label": "Create the room",
          onClick: () => edit((current) => addRoom(current, {
            floor_id: newFloorId,
            room_id: newRoomId,
            name: newRoomName,
            kind: newRoomKind,
            desks: Number(newRoomDesks) || 1,
          })),
        }, "Add room"),
      ]),

      R.createElement("div", {
        key: "department-actions", role: "group",
        "aria-label": "Department actions",
      }, [
        sakugaButton(R, {
          key: "clone", type: "button",
          "data-testid": "room-builder-clone",
          "aria-label": "Clone the selected department",
          onClick: () => edit((current) => cloneDepartment(
            current,
            room?.floor_id ?? current.floors[0].floor_id,
            `floor-${current.floors.length + 1}`,
          )),
        }, "Clone department"),
        sakugaButton(R, {
          key: "merge", type: "button", "data-testid": "room-builder-merge",
          "aria-label": "Merge all departments into the first",
          disabled: state.floors.length < 2,
          onClick: () => edit((current) => mergeDepartments(
            current,
            current.floors.map((floor) => floor.floor_id),
            current.floors[0].floor_id,
          )),
        }, "Merge departments"),
        sakugaButton(R, {
          key: "split", type: "button", "data-testid": "room-builder-split",
          "aria-label": "Split the selected room onto a new department floor",
          disabled: !room || state.rooms.filter(
            (row) => row.floor_id === room.floor_id).length < 2,
          onClick: () => edit((current) => splitDepartment(
            current,
            room!.floor_id,
            `floor-${current.floors.length + 1}`,
            [room!.room_id],
          )),
        }, "Split department"),
      ]),

      publishStatus(),
    ]);
  };
}

function splitList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}
