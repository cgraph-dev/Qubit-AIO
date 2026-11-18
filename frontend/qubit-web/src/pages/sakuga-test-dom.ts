/* Minimal in-test stand-ins for the vendored Sakuga custom elements.
 *
 * The real components are plain scripts in `public/sakuga-vfx/`; Vitest's
 * jsdom environment does not load external scripts, so the elements never
 * upgrade during a test run and their shadow roots never exist. That would
 * make `typeIntoSakuga` / `clickSakuga` unusable even though production
 * upgrades normally.
 *
 * This module installs lightweight, open-shadow equivalents that expose the
 * same inner native control (button, input, checkbox, range, radio, tabs) and
 * the same host attributes the wrappers write. It is test-only: no production
 * module imports it. Never assert on the drawing here — the engine is tested
 * in the browser; these stubs exist so page tests can reach the real control.
 */

type Prop = string | boolean | number | undefined;

const COMPONENTS: Record<string, (el: HTMLElement) => void> = {
  "sakuga-button": (el) => {
    attach(el, `<button type="button"><slot></slot></button>`);
    reflect(el, "disabled", (value) => {
      const button = el.shadowRoot?.querySelector("button");
      if (button) button.disabled = value === true;
    });
  },
  "sakuga-input": (el) => {
    const host = el as HTMLElement & { value?: string };
    attach(el, `<label><span data-label></span><input /></label>`);
    reflectText(el, "label", "[data-label]");
    reflect(el, "placeholder", (value) => setAttr(inner(el, "input"), "placeholder", value));
    reflect(el, "type", (value) => setAttr(inner(el, "input"), "type", value ?? "text"));
    inner(el, "input")?.addEventListener("input", () => {
      host.value = (inner(el, "input") as HTMLInputElement).value;
      el.setAttribute("value", host.value);
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    });
    Object.defineProperty(el, "value", {
      configurable: true,
      get: () => (inner(el, "input") as HTMLInputElement | null)?.value ?? "",
      set: (next: string) => {
        const input = inner(el, "input") as HTMLInputElement | null;
        if (input && input.value !== String(next ?? "")) input.value = String(next ?? "");
      },
    });
  },
  "sakuga-toggle": (el) => boolControl(el, "switch"),
  "sakuga-checkbox": (el) => boolControl(el, "checkbox"),
  "sakuga-slider": (el) => {
    const host = el as HTMLElement & { valueAsNumber?: number };
    attach(el, `<input type="range" />`);
    reflect(el, "min", (value) => setAttr(inner(el, "input"), "min", value));
    reflect(el, "max", (value) => setAttr(inner(el, "input"), "max", value));
    reflect(el, "step", (value) => setAttr(inner(el, "input"), "step", value));
    inner(el, "input")?.addEventListener("change", () => {
      host.valueAsNumber = Number((inner(el, "input") as HTMLInputElement).value);
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    });
    Object.defineProperty(el, "valueAsNumber", {
      configurable: true,
      get: () => Number((inner(el, "input") as HTMLInputElement | null)?.value ?? 0),
      set: (next: number) => {
        const input = inner(el, "input") as HTMLInputElement | null;
        if (input) input.value = String(next);
      },
    });
  },
  "sakuga-radio": (el) => {
    const host = el as HTMLElement & { value?: string };
    el.attachShadow({ mode: "open" });
    const group = document.createElement("div");
    group.setAttribute("role", "radiogroup");
    el.shadowRoot!.append(group);
    const name = `radio-${Math.random().toString(36).slice(2)}`;
    for (const option of Array.from(el.querySelectorAll("option"))) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = option.getAttribute("value") ?? "";
      input.disabled = option.hasAttribute("disabled");
      label.append(input, option.textContent ?? input.value);
      group.append(label);
      input.addEventListener("change", () => {
        host.value = input.value;
        el.setAttribute("value", input.value);
        el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      });
    }
    Object.defineProperty(el, "value", {
      configurable: true,
      get: () =>
        (group.querySelector("input:checked") as HTMLInputElement | null)?.value ?? "",
      set: (next: string) => {
        for (const input of Array.from(group.querySelectorAll("input"))) {
          input.checked = input.value === next;
        }
      },
    });
  },
  "sakuga-tabs": (el) => {
    el.attachShadow({ mode: "open" });
    const list = document.createElement("div");
    list.setAttribute("role", "tablist");
    el.shadowRoot!.append(list);
    const options = Array.from(el.querySelectorAll("option"));
    for (const option of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.textContent ?? option.getAttribute("value");
      button.dataset.value = option.getAttribute("value") ?? "";
      button.addEventListener("click", () => {
        const detail = {
          index: options.indexOf(option),
          value: button.dataset.value,
          label: button.textContent ?? "",
        };
        el.dispatchEvent(new CustomEvent("change", { detail, bubbles: true, composed: true }));
      });
      list.append(button);
    }
  },
  "sakuga-progress": (el) => {
    attach(el, `<progress max="1"></progress>`);
    reflect(el, "value", (value) => {
      const bar = inner(el, "progress") as HTMLProgressElement | null;
      if (bar) bar.value = Number(value ?? 0);
    });
  },
  "sakuga-rating": (el) => {
    const host = el as HTMLElement & { value?: number };
    el.attachShadow({ mode: "open" });
    const group = document.createElement("div");
    el.shadowRoot!.append(group);
    for (let star = 1; star <= 5; star += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(star);
      button.dataset.value = String(star);
      button.addEventListener("click", () => {
        host.value = star;
        el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      });
      group.append(button);
    }
  },
  "sakuga-tooltip": (el) => {
    attach(el, `<button type="button"><slot></slot></button>`);
    reflect(el, "text", (value) => setAttr(inner(el, "button"), "title", value));
  },
  "sakuga-toast": (el) => {
    el.attachShadow({ mode: "open" });
    const text = document.createElement("span");
    text.dataset.toast = "1";
    el.shadowRoot!.append(text);
    Object.defineProperty(el, "show", {
      configurable: true,
      value: (message?: string) => {
        text.textContent = message ?? "";
      },
    });
  },
};

function attach(el: HTMLElement, html: string): void {
  el.attachShadow({ mode: "open" });
  el.shadowRoot!.innerHTML = html;
}

function inner(el: HTMLElement, selector: string): Element | null {
  return el.shadowRoot?.querySelector(selector) ?? null;
}

function setAttr(target: Element | null, name: string, value: Prop): void {
  if (!target) return;
  if (value === undefined || value === null || value === false) target.removeAttribute(name);
  else target.setAttribute(name, String(value));
}

function reflect(el: HTMLElement, name: string, apply: (value: Prop) => void): void {
  const key = name.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
  Object.defineProperty(el, key, {
    configurable: true,
    get: () => {
      const raw = el.getAttribute(name);
      if (raw === null) return name === "disabled" ? false : undefined;
      if (raw === "") return true;
      if (raw === "true" || raw === "false") return raw === "true";
      return raw;
    },
    set: (value: Prop) => {
      if (value === undefined || value === null || value === false) el.removeAttribute(name);
      else el.setAttribute(name, value === true ? "" : String(value));
      apply(value);
    },
  });
  const initial = el.getAttribute(name);
  apply(initial === null ? undefined : initial === "" ? true : initial);
}

function reflectText(el: HTMLElement, name: string, selector: string): void {
  reflect(el, name, (value) => {
    const target = el.shadowRoot?.querySelector(selector);
    if (target) target.textContent = value === undefined || value === null ? "" : String(value);
  });
}

function boolControl(el: HTMLElement, type: "checkbox" | "switch"): void {
  const host = el as HTMLElement & { checked?: boolean };
  attach(el, `<label><input type="checkbox" /></label>`);
  const input = () => inner(el, "input") as HTMLInputElement | null;
  input()?.setAttribute("data-kind", type);
  input()?.addEventListener("change", () => {
    host.checked = Boolean(input()?.checked);
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  });
  Object.defineProperty(el, "checked", {
    configurable: true,
    get: () => Boolean(input()?.checked),
    set: (next: boolean) => {
      const target = input();
      if (target && target.checked !== Boolean(next)) target.checked = Boolean(next);
    },
  });
}

export function installSakugaTestDom(): void {
  if (typeof window === "undefined" || !window.customElements) return;
  for (const [tag, setup] of Object.entries(COMPONENTS)) {
    if (window.customElements.get(tag)) continue;
    window.customElements.define(
      tag,
      class extends HTMLElement {
        private upgraded = false;
        connectedCallback() {
          if (this.upgraded) return;
          this.upgraded = true;
          setup(this);
        }
      },
    );
  }
}

installSakugaTestDom();
