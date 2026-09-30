import { describe, expect, it } from "vitest";
import { terminalRefusalCopy } from "./terminal-errors";

describe("terminalRefusalCopy", () => {
  it("explains a below-floor profile with remediation", () => {
    const copy = terminalRefusalCopy(
      "TERMINAL_CONTEXT_BELOW_MINIMUM",
      "declares 4096",
      "qubit-brain4",
    );
    expect(copy).toContain("qubit-brain4");
    expect(copy).toContain("65,536");
    expect(copy).toContain("declares 4096");
  });

  it("explains an unverifiable window without guessing", () => {
    const copy = terminalRefusalCopy(
      "TERMINAL_CONTEXT_UNVERIFIED",
      "could not be resolved (not-cached)",
      "qubit-model",
    );
    expect(copy).toContain("qubit-model");
    expect(copy).toContain("rather than guessing");
  });

  it("explains a non-executable profile with the egress remedy", () => {
    const copy = terminalRefusalCopy(
      "TERMINAL_PROFILE_NOT_EXECUTABLE",
      "egress_allowed=False",
      "qubit-model",
    );
    expect(copy).toContain("egress");
    expect(copy).toContain("allowlist");
  });

  it("passes unknown codes through with their detail", () => {
    expect(terminalRefusalCopy("HTTP_503", "boom")).toBe("[HTTP_503] boom");
  });
});
