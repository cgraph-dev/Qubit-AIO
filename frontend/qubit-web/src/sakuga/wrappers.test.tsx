// @vitest-environment jsdom
/**
 * Wrapper + gallery contract tests.
 *
 * The vendored scripts never load under jsdom; `sakuga-test-dom` installs
 * open-shadow stand-ins that keep the one thing the wrapper owns: the host
 * attributes reach the element before it upgrades, and the native inner
 * control exists. The 43-frame toggle protocol is pinned in
 * `core-controls.test.tsx`; this file covers the rest of the surface, the
 * shadow-input event promotion (a real keystroke is not composed), the
 * select composition, and the gallery page's full roster.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import "../pages/sakuga-test-dom";
import {
  SakugaButton,
  SakugaCheckbox,
  SakugaInput,
  SakugaRadio,
  SakugaRating,
  SakugaSelect,
  SakugaSlider,
  SakugaTabs,
  SakugaToggle,
  SakugaTooltip,
} from "@qubit/shared/sakuga";
import { ListItem } from "@/sakuga/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

function mount(node: React.ReactNode): HTMLDivElement {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root!.render(<MemoryRouter>{node}</MemoryRouter>));
  return container;
}

function host(container: HTMLDivElement, tag: string): HTMLElement {
  const el = container.querySelector(tag);
  expect(el).not.toBeNull();
  expect(el!.shadowRoot).not.toBeNull();
  return el as HTMLElement;
}

describe("wrapper hosts and native inner controls", () => {
  it("renders the button and keeps variant/type before upgrade", () => {
    const tree = mount(<SakugaButton variant="secondary" type="submit">Cancel</SakugaButton>);
    const el = host(tree, "sakuga-button");
    expect(el.getAttribute("variant")).toBe("secondary");
    expect(el.getAttribute("type")).toBe("submit");
    expect(el.shadowRoot!.querySelector("button")).not.toBeNull();
    expect(el.textContent).toBe("Cancel");
  });

  it("renders the toggle with its label and switch", () => {
    const tree = mount(<SakugaToggle label="Notifications" defaultChecked />);
    const el = host(tree, "sakuga-toggle");
    expect(el.getAttribute("label")).toBe("Notifications");
    expect(el.getAttribute("aria-checked")).toBe("true");
    expect(el.shadowRoot!.querySelector("button, input")).not.toBeNull();
  });

  it("renders the checkbox with a real checkbox", () => {
    const tree = mount(<SakugaCheckbox defaultChecked>Remember me</SakugaCheckbox>);
    const el = host(tree, "sakuga-checkbox");
    expect(el.shadowRoot!.querySelector("input")).not.toBeNull();
    expect(el.getAttribute("aria-checked")).toBe("true");
  });

  it("renders the slider and reports its value", () => {
    const tree = mount(<SakugaSlider label="Volume" value={30} unit="%" />);
    const el = host(tree, "sakuga-slider");
    expect(el.getAttribute("label")).toBe("Volume");
    expect(el.getAttribute("unit")).toBe("%");
    expect(el.shadowRoot!.querySelector('input[type="range"]')).not.toBeNull();
  });

  it("renders a radio group with its label and options", () => {
    const tree = mount(
      <SakugaRadio
        label="Plan"
        value="b"
        options={[
          { value: "a", label: "A" },
          { value: "b", label: "B" },
        ]}
      />,
    );
    const el = host(tree, "sakuga-radio");
    // `label` and the option set are read once by the upstream element, so the
    // wrapper must pass them as initial attributes (not a late layout write).
    expect(el.getAttribute("label")).toBe("Plan");
    expect(el.querySelectorAll("option")).toHaveLength(2);
    expect(el.shadowRoot!.querySelectorAll("input")).toHaveLength(2);
  });

  it("renders the text input with its type applied at render", () => {
    const tree = mount(
      <SakugaInput label="Email" type="email" placeholder="you@example.com" />,
    );
    const el = host(tree, "sakuga-input");
    expect(el.getAttribute("type")).toBe("email");
    expect(el.getAttribute("label")).toBe("Email");
    expect(el.getAttribute("placeholder")).toBe("you@example.com");
    const inner = el.shadowRoot!.querySelector("input") as HTMLInputElement;
    expect(inner).not.toBeNull();
    expect(inner.type).toBe("email");
  });

  it("publishes a real (uncomposed) keystroke to onChange", async () => {
    const onChange = vi.fn();
    const tree = mount(<SakugaInput label="Name" value="" onChange={onChange} />);
    const el = host(tree, "sakuga-input");
    const inner = el.shadowRoot!.querySelector("input") as HTMLInputElement;
    expect(inner).not.toBeNull();

    await act(async () => {
      inner.value = "Qubit";
      // Exactly what the vendored input dispatches: bubbles, NOT composed.
      inner.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((done) => setTimeout(done, 20));
    });

    expect(onChange).toHaveBeenCalledWith("Qubit");
  });

  it("renders tabs with their label and one button per option", () => {
    const tree = mount(
      <SakugaTabs
        label="Sections"
        options={[
          { value: "home", label: "Home" },
          { value: "profile", label: "Profile" },
        ]}
      />,
    );
    const el = host(tree, "sakuga-tabs");
    expect(el.getAttribute("label")).toBe("Sections");
    expect(el.shadowRoot!.querySelectorAll("button")).toHaveLength(2);
  });

  it("renders the five-star rating with its label", () => {
    const tree = mount(<SakugaRating label="Quality" value={3} />);
    const el = host(tree, "sakuga-rating");
    expect(el.getAttribute("label")).toBe("Quality");
    expect(el.shadowRoot!.querySelectorAll("button")).toHaveLength(5);
  });

  it("renders the tooltip host and help control", () => {
    const tree = mount(<SakugaTooltip text="Helpful hint" label="Why" />);
    const el = host(tree, "sakuga-tooltip");
    expect(el.getAttribute("text")).toBe("Helpful hint");
    expect(el.shadowRoot!.querySelector("button")).not.toBeNull();
  });
});

describe("SakugaSelect composition", () => {
  const options = [
    { value: "openai", label: "OpenAI" },
    { value: "ollama", label: "Ollama" },
    { value: "anthropic", label: "Anthropic" },
  ];

  it("opens, filters, chooses and returns the form value", async () => {
    const onChange = vi.fn();
    const tree = mount(
      <SakugaSelect label="Provider" name="provider" options={options} onChange={onChange} />,
    );
    const trigger = tree.querySelector('[role="combobox"]') as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    act(() => trigger.click());
    const dialog = tree.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    // The filter is a SakugaInput; type into its shadow input for real.
    const filterHost = dialog!.querySelector("sakuga-input") as HTMLElement;
    const filter = filterHost.shadowRoot!.querySelector("input") as HTMLInputElement;
    await act(async () => {
      filter.value = "oll";
      filter.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((done) => setTimeout(done, 30));
    });

    const radios = tree.querySelectorAll('[role="dialog"] sakuga-radio option');
    expect(Array.from(radios).map((o) => o.textContent)).toEqual(["Ollama"]);

    const choice = tree.querySelector(
      '[role="dialog"] sakuga-radio',
    ) as HTMLElement;
    act(() => (choice.shadowRoot!.querySelector("label") as HTMLElement).click());

    expect(onChange).toHaveBeenCalledWith("ollama");
    expect(trigger.textContent).toContain("Ollama");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(
      (tree.querySelector('input[type="hidden"]') as HTMLInputElement).value,
    ).toBe("ollama");
  });

  it("closes on Escape", async () => {
    const tree = mount(<SakugaSelect label="Provider" options={options} />);
    const trigger = tree.querySelector('[role="combobox"]') as HTMLButtonElement;
    act(() => trigger.click());
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await new Promise((done) => setTimeout(done, 10));
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("ListItem compatibility", () => {
  it("keeps the dense row class, active state and click handler", () => {
    const onClick = vi.fn();
    const tree = mount(
      <ListItem active onClick={onClick}>
        Sessions
      </ListItem>,
    );
    const el = host(tree, "sakuga-button");
    expect(el.classList.contains("qsk-list-item")).toBe(true);
    expect(el.classList.contains("is-active")).toBe(true);
    expect(el.getAttribute("aria-current")).toBe("true");
    expect(el.getAttribute("data-active")).toBe("true");
    act(() => (el.shadowRoot!.querySelector("button") as HTMLElement).click());
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
