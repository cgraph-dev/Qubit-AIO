import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

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
  const EDGE = 40;
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
      role="tooltip"
      style={{
        top,
        transform: "translateY(-50%)",
        ...(placement === "right"
          ? { left: railRight + gap }
          : { right: window.innerWidth - railLeft + gap }),
        animation: isWarm ? "none" : undefined,
      }}
    >
      <span className="qagent-sidebar__tooltip-title">{title}</span>
      {detail && <span className="qagent-sidebar__tooltip-detail">{detail}</span>}
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
