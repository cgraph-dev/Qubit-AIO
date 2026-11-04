import { describe, expect, it } from "vitest";

import { BUILTIN_THEMES } from "./presets";
import { THEME_NAME_ALIASES, migrateThemeName } from "./themeAliases";

const EXPECTED_IDS = [
  "default",
  "q1-l",
  "q2",
  "q2-l",
  "q3",
  "q3-l",
  "q4",
  "q4-l",
  "q5",
  "q5-l",
  "q6",
  "q6-l",
];

describe("built-in theme ids", () => {
  it("serves exactly Q1..Q6 plus one large variant each", () => {
    expect(Object.keys(BUILTIN_THEMES).sort()).toEqual([...EXPECTED_IDS].sort());
  });

  it("presents the canonical default as Q1", () => {
    expect(BUILTIN_THEMES.default.name).toBe("default");
    expect(BUILTIN_THEMES.default.label).toBe("Q1");
  });

  it("labels every theme with its Q-number", () => {
    for (const [id, label] of [
      ["q2", "Q2"],
      ["q3", "Q3"],
      ["q4", "Q4"],
      ["q5", "Q5"],
      ["q6", "Q6"],
      ["q1-l", "Q1-L"],
      ["q2-l", "Q2-L"],
      ["q3-l", "Q3-L"],
      ["q4-l", "Q4-L"],
      ["q5-l", "Q5-L"],
      ["q6-l", "Q6-L"],
    ]) {
      expect(BUILTIN_THEMES[id].label).toBe(label);
    }
  });

  it("no longer ships the retired themes", () => {
    for (const gone of ["rose", "ember", "qubit-blue", "default-large", "midnight", "mono", "cyberpunk"]) {
      expect(BUILTIN_THEMES[gone]).toBeUndefined();
    }
  });
});

describe("large variants", () => {
  const pairs: Array<[string, string]> = [
    ["default", "q1-l"],
    ["q2", "q2-l"],
    ["q3", "q3-l"],
    ["q4", "q4-l"],
    ["q5", "q5-l"],
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
});

describe("read-old theme name migration", () => {
  it("maps every retired built-in id onto the theme it looks like", () => {
    expect(migrateThemeName("qubit-blue")).toBe("q2");
    expect(migrateThemeName("nous-blue")).toBe("q2");
    expect(migrateThemeName("lens-5i")).toBe("q2");
    expect(migrateThemeName("default-large")).toBe("q1-l");
    expect(migrateThemeName("midnight")).toBe("q3");
    expect(migrateThemeName("mono")).toBe("q4");
    expect(migrateThemeName("cyberpunk")).toBe("q5");
    expect(migrateThemeName("qubit-contrast")).toBe("q6");
  });

  it("every alias target is a theme that actually exists", () => {
    for (const target of Object.values(THEME_NAME_ALIASES)) {
      expect(BUILTIN_THEMES[target]).toBeDefined();
    }
  });

  it("passes canonical and unknown names through untouched", () => {
    expect(migrateThemeName("default")).toBe("default");
    expect(migrateThemeName("q4-l")).toBe("q4-l");
    expect(migrateThemeName("does-not-exist")).toBe("does-not-exist");
  });
});
