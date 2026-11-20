import { useEffect, useRef } from "react";
import { firstFocusable, trapTabKey } from "@/lib/focus-trap";

type ModalLayer = { focus: () => void; element: HTMLDivElement };
const layers: ModalLayer[] = [];
let scrollLock: { overflow: string; opener: HTMLElement | null } | null = null;

function lockDocument(opener: HTMLElement | null) {
  scrollLock = { overflow: document.body.style.overflow, opener };
  document.body.style.overflow = "hidden";
}

function unlockDocument() {
  const lock = scrollLock;
  if (!lock) return;
  scrollLock = null;
  document.body.style.overflow = lock.overflow;
  if (lock.opener?.isConnected) lock.opener.focus();
}

/**
 * Hook that adds standard modal behaviors when `open` is true:
 * - Only the top dialog handles Escape and focus, including nested portals
 * - Body scroll is locked
 * - Tab / Shift+Tab are trapped inside the dialog
 * - Focus moves into the dialog on open and is restored on close
 *
 * Returns a ref to attach to the modal container.
 *
 * `initialFocusSelector` lets a dialog pick a specific control (e.g. the
 * destructive confirm button); otherwise the first tabbable element wins. An
 * element that already owns focus inside the dialog (an `autoFocus` input) is
 * left alone so opening never steals the caret from the field the user needs.
 */
export function useModalBehavior({
  open,
  onClose,
  initialFocusSelector,
}: {
  open: boolean;
  onClose: () => void;
  initialFocusSelector?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const container = containerRef.current;
    if (!container) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const layer: ModalLayer = {
      element: container,
      focus: () => {
        const preferred = initialFocusSelector
          ? container.querySelector<HTMLElement>(initialFocusSelector)
          : null;
        (preferred ?? firstFocusable(container))?.focus();
      },
    };
    const isTop = () => layers.at(-1) === layer;
    if (layers.length === 0) lockDocument(previousFocus);
    layers.push(layer);
    if (!container.contains(previousFocus)) layer.focus();

    const onKey = (event: KeyboardEvent) => {
      if (!isTop() || event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      } else {
        trapTabKey(container, event);
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (isTop() && !container.contains(event.target as Node | null)) {
        layer.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      const wasTop = isTop();
      layers.splice(layers.indexOf(layer), 1);

      const remaining = layers.at(-1);
      if (!remaining) {
        unlockDocument();
      } else if (wasTop) {
        if (remaining.element.contains(previousFocus)) previousFocus?.focus?.();
        else remaining.focus();
      }
    };
  }, [open, initialFocusSelector]);

  return containerRef;
}
