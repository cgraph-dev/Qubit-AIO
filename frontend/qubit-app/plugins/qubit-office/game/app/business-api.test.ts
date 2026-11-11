import { describe, expect, it } from "vitest";
import {
  BUSINESS_PATH,
  BusinessApiError,
  businessUrl,
  createWarRoomSession,
  bootstrapOfficeSeats,
  fetchAgents,
  businessFetch,
} from "./business-api";
import { recordingFetch, respond } from "./business-test-harness";

describe("Business OS URLs", () => {
  it("lives under the office compat family and the /agent prefix", () => {
    expect(businessUrl("/overview", "")).toBe(
      "/agent/api/office/business/overview");
    expect(BUSINESS_PATH).toBe("/api/office/business");
    expect(businessUrl("/agents", "/qubit/", { tenant_id: "ten_1" })).toBe(
      "/qubit/agent/api/office/business/agents?tenant_id=ten_1");
  });

  it("omits empty query values instead of sending blank filters", () => {
    expect(businessUrl("/agents", "", {
      tenant_id: "ten_1", department: "", state: undefined,
    })).toBe("/agent/api/office/business/agents?tenant_id=ten_1");
  });
});

describe("the client never invents success", () => {
  it("throws the typed refusal envelope on ok:false", async () => {
    const { impl } = recordingFetch(() => respond(403, {
      ok: false, code: "BUSINESS_OWNER_ONLY", detail: "owner only",
    }));
    await expect(fetchAgents({}, { fetchImpl: impl })).rejects.toMatchObject({
      code: "BUSINESS_OWNER_ONLY", status: 403,
    });
  });

  it("throws on a 500 with a non-JSON body", async () => {
    const { impl } = recordingFetch(() => new Response("not json", {
      status: 500,
    }));
    await expect(fetchAgents({}, { fetchImpl: impl })).rejects.toBeInstanceOf(
      BusinessApiError);
  });

  it("maps a 401 to an auth failure, not an empty list", async () => {
    const { impl } = recordingFetch(() => respond(401, {}));
    await expect(fetchAgents({}, { fetchImpl: impl })).rejects.toMatchObject({
      code: "BUSINESS_UNAUTHORIZED", status: 401,
    });
  });
});

describe("mutations carry owner intent and the session token", () => {
  it("injects owner:true on POST, never on GET", async () => {
    const { calls, impl } = recordingFetch(() => respond(200, {
      ok: true, created: true, tenant_id: "ten_1",
      session: { session_id: "wrs_1" },
    }));
    const originalWindow = (globalThis as { window?: unknown }).window;
    (globalThis as { window?: unknown }).window = {
      __QUBIT_SESSION_TOKEN__: "tok",
      parent: {},
    };
    try {
      await createWarRoomSession({ title: "Idea" }, { fetchImpl: impl });
      await businessFetch("/overview", { fetchImpl: impl });
    } finally {
      (globalThis as { window?: unknown }).window = originalWindow;
    }
    const post = calls.find((call) => call.method === "POST");
    const get = calls.find((call) => call.method === "GET");
    expect(post?.body.owner).toBe(true);
    expect(get?.body).toBeNull();
  });

  it("bootstraps the office roster through one owner-gated POST", async () => {
    const { calls, impl } = recordingFetch(() => respond(200, {
      ok: true, schema: "qubit-office-business-bootstrap/1",
      tenant_id: "ten_personal", business_id: "biz_1",
      created_tenant: true, created_business: true, plan_ok: true,
      total: 283, created: 283, existing: 0, created_slugs: [],
    }));
    const result = await bootstrapOfficeSeats({}, { fetchImpl: impl });
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(new URL(calls[0].url).pathname).toBe(
      "/agent/api/office/business/bootstrap");
    expect(calls[0].body.owner).toBe(true);
    expect(result.created).toBe(283);
  });

  it("keeps an explicit owner value when a caller supplies one", async () => {
    const { calls, impl } = recordingFetch(() => respond(200, {
      ok: true, created: false, tenant_id: "ten_1",
      session: { session_id: "wrs_1" },
    }));
    await createWarRoomSession(
      { title: "Idea", idempotency_key: "k" }, { fetchImpl: impl });
    expect(calls[0].body.owner).toBe(true);
    expect(calls[0].body.idempotency_key).toBe("k");
  });
});
