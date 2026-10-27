'use client';

import { useEffect, useState } from 'react';

/** Local wall clock, client-only (avoids an SSR/hydration timestamp mismatch). */
export function useClock(): Date | null {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return now;
}
