/* The product-wide effect bus.
 *
 * Any Qubit surface can fire a named cue without importing the engine:
 *
 *   emitFx('success', { x, y })
 *   window.dispatchEvent(new CustomEvent('qubit:fx', { detail: { cue: 'error' } }))
 *
 * The visual vocabulary is Sakuga VFX's, and the palette is deliberately the
 * upstream one (white / lavender / violet / pink) rather than a Qubit theme
 * colour: the owner asked for the original colours. Nothing here invents state;
 * a cue is presentation only, exactly like `voicebridge/office/fx.py`.
 */
import { loadSakugaCore } from './loader';
import type { SakugaFxApi, SakugaWord } from './types';

export type FxTone = 'neutral' | 'success' | 'error' | 'warning' | 'accent';

export type FxCue =
  | 'impact'
  | 'success'
  | 'error'
  | 'warning'
  | 'celebrate'
  | 'pop'
  | 'slash'
  | 'tick'
  | 'focus'
  | 'type'
  | 'approval'
  | 'agents';

export interface FxOptions {
  /** Page coordinates. Omitted means the target's centre, else the viewport centre. */
  x?: number;
  y?: number;
  /** Anchor element; its centre is used when x/y are absent. */
  target?: Element | null;
  /** Scale multiplier for the burst/lettering. */
  scale?: number;
  /** Override the lettering word. */
  word?: SakugaWord | string;
  /** Skip the lettering (a dry cue). */
  silent?: boolean;
}

export interface FxEventDetail extends FxOptions {
  cue: FxCue;
}

type Listener = (detail: FxEventDetail) => void;

const listeners = new Set<Listener>();

function viewportCentre(): { x: number; y: number } {
  if (typeof window === 'undefined') return { x: 0, y: 0 };
  return { x: window.scrollX + window.innerWidth / 2, y: window.scrollY + window.innerHeight / 2 };
}

function anchor(detail: FxEventDetail): { x: number; y: number } {
  if (typeof detail.x === 'number' && typeof detail.y === 'number') {
    return { x: detail.x, y: detail.y };
  }
  const el = detail.target;
  if (el && typeof el.getBoundingClientRect === 'function') {
    const rect = el.getBoundingClientRect();
    return {
      x: rect.left + window.scrollX + rect.width / 2,
      y: rect.top + window.scrollY + rect.height / 2,
    };
  }
  return viewportCentre();
}

function draw(api: SakugaFxApi, detail: FxEventDetail): void {
  const { x, y } = anchor(detail);
  const s = Math.max(0.35, Math.min(3, detail.scale ?? 1));
  const word = detail.word;
  const lettering = (fallback: SakugaWord, opts: { dots?: boolean; size?: number } = {}) => {
    if (detail.silent) return;
    api.sfx(word ?? fallback, x, y - 46 * s, {
      size: opts.size ?? 56 * s,
      ...(opts.dots ? api.dotted() : api.hollow()),
    });
  };

  switch (detail.cue) {
    case 'impact':
      api.burst(x, y, { r: 60 * s, spikes: 14 });
      api.puff(x - 34 * s, y + 18 * s, { n: 5, size: 12 * s });
      api.puff(x + 34 * s, y + 18 * s, { n: 5, size: 12 * s });
      api.shards(x, y, { n: 9, speed: 200 * s, size: 5 * s });
      api.ticks(x, y, { n: 3, r: 26 * s });
      lettering('don', { size: 62 * s });
      break;
    case 'success':
      api.burst(x, y, { r: 52 * s, spikes: 12 });
      api.ring(x, y + 34 * s, { rx: 42 * s, ry: 13 * s });
      api.kira(x + 30 * s, y - 24 * s, { r: 8 * s });
      lettering('don', { size: 54 * s });
      break;
    case 'error':
      api.slash(x, y, { len: 120 * s, ang: -0.6, thick: 12 * s });
      api.lines(x, y, { n: 7, len: 46 * s });
      api.wob(x, y + 22 * s, { r: 18 * s });
      lettering('zuza', { dots: true, size: 52 * s });
      break;
    case 'warning':
      api.ring(x, y, { rx: 44 * s, ry: 13 * s, life: 0.6 });
      api.ticks(x, y, { n: 4, r: 22 * s });
      api.wob(x + 36 * s, y, { r: 14 * s });
      lettering('pon', { size: 46 * s });
      break;
    case 'celebrate':
      api.burst(x, y, { r: 70 * s, spikes: 16 });
      api.shards(x, y, { n: 12, speed: 240 * s, size: 6 * s });
      api.puff(x - 52 * s, y + 20 * s, { n: 6, size: 13 * s });
      api.puff(x + 52 * s, y + 20 * s, { n: 6, size: 13 * s });
      lettering('don', { size: 68 * s });
      break;
    case 'pop':
      api.puff(x, y, { n: 5, size: 11 * s });
      lettering('pon', { size: 44 * s });
      break;
    case 'slash':
      api.slash(x, y, { len: 140 * s, ang: -0.42, thick: 15 * s });
      lettering('shu', { size: 46 * s });
      break;
    case 'tick':
      api.kira(x, y, { r: 10 * s });
      api.ticks(x, y, { n: 2, r: 18 * s });
      lettering('kachi', { size: 42 * s });
      break;
    case 'focus':
      api.ticks(x, y, { n: 3, r: 20 * s });
      api.puff(x, y + 10 * s, { n: 3, size: 7 * s });
      break;
    case 'type':
      api.puff(x, y, { n: 3, size: 5 * s, life: 0.42, vy: -10 });
      break;
    case 'approval':
      api.ring(x, y, { rx: 50 * s, ry: 15 * s, life: 0.7 });
      api.ticks(x, y, { n: 5, r: 24 * s });
      lettering('kachi', { size: 48 * s });
      break;
    case 'agents':
      api.burst(x, y, { r: 40 * s, spikes: 10 });
      api.puff(x, y + 16 * s, { n: 4, size: 10 * s });
      lettering('gogo', { size: 48 * s });
      break;
    default:
      break;
  }
}

/** Fire one cue. Resolves after the engine has loaded; failures are non-fatal. */
export async function emitFx(cue: FxCue, options: FxOptions = {}): Promise<void> {
  const detail: FxEventDetail = { cue, ...options };
  for (const listener of listeners) listener(detail);
  try {
    if (typeof window !== 'undefined' && window.SakugaFX?.calm) return;
    const api = await loadSakugaCore();
    draw(api, detail);
  } catch {
    // Presentation must never break the action that triggered it.
  }
}

/** Fire a cue anchored to an element. */
export function emitFxAt(cue: FxCue, target: Element | null, options: FxOptions = {}): Promise<void> {
  return emitFx(cue, { ...options, target });
}

/** Subscribe to cues before the engine finishes loading (analytics, tests, sound). */
export function onFx(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Install the `qubit:fx` window listener once. Safe to call repeatedly. */
let installed = false;
export function installFxBridge(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('qubit:fx', (event) => {
    const detail = (event as CustomEvent<FxEventDetail>).detail;
    if (detail && typeof detail.cue === 'string') void emitFx(detail.cue, detail);
  });
}

/** Convenience for plain-DOM call sites (the office shell, Electron preload). */
export function fireFx(cue: FxCue, detail: Omit<FxEventDetail, 'cue'> = {}): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<FxEventDetail>('qubit:fx', { detail: { cue, ...detail } }));
}
