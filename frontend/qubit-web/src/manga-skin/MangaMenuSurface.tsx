import { createPortal } from "react-dom";
import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { panelPlacement, visibleViewport } from "./panelPlacement";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";

type MangaMenuSurfaceProps = {
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  className?: string;
  label: string;
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
  anchorRef, children, className = "", label,
  onClose, menuRef, dataUseAsMenu = false,
}: MangaMenuSurfaceProps) {
  const active = useMangaLayerActive();
  const ownRef = useRef<HTMLDivElement>(null);
  const target = menuRef ?? ownRef;
  const [position, setPosition] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!active) return;
    const element = target.current;
    if (!element) return;
    const place = () => {
      const box = anchorRef.current?.getBoundingClientRect();
      if (!box) return;
      const viewport = visibleViewport();
      const space = panelPlacement(box, viewport, { width: 340, height: 540 });
      const body = element.querySelector(".qm-panel-scroll");
      const fit = panelPlacement(box, viewport, {
        width: space.width,
        height: Math.min(space.maxHeight, (body?.scrollHeight ?? 0) + element.offsetHeight - (body?.clientHeight ?? 0)),
      });
      setPosition({ position: "fixed", ...space, top: fit.top, right: "auto", bottom: "auto" });
    };

    place();
    const frame = requestAnimationFrame(place);
    window.addEventListener("resize", place);
    window.visualViewport?.addEventListener("resize", place);
    window.visualViewport?.addEventListener("scroll", place);
    window.addEventListener("scroll", place, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("scroll", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [active, anchorRef, target]);

  const items = () => Array.from(target.current?.querySelectorAll<HTMLElement>(
    '[role="menuitem"]:not([disabled]):not([aria-disabled="true"])',
  ) ?? []);
  function focusControl(item?: HTMLElement | null) {
    (item?.shadowRoot?.querySelector<HTMLElement>("button") ?? item)?.focus();
  }

  useLayoutEffect(() => {
    if (!active) return;
    const first = target.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled]):not([aria-disabled="true"])');
    focusControl(first);
  }, [active, target]);

  const surface = (
    <div
      ref={target}
      role="menu"
      data-qm-panel={active ? "" : undefined}
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
          focusControl(anchorRef.current?.querySelector<HTMLElement>('[aria-haspopup="menu"]'));
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const choices = items();
          if (!choices.length) return;
          const current = choices.indexOf(document.activeElement as HTMLElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1
            : (current + (event.key === "ArrowDown" ? 1 : current < 0 ? 0 : -1) + choices.length) % choices.length;
          focusControl(choices[next]);
        }
      }}
    >
      {active ? <div className="qm-panel-scroll" role="presentation">{children}</div> : children}
    </div>
  );
  return active && typeof document !== "undefined" ? createPortal(surface, document.body) : surface;
}
