import { describe, expect, it } from "vitest";
import { pixelGlyphs } from "./PixelText";
import type { OfficeThemeFont } from "./office-theme";

const font = (over: Partial<OfficeThemeFont> = {}): OfficeThemeFont => ({
  src: "/plugins/qubit-office/themes/limezu/ui/packs/fonts/ui-font.png",
  cell: 32, cols: 8, rows: 8,
  glyphs: "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzñÇ",
  scale: 0.5, license: "test", licenseOk: true,
  ...over,
});

describe("pixel heading glyph layout", () => {
  it("maps characters to atlas cells at the requested size", () => {
    const glyphs = pixelGlyphs("AB", font(), 16);
    expect(glyphs).toHaveLength(2);
    expect(glyphs?.[0]).toMatchObject({
      char: "A", index: 10, col: 2, row: 1,
      style: {
        width: "16px", height: "16px",
        backgroundSize: "128px 128px",
        backgroundPosition: "-32px -16px",
      },
    });
    expect(glyphs?.[1]).toMatchObject({
      char: "B", index: 11, col: 3, row: 1,
      style: { backgroundPosition: "-48px -16px" },
    });
  });

  it("renders a half-cell spacer for a space", () => {
    const glyphs = pixelGlyphs("A B", font(), 16);
    expect(glyphs).toHaveLength(3);
    expect(glyphs?.[1]).toMatchObject({
      char: " ", index: null,
      style: { width: "8px", height: "16px" },
    });
    expect(glyphs?.[1].style.backgroundImage).toBeUndefined();
  });

  it("falls back when the font is absent or a glyph is unsupported", () => {
    expect(pixelGlyphs("A", null, 16)).toBeNull();
    expect(pixelGlyphs("A", font({ glyphs: "B" }), 16)).toBeNull();
    expect(pixelGlyphs("A😀", font(), 16)).toBeNull();
    expect(pixelGlyphs("A", font(), 0)).toBeNull();
  });

  it("accepts the pack's full declared glyph set", () => {
    expect(pixelGlyphs("Qubit 09", font(), 12)).toHaveLength(8);
    expect(pixelGlyphs("ñÇ", font(), 12)).not.toBeNull();
  });
});
