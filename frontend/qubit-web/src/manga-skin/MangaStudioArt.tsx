/**
 * Separated Manga studio artwork.
 * Every visual is an independent element: no baked-in text, no duplicated
 * status-panel frames, no interaction handlers, and no changes to the PTY.
 *
 * Optimized approved PNG cutouts ship with the dashboard. Replacing them with
 * the optional high-resolution PNG pack requires no JSX or CSS changes.
 * Separate editable vector studies are retained alongside the PNGs.
 */
export function MangaStudioTitleOrnaments() {
  return (
    <>
      {/* Three independently animated bevelled studio bars.
          React renders no text or fake panel inside the artwork. */}
      <span className="qm-manga-charge-cells qm-studio-charge-bars" aria-hidden="true">
        {[0, 1, 2].map((bar) => (
          <img
            key={bar}
            className="qm-studio-charge-bar"
            src="/manga-ui/manga-gold-charge-bar.svg"
            alt=""
            draggable={false}
          />
        ))}
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
