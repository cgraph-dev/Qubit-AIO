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
  it("resolves the main provider/model to a real profile and persists it", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          [
            {
              id: "qubit-go",
              kind: "api",
              provider: "opencode-go",
              model: "deepseek-v4-flash",
              locality: "external",
              enabled: true,
              default: true,
              context_tokens: 200000,
              min_context_tokens: 65536,
              meets_floor: true,
              code: "OK",
              has_credential: true,
            },
          ],
          200,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, profile_id: "qubit-go", meets_floor: true }, 200),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.setModelAssignment({
      scope: "main",
      provider: "opencode-go",
      model: "deepseek-v4-flash",
    });

    expect(result.ok).toBe(true);
    expect(result.model).toBe("deepseek-v4-flash");
    // Reads the real registry, then writes the profile — never the compat
    // `/api/model/set` brain-profile path.
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/agent/api/terminal/profiles",
    );
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/agent/api/terminal/profile");
    expect(JSON.parse(String(init?.body))).toEqual({ profile_id: "qubit-go" });
  });

  it("refuses an unknown provider/model with a typed error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        jsonResponse([], 200),
      ),
    );

    await expect(
      api.setModelAssignment({
        scope: "main",
        provider: "opencode-go",
        model: "nope",
      }),
    ).rejects.toMatchObject({ code: "UNKNOWN_MODEL_PROFILE" });
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
