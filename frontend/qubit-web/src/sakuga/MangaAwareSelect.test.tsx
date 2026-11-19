// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MangaAwareSelect } from "./MangaAwareSelect";
import { setMangaEnabled } from "@/manga-skin/manga-store";

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function mount(onChange = vi.fn()) {
  container = document.createElement("div");
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
    const combobox = container!.querySelector<HTMLButtonElement>('[role="combobox"]')!;
    act(() => combobox.click());
    const popover = document.body.querySelector<HTMLElement>(".qm-select-popover");
    expect(popover).not.toBeNull();
    expect(container!.contains(popover)).toBe(false);
    expect(combobox.getAttribute("aria-expanded")).toBe("true");
    expect(popover?.querySelectorAll('[role="option"]')).toHaveLength(2);
  });

  it("chooses an option without moving its hidden form field", () => {
    const onChange = mount();
    act(() => container!.querySelector<HTMLButtonElement>('[role="combobox"]')!.click());
    const choice = document.body.querySelector<HTMLButtonElement>('[role="option"]:last-child')!;
    act(() => choice.click());
    expect(onChange).toHaveBeenCalledWith("high");
    expect(document.body.querySelector(".qm-select-popover")).toBeNull();
  });

  it("closes on Escape and restores trigger focus", () => {
    mount();
    const trigger = container!.querySelector<HTMLButtonElement>('[role="combobox"]')!;
    act(() => trigger.click());
    act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.body.querySelector(".qm-select-popover")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
