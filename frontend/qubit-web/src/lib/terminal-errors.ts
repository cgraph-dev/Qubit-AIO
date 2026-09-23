/**
 * Actionable copy for `/chat` terminal ticket refusals.
 *
 * The server returns typed codes (`TERMINAL_CONTEXT_BELOW_MINIMUM`,
 * `TERMINAL_CONTEXT_UNVERIFIED`, `TERMINAL_PROFILE_NOT_EXECUTABLE`, …);
 * a raw code is not a useful terminal error. This maps each code to a
 * one-paragraph operator message naming the profile and the remediation.
 * Pure function — covered by `terminal-errors.test.ts`.
 */
export function terminalRefusalCopy(
  code: string,
  detail: string,
  profileId?: string,
): string {
  const who = profileId ? `profile '${profileId}'` : "the selected profile";
  switch (code) {
    case "TERMINAL_CONTEXT_BELOW_MINIMUM":
      return (
        `/chat needs a model with at least 65,536 context tokens, but ` +
        `${who} declares a smaller window. Pick a high-context profile ` +
        `above, or raise its declared window to the provider's real value ` +
        `(` +
        detail +
        `)`
      );
    case "TERMINAL_CONTEXT_UNVERIFIED":
      return (
        `The real context window of ${who} could not be cross-checked, ` +
        `so /chat stays closed rather than guessing. Pick a profile whose ` +
        `window resolves, or confirm the provider's model card value. ` +
        `(` +
        detail +
        `)`
      );
    case "TERMINAL_PROFILE_NOT_EXECUTABLE":
      return (
        `${who} is not executable: enable egress consent and the provider ` +
        `allowlist entry for it (Models / profile settings), then retry. ` +
        `(` +
        detail +
        `)`
      );
    case "UNKNOWN_PROFILE":
      return (
        `${who} is not a known model profile. Pick one from the list above. ` +
        `(` +
        detail +
        `)`
      );
    default:
      return `[${code}] ${detail}`;
  }
}
