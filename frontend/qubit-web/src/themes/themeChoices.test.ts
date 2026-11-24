import { describe, expect, it } from "vitest";
import { getFontChoice } from "./fonts";
import { mergeThemeChoices } from "./themeChoices";

describe("appearance catalog", () => {
  it("lists built-ins in display order even behind an older backend", () => {
    const themes = mergeThemeChoices([
      { name: "custom", label: "My theme", description: "" },
      { name: "g4", label: "Q6", description: "Old name" },
      { name: "q5", label: "Retired", description: "" },
      { name: "custom", label: "Duplicate", description: "" },
    ]);
    expect(themes.map((theme) => theme.label)).toEqual([
      "Cyber Ghost", "Cyber Ghost-L", "Lofi SciFi", "Lofi SciFi-L", "My theme",
    ]);
    expect(themes.find((theme) => theme.name === "g4")?.description).toContain("Manga");
    expect(themes.find((theme) => theme.name === "g4")?.description).not.toContain("Q6");
  });

  it("does not accept an inherited object property as a font", () => {
    expect(getFontChoice("inter")?.label).toBe("Inter");
    expect(getFontChoice("constructor")).toBeUndefined();
    expect(getFontChoice("toString")).toBeUndefined();
  });
});
