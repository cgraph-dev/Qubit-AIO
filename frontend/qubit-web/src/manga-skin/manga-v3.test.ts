// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = (file: string) => readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
const v2 = here("./manga-v2.css");
const v3 = here("./manga-v3.css");
const v4 = here("./manga-v4.css");
const v5 = here("./manga-v5.css");
const gate = here("./MangaSkin.tsx");
const picker = here("./PickerPanel.tsx");
const webSelect = here("../sakuga/MangaAwareSelect.tsx");
const sharedRoot = fileURLToPath(new URL("../../public/manga-ui/", import.meta.url));

describe("Manga V2/V3 integration contracts", () => {
  it("mounts all scoped skin layers under the route gate", () => {
    expect(gate).toContain('import "./manga.css";');
    expect(gate).toContain('import "./manga-v2.css";');
    expect(gate).toContain('import "./manga-v3.css";');
    expect(gate).toContain('import "./manga-v4.css";');
    expect(gate).toContain('import "./manga-v5.css";');
    for (const css of [v2, v3, v4, v5]) expect(css).toContain('html[data-qubit-manga="on"]');
  });

  it("uses a web-only portal dropdown rather than editing the shared desktop select", () => {
    expect(webSelect).toContain("<PickerPanel");
    expect(picker).toContain("createPortal(");
    expect(picker).toContain('trigger.closest<HTMLElement>(\'[aria-modal="true"]\') ?? document.body');
    expect(picker).toContain('position: "fixed"');
    expect(webSelect).toContain("useMangaLayerActive(");
    expect(here("./useMangaLayerActive.ts")).toContain('dataset.qubitManga === "on"');
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
    expect(v4).toContain("@keyframes qm-rail-select");
    expect(v5).toContain(".sessions-table");
    expect(v5).toContain(".cron-table");
    expect(v5).toContain(".workboard-card");
  });
});
