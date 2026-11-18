// @vitest-environment jsdom
/**
 * Wrapper contract tests for the vendored controls.
 *
 * The engine's scripts do not load under jsdom, so these tests install small
 * doubles that keep the one behaviour the wrapper is responsible for: the
 * attributes it writes must be the component's real input, and a redundant
 * write must not be mistaken for a change. The toggle double mirrors the
 * upstream `seq`/`pos`/`flipping` protocol exactly, because the ON animation
 * being killed by a redundant `setAttribute` is the defect these pin.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useState, type ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { SakugaLoading, SakugaToggle } from "@qubit/shared/sakuga";

const ON_FRAMES = Array.from({ length: 43 }, (_, index) => index);
const OFF_FRAMES = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0];

class FakeToggle extends HTMLElement {
  static observedAttributes = ["checked", "disabled", "label"];

  seq: number[] = [0];
  pos = 0;
  private acc = 0;
  private flipping = false;

  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    const button = document.createElement("button");
    button.type = "button";
    button.addEventListener("click", () => this.flip());
    root.append(button);
  }

  get checked(): boolean {
    return this.hasAttribute("checked");
  }

  set checked(next: boolean) {
    this.toggleAttribute("checked", Boolean(next));
  }

  get disabled(): boolean {
    return this.hasAttribute("disabled");
  }

  set disabled(next: boolean) {
    this.toggleAttribute("disabled", Boolean(next));
  }

  attributeChangedCallback(name: string): void {
    if (name !== "checked") return;
    const on = this.checked;
    this.shadowRoot?.querySelector("button")?.setAttribute("aria-checked", String(on));
    // Upstream: a callback that was NOT produced by a user flip jumps to the
    // end instead of animating.
    if (!this.flipping) {
      this.seq = [on ? 42 : 0];
      this.pos = 0;
    }
  }

  flip(): void {
    if (this.disabled) return;
    const on = !this.checked;
    this.flipping = true;
    this.checked = on;
    this.flipping = false;
    this.seq = on ? ON_FRAMES : OFF_FRAMES;
    this.pos = 0;
    this.acc = 0;
    this.dispatchEvent(new Event("change", { bubbles: true }));
  }

  frame(dt: number): void {
    this.acc += dt * 30;
    while (this.acc >= 1 && this.pos < this.seq.length - 1) {
      this.acc -= 1;
      this.pos += 1;
    }
  }
}

class FakeProgress extends HTMLElement {
  static observedAttributes = ["value", "label"];

  goal = 0;

  // Upstream exposes a read-only getter. The wrapper must never assign to it.
  get value(): number {
    return this.goal;
  }

  attributeChangedCallback(name: string, _old: string | null, value: string | null): void {
    if (name === "value") this.goal = value === null ? 0 : Number(value);
  }
}

beforeAll(() => {
  if (!customElements.get("sakuga-toggle")) customElements.define("sakuga-toggle", FakeToggle);
  if (!customElements.get("sakuga-progress")) customElements.define("sakuga-progress", FakeProgress);
});

let container: HTMLDivElement | null = null;
let root: Root | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

function mount(node: ReactNode): HTMLDivElement {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root!.render(node));
  return container;
}

function ControlledToggle() {
  const [on, setOn] = useState(false);
  return <SakugaToggle label="Notifications" checked={on} onChange={setOn} />;
}

describe("SakugaToggle controlled sync", () => {
  it("leaves the 43-frame ON sequence intact after a real click", () => {
    const host = mount(<ControlledToggle />).querySelector("sakuga-toggle") as FakeToggle;
    const button = host.shadowRoot!.querySelector("button")!;

    act(() => button.click());

    expect(host.checked).toBe(true);
    expect(host.seq).toEqual(ON_FRAMES);
    expect(host.seq.length).toBe(43);

    act(() => {
      for (let i = 0; i < 10; i += 1) host.frame(1 / 30);
    });
    expect(host.pos).toBe(10);
  });

  it("does not rewrite an attribute whose value is already present", () => {
    const host = mount(<ControlledToggle />).querySelector("sakuga-toggle") as FakeToggle;
    act(() => host.shadowRoot!.querySelector("button")!.click());

    // A second commit with the same controlled value must be inert.
    const before = host.seq;
    act(() => root!.render(<ControlledToggle />));
    expect(host.seq).toBe(before);
  });
});

describe("SakugaLoading", () => {
  function Loading({ value }: { value: number }) {
    return <SakugaLoading label="Loading jobs" value={value} />;
  }

  it("renders the progress and caption", () => {
    const tree = mount(<Loading value={0.42} />);
    expect(tree.textContent).toContain("Loading jobs");
    const bar = tree.querySelector("sakuga-progress");
    expect(bar).not.toBeNull();
    expect(bar!.getAttribute("aria-valuenow")).toBe("42");
  });

  it("updates a read-only progress value without throwing", () => {
    const tree = mount(<Loading value={0.2} />);
    const bar = tree.querySelector("sakuga-progress") as FakeProgress;
    expect(bar.value).toBeCloseTo(0.2);

    act(() => root!.render(<Loading value={0.65} />));
    expect(bar.getAttribute("value")).toBe("0.65");
    expect(bar.value).toBeCloseTo(0.65);
  });
});
