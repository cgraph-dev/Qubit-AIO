import { createPortal } from "react-dom";
import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";

type MangaMenuSurfaceProps = {
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  className?: string;
  role?: "menu" | "listbox";
  label?: string;
  onClose?: () => void;
  /** Optional shared ref for custom outside-click handlers in the owner. */
  menuRef?: RefObject<HTMLDivElement | null>;
  /** Marker for the Models page's legacy outside-click guard. */
  dataUseAsMenu?: boolean;
};

/**
 * Default themes preserve the original inline dropdown. In manga themes,
 * the same live content is rendered in a fixed, viewport-aware document.body
 * portal to escape scroll panels, clipped cards, and nested stacking contexts.
 */
export function MangaMenuSurface({
  anchorRef, children, className = "", role = "menu", label,
  onClose, menuRef, dataUseAsMenu = false,
}: MangaMenuSurfaceProps) {
  const active = useMangaLayerActive();
  const ownRef = useRef<HTMLDivElement>(null);
  const target = menuRef ?? ownRef;
  const [position, setPosition] = useState<CSSProperties>({});

  const place = useCallback(() => {
    const box = anchorRef.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.min(340, Math.max(160, window.innerWidth - 24));
    const measuredHeight = target.current?.scrollHeight ?? 300;
    const height = Math.min(measuredHeight, Math.max(160, window.innerHeight - 24));
    const right = Math.max(12, window.innerWidth - box.right);
    const left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12));
    const availableBelow = window.innerHeight - box.bottom - 12;
    const availableAbove = box.top - 12;
    const up = availableBelow < Math.min(height, 260) && availableAbove > availableBelow;
    const top = up
      ? Math.max(12, box.top - height - 6)
      : Math.min(window.innerHeight - 12 - height, box.bottom + 6);
    setPosition({
      position: "fixed",
      left: Math.max(12, Math.min(left, window.innerWidth - width - 12)),
      top: Math.max(12, top),
      width,
      maxHeight: Math.max(130, window.innerHeight - 24),
      right: "auto",
      bottom: "auto",
    });
    void right; // End-aligned layout is bounded by the same viewport algorithm.
  }, [anchorRef, target]);

  useLayoutEffect(() => {
    if (!active) return;
    place();
    const frame = requestAnimationFrame(place);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [active, place]);

  const surface = (
    <div
      ref={target}
      role={role}
      aria-label={label}
      data-use-as-menu={dataUseAsMenu ? "" : undefined}
      className={[
        className,
        active ? "qm-floating-menu" : "",
      ].filter(Boolean).join(" ")}
      style={active ? position : undefined}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose?.();
          anchorRef.current?.querySelector<HTMLElement>("button")?.focus();
        }
      }}
    >
      {children}
    </div>
  );
  return active && typeof document !== "undefined" ? createPortal(surface, document.body) : surface;
}
