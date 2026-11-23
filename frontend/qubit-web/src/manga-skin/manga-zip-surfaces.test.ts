// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (name: string) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), "utf8");
const style = read("./manga-zip-surfaces.css");
const composer = read("./qubit-chat-manga.css");
const registry = read("./manga-zip-registry.css");
const gate = read("./MangaSkin.tsx");
const installer = read("../../../../scripts/install_manga_zip_assets.py");
const sidebarFix = read("./manga-zip-sidebar-fix.css");

describe("full Manga ZIP asset integration safety", () => {
  it("is enabled only with a full 89-family manifest and the Manga gate", () => {
    expect(style).toContain('html[data-qubit-manga="on"][data-qubit-manga-zip="ready"]');
    expect(gate).toContain('fetch("/manga-ui/zip/manifest.json"');
    expect(gate).toContain("manifest.length === 89");
    expect(gate).toContain("delete root.dataset.qubitMangaZip");
  });

  it("verifies both original SVG and PNG formats and never overwrites old artwork", () => {
    expect(installer).toContain('for ext in (".svg", ".png")');
    expect(installer).toContain('DEST = ROOT / "frontend/qubit-web/public/manga-ui/zip"');
    expect(installer).toContain("hashes[filename] = sha256(payload)");
    expect(installer).toContain("registered != set(hashes)");
  });

  it("keeps existing yellow selected-route treatment and backend status", () => {
    expect(style).not.toContain("nav-default.svg");
    expect(style).not.toContain('.qagent-sidebar__link[aria-current="page"] {');
    expect(sidebarFix).toContain('url("/manga-ui/sidebar-gateway.svg")');
  });

  it("carries all key page-family hooks while respecting motion", () => {
    expect(style).toContain("right-model.svg");
    expect(registry).toContain("chat-composer.svg");
    expect(style).not.toContain("chat-composer.svg");
    expect(composer).toContain(".qchat__composer::before");
    expect(composer).toContain('border-image-source:url("/manga-ui/qubit-chat/composer.svg")');
    expect(style).toContain("prefers-reduced-motion:reduce");
    expect(style).not.toContain("plugin-card.svg");
    expect(style).not.toContain("model-card.svg");
  });
});
