// @vitest-environment jsdom
// Radio-group contract for the Qubit-owned segmented control: roles, checked
// state, click selection and the roving arrow/Home/End keys.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import "../pages/sakuga-test-dom";
import { SakugaSegmented } from "./sakuga-segmented";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

async function render(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(node));
}

function radios(): HTMLElement[] {
  return Array.from(container!.querySelectorAll<HTMLElement>('[role="radio"]'));
}

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  container = null;
  root = null;
});

describe("SakugaSegmented", () => {
  it("renders a radiogroup with one checked radio", async () => {
    await render(
      <SakugaSegmented
        aria-label="Session category"
        value="chats"
        onChange={() => {}}
        options={[
          { value: "chats", label: "Chats" },
          { value: "automation", label: "Automation" },
          { value: "all", label: "All" },
        ]}
      />,
    );
    expect(container!.querySelector('[role="radiogroup"]')?.getAttribute("aria-label")).toBe(
      "Session category",
    );
    expect(radios().map((node) => node.textContent?.trim())).toEqual([
      "Chats",
      "Automation",
      "All",
    ]);
    expect(radios().map((node) => node.getAttribute("aria-checked"))).toEqual([
      "true",
      "false",
      "false",
    ]);
    expect(radios().map((node) => node.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
  });

  it("selects on click", async () => {
    const onChange = vi.fn();
    await render(
      <SakugaSegmented
        value="chats"
        onChange={onChange}
        options={[
          { value: "chats", label: "Chats" },
          { value: "all", label: "All" },
        ]}
      />,
    );
    await act(async () => radios()[1].click());
    expect(onChange).toHaveBeenCalledWith("all");
  });

  it("moves selection and focus with the arrow, Home and End keys", async () => {
    const onChange = vi.fn();
    await render(
      <SakugaSegmented
        value="chats"
        onChange={onChange}
        options={[
          { value: "chats", label: "Chats" },
          { value: "automation", label: "Automation" },
          { value: "all", label: "All" },
        ]}
      />,
    );
    await act(async () => {
      radios()[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith("automation");
    expect(document.activeElement).toBe(radios()[1]);

    await act(async () => {
      radios()[1].dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith("all");

    await act(async () => {
      radios()[2].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith("chats");

    await act(async () => {
      radios()[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith("chats");
  });
});
