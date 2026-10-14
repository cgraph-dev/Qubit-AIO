/* F04 — the Canvas2D scene host.
 *
 * This is the DEFAULT host and the spike's control. It proves the SceneHost
 * lifecycle contract with the technology already in the tree, so the Phaser
 * host can be judged against a known-good baseline rather than against
 * nothing. It is also the honest fallback if the engine spike fails
 * (10-DECISIONS.md § Renderer comparison: "Keep Canvas2D ... suitable for a
 * limited repair").
 *
 * It owns NO timer and NO fetch — a gesture redraws on its own event, which is
 * what C7 permits and what the amendment's T3 check asserts.
 */

import type { Camera, RenderModel, SceneHost } from "./SceneHost";

export interface CanvasHostOptions {
  /** Called when the user pans or zooms, so the host can stop passive re-framing
   *  and persist the choice. Presentation-only (amendment § 2). */
  onCamera?: (cam: Camera) => void;
  /** Pick radius in CSS px for a click that selects a figure. */
  pickRadius?: number;
}

export function createCanvasHost(opts: CanvasHostOptions = {}): SceneHost {
  let container: HTMLElement | null = null;
  let canvas: HTMLCanvasElement | null = null;
  let cam: Camera = { cx: 0, cy: 0, zoom: 1 };
  let model: RenderModel | null = null;
  const listeners: Array<[string, EventListener]> = [];
  let destroyed = false;

  const pickRadius = opts.pickRadius ?? 22;
  const DRAG_SLOP = 4;
  let drag: { x: number; y: number; cx: number; cy: number; moved: boolean } | null =
    null;
  let suppressClick = false;

  function on<K extends keyof HTMLElementEventMap>(
    el: EventTarget, type: string, fn: (ev: HTMLElementEventMap[K]) => void,
    o?: AddEventListenerOptions,
  ) {
    el.addEventListener(type, fn as EventListener, o);
    // record with the CONCRETE fn so removal is exact
    listeners.push([type, fn as EventListener]);
  }

  function redraw() {
    if (!canvas || !model) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    model.draw(ctx, cam);
  }

  function down(ev: PointerEvent) {
    drag = { x: ev.clientX, y: ev.clientY, cx: cam.cx, cy: cam.cy, moved: false };
    if (ev.pointerId !== undefined && canvas?.setPointerCapture) {
      try {
        canvas.setPointerCapture(ev.pointerId);
      } catch {
        /* a synthetic event has no real pointer; a named gap, not a crash */
      }
    }
    if (canvas) canvas.style.cursor = "grabbing";
  }

  function move(ev: PointerEvent) {
    if (!drag) return;
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < DRAG_SLOP) return;
    drag.moved = true;
    cam = { ...cam, cx: drag.cx - dx / cam.zoom, cy: drag.cy - dy / cam.zoom };
    opts.onCamera?.(cam);
    redraw();
  }

  function up() {
    if (drag?.moved) suppressClick = true;
    drag = null;
    if (canvas) canvas.style.cursor = "grab";
  }

  function wheel(ev: WheelEvent) {
    ev.preventDefault();
    if (!canvas) return;
    const before = cam.zoom;
    const after = Math.max(0.35, Math.min(2.4, before * (ev.deltaY > 0 ? 0.9 : 1.1)));
    if (after === before) return;
    // cursor-anchored: keep the world point under the pointer fixed
    const r = canvas.getBoundingClientRect();
    const px = ev.clientX - r.left - canvas.width / 2;
    const py = ev.clientY - r.top - canvas.height / 2;
    cam = {
      cx: cam.cx + px / before - px / after,
      cy: cam.cy + py / before - py / after,
      zoom: after,
    };
    opts.onCamera?.(cam);
    redraw();
  }

  function click(ev: MouseEvent) {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    // Selection itself belongs to the caller's model (F05/F07 wire it). The
    // host's job is to deliver a canvas-local point with no drag in flight.
    if (!canvas) return;
    const r = canvas.getBoundingClientRect();
    clickSink?.({ x: ev.clientX - r.left, y: ev.clientY - r.top,
                 radius: Math.max(6, pickRadius / cam.zoom) });
  }

  let clickSink: ((hit: { x: number; y: number; radius: number }) => void) | null =
    null;

  return {
    mount(el, cv) {
      container = el;
      canvas = cv;
      on<never>(cv, "pointerdown", down as never);
      on<never>(cv, "pointermove", move as never);
      on<never>(cv, "pointerup", up as never);
      // a cancelled gesture must end the drag, or the camera stays glued to a
      // pointer that no longer exists
      on<never>(cv, "pointercancel", up as never);
      on<never>(cv, "wheel", wheel as never, { passive: false });
      on<never>(cv, "click", click as never);
    },
    step(nextModel, nextCam) {
      if (destroyed) return;
      model = nextModel;
      cam = nextCam;
      redraw();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (canvas) {
        for (const [type, fn] of listeners) {
          canvas.removeEventListener(type, fn);
        }
      }
      listeners.length = 0;
      drag = null;
      model = null;
      container = null;
      canvas = null;
    },
    camera() {
      return cam;
    },
    listenerCount() {
      return listeners.length;
    },
    /** Not part of SceneHost: the spike's seam for wiring selection in F07. */
    setClickSink(fn) {
      clickSink = fn;
    },
  } as SceneHost & {
    setClickSink(fn: (hit: { x: number; y: number; radius: number }) => void): void;
  };
}
