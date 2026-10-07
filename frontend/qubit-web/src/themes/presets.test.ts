import { describe, expect, it } from "vitest";

import { BUILTIN_THEMES } from "./presets";
import { THEME_NAME_ALIASES, migrateThemeName } from "./themeAliases";

describe("built-in theme ids", () => {
  it("serves the canonical Qubit-blue id, not the legacy id", () => {
    expect(BUILTIN_THEMES["qubit-blue"]).toBeDefined();
    expect(BUILTIN_THEMES["qubit-blue"].name).toBe("qubit-blue");
    expect(BUILTIN_THEMES["nous-blue"]).toBeUndefined();
  });
});

describe("read-old theme name migration", () => {
  it("resolves the pre-rebrand id to the canonical id", () => {
    expect(migrateThemeName("nous-blue")).toBe("qubit-blue");
  });

  it("resolves the earlier LENS_5I id to the canonical id", () => {
    expect(THEME_NAME_ALIASES["lens-5i"]).toBe("qubit-blue");
    expect(migrateThemeName("lens-5i")).toBe("qubit-blue");
  });

  it("passes canonical and unknown names through untouched", () => {
    expect(migrateThemeName("qubit-blue")).toBe("qubit-blue");
    expect(migrateThemeName("midnight")).toBe("midnight");
    expect(migrateThemeName("does-not-exist")).toBe("does-not-exist");
  });
});
