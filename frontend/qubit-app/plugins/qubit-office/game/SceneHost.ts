/* F04 — the engine-agnostic scene host.
 *
 * WHY THIS INTERFACE EXISTS
 * -------------------------
 * The spike must answer one question: can ONE engine instance mount, run and
 * destroy cleanly inside the host's React tree, with no duplicate registration
 * and no leaked listeners? That is a question about LIFECYCLE, not about which
 * engine answers it. So the React host talks to this interface and never to a
 * concrete engine — which is what makes the Phaser-vs-Pixi decision reversible
 * (10-DECISIONS.md § Renderer comparison) instead of a rewrite.
 *
 * C7 BOUNDARY, ENFORCED BY TYPE: a `RenderModel` is a pure function of the
 * payload. `step()` is called on data change, never on a clock the view owns.
 * The engine may interpolate between two authoritative points; it may not
 * advance state the payload did not carry.
 */

export interface Camera {
  cx: number;
  cy: number;
  zoom: number;
}

export interface RenderModel {
  /** Bumped by the adapter on every accepted payload. The scene rebuilds only
   *  when this changes — a poll that returned identical bytes costs nothing. */
  revision: number;
  /** Cosmetic + authoritative drawing input. No work meaning. */
  draw: (ctx: CanvasRenderingContext2D, cam: Camera) => void;
}

export interface SceneHost {
  /** Attach to a mounted container. Called ONCE per mount. */
  mount(container: HTMLElement, canvas: HTMLCanvasElement): void;
  /** Render one revision. Called on data change and on camera gestures. */
  step(model: RenderModel, cam: Camera): void;
  /** Release every listener, texture and child. Must be idempotent. */
  destroy(): void;
  /** The camera after the last gesture, so the host can own Fit/Reset. */
  camera(): Camera;
  /** How many listeners this instance currently holds. The leak check. */
  listenerCount(): number;
}

export type SceneFactory = () => SceneHost;
