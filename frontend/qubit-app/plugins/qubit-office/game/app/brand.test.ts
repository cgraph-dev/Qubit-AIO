import { describe, expect, it } from "vitest";
import { OFFICE_BRAND } from "./brand";

describe("Qubit Office brand", () => {
  it("names the product Qubit Office", () => {
    expect(OFFICE_BRAND.name).toBe("Qubit Office");
    expect(OFFICE_BRAND.title).toBe("Qubit Office");
    expect(OFFICE_BRAND.shortName).toBe("Office");
    expect(OFFICE_BRAND.description.length).toBeGreaterThan(0);
    expect(OFFICE_BRAND.logoPath.startsWith("/")).toBe(true);
  });

  it("keeps the upstream name out of user-visible copy", () => {
    for (const value of [
      OFFICE_BRAND.name,
      OFFICE_BRAND.shortName,
      OFFICE_BRAND.title,
      OFFICE_BRAND.description,
      OFFICE_BRAND.credit,
    ]) {
      expect(value.toLowerCase()).not.toContain("habblaud");
    }
  });

  it("keeps the MIT legal label and the upstream pin", () => {
    expect(OFFICE_BRAND.legalLabel).toContain("Habblaud");
    expect(OFFICE_BRAND.legalLabel).toContain("MIT");
    expect(OFFICE_BRAND.upstream).toContain("github.com/marmottajr/habblaud");
    expect(OFFICE_BRAND.upstreamCommit).toHaveLength(40);
  });

  it("keeps the LimeZu art credit", () => {
    expect(OFFICE_BRAND.credit).toContain("LimeZu");
    expect(OFFICE_BRAND.creditUrl).toBe("https://limezu.itch.io");
  });
});
