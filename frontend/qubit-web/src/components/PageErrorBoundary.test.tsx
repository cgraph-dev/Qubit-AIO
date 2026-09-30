// @vitest-environment jsdom
/**
 * The page-level error boundary must contain a render crash — the cron
 * blueprints TypeError previously propagated to the React root and blanked the
 * whole SPA.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { PageErrorBoundary } from "./PageErrorBoundary";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Boom(): never {
  throw new TypeError("e.fields is not iterable");
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  // React logs the caught error; keep the test output clean.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.restoreAllMocks();
});

describe("PageErrorBoundary", () => {
  it("renders children when nothing throws", async () => {
    await act(async () =>
      root.render(
        <PageErrorBoundary label="Cron">
          <p>blueprints render</p>
        </PageErrorBoundary>,
      ),
    );
    expect(container.textContent).toContain("blueprints render");
    expect(
      container.querySelector('[data-testid="page-error-boundary"]'),
    ).toBeNull();
  });

  it("contains a render crash instead of blanking the tree", async () => {
    await act(async () =>
      root.render(
        <PageErrorBoundary label="Cron">
          <Boom />
        </PageErrorBoundary>,
      ),
    );
    const fallback = container.querySelector(
      '[data-testid="page-error-boundary"]',
    );
    expect(fallback).not.toBeNull();
    expect(fallback!.textContent).toContain("Cron failed to render");
    expect(fallback!.textContent).toContain("e.fields is not iterable");
  });
});
