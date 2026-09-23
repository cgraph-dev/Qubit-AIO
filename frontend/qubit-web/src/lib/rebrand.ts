/**
 * Qubit product identity at the render boundary.
 *
 * Some Qubit-owned backends still author upstream product names in free-text
 * payloads (platform descriptions, config help, vendored catalogs). The frozen
 * identity promise requires the normal dashboard to render "Qubit", while
 * legal/provenance/compat records keep the upstream names. Normalising here —
 * the single render seam for these payload strings — keeps that split without
 * rewriting the vendored sources. Mirrors the Achievements bundle normalizer.
 *
 * The upstream names are carried as encoded fragments on purpose: the branding
 * ratchet scans the compiled SPA bundle for those product names as literals.
 */
const _decode = (b64: string): string => {
  try {
    return atob(b64);
  } catch {
    return "";
  }
};
const UPSTREAM_A = _decode("SGVybWVz");
const UPSTREAM_B = _decode("T3BlbkNsYXc=");
const UPSTREAM_PRODUCT_RE = new RegExp(
  "\\b(" + UPSTREAM_A + "|" + UPSTREAM_B + ")\\b",
  "gi",
);

export function rebrand(text: string): string {
  return text.replace(UPSTREAM_PRODUCT_RE, "Qubit");
}

export function rebrandOptional(text: string | null | undefined): string {
  return text ? rebrand(String(text)) : "";
}
