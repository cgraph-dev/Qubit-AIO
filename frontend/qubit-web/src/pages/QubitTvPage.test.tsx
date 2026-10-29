// @vitest-environment jsdom
/**
 * /qubit-tv — the web Qubit TV surface.
 *
 * Web variant of the MIT HermesTV desktop plugin: subscription services open
 * in a browser window (cross-origin DRM cannot be embedded), while YouTube is
 * the only playback path embedded in the page. These tests pin the service
 * list, search/favorites (localStorage), the open call, and the embed parser.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n";
import QubitTvPage from "@/pages/QubitTvPage";
import {
  TV_FAVORITES_KEY,
  TV_SERVICES,
  parseYouTubeId,
} from "@/lib/qubit-tv";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render() {
  await act(async () => {
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/qubit-tv"]}>
          <QubitTvPage />
        </MemoryRouter>
      </I18nProvider>,
    );
  });
}

function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    (button.textContent ?? "").includes(text),
  ) as HTMLButtonElement | undefined;
}

function favorites(): unknown {
  return JSON.parse(window.localStorage.getItem(TV_FAVORITES_KEY) ?? "null");
}

beforeEach(() => {
  window.localStorage.clear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("QubitTvPage", () => {
  it("renders every upstream service", async () => {
    await render();
    expect(container.querySelector('[data-testid="qubit-tv-page"]')).not.toBeNull();
    expect(container.textContent).toContain("Qubit TV");
    for (const service of TV_SERVICES) {
      expect(
        container.querySelector(`[data-testid="qubit-tv-service-${service.id}"]`),
      ).not.toBeNull();
    }
    expect(TV_SERVICES).toHaveLength(10);
    // Honest copy: DRM services are not embedded.
    expect(container.textContent).toContain(
      "DRM-protected playback cannot be embedded",
    );
  });

  it("filters services by search", async () => {
    await render();
    const search = container.querySelector<HTMLInputElement>(
      'input[aria-label="Find a service"]',
    )!;
    await act(async () => typeInto(search, "net"));
    expect(
      container.querySelector('[data-testid="qubit-tv-service-netflix"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="qubit-tv-service-hulu"]'),
    ).toBeNull();
  });

  it("persists favorites and filters to favorites only", async () => {
    await render();
    const favoriteButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Toggle Netflix favorite"]',
    )!;
    await act(async () => {
      favoriteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(favorites()).toEqual(["netflix"]);

    await act(async () => {
      buttonByText("Show favorites")!.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(
      container.querySelector('[data-testid="qubit-tv-service-netflix"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="qubit-tv-service-hulu"]'),
    ).toBeNull();
  });

  it("opens a service in a new browser window", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await render();
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Open Netflix"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][0]).toBe("https://www.netflix.com/");
    expect(open.mock.calls[0][1]).toBe("_blank");
    expect(open.mock.calls[0][2]).toContain("noopener");
    expect(open.mock.calls[0][2]).toContain("noreferrer");
  });

  it("embeds a YouTube video from a watch URL", async () => {
    await render();
    const youtube = container.querySelector<HTMLElement>(
      '[data-testid="qubit-tv-youtube"]',
    )!;
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="YouTube URL or video ID"]',
    )!;
    await act(async () =>
      typeInto(input, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    );
    await act(async () => {
      youtube
        .querySelector("form")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const iframe = container.querySelector<HTMLIFrameElement>(
      '[data-testid="qubit-tv-embed"] iframe',
    )!;
    expect(iframe).not.toBeNull();
    expect(iframe.getAttribute("src")).toBe(
      "https://www.youtube.com/embed/dQw4w9WgXcQ",
    );
  });

  it("rejects an invalid YouTube value", async () => {
    await render();
    const youtube = container.querySelector<HTMLElement>(
      '[data-testid="qubit-tv-youtube"]',
    )!;
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="YouTube URL or video ID"]',
    )!;
    await act(async () => typeInto(input, "not a video"));
    await act(async () => {
      youtube
        .querySelector("form")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('[data-testid="qubit-tv-embed"]')).toBeNull();
    expect(
      container.querySelector('[role="alert"]')?.textContent,
    ).toContain("valid YouTube");
  });
});

describe("parseYouTubeId", () => {
  it("accepts bare ids and common URL shapes", () => {
    expect(parseYouTubeId("dQw4w9WgXcQ")).toBe("dQw4w9WgXcQ");
    expect(parseYouTubeId("https://youtu.be/dQw4w9WgXcQ")).toBe("dQw4w9WgXcQ");
    expect(
      parseYouTubeId("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"),
    ).toBe("dQw4w9WgXcQ");
    expect(
      parseYouTubeId("https://www.youtube.com/embed/dQw4w9WgXcQ"),
    ).toBe("dQw4w9WgXcQ");
    expect(
      parseYouTubeId("https://youtube.com/shorts/dQw4w9WgXcQ"),
    ).toBe("dQw4w9WgXcQ");
    expect(
      parseYouTubeId("https://music.youtube.com/watch?v=dQw4w9WgXcQ"),
    ).toBe("dQw4w9WgXcQ");
  });

  it("rejects non-YouTube values", () => {
    expect(parseYouTubeId("")).toBeNull();
    expect(parseYouTubeId("hello")).toBeNull();
    expect(parseYouTubeId("https://example.com/watch?v=dQw4w9WgXcQ")).toBeNull();
    expect(parseYouTubeId("https://youtu.be/short")).toBeNull();
  });
});
