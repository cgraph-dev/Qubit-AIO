// @vitest-environment jsdom
/**
 * /kitchen — the intake surface for absorbing other agents/apps into Qubit.
 *
 * UI shell only (this pass): staging is page-local state, no backend. These
 * tests pin the page's shape — it renders, the two intake modes switch, and a
 * staged source produces the decomposition → integration-plan pipeline. They
 * must not assert on network calls the page does not make.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { I18nProvider } from "@/i18n";
import KitchenPage from "@/pages/KitchenPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render() {
  await act(async () => {
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/kitchen"]}>
          <KitchenPage />
        </MemoryRouter>
      </I18nProvider>,
    );
  });
}

/** Set a controlled React input's value the way a user typing would. */
function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((b) =>
    (b.textContent ?? "").includes(text),
  ) as HTMLButtonElement | undefined;
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("KitchenPage", () => {
  it("renders the Kitchen with an empty staged list", async () => {
    await render();
    expect(container.querySelector('[data-testid="kitchen-page"]')).not.toBeNull();
    expect(container.textContent).toContain("Kitchen");
    expect(container.textContent).toContain("Nothing staged yet");
    // The page is honest that intake is not wired yet.
    expect(container.textContent).toContain("not wired to a backend");
  });

  it("stages a repo and shows the integration pipeline", async () => {
    await render();
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="Repository URL"]',
    )!;
    await act(async () => {
      typeInto(input, "https://github.com/acme/widget");
    });
    const stage = buttonByText("Stage repo")!;
    expect(stage.disabled).toBe(false);
    await act(async () => {
      stage.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(
      container.querySelector('[data-testid="kitchen-staged"]'),
    ).not.toBeNull();
    // The repo name is derived from the URL's last segment.
    expect(container.textContent).toContain("widget");
    const pipeline = container.querySelector('[data-testid="kitchen-pipeline"]');
    expect(pipeline).not.toBeNull();
    expect(pipeline!.textContent).toContain("Decompose");
    expect(pipeline!.textContent).toContain("Absorb");
  });

  it("switches to folder intake", async () => {
    await render();
    const folderTab = buttonByText("Upload a folder")!;
    await act(async () => {
      folderTab.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.textContent).toContain("Drop a folder here");
    // The stage button is disabled until files are chosen.
    expect(buttonByText("Stage folder")!.disabled).toBe(true);
  });
});
