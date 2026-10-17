/**
 * The Qubit wordmark glyph.
 *
 * A qubit schematized as an orbit read edge-on: one nucleus, one steering
 * field, one particle in superposition. Deliberately NOT a letterform in a
 * rounded box — the box was the upstream-adjacent tile the brand retired.
 *
 * Colour comes from the caller (`currentColor`) so the mark survives every
 * theme; the one accent -- the particle that is both in and out of the
 * baseline -- is the only hard-coded hue and matches the Qubit teal.
 */
export function QubitMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 40 40"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient id="qubit-mark-accent" x1="6" y1="34" x2="34" y2="6" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#ffe6cb" />
        </linearGradient>
      </defs>

      {/* Steering field: two crossing orbits, no enclosing box. */}
      <ellipse
        cx="20"
        cy="20"
        rx="15.5"
        ry="6.6"
        stroke="currentColor"
        strokeOpacity=".55"
        strokeWidth="1.8"
        transform="rotate(-32 20 20)"
      />
      <ellipse
        cx="20"
        cy="20"
        rx="15.5"
        ry="6.6"
        stroke="currentColor"
        strokeOpacity=".28"
        strokeWidth="1.8"
        transform="rotate(32 20 20)"
      />

      {/* Nucleus. */}
      <circle cx="20" cy="20" r="3.4" fill="url(#qubit-mark-accent)" />

      {/* The measured particle. */}
      <circle cx="32.4" cy="11.6" r="2.9" fill="currentColor" />
      <circle cx="32.4" cy="11.6" r="5.6" stroke="currentColor" strokeOpacity=".3" strokeWidth="1.2" />
    </svg>
  );
}
