/**
 * Connection-latency sampler for the dashboard shell.
 *
 * The gateway/status strip samples the round-trip time of a lightweight
 * request to the Qubit backend on a fixed interval. Raw samples are noisy
 * (GC pause, a slow route handler, a momentarily busy event loop), so the
 * sampler keeps an exponentially-weighted moving average (EWMA) next to the
 * latest reading. It is a plain value object — no timers, no DOM — so the
 * shell can own the sampling cadence and the behaviour is unit-testable.
 */

export interface LatencyStats {
  /** Latest round-trip in milliseconds, or null before the first sample. */
  latestMs: number | null;
  /** EWMA-smoothed round-trip in milliseconds, or null before the first sample. */
  averageMs: number | null;
  /** Number of accepted samples recorded since the last reset. */
  samples: number;
}

const DEFAULT_ALPHA = 0.3;

export class LatencySampler {
  private latest: number | null = null;
  private average: number | null = null;
  private count = 0;
  private readonly alpha: number;

  constructor(alpha: number = DEFAULT_ALPHA) {
    this.alpha = Number.isFinite(alpha) && alpha > 0 && alpha <= 1 ? alpha : DEFAULT_ALPHA;
  }

  /** Record one round-trip sample. Non-finite or negative values are ignored. */
  record(ms: number): void {
    if (!Number.isFinite(ms) || ms < 0) return;
    this.latest = ms;
    this.average =
      this.average === null ? ms : this.alpha * ms + (1 - this.alpha) * this.average;
    this.count += 1;
  }

  /** Forget every sample (e.g. after the connection is rebuilt). */
  reset(): void {
    this.latest = null;
    this.average = null;
    this.count = 0;
  }

  get stats(): LatencyStats {
    return { latestMs: this.latest, averageMs: this.average, samples: this.count };
  }
}

/** Human-readable latency, e.g. "12 ms", "<1 ms", or the em-dash placeholder. */
export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1) return "<1 ms";
  return `${Math.round(ms)} ms`;
}

/** Time an async operation, returning its value and elapsed milliseconds. */
export async function measureLatency<T>(
  operation: () => Promise<T>,
  now: () => number = () => performance.now(),
): Promise<{ value: T; ms: number }> {
  const start = now();
  const value = await operation();
  return { value, ms: Math.max(0, now() - start) };
}
