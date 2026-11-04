import { useEffect, useRef } from "react";
import { firstFocusable, trapTabKey } from "@/lib/focus-trap";

/**
 * Hook that adds standard modal behaviors when `open` is true:
 * - Escape key calls `onClose`
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
    const prevActive = document.activeElement as HTMLElement | null;
    const alreadyInside = Boolean(
      container && prevActive && container.contains(prevActive),
    );

    if (!alreadyInside) {
      const preferred = initialFocusSelector
        ? container?.querySelector<HTMLElement>(initialFocusSelector)
        : null;
      (preferred ?? firstFocusable(container))?.focus?.();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      trapTabKey(container, e);
    };

    // A pointer click on the backdrop or a programmatic focus move outside
    // the dialog must not leave a keyboard user stranded behind it.
    const onFocusIn = (e: FocusEvent) => {
      if (!container) return;
      const target = e.target as Node | null;
      if (target && !container.contains(target)) {
        if (initialFocusSelector) {
          container.querySelector<HTMLElement>(initialFocusSelector)?.focus();
        } else {
          firstFocusable(container)?.focus();
        }
      }
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      document.body.style.overflow = prevOverflow;
      prevActive?.focus?.();
    };
  }, [open, initialFocusSelector]);

  return containerRef;
}
