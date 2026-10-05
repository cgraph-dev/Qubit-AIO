/**
 * Shared resilience probe for dashboard pages.
 *
 * A backend that returns an empty body, an error, or a payload missing
 * fields the page assumed were required must never blank the route or crash
 * React. This helper mounts a page under a hostile data layer and reports
 * whether the render threw or leaked raw JSON / `[object Object]`.
 *
 * The house convention (WorktreesPage.test.tsx, LogsPage.test.tsx) renders
 * the page component directly under `createRoot`, importing the page and its
 * providers *together* (dynamically) so they share one module instance. This
 * helper keeps that shape.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import type { ComponentType, ReactNode } from "react";

export interface ProbeResult {
  /** The render threw (an error boundary caught it or it propagated). */
  threw: boolean;
  /** The thrown value, if any. */
  error: unknown;
  /** Visible text of the mounted tree. */
  text: string;
  /** Raw JSON or `[object Object]` leaked into visible text. */
  rawJson: boolean;
  /** The container rendered but produced no meaningful text. */
  blank: boolean;
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Patterns that betray a value rendered as a raw object/JSON blob. */
export function leaksRawJson(text: string): boolean {
  return text.includes("[object Object]") || /^\s*\{\s*"/.test(text.trim());
}

/**
 * Mount `Page` inside the dashboard shell (i18n + router + page header
 * provider), flushing microtasks so `useEffect` data loads settle, and report
 * the first-render outcome. Render throws are captured so one probe never
 * takes down the whole suite.
 */
export async function probePage(
  Page: ComponentType,
  opts: { flushMs?: number } = {},
): Promise<{ result: ProbeResult; unmount: () => Promise<void> }> {
  // Import providers alongside so every module resolves to the same instance.
  const [{ I18nProvider }, { PageHeaderProvider }, { SystemActionsProvider }] =
    await Promise.all([
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
      import("@/contexts/SystemActions"),
    ]);

  const container = document.createElement("div");
  document.body.append(container);
  let root: Root | null = null;
  let threw = false;
  let error: unknown = null;

  const origError = console.error;
  console.error = () => {};
  try {
    root = createRoot(container);
    const ui: ReactNode = (
      <I18nProvider>
        <SystemActionsProvider>
          <MemoryRouter initialEntries={["/"]}>
            <PageHeaderProvider pluginTabs={[]}>
              <Page />
            </PageHeaderProvider>
          </MemoryRouter>
        </SystemActionsProvider>
      </I18nProvider>
    );
    await act(async () => {
      root!.render(ui);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, opts.flushMs ?? 40));
    });
  } catch (e) {
    threw = true;
    error = e;
  } finally {
    console.error = origError;
  }

  const text = container.textContent ?? "";
  const result: ProbeResult = {
    threw,
    error,
    text,
    rawJson: leaksRawJson(text),
    blank: text.trim().length === 0 && !threw,
  };

  return {
    result,
    unmount: async () => {
      if (root) {
        await act(async () => {
          root!.unmount();
        });
      }
      container.remove();
    },
  };
}
