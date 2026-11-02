/*
 * The office adapter must never abort its own in-flight read.
 *
 * Live symptom this pins: a 400 ms poll whose load() aborted the previous
 * request made EVERY `/agent/api/office/tower` request end net::ERR_ABORTED
 * (the tower fold takes longer than 400 ms), starving the store until the
 * iframe's `/api/stream` never delivered a snapshot and the page stayed on
 * "Connecting…". A load requested while one is in flight is now a no-op.
 */
import { describe, expect, it, vi } from "vitest";
import { OfficeAdapter } from "../adapter";

const SCHEMA = "qubit-office-tower/1";

function towerBody(characters: unknown[] = [], floors: unknown[] = []) {
  return JSON.stringify({
    schema: SCHEMA,
    ok: true,
    hud: {},
    characters,
    floorplan: { schema: "qubit-office-floorplan/1", floors },
  });
}

function okResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OfficeAdapter serialization", () => {
  it("does not abort an in-flight load when another load is requested", async () => {
    let release!: (r: Response) => void;
    let signal: AbortSignal | undefined;
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>((resolve) => { release = resolve; });
    });
    const adapter = new OfficeAdapter({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onSnapshot: () => {},
    });

    const first = adapter.load();
    const second = adapter.load(); // must be skipped, not an abort+restart

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(false);

    release(okResponse(towerBody()));
    await Promise.all([first, second]);

    expect(signal?.aborted).toBe(false);
    expect(adapter.snapshot().error).toBeNull();
    expect(adapter.snapshot().payload).not.toBeNull();
    adapter.dispose();
  });

  it("starts the next load once the previous one has settled", async () => {
    const fetchImpl = vi.fn(async () => okResponse(towerBody()));
    const adapter = new OfficeAdapter({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onSnapshot: () => {},
    });
    await adapter.load();
    await adapter.load();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    adapter.dispose();
  });

  it("retains the last good payload and names a later failure", async () => {
    const responses = [
      okResponse(towerBody([{ agent_id: 1, slug: "ceo" }])),
      new Response("nope", { status: 503 }),
    ];
    const fetchImpl = vi.fn(async () => responses.shift() as Response);
    const adapter = new OfficeAdapter({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onSnapshot: () => {},
    });
    await adapter.load();
    expect(adapter.snapshot().provenance).toBe("live");
    await adapter.load();
    const snap = adapter.snapshot();
    expect(snap.payload).not.toBeNull();
    expect(snap.error).toBe("HTTP 503");
    expect(snap.stale).toBe(true);
    adapter.dispose();
  });

  it("reports a first-read failure without inventing a payload", async () => {
    const fetchImpl = vi.fn(async () => new Response("nope", { status: 401 }));
    const adapter = new OfficeAdapter({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onSnapshot: () => {},
    });
    await adapter.load();
    const snap = adapter.snapshot();
    expect(snap.payload).toBeNull();
    expect(snap.error).toBe("HTTP 401");
    expect(snap.provenance).toBe("none");
    adapter.dispose();
  });
});
