// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import { installKanbanTooltips } from "./manga-kanban-tooltips";

let cleanup: (() => void) | null = null;

afterEach(() => {
  cleanup?.();
  cleanup = null;
  document.body.innerHTML = "";
});

function hover(target: Element) {
  target.dispatchEvent(new MouseEvent("pointerover", { bubbles: true }));
}

function leave(target: Element) {
  target.dispatchEvent(new MouseEvent("pointerout", { bubbles: true }));
}

describe("kanban manga tooltips", () => {
  it("swaps a plain title for one portaled manga bubble", () => {
    const board = document.createElement("div");
    board.className = "qubit-kanban";
    const trigger = document.createElement("span");
    trigger.title = "Saved tooltip";
    board.append(trigger);
    document.body.append(board);
    cleanup = installKanbanTooltips();

    hover(trigger);

    expect(trigger.hasAttribute("title")).toBe(false);
    expect(trigger.getAttribute("data-qm-tip")).toBe("Saved tooltip");
    const bubble = document.querySelector(".qm-kanban-tip");
    expect(bubble).not.toBeNull();
    expect(bubble?.textContent).toBe("Saved tooltip");
    expect(bubble?.parentElement).toBe(document.body);

    leave(trigger);
    expect(trigger.getAttribute("title")).toBe("Saved tooltip");
    expect(trigger.hasAttribute("data-qm-tip")).toBe(false);
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();
  });

  it("ignores triggers outside the board but suppresses native titles inside a HoverTip", () => {
    const outside = document.createElement("span");
    outside.title = "outside";
    const tip = document.createElement("span");
    tip.className = "qsk-hover-tip";
    const inner = document.createElement("button");
    inner.title = "inner";
    tip.append(inner);
    const board = document.createElement("div");
    board.className = "qubit-kanban";
    board.append(tip);
    document.body.append(outside, board);
    cleanup = installKanbanTooltips();

    hover(outside);
    expect(outside.title).toBe("outside");

    hover(inner);
    expect(inner.hasAttribute("title")).toBe(false);
    expect(inner.getAttribute("data-qm-tip")).toBe("inner");
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();

    leave(inner);
    expect(inner.title).toBe("inner");
  });

  it("strips the redundant column-header title so "+" has exactly one tooltip", () => {
    const board = document.createElement("div");
    board.className = "qubit-kanban";
    const header = document.createElement("div");
    header.className = "qubit-kanban-column-header";
    header.title = "Column help";
    const tip = document.createElement("span");
    tip.className = "qsk-hover-tip";
    const inner = document.createElement("button");
    tip.append(inner);
    header.append(tip);
    board.append(header);
    document.body.append(board);
    cleanup = installKanbanTooltips();

    expect(header.hasAttribute("title")).toBe(false);
    expect(header.getAttribute("data-qm-header-tip")).toBe("Column help");

    hover(inner);
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();

    leave(inner);
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();

    cleanup();
    cleanup = null;
    expect(header.getAttribute("title")).toBe("Column help");
    expect(header.hasAttribute("data-qm-header-tip")).toBe(false);
  });

  it("cleans up the bubble and listeners on unmount", () => {
    const board = document.createElement("div");
    board.className = "qubit-kanban";
    const trigger = document.createElement("span");
    trigger.title = "cleanup";
    board.append(trigger);
    document.body.append(board);
    cleanup = installKanbanTooltips();

    hover(trigger);
    cleanup();
    cleanup = null;

    expect(trigger.title).toBe("cleanup");
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();
    hover(trigger);
    expect(document.querySelector(".qm-kanban-tip")).toBeNull();
  });
});
