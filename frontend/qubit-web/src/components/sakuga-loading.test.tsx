// @vitest-environment jsdom
// The page-load progress surface: busy crawl by default, determinate when a
// real value exists, and the accessible status/progressbar contract.
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import "../pages/sakuga-test-dom";
import { SakugaLoading } from "@/sakuga/ui";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

async function render(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(node));
}

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  container = null;
  root = null;
});

describe("SakugaLoading", () => {
  it("exposes a status and a progressbar with the real label", async () => {
    await render(<SakugaLoading label="Loading sessions…" />);
    const status = container!.querySelector('[role="status"]');
    expect(status?.getAttribute("aria-label")).toBe("Loading sessions…");
    expect(status?.textContent).toContain("Loading sessions…");
    const bar = container!.querySelector('[role="progressbar"]');
    expect(bar).toBeTruthy();
    const value = Number(bar!.getAttribute("aria-valuenow"));
    expect(value).toBeGreaterThan(0);
    expect(value).toBeLessThan(90);
  });

  it("is determinate when given a real value", async () => {
    await render(<SakugaLoading label="Uploading" value={0.42} />);
    expect(
      container!.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow"),
    ).toBe("42");
  });

  it("can hide the caption without losing the accessible name", async () => {
    await render(<SakugaLoading label="Working" showLabel={false} />);
    expect(container!.querySelector(".qsk-loading__label")).toBeNull();
    expect(container!.querySelector('[role="status"]')?.getAttribute("aria-label")).toBe(
      "Working",
    );
  });
});
