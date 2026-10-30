import { describe, expect, it } from "vitest";
import {
  NAV_DESCRIPTIONS,
  SIDEBAR_FALLBACK_SECTION,
  SIDEBAR_SECTIONS,
  navDescription,
  sectionizeNav,
} from "./sidebar-sections";

const item = (path: string) => ({ path, label: path });

describe("sectionizeNav", () => {
  it("groups known paths in declaration order regardless of input order", () => {
    const out = sectionizeNav([item("/config"), item("/chat"), item("/skills")]);
    expect(out.map((s) => s.section.id)).toEqual(["primary", "agents", "system"]);
    expect(out[0].entries.map((e) => e.path)).toEqual(["/chat"]);
  });

  it("preserves the nav's own order inside a section", () => {
    const out = sectionizeNav([item("/models"), item("/files"), item("/logs")]);
    expect(out).toHaveLength(1);
    expect(out[0].entries.map((e) => e.path)).toEqual(["/models", "/files", "/logs"]);
  });

  it("keeps an unlisted path visible in the trailing fallback section", () => {
    const out = sectionizeNav([item("/chat"), item("/some-future-plugin")]);
    const last = out[out.length - 1];
    expect(last.section.id).toBe(SIDEBAR_FALLBACK_SECTION.id);
    expect(last.entries.map((e) => e.path)).toEqual(["/some-future-plugin"]);
  });

  it("drops empty sections instead of rendering bare headings", () => {
    const out = sectionizeNav([item("/chat")]);
    expect(out).toHaveLength(1);
    expect(out[0].section.id).toBe("primary");
  });

  it("never loses or duplicates an entry", () => {
    const paths = SIDEBAR_SECTIONS.flatMap((s) => [...s.paths]);
    const out = sectionizeNav(paths.map(item));
    const flat = out.flatMap((s) => s.entries.map((e) => e.path));
    expect(flat.slice().sort()).toEqual(paths.slice().sort());
  });

  it("declares every known path exactly once across sections", () => {
    const all = SIDEBAR_SECTIONS.flatMap((s) => [...s.paths]);
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("navDescription", () => {
  it("returns the one-line explanation for a documented path", () => {
    expect(navDescription("/models")).toBe(NAV_DESCRIPTIONS["/models"]);
  });

  it("returns undefined when the label stands alone", () => {
    expect(navDescription("/not-a-route")).toBeUndefined();
  });
});
