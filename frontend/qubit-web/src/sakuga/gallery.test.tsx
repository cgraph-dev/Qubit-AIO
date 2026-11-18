// @vitest-environment jsdom
/**
 * The hidden /sakuga-gallery surface: every vendored control and every Qubit
 * extra must be represented in both the React page and the standalone copy
 * that loads the vendored scripts directly.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import "../pages/sakuga-test-dom";
import SakugaGalleryPage from "@/pages/SakugaGalleryPage";
import { ROUTE_TITLES } from "@/lib/resolve-page-title";

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

describe("Sakuga gallery", () => {
  it("is a mounted hidden route with a title", () => {
    expect(ROUTE_TITLES["/sakuga-gallery"]).toBe("Sakuga Gallery");
  });

  it("renders every upstream control and every Qubit extra", () => {
    const tree = mount(<SakugaGalleryPage />);
    const titles = Array.from(tree.querySelectorAll(".skg-card__title")).map(
      (el) => el.textContent ?? "",
    );
    const expected = [
      "Primary button",
      "Secondary button",
      "Toggle",
      "Checkbox",
      "Slider",
      "Text input",
      "Radio",
      "Rating",
      "Tabs",
      "Progress",
      "SakugaLoading (busy)",
      "SakugaLoading (determinate)",
      "Toast",
      "Tooltip",
      "SakugaSelect",
      "ListItem rows",
      "Disabled states",
      "Effect bus (emitFx)",
    ];
    expect(titles).toHaveLength(expected.length);
    for (const title of expected) {
      expect(titles.some((t) => t.startsWith(title))).toBe(true);
    }
    // Every vendored host is present and every upstream tag is represented.
    for (const tag of [
      "sakuga-button",
      "sakuga-toggle",
      "sakuga-checkbox",
      "sakuga-slider",
      "sakuga-input",
      "sakuga-radio",
      "sakuga-rating",
      "sakuga-tabs",
      "sakuga-progress",
      "sakuga-toast",
      "sakuga-tooltip",
    ]) {
      expect(tree.querySelector(tag)).not.toBeNull();
    }
    // Qubit extras.
    expect(tree.querySelector('[role="combobox"]')).not.toBeNull();
    expect(tree.querySelector(".qsk-list-item")).not.toBeNull();
    expect(tree.querySelector(".qsk-loading")).not.toBeNull();
    expect(tree.textContent).toContain("emitFx");
  });

  it("ships a standalone copy that loads all eleven vendored scripts", () => {
    const html = readFileSync(
      resolve(__dirname, "../../public/sakuga-gallery.html"),
      "utf8",
    );
    for (const name of [
      "button",
      "toggle",
      "slider",
      "checkbox",
      "radio",
      "input",
      "tabs",
      "progress",
      "rating",
      "toast",
      "tooltip",
    ]) {
      expect(html).toContain(`<script src="/sakuga-vfx/${name}/${name}.js"></script>`);
    }
    expect(html).toContain("/sakuga-gallery");
  });
});
