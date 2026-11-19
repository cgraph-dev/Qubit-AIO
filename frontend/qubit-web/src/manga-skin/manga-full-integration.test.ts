// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), "utf8");
const registry = read("./manga-zip-registry.css");
const routes = read("./manga-zip-routes.css");
const controls = read("./manga-zip-controls.css");
const gate = read("./MangaSkin.tsx");
const app = read("../App.tsx");
const store = read("./manga-store.ts");
const bundle = read("../../scripts/manga-original-svg-bundle.b64");
const uncompressed = inflateSync(Buffer.from(bundle.trim(), "base64"));
const { svg, manifest } = JSON.parse(uncompressed.toString("utf8"));

describe("all 89 original Manga ZIP SVG assets and dashboard integration", () => {
  it("uses the exact source archive with a stable hash", () => {
    expect(createHash("sha256").update(uncompressed).digest("hex")).toBe(
      "3c1ddf1e2f216a57cf757e66c74fc95aa8fd446c770bdcad218a91864713a66f"
    );
    expect(manifest).toHaveLength(89);
    expect(Object.keys(svg)).toHaveLength(89);
  });
  it("registers every source asset and no unrecognized asset name", () => {
    const tokens = [...registry.matchAll(/--qmz-([\w-]+):url\("\/manga-ui\/zip\/[\w-]+\.svg"\)/g)].map((m) => m[1]);
    const sources = manifest.map(({ name }: { name: string }) => name);
    expect([...tokens].sort()).toEqual([...sources].sort());
    for (const name of sources) {
      expect(registry).toContain('data-qm-zip-asset="' + name + '"');
      expect(svg[name + ".svg"]).toContain("<svg");
    }
  });
  it("wires page and controls styles after complete ZIP asset gate", () => {
    for (const rule of [registry, routes, controls]) {
      expect(rule).toContain('data-qubit-manga-zip="ready"');
      expect(rule).toContain('data-qubit-manga="on"');
    }
    expect(gate).toContain('manga-zip-registry.css');
    expect(gate).toContain('manga-zip-routes.css');
    expect(gate).toContain('manga-zip-controls.css');
    expect(app).toContain("data-qm-route={pathname}");
  });
  it("preserves dynamic yellow selection and collapse controls", () => {
    expect(app).toContain("onClick={toggleCollapsed}");
    expect(app).toContain("PanelLeftClose");
    expect(app).toContain("PanelLeftOpen");
    expect(read("./manga-fidelity.css")).toContain("v3-nav-row-selected.svg");
    expect(routes).not.toContain('.qagent-sidebar__link[aria-current="page"]');
  });
  it("does not skin Workbench, HUD, or Office routes", () => {
    for (const excluded of [
      "/qubit-workbench", "/qubit-hud", "/qubit-office", "/qubit-office2", "/qubit-office3"
    ]) expect(store).toContain('"' + excluded + '"');
    expect(controls).toContain("prefers-reduced-motion:reduce");
  });
});
