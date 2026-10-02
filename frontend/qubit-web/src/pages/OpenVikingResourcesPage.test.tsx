// @vitest-environment jsdom
/**
 * Open Viking resources page: the four resource sections render with
 * their own orientation copy (Documentation, About, SDK & API,
 * Agent Integrations).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import OpenVikingResourcesPage from "@/pages/OpenVikingResourcesPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe("OpenVikingResourcesPage", () => {
  it("renders all four resource sections", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingResourcesPage />
        </MemoryRouter>,
      );
    });
    for (const section of ["documentation", "about", "sdk-api", "agent-integrations"]) {
      expect(container.querySelector(`[data-testid="viking-resource-${section}"]`)).not.toBeNull();
    }
    expect(container.textContent).toContain("SDK & API");
    expect(container.textContent).toContain("Agent Integrations");
  });
});
