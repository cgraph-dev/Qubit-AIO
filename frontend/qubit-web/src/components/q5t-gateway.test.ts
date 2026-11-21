// @vitest-environment node
/**
 * Contract for the Manga gateway metrics plate. The generated SVG owns the
 * frame and its two separator rules; the three live rows must fill the plate
 * inset so each band centres on one row. A percentage `height` alone is not
 * enough: the semantic link between the absolute content box and the `dl`
 * needs a definite height or the tracks collapse onto the top edge.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const css = read("./q5t-gateway.css");
const app = read("../App.tsx");
const art = read("../../public/manga-ui/gateway-panel.svg");

const rule = (selector: string) => {
  const index = css.indexOf(selector);
  expect(index).toBeGreaterThanOrEqual(0);
  return css.slice(index, css.indexOf("}", index));
};

describe("Manga gateway metrics plate", () => {
  it("keeps every rule scoped to the Manga attribute so classic is untouched", () => {
    expect(css).not.toMatch(/(^|\n)\s*\.q5t-gateway/);
    expect(css.match(/html\[data-qubit-manga="on"\]/g)?.length ?? 0).toBeGreaterThan(0);
  });

  it("places the rows inside the artwork's three separator bands", () => {
    expect(art).toContain('viewBox="0 0 600 206"');
    expect(art).toContain('M26 84H574');
    expect(art).toContain('M26 135H574');
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content {")).toContain(
      "inset: 7.3% 6.8%",
    );
    // 39 + 29 + 32 = 100% of the inset keeps the track edges on the rules.
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content dl {")).toContain(
      "grid-template-rows: 39% 29% 32%",
    );
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content dl {")).toContain(
      "block-size: 100%",
    );
  });

  it("gives the semantic link a definite height between the plate and the rows", () => {
    const content = rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content {");
    expect(content).toContain("display: grid");
    expect(content).toContain("grid-template: minmax(0, 1fr) / minmax(0, 1fr)");
    const link = rule("html[data-qubit-manga=\"on\"] .q5t-gateway__link {");
    expect(link).toContain("display: block");
    expect(link).toContain("block-size: 100%");
    expect(link).toContain("inline-size: 100%");
  });

  it("keeps labels left, values right and the Running state green", () => {
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content dl > div {")).toContain(
      "justify-content: space-between",
    );
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content dd {")).toContain(
      "text-align: end",
    );
    const up = rule('html[data-qubit-manga="on"] .q5t-gateway__content dd[data-state="up"] {');
    expect(up).toContain("#32fa70");
    expect(rule("html[data-qubit-manga=\"on\"] .q5t-gateway__content dd[data-state]::before {")).toContain(
      "border-radius: 50%",
    );
  });

  it("keeps the three real rows and the /sessions click-through", () => {
    expect(app).toContain("className=\"q5t-gateway__link block text-left\"");
    expect(app).toContain('to="/sessions"');
    expect(app).toContain("{stripLabel(t.app.gatewayStatusLabel)}");
    expect(app).toContain("{stripLabel(t.app.activeSessionsLabel)}");
    expect(app).toContain("formatLatency(latencyMs)");
    expect((app.match(/<dt>/g) ?? []).length).toBeGreaterThanOrEqual(3);
  });
});
