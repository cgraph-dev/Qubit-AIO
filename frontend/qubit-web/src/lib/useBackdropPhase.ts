import { useEffect, useRef } from "react";

/**
 * Start a looping backdrop video at a deterministic phase offset.
 *
 * Both plates render the SAME file (the sidebar backdrop), and two <video>
 * elements on one page loop on the same duration — so they drift into lockstep
 * within a few seconds and the two rails visibly pulse together. Playing the
 * same clip at different points of its own timeline is what makes each plate
 * read as its own ambient layer.
 *
 * Two things are needed, and only the first is obvious:
 *
 *   1. jump to the offset once metadata is readable (`seeked` fires after);
 *   2. re-apply it after EVERY loop. A looping <video> wraps back to 0 on its
 *      own, which would drop the offset after one cycle and re-sync the rails —
 *      the bug this hook exists to fix. The wrap is caught by watching for a
 *      backward jump in `currentTime`, with the `ended` event as a fallback for
 *      engines that stop rather than wrap.
 *
 * `rate` is a separate, additive lever: slightly different playback speeds make
 * the two clips drift apart instead of merely starting apart, so they never
 * settle back into phase. Kept near 1 so the motion still reads as ambient
 * rather than sped up or slowed down.
 */
export function useBackdropPhase(
  offsetSeconds: number,
  { rate = 1 }: { rate?: number } = {},
) {
  const ref = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;

    // Respect the OS setting: the stylesheet hides the clip entirely here.
    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }

    if (rate !== 1) video.playbackRate = rate;

    let cancelled = false;

    const applyOffset = () => {
      if (cancelled) return;
      const duration = video.duration;
      if (!Number.isFinite(duration) || duration <= 0) return;
      video.currentTime = offsetSeconds % duration;
    };

    // Seek once metadata exists; `loadedmetadata` covers an already-buffered
    // element and `seeked` re-arms after the browser's own seek resolution.
    if (video.readyState >= 1) {
      applyOffset();
    } else {
      video.addEventListener("loadedmetadata", applyOffset, { once: true });
    }

    // Re-arm the offset on every wrap. `timeupdate` is the only event that
    // fires reliably across a loop boundary in every engine; the backward
    // jump is what distinguishes a wrap from ordinary playback.
    let last = 0;
    const onTimeUpdate = () => {
      const now = video.currentTime;
      if (now + 0.5 < last) applyOffset();
      last = now;
    };
    const onEnded = () => {
      applyOffset();
      void video.play().catch(() => {});
    };

    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("ended", onEnded);

    // A back/forward-cache restore can reset the element to 0; re-apply.
    const onPageShow = () => applyOffset();
    window.addEventListener("pageshow", onPageShow);

    return () => {
      cancelled = true;
      video.removeEventListener("loadedmetadata", applyOffset);
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("ended", onEnded);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, [offsetSeconds, rate]);

  return ref;
}
