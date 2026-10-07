/**
 * chatLiveFrame — recognition of chat-session live-update frames.
 *
 * Guards the client half of the live-update contract: a session create /
 * model pick / rename / delete and a global model assignment arrive as AG-UI
 * CUSTOM frames, and the reader must recognise them without being confused by
 * the literal `"CUSTOM"` type. A regression here is invisible in unit-blind
 * code and shows up only as "I have to refresh the page".
 */
import { describe, expect, it } from "vitest";

import { chatLiveFrame } from "./chatLiveEvents";

describe("chatLiveFrame", () => {
  it("recognises session.created via the CUSTOM envelope", () => {
    const f = chatLiveFrame({
      type: "CUSTOM",
      name: "session.created",
      value: { session_id: "dashboard-abc", session: { id: "dashboard-abc", model: "qubit-brain" } },
    });
    expect(f).not.toBeNull();
    expect(f!.name).toBe("session.created");
    expect(f!.isSession).toBe(true);
    expect(f!.isModelChange).toBe(false);
    expect(f!.sessionId).toBe("dashboard-abc");
    expect(f!.session?.model).toBe("qubit-brain");
  });

  it("recognises model.changed", () => {
    const f = chatLiveFrame({
      type: "CUSTOM",
      name: "model.changed",
      value: { provider: "ollama", model: "qubit-brain", scope: "main" },
    });
    expect(f!.isModelChange).toBe(true);
    expect(f!.isSession).toBe(false);
  });

  it("derives session_id from the nested record when the flat id is absent", () => {
    const f = chatLiveFrame({
      type: "CUSTOM",
      name: "session.updated",
      value: { session: { id: "s-1", model: "x" } },
    });
    expect(f!.sessionId).toBe("s-1");
  });

  it("ignores unrelated CUSTOM frames (todo/ subagent.)", () => {
    expect(
      chatLiveFrame({ type: "CUSTOM", name: "todo.updated", value: {} }),
    ).toBeNull();
    expect(
      chatLiveFrame({ type: "CUSTOM", name: "subagent.progress", value: {} }),
    ).toBeNull();
  });

  it("ignores non-CUSTOM frames", () => {
    expect(chatLiveFrame({ type: "RUN_STARTED", runId: "r2" })).toBeNull();
    expect(chatLiveFrame({ type: "TEXT_MESSAGE_CONTENT", delta: "hi" })).toBeNull();
  });
});
