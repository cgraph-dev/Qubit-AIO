// @vitest-environment node
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
const read = (p: string) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8");
const css = read("./manga-zip-sidebar-fix.css");
const gate = read("./MangaSkin.tsx");
const app = read("../App.tsx");

describe("ZIP gateway frame, anime sparkle, and charging cells", () => {
  it("ships the user's original text-free gateway frame without modification", () => {
    const zipGateway = read("../../public/manga-ui/sidebar-gateway.svg");
    expect(createHash("sha256").update(zipGateway).digest("hex")).toBe(
      "23190ad7bd7451728a0c81f535ec5543c356c86312cb3a0be81f884d228d9213"
    );
    expect(css).toContain('url("/manga-ui/sidebar-gateway.svg")');
    expect(zipGateway).not.toContain("Running");
  });

  it("replaces sharp lightning with the fixed-aspect anime sparkle", () => {
    const animeStar = read("../../public/manga-ui/manga-anime-star.svg");
    expect(animeStar).toMatch(/viewBox="0 0 (\d+) \1"/);
    expect(css).toContain('url("/manga-ui/manga-anime-star.svg")');
    expect(css).toContain("background:url(\"/manga-ui/neon-bolt-nut.svg\")");
  });

  it("does not block keyframe colors with important base declarations", () => {
    expect(css).toContain("@keyframes qm-zip-charge-cell");
    expect(css).toContain("animation:qm-zip-charge-cell 2.1s steps(1,end) infinite!important");
    expect(css).not.toContain("background:#ffcf17!important");
    expect(css).not.toContain("box-shadow:inset 0 0 2px #fff8af,0 0 1px #ffe629!important");
    expect(css).toContain("animation-delay:-.7s");
    expect(css).toContain("animation-delay:-1.4s");
    expect(css).toContain("prefers-reduced-motion:reduce");
  });

  it("preserves live status, yellow selected-route, and the functional collapse button", () => {
    expect(app).toContain("<SidebarStatusStrip status={status} latencyMs={latencyMs} />");
    expect(app).toContain("onClick={toggleCollapsed}");
    expect(read("./manga-fidelity.css")).toContain("v3-nav-row-selected.svg");
    expect(gate.indexOf('manga-zip-sidebar-fix.css')).toBeGreaterThan(
      gate.indexOf('manga-fidelity.css')
    );
  });
});
