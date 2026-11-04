/**
 * Qubit ambient backdrop — a looping video behind the whole dashboard.
 *
 * The source clip is a dark chalkboard of handwritten maths — high-contrast
 * white strokes on near-black. The shipped asset is that clip downscaled to
 * 1600px wide and softened (sigma 11) just enough that the chalk stops reading
 * as text competing with the UI, while keeping its structure and glow. A lighter
 * blur is deliberate: the previous sigma-18 pass destroyed the detail and read
 * as mush. Bright chalk clusters still survive the blur, so a theme-tinted scrim
 * sits on top and clamps the frame toward the skin's own canvas — that scrim is
 * what guarantees contrast on any theme, not the video's own darkness.
 *
 * Cost control: one muted, inline element. It does not autoplay under
 * `prefers-reduced-motion` (a single still frame is shown), and the panel
 * renders nothing behind the video if it fails to load, so the shell keeps
 * working unchanged.
 */
import { memo, useEffect, useState } from "react";

/** The derived backdrop asset: the source clip, blurred and darkened. */
export const BACKDROP_VIDEO_SRC = "/media/qubit-backdrop.mp4";

/**
 * First-frame still of the same clip, used as the video's `poster`.
 *
 * The backdrop used to ship `preload="auto"`, so a cold load pulled 4 MB of
 * decorative video while the entry JS was still downloading. `preload="metadata"`
 * plus this poster keeps the first paint cheap: the poster paints instantly and
 * the video still starts by itself, so what the operator sees is unchanged.
 */
export const BACKDROP_POSTER_SRC = "/media/qubit-backdrop-poster.jpg";

/**
 * Scrim strength, per polarity.
 *
 * The scrim is painted in the theme's OWN canvas colour, so it already adapts;
 * the two strengths only decide how much of the bokeh survives. Dark skins keep
 * the video legible under a lighter scrim; light skins need it much heavier or
 * the canvas stops reading as light at all.
 *
 * Polarity is read from the theme engine's `--qplate-contrast` custom property
 * (it is `#ffffff` on dark themes, `#000000` on light), which the theme provider
 * already maintains — the app has no other light/dark signal to pass in.
 *
 * The dark scrim is stronger than a flat ambient clip needs: the backdrop is
 * now a high-contrast illustration with near-white highlights, so anything less
 * lets those highlights punch through the panels and fight body text.
 */
export const BACKDROP_SCRIM_DARK = 0.74;
export const BACKDROP_SCRIM_LIGHT = 0.93;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Scrim opacity for the active canvas.
 *
 * `--qplate-contrast` is set by the theme provider next to the palette it
 * belongs to, so reading it here keeps the backdrop in step with the theme
 * without threading theme state through the shell. A missing value (older
 * build, reduced DOM) falls back to the dark strength — the safe side.
 */
export function scrimOpacity(contrast: string | undefined): number {
  return contrast?.trim().toLowerCase() === "#000000"
    ? BACKDROP_SCRIM_LIGHT
    : BACKDROP_SCRIM_DARK;
}

export function QubitAmbient() {
  const still = prefersReducedMotion();
  const [contrast, setContrast] = useState<string | undefined>(() =>
    typeof document === "undefined"
      ? undefined
      : getComputedStyle(document.documentElement)
          .getPropertyValue("--qplate-contrast")
          .trim() || undefined,
  );

  // The theme provider swaps the palette (and this property with it) at runtime,
  // so a theme switch has to re-read it or a light skin keeps the dark scrim.
  useEffect(() => {
    const root = document.documentElement;
    const read = () =>
      setContrast(
        getComputedStyle(root).getPropertyValue("--qplate-contrast").trim() ||
          undefined,
      );
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true });
    return () => observer.disconnect();
  }, []);

  const scrim = scrimOpacity(contrast);

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
      data-qubit-backdrop=""
    >
      <video
        autoPlay={!still}
        className="absolute inset-0 h-full w-full object-cover"
        disablePictureInPicture
        loop
        muted
        playsInline
        preload="metadata"
        poster={BACKDROP_POSTER_SRC}
        src={BACKDROP_VIDEO_SRC}
        // Desaturate and dim the clip itself, before the scrim. The scrim alone
        // only lowers overall luminance; at 74% it flattens the artwork into
        // grey mud while bright highlights still bleed through. Pushing the
        // saturation down here keeps the panels' colour hierarchy intact and
        // leaves the backdrop as texture rather than artwork competing with it.
        style={{
          filter: `saturate(${still ? 0.55 : 0.42}) brightness(0.62) contrast(1.05)`,
        }}
      />
      {/* Scrim: the skin's canvas over the video. Tinted from --background-base
          so every theme clamps it toward its OWN surface instead of a fixed
          navy that would be theme-blind. */}
      <div
        className="absolute inset-0"
        style={{ background: "var(--background-base)", opacity: scrim }}
        data-backdrop-scrim=""
      />
    </div>
  );
}

export const QubitAmbientMemo = memo(QubitAmbient);
