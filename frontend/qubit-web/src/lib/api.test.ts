// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  ConfirmRequiredError,
  fetchJSON,
  normalizeSessionMessage,
  parseConfirmEnvelope,
  resolveModelProfile,
  runMutationWithConfirm,
  setManagementProfile,
  settleConfirmation,
  withApiPrefix,
} from "./api";

const reloadMocks = vi.hoisted(() => ({
  attemptDashboardTokenReloadOnce: vi.fn(() => false),
  clearDashboardTokenReloadAttempt: vi.fn(),
}));

vi.mock("./dashboard-auth-reload", () => ({
  attemptDashboardTokenReloadOnce: reloadMocks.attemptDashboardTokenReloadOnce,
  clearDashboardTokenReloadAttempt: reloadMocks.clearDashboardTokenReloadAttempt,
}));

const SESSION_HEADER = "X-Hermes-Session-Token";

beforeEach(() => {
  reloadMocks.attemptDashboardTokenReloadOnce.mockReset();
  reloadMocks.attemptDashboardTokenReloadOnce.mockReturnValue(false);
  reloadMocks.clearDashboardTokenReloadAttempt.mockReset();

  Object.defineProperty(window, "__HERMES_SESSION_TOKEN__", {
    configurable: true,
    value: "stale-token",
    writable: true,
  });
  Object.defineProperty(window, "__HERMES_AUTH_REQUIRED__", {
    configurable: true,
    value: false,
    writable: true,
  });
});

afterEach(() => {
  setManagementProfile("");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonFetchMock(body: unknown = { ok: true }) {
  return vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
  );
}

describe("fetchJSON", () => {
  it("tries the one-shot reload path for loopback 401s", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        clone: () => ({
          json: async () => ({}),
        }),
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        text: async () => "Unauthorized",
      })),
    );
    reloadMocks.attemptDashboardTokenReloadOnce.mockReturnValue(true);

    const pending = fetchJSON("/api/status");
    await expect(Promise.race([pending, Promise.resolve("pending")])).resolves.toBe(
      "pending",
    );

    expect(reloadMocks.attemptDashboardTokenReloadOnce).toHaveBeenCalledTimes(1);
    expect(reloadMocks.clearDashboardTokenReloadAttempt).not.toHaveBeenCalled();
  });

  it("clears the reload latch after a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        json: async () => ({ ok: true }),
        ok: true,
        status: 200,
      })),
    );

    await expect(fetchJSON("/api/status")).resolves.toEqual({ ok: true });

    expect(reloadMocks.clearDashboardTokenReloadAttempt).toHaveBeenCalledTimes(1);
  });
});

describe("api.getModelOptions", () => {
  it("requests a live model refresh when asked", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/agent/api/model/options?refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("keeps explicit profile scoping when refreshing", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ profile: "default", refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/agent/api/model/options?profile=default&refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("api OAuth helpers", () => {
  it("starts OAuth login in gated mode without requiring an injected session token", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    expect(fetchMock).toHaveBeenCalledWith(
      "/agent/api/providers/oauth/openai-codex/start",
      expect.objectContaining({
        body: "{}",
        credentials: "include",
        method: "POST",
      }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has(SESSION_HEADER)).toBe(false);
  });

  it("still sends the injected session token for OAuth login in loopback mode", async () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get(SESSION_HEADER)).toBe("loopback-token");
  });

  it("runs provider auth mutations in gated mode via cookie auth", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await api.disconnectOAuthProvider("anthropic");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.cancelOAuthSession("oauth-session");
    await api.revealEnvVar("OPENAI_API_KEY");

    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect(init.credentials).toBe("include");
      expect((init.headers as Headers).has(SESSION_HEADER)).toBe(false);
    }
  });

  it("keeps every OAuth operation on the selected management profile", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);
    setManagementProfile("worker");

    await api.getOAuthProviders();
    await api.disconnectOAuthProvider("anthropic");
    await api.startOAuthLogin("openai-codex");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.pollOAuthSession("anthropic", "oauth-session");
    await api.cancelOAuthSession("oauth-session");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/agent/api/providers/oauth?profile=worker",
      "/agent/api/providers/oauth/anthropic?profile=worker",
      "/agent/api/providers/oauth/openai-codex/start?profile=worker",
      "/agent/api/providers/oauth/anthropic/submit?profile=worker",
      "/agent/api/providers/oauth/anthropic/poll/oauth-session?profile=worker",
      "/agent/api/providers/oauth/sessions/oauth-session?profile=worker",
    ]);
  });
});

describe("api.checkQubitUpdate", () => {
  const report = {
    install_method: "git-checkout",
    current_version: "1.0.0",
    behind: null,
    update_available: false,
    can_apply: false,
    update_command: "scripts/install.sh",
    message: "Qubit updates are operator-owned and staged.",
  };

  it("reads the truthful update report from the canonical endpoint", async () => {
    const fetchMock = jsonFetchMock(report);
    vi.stubGlobal("fetch", fetchMock);

    const info = await api.checkQubitUpdate(false);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/agent/api/qubit/update/check");
    expect(info.can_apply).toBe(false);
    expect(info.update_available).toBe(false);
    expect(info.message).toContain("operator-owned");
  });

  it("forwards the force flag to the update check", async () => {
    const fetchMock = jsonFetchMock(report);
    vi.stubGlobal("fetch", fetchMock);

    await api.checkQubitUpdate(true);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/agent/api/qubit/update/check?force=true",
    );
  });
});

function confirmEnvelope(confirmationId = "cnf-1", detail = "needs approval") {
  return {
    ok: false,
    state: "NEEDS_CONFIRMATION",
    code: "CONFIRM_REQUIRED",
    detail,
    confirmation_id: confirmationId,
  };
}

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

describe("parseConfirmEnvelope", () => {
  it("parses a typed CONFIRM_REQUIRED body", () => {
    const env = parseConfirmEnvelope(JSON.stringify(confirmEnvelope()));
    expect(env?.code).toBe("CONFIRM_REQUIRED");
    expect(env?.confirmation_id).toBe("cnf-1");
  });

  it("parses the envelope out of a fetchJSON error string", () => {
    const env = parseConfirmEnvelope(
      `409: ${JSON.stringify(confirmEnvelope("cnf-2"))}`,
    );
    expect(env?.confirmation_id).toBe("cnf-2");
  });

  it("returns null for unrelated bodies", () => {
    expect(parseConfirmEnvelope(JSON.stringify({ ok: false, code: "X" }))).toBeNull();
    expect(parseConfirmEnvelope("not json")).toBeNull();
  });
});

describe("fetchJSON staged confirmation", () => {
  it("throws a typed ConfirmRequiredError carrying the confirmation id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(confirmEnvelope("cnf-3"), 409)),
    );

    const err = await fetchJSON("/api/skills/toggle", {
      method: "PUT",
      body: JSON.stringify({ name: "x", enabled: true }),
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ConfirmRequiredError);
    const confirmErr = err as ConfirmRequiredError;
    expect(confirmErr.hasConfirmationId).toBe(true);
    expect(confirmErr.detail).toBe("needs approval");
  });
});

describe("runMutationWithConfirm", () => {
  it("settles an action stage by re-issuing with confirmation_id", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(confirmEnvelope("cnf-cron"), 409))
      .mockResolvedValueOnce(jsonResponse({ id: "1", name: "job" }, 200));
    vi.stubGlobal("fetch", fetchMock);

    const result = await runMutationWithConfirm(
      () =>
        fetchJSON("/api/cron/jobs?profile=default", {
          method: "POST",
          body: JSON.stringify({ name: "job", when: "every 60 minutes" }),
        }),
      () => true,
    );

    expect(result).toEqual({ id: "1", name: "job" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe(
      "/agent/api/cron/jobs?profile=default&confirmation_id=cnf-cron",
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "job",
      when: "every 60 minutes",
      confirmation_id: "cnf-cron",
    });
  });

  it("falls back to POST /api/ops/confirm when the re-issue re-stages", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      // initial stage (ops-journal, e.g. skills_toggle)
      .mockResolvedValueOnce(jsonResponse(confirmEnvelope("cnf-ops"), 409))
      // re-issue re-stages (ops-journal ignores confirmation_id)
      .mockResolvedValueOnce(jsonResponse(confirmEnvelope("cnf-new"), 409))
      // settle the ORIGINAL id on the shared journal
      .mockResolvedValueOnce(jsonResponse({ ok: true, state: "executed" }, 200));
    vi.stubGlobal("fetch", fetchMock);

    const result = await runMutationWithConfirm(
      () =>
        fetchJSON("/api/skills/toggle", {
          method: "PUT",
          body: JSON.stringify({ name: "x", enabled: true }),
        }),
      () => true,
    );

    expect(result).toEqual({ ok: true, state: "executed" });
    const [retryUrl, retryInit] = fetchMock.mock.calls[1];
    expect(retryUrl).toBe("/agent/api/skills/toggle?confirmation_id=cnf-ops");
    expect(JSON.parse(String(retryInit?.body))).toEqual({
      name: "x",
      enabled: true,
      confirmation_id: "cnf-ops",
    });
    const [opsUrl, opsInit] = fetchMock.mock.calls[2];
    expect(opsUrl).toBe("/agent/api/ops/confirm");
    expect(JSON.parse(String(opsInit?.body))).toEqual({
      confirmation_id: "cnf-ops",
    });
  });

  it("re-issues the same request with confirm:true when the stage has no id", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ ...confirmEnvelope("", "schedule create"), ok: false }, 409),
      )
      .mockResolvedValueOnce(jsonResponse({ id: "1", name: "job" }, 200));
    vi.stubGlobal("fetch", fetchMock);

    const result = await runMutationWithConfirm(
      () =>
        fetchJSON("/api/cron/jobs?profile=default", {
          method: "POST",
          body: JSON.stringify({ name: "job", when: "every 60 minutes" }),
        }),
      () => true,
    );

    expect(result).toEqual({ id: "1", name: "job" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/agent/api/cron/jobs?profile=default&confirm=true");
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "job",
      when: "every 60 minutes",
      confirm: true,
    });
  });

  it("rethrows when the human declines", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(confirmEnvelope("cnf-decline"), 409)),
    );

    const err = await runMutationWithConfirm(
      () => fetchJSON("/api/skills/toggle", { method: "PUT" }),
      () => false,
    ).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ConfirmRequiredError);
    expect((err as ConfirmRequiredError).confirmationId).toBe("cnf-decline");
  });

  it("rethrows unrelated errors untouched", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ok: false, code: "BOOM" }, 400)),
    );

    await expect(
      runMutationWithConfirm(() => fetchJSON("/api/skills/toggle", { method: "PUT" })),
    ).rejects.toThrow(/400/);
  });
});

describe("api.restartGateway confirmation", () => {
  it("stages with an empty body, then settles with confirm:true in the body", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            ok: false,
            code: "GATEWAY_CONFIRM_REQUIRED",
            state: "CONFIRM_REQUIRED",
            detail: "gateway restart changes a service; re-issue with confirm",
          },
          409,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, name: "gateway-restart", state: "RESTARTING" }, 200),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.restartGateway(() => true);

    expect(result).toMatchObject({ ok: true, state: "RESTARTING" });
    // Initial staging call: JSON body with no confirm.
    const [firstUrl, firstInit] = fetchMock.mock.calls[0];
    expect(firstUrl).toBe("/agent/api/gateway/restart");
    expect(firstInit?.method).toBe("POST");
    expect(JSON.parse(String(firstInit?.body))).toEqual({});
    // Settle: same route, confirm injected into the JSON body the supervisor
    // reads (a query-only confirm would never be seen).
    const [retryUrl, retryInit] = fetchMock.mock.calls[1];
    expect(retryUrl).toBe("/agent/api/gateway/restart?confirm=true");
    expect(JSON.parse(String(retryInit?.body))).toEqual({ confirm: true });
  });

  it("declining the prompt leaves the gateway un-restarted", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(confirmEnvelope("cnf-gw"), 409));
    vi.stubGlobal("fetch", fetchMock);

    const err = await api.restartGateway(() => false).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ConfirmRequiredError);
    // Only the staging call fired; no confirm-settled mutation was sent.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("api.deleteCronJob", () => {
  it("completes the staged delete with confirm on body and query", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(confirmEnvelope(""), 409))
      .mockResolvedValueOnce(jsonResponse({ ok: true }, 200));
    vi.stubGlobal("fetch", fetchMock);

    await api.deleteCronJob("7", "default", () => true);

    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/agent/api/cron/jobs/7?profile=default&confirm=true");
    expect(init?.method).toBe("DELETE");
  });
});

describe("api.createCronJob", () => {
  it("maps the editor schedule onto the compat `when` field", async () => {
    const fetchMock = jsonFetchMock({ id: "1" });
    vi.stubGlobal("fetch", fetchMock);

    await api.createCronJob(
      { name: "job", prompt: "ping", schedule: "every 60 minutes" },
      "default",
      () => true,
    );

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "job",
      prompt: "ping",
      schedule: "every 60 minutes",
      when: "every 60 minutes",
    });
  });
});

describe("api.createSkill", () => {
  it("posts a validated manifest with a real sha256 content hash", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await api.createSkill(
      { name: "Release Helper", content: "# Release\n\nCut notes." },
      undefined,
      () => true,
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/agent/api/skills");
    const body = JSON.parse(String(init?.body)) as {
      manifest: Record<string, unknown>;
    };
    expect(body.manifest.skill_id).toBe("release-helper");
    expect(body.manifest.license).toBe("proprietary");
    expect(body.manifest.capabilities).toEqual(["skill.local"]);
    expect(body.manifest.scopes).toEqual(["local"]);
    expect(body.manifest.content_hash).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe("api.saveConfig / api.powerAction / api.getPowerStatus", () => {
  it("saveConfig stages then confirms when the backend requires it", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(confirmEnvelope(""), 409))
      .mockResolvedValueOnce(jsonResponse({ ok: true }, 200));
    vi.stubGlobal("fetch", fetchMock);

    await api.saveConfig({ ui: { theme: "dark" } }, "", false, () => true);

    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/agent/api/config?confirm=true");
    const body = JSON.parse(String(init?.body));
    expect(body.confirm).toBe(true);
    expect(body.config).toEqual({ ui: { theme: "dark" } });
  });

  it("posts an explicitly confirmed power action", async () => {
    const fetchMock = jsonFetchMock({ state: "OK", health: "degraded" });
    vi.stubGlobal("fetch", fetchMock);

    await api.powerAction("emergency-stop");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/agent/api/power");
    expect(JSON.parse(String(init?.body))).toEqual({
      action: "emergency-stop",
      confirmed: true,
    });
  });

  it("reads the live power control status", async () => {
    const fetchMock = jsonFetchMock({
      state: "OK",
      control: { emergency_stop: true, global_disable: false },
    });
    vi.stubGlobal("fetch", fetchMock);

    const status = await api.getPowerStatus();

    expect(fetchMock.mock.calls[0][0]).toBe("/agent/api/power");
    expect(status.control?.emergency_stop).toBe(true);
  });
});

describe("settleConfirmation", () => {
  // The pending-approvals banner renders journal entries and settles them by
  // id. That endpoint consumes the shared approval journal (not the per-action
  // compat re-issue), so it is kept as an explicit id-only helper.
  it("posts the confirmation id to the shared journal endpoint", async () => {
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await settleConfirmation("cnf-42");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/agent/api/ops/confirm");
    expect(JSON.parse(String(init?.body))).toEqual({ confirmation_id: "cnf-42" });
  });

  // The ops-confirm envelope fabricates ok:true/state:executed while the real
  // outcome rides under receipt.result.state (AI8 D-5/D-9). Settling must
  // surface the failure, not report a fake success.
  it("rejects a 200 envelope whose receipt reports a failed result", async () => {
    vi.stubGlobal(
      "fetch",
      jsonFetchMock({
        ok: true,
        state: "executed",
        detail: "That failed: MISSING.",
        receipt: {
          result: {
            state: "MISSING",
            detail: "unknown brain profile 'deepseek-v4-flash'",
          },
        },
      }),
    );

    await expect(settleConfirmation("cnf-fab")).rejects.toThrow(
      /MISSING: unknown brain profile/,
    );
  });

  it("rejects a 200 envelope that says ok:false", async () => {
    vi.stubGlobal(
      "fetch",
      jsonFetchMock({ ok: false, code: "REFUSED", detail: "nope" }),
    );

    await expect(settleConfirmation("cnf-no")).rejects.toThrow(/REFUSED: nope/);
  });
});

describe("api.getLogs", () => {
  it("sends the backend search filter only when non-blank", async () => {
    const fetchMock = jsonFetchMock({ file: "agent", lines: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getLogs({ file: "agent", lines: 100, search: "  timeout  " });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/agent/api/logs?file=agent&lines=100&search=timeout",
    );

    await api.getLogs({ file: "agent", lines: 100, search: "   " });
    expect(fetchMock.mock.calls[1][0]).toBe("/agent/api/logs?file=agent&lines=100");
  });
});

describe("api.setProfileModel honesty guard", () => {
  it("rejects a fabricated provider echo (AI8 D-3)", async () => {
    vi.stubGlobal(
      "fetch",
      jsonFetchMock({
        ok: true,
        provider: "ollama-local",
        model: "deepseek-v4-flash",
      }),
    );

    await expect(
      api.setProfileModel("qubit-brain4", "opencode-go", "deepseek-v4-flash"),
    ).rejects.toThrow(/PROFILE_MODEL_NOT_APPLIED/);
  });

  it("accepts the applied provider/model", async () => {
    vi.stubGlobal(
      "fetch",
      jsonFetchMock({
        ok: true,
        provider: "opencode-go",
        model: "deepseek-v4-flash",
      }),
    );

    await expect(
      api.setProfileModel("qubit-brain4", "opencode-go", "deepseek-v4-flash"),
    ).resolves.toMatchObject({ provider: "opencode-go" });
  });
});

describe("api per-subscription webhook operations", () => {
  function confirmThenOk(first: unknown, second: unknown) {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify(first), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(second), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  const gate = {
    ok: false,
    code: "CONFIRM_REQUIRED",
    state: "NEEDS_CONFIRMATION",
    detail: "requires confirmation",
  };

  it("rotates the signing secret through the confirm round-trip", async () => {
    const fetchMock = confirmThenOk(gate, {
      ok: true,
      reference_kind: "file:",
    });
    const res = await api.rotateWebhookSecret("hook", () => true);
    expect(res.reference_kind).toBe("file:");
    expect(fetchMock.mock.calls[0][0]).toBe("/agent/api/webhooks/hook/rotate");
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      "/agent/api/webhooks/hook/rotate",
    );
    expect(String(fetchMock.mock.calls[1][0])).toContain("confirm=true");
  });

  it("sends a test delivery and surfaces the honest verdict", async () => {
    const fetchMock = confirmThenOk(gate, {
      ok: true,
      verdict: "FAILED",
      error: "connection refused",
    });
    const res = await api.testWebhook("hook", () => true);
    expect(res.verdict).toBe("FAILED");
    expect(res.error).toBe("connection refused");
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      "/agent/api/webhooks/hook/test",
    );
  });

  it("retries the last failed delivery", async () => {
    confirmThenOk(gate, { ok: true, verdict: "DELIVERED" });
    const res = await api.retryWebhook("hook", () => true);
    expect(res.verdict).toBe("DELIVERED");
  });

  it("reads delivery receipts with the requested limit", async () => {
    const fetchMock = jsonFetchMock({
      ok: true,
      deliveries: [{ verdict: "DELIVERED" }],
      count: 1,
      subscription: "hook",
    });
    vi.stubGlobal("fetch", fetchMock);
    const res = await api.getWebhookDeliveries("hook", 25);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/agent/api/webhooks/hook/deliveries?limit=25",
    );
    expect(res.count).toBe(1);
  });
});

describe("api MCP tool invocation", () => {
  it("invokes through the confirm round-trip with tool + args", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            code: "CONFIRM_REQUIRED",
            state: "NEEDS_CONFIRMATION",
            detail: "invoke requires confirmation",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, state: "OK" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const res = await api.invokeMcpTool(
      "cgraph-graft",
      "graft_find_code",
      { query: "x" },
      () => true,
    );
    expect(res.state).toBe("OK");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/agent/api/mcp/servers/cgraph-graft/invoke",
    );
    const body = JSON.parse(
      String((fetchMock.mock.calls[1][1] as RequestInit).body),
    );
    expect(body).toMatchObject({
      tool: "graft_find_code",
      args: { query: "x" },
      confirm: true,
    });
  });
});

describe("api profile row-action confirm wiring", () => {
  it("settles a staged rename by re-issuing the PATCH with confirm", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            code: "CONFIRM_REQUIRED",
            state: "NEEDS_CONFIRMATION",
            detail: "profile rename requires confirmation",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, name: "new" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const res = await api.renameProfile("old", "new", () => true);
    expect(res.name).toBe("new");
    const body = JSON.parse(
      String((fetchMock.mock.calls[1][1] as RequestInit).body),
    );
    expect(body).toMatchObject({ new_name: "new", confirm: true });
  });

  it("settles a staged delete via the query string", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            code: "CONFIRM_REQUIRED",
            state: "NEEDS_CONFIRMATION",
            detail: "profile delete requires confirmation",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, removed: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const res = await api.deleteProfile("gone", () => true);
    expect(res.removed).toBe(true);
    expect(String(fetchMock.mock.calls[1][0])).toContain("confirm=true");
  });
});

describe("withApiPrefix", () => {
  it("maps the vendored /api namespace onto the Qubit-owned /agent prefix", () => {
    expect(withApiPrefix("/api/status")).toBe("/agent/api/status");
    expect(withApiPrefix("/api/qubit/chat")).toBe("/agent/api/qubit/chat");
    expect(withApiPrefix("/api/plugins/kanban/boards?x=1")).toBe(
      "/agent/api/plugins/kanban/boards?x=1",
    );
  });

  it("leaves non-API paths (auth, plugin assets) unchanged", () => {
    expect(withApiPrefix("/auth/logout")).toBe("/auth/logout");
    expect(withApiPrefix("/plugins/fleet.json")).toBe("/plugins/fleet.json");
  });
});

describe("resolveModelProfile", () => {
  const PROFILES = [
    {
      id: "qubit-brain4",
      kind: "local",
      provider: "ollama",
      model: "qubit-brain4",
      locality: "local",
      enabled: true,
      default: true,
      context_tokens: 4096,
      min_context_tokens: 65536,
      meets_floor: false,
      code: "TERMINAL_CONTEXT_BELOW_MINIMUM",
      has_credential: false,
    },
    {
      id: "qubit-go",
      kind: "api",
      provider: "opencode-go",
      model: "deepseek-v4-flash",
      locality: "external",
      enabled: true,
      default: false,
      context_tokens: 200000,
      min_context_tokens: 65536,
      meets_floor: true,
      code: "OK",
      has_credential: true,
    },
  ];

  it("matches provider+model against the real registry", () => {
    expect(
      resolveModelProfile(PROFILES, "opencode-go", "deepseek-v4-flash")?.id,
    ).toBe("qubit-go");
  });

  it("accepts a model-only match and rejects an unknown model", () => {
    expect(resolveModelProfile(PROFILES, "", "qubit-brain4")?.id).toBe(
      "qubit-brain4",
    );
    expect(resolveModelProfile(PROFILES, "opencode-go", "nope")).toBeUndefined();
    expect(resolveModelProfile(PROFILES, "opencode-go", "")).toBeUndefined();
  });
});

describe("api.setModelAssignment", () => {
  it("posts the main provider/model to the canonical /api/model/set route", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          ok: true,
          scope: "main",
          provider: "opencode-go",
          model: "deepseek-v4-flash",
        },
        200,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.setModelAssignment({
      scope: "main",
      provider: "opencode-go",
      model: "deepseek-v4-flash",
    });

    expect(result.ok).toBe(true);
    expect(result.model).toBe("deepseek-v4-flash");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/agent/api/model/set");
    expect(url).not.toContain("/terminal/profiles");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      scope: "main",
      provider: "opencode-go",
      model: "deepseek-v4-flash",
    });
  });

  it("passes through the confirm_required envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        jsonResponse(
          { ok: false, confirm_required: true, confirm_message: "expensive" },
          200,
        ),
      ),
    );

    const result = await api.setModelAssignment({
      scope: "main",
      provider: "openai-api",
      model: "o1",
    });
    expect(result.confirm_required).toBe(true);
  });

  it("surfaces the backend's typed refusal instead of a fabricated success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        jsonResponse(
          { ok: false, code: "MODEL_ENDPOINT_REQUIRED", detail: "no endpoint" },
          400,
        ),
      ),
    );

    await expect(
      api.setModelAssignment({
        scope: "main",
        provider: "openrouter",
        model: "x/y",
      }),
    ).rejects.toMatchObject({ code: "MODEL_ENDPOINT_REQUIRED" });
  });

  it("refuses auxiliary task pins with a typed error", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      api.setModelAssignment({
        scope: "auxiliary",
        provider: "opencode-go",
        model: "deepseek-v4-flash",
        task: "vision",
      }),
    ).rejects.toMatchObject({ code: "AUX_PIN_REFUSED" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("normalizeSessionMessage", () => {
  // The Qubit chat-session store nests tool_calls/reasoning under `meta`; the
  // transcript renders the top-level shape. Mapping must never drop them.
  it("flattens meta.tool_calls / meta.reasoning onto the top level", () => {
    const msg = normalizeSessionMessage({
      role: "assistant",
      content: "",
      meta: {
        tool_calls: [
          { id: "call_1", function: { name: "write_file", arguments: "{}" } },
        ],
        reasoning: "weighing options",
      },
    });
    expect(msg.tool_calls?.[0].function.name).toBe("write_file");
    expect(msg.reasoning).toBe("weighing options");
  });

  it("prefers top-level fields and falls back to reasoning_content", () => {
    const msg = normalizeSessionMessage({
      role: "assistant",
      content: null,
      tool_name: "terminal",
      meta: { reasoning_content: "chain" },
    });
    expect(msg.tool_name).toBe("terminal");
    expect(msg.reasoning).toBe("chain");
  });

  it("leaves a message without meta unchanged", () => {
    const msg = { role: "user" as const, content: "hi" };
    expect(normalizeSessionMessage(msg)).toEqual(msg);
  });
});

describe("api.uploadFile", () => {
  // Ported from the upstream dashboard (web/src/lib/api.ts:543-557): raw bytes
  // stream as multipart/form-data to /api/files/upload-stream. The old path
  // read the file as lossy UTF-8 text and POSTed JSON, which mangled binary
  // payloads yet still reported success.
  it("POSTs multipart/form-data with path, overwrite, and the file bytes", async () => {
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["hello bytes"], "probe.txt", { type: "text/plain" });
    await api.uploadFile("/tmp/project/probe.txt", file, true);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/agent/api/files/upload-stream");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("path")).toBe("/tmp/project/probe.txt");
    expect(form.get("overwrite")).toBe("true");
    const sent = form.get("file") as File;
    expect(sent.name).toBe("probe.txt");
    // No explicit Content-Type: the browser must add the multipart boundary.
    const headers = new Headers(init.headers);
    expect(headers.has("Content-Type")).toBe(false);
  });

  it("surfaces the typed FILES_BINARY refusal instead of faking success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(
        async () =>
          new Response(
            JSON.stringify({
              ok: false,
              state: "UNAVAILABLE",
              code: "FILES_BINARY",
              detail:
                "Binary uploads are not stored; Qubit serves bounded UTF-8 text writes through the confined file broker.",
              qubit_counterpart: "POST /api/fs/write-text (text only)",
            }),
            { status: 403, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );

    const file = new File([new Uint8Array([0, 159, 146, 150])], "pic.bin");
    await expect(api.uploadFile("/tmp/project/pic.bin", file, true)).rejects.toThrow(
      /FILES_BINARY/,
    );
  });

  it("settles a broker 409 by re-issuing the multipart with confirmation_id", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            state: "NEEDS_CONFIRMATION",
            code: "CONFIRM_REQUIRED",
            detail: "writing 'probe.txt' is a brokered, confirmed, reversible confined file patch",
            confirmation_id: "cid-upload-1",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["hello"], "probe.txt", { type: "text/plain" });
    await runMutationWithConfirm(
      () => api.uploadFile("/tmp/project/probe.txt", file, true),
      () => true,
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [firstUrl] = fetchMock.mock.calls[0] as [string];
    const [secondUrl, secondInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(firstUrl).toBe("/agent/api/files/upload-stream");
    expect(secondUrl).toContain("confirmation_id=cid-upload-1");
    expect(secondInit.body).toBeInstanceOf(FormData);
  });
});
