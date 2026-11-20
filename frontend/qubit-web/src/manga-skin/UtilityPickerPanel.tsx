import { FxButton } from "@/sakuga/FxButton";
import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { panelPlacement, visibleViewport } from "./panelPlacement";

/** One replacement panel for both sidebar pickers, including on mobile. */
export function UtilityPickerPanel({ id, title, closeLabel, onClose, anchor, children }: {
  id: string;
  title: string;
  closeLabel: string;
  onClose: () => void;
  anchor: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const trigger = anchor.current?.querySelector<HTMLElement>(".qm-utility-trigger");
    const place = () => {
      if (!trigger) return;
      const bounds = trigger.getBoundingClientRect();
      const rail = trigger.closest("#app-sidebar")?.getBoundingClientRect();
      const anchorBounds = {
        left: bounds.left, right: rail?.right ?? bounds.right,
        top: bounds.top, bottom: bounds.bottom,
      };
      const viewport = visibleViewport();
      const layout = panelPlacement(anchorBounds, viewport, { width: 390, height: 590, dock: true });
      dialog.style.width = `${layout.width}px`;
      dialog.style.maxHeight = `${layout.maxHeight}px`;
      dialog.style.left = `${layout.left}px`;
      const measured = panelPlacement(anchorBounds, viewport, { width: layout.width, height: dialog.offsetHeight, dock: true });
      dialog.style.top = `${measured.top}px`;
    };
    dialog.showModal();
    place();
    const body = dialog.querySelector<HTMLElement>(".qm-utility-picker__body");
    const selected = body?.querySelector<HTMLElement>('[data-qm-picker-choice][aria-pressed="true"]');
    if (body && selected) {
      const bottom = selected.getBoundingClientRect().bottom - body.getBoundingClientRect().bottom;
      if (bottom > 0) body.scrollTop += bottom + 8;
    }
    const observer = new ResizeObserver(place);
    observer.observe(dialog);
    const viewport = window.visualViewport;
    window.addEventListener("resize", place);
    viewport?.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      viewport?.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
      dialog.close();
      trigger?.focus();
    };
  }, [anchor]);

  return createPortal(
    <dialog
      id={id}
      ref={ref}
      data-qm-picker=""
      data-qm-panel=""
      aria-labelledby={`${id}-title`}
      className="qm-utility-picker"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect();
        if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) onClose();
      }}
    >
      <header className="qm-utility-picker__head">
        <h2 id={`${id}-title`}>{title}</h2>
        <FxButton type="button" autoFocus aria-label={closeLabel} onClick={onClose}><X aria-hidden size={18} /></FxButton>
      </header>
      <div className="qm-utility-picker__body">{children}</div>
    </dialog>,
    document.body,
  );
}
