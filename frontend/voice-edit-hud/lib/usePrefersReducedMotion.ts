'use client';

import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

function readPreference(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(QUERY).matches;
}

/** Live `prefers-reduced-motion` flag; initial value is synchronous. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(readPreference);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia(QUERY);
    const onChange = () => setReduced(query.matches);
    setReduced(query.matches);
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, []);

  return reduced;
}
