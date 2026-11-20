// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { DEFAULT_SCHEDULE_STATE } from "@/lib/schedule";
import { describeScheduleChoice } from "@/components/ScheduleBuilder";

describe("manga human-first UX", () => {
  it("shows plain-language interval schedules without changing backend syntax", () => {
    expect(describeScheduleChoice(DEFAULT_SCHEDULE_STATE)).toBe("Run every 30 minutes");
    expect(describeScheduleChoice({ ...DEFAULT_SCHEDULE_STATE, intervalValue: 1, intervalUnit: "hours" }))
      .toBe("Run every 1 hour");
  });

  it("explains calendar modes to humans", () => {
    expect(describeScheduleChoice({ ...DEFAULT_SCHEDULE_STATE, mode: "daily" })).toContain("Run every day");
    expect(describeScheduleChoice({ ...DEFAULT_SCHEDULE_STATE, mode: "weekly" })).toContain("Monday");
    expect(describeScheduleChoice({ ...DEFAULT_SCHEDULE_STATE, mode: "once", onceAt: "" })).toBe("Choose a date and time");
  });
});
