import { describe, expect, it, vi } from "vitest";
import {
  completeEtsyAuthorization,
  disconnectEtsy,
  etsyAuthorizeUrl,
  etsyCompleteUrl,
  etsyDisconnectUrl,
  etsyStatusUrl,
  fetchEtsyStatus,
  isConnected,
  missingText,
  parseAuthorizationInput,
  parseStateInput,
  startEtsyAuthorization,
  statusLabel,
  type EtsyStatus,
} from "./etsy-connection";
import { recordingFetch, respond } from "./business-test-harness";

function status(over: Partial<EtsyStatus> = {}): EtsyStatus {
  return {
    platform: "etsy",
    state: "authorization_required",
    configured: true,
    connected: false,
    missing: [],
    scopes: ["shops_r", "listings_r"],
    redirect_uri: "http://127.0.0.1:8890/cb",
    ...over,
  };
}

describe("Etsy connection helpers", () => {
  it("builds compat URLs under /agent and rebases the base path", () => {
    expect(etsyStatusUrl("")).toBe("/agent/api/office/business/etsy/status");
    expect(etsyStatusUrl("/dash/"))
      .toBe("/dash/agent/api/office/business/etsy/status");
    expect(etsyAuthorizeUrl(""))
      .toBe("/agent/api/office/business/etsy/authorize");
    expect(etsyCompleteUrl("")).toBe("/agent/api/office/business/etsy/complete");
    expect(etsyDisconnectUrl(""))
      .toBe("/agent/api/office/business/etsy/disconnect");
  });

  it("claims connected only when the server says both fields", () => {
    expect(isConnected(status({ state: "connected", connected: true })))
      .toBe(true);
    expect(isConnected(status({ state: "connected", connected: false })))
      .toBe(false);
    expect(isConnected(status({ state: "expired", connected: true })))
      .toBe(false);
    expect(isConnected(null)).toBe(false);
  });

  it("labels each typed state and explains what is missing", () => {
    expect(statusLabel(status({ state: "connected", connected: true })))
      .toBe("Connected");
    expect(statusLabel(status({ state: "not_configured" })))
      .toBe("Not configured");
    expect(statusLabel(status({ state: "expired" }))).toBe("Expired");
    expect(missingText(status({
      state: "not_configured", configured: false,
      missing: ["client_id", "client_secret"],
    }))).toContain("client_id, client_secret");
    expect(missingText(status({ state: "authorization_required" })))
      .toContain("authorize");
    expect(missingText(status({ state: "expired", has_refresh: true })))
      .toContain("expired");
    expect(missingText(status({ state: "connected", connected: true })))
      .toBe("");
  });

  it("parses a pasted redirect URL or a bare code", () => {
    expect(parseAuthorizationInput(
      "http://127.0.0.1:8890/cb?code=abc123&state=st-1")).toBe("abc123");
    expect(parseAuthorizationInput("  raw-code  ")).toBe("raw-code");
    expect(parseAuthorizationInput("")).toBe("");
    expect(parseStateInput(
      "http://127.0.0.1:8890/cb?code=abc123&state=st-1")).toBe("st-1");
    expect(parseStateInput("raw-code")).toBe("");
  });
});

describe("Etsy connection transport", () => {
  it("reads a typed status without throwing", async () => {
    const { impl } = recordingFetch(() =>
      respond(200, { ok: true, etsy: status() }));
    const read = await fetchEtsyStatus({ fetchImpl: impl });
    expect(read.kind).toBe("ready");
    if (read.kind === "ready") {
      expect(read.status.state).toBe("authorization_required");
      expect(read.status.connected).toBe(false);
    }
  });

  it("returns a named error for 401 and a non-JSON answer", async () => {
    const unauthorized = await fetchEtsyStatus({
      fetchImpl: recordingFetch(() => respond(401, {})).impl,
    });
    expect(unauthorized.kind).toBe("error");
    if (unauthorized.kind === "error") {
      expect(unauthorized.code).toBe("UNAUTHORIZED");
      expect(unauthorized.message).toContain("401");
    }
    const bad = await fetchEtsyStatus({
      fetchImpl: recordingFetch(() =>
        new Response("not json", { status: 200 })).impl,
    });
    expect(bad.kind).toBe("error");
  });

  it("posts owner intent for start/complete/disconnect and never sends a token", async () => {
    const bodies: unknown[] = [];
    const { impl } = recordingFetch((call) => {
      bodies.push(call.body);
      if (call.url.endsWith("/status")) {
        return respond(200, { ok: true, etsy: status() });
      }
      if (call.url.endsWith("/authorize")) {
        return respond(200, { ok: true, etsy: {
          url: "https://www.etsy.com/oauth/connect?x=1",
          state: "state-1", expires_at: 123,
        } });
      }
      if (call.url.endsWith("/complete")) {
        return respond(200, { ok: true, etsy:
          status({ state: "connected", connected: true }) });
      }
      return respond(200, { ok: true, etsy:
        status({ state: "authorization_required" }) });
    });

    const started = await startEtsyAuthorization({ fetchImpl: impl });
    expect(started.url).toContain("etsy.com");
    expect(started.state).toBe("state-1");
    const connected = await completeEtsyAuthorization("code-1", "state-1", {
      fetchImpl: impl,
    });
    expect(connected.connected).toBe(true);
    const cleared = await disconnectEtsy({ fetchImpl: impl });
    expect(cleared.state).toBe("authorization_required");

    expect(bodies).toEqual([
      { owner: true },
      { owner: true, code: "code-1", state: "state-1" },
      { owner: true },
    ]);
  });

  it("surfaces a typed refusal and refuses an empty code locally", async () => {
    const { impl } = recordingFetch(() =>
      respond(409, { ok: false, code: "etsy_state_invalid",
                     detail: "state mismatch" }));
    await expect(completeEtsyAuthorization("code", "wrong", {
      fetchImpl: impl,
    })).rejects.toMatchObject({ code: "etsy_state_invalid", status: 409 });

    const spy = vi.fn() as unknown as typeof fetch;
    await expect(completeEtsyAuthorization("  ", "state", {
      fetchImpl: spy,
    })).rejects.toMatchObject({ code: "ETSY_BAD_INPUT" });
    expect(spy).not.toHaveBeenCalled();
  });
});
