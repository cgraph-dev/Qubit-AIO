// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/sakuga/ui", () => ({
  Button: ({ children, variant: _variant, fx: _fx, ...props }: {
    children?: React.ReactNode;
    variant?: string;fx?: unknown;
    [key: string]: unknown;
  }) => <button {...props}>{children}</button>,
}));
vi.mock("@/hooks/useModalBehavior", () => ({
  useModalBehavior: () => ({ current: null }),
}));

import { ConfirmDialog } from "./ConfirmDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLElement;
let root: Root;
beforeEach(() => {
 host=document.createElement("div");
 document.body.appendChild(host);
 root=createRoot(host);
});
afterEach(async () => {
 await act(async () => {root.unmount();});
 host.remove();
 document.querySelector('[role="dialog"]')?.remove();
});

describe("unified Manga confirmation dialog", () => {
 it("does not render while closed", async () => {
  await act(async () => root.render(
   <ConfirmDialog open={false} title="Restart gateway?" onCancel={()=>{}} onConfirm={()=>{}} />,
  ));
  expect(document.querySelector(".qm-confirm-dialog")).toBeNull();
 });
 it("preserves the accessible title, confirmation and cancellation behavior", async () => {
  const onConfirm=vi.fn();
  const onCancel=vi.fn();
  await act(async () => root.render(
   <ConfirmDialog open title="Restart gateway?" description="Active sessions will reconnect"
    confirmLabel="Restart Gateway" onCancel={onCancel} onConfirm={onConfirm}/>,
  ));
  const dialog=document.querySelector('[role="dialog"]')!;
  expect(dialog).not.toBeNull();
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(dialog.querySelector(".qm-confirm-dialog__title")?.textContent).toContain("Restart gateway?");
  expect(dialog.querySelector(".qm-confirm-dialog__description")?.textContent).toContain("reconnect");
  await act(async ()=>{(dialog.querySelector("[data-confirm]") as HTMLButtonElement).click();});
  expect(onConfirm).toHaveBeenCalledTimes(1);
  await act(async ()=>{(dialog.querySelector(".qm-confirm-dialog__cancel") as HTMLButtonElement).click();});
  expect(onCancel).toHaveBeenCalledTimes(1);
 });
});
