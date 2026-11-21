import { FxButton } from "@/sakuga/FxButton";
import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { panelPlacement, visibleViewport } from "./panelPlacement";
import { PickerPanel } from "./PickerPanel";
import { useMangaLayerActive } from "./useMangaLayerActive";

/**
 * One replacement panel for both sidebar pickers, including on mobile.
 *
 * In Manga it is the same PickerPanel as the /qubit-chat voice and reasoning
 * selects: plum surface, gold star, yellow-outlined filter and a radio list.
 * Every other theme keeps the neutral dialog so the sidebar still matches its
 * palette. Both branches own a real filter, Escape/outside-click dismissal and
 * focus restoration.
 */
export function UtilityPickerPanel({
  id, title, closeLabel, onClose, anchor, searchable = true, filterLabel, filterPlaceholder, children, footer,
}: {
  id: string;
  title: string;
  closeLabel: string;
  onClose: () => void;
  anchor: { readonly current: HTMLElement | null };
  searchable?: boolean;
  filterLabel?: string;
  filterPlaceholder?: string;
  children: (filter: string) => ReactNode;
  footer?: ReactNode;
}) {
  const manga = useMangaLayerActive();
  const [filter, setFilter] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const label = filterLabel ?? `Filter ${title}`;

  const clearAndClose = useCallback(() => {
    setFilter("");
    onClose();
  }, [onClose]);

  useLayoutEffect(() => {
    if (manga) return;
    const dialog = dialogRef.current;
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
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    observer?.observe(dialog);
    const viewport = window.visualViewport;
    window.addEventListener("resize", place);
    viewport?.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    const focusFrame = window.setTimeout(() => filterRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(focusFrame);
      observer?.disconnect();
      window.removeEventListener("resize", place);
      viewport?.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
      dialog.close();
      trigger?.focus();
    };
  }, [anchor, manga]);

  if (manga) {
    return (
      <PickerPanel
        id={id}
        label={title}
        closeLabel={closeLabel}
        onClose={clearAndClose}
        anchor={anchor}
        dock
        showClose
        filter={filter}
        onFilterChange={setFilter}
        filterLabel={label}
        filterPlaceholder={filterPlaceholder}
        footer={footer}
      >
        {searchable ? children(filter) : children("")}
      </PickerPanel>
    );
  }

  return createPortal(
    <dialog
      id={id}
      ref={dialogRef}
      data-qm-picker=""
      data-qm-panel=""
      aria-labelledby={`${id}-title`}
      className="qm-utility-picker"
      onCancel={(event) => { event.preventDefault(); clearAndClose(); }}
      onKeyDown={(event) => { if (event.key === "Escape") event.stopPropagation(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect();
        if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) clearAndClose();
      }}
    >
      <header className="qm-utility-picker__head">
        <h2 id={`${id}-title`}>{title}</h2>
        <FxButton type="button" autoFocus aria-label={closeLabel} onClick={clearAndClose}><X aria-hidden size={18} /></FxButton>
      </header>
      {searchable && (
        <input
          ref={filterRef}
          className="qm-utility-picker__filter"
          aria-label={label}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={filterPlaceholder ?? "Filter…"}
        />
      )}
      <div className="qm-utility-picker__body">
        <div {...{ "data-qm-picker-list": "" }}>{searchable ? children(filter) : children("")}</div>
        {footer}
      </div>
    </dialog>,
    document.body,
  );
}
