const BOARD = ".qubit-kanban";
const BUBBLE_CLASS = "qm-kanban-tip";

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function isInsideBoard(node: Element | null): boolean {
  return !!node?.closest(BOARD);
}

function triggerFrom(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest<HTMLElement>("[title]");
  if (!el || !isInsideBoard(el) || el.closest(".qsk-hover-tip")) return null;
  return el;
}

/**
 * Turns plain `title` tooltips inside the Kanban board into one fixed,
 * portaled Manga bubble. Returns the cleanup.
 */
export function installKanbanTooltips(): () => void {
  let trigger: HTMLElement | null = null;
  let bubble: HTMLDivElement | null = null;
  let label: HTMLSpanElement | null = null;

  const position = () => {
    if (!trigger || !bubble) return;
    const rect = trigger.getBoundingClientRect();
    const width = bubble.offsetWidth;
    const height = bubble.offsetHeight;
    const placement = rect.top - height - 10 >= 8 ? "top" : "bottom";
    const top = placement === "top" ? rect.top - height - 10 : rect.bottom + 10;
    const center = rect.left + rect.width / 2;
    const maxLeft = Math.max(8, window.innerWidth - width - 8);
    const left = clamp(center - width / 2, 8, maxLeft);
    bubble.dataset.placement = placement;
    bubble.style.left = `${Math.round(left)}px`;
    bubble.style.top = `${Math.round(clamp(top, 8, Math.max(8, window.innerHeight - height - 8)))}px`;
    bubble.style.setProperty("--qm-tip-arrow", `${Math.round(clamp(center - left, 12, width - 12))}px`);
  };

  const hide = () => {
    if (trigger) {
      const stored = trigger.getAttribute("data-qm-tip");
      if (stored !== null) {
        trigger.removeAttribute("data-qm-tip");
        if (!trigger.hasAttribute("title")) trigger.setAttribute("title", stored);
      }
    }
    trigger = null;
    label = null;
    bubble?.remove();
    bubble = null;
  };

  const show = (el: HTMLElement) => {
    const title = el.getAttribute("title");
    if (!title || trigger === el) return;
    hide();
    el.removeAttribute("title");
    el.setAttribute("data-qm-tip", title);
    trigger = el;
    bubble = document.createElement("div");
    bubble.className = BUBBLE_CLASS;
    bubble.setAttribute("role", "tooltip");
    label = document.createElement("span");
    label.textContent = title;
    bubble.append(label);
    document.body.append(bubble);
    position();
  };

  const onPointerOver = (event: Event) => {
    const el = triggerFrom(event.target);
    if (el) show(el);
  };

  const onPointerOut = (event: PointerEvent) => {
    if (!trigger) return;
    const next = event.relatedTarget;
    if (next instanceof Node && trigger.contains(next)) return;
    hide();
  };

  const onFocusIn = (event: Event) => {
    const el = triggerFrom(event.target);
    if (el) show(el);
  };

  const onFocusOut = (event: FocusEvent) => {
    if (!trigger) return;
    const next = event.relatedTarget;
    if (next instanceof Node && trigger.contains(next)) return;
    hide();
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") hide();
  };

  const onViewportChange = () => hide();

  const observer = new MutationObserver(() => {
    if (trigger && !trigger.isConnected) hide();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  document.addEventListener("pointerover", onPointerOver, true);
  document.addEventListener("pointerout", onPointerOut, true);
  document.addEventListener("focusin", onFocusIn, true);
  document.addEventListener("focusout", onFocusOut, true);
  document.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("scroll", onViewportChange, true);
  window.addEventListener("resize", onViewportChange);

  return () => {
    hide();
    observer.disconnect();
    document.removeEventListener("pointerover", onPointerOver, true);
    document.removeEventListener("pointerout", onPointerOut, true);
    document.removeEventListener("focusin", onFocusIn, true);
    document.removeEventListener("focusout", onFocusOut, true);
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("scroll", onViewportChange, true);
    window.removeEventListener("resize", onViewportChange);
  };
}
