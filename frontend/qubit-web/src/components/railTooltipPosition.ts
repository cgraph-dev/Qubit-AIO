import type { visibleViewport } from "@/manga-skin/panelPlacement";

type Box = Pick<DOMRectReadOnly, "left" | "right" | "top" | "bottom">;

export function railTooltipPosition(anchor: Box, rail: Box, width: number, height: number, viewport: ReturnType<typeof visibleViewport>) {
  const edge = 12;
  const gap = 8;
  const minX = viewport.x + edge;
  const maxX = viewport.x + viewport.width - edge;
  const minY = viewport.y + edge;
  const maxY = viewport.y + viewport.height - edge;
  const centre = (anchor.top + anchor.bottom) / 2;
  const anchorVisible = anchor.bottom > Math.max(minY, rail.top) &&
    anchor.top < Math.min(maxY, rail.bottom) && anchor.right > minX && anchor.left < maxX;
  const right = rail.right + gap;
  const left = rail.left - gap - width;
  const placement = right + width <= maxX ? "right" : left >= minX ? "left" : null;
  if (!anchorVisible || !placement || height > maxY - minY) return null;
  return {
    placement, left: placement === "right" ? right : left,
    top: Math.min(Math.max(centre - height / 2, minY), maxY - height),
  };
}
