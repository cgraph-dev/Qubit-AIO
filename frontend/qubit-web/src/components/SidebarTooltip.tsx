import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { visibleViewport } from "@/manga-skin/panelPlacement";
import { playLocalFx } from "@/sakuga/localFx";
import "./sidebar-tooltip.css";

import { railTooltipPosition } from "./railTooltipPosition";

export function SidebarTooltip({ anchor, label, detail, warmRef }: {
  anchor: HTMLElement;
  label: string;
  detail?: string;
  warmRef?: RefObject<number>;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const fxRef = useRef(false);
  const visibleRef = useRef(false);

  useEffect(() => {
    const warm = warmRef && Date.now() - warmRef.current < 300;
    const keyFocus = anchor.matches(":focus-visible") || Boolean(anchor.querySelector(":focus-visible"));
    const timer = window.setTimeout(() => setReady(true), warm || keyFocus ? 0 : 320);
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      window.clearTimeout(timer);
      setReady(false);
    };
    window.addEventListener("keydown", dismiss);
    anchor.addEventListener("click", dismiss);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", dismiss);
      anchor.removeEventListener("click", dismiss);
    };
  }, [anchor, warmRef]);

  useLayoutEffect(() => {
    const tip = ref.current;
    if (!tip || !ready) return;
    const rail = anchor.closest("#app-sidebar") ?? anchor;
    const trigger = anchor.matches("a,button,[tabindex],sakuga-button") ? anchor
      : anchor.querySelector<HTMLElement>("a,button,sakuga-button") ?? anchor;
    const nav = anchor.closest("nav");
    // The open cue belongs to the menu trigger, fires once per mount when the
    // tooltip first becomes visible, and stays silent for a warm re-open and
    // for reduced-motion/hidden tabs (also enforced inside playLocalFx).
    const announceOpen = () => {
      if (fxRef.current) return;
      fxRef.current = true;
      const warm = warmRef ? Date.now() - warmRef.current < 300 : false;
      const canAnimate = !document.hidden &&
        !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (!warm && canAnimate) playLocalFx("pop", trigger, 0.45);
    };
    const place = () => {
      const viewport = visibleViewport();
      const rect = anchor.getBoundingClientRect();
      const railBox = rail.getBoundingClientRect();
      const navBox = nav?.getBoundingClientRect() ?? railBox;
      const box = { left: railBox.left, right: railBox.right,
        top: Math.max(railBox.top, navBox.top), bottom: Math.min(railBox.bottom, navBox.bottom) };
      const position = railTooltipPosition(rect, box, tip.offsetWidth, tip.offsetHeight, viewport);
      const visible = Boolean(position);
      const wasVisible = visibleRef.current;
      visibleRef.current = visible;
      tip.style.visibility = visible ? "visible" : "hidden";
      if (position) {
        tip.dataset.placement = position.placement;
        tip.style.left = `${position.left}px`;
        tip.style.top = `${position.top}px`;
      }
      if (visible) {
        announceOpen();
        if (warmRef) warmRef.current = Date.now();
      } else if (wasVisible && warmRef) {
        warmRef.current = Date.now();
      }
    };
    place();
    const previous = trigger.getAttribute("aria-describedby");
    if (detail) trigger.setAttribute("aria-describedby", [previous, `${id}-detail`].filter(Boolean).join(" "));
    const observer = new ResizeObserver(place);
    observer.observe(tip);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    window.visualViewport?.addEventListener("scroll", place);
    window.visualViewport?.addEventListener("resize", place);
    return () => {
      if (visibleRef.current && warmRef) warmRef.current = Date.now();
      if (detail) {
        if (previous) trigger.setAttribute("aria-describedby", previous);
        else trigger.removeAttribute("aria-describedby");
      }
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      window.visualViewport?.removeEventListener("scroll", place);
      window.visualViewport?.removeEventListener("resize", place);
    };
  }, [anchor, ready, warmRef, detail, id]);

  return ready && createPortal(
    <div ref={ref} id={id} role="tooltip" data-rail-tooltip="" className="manga-rail-tooltip">
      <span className="manga-rail-tooltip__title" dir="auto">{label}</span>
      {detail && <span className="manga-rail-tooltip__body" id={`${id}-detail`} dir="auto">{detail}</span>}
    </div>,
    document.body,
  );
}
