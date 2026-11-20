type Bounds = Pick<DOMRectReadOnly, "left" | "top" | "right" | "bottom">;
type Viewport = { width: number; height: number; x: number; y: number };

const GUTTER = 12;
const GAP = 8;

export function panelPlacement(anchor: Bounds, viewport: Viewport, {
  width: desiredWidth, height: desiredHeight, dock = false,
}: { width: number; height: number; dock?: boolean }) {
  const { x, y, width: viewWidth, height: viewHeight } = viewport;
  const width = Math.min(desiredWidth, Math.max(1, viewWidth - 2 * GUTTER));
  const maxHeight = Math.min(desiredHeight, Math.max(1, viewHeight - 2 * GUTTER));
  const clamp = (n: number, start: number, end: number) => Math.max(start, Math.min(n, end));
  const right = x + viewWidth - GUTTER;
  const bottom = y + viewHeight - GUTTER;
  if (dock && viewWidth < 680) {
    return { width, maxHeight, left: x + (viewWidth - width) / 2, top: bottom - maxHeight };
  }
  const below = bottom - anchor.bottom - GAP;
  const above = anchor.top - y - GUTTER - GAP;
  const top = dock ? anchor.bottom - maxHeight
    : below >= maxHeight || below >= above ? anchor.bottom + GAP
    : anchor.top - GAP - maxHeight;
  return {
    width, maxHeight,
    left: clamp(dock ? anchor.right + GAP : anchor.left, x + GUTTER, right - width),
    top: clamp(top, y + GUTTER, bottom - maxHeight),
  };
}

export function visibleViewport(): Viewport {
  const v = window.visualViewport;
  return { width: v?.width ?? window.innerWidth, height: v?.height ?? window.innerHeight,
    x: v?.offsetLeft ?? 0, y: v?.offsetTop ?? 0 };
}
