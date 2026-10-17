// @vitest-environment jsdom
/**
 * Long-lived tabs must be TOLD when the server serves a newer build, so a
 * shipped fix cannot silently stay invisible in an old tab.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  installBuildWatch,
  loadedBundleName,
  servedBundleName,
  showBuildNotice,
} from "./build-watch";

function htmlFor(bundle: string) {
  return `<!doctype html><html><head><script type="module" crossorigin src="/assets/${bundle}"></script></head><body></body></html>`;
}

function setLoadedBundle(bundle: string) {
  document.head.innerHTML = "";
  const s = document.createElement("script");
  s.type = "module";
  s.src = `/assets/${bundle}`;
  document.head.appendChild(s);
}

function fakeFetch(bundle: string | null, ok = true) {
  return vi.fn(async () =>
    ({
      ok,
      text: async () => (bundle ? htmlFor(bundle) : "<html></html>"),
    }) as unknown as Response,
  );
}

beforeEach(() => {
  document.body.innerHTML = "";
  document.head.innerHTML = "";
  window.sessionStorage.clear();
  vi.useRealTimers();
});

describe("bundle name parsing", () => {
  it("reads the loaded entry chunk and the served one", () => {
    setLoadedBundle("index-aaa.js");
    expect(loadedBundleName()).toBe("index-aaa.js");
    expect(servedBundleName(htmlFor("index-bbb.js"))).toBe("index-bbb.js");
  });

  it("ignores documents without an entry chunk (dev server)", () => {
    expect(loadedBundleName()).toBe("");
    expect(servedBundleName("<html></html>")).toBe("");
  });
});

describe("installBuildWatch", () => {
  it("reports a newer build exactly once", async () => {
    setLoadedBundle("index-old.js");
    const onNewBuild = vi.fn();
    const fetchFn = fakeFetch("index-new.js");
    installBuildWatch({ fetchFn, onNewBuild, intervalMs: 1000 });
    await vi.waitFor(() => expect(onNewBuild).toHaveBeenCalledTimes(1));
    expect(onNewBuild).toHaveBeenCalledWith("index-new.js");
    // Same remote build again: no second notice.
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((r) => setTimeout(r, 5));
    expect(onNewBuild).toHaveBeenCalledTimes(1);
  });

  it("stays quiet when the tab already runs the served build", async () => {
    setLoadedBundle("index-same.js");
    const onNewBuild = vi.fn();
    installBuildWatch({ fetchFn: fakeFetch("index-same.js"), onNewBuild, intervalMs: 1000 });
    await new Promise((r) => setTimeout(r, 20));
    expect(onNewBuild).not.toHaveBeenCalled();
  });

  it("swallows network failures and retries later", async () => {
    setLoadedBundle("index-old.js");
    const onNewBuild = vi.fn();
    const fetchFn = vi.fn(async () => {
      throw new Error("offline");
    });
    const stop = installBuildWatch({ fetchFn, onNewBuild, intervalMs: 1000 });
    await new Promise((r) => setTimeout(r, 20));
    expect(onNewBuild).not.toHaveBeenCalled();
    stop();
  });
});

describe("showBuildNotice", () => {
  it("renders one banner and reloads on click", () => {
    const reload = vi.fn();
    showBuildNotice("index-new.js", document, reload);
    const banner = document.getElementById("qubit-build-notice");
    expect(banner).not.toBeNull();
    const btn = Array.from(banner!.querySelectorAll("button")).find(
      (b) => b.textContent === "Reload",
    )!;
    btn.click();
    expect(reload).toHaveBeenCalledTimes(1);
    // A second call never stacks another banner.
    showBuildNotice("index-new.js", document, reload);
    expect(document.querySelectorAll("#qubit-build-notice")).toHaveLength(1);
  });

  it("stays dismissed for the same build, returns for the next one", () => {
    showBuildNotice("index-new.js", document, () => {});
    const close = Array.from(
      document.querySelectorAll("#qubit-build-notice button"),
    ).find((b) => b.textContent === "×") as HTMLButtonElement;
    close.click();
    expect(document.getElementById("qubit-build-notice")).toBeNull();
    showBuildNotice("index-new.js", document, () => {});
    expect(document.getElementById("qubit-build-notice")).toBeNull();
    showBuildNotice("index-newer.js", document, () => {});
    expect(document.getElementById("qubit-build-notice")).not.toBeNull();
  });
});
