// @vitest-environment jsdom
/**
 * ocFamilyTalkApi — ROUTES manifest + no-wake-writer guard.
 *
 * Every URL this module requests must live under `/api/qubit/<family>/<action>`
 * (the ROUTES manifest is the exact list), every mutation must be a POST
 * settled through the shared confirmation round-trip (never a PUT), and
 * nothing here may touch voice-edit paths or write wake state.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchJSON = vi.hoisted(() => vi.fn());
const runMutationWithConfirm = vi.hoisted(() =>
  vi.fn((call: () => Promise<unknown>) => call()),
);

vi.mock("@/lib/api", () => ({
  fetchJSON,
  runMutationWithConfirm,
}));

import {
  DISABLED_SCHEDULER_ACTIONS,
  DISABLED_TALK_ACTIONS,
  ROUTES,
  appendTalkTranscript,
  callTalkTool,
  cancelTask,
  disableTtsProfile,
  enableTtsProfile,
  getSuspendPrepare,
  getSuspendResume,
  getTalkCatalog,
  getTalkConfig,
  getTalkMode,
  getVoicewake,
  retryTask,
  setTtsPersona,
  setTtsProvider,
  speakTalk,
  steerTalkClient,
  updateCronEnabled,
} from "@/lib/ocFamilyTalkApi";

// Voice-edit settings paths (writable). The one legitimate wake touch is the
// GET-only `/api/qubit/talk/voicewake` live read, asserted separately below.
const VOICE_EDIT_RE = /voice-edit|voice_edit|\/api\/voice\//i;
const VOICEWAKE_READ = "/api/qubit/talk/voicewake";

function calledUrls(): Array<{ url: string; init?: RequestInit }> {
  return fetchJSON.mock.calls.map((c) => ({
    url: String(c[0]),
    init: c[1] as RequestInit | undefined,
  }));
}

beforeEach(() => {
  fetchJSON.mockReset();
  runMutationWithConfirm.mockClear();
  fetchJSON.mockResolvedValue({ state: "OK" });
});

describe("ROUTES manifest", () => {
  it("lists every path under /api/qubit/<family>/<action> with a method", () => {
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const r of ROUTES) {
      expect(r.path.startsWith(`/api/qubit/${r.family}/`)).toBe(true);
      expect(["GET", "POST"]).toContain(r.method);
      expect(r.action.length).toBeGreaterThan(0);
    }
  });

  it("has no duplicate paths", () => {
    const paths = ROUTES.map((r) => `${r.method} ${r.path}`);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("never lists a voice-edit or wake-write path", () => {
    for (const r of ROUTES) {
      expect(r.path).not.toMatch(VOICE_EDIT_RE);
    }
  });
});

describe("reads stay reads, writes stay POST-through-confirm", () => {
  it("talk + wake reads issue no mutation", async () => {
    await getTalkMode("realtime");
    await getTalkCatalog();
    await getTalkConfig();
    await getVoicewake();
    await getSuspendPrepare();
    await getSuspendResume();
    expect(runMutationWithConfirm).not.toHaveBeenCalled();
    for (const { url, init } of calledUrls()) {
      expect(url.startsWith("/api/qubit/")).toBe(true);
      expect(url).not.toMatch(VOICE_EDIT_RE);
      expect(init?.method ?? "GET").toBe("GET");
    }
  });

  it("talk writes go POST through the shared confirm round-trip", async () => {
    await steerTalkClient({ sessionKey: "s", text: "hold" }, () => true);
    await appendTalkTranscript({ sessionKey: "s", text: "hi" }, () => true);
    await callTalkTool({ toolName: "local.echo", args: {} }, () => true);
    await speakTalk("hello", () => true);
    expect(runMutationWithConfirm).toHaveBeenCalledTimes(4);
    for (const { url, init } of calledUrls()) {
      expect(url.startsWith("/api/qubit/talk/")).toBe(true);
      expect(url).not.toMatch(VOICE_EDIT_RE);
      expect(init?.method).toBe("POST");
    }
  });

  it("scheduler writes go POST through the shared confirm round-trip", async () => {
    await updateCronEnabled(7, false, () => true);
    await setTtsProvider("piper", () => true);
    await enableTtsProfile("p1", () => true);
    await disableTtsProfile("p1", () => true);
    await setTtsPersona({ persona: "p1", session: "s" }, () => true);
    await cancelTask({ receiptId: "r1" }, () => true);
    await retryTask({ session: "s" }, () => true);
    expect(runMutationWithConfirm).toHaveBeenCalledTimes(7);
    for (const { url, init } of calledUrls()) {
      expect(url.startsWith("/api/qubit/scheduler/")).toBe(true);
      expect(url).not.toMatch(VOICE_EDIT_RE);
      expect(init?.method).toBe("POST");
    }
  });

  it("cron.update sends exactly the enabled-only patch", async () => {
    await updateCronEnabled(7, false, () => true);
    const body = JSON.parse(String(fetchJSON.mock.calls[0][1]?.body ?? "{}"));
    expect(body.patch).toEqual({ enabled: false });
  });

  it("the only wake touch is the GET-only voicewake live read", async () => {
    await getVoicewake();
    const { url, init } = calledUrls()[0];
    expect(url).toBe(VOICEWAKE_READ);
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("never issues a PUT to any voice-edit path", () => {
    for (const { url, init } of calledUrls()) {
      expect(`${init?.method ?? "GET"} ${url}`).not.toMatch(/PUT/i);
      expect(url).not.toMatch(VOICE_EDIT_RE);
    }
  });
});

describe("disabled tables carry code + prerequisite, never a call", () => {
  it("talk disabled rows name the realtime prerequisite", () => {
    expect(DISABLED_TALK_ACTIONS.length).toBeGreaterThanOrEqual(11);
    for (const a of DISABLED_TALK_ACTIONS) {
      expect(a.code.length).toBeGreaterThan(0);
      expect(a.reason.length).toBeGreaterThan(0);
      expect(a.counterpart.length).toBeGreaterThan(0);
    }
    const sessionRows = DISABLED_TALK_ACTIONS.filter((a) =>
      a.action.startsWith("talk.session."),
    );
    expect(sessionRows.length).toBe(7);
    expect(
      sessionRows.every((a) => a.code === "TALK_REALTIME_OPERATOR_REQUIRED"),
    ).toBe(true);
  });

  it("scheduler disabled rows cover scratch/handoff/portal/suggestions/dismiss/mentions", () => {
    const actions = DISABLED_SCHEDULER_ACTIONS.map((a) => a.action);
    for (const prefix of [
      "cron.scratch.",
      "gateway.suspend.handoff",
      "portal.",
      "taskSuggestions.",
      "tasks.dismiss",
      "mentions.",
    ]) {
      expect(actions.some((a) => a.startsWith(prefix))).toBe(true);
    }
  });
});
