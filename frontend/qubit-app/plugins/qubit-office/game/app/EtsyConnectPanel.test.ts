import { describe, expect, it, vi } from "vitest";
import { createEtsyConnectPanel } from "./EtsyConnectPanel";
import {
  allByTestId, createHarness, recordingFetch, respond, textOf,
} from "./business-test-harness";

const NOT_CONFIGURED = {
  platform: "etsy",
  state: "not_configured",
  configured: false,
  connected: false,
  missing: ["client_id", "client_secret", "redirect_uri"],
  scopes: [],
};

const REQUIRED = {
  platform: "etsy",
  state: "authorization_required",
  configured: true,
  connected: false,
  missing: [],
  scopes: ["shops_r", "listings_r"],
  redirect_uri: "http://127.0.0.1:8890/cb",
};

const CONNECTED = {
  platform: "etsy",
  state: "connected",
  configured: true,
  connected: true,
  missing: [],
  scopes: ["shops_r"],
  redirect_uri: "http://127.0.0.1:8890/cb",
  expires_at: 1_700_003_600,
  has_refresh: true,
};

function backend(overrides: {
  status?: unknown;
  authorize?: () => Response | Promise<Response>;
  complete?: () => Response | Promise<Response>;
} = {}) {
  let current = overrides.status ?? REQUIRED;
  const { calls, impl } = recordingFetch((call) => {
    if (call.url.endsWith("/status")) {
      return respond(200, { ok: true, etsy: current });
    }
    if (call.url.endsWith("/authorize")) {
      if (overrides.authorize) return overrides.authorize();
      return respond(200, { ok: true, etsy: {
        url: "https://www.etsy.com/oauth/connect?code_challenge=abc",
        state: "state-1",
        expires_at: 1_700_000_600,
      } });
    }
    if (call.url.endsWith("/complete")) {
      if (overrides.complete) return overrides.complete();
      current = CONNECTED;
      return respond(200, { ok: true, etsy: CONNECTED });
    }
    if (call.url.endsWith("/disconnect")) {
      current = REQUIRED;
      return respond(200, { ok: true, etsy: REQUIRED });
    }
    return respond(404, { ok: false, code: "NOT_FOUND", detail: call.url });
  });
  return { calls, impl, status: () => current };
}

async function mount(impl: typeof fetch, extra: Record<string, unknown> = {}) {
  const h = createHarness();
  const Panel = createEtsyConnectPanel(h.React as never, h.hooks as never);
  const onClose = vi.fn();
  const openUrl = vi.fn();
  h.mount(Panel as never, {
    base: "",
    fetchImpl: impl,
    onClose,
    openUrl,
    ...extra,
  });
  await h.settle();
  return { h, onClose, openUrl };
}

describe("EtsyConnectPanel status", () => {
  it("names a not-configured account and offers no fake connect", async () => {
    const { impl } = backend({ status: NOT_CONFIGURED });
    const { h, openUrl } = await mount(impl);
    const pill = allByTestId(h, "etsy-connect-pill")[0];
    expect(textOf(pill)).toBe("Not configured");
    expect(allByTestId(h, "etsy-connect-status")[0].props["data-connected"])
      .toBe("false");
    expect(textOf(allByTestId(h, "etsy-connect-missing")[0]))
      .toContain("client_id, client_secret, redirect_uri");
    expect(allByTestId(h, "etsy-connect-start")).toHaveLength(0);
    expect(allByTestId(h, "etsy-connect-form")).toHaveLength(0);
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("shows authorization-required with the Connect and completion controls", async () => {
    const { impl } = backend();
    const { h } = await mount(impl);
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Not connected");
    expect(textOf(allByTestId(h, "etsy-connect-start")[0])).toBe("Connect Etsy");
    expect(allByTestId(h, "etsy-connect-form")).toHaveLength(1);
    expect(textOf(allByTestId(h, "etsy-connect-scopes")[0]))
      .toContain("shops_r");
  });

  it("never shows connected when the server reports a mismatch", async () => {
    const forged = { ...REQUIRED, state: "connected", connected: false };
    const { impl } = backend({ status: forged });
    const { h } = await mount(impl);
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Not connected");
    expect(allByTestId(h, "etsy-connect-connected")).toHaveLength(0);
    expect(allByTestId(h, "etsy-connect-disconnect")).toHaveLength(0);
  });

  it("shows the real connected state only from the backend", async () => {
    const { impl } = backend({ status: CONNECTED });
    const { h } = await mount(impl);
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Connected");
    expect(allByTestId(h, "etsy-connect-status")[0].props["data-connected"])
      .toBe("true");
    expect(allByTestId(h, "etsy-connect-connected")).toHaveLength(1);
    expect(allByTestId(h, "etsy-connect-disconnect")).toHaveLength(1);
    // No token value is rendered, because the backend never sends one.
    expect(h.tree.map(textOf).join(" ")).not.toContain("access");
  });

  it("renders a typed read error instead of an empty success", async () => {
    const { impl } = backend();
    const { h } = await mount(impl, {
      fetchImpl: recordingFetch(() =>
        respond(401, { ok: false })).impl,
    });
    const error = allByTestId(h, "etsy-connect-error")[0];
    expect(error).toBeDefined();
    expect(error.props.role).toBe("alert");
    expect(textOf(error)).toContain("401");
  });
});

describe("EtsyConnectPanel actions", () => {
  it("Connect Etsy opens the authorization URL and fills the state", async () => {
    const { impl, calls } = backend();
    const { h, openUrl } = await mount(impl);
    const start = allByTestId(h, "etsy-connect-start")[0];
    start.props.onClick();
    await h.settle();

    expect(openUrl).toHaveBeenCalledTimes(1);
    expect(openUrl.mock.calls[0][0]).toContain("etsy.com/oauth/connect");
    expect(allByTestId(h, "etsy-connect-state")[0].props.value).toBe("state-1");
    expect(textOf(allByTestId(h, "etsy-connect-notice")[0]))
      .toContain("paste the redirect URL");
    const authorize = calls.find((call) => call.url.endsWith("/authorize"));
    expect(authorize?.body).toEqual({ owner: true });
  });

  it("completes from a pasted redirect URL and shows connected", async () => {
    const { impl, calls } = backend();
    const { h } = await mount(impl);
    allByTestId(h, "etsy-connect-start")[0].props.onClick();
    await h.settle();
    const code = allByTestId(h, "etsy-connect-code")[0];
    code.props.onChange({
      target: { value: "http://127.0.0.1:8890/cb?code=code-1&state=state-1" },
    });
    await h.settle();
    allByTestId(h, "etsy-connect-complete")[0].props.onClick();
    await h.settle();

    const complete = calls.find((call) => call.url.endsWith("/complete"));
    expect(complete?.body).toEqual({
      owner: true, code: "code-1", state: "state-1",
    });
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Connected");
    expect(textOf(allByTestId(h, "etsy-connect-notice")[0]))
      .toBe("Etsy is connected.");
  });

  it("refuses an empty code before sending anything", async () => {
    const { impl, calls } = backend();
    const { h } = await mount(impl);
    const before = calls.length;
    allByTestId(h, "etsy-connect-complete")[0].props.onClick();
    await h.settle();
    expect(calls.length).toBe(before);
    expect(textOf(allByTestId(h, "etsy-connect-action-error")[0]))
      .toContain("redirect URL");
  });

  it("renders a typed exchange refusal and stays not connected", async () => {
    const { impl } = backend({
      complete: () => respond(409, {
        ok: false, code: "etsy_state_invalid",
        detail: "the Etsy callback state does not match",
      }),
    });
    const { h } = await mount(impl);
    allByTestId(h, "etsy-connect-start")[0].props.onClick();
    await h.settle();
    allByTestId(h, "etsy-connect-code")[0].props.onChange({
      target: { value: "code-1" },
    });
    await h.settle();
    allByTestId(h, "etsy-connect-complete")[0].props.onClick();
    await h.settle();
    const error = allByTestId(h, "etsy-connect-action-error")[0];
    expect(error.props["data-code"]).toBe("etsy_state_invalid");
    expect(textOf(error)).toContain("does not match");
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Not connected");
  });

  it("disconnect clears the real token through the backend", async () => {
    const { impl, calls } = backend({ status: CONNECTED });
    const { h } = await mount(impl);
    allByTestId(h, "etsy-connect-disconnect")[0].props.onClick();
    await h.settle();
    expect(calls.some((call) => call.url.endsWith("/disconnect"))).toBe(true);
    expect(textOf(allByTestId(h, "etsy-connect-pill")[0])).toBe("Not connected");
  });
});
