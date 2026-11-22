const BOARD = ".qubit-kanban";
const BUBBLE_CLASS = "qm-kanban-tip";

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function isInsideBoard(node: Element | null): boolean {
  return !!node?.closest(BOARD);
}

function restoreTitle(el: HTMLElement) {
  const stored = el.getAttribute("data-qm-tip");
  if (stored === null) return;
  el.removeAttribute("data-qm-tip");
  if (!el.hasAttribute("title")) el.setAttribute("title", stored);
}

function suppressTitle(el: HTMLElement): boolean {
  const title = el.getAttribute("title");
  if (title === null) return false;
  el.removeAttribute("title");
  el.setAttribute("data-qm-tip", title);
  return true;
}

/**
 * Turns plain `title` tooltips inside the Kanban board into one fixed,
 * portaled Manga bubble. A trigger that the bundle already wraps in
 * `.qsk-hover-tip` keeps that single bubble; its native ancestor title is
 * suppressed instead of opening a second tooltip. Returns the cleanup.
 */
export function installKanbanTooltips(): () => void {
  let portalTrigger: HTMLElement | null = null;
  let suppressed: HTMLElement | null = null;
  let bubble: HTMLDivElement | null = null;

  const position = () => {
    if (!portalTrigger || !bubble) return;
    const rect = portalTrigger.getBoundingClientRect();
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

  const hidePortal = () => {
    if (portalTrigger) restoreTitle(portalTrigger);
    portalTrigger = null;
    bubble?.remove();
    bubble = null;
  };

  const unsuppress = () => {
    if (suppressed) restoreTitle(suppressed);
    suppressed = null;
  };

  const suppress = (el: HTMLElement | null) => {
    if (suppressed === el) return;
    unsuppress();
    if (el && suppressTitle(el)) suppressed = el;
  };

  const showPortal = (el: HTMLElement) => {
    hidePortal();
    const title = el.getAttribute("title") ?? el.getAttribute("data-qm-tip");
    if (!title) return;
    if (el === suppressed) suppressed = null;
    if (el.hasAttribute("title")) suppressTitle(el);
    portalTrigger = el;
    bubble = document.createElement("div");
    bubble.id = BUBBLE_CLASS;
    bubble.className = BUBBLE_CLASS;
    bubble.setAttribute("role", "tooltip");
    const label = document.createElement("span");
    label.textContent = title;
    bubble.append(label);
    document.body.append(bubble);
    position();
  };

  const enter = (target: Element) => {
    if (!isInsideBoard(target)) return;
    if (target.closest(".qsk-hover-tip")) {
      hidePortal();
      const titled =
        target.closest<HTMLElement>("[title]") ??
        target.closest<HTMLElement>("[data-qm-tip]");
      if (titled && isInsideBoard(titled)) suppress(titled);
      else unsuppress();
      return;
    }
    unsuppress();
    const el = target.closest<HTMLElement>("[title]");
    if (!el || !isInsideBoard(el)) {
      hidePortal();
      return;
    }
    showPortal(el);
  };

  const leave = (next: EventTarget | null) => {
    if (portalTrigger && !(next instanceof Node && portalTrigger.contains(next))) {
      hidePortal();
    }
    if (suppressed && !(next instanceof Node && suppressed.contains(next))) {
      unsuppress();
    }
  };

  const onPointerOver = (event: Event) => {
    if (!(event.target instanceof Element)) return;
    enter(event.target);
  };

  const onPointerOut = (event: PointerEvent) => leave(event.relatedTarget);

  const onFocusIn = (event: Event) => {
    if (!(event.target instanceof Element)) return;
    enter(event.target);
  };

  const onFocusOut = (event: FocusEvent) => leave(event.relatedTarget);

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      hidePortal();
      unsuppress();
    }
  };

  const onViewportChange = () => hidePortal();

  // The column header repeats its help text in the visible `.qubit-kanban-
  // column-sub` line. Its native `title` also fires while the pointer crosses
  // the header on the way to the "+", producing a second OS tooltip; strip it
  // before it can arm, and re-strip as columns re-render.
  const stripHeaderTitles = () => {
    document
      .querySelectorAll<HTMLElement>(".qubit-kanban .qubit-kanban-column-header[title]")
      .forEach((el) => {
        const title = el.getAttribute("title");
        if (!title) return;
        el.removeAttribute("title");
        el.setAttribute("data-qm-header-tip", title);
      });
  };

  const restoreHeaderTitles = () => {
    document.querySelectorAll<HTMLElement>("[data-qm-header-tip]").forEach((el) => {
      const title = el.getAttribute("data-qm-header-tip");
      if (title === null) return;
      el.removeAttribute("data-qm-header-tip");
      if (!el.hasAttribute("title")) el.setAttribute("title", title);
    });
  };

  stripHeaderTitles();

  const observer = new MutationObserver(() => {
    if (portalTrigger && !portalTrigger.isConnected) hidePortal();
    if (suppressed && !suppressed.isConnected) unsuppress();
    stripHeaderTitles();
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
    hidePortal();
    unsuppress();
    restoreHeaderTitles();
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
