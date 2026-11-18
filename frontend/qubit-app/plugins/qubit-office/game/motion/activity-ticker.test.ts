// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { attachActivityTickerController } from "./activity-ticker";

function mountFeed(): { root: HTMLElement; ticker: HTMLElement } {
  const root = document.createElement("div");
  root.className = "ui-root";
  const ticker = document.createElement("span");
  ticker.className = "ui-feed__ticker";
  ticker.textContent = "waiting";
  root.append(ticker);
  document.body.append(root);
  return { root, ticker };
}

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => {
  document.body.innerHTML = "";
  document.documentElement.removeAttribute("data-office-reduced-motion");
});

describe("attachActivityTickerController", () => {
  it("pulses the collapsed ticker on a text change", async () => {
    const { root, ticker } = mountFeed();
    const stop = attachActivityTickerController(root, { pulseMs: 60 });

    ticker.textContent = "working";
    await flush();
    expect(ticker.getAttribute("data-fx-tick")).toBe("1");

    await new Promise((resolve) => setTimeout(resolve, 90));
    expect(ticker.hasAttribute("data-fx-tick")).toBe(false);
    stop();
  });

  it("clears the pulse and timer on cleanup", async () => {
    const { root, ticker } = mountFeed();
    const stop = attachActivityTickerController(root, { pulseMs: 5000 });

    ticker.textContent = "changed";
    await flush();
    expect(ticker.getAttribute("data-fx-tick")).toBe("1");

    stop();
    expect(ticker.hasAttribute("data-fx-tick")).toBe(false);
  });

  it("does not pulse under reduced motion", async () => {
    document.documentElement.setAttribute("data-office-reduced-motion", "1");
    const { root, ticker } = mountFeed();
    const stop = attachActivityTickerController(root);

    ticker.textContent = "changed";
    await flush();
    expect(ticker.hasAttribute("data-fx-tick")).toBe(false);
    stop();
  });

  it("never touches the feed row or its button wrapper", async () => {
    const { root } = mountFeed();
    const list = document.createElement("ul");
    const item = document.createElement("li");
    item.className = "ui-feed__item";
    const row = document.createElement("button");
    row.className = "ui-feed__row";
    item.append(row);
    list.append(item);
    root.append(list);

    const stop = attachActivityTickerController(root);
    await flush();
    expect(item.hasAttribute("data-fx-tick")).toBe(false);
    expect(item.hasAttribute("data-fx-fresh")).toBe(false);
    expect(row.hasAttribute("data-fx-tick")).toBe(false);
    stop();
  });

  it("is a no-op when the root has no ticker", () => {
    const root = document.createElement("div");
    const stop = attachActivityTickerController(root);
    expect(() => stop()).not.toThrow();
  });
});
