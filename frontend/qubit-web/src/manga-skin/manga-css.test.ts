// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  fileURLToPath(new URL("./manga.css", import.meta.url)),
  "utf8",
);

const root = fileURLToPath(new URL("../../public/manga-ui/", import.meta.url));

describe("manga skin stylesheet", () => {
  it("is gated behind the document marker", () => {
    expect(css).toContain('html[data-qubit-manga="on"]');
    // The retired standalone MANGA switch must not come back.
    expect(css).not.toContain(".qm-skin-switch");
  });

  it("styles the Q6 theme-panel entry and active trigger", () => {
    expect(css).toContain(".qm-theme-trigger[data-manga-theme=\"true\"]");
    const popup = readFileSync(new URL("./manga-popup-panels.css", import.meta.url), "utf8");
    expect(popup).toContain('.qm-picker-choice[aria-pressed="true"]');
    expect(popup).toContain('border-image: var(--qmz-dropdown-panel)');
    expect(existsSync(root + "zip/dropdown-panel.svg")).toBe(true);
    expect(css).not.toContain("qrail-flyout");
  });

  it("carries the full manga palette", () => {
    for (const token of [
      "--qm-magenta: #ff269a",
      "--qm-yellow: #ffe500",
      "--qm-violet: #b965ff",
      "--qm-cyan: #27e9f3",
      "--qm-bg: #08061a",
    ]) {
      expect(css).toContain(token);
    }
  });

  it("recolours Sakuga through its public variable seam", () => {
    for (const token of [
      "--sk-pri:",
      "--sk-acc:",
      "--sk-card:",
      "--sk-btn-sh:",
      "::part(button)",
    ]) {
      expect(css).toContain(token);
    }
  });

  it("covers the shared page families", () => {
    for (const selector of [
      "#app-sidebar",
      ".qagent-sidebar__section-head",
      ".qagent-sidebar__link[aria-current=\"page\"]",
      ".settings-section__heading",
      ".settings-group",
      ".settings-row",
      ".qchat__side",
      ".qchat__message",
      'table',
      '[role="dialog"]',
      'input, textarea, select',
    ]) {
      expect(css).toContain(selector);
    }
  });

  it("keeps the real terminal out of the skin", () => {
    expect(css).toMatch(/\.xterm/);
    expect(css).not.toMatch(/\.xterm\s*\{[^}]*background:/);
  });

  it("ships every ornament the stylesheet references", () => {
    for (const asset of [
      "halftone.svg",
      "impact-star.svg",
      "section-slashes.svg",
      "panel-corner.svg",
    ]) {
      expect(existsSync(root + asset)).toBe(true);
    }
  });
});
