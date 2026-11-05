// @vitest-environment jsdom
/**
 * The dashboard ships one translation dictionary in the initial bundle and
 * loads the other locales on demand. These tests pin both halves: English is
 * present synchronously, and a saved/selected non-English locale still yields
 * the same strings before paint (preloaded) or after a switch (lazy chunk).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  I18nProvider,
  getInitialLocale,
  loadTranslations,
  useI18n,
} from "@/i18n";

let container: HTMLDivElement;
let root: Root;
let ctx: ReturnType<typeof useI18n>;

function Probe() {
  ctx = useI18n();
  return null;
}

async function render(props: {
  initialLocale?: Parameters<typeof I18nProvider>[0]["initialLocale"];
  initialTranslations?: Parameters<typeof I18nProvider>[0]["initialTranslations"];
} = {}) {
  await act(async () => {
    root.render(
      <I18nProvider {...props}>
        <Probe />
      </I18nProvider>,
    );
  });
}

async function flush() {
  for (let i = 0; i < 20; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.lang = "";
  document.documentElement.dir = "";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("I18nProvider", () => {
  it("renders English synchronously without loading another dictionary", async () => {
    await render();

    expect(ctx.locale).toBe("en");
    expect(ctx.t.common.save).toBe("Save");
  });

  it("honours a dictionary preloaded by the entry point", async () => {
    const de = await loadTranslations("de");
    await render({ initialLocale: "de", initialTranslations: de });

    expect(ctx.locale).toBe("de");
    expect(ctx.t.common.save).toBe("Speichern");
    expect(document.documentElement.lang).toBe("de");
  });

  it("loads a selected locale lazily and persists the choice", async () => {
    await render();
    expect(ctx.t.common.save).toBe("Save");

    await act(async () => {
      ctx.setLocale("fr");
    });
    await flush();

    expect(ctx.locale).toBe("fr");
    expect(ctx.t.common.save).toBe("Enregistrer");
    expect(localStorage.getItem("qubit-locale")).toBe("fr");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("migrates the pre-rebrand saved locale", () => {
    localStorage.setItem("hermes-locale", "ar");

    expect(getInitialLocale()).toBe("ar");
    expect(localStorage.getItem("qubit-locale")).toBe("ar");
    expect(localStorage.getItem("hermes-locale")).toBeNull();
  });
});
