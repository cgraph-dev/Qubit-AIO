// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MangaAwareSelect } from "./MangaAwareSelect";
import { setMangaEnabled } from "@/manga-skin/manga-store";

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function mount(onChange = vi.fn(), modal = false) {
  container = document.createElement("div");
  if (modal) container.setAttribute("aria-modal", "true");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      <MangaAwareSelect
        label="Reasoning effort"
        name="reasoning"
        options={[{ value: "off", label: "Off" }, { value: "high", label: "High" }]}
        value="off"
        onChange={onChange}
      />,
    );
  });
  return onChange;
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

beforeEach(() => {
  setMangaEnabled(true);
  document.documentElement.dataset.qubitManga = "on";
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
  document.querySelector(".qm-select-popover")?.remove();
  setMangaEnabled(false);
  delete document.documentElement.dataset.qubitManga;
});

describe("MangaAwareSelect portal", () => {
  it("renders an accessible dropdown at document.body rather than inside a clipped ancestor", () => {
    mount();
    const trigger = container!.querySelector<HTMLButtonElement>(".qm-select-trigger")!;
    act(() => trigger.click());
    const popover = document.body.querySelector<HTMLElement>(".qm-select-popover");
    expect(popover?.getAttribute("role")).toBe("dialog");
    expect(popover?.hasAttribute("data-qm-panel")).toBe(true);
    expect(trigger.getAttribute("aria-controls")).toBe(popover?.id);
    expect(container!.contains(popover)).toBe(false);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(popover?.querySelectorAll('[role="option"]')).toHaveLength(2);
  });

  it("chooses an option without moving its hidden form field", () => {
    const onChange = mount();
    act(() => container!.querySelector<HTMLButtonElement>(".qm-select-trigger")!.click());
    const choice = document.body.querySelector<HTMLButtonElement>('[role="option"]:last-child')!;
    act(() => choice.click());
    expect(onChange).toHaveBeenCalledWith("high");
    expect(document.body.querySelector(".qm-select-popover")).toBeNull();
  });

  it("closes on Escape and restores trigger focus", () => {
    mount();
    const trigger = container!.querySelector<HTMLButtonElement>(".qm-select-trigger")!;
    act(() => trigger.click());
    act(() => document.querySelector(".qm-select-filter")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.body.querySelector(".qm-select-popover")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps options and keyboard focus inside the owning modal", async () => {
    mount(vi.fn(), true);
    const trigger = container!.querySelector<HTMLButtonElement>(".qm-select-trigger")!;
    act(() => trigger.click());
    await settle();
    const filter = container!.querySelector<HTMLInputElement>(".qm-select-filter")!;
    expect(document.activeElement).toBe(filter);
    act(() => filter.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    expect(document.activeElement?.getAttribute("role")).toBe("option");
    act(() => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.activeElement).toBe(trigger);
  });

  it("follows the gate attribute instead of a stale route snapshot", async () => {
    mount();
    expect(container!.querySelector(".qm-select-trigger")).not.toBeNull();

    // Client navigation to /chat, Office, etc. removes the marker while the
    // manga theme stays selected; the select must fall back to the shared control.
    delete document.documentElement.dataset.qubitManga;
    await settle();
    expect(container!.querySelector(".qm-select-trigger")).toBeNull();
    expect(container!.querySelector('[role="combobox"]')).not.toBeNull();

    document.documentElement.dataset.qubitManga = "on";
    await settle();
    expect(container!.querySelector(".qm-select-trigger")).not.toBeNull();
  });
});
