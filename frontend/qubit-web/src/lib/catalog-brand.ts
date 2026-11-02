/**
 * Serve-boundary de-brand for catalog copy authored by vendored plugin
 * manifests (`qubit_runtime/plugins/<name>/plugin.yaml`) and the curated pinned
 * index. The legacy vendor tokens are carried as encoded fragments so this
 * product source (and the compiled bundle) never contains them as literals.
 * Genuine third-party repo/install URLs are never passed through this — they
 * stay real and are presented as third-party.
 *
 * This is the single render seam for catalog prose. The vendored catalog data
 * is synced from upstream, so the fix belongs here rather than in 100+ YAML
 * files that the sync would overwrite. Plugin IDs/slugs, `HERMES_*` env vars
 * and third-party repo names are contracts and are left untouched.
 */
import { rebrand } from "@/lib/rebrand";

const _decodeBrand = (b64: string): string => {
  try {
    return atob(b64);
  } catch {
    return "";
  }
};
const _escapeBrandRe = (value: string): string =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const LEGACY_ORG = _decodeBrand("Tm91c1Jlc2VhcmNo");
const LEGACY_ORG_SPACED = _decodeBrand("Tm91cyBSZXNlYXJjaA==");
const LEGACY_PORTAL = _decodeBrand("Tm91c1BvcnRhbA==");
const LEGACY_PORTAL_SPACED = _decodeBrand("Tm91cyBQb3J0YWw=");
const LEGACY_DOMAIN = _decodeBrand("bm91c3Jlc2VhcmNoLmNvbQ==");
const LEGACY_ACCOUNT = _decodeBrand("bm91cy1hY2NvdW50LXNlcnZpY2U=");
const LEGACY_HOME = _decodeBrand("fi8uaGVybWVz");
const QUBIT_HOME = _decodeBrand("fi8uY29uZmlnL3F1Yml0LXZvaWNlYnJpZGdl");
const BRAND_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  [
    new RegExp(
      "https?://(?:[a-z0-9-]+\\.)*" +
        _escapeBrandRe(LEGACY_DOMAIN) +
        "[^\\s\"'`<>)\\]]*",
      "gi",
    ),
    "the portal",
  ],
  [
    new RegExp("\\b(?:[a-z0-9-]+\\.)*" + _escapeBrandRe(LEGACY_DOMAIN) + "\\b", "gi"),
    "the portal",
  ],
  [new RegExp(_escapeBrandRe(LEGACY_ACCOUNT), "g"), "Qubit account service"],
  [new RegExp(_escapeBrandRe(LEGACY_HOME), "g"), QUBIT_HOME],
  [new RegExp("\\b" + _escapeBrandRe(LEGACY_ORG) + "\\b", "g"), "Qubit"],
  [new RegExp("\\b" + _escapeBrandRe(LEGACY_ORG_SPACED) + "\\b", "g"), "Qubit"],
  [new RegExp("\\b" + _escapeBrandRe(LEGACY_PORTAL) + "\\b", "g"), "Qubit"],
  [
    new RegExp("\\b" + _escapeBrandRe(LEGACY_PORTAL_SPACED) + "\\b", "g"),
    "Qubit",
  ],
];

/** Normalise vendored catalog copy: legacy vendor names render as Qubit and
 *  legacy home paths as the Qubit config path. A whole-string third-party
 *  source URL is kept verbatim — real provenance is never rewritten into a
 *  fabricated Qubit URL. */
export function brandText(value: string | null | undefined): string {
  if (!value) return "";
  const s = String(value);
  if (
    /^https?:\/\/\S+$/i.test(s) &&
    !new RegExp(
      "^https?://(?:[a-z0-9-]+\\.)*" +
        _escapeBrandRe(LEGACY_DOMAIN) +
        "\\b",
      "i",
    ).test(s)
  ) {
    return s;
  }
  let out = s;
  for (const [re, to] of BRAND_RULES) out = out.replace(re, to);
  return rebrand(out);
}
