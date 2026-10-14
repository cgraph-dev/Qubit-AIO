/* F05 — the data adapter: serialized, abortable, revision-fenced.
 *
 * THE FOUR FAILURES THIS PREVENTS
 * -------------------------------
 * 1. **Out-of-order responses regress the view.** Two overlapping polls can
 *    land in the wrong order; the older one arriving second would overwrite the
 *    newer state and the tower would appear to travel backwards in time. Every
 *    response carries the `revision` it was requested at, and an older
 *    revision is DROPPED, never applied.
 * 2. **A failed poll blanks the scene.** The last good payload is retained and
 *    the failure is reported alongside it, so a blip shows "stale" rather than
 *    an empty office. An unavailable backend must never look like an empty one.
 * 3. **A hidden tab keeps spending.** Polling pauses when the document is
 *    hidden and resumes on visibility, without touching org state.
 * 4. **Two loads in flight.** Loads are SERIALIZED: a new load supersedes and
 *    aborts the one before it rather than racing it.
 *
 * The `preview` flag is passed through untouched and never cleared. The
 * recording fixture sets `preview: true` conceptually (see PROVENANCE.md); a
 * live payload sets it false. The adapter's job is to carry the backend's own
 * claim, not to decide it.
 */

import {
  EXPECTED_SCHEMA,
  type OfficeTowerPayload,
} from "./types";

/** What the UI renders. `payload` is null ONLY before the first success. */
export interface OfficeSnapshot {
  /** Monotonic. Bumped only when a NEWER payload is accepted. */
  revision: number;
  payload: OfficeTowerPayload | null;
  /** Set when the most recent attempt failed. `payload` may still be good. */
  error: string | null;
  /** True while a load is in flight (a real request, honestly labelled). */
  loading: boolean;
  /** True when `payload` is known-stale because the last attempt failed. */
  stale: boolean;
  /** Where the data came from. A recorded fixture is never called live. */
  provenance: "live" | "recording" | "none";
}

export interface AdapterOptions {
  /** The API base. `/agent` because the SPA rewrites that prefix. */
  baseUrl?: string;
  /** Session token, read from the page the server injected it into. */
  token?: string;
  /** Poll cadence in ms. The tower's own recorded cadence is 400 ms. */
  intervalMs?: number;
  /** Injected for tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch;
  /** Called on every accepted snapshot. */
  onSnapshot: (snap: OfficeSnapshot) => void;
}

const DEFAULT_INTERVAL_MS = 400;

export class OfficeAdapter {
  private revision = 0;
  private payload: OfficeTowerPayload | null = null;
  private error: string | null = null;
  private loading = false;
  private provenance: OfficeSnapshot["provenance"] = "none";
  private inflight: AbortController | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private disposed = false;
  private readonly opts: Required<Pick<AdapterOptions, "baseUrl" | "intervalMs">> &
    AdapterOptions;

  constructor(opts: AdapterOptions) {
    this.opts = {
      baseUrl: "/agent",
      intervalMs: DEFAULT_INTERVAL_MS,
      ...opts,
    };
  }

  /** The current snapshot. A pure read — never triggers a load. */
  snapshot(): OfficeSnapshot {
    return {
      revision: this.revision,
      payload: this.payload,
      error: this.error,
      loading: this.loading,
      stale: this.error !== null && this.payload !== null,
      provenance: this.provenance,
    };
  }

  /**
   * One serialized load. A call while one is in flight ABORTS the earlier one
   * and supersedes it, so two responses can never race.
   */
  async load(): Promise<void> {
    if (this.disposed) return;
    this.inflight?.abort();
    const ctl = new AbortController();
    this.inflight = ctl;
    const requestedAt = this.revision;
    this.loading = true;
    this.emit();

    const f = this.opts.fetchImpl ?? fetch;
    const token = this.opts.token ?? readSessionToken();
    try {
      const res = await f(`${this.opts.baseUrl}/api/office/tower`, {
        signal: ctl.signal,
        headers: token ? { "X-Api-Token": token } : undefined,
      });
      if (ctl.signal.aborted) return;
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const body = (await res.json()) as unknown;
      if (ctl.signal.aborted) return;
      this.accept(body, requestedAt);
    } catch (err) {
      if (ctl.signal.aborted || this.disposed) return;
      // Retain the last good payload; name the failure. A failed read must
      // never render as an empty office.
      this.error = err instanceof Error ? err.message : String(err);
      this.loading = false;
      this.emit();
    } finally {
      if (this.inflight === ctl) this.inflight = null;
    }
  }

  /** Validate + apply one payload. Exposed so tests can drive it directly. */
  accept(body: unknown, requestedAt: number): void {
    const check = parseTower(body);
    if (!check.ok) {
      this.error = check.error;
      this.loading = false;
      this.emit();
      return;
    }
    // THE REVISION FENCE. A response older than what we have already applied
    // is dropped on the floor, so a slow request cannot undo a fast one.
    if (requestedAt < this.revision) {
      this.loading = false;
      return;
    }
    this.payload = check.payload;
    this.revision = requestedAt + 1;
    this.error = null;
    this.loading = false;
    this.provenance = check.payload.preview ? "recording" : "live";
    this.emit();
  }

  /** Start polling. Pauses while the document is hidden. */
  start(): void {
    if (this.timer !== null || this.disposed) return;
    void this.load();
    this.timer = setInterval(() => {
      if (isHidden()) return;
      void this.load();
    }, this.opts.intervalMs);
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Release everything. Idempotent — safe in a React cleanup. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
    this.inflight?.abort();
    this.inflight = null;
    // A disposed adapter is not loading. Leaving the flag true would have a
    // caller that reads `snapshot()` after teardown believe a request is still
    // in flight forever.
    this.loading = false;
  }

  private emit(): void {
    if (this.disposed) return;
    this.opts.onSnapshot(this.snapshot());
  }
}

function isHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/**
 * The session token the server injects into the SPA shell
 * (`webui.py:1563` `window.__QUBIT_SESSION_TOKEN__`). Never a query param —
 * `token_ok` rejects query tokens outright.
 */
export function readSessionToken(): string {
  const w = globalThis as unknown as { __QUBIT_SESSION_TOKEN__?: string };
  return typeof w.__QUBIT_SESSION_TOKEN__ === "string"
    ? w.__QUBIT_SESSION_TOKEN__
    : "";
}

/** Structural validation. Returns a typed refusal, never a coerced payload. */
export function parseTower(body: unknown):
  | { ok: true; payload: OfficeTowerPayload }
  | { ok: false; error: string } {
  if (body === null || typeof body !== "object") {
    return { ok: false, error: "the response was not an object" };
  }
  const b = body as Record<string, unknown>;
  if (b.schema !== EXPECTED_SCHEMA) {
    // A schema change is a real event; silently accepting it would let a
    // backend rename render as an empty office.
    return {
      ok: false,
      error: `unexpected schema ${JSON.stringify(b.schema)}; this build reads ${EXPECTED_SCHEMA}`,
    };
  }
  if (b.ok !== true) {
    // The route answers `ok:false` with a `detail` when the store is unreadable.
    return {
      ok: false,
      error:
        typeof b.detail === "string"
          ? b.detail
          : "the tower report answered not-ok",
    };
  }
  if (typeof b.hud !== "object" || b.hud === null) {
    return { ok: false, error: "the payload carries no hud" };
  }
  if (!Array.isArray(b.characters)) {
    return { ok: false, error: "the payload carries no characters array" };
  }
  // `empty` is the backend's own statement; if it says empty, that is the
  // truth and the scene must say so rather than draw a remembered office.
  return { ok: true, payload: body as OfficeTowerPayload };
}
