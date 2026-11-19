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
  if (document.documentElement.dataset.qubitManga === "on") {
    // Manga panels enter as an inked page reveal, not a blurry dashboard fade.
    // clipPath is cleared when finished so portaled popups and scroll work.
    gsap.fromTo(
      el,
      { opacity: 0, x: -14, clipPath: "inset(0 100% 0 0)" },
      {
        opacity: 1,
        x: 0,
        clipPath: "inset(0 0 0 0)",
        duration: 0.46,
        ease: "back.out(1.25)",
        clearProps: "transform,clipPath",
        overwrite: "auto",
      },
    );
    return;
  }
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
  gsap.killTweensOf(rows);
  if (document.documentElement.dataset.qubitManga === "on") {
    gsap.fromTo(
      rows,
      { opacity: 0, x: -12, rotation: -1.6 },
      {
        opacity: 1,
        x: 0,
        rotation: 0,
        duration: 0.36,
        ease: "back.out(1.65)",
        stagger: 0.025,
        clearProps: "transform",
        overwrite: "auto",
      },
    );
    return;
  }
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
