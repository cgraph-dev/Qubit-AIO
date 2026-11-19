// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
describe("Manga Office loading-only redesign", () => {
  const css = read("./office-manga.css");
  const component = read("./OfficeLoadingScreen.tsx");
  const config = read("./config.ts");
  it("keeps the real Sakuga progress / retry effects while styling only the office loading overlay", () => {
    expect(component).toContain('import "./office-manga.css";');
    expect(component).toContain('sakugaProgress(R');
    expect(component).toContain('sakugaButton(R');
    expect(component).toContain('officeFx("impact")');
    expect(css).toContain(".office-loading__progress");
    expect(css).toContain(".office-loading__content");
    expect(css).not.toMatch(/(^|\n)\s*(?:body|html|#app-sidebar)\s*\{/);
  });
  it("shows the existing atomic cat asset instead of orbit + spinner clutter", () => {
    expect(css).toContain("/manga-ui/qubit-cat-atom.svg");
    expect(css).toContain(".office-loading__spinner {display:none;}");
    expect(css).toContain(".office-loading__mark::before");
    expect(css).toContain("prefers-reduced-motion:reduce");
  });
  it("does not start optional weather animation by default", () => {
    expect(config).toContain("effects: false");
    expect(config).toContain("typeof merged.effects");
    expect(component).toContain('config.effects ? effectForInitial(config) : "none"');
    expect(component).toContain('createEffectSurface');
  });
});
