import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// The HUD reads `prefers-reduced-motion` synchronously; tests run reduced so
// Three.js/GSAP are never initialised and timers stay deterministic.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!('ResizeObserver' in window)) {
  Object.defineProperty(window, 'ResizeObserver', {
    writable: true,
    value: ResizeObserverStub,
  });
  Object.defineProperty(globalThis, 'ResizeObserver', {
    writable: true,
    value: ResizeObserverStub,
  });
}

// jsdom does not implement canvas; the ring/background must no-op cleanly.
const originalGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function getContext(
  this: HTMLCanvasElement,
  contextId: string,
  ...args: unknown[]
) {
  if (
    contextId === '2d' ||
    contextId === 'webgl' ||
    contextId === 'webgl2' ||
    contextId === 'experimental-webgl'
  ) {
    return null;
  }
  return originalGetContext.call(
    this,
    contextId as never,
    ...(args as never[]),
  );
} as typeof HTMLCanvasElement.prototype.getContext;
