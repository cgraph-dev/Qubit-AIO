import { emitFxAt, type FxCue } from "./ui";

/** Native surfaces retain the Sakuga cue without needing a second control. */
export function playLocalFx(cue: FxCue, target: Element, scale = .5) {
  if (document.hidden || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const rect = target.getBoundingClientRect();
  void Promise.resolve(emitFxAt(cue, target, {
    scale, x: rect.left + window.scrollX + rect.width / 2,
    y: rect.top + window.scrollY + rect.height / 2,
  })).catch(() => {});
}
