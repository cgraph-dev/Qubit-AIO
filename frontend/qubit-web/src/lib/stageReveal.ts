/** Stagger the sidebar entries once, without animating the routed viewport. */
import gsap from "gsap";

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
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
