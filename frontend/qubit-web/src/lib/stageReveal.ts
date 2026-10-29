/**
 * GSAP staging for the dashboard shell.
 *
 * Two jobs, both transform/opacity only so no layout is recomputed:
 *
 * - `stageRoute(el)` — a short GSAP timeline that fades/lifts the routed
 *   content on navigation, so pages arrive instead of snapping.
 * - `stageChrome(root)` — staggers the sidebar nav rows once on mount.
 *
 * Both are no-ops under `prefers-reduced-motion` and when the element is
 * missing, so callers never need a guard.
 */
import gsap from "gsap";

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function stageRoute(el: HTMLElement | null): void {
  if (!el || reducedMotion()) return;
  gsap.killTweensOf(el);
  gsap.fromTo(
    el,
    { opacity: 0, y: 10, filter: "blur(3px)" },
    {
      opacity: 1,
      y: 0,
      filter: "blur(0px)",
      duration: 0.42,
      ease: "power3.out",
      clearProps: "filter,transform",
      overwrite: "auto",
    },
  );
}

export function stageChrome(root: HTMLElement | null): void {
  if (!root || reducedMotion()) return;
  const rows = root.querySelectorAll<HTMLElement>('[data-stage-row="nav"]');
  if (!rows.length) return;
  gsap.fromTo(
    rows,
    { opacity: 0, x: -8 },
    {
      opacity: 1,
      x: 0,
      duration: 0.34,
      ease: "power2.out",
      stagger: 0.018,
      clearProps: "transform",
      overwrite: "auto",
    },
  );
}
