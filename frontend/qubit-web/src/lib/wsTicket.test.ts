// @vitest-environment jsdom
/**
 * `/chat` ws-ticket typed-refusal contract.
 *
 * The terminal mints a short-lived single-use PTY ticket over the authed HTTP
 * route. A context-floor / unverified-window refusal must arrive as a TYPED
 * envelope (`code` + `detail`) so the terminal chrome can explain WHY it
 * refused — never a bare `HTTP 409`. A successful grant must carry the
 * non-secret model profile and the context-verification provenance.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, WsTicketError } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api.wsTicket", () => {
  beforeEach(() => {
    (window as unknown as { __QUBIT_SESSION_TOKEN__?: string })
      .__QUBIT_SESSION_TOKEN__ = "tok";
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the grant with model + verification provenance", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({
        ok: true,
        ticket: "t-1",
        expires_in: 30,
        scope: "pty",
        profile_id: "big-api",
        provider: "opencode",
        model: "big-model",
        context_tokens: 131072,
        min_context_tokens: 65536,
        context_verification: "when_available",
        context_window_source: "not-cached",
        context_verified: false,
      }),
    ));
    const grant = await api.wsTicket();
    expect(grant.ticket).toBe("t-1");
    expect(grant.model).toBe("big-model");
    expect(grant.context_verified).toBe(false);
    expect(grant.context_window_source).toBe("not-cached");
  });

  it("threads the resume target so the grant reflects the session model", async () => {
    // A resumed /chat must report the STORED session model, not the config
    // default (2026-10-05: the MODEL card said deepseek while the terminal
    // ran qubit-brain4). The server overlays `sessions.model` only when the
    // mint carries the resume id.
    let sent: Record<string, unknown> = {};
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({
        ok: true,
        ticket: "t-2",
        profile_id: "opencode-go-deepseek-v4.1-flash",
        provider: "ollama",
        model: "qubit-brain4",
        session_pinned: true,
      });
    }));
    const grant = await api.wsTicket(undefined, "pty", "dashboard-abc123");
    expect(sent.resume).toBe("dashboard-abc123");
    expect(grant.model).toBe("qubit-brain4");
  });

  it("throws a typed WsTicketError on a 409 context refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse(
        {
          ok: false,
          state: "UNAVAILABLE",
          code: "TERMINAL_CONTEXT_BELOW_MINIMUM",
          detail: "/chat requires a model with at least 65536 context tokens",
          owner: "qubit-policy",
        },
        409,
      ),
    ));
    await expect(api.wsTicket()).rejects.toBeInstanceOf(WsTicketError);
    await api.wsTicket().catch((err: WsTicketError) => {
      expect(err.code).toBe("TERMINAL_CONTEXT_BELOW_MINIMUM");
      expect(err.detail).toContain("65536");
    });
  });

  it("throws a typed error when the grant has no ticket", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true })));
    await expect(api.wsTicket()).rejects.toMatchObject({
      code: "WS_TICKET_MISSING",
    });
  });
});
