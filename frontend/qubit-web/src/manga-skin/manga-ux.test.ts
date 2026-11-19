import { describe, expect, it } from "vitest";
import { DEFAULT_SCHEDULE_STATE } from "@/lib/schedule";
import { describeScheduleChoice } from "@/components/ScheduleBuilder";
import { MANGA_PAGE_GUIDES, mangaGuideForPath } from "./MangaPageGuide";
import { mangaRouteEligible } from "./manga-store";

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

  it("describes dashboard destinations and nested views", () => {
    expect(MANGA_PAGE_GUIDES["/cron"]).toContain("when");
    expect(mangaGuideForPath("/open-viking/resources/subview")).toBe(MANGA_PAGE_GUIDES["/open-viking/resources"]);
    expect(mangaGuideForPath("/random-plugin")).toBeUndefined();
  });

  it("never skins protected routes", () => {
    for (const path of ["/chat", "/qubit-workbench", "/qubit-hud", "/qubit-office", "/qubit-office2", "/qubit-office3"]) {
      expect(mangaRouteEligible(path)).toBe(false);
      expect(mangaGuideForPath(path)).toBeUndefined();
    }
  });
});
