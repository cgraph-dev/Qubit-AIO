import { describe, expect, it, vi } from "vitest";
import type { AgentSeat } from "./agents-panel";
import {
  fetchTalkTranscript,
  normalizeTalkMessages,
  sendTalkMessage,
  startTalkSession,
  talkSessionId,
  talkSessionName,
  talkUrl,
  visibleTalkMessages,
} from "./talk-panel";

const seat = (over: Partial<AgentSeat> = {}): AgentSeat => ({
  slug: "lead-quality",
  name: "Lead Quality",
  seat_type: "worker",
  supervisor: "chief-of-staff",
  division: "quality",
  room: "quality-desk-a",
  emoji: "🔍",
  color: "#111",
  portrait: null,
  body: null,
  ...over,
});

const win = (token = "tok") => ({
  __QUBIT_SESSION_TOKEN__: token,
  parent: {},
}) as unknown as Window;

const respond = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

describe("the seat's canonical session", () => {
  it("is deterministic, URL-safe and bounded", () => {
    expect(talkSessionId(seat())).toBe("office-talk-lead-quality");
    expect(talkSessionId(seat({ slug: " Design / UI Designer " })))
      .toBe("office-talk-design-ui-designer");
    expect(talkSessionId(seat({ slug: "" }))).toBe("office-talk-seat");
    expect(talkSessionId(seat({ slug: "x".repeat(200) })).length).toBe(80);
  });

  it("names the conversation for the canonical store", () => {
    expect(talkSessionName(seat())).toBe("Office talk · Lead Quality");
  });

  it("uses the office /agent prefix and the host base", () => {
    expect(talkUrl("/api/qubit/chat", "")).toBe("/agent/api/qubit/chat");
    expect(talkUrl("/api/qubit/chat", "/qubit/"))
      .toBe("/qubit/agent/api/qubit/chat");
  });
});

describe("normalising the transcript", () => {
  it("keeps real text rows and never synthesises one", () => {
    const rows = normalizeTalkMessages([
      { role: "user", content: "Hello", timestamp: 1 },
      { role: "assistant", content: "Hi" },
      { role: "", content: "no role" },
      { role: "assistant", content: "   " },
      null,
      "not a row",
    ]);
    expect(rows).toEqual([
      { role: "user", content: "Hello", timestamp: 1 },
      { role: "assistant", content: "Hi" },
    ]);
  });

  it("renders only the user/assistant exchange", () => {
    expect(visibleTalkMessages([
      { role: "user", content: "a" },
      { role: "tool", content: "b" },
      { role: "assistant", content: "c" },
      { role: "system", content: "d" },
    ])).toEqual([
      { role: "user", content: "a" },
      { role: "assistant", content: "c" },
    ]);
  });
});

describe("reading one conversation", () => {
  it("returns a typed empty state for the store's 404", async () => {
    const f = vi.fn(async () => respond(404, { ok: false }));
    const state = await fetchTalkTranscript(seat(), {
      base: "", fetchImpl: f as never, win: win(),
    });
    expect(state.kind).toBe("empty");
    if (state.kind === "empty") {
      expect(state.code).toBe("NO_SESSION");
      expect(state.sessionId).toBe("office-talk-lead-quality");
    }
    expect(f).toHaveBeenCalledTimes(1);
    expect(String(f.mock.calls[0][0])).toContain(
      "/agent/api/qubit/chat/sessions/office-talk-lead-quality");
    expect((f.mock.calls[0][1] as { headers: Record<string, string> })
      .headers["X-Qubit-Session-Token"]).toBe("tok");
  });

  it("returns a typed unavailable state when the token is refused", async () => {
    const state = await fetchTalkTranscript(seat(), {
      base: "", fetchImpl: (async () => respond(401, {})) as never, win: win(),
    });
    expect(state.kind).toBe("unavailable");
    if (state.kind === "unavailable") expect(state.code).toBe("TOKEN_REFUSED");
  });

  it("does not call the store without a session token", async () => {
    const f = vi.fn(async () => respond(200, {}));
    const state = await fetchTalkTranscript(seat(), {
      base: "", fetchImpl: f as never, win: win(""),
    });
    expect(state.kind).toBe("unavailable");
    if (state.kind === "unavailable") expect(state.code).toBe("NO_SESSION_TOKEN");
    expect(f).not.toHaveBeenCalled();
  });

  it("returns the canonical rows unchanged, with no fabricated extras", async () => {
    const messages = [
      { role: "user", content: "Audit the last deploy." },
      { role: "assistant", content: "The deploy is green." },
    ];
    const state = await fetchTalkTranscript(seat(), {
      base: "",
      fetchImpl: (async () => respond(200, {
        session: { id: "office-talk-lead-quality", name: "Office talk · Lead Quality" },
        messages,
      })) as never,
      win: win(),
    });
    expect(state.kind).toBe("ready");
    if (state.kind === "ready") {
      expect(state.messages).toEqual(messages);
      expect(state.messages).toHaveLength(2);
    }
  });

  it("reports a transport failure as typed, not as an empty conversation", async () => {
    const state = await fetchTalkTranscript(seat(), {
      base: "",
      fetchImpl: (async () => { throw new Error("socket closed"); }) as never,
      win: win(),
    });
    expect(state.kind).toBe("unavailable");
    if (state.kind === "unavailable") {
      expect(state.code).toBe("NETWORK");
      expect(state.detail).toContain("socket closed");
    }
  });
});

describe("starting a conversation", () => {
  it("creates the canonical session and then reads it", async () => {
    const calls: Array<{ url: string; method?: string; body?: string }> = [];
    const f = async (url: string, init?: { method?: string; body?: string }) => {
      calls.push({ url: String(url), method: init?.method, body: init?.body });
      if (init?.method === "POST") {
        return respond(200, { ok: true, session: { id: "office-talk-lead-quality" } });
      }
      return respond(200, {
        session: { id: "office-talk-lead-quality", name: "Office talk · Lead Quality" },
        messages: [],
      });
    };
    const state = await startTalkSession(seat(), {
      base: "", fetchImpl: f as never, win: win(),
    });
    expect(state.kind).toBe("ready");
    expect(calls[0].method).toBe("POST");
    expect(JSON.parse(String(calls[0].body))).toEqual({
      id: "office-talk-lead-quality", name: "Office talk · Lead Quality",
    });
    expect(calls[1].url).toContain("/sessions/office-talk-lead-quality");
  });

  it("treats an existing session as a read, not a failure", async () => {
    const calls: string[] = [];
    const f = async (url: string, init?: { method?: string }) => {
      calls.push(String(url));
      if (init?.method === "POST") return respond(409, { code: "SESSION_EXISTS" });
      return respond(200, { session: { id: "office-talk-lead-quality" }, messages: [] });
    };
    const state = await startTalkSession(seat(), {
      base: "", fetchImpl: f as never, win: win(),
    });
    expect(state.kind).toBe("ready");
    expect(calls).toHaveLength(2);
  });
});

describe("sending through the existing chat action", () => {
  it("posts the canonical turn and returns the real reply", async () => {
    const f = vi.fn(async () => respond(200, {
      ok: true, reply: "Done — the audit is clean.", session: "office-talk-lead-quality",
    }));
    const result = await sendTalkMessage(seat(), "  Audit the deploy.  ", {
      base: "", fetchImpl: f as never, win: win(),
    });
    expect(result).toEqual({ ok: true, reply: "Done — the audit is clean." });
    const [url, init] = f.mock.calls[0] as unknown as [
      string, { method: string; headers: Record<string, string>; body: string },
    ];
    expect(url).toBe("/agent/api/qubit/chat");
    expect(init.method).toBe("POST");
    expect(init.headers["X-Qubit-Session-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({
      text: "Audit the deploy.", session: "office-talk-lead-quality",
    });
  });

  it("returns a typed refusal and never invents a reply", async () => {
    const result = await sendTalkMessage(seat(), "Do it", {
      base: "",
      fetchImpl: (async () => respond(502, {
        ok: false, code: "MODEL_NOT_ALLOWED",
        detail: "the active profile refuses this model",
      })) as never,
      win: win(),
    });
    expect(result).toEqual({
      ok: false, code: "MODEL_NOT_ALLOWED",
      detail: "the active profile refuses this model",
    });
  });

  it("refuses an empty message before any request", async () => {
    const f = vi.fn();
    const result = await sendTalkMessage(seat(), "   ", {
      base: "", fetchImpl: f as never, win: win(),
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("EMPTY_MESSAGE");
    expect(f).not.toHaveBeenCalled();
  });

  it("refuses without a session token", async () => {
    const f = vi.fn();
    const result = await sendTalkMessage(seat(), "hello", {
      base: "", fetchImpl: f as never, win: win(""),
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("NO_SESSION_TOKEN");
    expect(f).not.toHaveBeenCalled();
  });
});
