import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { panelPlacement, visibleViewport } from "./panelPlacement";
import { useMangaLayerActive } from "./useMangaLayerActive";
import { cn } from "@/lib/utils";

/**
 * One Manga popover for every picker in the dashboard: the /qubit-chat voice
 * and reasoning selects, and the sidebar theme and language switchers.
 *
 * It is the panel the owner approved as the Manga reference: a plum surface
 * with the pink/white double border, gold star, filtered radio list and a
 * visible thin scrollbar. Keeping one shell here means a change to the panel
 * can never leave one of the four pickers on an older design.
 */
export interface PickerPanelProps {
  id: string;
  label: string;
  closeLabel?: string;
  onClose: () => void;
  /** Trigger container; the panel is placed against it, never inside it. */
  anchor: { readonly current: HTMLElement | null };
  /** Sidebar utilities dock beside the rail; selects sit under the trigger. */
  dock?: boolean;
  filter: string;
  onFilterChange: (value: string) => void;
  filterLabel: string;
  filterPlaceholder?: string;
  showClose?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}

export function PickerPanel({
  id,
  label,
  closeLabel,
  onClose,
  anchor,
  dock = false,
  filter,
  onFilterChange,
  filterLabel,
  filterPlaceholder = "Filter…",
  showClose = false,
  children,
  footer,
}: PickerPanelProps) {
  const panel = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const [placement, setPlacement] = useState<CSSProperties>({ position: "fixed", visibility: "hidden" });
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null);

  useLayoutEffect(() => {
    const trigger =
      anchor.current?.querySelector<HTMLElement>(".qm-utility-trigger, .qm-select-trigger") ??
      anchor.current;
    if (!trigger) return;
    triggerRef.current = trigger;
    setPortalHost(trigger.closest<HTMLElement>('[aria-modal="true"]') ?? document.body);

    const place = () => {
      const box = trigger.getBoundingClientRect();
      const rail = dock ? trigger.closest("#app-sidebar")?.getBoundingClientRect() : null;
      const anchorBounds = dock
        ? { left: box.left, right: rail?.right ?? box.right, top: box.top, bottom: box.bottom }
        : box;
      const viewport = visibleViewport();
      const layout = panelPlacement(
        anchorBounds,
        viewport,
        dock ? { width: 390, height: 590, dock: true } : { width: 360, height: 420 },
      );
      setPlacement({
        position: "fixed",
        width: layout.width,
        maxHeight: layout.maxHeight,
        left: layout.left,
        top: layout.top,
        visibility: "visible",
      });
    };

    place();
    const selected = panel.current?.querySelector<HTMLElement>('[role="option"][aria-selected="true"]');
    selected?.scrollIntoView?.({ block: "nearest" });
    const focusFrame = window.setTimeout(() => filterRef.current?.focus(), 0);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    if (panel.current) observer?.observe(panel.current);
    const viewport = window.visualViewport;
    window.addEventListener("resize", place);
    viewport?.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    return () => {
      window.clearTimeout(focusFrame);
      observer?.disconnect();
      window.removeEventListener("resize", place);
      viewport?.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
      trigger.focus();
    };
  }, [anchor, dock, portalHost]);

  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || triggerRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [onClose]);

  const options = () =>
    Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? []);

  if (!portalHost) return null;

  return createPortal(
    <div
      ref={panel}
      id={id}
      className="qm-select-popover qm-picker-panel"
      role="dialog"
      data-qm-panel=""
      aria-label={label}
      style={placement}
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (next && !event.currentTarget.contains(next) && next !== triggerRef.current) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
          return;
        }
        if (event.target === filterRef.current && event.key === "ArrowDown") {
          event.preventDefault();
          options()[0]?.focus();
          return;
        }
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        const enabled = options();
        if (enabled.length === 0) return;
        const at = enabled.indexOf(event.target as HTMLButtonElement);
        if (at < 0 && event.key === "ArrowUp") {
          filterRef.current?.focus();
          return;
        }
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? enabled.length - 1
              : (at + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
        enabled[next]?.focus();
      }}
    >
      <div className="qm-select-popover__top">
        <span aria-hidden="true" className="qm-select-popover__spark">
          ✦
        </span>
        <input
          ref={filterRef}
          className="qm-select-filter"
          aria-label={filterLabel}
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder={filterPlaceholder}
        />
        {showClose && (
          <button
            type="button"
            className="qm-select-close"
            aria-label={closeLabel ?? label}
            onClick={onClose}
          >
            <X aria-hidden size={16} />
          </button>
        )}
      </div>
      <div className="qm-select-popover__options">
        <div role="listbox" aria-label={label}>
          {children}
        </div>
        {footer}
      </div>
    </div>,
    portalHost,
  );
}

/** One selectable row in a picker panel (gold radio in Manga, plain row otherwise). */
export function PickerRow({
  selected,
  onSelect,
  disabled,
  className,
  children,
  lang,
  dir,
}: {
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
  lang?: string;
  dir?: string;
}) {
  const manga = useMangaLayerActive();
  if (!manga) {
    return (
      <button
        type="button"
        aria-pressed={selected}
        data-qm-picker-choice=""
        disabled={disabled}
        className={cn("qm-picker-choice", className)}
        onClick={onSelect}
        lang={lang}
        dir={dir}
      >
        {children}
      </button>
    );
  }
  return (
    <button
      type="button"
      role="option"
      tabIndex={-1}
      aria-selected={selected}
      data-qm-picker-choice=""
      disabled={disabled}
      className={cn("qm-select-option", className)}
      onClick={onSelect}
      lang={lang}
      dir={dir}
    >
      <span className="qm-select-radio" aria-hidden="true">
        {selected ? "●" : ""}
      </span>
      {children}
    </button>
  );
}
