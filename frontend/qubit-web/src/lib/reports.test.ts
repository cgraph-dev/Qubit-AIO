/** collectReports — the recent-reports derivation. Pure, no DOM. */
import { describe, expect, it } from "vitest";

import {
  collectReports,
  isReportMessage,
  reportBody,
  type ReportSourceMessage,
} from "./reports";

describe("isReportMessage", () => {
  it("is true for a report flag or a task key", () => {
    expect(isReportMessage({ report: true })).toBe(true);
    expect(isReportMessage({ taskKey: "task-9" })).toBe(true);
  });
  it("is false for a plain message", () => {
    expect(isReportMessage({ text: "hi" })).toBe(false);
  });
});

describe("reportBody", () => {
  it("prefers the full report text over the summary", () => {
    expect(reportBody({ text: "summary", reportText: "full body" })).toBe("full body");
  });
  it("falls back to text when there is no full body", () => {
    expect(reportBody({ text: "summary" })).toBe("summary");
  });
});

describe("collectReports", () => {
  const messages: ReportSourceMessage[] = [
    { role: "user", text: "build me a thing" },
    { role: "assistant", text: "first summary", report: true, reportText: "# Build one\n\nbody one", taskKey: "t1" },
    { role: "assistant", text: "plain reply" },
    { role: "assistant", text: "second summary", report: true, reportText: "Build two done", taskKey: "t2" },
  ];

  it("indexes only report messages, newest first", () => {
    const reports = collectReports(messages);
    expect(reports.map((r) => r.index)).toEqual([3, 1]);
    expect(reports.map((r) => r.taskKey)).toEqual(["t2", "t1"]);
  });

  it("uses the first non-empty line as the title", () => {
    const reports = collectReports(messages);
    expect(reports[1].title).toBe("Build one"); // markdown heading fence stripped
    expect(reports[0].title).toBe("Build two done");
  });

  it("keeps the full body for reading", () => {
    const reports = collectReports(messages);
    expect(reports[1].body).toContain("body one");
  });

  it("returns [] for a transcript with no reports", () => {
    expect(collectReports([{ text: "hi" }])).toEqual([]);
  });

  it("bounded title for a very long first line", () => {
    const long = "x".repeat(400);
    const [r] = collectReports([{ report: true, text: long }]);
    expect(r.title.length).toBeLessThanOrEqual(90);
    expect(r.title.endsWith("…")).toBe(true);
  });

  it("a task-key-only message still lists (kind summary)", () => {
    const [r] = collectReports([{ taskKey: "t9", text: "done" }]);
    expect(r.kind).toBe("summary");
    expect(r.taskKey).toBe("t9");
  });
});
