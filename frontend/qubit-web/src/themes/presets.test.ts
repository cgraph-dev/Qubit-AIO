import { describe, expect, it } from "vitest";

import { BUILTIN_THEMES, isMangaThemeName } from "./presets";
import { THEME_NAME_ALIASES, migrateThemeName } from "./themeAliases";

const EXPECTED_IDS = ["g3", "g3-l", "g4", "g4-l"];

describe("built-in theme ids", () => {
  it("serves exactly two base themes plus one large variant each", () => {
    expect(Object.keys(BUILTIN_THEMES).sort()).toEqual([...EXPECTED_IDS].sort());
  });

  it("no longer lists Q1/Q1-L/Q2/Q2-L/Q5/Q5-L", () => {
    for (const gone of ["default", "q1-l", "q2", "q2-l", "q3", "q3-l", "q5", "q5-l"]) {
      expect(BUILTIN_THEMES[gone]).toBeUndefined();
    }
  });

  it("labels every theme with its display name", () => {
    for (const [id, label] of [
      ["g3", "Cyber Ghost"],
      ["g4", "Lofi SciFi"],
      ["g3-l", "Cyber Ghost-L"],
      ["g4-l", "Lofi SciFi-L"],
    ]) {
      expect(BUILTIN_THEMES[id].label).toBe(label);
    }
  });

  it("no longer ships the retired themes", () => {
    for (const gone of [
      "rose",
      "ember",
      "qubit-blue",
      "default-large",
      "midnight",
      "mono",
      "cyberpunk",
      "default",
      "q1-l",
      "q5",
      "q5-l",
      "qubit",
    ]) {
      expect(BUILTIN_THEMES[gone]).toBeUndefined();
    }
  });
});

describe("large variants", () => {
  const pairs: Array<[string, string]> = [
    ["g3", "g3-l"],
    ["g4", "g4-l"],
  ];

  it("every base theme has a large variant that keeps its palette", () => {
    for (const [base, large] of pairs) {
      expect(BUILTIN_THEMES[large].palette).toEqual(BUILTIN_THEMES[base].palette);
    }
  });

  it("scales type up and spacing out without changing the look", () => {
    for (const [base, large] of pairs) {
      const b = BUILTIN_THEMES[base];
      const l = BUILTIN_THEMES[large];
      expect(parseFloat(l.typography.baseSize)).toBeGreaterThan(
        parseFloat(b.typography.baseSize),
      );
      expect(l.layout.density).toBe("spacious");
      expect(l.typography.fontSans).toBe(b.typography.fontSans);
      expect(l.layout.radius).toBe(b.layout.radius);
    }
  });

  it("g4/g4-l are the manga theme pair", () => {
    expect(isMangaThemeName("g4")).toBe(true);
    expect(isMangaThemeName("g4-l")).toBe(true);
    expect(isMangaThemeName("q2")).toBe(false);
  });
});

describe("read-old theme name migration", () => {
  it("maps every retired built-in id onto a surviving theme", () => {
    expect(migrateThemeName("default")).toBe("g4-l");
    expect(migrateThemeName("q1")).toBe("g3");
    expect(migrateThemeName("q1-l")).toBe("g3-l");
    expect(migrateThemeName("q2")).toBe("g3");
    expect(migrateThemeName("q2-l")).toBe("g3-l");
    expect(migrateThemeName("q3")).toBe("g3");
    expect(migrateThemeName("q3-l")).toBe("g3-l");
    expect(migrateThemeName("q5")).toBe("g3");
    expect(migrateThemeName("q5-l")).toBe("g3-l");
    expect(migrateThemeName("qubit")).toBe("g3");
    expect(migrateThemeName("qubit-contrast")).toBe("g3");
    expect(migrateThemeName("default-large")).toBe("g4-l");
    expect(migrateThemeName("midnight")).toBe("g3");
    expect(migrateThemeName("mono")).toBe("g3");
    expect(migrateThemeName("cyberpunk")).toBe("g3");
  });

  it("every alias target is a theme that actually exists", () => {
    for (const target of Object.values(THEME_NAME_ALIASES)) {
      expect(BUILTIN_THEMES[target]).toBeDefined();
    }
  });

  it("passes canonical and unknown names through untouched", () => {
    expect(migrateThemeName("g3")).toBe("g3");
    expect(migrateThemeName("g3-l")).toBe("g3-l");
    expect(migrateThemeName("g4-l")).toBe("g4-l");
    expect(migrateThemeName("does-not-exist")).toBe("does-not-exist");
  });
});
