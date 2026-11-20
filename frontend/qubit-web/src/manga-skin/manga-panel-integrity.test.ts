// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");
const styles = read("./manga-panel-integrity.css");
const gate = read("./MangaSkin.tsx");
const sidebar = read("./manga-production-layout-repair.css");

describe("Manga web-dashboard page panel integrity", () => {
  it("loads the page contract after the approved sidebar styles", () => {
    const sidebar = gate.indexOf('import "./manga-production-layout-repair.css";');
    const panels = gate.indexOf('import "./manga-panel-integrity.css";');
    expect(sidebar).toBeGreaterThanOrEqual(0);
    expect(panels).toBeGreaterThan(sidebar);
  });
  it("replaces repeated ZIP headers with one non-repeating live text plate", () => {
    expect(styles).toContain(".settings-section__heading");
    expect(styles).toContain(".settings-page__title");
    expect(styles).toContain(".qplate-module__head");
    expect(styles).toContain("background-repeat:no-repeat!important");
    expect(styles).toContain("width:fit-content!important");
    expect(styles).not.toContain("module-header.svg");
  });
  it("removes conflicting pseudo-panels from the main page and the gateway", () => {
    expect(styles).toContain('.settings-section,.settings-group,.qplate-module');
    expect(styles).toContain('content:none!important');
    expect(styles).toContain('.qm-manga-gateway-shell,.qagent-sidebar__system');
    expect(styles).toContain("border-image:none!important");
  });
  it("preserves typography, keyboard focus, and motion accessibility", () => {
    expect(styles).toContain("text-rendering:optimizeLegibility");
    expect(styles).toContain("outline:2px solid #ffe75d");
    expect(styles).toContain("@media(prefers-reduced-motion:reduce)");
    expect(gate).toContain('emitFxAt("pop", action, { scale: 0.18 })');
    expect(gate).toContain("now - lastCue < 280");
    expect(sidebar).not.toContain('#app-sidebar *\\n');
    expect(sidebar).toContain("DO NOT disable animations across every sidebar descendant");
    expect(gate).toContain('isTerminalChatPath(pathname)');
  });
  it("does not target the terminal, office, workbench, or unrelated themes", () => {
    expect(styles).toContain('html:root[data-qubit-manga="on"]');
    expect(styles).not.toContain("xterm");
    expect(styles).not.toContain("/workbench");
    expect(styles).not.toContain("/qubit-office");
  });
});
