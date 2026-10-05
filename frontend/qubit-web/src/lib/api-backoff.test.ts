import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { describe, expect, it, vi } from "vitest";

import { WsTicketError, isTransientNetworkError, withBackoff } from "./api";

describe("isTransientNetworkError", () => {
  it("treats a restart's fetch failure as transient", () => {
    expect(isTransientNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isTransientNetworkError(new Error("NetworkError when attempting to fetch resource."))).toBe(
      true,
    );
    expect(isTransientNetworkError(new Error("connect ECONNREFUSED 127.0.0.1:8890"))).toBe(true);
  });

  it("does not retry refusals or deliberate aborts", () => {
    expect(isTransientNetworkError(new Error("HTTP_409: context floor"))).toBe(false);
    const abort = new Error("The user aborted a request.");
    abort.name = "AbortError";
    expect(isTransientNetworkError(abort)).toBe(false);
  });

  it("does not treat an HTTP 500 Response as transient", () => {
    // fetchJSON throws `Error("<status>: <body>")`. A non-ok Response reaches
    // isTransientNetworkError only as that Error (or as a Response object if
    // ever passed directly). Either way it must NOT be retried: a 500 is a
    // served error, not an unreachable backend.
    const res500 = new Response("Internal Server Error", { status: 500 });
    expect(isTransientNetworkError(res500)).toBe(false);
    expect(isTransientNetworkError(new Error("500: Internal Server Error"))).toBe(
      false,
    );
    expect(isTransientNetworkError(new Error("502: Bad Gateway"))).toBe(false);
  });

  it("does not treat a typed WsTicketError refusal as transient", () => {
    // The /chat terminal ws-ticket call wraps typed refusals in WsTicketError
    // (e.g. context floor). Its name is not TypeError and its message carries
    // no network phrase, so withBackoff must never retry it.
    const refusal = new WsTicketError("CONTEXT_FLOOR", "model window below floor");
    expect(isTransientNetworkError(refusal)).toBe(false);
    const httpFail = new WsTicketError("HTTP_503", "ws-ticket refused (503)");
    // Even an HTTP_503 code is a served refusal, not an unreachable backend —
    // retrying it would hammer the same refusal.
    expect(isTransientNetworkError(httpFail)).toBe(false);
  });

  it("does not treat a real fetch AbortController error as transient", async () => {
    // Raise an AbortError from a genuine fetch + AbortController against a real
    // (never-responding) HTTP server, so the classification is proven on the
    // real DOMException path a cancelled request produces — not a hand-built
    // Error. (vitest's node environment needs an absolute URL to reach fetch at
    // all; a relative "/api/..." path throws TypeError before the signal has
    // any effect, which would not exercise the abort path.)
    const server = createServer(() => {
      /* hold the connection open; the client aborts before any response */
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = (server.address() as AddressInfo).port;
    try {
      const controller = new AbortController();
      const pending = fetch(`http://127.0.0.1:${port}/api/qubit/chat/stream`, {
        method: "POST",
        signal: controller.signal,
      });
      setTimeout(() => controller.abort(), 10);
      let caught: unknown;
      try {
        await pending;
      } catch (err) {
        caught = err;
      }
      expect(caught).toBeInstanceOf(Error);
      expect((caught as Error).name).toBe("AbortError");
      // A deliberate abort must NOT be retried by withBackoff.
      expect(isTransientNetworkError(caught)).toBe(false);
    } finally {
      server.close();
    }
  });
});

describe("withBackoff", () => {
  it("recovers when the backend comes back inside the window", async () => {
    let calls = 0;
    const fn = vi.fn(async () => {
      calls += 1;
      if (calls < 3) throw new TypeError("Failed to fetch");
      return "ok";
    });
    await expect(withBackoff(fn, { baseMs: 1 })).resolves.toBe("ok");
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("gives up after the attempt budget and rethrows the real error", async () => {
    const fn = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(withBackoff(fn, { attempts: 3, baseMs: 1 })).rejects.toThrow("Failed to fetch");
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("never retries a typed refusal", async () => {
    const fn = vi.fn(async () => {
      throw new Error("HTTP_409: context floor");
    });
    await expect(withBackoff(fn, { attempts: 5, baseMs: 1 })).rejects.toThrow("HTTP_409");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("stops immediately when the caller says so (unmount)", async () => {
    const fn = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(withBackoff(fn, { baseMs: 1, shouldStop: () => true })).rejects.toBeTruthy();
    expect(fn).not.toHaveBeenCalled();
  });

  // ---- dead-end proof: >~7s outage exhausts the ladder and REJECTS ----------
  // This is the client half of the /chat terminal dead-end. When the backend is
  // down past the budget the retry ladder stops and withBackoff REJECTS rather
  // than retrying forever or swallowing the error. TerminalChatPage's wsTicket
  // .catch then shows a banner and schedules NO further retry, so no socket
  // exists and ws.onclose never re-arms — the page is wedged until reload.
  it("exhausts the bounded budget and REJECTS (no infinite retry, no swallow)", async () => {
    const fn = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    let outcome: "resolved" | "rejected" | "pending" = "pending";
    const settled = withBackoff(fn, { attempts: 5, baseMs: 1 });
    await settled.then(
      () => (outcome = "resolved"),
      () => (outcome = "rejected"),
    );
    expect(outcome).toBe("rejected"); // did NOT resolve/retry forever
    expect(fn).toHaveBeenCalledTimes(5); // bounded: exactly the attempt budget
    expect(fn.mock.calls.length).toBe(5); // no retry past the limit
    await expect(
      withBackoff(fn, { attempts: 5, baseMs: 1 }),
    ).rejects.toThrow("Failed to fetch"); // error surfaces, not swallowed
    expect(fn).toHaveBeenCalledTimes(10); // second cycle = another full budget
  });

  it("fails a typed 409-style refusal immediately without consuming the budget", async () => {
    const fn = vi.fn(async () => {
      throw new Error("HTTP_409: context floor");
    });
    await expect(withBackoff(fn, { attempts: 5, baseMs: 1 })).rejects.toThrow("HTTP_409");
    expect(fn).toHaveBeenCalledTimes(1); // refusal: zero retries consumed
    await expect(
      withBackoff(fn, { attempts: 5, baseMs: 1 }),
    ).rejects.toThrow("context floor");
    expect(fn).toHaveBeenCalledTimes(2); // still one call per cycle
  });
});
