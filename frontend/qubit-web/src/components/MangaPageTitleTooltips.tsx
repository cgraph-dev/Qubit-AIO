import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Q5TTooltipPlate } from "./Q5TTooltipPlate";

interface PageTitleTip {
  title: string;
  anchor: HTMLElement;
}

/**
 * Replaces native browser title tooltips on Manga dashboard pages with the
 * owner Q5T plate. The sidebar is excluded because it has its own positioned
 * rail tooltip. Titles are temporarily moved to data-q5t-title so the OS does
 * not paint its plain black tooltip underneath the plate.
 */
export function MangaPageTitleTooltips() {
  const [tip, setTip] = useState<PageTitleTip | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const isManga = () => document.documentElement.dataset.qubitManga === "on";
    const stripTitles = () => {
      if (!isManga()) return;
      document.querySelectorAll<HTMLElement>("[title]").forEach((element) => {
        if (element.closest("#app-sidebar")) return;
        const title = element.getAttribute("title");
        if (!title) return;
        element.dataset.q5tTitle = title;
        element.removeAttribute("title");
      });
    };
    stripTitles();
    const observer = new MutationObserver(stripTitles);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["title"],
    });

    const showFrom = (target: EventTarget | null) => {
      if (!isManga()) return;
      const element = (target as Element | null)?.closest?.<HTMLElement>("[data-q5t-title]");
      if (!element || element.closest("#app-sidebar")) {
        setTip(null);
        return;
      }
      const title = element.dataset.q5tTitle;
      if (title) setTip({ title, anchor: element });
    };
    const hide = () => setTip(null);
    const onOver = (event: PointerEvent) => showFrom(event.target);
    const onFocus = (event: FocusEvent) => showFrom(event.target);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") hide(); };
    document.addEventListener("pointerover", onOver);
    document.addEventListener("pointerout", hide);
    document.addEventListener("focusin", onFocus);
    document.addEventListener("focusout", hide);
    document.addEventListener("keydown", onKey);

    return () => {
      observer.disconnect();
      document.removeEventListener("pointerover", onOver);
      document.removeEventListener("pointerout", hide);
      document.removeEventListener("focusin", onFocus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("keydown", onKey);
      document.querySelectorAll<HTMLElement>("[data-q5t-title]").forEach((element) => {
        const title = element.dataset.q5tTitle;
        if (title) element.setAttribute("title", title);
        delete element.dataset.q5tTitle;
      });
    };
  }, []);

  if (!tip) return null;
  const rect = tip.anchor.getBoundingClientRect();
  const width = 300;
  const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - width - 8));
  const above = rect.top > 190;
  const top = above ? rect.top - 8 : rect.bottom + 8;
  return createPortal(
    <div
      className="q5t-tooltip-shell q5t-page-tooltip"
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
      <Q5TTooltipPlate title={tip.title} />
    </div>,
    document.body,
  );
}
