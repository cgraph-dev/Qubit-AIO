/* F04/F07 — the Phaser scene host.
 *
 * THE ENGINE DECISION, MADE AND RECORDED
 * --------------------------------------
 * The owner chose **Phaser 4.2.1**. Rationale as given: newest, most featured,
 * best fit for AI-driven work. `10-DECISIONS.md:45` forbade GUESSING a version;
 * this is an explicit owner ruling, not a guess, and it supersedes P02's
 * "Phaser 3" for the same reason P02 gave for naming 3 at the time — the
 * plan picked the current stable, and the current stable has moved.
 *
 * PROVENANCE (required by AGENTS.md before reusing external source):
 *   package  phaser@4.2.1
 *   license  MIT — Copyright (c) 2026 Richard Davey, Phaser Studio Inc.
 *   source   https://github.com/phaserjs/phaser (npm tarball, unmodified)
 *   entry    dist/phaser.esm.js (8,405,542 bytes)
 *   local changes: NONE. This file only CALLS the library.
 * The MIT notice travels with the built artifact; nothing was copied from
 * Phaser into Qubit source.
 *
 * WHY A SEPARATE FILE FROM CanvasSceneHost
 * ----------------------------------------
 * Both implement `SceneHost`. That is the point of the seam: the React layer
 * never learns which engine it is talking to, so keeping the Canvas host as a
 * working fallback costs one file and zero coupling. If Phaser's bundle or a
 * browser turns out to be a problem, the fallback is a factory argument.
 *
 * C7 BOUNDARY: Phaser has its own requestAnimationFrame loop — that is what a
 * game engine IS, and the amendment permits it explicitly ("cosmetic scenery,
 * ambient idle motion, camera travel and view transitions are presentation").
 * What the engine must NEVER do is advance DOMAIN state. So this host renders
 * whatever `RenderModel.draw` paints for the revision it was given and holds no
 * character position of its own. A frozen event stream still means a still
 * building.
 */

import Phaser from "phaser";
import type { Camera, RenderModel, SceneHost } from "./SceneHost";

/**
 * Phaser 4 boots into a container element. The drawing itself stays in the
 * SAME `RenderModel.draw` a Canvas2D context would receive — Phaser supplies a
 * real 2D context for the `textures` route below, so the existing renderer is
 * reused rather than reimplemented. That is what makes this a host swap and not
 * a rewrite.
 */
class OfficeScene extends Phaser.Scene {
  private host: PhaserOfficeHost;

  constructor(host: PhaserOfficeHost) {
    super({ key: "OfficeScene" });
    this.host = host;
  }

  create(): void {
    // A single canvas-backed texture the host repaints on each accepted
    // revision. One game object, repainted — never thousands of sprites
    // rebuilt per poll, which is how a 400 ms poll becomes a 400 ms GC storm.
    const tex = this.textures.createCanvas(
      "office-surface",
      Math.max(1, this.host.surfaceWidth),
      Math.max(1, this.host.surfaceHeight),
    );
    if (tex) {
      this.host.attachTexture(tex);
      this.add.image(0, 0, "office-surface").setOrigin(0, 0);
    }
    this.host.markReady();
  }
}

export class PhaserOfficeHost implements SceneHost {
  private game: Phaser.Game | null = null;
  private texture: Phaser.Textures.CanvasTexture | null = null;
  private container: HTMLElement | null = null;
  private cam: Camera = { cx: 0, cy: 0, zoom: 1 };
  private model: RenderModel | null = null;
  private destroyed = false;
  private ready = false;
  private pendingRepaint = false;
  private listeners: Array<[EventTarget, string, EventListener]> = [];

  surfaceWidth = 880;
  surfaceHeight = 500;

  attachTexture(tex: Phaser.Textures.CanvasTexture): void {
    this.texture = tex;
    this.ready = true;
    // A revision that arrived before `create()` ran is painted now rather than
    // dropped — the loading/ready branch problem, at the engine layer.
    if (this.pendingRepaint) {
      this.pendingRepaint = false;
      this.repaint();
    }
  }

  markReady(): void {
    this.ready = true;
  }

  mount(container: HTMLElement, canvas: HTMLCanvasElement): void {
    this.container = container;
    // SIZE FROM THE STAGE, NOT THE HIDDEN MEASURING CANVAS. The measuring
    // canvas is a fixed 880x500 while the stage is fluid, so reading the canvas
    // produced a surface narrower than its container and a click-coordinate map
    // that was wrong by the ratio. The stage is what the user actually sees.
    const box = container.getBoundingClientRect();
    this.surfaceWidth = Math.max(240, Math.floor(box.width) || canvas.width || 880);
    this.surfaceHeight = Math.max(160, Math.floor(box.height) || canvas.height || 500);

    // Phaser creates and owns its own canvas inside `container`. The canvas
    // React rendered is only the MEASURING STICK for the surface size — the
    // shell already hides it, and it is never the drawing surface. Exactly one
    // canvas is visible per mount.
    if (canvas.style.display !== "none") canvas.style.display = "none";

    this.game = new Phaser.Game({
      type: Phaser.CANVAS,
      parent: container,
      width: this.surfaceWidth,
      height: this.surfaceHeight,
      banner: false,
      audio: { noAudio: true },
      scene: [new OfficeScene(this)],
      // The engine's own loop is presentational. It never fetches and never
      // writes; the host's `step()` is the only thing that changes pixels.
      fps: { target: 30, forceSetTimeOut: false },
    });

    // Phaser installs its own input; the gesture vocabulary stays Qubit's so
    // the C4/C7 checks keep holding over one implementation, not two.
    const on = (type: string, fn: EventListener) => {
      container.addEventListener(type, fn);
      this.listeners.push([container, type, fn]);
    };
    let drag: { x: number; y: number; cx: number; cy: number; moved: boolean } | null = null;
    on("pointerdown", ((ev: PointerEvent) => {
      drag = { x: ev.clientX, y: ev.clientY, cx: this.cam.cx, cy: this.cam.cy, moved: false };
    }) as EventListener);
    on("pointermove", ((ev: PointerEvent) => {
      if (!drag) return;
      const dx = ev.clientX - drag.x;
      const dy = ev.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true;
      this.cam = { ...this.cam, cx: drag.cx - dx / this.cam.zoom, cy: drag.cy - dy / this.cam.zoom };
      this.repaint();
    }) as EventListener);
    on("pointerup", (() => { drag = null; }) as EventListener);
    on("wheel", ((ev: WheelEvent) => {
      ev.preventDefault();
      const factor = ev.deltaY > 0 ? 0.9 : 1.1;
      this.cam = { ...this.cam, zoom: Math.max(0.35, Math.min(2.4, this.cam.zoom * factor)) };
      this.repaint();
    }) as EventListener);
  }

  step(model: RenderModel, cam: Camera): void {
    if (this.destroyed) return;
    this.model = model;
    this.cam = cam;
    this.repaint();
  }

  private repaint(): void {
    if (this.destroyed) return;
    if (!this.ready || !this.texture) {
      // The scene has not finished `create()`. Remember, do not drop.
      this.pendingRepaint = true;
      return;
    }
    const ctx = this.texture.getContext();
    if (!ctx) return;
    ctx.clearRect(0, 0, this.surfaceWidth, this.surfaceHeight);
    this.model?.draw(ctx, this.cam);
    this.texture.refresh();
  }

  camera(): Camera {
    return this.cam;
  }

  listenerCount(): number {
    return this.listeners.length;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const [el, type, fn] of this.listeners) el.removeEventListener(type, fn);
    this.listeners.length = 0;
    // `game.destroy(true)` also removes the canvas it created; leaving it would
    // stack one canvas per StrictMode remount.
    try {
      this.game?.destroy(true);
    } catch {
      /* an engine torn down mid-boot can throw; it is gone either way */
    }
    this.game = null;
    this.texture = null;
    this.model = null;
    this.container = null;
  }
}

/** The factory the React host takes. This is the entire engine swap. */
export function createPhaserHost(): SceneHost {
  return new PhaserOfficeHost();
}
