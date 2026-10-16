// @vitest-environment jsdom
/**
 * ModelPickerDialog — the empty-provider recovery path.
 *
 * With no key and no OAuth login anywhere the picker lists zero providers.
 * Upstream (and Qubit's own /env + /models pages, both real routes) offer the
 * two in-app fixes rather than a dead end. This pins the message, both
 * affordances, and the close-before-navigate contract (the dialog is a
 * full-screen portal and would otherwise keep covering the target page).
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.hoisted(() => vi.fn());

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("@nous-research/ui/ui/components/button", () => ({
  Button: ({
    children,
    onClick,
    ...rest
  }: {
    children?: ReactNode;
    onClick?: () => void;
    "aria-label"?: string;
  }) => (
    <button type="button" onClick={onClick} aria-label={rest["aria-label"]}>
      {children}
    </button>
  ),
}));
vi.mock("@nous-research/ui/ui/components/checkbox", () => ({
  Checkbox: () => null,
}));
vi.mock("@nous-research/ui/ui/components/list-item", () => ({
  ListItem: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@nous-research/ui/ui/components/spinner", () => ({
  Spinner: () => <span />,
}));
vi.mock("@nous-research/ui/ui/components/input", () => ({
  Input: (props: Record<string, unknown>) => <input {...props} />,
}));
vi.mock("@nous-research/ui/ui/components/label", () => ({
  Label: ({ children }: { children?: ReactNode }) => <label>{children}</label>,
}));
vi.mock("@/components/ConfirmDialog", () => ({
  ConfirmDialog: () => null,
}));

import { ModelPickerDialog, NO_PROVIDERS_MESSAGE } from "./ModelPickerDialog";

let container: HTMLDivElement;
let root: Root;
const onClose = vi.fn();

async function renderPicker(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

beforeEach(() => {
  onClose.mockReset();
  navigate.mockReset();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("ModelPickerDialog empty-provider recovery", () => {
  it("offers the two in-app fixes when no providers exist", async () => {
    await renderPicker(
      <ModelPickerDialog
        loader={async () => ({ providers: [] })}
        onApply={() => undefined}
        onClose={onClose}
      />,
    );

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain(NO_PROVIDERS_MESSAGE),
    );
    const labels = Array.from(document.body.querySelectorAll("button")).map(
      (b) => b.textContent,
    );
    expect(labels).toContain("Open Keys");
    expect(labels).toContain("Sign in to a provider");
  });

  it("closes the dialog then navigates to the target page", async () => {
    await renderPicker(
      <ModelPickerDialog
        loader={async () => ({ providers: [] })}
        onApply={() => undefined}
        onClose={onClose}
      />,
    );
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain("Open Keys"),
    );

    const openKeys = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === "Open Keys") as HTMLButtonElement;
    await act(async () => {
      openKeys.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onClose).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/env");
  });
});
