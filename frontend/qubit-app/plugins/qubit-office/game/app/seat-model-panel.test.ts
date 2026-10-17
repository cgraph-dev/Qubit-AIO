import { describe, expect, it, vi } from "vitest";
import {
  assignmentPayload,
  authHeaders,
  describeSeatRouting,
  describeWriteFailure,
  fetchSeatModel,
  modelsFor,
  saveSeatModel,
  seatModelUrl,
  selectableProviders,
  type SeatModelState,
} from "./seat-model-panel";

const state = (over: Partial<SeatModelState> = {}): SeatModelState => ({
  ok: true,
  agent_id: 3,
  slug: "lead-quality",
  model: "",
  provider: "",
  inherits: true,
  providers: [
    { slug: "opencode-go", name: "OpenCode Go", models: ["deepseek-v4.1-flash"], authenticated: true },
    { slug: "omniroute", name: "OmniRoute", models: ["auto"], authenticated: true },
    { slug: "cheapvibecode", name: "CheapVibeCode", models: ["grok-4.7"], authenticated: false },
    { slug: "empty", name: "No Models", models: [], authenticated: true },
  ],
  ...over,
});

describe("seat model route", () => {
  it("addresses the seat by id under the office habblaud prefix", () => {
    expect(seatModelUrl(3)).toBe(
      "/agent/api/office/habblaud/agents/3/model",
    );
  });

  it("encodes a slug-shaped id", () => {
    expect(seatModelUrl("lead quality")).toContain("lead%20quality");
  });
});

describe("describeSeatRouting", () => {
  it("says a seat inherits when unassigned", () => {
    expect(describeSeatRouting(state())).toBe("Inherits the profile default model");
  });

  it("shows provider and model when assigned", () => {
    expect(describeSeatRouting(state({
      model: "grok-4.7", provider: "cheapvibecode", inherits: false,
    }))).toBe("cheapvibecode / grok-4.7");
  });

  it("never renders an empty cell for a failed read", () => {
    expect(describeSeatRouting({ ok: false })).toContain("unavailable");
    expect(describeSeatRouting(null)).toContain("unavailable");
  });
});

describe("provider selection", () => {
  it("drops providers with no models", () => {
    const slugs = selectableProviders(state()).map((p) => p.slug);
    expect(slugs).toContain("opencode-go");
    expect(slugs).not.toContain("empty");
  });

  it("drops providers the host holds no credential for", () => {
    // A provider the operator cannot call must not be selectable: choosing it
    // would write an assignment that fails at the first turn.
    const slugs = selectableProviders(state()).map((p) => p.slug);
    expect(slugs).not.toContain("cheapvibecode");
  });

  it("offers only that provider's models", () => {
    expect(modelsFor(state(), "omniroute")).toEqual(["auto"]);
    expect(modelsFor(state(), "opencode-go")).toEqual(["deepseek-v4.1-flash"]);
    expect(modelsFor(state(), "nope")).toEqual([]);
  });

  it("is empty (not broken) when the catalogue is missing", () => {
    expect(selectableProviders({ ok: true })).toEqual([]);
    expect(selectableProviders(null)).toEqual([]);
  });
});

describe("assignmentPayload", () => {
  it("trims both fields", () => {
    expect(assignmentPayload(" omniroute ", " auto ")).toEqual({
      provider: "omniroute", model: "auto",
    });
  });

  it("sends an explicit empty model as a CLEAR, not an omission", () => {
    // The server reads a missing model as "no change"; an empty string is the
    // documented way to return a seat to inheriting.
    expect(assignmentPayload("", "")).toEqual({ provider: "", model: "" });
  });
});

describe("describeWriteFailure", () => {
  it("explains an unknown model in terms the operator can act on", () => {
    const msg = describeWriteFailure(400, { code: "UNKNOWN_MODEL" });
    expect(msg).toContain("not in the catalogue");
  });

  it("names the emergency stop when it is latched", () => {
    expect(describeWriteFailure(409, { code: "ESTOP_ENGAGED" }))
      .toContain("emergency stop");
  });

  it("prefers the server's own detail over a generic line", () => {
    expect(describeWriteFailure(500, { detail: "disk full" })).toBe("disk full");
  });

  it("still says something useful with no body", () => {
    expect(describeWriteFailure(502, null)).toContain("502");
  });
});

describe("authHeaders", () => {
  it("carries the host session token when present", () => {
    expect(authHeaders("tok")["X-Qubit-Session-Token"]).toBe("tok");
  });

  it("omits the header when there is no token", () => {
    expect(authHeaders("")["X-Qubit-Session-Token"]).toBeUndefined();
  });
});

const jsonResponse = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

describe("fetchSeatModel", () => {
  it("marks a successful read ok", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(state()));
    const out = await fetchSeatModel(3, fetchImpl as never, null);
    expect(out.ok).toBe(true);
    expect(out.slug).toBe("lead-quality");
    expect((fetchImpl.mock.calls[0] as unknown[])[1]).toMatchObject({ method: "GET" });
  });

  it("turns a 404 into an ok:false with a reason, never a throw", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ code: "AGENT_NOT_FOUND" }, 404));
    const out = await fetchSeatModel(99, fetchImpl as never, null);
    expect(out.ok).toBe(false);
    expect(out.detail).toContain("no longer exists");
  });
});

describe("saveSeatModel", () => {
  it("POSTs the trimmed assignment", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(state({
      model: "auto", provider: "omniroute", inherits: false,
    })));
    const out = await saveSeatModel(3, " omniroute ", " auto ", fetchImpl as never, null);
    expect(out.ok).toBe(true);
    const init = (fetchImpl.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ provider: "omniroute", model: "auto" });
  });

  it("reports a rejected write instead of claiming success", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ code: "UNKNOWN_MODEL" }, 400));
    const out = await saveSeatModel(3, "p", "not-a-model", fetchImpl as never, null);
    expect(out.ok).toBe(false);
    expect(out.detail).toContain("not in the catalogue");
  });
});
