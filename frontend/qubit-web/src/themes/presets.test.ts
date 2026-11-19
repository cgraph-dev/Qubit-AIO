import { describe, expect, it } from "vitest";

import { BUILTIN_THEMES, isMangaThemeName } from "./presets";
import { THEME_NAME_ALIASES, migrateThemeName } from "./themeAliases";

const EXPECTED_IDS = [
  "q2",
  "q2-l",
  "q3",
  "q3-l",
  "q4",
  "q4-l",
  "q6",
  "q6-l",
];

describe("built-in theme ids", () => {
  it("serves exactly Q2..Q6 plus one large variant each", () => {
    expect(Object.keys(BUILTIN_THEMES).sort()).toEqual([...EXPECTED_IDS].sort());
  });

  it("no longer lists Q1/Q1-L/Q5/Q5-L", () => {
    for (const gone of ["default", "q1-l", "q5", "q5-l"]) {
      expect(BUILTIN_THEMES[gone]).toBeUndefined();
    }
  });

  it("labels every theme with its Q-number", () => {
    for (const [id, label] of [
      ["q2", "Q2"],
      ["q3", "Q3"],
      ["q4", "Q4"],
      ["q6", "Q6"],
      ["q2-l", "Q2-L"],
      ["q3-l", "Q3-L"],
      ["q4-l", "Q4-L"],
      ["q6-l", "Q6-L"],
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
    ["q2", "q2-l"],
    ["q3", "q3-l"],
    ["q4", "q4-l"],
    ["q6", "q6-l"],
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

  it("Q6/Q6-L are the manga theme pair", () => {
    expect(isMangaThemeName("q6")).toBe(true);
    expect(isMangaThemeName("q6-l")).toBe(true);
    expect(isMangaThemeName("q2")).toBe(false);
  });
});

describe("read-old theme name migration", () => {
  it("maps every retired built-in id onto a surviving theme", () => {
    expect(migrateThemeName("default")).toBe("q2");
    expect(migrateThemeName("q1")).toBe("q2");
    expect(migrateThemeName("q1-l")).toBe("q2-l");
    expect(migrateThemeName("q5")).toBe("q2");
    expect(migrateThemeName("q5-l")).toBe("q2-l");
    expect(migrateThemeName("qubit")).toBe("q2");
    expect(migrateThemeName("qubit-contrast")).toBe("q2");
    expect(migrateThemeName("default-large")).toBe("q2-l");
    expect(migrateThemeName("midnight")).toBe("q2");
    expect(migrateThemeName("mono")).toBe("q3");
    expect(migrateThemeName("cyberpunk")).toBe("q4");
  });

  it("every alias target is a theme that actually exists", () => {
    for (const target of Object.values(THEME_NAME_ALIASES)) {
      expect(BUILTIN_THEMES[target]).toBeDefined();
    }
  });

  it("passes canonical and unknown names through untouched", () => {
    expect(migrateThemeName("q2")).toBe("q2");
    expect(migrateThemeName("q4-l")).toBe("q4-l");
    expect(migrateThemeName("q6-l")).toBe("q6-l");
    expect(migrateThemeName("does-not-exist")).toBe("does-not-exist");
  });
});
