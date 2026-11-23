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
    expect(styles).not.toMatch(/url\(["']?\/manga-ui\/zip\/module-header\.svg/);
    expect(styles).toMatch(/url\("\/manga-ui\/qubit-module-header\.svg"\)[^;]+no-repeat/);
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
  it("drops the redundant frame around a section tab strip but keeps the tablist", () => {
    expect(styles).toContain(
      '.settings-section:has(> .settings-section__actions[role="tablist"]):not(:has(> .settings-group))',
    );
    expect(styles).toContain(
      '.settings-section > .settings-section__actions[role="tablist"]',
    );
    expect(styles).toContain("background-color:transparent!important");
    const at = styles.indexOf(
      '.settings-section > .settings-section__actions[role="tablist"]',
    );
    const body = styles.slice(at, styles.indexOf("}", at));
    expect(body).toContain("border:0!important");
    expect(body).toContain("box-shadow:none!important");
  });

  it("gives heading count badges spacing and a yellow manga plate", () => {
    const at = styles.indexOf(") .settings-count {");
    expect(at).toBeGreaterThanOrEqual(0);
    const body = styles.slice(at, styles.indexOf("}", at));
    expect(body).toContain("margin-inline-start:10px!important");
    expect(body).toContain("flex:0 0 auto!important");
    expect(body).toContain("background:var(--qm-yellow");
    expect(body).toContain("box-shadow:2px 2px 0 var(--qm-black");
  });

  it("does not target the terminal, office, workbench, or unrelated themes", () => {
    expect(styles).toContain('html:root[data-qubit-manga="on"]');
    expect(styles).not.toContain("xterm");
    expect(styles).not.toContain("/workbench");
    expect(styles).not.toContain("/qubit-office");
  });
});
