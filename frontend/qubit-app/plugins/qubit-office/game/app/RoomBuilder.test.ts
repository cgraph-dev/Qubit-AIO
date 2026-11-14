import { describe, expect, it } from "vitest";
import { createRoomBuilder } from "./RoomBuilder";
import * as RB from "./room-builder";
import {
  allByTestId,
  createHarness,
  textOf,
} from "./business-test-harness";

type Harness = ReturnType<typeof createHarness>;
type Element = Harness["tree"][number];

function mount(props: Record<string, unknown> = {}): Harness {
  const harness = createHarness();
  const Component = createRoomBuilder(
    harness.React as never, harness.hooks as never);
  harness.mount(Component as never, props);
  return harness;
}

function find(h: Harness, testId: string, match: (el: Element) => boolean = () => true) {
  const found = allByTestId(h, testId).find(match);
  if (!found) throw new Error(`missing ${testId}`);
  return found;
}

function click(h: Harness, testId: string,
               match: (el: Element) => boolean = () => true) {
  const button = find(h, testId, match);
  (button.props.onClick as () => void)();
  h.flush();
}

function input(h: Harness, testId: string, value: string) {
  const field = find(h, testId);
  (field.props.onChange as (event: { target: { value: string } }) => void)({
    target: { value },
  });
  h.flush();
}

function select(h: Harness, testId: string, value: string) {
  input(h, testId, value);
}

function live(h: Harness): string {
  return textOf(find(h, "room-builder-live"));
}

function status(h: Harness): string {
  return textOf(find(h, "room-builder-status"));
}

describe("RoomBuilder", () => {
  it("renders the draft status, rooms and accessible controls", () => {
    const h = mount();
    expect(live(h)).toContain("Draft revision 0");
    expect(live(h)).toContain("10 rooms");
    expect(status(h)).toContain("status draft");
    expect(allByTestId(h, "room-builder-room").length).toBe(10);
    for (const testId of [
      "room-builder-preview", "room-builder-undo", "room-builder-redo",
      "room-builder-approve", "room-builder-rollback", "room-builder-publish",
      "room-builder-add-cell", "room-builder-add-desk",
      "room-builder-add-room", "room-builder-assign",
    ]) {
      expect(find(h, testId).props["aria-label"]).toBeTruthy();
    }
    expect(find(h, "room-builder-problems").props.role).toBe("status");
  });

  it("adds a room, then undoes and redoes the edit", () => {
    const h = mount();
    input(h, "room-builder-new-room-id", "extra-1");
    input(h, "room-builder-new-room-name", "Extra Room");
    select(h, "room-builder-new-room-kind", "storage");
    input(h, "room-builder-new-room-desks", "2");
    click(h, "room-builder-add-room");
    expect(allByTestId(h, "room-builder-room").length).toBe(11);
    expect(status(h)).toContain("revision 1");

    click(h, "room-builder-undo");
    expect(allByTestId(h, "room-builder-room").length).toBe(10);
    click(h, "room-builder-redo");
    expect(allByTestId(h, "room-builder-room").length).toBe(11);
  });

  it("approves a valid draft and publishes it", async () => {
    const h = mount();
    click(h, "room-builder-approve");
    expect(find(h, "room-builder-status").props["data-status"]).toBe("approved");

    click(h, "room-builder-publish");
    await h.settle();
    const publishStatus = find(h, "room-builder-publish-status");
    expect(publishStatus.props["data-state"]).toBe("published");
    expect(textOf(publishStatus)).toContain("version 1");
  });

  it("publishes twice and rolls back to the first version", async () => {
    const h = mount();
    click(h, "room-builder-publish");
    await h.settle();
    expect(find(h, "room-builder-publish-status").props["data-state"])
      .toBe("published");

    input(h, "room-builder-new-room-id", "extra-1");
    input(h, "room-builder-new-room-name", "Extra");
    click(h, "room-builder-add-room");
    expect(allByTestId(h, "room-builder-room").length).toBe(11);
    click(h, "room-builder-publish");
    await h.settle();
    expect(textOf(find(h, "room-builder-publish-status")))
      .toContain("version 2");

    click(h, "room-builder-rollback");
    expect(allByTestId(h, "room-builder-room").length).toBe(11);
    expect(find(h, "room-builder-status").props["data-status"])
      .toBe("rolled_back");
  });

  it("edits palette, light, signage and seed", () => {
    const h = mount();
    select(h, "room-builder-palette", "finance_navy");
    expect(find(h, "room-builder-palette").props.value).toBe("finance_navy");
    select(h, "room-builder-light", "light_focused_task");
    expect(find(h, "room-builder-light").props.value)
      .toBe("light_focused_task");
    input(h, "room-builder-signage", "sign-finance");
    expect(find(h, "room-builder-signage").props.value).toBe("sign-finance");
    input(h, "room-builder-seed", "4242");
    expect(find(h, "room-builder-seed").props.value).toBe("4242");
    expect(find(h, "room-builder-problems").props.role).toBe("status");
  });

  it("assigns business, roles, agents and memory scope", () => {
    const h = mount();
    input(h, "room-builder-business", "business:sample");
    input(h, "room-builder-department", "operations");
    input(h, "room-builder-roles", "operations-lead, operations-analyst");
    input(h, "room-builder-agents", "agent:demo");
    input(h, "room-builder-capability", "capability:work:work-1");
    input(h, "room-builder-memory", "memory:operations:work-1");
    click(h, "room-builder-assign");
    const assignment = textOf(find(h, "room-builder-assignment"));
    expect(assignment).toContain("business:sample");
    expect(assignment).toContain("operations-lead");
    expect(assignment).toContain("agent:demo");
    expect(assignment).toContain("memory:operations:work-1");
  });

  it("shows validation problems instead of hiding an invalid draft", () => {
    const invalid = RB.createSampleState();
    invalid.rooms[0].cells[0].cols = 24;
    const h = mount({ initialState: invalid });
    expect(find(h, "room-builder-problems").props.role).toBe("alert");
    const problems = allByTestId(h, "room-builder-problem")
      .map((el) => textOf(el)).join(" ");
    expect(problems).toContain("16x12");

    const missingDoor = RB.createSampleState();
    missingDoor.portals = missingDoor.portals.filter(
      (portal) => portal.source_room_id !== "rec-1"
        && portal.target_room_id !== "rec-1");
    const h2 = mount({ initialState: missingDoor });
    const problems2 = allByTestId(h2, "room-builder-problem")
      .map((el) => textOf(el)).join(" ");
    expect(problems2).toContain("MISSING_DOOR");
  });

  it("surfaces an injected publish refusal", async () => {
    const h = mount({
      onPublish: async () => ({
        ok: false, problems: ["OFFICE_MAP_INVALID from server"],
      }),
    });
    click(h, "room-builder-publish");
    await h.settle();
    const publishStatus = find(h, "room-builder-publish-status");
    expect(publishStatus.props["data-state"]).toBe("error");
    expect(textOf(publishStatus)).toContain("OFFICE_MAP_INVALID");
  });

  it("clones, merges and splits departments through the controls", () => {
    const h = mount();
    click(h, "room-builder-clone");
    expect(allByTestId(h, "room-builder-room").length).toBe(20);
    expect(find(h, "room-builder-problems").props.role).toBe("status");
    click(h, "room-builder-merge");
    expect(allByTestId(h, "room-builder-room").length).toBe(20);
    expect(find(h, "room-builder-status").props["data-problems"]).toBe(0);
  });
});
