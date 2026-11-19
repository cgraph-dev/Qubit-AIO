/**
 * Separated Manga studio artwork.
 * Every visual is an independent element: no baked-in text, no duplicated
 * status-panel frames, no interaction handlers, and no changes to the PTY.
 *
 * High-resolution approved PNG cutouts may replace the SVGs at these paths
 * without changing React, but SVG files provide an always-available fallback.
 */
export function MangaStudioTitleOrnaments() {
  return (
    <>
      {/* Original approved gold-bar asset is still styled by the existing
          qm-manga-charge-cells rule; no extra bars or plate background. */}
      <span className="qm-manga-charge-cells" aria-hidden="true">
        <i /><i /><i />
      </span>
      <span className="qm-studio-title-corner" aria-hidden="true">
        <img
          className="qm-studio-title-corner__chrome"
          src="/manga-ui/manga-corner-reflection.svg"
          alt=""
          draggable={false}
        />
        <img
          className="qm-studio-title-corner__star"
          src="/manga-ui/studio-star.png"
          alt=""
          draggable={false}
        />
      </span>
    </>
  );
}

/** Replaces only the Manga brand's cat image, not the Qubit mark or label. */
export function MangaStudioBrandAtom() {
  return (
    <img
      className="qm-brand-cat qm-studio-brand-atom"
      src="/manga-ui/studio-atom.png"
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
}

/** Optional standalone decoration for a designated panel; never auto-injected. */
export function MangaStudioImpactStrokes({ className = "" }: { className?: string }) {
  return (
    <img
      className={["qm-studio-impact-strokes", className].filter(Boolean).join(" ")}
      src="/manga-ui/studio-strokes.png"
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
}
