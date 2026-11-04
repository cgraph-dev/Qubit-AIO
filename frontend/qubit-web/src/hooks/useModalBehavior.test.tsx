// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useModalBehavior } from "./useModalBehavior";
import { focusableElements, trapTabKey } from "@/lib/focus-trap";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Dialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useModalBehavior({ open, onClose });
  if (!open) return null;
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="Test dialog">
      <button type="button">first</button>
      <button type="button">last</button>
    </div>
  );
}

let container: HTMLDivElement;
let root: Root | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  container?.remove();
});

async function render(open: boolean, onClose = vi.fn()) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<Dialog open={open} onClose={onClose} />);
  });
  return { onClose, rerender: async (next: boolean) => {
    await act(async () => root!.render(<Dialog open={next} onClose={onClose} />));
  }};
}

function key(keyName: string, shiftKey = false) {
  return new KeyboardEvent("keydown", {
    key: keyName,
    shiftKey,
    bubbles: true,
    cancelable: true,
  });
}

describe("focusableElements", () => {
  it("skips disabled, hidden and aria-hidden controls", () => {
    const host = document.createElement("div");
    host.innerHTML = `
      <button>ok</button>
      <button disabled>disabled</button>
      <button aria-hidden="true">aria hidden</button>
      <button hidden>hidden</button>
      <a href="/x">link</a>
    `;
    expect(focusableElements(host).map((el) => el.textContent)).toEqual([
      "ok",
      "link",
    ]);
  });

  it("returns an empty list for a missing container", () => {
    expect(focusableElements(null)).toEqual([]);
  });
});

describe("trapTabKey", () => {
  it("wraps forward from the last element to the first", () => {
    const host = document.createElement("div");
    host.innerHTML = "<button>a</button><button>b</button>";
    document.body.append(host);
    const [first, last] = focusableElements(host);
    last.focus();
    const event = key("Tab");
    expect(trapTabKey(host, event, last)).toBe(true);
    expect(document.activeElement).toBe(first);
    host.remove();
  });

  it("wraps backward from the first element to the last", () => {
    const host = document.createElement("div");
    host.innerHTML = "<button>a</button><button>b</button>";
    document.body.append(host);
    const [first, last] = focusableElements(host);
    first.focus();
    const event = key("Tab", true);
    expect(trapTabKey(host, event, first)).toBe(true);
    expect(document.activeElement).toBe(last);
    host.remove();
  });
});

describe("useModalBehavior", () => {
  it("moves focus into the dialog and restores it after close", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "trigger";
    document.body.append(trigger);
    trigger.focus();

    const { rerender } = await render(true);
    expect((document.activeElement as HTMLElement)?.textContent).toBe("first");

    await rerender(false);
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  it("traps Tab inside the dialog", async () => {
    await render(true);
    const buttons = Array.from(document.querySelectorAll("[role=dialog] button"));
    (buttons[1] as HTMLElement).focus();

    const event = key("Tab");
    document.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(buttons[0]);
  });

  it("closes on Escape and restores body scrolling", async () => {
    const onClose = vi.fn();
    await render(true, onClose);
    expect(document.body.style.overflow).toBe("hidden");
    document.dispatchEvent(key("Escape"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
