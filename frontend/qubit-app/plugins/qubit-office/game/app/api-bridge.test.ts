import { describe, expect, it, vi } from "vitest";
import { installOfficeApiBridge } from "./api-bridge";

// The bridge runs on a real `Request`, and node's Request (unlike a browser's)
// refuses a relative URL — the app always arrives with one already resolved
// against the document, so the tests hand it the absolute form. This is the
// same request the bridge sees in the browser, not a weaker stand-in.
const ORIGIN = "http://localhost:8890";
const APP_HREF = `${ORIGIN}/plugin-apps/qubit-office/habblaud/`;
const abs = (path: string) => `${ORIGIN}${path}`;

describe("installOfficeApiBridge", () => {
  it("intercepts the shipped client's startup /api/access fetch and rewrites every supported GET with session auth", async () => {
    const calls: Request[] = [];
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      __QUBIT_SESSION_TOKEN__: "startup-session",
      fetch: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(new Request(input, init));
        return new Response("{}", { status: 200 });
      }),
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);

    // Mirrors the literal `v(fetch)` at module startup in the shipped app
    // chunk. It runs immediately after the iframe navigation begins.
    await fake.fetch(abs("/api/access"), {
      credentials: "same-origin", cache: "no-store",
    });
    await fake.fetch(abs("/api/agents/7"));
    await fake.fetch(abs("/api/agents/a%2Fb"));
    await fake.fetch(abs("/api/sessions/recent?limit=5"));
    await fake.fetch(abs("/api/timeline/days"));
    await fake.fetch(abs("/api/timeline/20711"));
    await fake.fetch(abs("/api/stats/days"));
    await fake.fetch(abs("/api/stats?day=20711"));

    expect(calls.map((request) => request.url)).toEqual([
      "http://localhost:8890/agent/api/office/habblaud/access",
      "http://localhost:8890/agent/api/office/habblaud/agents/7",
      "http://localhost:8890/agent/api/office/habblaud/agents/a%2Fb",
      "http://localhost:8890/agent/api/office/habblaud/sessions/recent?limit=5",
      "http://localhost:8890/agent/api/office/habblaud/timeline/days",
      "http://localhost:8890/agent/api/office/habblaud/timeline/20711",
      "http://localhost:8890/agent/api/office/habblaud/stats/days",
      "http://localhost:8890/agent/api/office/habblaud/stats?day=20711",
    ]);
    expect(calls.every((request) => request.method === "GET")).toBe(true);
    expect(calls.every((request) => request.headers.get("X-Qubit-Session-Token") === "startup-session")).toBe(true);
    restore();
  });

  it("installs the bridge synchronously before iframe navigation can dispatch startup fetch", async () => {
    const calls: string[] = [];
    const original = async (input: RequestInfo | URL) => {
      calls.push(input instanceof Request ? input.url : String(input));
      return new Response("{}", { status: 200 });
    };
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      __QUBIT_SESSION_TOKEN__: "boot-token",
      fetch: original,
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    // This is the first fetch the client issues while its module graph starts.
    await fake.fetch(abs("/api/access"), { credentials: "same-origin" });
    expect(calls).toEqual([`${ORIGIN}/agent/api/office/habblaud/access`]);
    restore();
  });

  it("refuses unsupported operations with a visible typed response and never forwards", async () => {
    const calls: unknown[] = [];
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      fetch: vi.fn(async (input: RequestInfo | URL) => {
        calls.push(input);
        return new Response("{}", { status: 200 });
      }),
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    const mutation = await fake.fetch(abs("/api/messages"), { method: "POST" });
    const unsupportedRead = await fake.fetch(abs("/api/permissions"));
    const unsupportedItem = await fake.fetch(abs("/api/permissions/one"));
    const unrelated = await fake.fetch(abs("/api/deliveries"));
    expect(mutation.status).toBe(501);
    expect(unsupportedRead.status).toBe(501);
    expect(unsupportedItem.status).toBe(501);
    expect(unrelated.status).toBe(501);
    expect(calls).toHaveLength(0);
    restore();
  });

  it("refuses spawn/session endpoints so the office cannot create an agent session", async () => {
    const calls: unknown[] = [];
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      fetch: vi.fn(async (input: RequestInfo | URL) => {
        calls.push(input);
        return new Response("{}", { status: 200 });
      }),
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    const folders = await fake.fetch(abs("/api/spawn/folders"));
    const create = await fake.fetch(abs("/api/spawn"), { method: "POST", body: "{}" });
    const status = await fake.fetch(abs("/api/spawn/session-1"));
    const put = await fake.fetch(abs("/api/agents/7"), { method: "PUT", body: "{}" });
    const patch = await fake.fetch(abs("/api/agents/7"), { method: "PATCH", body: "{}" });
    const remove = await fake.fetch(abs("/api/agents/7"), { method: "DELETE" });
    expect([folders.status, create.status, status.status, put.status, patch.status, remove.status]).toEqual([501, 501, 501, 501, 501, 501]);
    expect((await put.json()).disabled).toBe(true);
    expect(calls).toHaveLength(0);
    restore();
  });

  it("honours the host API prefix global and explicit per-install override", async () => {
    const seen: string[] = [];
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      __QUBIT_API_PREFIX__: "/tenant/agent/",
      fetch: vi.fn(async (input: RequestInfo | URL) => {
        seen.push(input instanceof Request ? input.url : String(input));
        return new Response("{}", { status: 200 });
      }),
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    await fake.fetch(abs("/api/access"));
    expect(seen[0]).toBe(`${ORIGIN}/tenant/agent/api/office/habblaud/access`);
    restore();

    seen.length = 0;
    const restoreOverride = installOfficeApiBridge(fake, { apiPrefix: "/custom/agent/" });
    await fake.fetch(abs("/api/access"));
    expect(seen[0]).toBe(`${ORIGIN}/custom/agent/api/office/habblaud/access`);
    restoreOverride();
  });

  it("restores the iframe fetch when the bridge is removed", async () => {
    const calls: unknown[] = [];
    const originalFetch = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(input);
      return new Response("ok", { status: 200 });
    });
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      fetch: originalFetch,
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    restore();
    await fake.fetch(abs("/api/spawn"));
    expect(originalFetch).toHaveBeenCalledTimes(1);
  });

  it("restores its fetch wrapper when the bridge is removed", async () => {
    const originalFetch = vi.fn(async () => new Response("ok", { status: 200 }));
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      fetch: originalFetch,
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    const bridge = fake.fetch;
    restore();
    expect(fake.fetch).toBe(originalFetch);
    await fake.fetch(abs("/api/spawn"));
    expect(originalFetch).toHaveBeenCalledTimes(1);
    expect(bridge).not.toBe(fake.fetch);
  });

  it("does not intercept external origins or non-API paths", async () => {
    const calls: unknown[] = [];
    const fake = {
      location: { href: APP_HREF, origin: ORIGIN },
      fetch: vi.fn(async (input: RequestInfo | URL) => {
        calls.push(input);
        return new Response("ok", { status: 200 });
      }),
    } as unknown as Window;
    const restore = installOfficeApiBridge(fake);
    await fake.fetch("https://example.com/api/access");
    await fake.fetch(abs("/assets/logo.png"));
    expect(calls).toHaveLength(2);
    restore();
  });

  // ------------------------------------------------------------------------
  // The session token: without it the rewritten request 401s and the client
  // navigates to /login -> /sessions, so the office never renders.
  // ------------------------------------------------------------------------
  const tokenWindow = (token?: string, parentToken?: string) => {
    const seen: Array<{ url: string; token: string | null }> = [];
    const win = {
      location: { href: "http://localhost:8890/plugin-apps/qubit-office/habblaud/", origin: "http://localhost:8890" },
      fetch: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = new Request(input, init);
        seen.push({ url: req.url, token: req.headers.get("X-Qubit-Session-Token") });
        return new Response("{}", { status: 200 });
      }),
    } as unknown as Window & { __QUBIT_SESSION_TOKEN__?: string; parent: unknown };
    if (token !== undefined) win.__QUBIT_SESSION_TOKEN__ = token;
    (win as { parent: unknown }).parent =
      parentToken === undefined ? win : { __QUBIT_SESSION_TOKEN__: parentToken };
    return { win, seen };
  };

  it("attaches the session token to a rewritten request", async () => {
    const { win, seen } = tokenWindow("sess-abc");
    const restore = installOfficeApiBridge(win);
    await win.fetch("http://localhost:8890/api/access");
    expect(seen[0].url).toBe("http://localhost:8890/agent/api/office/habblaud/access");
    expect(seen[0].token).toBe("sess-abc");
    restore();
  });

  it("falls back to the parent document's token when the frame has none", async () => {
    const { win, seen } = tokenWindow(undefined, "parent-xyz");
    const restore = installOfficeApiBridge(win);
    await win.fetch("http://localhost:8890/api/access");
    expect(seen[0].token).toBe("parent-xyz");
    restore();
  });

  it("preserves a token the caller already attached", async () => {
    const { win, seen } = tokenWindow("doc-token");
    const restore = installOfficeApiBridge(win);
    await win.fetch("http://localhost:8890/api/access", {
      headers: { "X-Qubit-Session-Token": "caller-token" },
    });
    expect(seen[0].token).toBe("caller-token");
    restore();
  });

  it("preserves a token already on the request URL", async () => {
    const { win, seen } = tokenWindow("doc-token");
    const restore = installOfficeApiBridge(win);
    await win.fetch("http://localhost:8890/api/access?token=url-token");
    expect(seen[0].url).toContain("token=url-token");
    restore();
  });
});
