import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import { emitFxAt } from "@/sakuga/ui";

/**
 * The rail's flyout label.
 *
 * Portalled to `document.body` with `position: fixed`, so it is never clipped
 * by the sidebar's own `overflow-y: auto` or by a transformed ancestor — the
 * whole point of a rail tooltip is that it lives OUTSIDE the rail.
 *
 * Placement rules, in order of preference:
 *   1. to the right of the rail's outer edge (`#app-sidebar`), vertically
 *      centred on the anchor row;
 *   2. flipped to the LEFT of the rail when the right side would overflow the
 *      viewport (narrow window, rail on the right half);
 *   3. clamped to the viewport vertically, so a tooltip on the first or last
 *      row never runs off the top/bottom edge.
 *
 * A "warm" ref keeps it from re-animating while the pointer travels straight
 * from one row to the next — the flicker that makes icon rails feel cheap.
 */
export function SidebarTooltip({ anchor, label, warmRef }: SidebarTooltipProps) {
  const [isWarm] = useState(() => {
    if (!warmRef) return false;
    return Date.now() - warmRef.current < 300;
  });

  useEffect(() => {
    if (!warmRef) return;
    warmRef.current = Date.now();
    return () => {
      if (warmRef) warmRef.current = Date.now();
    };
  }, [warmRef]);

  // The rail flyout is a Sakuga bubble now: the same white card, ink border,
  // lavender shadow and a small pop from the anchor when it opens.
  useEffect(() => {
    void emitFxAt("pop", anchor, { scale: 0.45 });
  }, [anchor]);

  const rect = anchor.getBoundingClientRect();
  const sidebar = document.getElementById("app-sidebar")?.getBoundingClientRect();
  const gap = 10;
  const railRight = sidebar && sidebar.width > 0 ? sidebar.right : rect.right;
  const railLeft = sidebar && sidebar.width > 0 ? sidebar.left : rect.left;

  // Flip left when the card would leave the viewport on the right.
  const CARD_WIDTH = 232;
  const fitsRight = railRight + gap + CARD_WIDTH <= window.innerWidth - 8;
  const placement: "right" | "left" = fitsRight ? "right" : "left";

  const [title, ...rest] = label.split(" — ");
  const detail = rest.length > 0 ? rest.join(" — ") : undefined;

  // Clamp vertically: the card is ~2 lines tall at most.
  const manga = document.documentElement.dataset.qubitManga === "on";
  const EDGE = manga ? 62 : 40;
  const centreY = rect.top + rect.height / 2;
  const top = Math.min(Math.max(centreY, EDGE), window.innerHeight - EDGE);

  return createPortal(
    <div
      className={cn(
        "qagent-sidebar__tooltip",
        "pointer-events-none fixed z-[100]",
        "min-w-[7rem] max-w-[15rem]",
        "rounded-[var(--qrail-radius,0.4rem)] border",
      )}
      data-placement={placement}
      data-sakuga-tip="1"
      role="tooltip"
      style={{
        top,
        transform: "translateY(-50%)",
        ...(manga ? {} : {
          background: "var(--sk-card, #fff)",
          color: "var(--sk-ink, #1d1a4a)",
          border: "2.5px solid var(--sk-ink, #1d1a4a)",
          borderRadius: 12,
          boxShadow: "5px 6px 0 var(--sk-soft, #b8b3ff)",
          padding: "9px 13px",
          font: "600 13px/1.35 -apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif",
        }),
        ...(placement === "right"
          ? { left: railRight + gap }
          : { right: window.innerWidth - railLeft + gap }),
        animation: isWarm ? "none" : undefined,
      }}
    >
      <span className="qagent-sidebar__tooltip-title" style={manga ? undefined : { color: "var(--sk-ink, #1d1a4a)" }}>{title}</span>
      {detail && <span className="qagent-sidebar__tooltip-detail" style={manga ? undefined : { color: "var(--sk-dim, #8580b0)", fontWeight: 500 }}>{detail}</span>}
    </div>,
    document.body,
  );
}

type TooltipWarmRef = React.RefObject<number>;

interface SidebarTooltipProps {
  anchor: HTMLElement;
  /** `Label` or `Label — one-line explanation`. */
  label: string;
  warmRef?: TooltipWarmRef;
}
