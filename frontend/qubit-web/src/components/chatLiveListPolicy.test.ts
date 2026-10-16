/**
 * chatLiveListPolicy — which live frames may remount the session switcher.
 *
 * A remount (nonce bump) discards the switcher's search text, scroll position
 * and paging offset, and re-runs its fetch. It is only justified when the
 * frame can change membership or order. Pinning that decision here stops a
 * per-session MODEL pick on a background session from churning the list.
 */
import { describe, expect, it } from "vitest";

import { shouldRemountSessionList } from "./chatLiveListPolicy";

describe("shouldRemountSessionList", () => {
  it("remounts on session.created (membership changed)", () => {
    expect(
      shouldRemountSessionList(
        { name: "session.created", isSession: true, sessionId: "s1" },
        "s1",
      ),
    ).toBe(true);
  });

  it("remounts on session.deleted (membership changed)", () => {
    expect(
      shouldRemountSessionList(
        { name: "session.deleted", isSession: true, sessionId: "s1" },
        "s1",
      ),
    ).toBe(true);
  });

  it("remounts on session.updated for OUR active session (order may change)", () => {
    expect(
      shouldRemountSessionList(
        { name: "session.updated", isSession: true, sessionId: "s1" },
        "s1",
      ),
    ).toBe(true);
  });

  it("does NOT remount on session.updated for a DIFFERENT session (model pick)", () => {
    // The reported chain: switching model provider on a background session
    // must not churn the open list.
    expect(
      shouldRemountSessionList(
        { name: "session.updated", isSession: true, sessionId: "other" },
        "s1",
      ),
    ).toBe(false);
  });

  it("does NOT remount when no session is selected yet", () => {
    expect(
      shouldRemountSessionList(
        { name: "session.updated", isSession: true, sessionId: "other" },
        null,
      ),
    ).toBe(false);
  });

  it("never remounts on model.changed (a global config write)", () => {
    expect(
      shouldRemountSessionList(
        { name: "model.changed", isSession: false, isModelChange: true },
        "s1",
      ),
    ).toBe(false);
  });

  it("does not remount for null / non-session frames", () => {
    expect(shouldRemountSessionList(null, "s1")).toBe(false);
    expect(
      shouldRemountSessionList({ name: "other.event" }, "s1"),
    ).toBe(false);
  });
});
