import { describe, expect, it, vi } from "vitest";
import {
  agentsPanelUrl,
  fetchAgentPanel,
  groupingProblem,
  readToken,
  seatTypeLabel,
  summaryLine,
  type AgentPanelData,
} from "./agents-panel";

/** A minimal payload shaped exactly like the real route's answer. */
function payload(over: Partial<AgentPanelData> = {}): AgentPanelData {
  const seats = [
    { slug: "a-one", name: "One", seat_type: "director_portfolio", supervisor: "ceo",
      division: "alpha", room: "boardroom", emoji: "🔍", color: "#111" },
    { slug: "a-two", name: "Two", seat_type: "worker", supervisor: "a-one",
      division: "alpha", room: "alpha-desk-a", emoji: "🛠", color: "#222" },
    { slug: "x-ceo", name: "Chief of Staff", seat_type: "director_functional",
      supervisor: "ceo", division: "", room: "boardroom", emoji: "🎛", color: "cyan" },
  ];
  return {
    ok: true,
    schema: "qubit-office-agents-panel/1",
    counts: {
      headcount: seats.length, divisions: 1, roster_divisions: 1,
      boardroom_seats: 1, rooms: 2, floors: 1,
      seat_types: { director_portfolio: 1, worker: 1, director_functional: 1 },
      per_division: { alpha: 2, "": 1 },
    },
    divisions: [
      { slug: "alpha", label: "Alpha", kind: "product", floor: 1,
        headcount: 2, seats: seats.slice(0, 2) },
      { slug: "", label: "Boardroom (executive tier)", kind: "executive",
        floor: null, headcount: 1, seats: seats.slice(2) },
    ],
    seats,
    ...over,
  };
}

const respond = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

describe("the panel's route", () => {
  it("lives under /agent — a bare /api call from the office document is a 404", () => {
    expect(agentsPanelUrl("")).toBe("/agent/api/office/habblaud/agents/panel");
  });

  it("rebases onto a subpath deployment", () => {
    expect(agentsPanelUrl("/qubit/")).toBe(
      "/qubit/agent/api/office/habblaud/agents/panel");
  });
});

describe("the session token", () => {
  it("reads the document's own injected token", () => {
    const win = { __QUBIT_SESSION_TOKEN__: "tok", parent: {} } as unknown as Window;
    expect(readToken(win)).toBe("tok");
  });

  it("falls back to the PARENT document (the embedded app's realm)", () => {
    const win = { parent: { __QUBIT_SESSION_TOKEN__: "parent-tok" } } as unknown as Window;
    expect(readToken(win)).toBe("parent-tok");
  });

  it("is empty when neither document carries one", () => {
    expect(readToken({ parent: {} } as unknown as Window)).toBe("");
  });
});

describe("counts and labels", () => {
  it("summarises the real numbers and nothing more", () => {
    const line = summaryLine(payload().counts);
    expect(line).toBe("3 seats · 1 divisions · 2 rooms · 1 in the boardroom");
  });

  it("names every seat type the plan builds", () => {
    expect(seatTypeLabel("director_functional")).toBe("Functional director");
    expect(seatTypeLabel("director_portfolio")).toBe("Portfolio director");
    expect(seatTypeLabel("worker")).toBe("Worker");
    expect(seatTypeLabel("mystery")).toBe("mystery");
  });
});

describe("a grouping that does not add up is named, never rendered short", () => {
  it("accepts a consistent payload", () => {
    expect(groupingProblem(payload())).toBe("");
  });

  it("refuses a bucket count that disagrees with the seat list", () => {
    const bad = payload();
    bad.divisions = [bad.divisions![0]];
    expect(groupingProblem(bad)).toContain("groups 2 seats but the route served 3");
  });

  it("refuses a headcount that disagrees with the seat list", () => {
    const bad = payload();
    bad.counts = { ...bad.counts!, headcount: 9 };
    expect(groupingProblem(bad)).toContain("headcount (9)");
  });
});

describe("fetching the panel", () => {
  it("sends the token header and parses the real payload", async () => {
    const fetchImpl = vi.fn(async () => respond(200, payload()));
    const state = await fetchAgentPanel({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      win: { __QUBIT_SESSION_TOKEN__: "tok", parent: {} } as unknown as Window,
      base: "",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/agent/api/office/habblaud/agents/panel");
    expect((init.headers as Record<string, string>)["X-Qubit-Session-Token"]).toBe("tok");
    expect(state.kind).toBe("ready");
    if (state.kind === "ready") {
      expect(state.data.counts?.headcount).toBe(3);
    }
  });

  it("names a 401 as a token problem instead of an empty roster", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(401, { detail: "unauthorized" })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("HTTP 401");
  });

  it("names a 404 as a missing route, not as 'no agents'", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(404, { detail: "no route" })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("HTTP 404");
  });

  it("carries the server's own refusal detail", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(503, {
        ok: false, code: "AGENCY_ORG_UNAVAILABLE",
        detail: "the building plan refused this roster: span",
      })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") {
      expect(state.message).toContain("the building plan refused this roster");
    }
  });

  it("refuses to call an inconsistent payload ready", async () => {
    const bad = payload();
    bad.seats = bad.seats!.slice(0, 1);
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(200, bad)) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("inconsistent");
  });
});
