/* Loader for the vendored Sakuga VFX assets.
 *
 * The upstream files are plain scripts (not modules): `core.js` sets
 * `window.SakugaFX` and each component registers a custom element. They are
 * served from a public directory, so this loader injects one `<script>` per
 * file, deduplicated by URL, and resolves once the element is registered.
 *
 * Cost control matters here: `core.js` is ~40 KB but the toggle component is
 * ~506 KB of painted frames. Core is loaded when effects are first needed;
 * a component is loaded only when that control first renders. The toggle must
 * never be part of an initial payload.
 */
import type { SakugaComponentName, SakugaFxApi } from './types';

const COMPONENTS: readonly SakugaComponentName[] = [
  'button',
  'toggle',
  'slider',
  'checkbox',
  'radio',
  'input',
  'tabs',
  'progress',
  'rating',
  'toast',
  'tooltip',
];

const TAGS: Record<SakugaComponentName, string> = {
  button: 'sakuga-button',
  toggle: 'sakuga-toggle',
  slider: 'sakuga-slider',
  checkbox: 'sakuga-checkbox',
  radio: 'sakuga-radio',
  input: 'sakuga-input',
  tabs: 'sakuga-tabs',
  progress: 'sakuga-progress',
  rating: 'sakuga-rating',
  toast: 'sakuga-toast',
  tooltip: 'sakuga-tooltip',
};

let base = '/sakuga-vfx';
const scripts = new Map<string, Promise<void>>();

/** Point the loader at a different served copy (office frame, Electron file://). */
export function configureSakuga(options: { base?: string } = {}): void {
  if (options.base) base = options.base.replace(/\/+$/, '');
}

export function sakugaBase(): string {
  return base;
}

export function sakugaTag(name: SakugaComponentName): string {
  return TAGS[name];
}

function canUseDom(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function inject(src: string): Promise<void> {
  const existing = scripts.get(src);
  if (existing) return existing;
  const promise = new Promise<void>((resolve, reject) => {
    if (!canUseDom()) {
      reject(new Error('SAKUGA_NO_DOM'));
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.dataset.sakuga = '1';
    script.onload = () => resolve();
    script.onerror = () => {
      scripts.delete(src);
      reject(new Error(`SAKUGA_LOAD_FAILED ${src}`));
    };
    document.head.append(script);
  });
  scripts.set(src, promise);
  return promise;
}

/** Load and return the shared effects engine. Idempotent. */
export function loadSakugaCore(): Promise<SakugaFxApi> {
  if (!canUseDom()) return Promise.reject(new Error('SAKUGA_NO_DOM'));
  if (window.SakugaFX) return Promise.resolve(window.SakugaFX);
  return inject(`${base}/core.js`).then(() => {
    if (!window.SakugaFX) throw new Error('SAKUGA_CORE_NOT_REGISTERED');
    return window.SakugaFX;
  });
}

/** Load one component file and resolve when its custom element exists. */
export function loadSakugaComponent(name: SakugaComponentName): Promise<void> {
  if (!canUseDom()) return Promise.reject(new Error('SAKUGA_NO_DOM'));
  return inject(`${base}/${name}/${name}.js`).then(() => {
    if (!window.customElements.get(TAGS[name])) {
      throw new Error(`SAKUGA_ELEMENT_NOT_REGISTERED ${TAGS[name]}`);
    }
  });
}

/** Load several components (the engine first is deliberate: one shared loop). */
export async function loadSakugaComponents(names: readonly SakugaComponentName[] = COMPONENTS): Promise<void> {
  await loadSakugaCore();
  await Promise.all(names.map((name) => loadSakugaComponent(name)));
}

/** True once the element for this component is defined in the current document. */
export function sakugaComponentReady(name: SakugaComponentName): boolean {
  return canUseDom() && Boolean(window.customElements?.get(TAGS[name]));
}

export const SAKUGA_COMPONENTS = COMPONENTS;
