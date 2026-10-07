import { describe, expect, it } from "vitest";
import { chatLiveFrame } from "./chatLiveEvents";

// The EXACT frames captured from the real Python producer (EventHub.wire_frame
// output, JSON.parse'd) — proves the two halves agree on the wire shape.
const WIRE = [
  '{"type":"CUSTOM","timestamp":1791348047477,"name":"session.created","value":{"session_id":"probe-live-1","session":{"id":"probe-live-1","name":"live-probe-create","model":null,"source":"dashboard"}}}',
  '{"type":"CUSTOM","timestamp":1791348047482,"name":"session.updated","value":{"session_id":"probe-live-1","session":{"id":"probe-live-1","name":"probe-renamed","model":null}}}',
  '{"type":"CUSTOM","timestamp":1791348047485,"name":"session.updated","value":{"session_id":"probe-live-1","session":{"id":"probe-live-1","name":"probe-renamed","model":"deepseek-v4.1-flash"}}}',
  '{"type":"CUSTOM","timestamp":1791348047488,"name":"session.deleted","value":{"session_id":"probe-live-1","session":{"id":"probe-live-1"}}}',
];

describe("producer/consumer wire agreement (real captured frames)", () => {
  it("session.created is recognised as a session frame", () => {
    const f = chatLiveFrame(JSON.parse(WIRE[0]));
    expect(f?.name).toBe("session.created");
    expect(f?.isSession).toBe(true);
    expect(f?.sessionId).toBe("probe-live-1");
    expect(f?.session?.name).toBe("live-probe-create");
  });
  it("session.updated carries the per-session model", () => {
    const f = chatLiveFrame(JSON.parse(WIRE[2]));
    expect(f?.name).toBe("session.updated");
    expect(f?.isSession).toBe(true);
    expect(f?.session?.model).toBe("deepseek-v4.1-flash");
  });
  it("session.deleted is recognised", () => {
    const f = chatLiveFrame(JSON.parse(WIRE[3]));
    expect(f?.name).toBe("session.deleted");
    expect(f?.isSession).toBe(true);
    expect(f?.sessionId).toBe("probe-live-1");
  });
  it("a unrelated frame is ignored (no false trigger)", () => {
    const f = chatLiveFrame({ type: "RUN_STARTED", run_id: "x" });
    expect(f).toBeNull();
  });
});
