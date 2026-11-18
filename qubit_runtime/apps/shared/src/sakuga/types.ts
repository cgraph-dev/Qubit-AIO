/* Typed surface of the vendored Sakuga VFX engine (`core.js`).
 *
 * The engine is copied byte-identical from
 * https://github.com/finktheartist/sakuga-vfx (MIT, pin c3dabb17). It publishes
 * `window.SakugaFX`; this file only describes that object so Qubit code can call
 * it without `any`. Do not add behaviour here: if the engine needs a change, the
 * vendored file is the place, and it must be recorded in PROVENANCE.
 */

export interface SakugaPalette {
  fill: string;
  shade: string;
  line: string;
  accent: string;
  ink: string;
  sfx: readonly [string, string];
}

export interface SakugaSpring {
  x: number;
  v: number;
  to: number;
  k: number;
  d: number;
  step(dt: number): number;
}

export interface SakugaPoint {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface SakugaLayerHandle {
  c: HTMLCanvasElement | null;
  g: CanvasRenderingContext2D | null;
  live: unknown[];
  dirty: boolean;
}

export interface SakugaEffectOptions {
  r?: number;
  spikes?: number;
  life?: number;
  layer?: SakugaLayerHandle;
  n?: number;
  size?: number;
  vx?: number;
  vy?: number;
  spread?: number;
  speed?: number;
  dir?: number;
  arc?: number;
  grav?: number;
  rx?: number;
  ry?: number;
  w?: number;
  h?: number;
  len?: number;
  ang?: number;
  thick?: number;
  col?: string;
  tilt?: number;
  fan?: number;
}

export interface SakugaSfxOptions {
  size?: number;
  rot?: number;
  life?: number;
  col?: string;
  edge?: string;
  dots?: boolean;
}

export type SakugaWord = 'don' | 'gogo' | 'kachi' | 'pon' | 'shu' | 'zuza';

export interface SakugaFxApi {
  version: number;
  paused: boolean;
  readonly P: SakugaPalette;
  readonly calm: boolean;
  readonly now: number;
  TAU: number;
  clamp(v: number, a?: number, b?: number): number;
  lerp(a: number, b: number, t: number): number;
  rnd(a?: number, b?: number): number;
  Spring: new (x?: number, k?: number, d?: number) => SakugaSpring;
  advance(dt: number): void;
  later(ms: number, fn: () => void): void;
  persist(draw: (k: number, t: number, dt: number) => void, layer?: SakugaLayerHandle): { dead?: boolean };
  blob(circles: readonly [number, number, number][], lw?: number, _?: unknown, opts?: { lx?: number; arcs?: boolean; deep?: number }): void;
  shape(path: Path2D, lw?: number, off?: number, fill?: string): void;
  readonly g: CanvasRenderingContext2D | null;
  puff(x: number, y: number, opts?: SakugaEffectOptions): void;
  burst(x: number, y: number, opts?: SakugaEffectOptions): void;
  shards(x: number, y: number, opts?: SakugaEffectOptions): void;
  ring(x: number, y: number, opts?: SakugaEffectOptions): void;
  splash(x: number, y: number, opts?: SakugaEffectOptions): void;
  slash(x: number, y: number, opts?: SakugaEffectOptions): void;
  lines(x: number, y: number, opts?: SakugaEffectOptions): void;
  ticks(x: number, y: number, opts?: SakugaEffectOptions): void;
  donut(x: number, y: number, opts?: SakugaEffectOptions): void;
  wob(x: number, y: number, opts?: SakugaEffectOptions): void;
  kira(x: number, y: number, opts?: SakugaEffectOptions): void;
  sfx(name: SakugaWord | string, x: number, y: number, opts?: SakugaSfxOptions): void;
  hollow(): { col: string; edge: string };
  dotted(): { dots: boolean; edge: string };
  at(el: Element, fx?: number, fy?: number): SakugaPoint;
  esc(value: unknown): string;
  squashy(el: HTMLElement, opts?: { low?: number; k?: number; d?: number; wide?: number }): {
    s: SakugaSpring;
    pop: SakugaSpring;
    tilt: SakugaSpring;
    step(dt: number): void;
  };
  Base: CustomElementConstructor;
  define(tag: string, ctor: CustomElementConstructor): void;
}

export type SakugaComponentName =
  | 'button'
  | 'toggle'
  | 'slider'
  | 'checkbox'
  | 'radio'
  | 'input'
  | 'tabs'
  | 'progress'
  | 'rating'
  | 'toast'
  | 'tooltip';

declare global {
  interface Window {
    SakugaFX?: SakugaFxApi;
  }
}
