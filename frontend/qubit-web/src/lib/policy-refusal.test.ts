import { describe, expect, it } from "vitest";
import {
  isUpdateOperatorOwnedRefusal,
  parsePolicyRefusal,
  policyRefusalMessage,
} from "./policy-refusal";

const UPDATE_REFUSAL = {
  ok: false,
  state: "UNAVAILABLE",
  code: "UPDATE_OPERATOR_OWNED",
  detail:
    "In-place self-update from the dashboard is refused; upgrades are " +
    "versioned, staged, and reversible.",
  qubit_counterpart: "CLI `qubit product_*` / package + dashboard Setup page",
  owner: "qubit-policy",
  rollback: "read-only; nothing to roll back",
};

// fetchJSON throws `Error("<status>: <body>")` for every non-2xx.
function thrown(body: unknown, status = 403): Error {
  return new Error(`${status}: ${JSON.stringify(body)}`);
}

describe("parsePolicyRefusal", () => {
  it("recovers a typed refusal from a fetchJSON 403 error", () => {
    const refusal = parsePolicyRefusal(thrown(UPDATE_REFUSAL));
    expect(refusal).not.toBeNull();
    expect(refusal?.code).toBe("UPDATE_OPERATOR_OWNED");
    expect(refusal?.state).toBe("UNAVAILABLE");
    expect(refusal?.counterpart).toContain("qubit product_");
    expect(refusal?.owner).toBe("qubit-policy");
  });

  it("parses a plain-string error too", () => {
    expect(
      parsePolicyRefusal(`403: ${JSON.stringify(UPDATE_REFUSAL)}`),
    ).not.toBeNull();
  });

  it("returns null for non-JSON and unrelated errors", () => {
    expect(parsePolicyRefusal(new Error("NetworkError: failed to fetch"))).toBe(
      null,
    );
    expect(parsePolicyRefusal(new Error("403: Forbidden"))).toBeNull();
    expect(parsePolicyRefusal(undefined)).toBeNull();
  });
});

describe("isUpdateOperatorOwnedRefusal", () => {
  it("matches the expected update policy refusal", () => {
    expect(isUpdateOperatorOwnedRefusal(thrown(UPDATE_REFUSAL))).toBe(true);
  });

  it("does not match a different typed refusal", () => {
    expect(
      isUpdateOperatorOwnedRefusal(
        thrown({ ...UPDATE_REFUSAL, code: "SECRET_NEVER_SERVED" }),
      ),
    ).toBe(false);
  });
});

describe("policyRefusalMessage", () => {
  it("joins the detail with the Qubit counterpart", () => {
    const refusal = parsePolicyRefusal(thrown(UPDATE_REFUSAL));
    const message = policyRefusalMessage(refusal!);
    expect(message).toContain("refused");
    expect(message).toContain("qubit product_");
  });
});
