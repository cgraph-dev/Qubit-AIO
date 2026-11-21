import type { CSSProperties } from "react";
import { SakugaLoading } from "@/sakuga/ui";

/**
 * Manga /qubit-chat loading mark.
 *
 * One Sakuga activity indicator only. The previous native mark stacked a gold
 * atom SVG, the Sakuga bar and a second indeterminate sweep, so a single load
 * showed three animations at once. Centering and padding still come from the
 * existing `.qchat__manga-loader` rule; the atom art is no longer rendered.
 */
export function MangaChatLoader({
  label = "Loading conversation…",
  size = "md",
}: { label?: string; size?: "md" | "inline" }) {
  // Streaming already has an aria-live message on its parent, so the inline
  // mark keeps the bar but not a second label/announcement.
  if (size === "inline") {
    return (
      <span className="qchat__manga-loader-inline" aria-hidden="true">
        <SakugaLoading
          label="Qubit is thinking…"
          showLabel={false}
          style={{ "--sk-progress-width": "96px" } as CSSProperties}
        />
      </span>
    );
  }
  return (
    <div className="qchat__manga-loader" data-testid="manga-chat-loader">
      <SakugaLoading label={label} />
    </div>
  );
}
