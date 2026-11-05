import { describe, expect, it } from "vitest";
import { sessionResumeHref } from "./session-resume-href";

describe("sessionResumeHref", () => {
  it("builds the plain resume route when the row has no profile stamp", () => {
    expect(sessionResumeHref("sid-1")).toBe("/chat?resume=sid-1");
    expect(sessionResumeHref("sid-1", null)).toBe("/chat?resume=sid-1");
    expect(sessionResumeHref("sid-1", "  ")).toBe("/chat?resume=sid-1");
  });

  it("scopes the PTY to the row's owning profile", () => {
    expect(sessionResumeHref("sid-1", "guanli")).toBe(
      "/chat?resume=sid-1&profile=guanli",
    );
  });

  it("encodes ids and profile names", () => {
    const href = sessionResumeHref("a/b c", "p&q");
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("resume")).toBe("a/b c");
    expect(params.get("profile")).toBe("p&q");
  });
});
