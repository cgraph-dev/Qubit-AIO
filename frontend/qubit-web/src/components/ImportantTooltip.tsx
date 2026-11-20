import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Q5TTooltipPlate } from "./Q5TTooltipPlate";

/**
 * The larger Q5T plate is reserved for blocked/destructive actions where the
 * explanation itself matters (disabled restart/update, safety refusals).
 * Ordinary rail tooltips use the small menu plate instead.
 */
export function ImportantTooltip({
  active,
  title,
  detail,
  children,
}: {
  active: boolean;
  title: string;
  detail?: string;
  children: ReactNode;
}) {
  const trigger = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!active) setOpen(false);
  }, [active]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!active) return <>{children}</>;

  const rect = trigger.current?.getBoundingClientRect();
  const width = 300;
  const left = rect ? Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - width - 8)) : 8;
  const above = rect ? rect.top > 190 : false;
  const top = rect ? (above ? rect.top - 8 : rect.bottom + 8) : 8;

  return (
    <>
      <span
        ref={trigger}
        className="q5t-important-trigger"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocusCapture={() => setOpen(true)}
        onBlurCapture={() => setOpen(false)}
      >
        {children}
      </span>
      {open && createPortal(
        <div
          className="q5t-tooltip-shell q5t-important-tooltip"
          role="tooltip"
          style={{
            position: "fixed",
            left,
            top,
            width,
            zIndex: 2147483000,
            pointerEvents: "none",
            transform: above ? "translateY(-100%)" : undefined,
          }}
        >
          <Q5TTooltipPlate title={title}>{detail ?? null}</Q5TTooltipPlate>
        </div>,
        document.body,
      )}
    </>
  );
}
