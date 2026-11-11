// @vitest-environment jsdom
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

describe("office brand patch", () => {
  it("replaces the vendored topbar identity without removing legal credit", async () => {
    document.body.innerHTML = `
      <div class="ui-splash"><span class="ui-splash__name">Habblaud</span><p>Connecting to the office…</p></div>
      <div class="ui-brand">
        <span class="ui-brand__mark"><img id="upstream-logo"></span>
        <div class="ui-brand__text">
          <span class="ui-brand__name"><svg class="ui-wordmark"></svg><span class="ui-sr">Habblaud</span></span>
          <div class="ui-topbar__status">Connected</div>
        </div>
      </div>`;
    const { installOfficeBrand } = await import("./brand-patch");
    installOfficeBrand(window);
    expect(document.querySelector(".ui-splash")).toBeNull();
    expect(document.querySelector("[data-qubit-vendor-splash]")).not.toBeNull();
    expect(document.querySelector(".ui-brand__mark")).toBeNull();
    expect(document.querySelector(".ui-wordmark")).toBeNull();
    expect(document.querySelector(".ui-brand__name")?.textContent).toBe("Qubit Office");
    expect(document.querySelector(".ui-topbar__status")?.textContent).toBe("Connected");
    expect(document.title).toBe("Qubit Office");
  });
});
