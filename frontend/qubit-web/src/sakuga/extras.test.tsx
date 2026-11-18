// @vitest-environment jsdom
/**
 * Qubit-owned Sakuga extras: native semantics plus the composed controls.
 *
 * The vendored scripts do not load under jsdom, so `sakuga-test-dom` installs
 * open-shadow stand-ins for the composed pieces (Button/Input/Checkbox/
 * Tooltip). The extras themselves wrap real native elements, so typing,
 * form values and file/chip behaviour are exercised against those natives.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "../pages/sakuga-test-dom";
import {
  SakugaDate,
  SakugaFile,
  SakugaFormRow,
  SakugaHoverTooltip,
  SakugaInput,
  SakugaMultiSelect,
  SakugaNumber,
  SakugaPagination,
  SakugaTextarea,
  SakugaTime,
} from "@qubit/shared/sakuga";

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

function mount(node: ReactNode): HTMLDivElement {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root!.render(<>{node}</>));
  return container;
}

const nativeSetter = Object.getOwnPropertyDescriptor(
  HTMLInputElement.prototype,
  "value",
)!.set!;
const textareaSetter = Object.getOwnPropertyDescriptor(
  HTMLTextAreaElement.prototype,
  "value",
)!.set!;

function typeInto(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  act(() => {
    if (input instanceof HTMLTextAreaElement) textareaSetter.call(input, value);
    else nativeSetter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function clickInner(host: Element | null, selector = "button") {
  const target = (host as HTMLElement | null)?.shadowRoot?.querySelector(selector) as
    | HTMLElement
    | null;
  if (!target) throw new Error(`missing inner ${selector}`);
  act(() => target.click());
}

function stepper(tree: HTMLDivElement, label: string): HTMLElement {
  const el = tree.querySelector(`sakuga-button[aria-label="${label}"]`);
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

describe("SakugaTextarea", () => {
  it("types, reports the value and submits with the form", () => {
    const onChange = vi.fn();
    const tree = mount(
      <form>
        <SakugaTextarea label="Notes" name="notes" rows={2} onChange={onChange} />
      </form>,
    );
    const textarea = tree.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();
    expect(textarea.getAttribute("rows")).toBe("2");

    typeInto(textarea, "line one\nline two");
    expect(onChange).toHaveBeenCalledWith("line one\nline two");

    const form = tree.querySelector("form") as HTMLFormElement;
    expect(new FormData(form).get("notes")).toBe("line one\nline two");
  });

  it("keeps label/name/required/maxLength as native attributes", () => {
    const tree = mount(
      <SakugaTextarea label="Summary" name="summary" required maxLength={40} defaultValue="hi" />,
    );
    const textarea = tree.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.name).toBe("summary");
    expect(textarea.required).toBe(true);
    expect(textarea.maxLength).toBe(40);
    expect(textarea.value).toBe("hi");
    expect(tree.querySelector("label")?.getAttribute("for")).toBe(textarea.id);
  });
});

describe("SakugaNumber", () => {
  it("steps with the composed buttons and emits numbers", () => {
    const onChange = vi.fn();
    const tree = mount(
      <SakugaNumber label="Retries" name="retries" min={0} max={5} value={2} onChange={onChange} />,
    );
    const input = tree.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(input.name).toBe("retries");
    expect(input.min).toBe("0");
    expect(input.max).toBe("5");

    clickInner(stepper(tree, "Increase"));
    expect(onChange).toHaveBeenLastCalledWith(3);
    expect(input.value).toBe("3");

    clickInner(stepper(tree, "Decrease"));
    expect(onChange).toHaveBeenLastCalledWith(2);
    expect(input.value).toBe("2");
  });

  it("clamps to the bounds instead of stepping past them", () => {
    const onChange = vi.fn();
    const tree = mount(
      <SakugaNumber label="Count" min={1} max={3} value={2} onChange={onChange} />,
    );
    const input = tree.querySelector('input[type="number"]') as HTMLInputElement;
    clickInner(stepper(tree, "Increase"));
    expect(onChange).toHaveBeenLastCalledWith(3);
    clickInner(stepper(tree, "Increase"));
    expect(onChange).toHaveBeenLastCalledWith(3);
    expect(input.value).toBe("3");
  });
});

describe("SakugaDate and SakugaTime", () => {
  it("change through the native input and call showPicker from the button", () => {
    const onChange = vi.fn();
    const tree = mount(
      <SakugaDate label="Date" name="when" value="2026-11-18" onChange={onChange} />,
    );
    const input = tree.querySelector('input[type="date"]') as HTMLInputElement;
    expect(input.name).toBe("when");
    typeInto(input, "2026-12-01");
    expect(onChange).toHaveBeenLastCalledWith("2026-12-01");

    const showPicker = vi.fn();
    (input as HTMLInputElement & { showPicker?: () => void }).showPicker = showPicker;
    clickInner(tree.querySelector('sakuga-button[aria-label="Open date picker"]'));
    expect(showPicker).toHaveBeenCalledTimes(1);
  });

  it("renders a time input and a time picker button", () => {
    const tree = mount(<SakugaTime label="Time" name="at" value="09:30" />);
    expect(tree.querySelector('input[type="time"]')).not.toBeNull();
    expect(tree.querySelector('sakuga-button[aria-label="Open time picker"]')).not.toBeNull();
  });
});

describe("SakugaFile", () => {
  it("shows picked files as chips and removes them", () => {
    const onFiles = vi.fn();
    const tree = mount(<SakugaFile label="Attachments" name="files" multiple onFiles={onFiles} />);
    const input = tree.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.name).toBe("files");
    expect(input.multiple).toBe(true);

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    act(() => input.dispatchEvent(new Event("change", { bubbles: true })));

    expect(onFiles).toHaveBeenLastCalledWith([file]);
    expect(tree.textContent).toContain("notes.txt");
    expect(tree.querySelector('[aria-label="Remove notes.txt"]')).not.toBeNull();

    act(() => (tree.querySelector('[aria-label="Remove notes.txt"]') as HTMLButtonElement).click());
    expect(onFiles).toHaveBeenLastCalledWith([]);
    expect(tree.textContent).not.toContain("notes.txt");
    // The native input keeps its name, so a form still submits whatever it holds.
    expect((tree.querySelector('input[type="file"]') as HTMLInputElement).name).toBe("files");
  });
});

describe("SakugaMultiSelect", () => {
  const options = [
    { value: "sessions", label: "Sessions" },
    { value: "files", label: "Files" },
    { value: "models", label: "Models" },
  ];

  it("opens, toggles checkboxes, writes hidden form values and closes on Escape", async () => {
    const onChange = vi.fn();
    const tree = mount(
      <form>
        <SakugaMultiSelect
          label="Notify on"
          name="notify"
          options={options}
          defaultValue={["sessions"]}
          onChange={onChange}
        />
      </form>,
    );
    const trigger = tree.querySelector('[role="combobox"]') as HTMLButtonElement;
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    act(() => trigger.click());
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    const checkboxes = tree.querySelectorAll('[role="dialog"] sakuga-checkbox');
    expect(checkboxes).toHaveLength(3);
    clickInner(checkboxes[1], "input");

    expect(onChange).toHaveBeenLastCalledWith(["sessions", "files"]);
    const form = tree.querySelector("form") as HTMLFormElement;
    expect(new FormData(form).getAll("notify")).toEqual(["sessions", "files"]);

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await new Promise((done) => setTimeout(done, 10));
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("filters the rows through the composed input", () => {
    const tree = mount(<SakugaMultiSelect label="Notify on" options={options} />);
    act(() => (tree.querySelector('[role="combobox"]') as HTMLButtonElement).click());
    const filter = tree.querySelector('[role="dialog"] sakuga-input') as HTMLElement;
    const input = filter.shadowRoot!.querySelector("input") as HTMLInputElement;
    typeInto(input, "mod");
    const rows = tree.querySelectorAll('[role="dialog"] sakuga-checkbox');
    expect(Array.from(rows).map((row) => row.textContent)).toEqual(["Models"]);
  });
});

describe("SakugaFormRow", () => {
  it("renders a linked label, the click-help bubble and the description", () => {
    const tree = mount(
      <SakugaFormRow
        label="Retention"
        htmlFor="row-control"
        help="How long data is kept."
        description="Nightly prune."
      >
        <SakugaInput id="row-control" label="Retention days" value="30" onChange={() => {}} />
      </SakugaFormRow>,
    );
    expect(tree.querySelector("label")?.getAttribute("for")).toBe("row-control");
    const tooltip = tree.querySelector("sakuga-tooltip");
    expect(tooltip).not.toBeNull();
    expect(tooltip!.getAttribute("text")).toBe("How long data is kept.");
    expect(tree.textContent).toContain("Nightly prune.");
  });

  it("omits the bubble when no help is supplied", () => {
    const tree = mount(
      <SakugaFormRow label="Plain">
        <span>control</span>
      </SakugaFormRow>,
    );
    expect(tree.querySelector("sakuga-tooltip")).toBeNull();
  });
});

describe("SakugaPagination", () => {
  function Harness() {
    const [page, setPage] = useState(3);
    return <SakugaPagination page={page} pageCount={12} onChange={setPage} />;
  }

  it("moves with prev/next and clamps a typed page", () => {
    const tree = mount(<Harness />);
    expect(tree.textContent).toContain("of 12");
    const nav = tree.querySelector("nav");
    expect(nav!.getAttribute("aria-label")).toBe("Pagination");

    clickInner(tree.querySelector('sakuga-button[aria-label="Next page"]'));
    const host = tree.querySelector("sakuga-input") as HTMLElement;
    expect((host.shadowRoot!.querySelector("input") as HTMLInputElement).value).toBe("4");

    clickInner(tree.querySelector('sakuga-button[aria-label="Previous page"]'));
    expect((host.shadowRoot!.querySelector("input") as HTMLInputElement).value).toBe("3");

    const input = host.shadowRoot!.querySelector("input") as HTMLInputElement;
    typeInto(input, "99");
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, composed: true }));
    });
    expect(input.value).toBe("12");
  });
});


describe("SakugaHoverTooltip", () => {
  it("opens on hover, closes on Escape and bumps on click", () => {
    const tree = mount(
      <SakugaHoverTooltip label="Help" content="Short hint">
        <button type="button">Trigger</button>
      </SakugaHoverTooltip>,
    );
    const tip = tree.querySelector(".qsk-hover-tip") as HTMLElement;
    expect(tree.querySelector('[role="tooltip"]')).toBeNull();

    act(() => {
      tip.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(tree.textContent).toContain("Short hint");
    expect(tree.querySelector(".qsk-hover-tip__label")?.textContent).toBe("Help");

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(tree.querySelector('[role="tooltip"]')).toBeNull();

    act(() => {
      tip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(tree.querySelector('[role="tooltip"]')).not.toBeNull();
  });
});
