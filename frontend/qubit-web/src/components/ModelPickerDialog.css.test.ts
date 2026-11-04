// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  fileURLToPath(new URL("./ModelPickerDialog.css", import.meta.url)),
  "utf8",
);

describe("ModelPickerDialog mobile cascade", () => {
  it("stacks the body after every desktop body rule", () => {
    const lastMobile = css.lastIndexOf("@media (max-width: 680px)");
    expect(lastMobile).toBeGreaterThan(-1);
    const tail = css.slice(lastMobile);
    expect(tail).toMatch(
      /\.qmodel-picker__body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    );
    expect(tail).toMatch(/\.qmodel-picker__current-name\s*\{[^}]*min-width:\s*0/);
  });
});
