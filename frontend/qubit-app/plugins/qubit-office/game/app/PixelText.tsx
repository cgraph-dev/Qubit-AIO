/* A tiny bitmap-font renderer for headings/chrome.
 *
 * The owner's uı-fonts pack is a PNG sheet of 32x32 glyphs, not a TTF. This
 * component maps the payload's `ui.font.glyphs` order to sprite cells and
 * paints one background-positioned span per glyph. It is deliberately used
 * ONLY for headings/chrome: body paragraphs stay in the normal UI font so
 * legibility is never traded for style.
 *
 * FALLBACK IS TOTAL: a missing/blocked font, a glyph outside the sheet, or a
 * character the pack does not carry renders the plain text instead. The
 * visible glyphs are decorative (aria-hidden); the real string is the
 * accessible name, so screen readers never get a run of empty spans.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  officeUiFont,
  type OfficeTheme,
  type OfficeThemeFont,
} from "./office-theme";

export interface PixelTextProps {
  text: string;
  theme?: OfficeTheme | null;
  /** Target glyph height in CSS pixels (the font sheet's cell is 32). */
  size?: number;
  className?: string;
  "data-testid"?: string;
}

export interface PixelGlyph {
  char: string;
  index: number | null;
  col: number;
  row: number;
  style: Record<string, string | number>;
}

/**
 * The glyph layout for a string, or null when the font is absent or any
 * non-space character is outside the pack's declared glyph order.
 */
export function pixelGlyphs(
  text: string,
  font: OfficeThemeFont | null | undefined,
  size: number,
): PixelGlyph[] | null {
  if (!font || !(size > 0)) return null;
  const cell = Number(font.cell);
  const cols = Number(font.cols);
  if (!(cell > 0) || !(cols > 0) || !font.glyphs) return null;
  const glyphIndex = new Map<string, number>();
  for (let i = 0; i < font.glyphs.length; i += 1) {
    if (!glyphIndex.has(font.glyphs[i])) glyphIndex.set(font.glyphs[i], i);
  }
  const scale = size / cell;
  const glyphSize = cell * scale;
  const backgroundSize = `${font.cols * cell * scale}px `
    + `${font.rows * cell * scale}px`;
  const glyphs: PixelGlyph[] = [];
  for (const char of Array.from(String(text || ""))) {
    if (char === " ") {
      glyphs.push({
        char, index: null, col: 0, row: 0,
        style: { width: `${glyphSize * 0.5}px`, height: `${glyphSize}px` },
      });
      continue;
    }
    const index = glyphIndex.get(char);
    if (index === undefined) return null;
    const col = index % font.cols;
    const row = Math.floor(index / font.cols);
    glyphs.push({
      char, index, col, row,
      style: {
        width: `${glyphSize}px`,
        height: `${glyphSize}px`,
        backgroundImage: `url("${font.src}")`,
        backgroundSize,
        backgroundPosition:
          `-${col * glyphSize}px -${row * glyphSize}px`,
      },
    });
  }
  return glyphs;
}

export function createPixelText(R: OfficeGameShellProps["React"]) {
  return function PixelText(props: PixelTextProps) {
    const font = officeUiFont(props.theme);
    const size = props.size ?? (font ? font.cell * (font.scale ?? 0.5) : 16);
    const glyphs = pixelGlyphs(props.text, font, size);
    const common = {
      className: "office-pixel-text" + (props.className ? ` ${props.className}` : ""),
      "data-testid": props["data-testid"],
      "data-office-font": font ? "pixel" : "fallback",
    };
    if (!glyphs) {
      return R.createElement("span", {
        ...common,
        "data-office-font": "fallback",
      }, props.text);
    }
    return R.createElement("span", {
      ...common,
      role: "img",
      "aria-label": props.text,
      title: props.text,
    }, glyphs.map((glyph, index) => R.createElement("span", {
      key: `${glyph.char}:${index}`,
      className: "office-pixel-text__glyph",
      "aria-hidden": "true",
      style: glyph.style,
    })));
  };
}
