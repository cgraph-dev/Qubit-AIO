// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = (file: string) => readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
const v2 = here("./manga-v2.css");
const v3 = here("./manga-v3.css");
const store = here("./manga-store.ts");
const gate = here("./MangaSkin.tsx");
const webSelect = here("../sakuga/MangaAwareSelect.tsx");
const sharedRoot = fileURLToPath(new URL("../../public/manga-ui/", import.meta.url));

describe("Manga V2/V3 integration contracts", () => {
  it("mounts all scoped skin layers under the route gate", () => {
    expect(gate).toContain('import "./manga.css";');
    expect(gate).toContain('import "./manga-v2.css";');
    expect(gate).toContain('import "./manga-v3.css";');
    for (const css of [v2, v3]) expect(css).toContain('html[data-qubit-manga="on"]');
  });

  it("protects terminal, office, workbench and HUD surfaces", () => {
    for (const route of ["/chat", "/qubit-workbench", "/qubit-hud", "/qubit-office", "/qubit-office2", "/qubit-office3"]) {
      expect(store).toContain(`"${route}"`);
    }
    expect(store).toContain('pathname.startsWith(path + "/")');
  });

  it("uses a web-only portal dropdown rather than editing the shared desktop select", () => {
    expect(webSelect).toContain("createPortal(");
    expect(webSelect).toContain('document.body');
    expect(webSelect).toContain('position: "fixed"');
    expect(webSelect).toContain("mangaRouteEligible(");
    expect(webSelect).toContain("OriginalSelect");
  });

  it("delivers high-resolution SVG corners and manga star", () => {
    for (const file of ["manga-impact-star.svg", "manga-corner-top-left.svg", "manga-corner-bottom-right.svg"]) {
      expect(existsSync(sharedRoot + file)).toBe(true);
      const svg = readFileSync(sharedRoot + file, "utf8");
      expect(svg).toContain("<svg");
      expect(svg).toContain("viewBox");
    }
    expect(v3).toContain('manga-corner-top-left.svg');
    expect(v3).toContain('manga-corner-bottom-right.svg');
  });

  it("respects reduced motion while giving default motion expressive comic details", () => {
    expect(v3).toContain("prefers-reduced-motion:reduce");
    expect(v3).toContain("@keyframes qm-comic-pop");
    expect(v2).toContain("@keyframes qm-star-enter");
  });
});
