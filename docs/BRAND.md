# Qubit BRAND — zero legacy brand on product surfaces

**Owner directive (2026-09-22).** No legacy brand reference may be left in Qubit
on any **shipped or user-facing surface**: not `NousPortal`, not `Hermes`, not
`OpenClaw`, and not `Nous Research` / `@NousResearch`. This includes URLs, page
titles, navigation labels, CLI commands and help text, installer / service / log
/ report labels, dashboard paths, built/embedded bundles, and shipped docs.

This is not achieved. Current footprint below.

## The one required exception (do not remove)

**Legal attribution.** `LICENSE` and `THIRD_PARTY_NOTICES.md`, and the upstream
license/copyright text they carry, **must** retain the upstream copyright and
license line. Removing it is a license violation and the repo's own rule
(`AGENTS.md`) requires stopping with `BLOCKED — LICENSE REVIEW REQUIRED` when
provenance or compatibility is uncertain. These legal/provenance surfaces are the
**only** place a legacy name may remain in the shipped product, and only as
attribution.

## Preserved as-is (not product surfaces, not edited)

- `docs/archive/2026-09-21/` — frozen records and historical evidence.
- `docs/differential/INTERFACE.md` — frozen contract surface (contains legacy
  tokens by definition of what it compares against).
- `docs/archive/2026-09-22-pre-reset/` — the pre-reset docs.

## Internal engineering docs

An internal doc may name the upstream **only** where it is load-bearing (e.g.
stating which install is the parity reference, or which files were vendored).
Everywhere else, use "the upstream reference" / "the reference dashboard".

## Current footprint (measured 2026-09-22, files containing any legacy token)

**Scope:** repo excluding `.git`, `node_modules`, `dist`, `graft`, `.venv`, and
`docs/archive` (historical). Measured 2026-09-22:

| Token | Live files |
|---|---:|
| `Hermes` | 1,167 |
| `NousResearch` | 380 |
| `NousPortal` | 32 |
| `OpenClaw` | 185 |
| **unique files (any token)** | **1,634** |

Including `docs/archive` (exempt/frozen) the unique total is **1,752**. Per-area:
`qubit_runtime/` dominates (890 `Hermes`, 363 `NousResearch`, 27 `NousPortal`),
then `tests/` (211 `Hermes`, 116 `OpenClaw`), `docs/` non-archive (112 `Hermes`,
99 `OpenClaw`), `scripts/` (22 `Hermes`, 13 `OpenClaw`), `frontend/`
(11 `Hermes`), `voicebridge/` (6 `Hermes`, 27 `OpenClaw`).

The `qubit_runtime/` figure is the deep-rebrand core: the fork is renamed but
upstream identifiers (`hermes_*`, `HERMES_HOME`, `NousResearch`) remain.

## Known conflicts to resolve first

- `tests/test_branding_bundle_scan9u.py` (requires de-branding) vs
  `tests/test_branding_leaks_fixes.py` (requires the strings present). Owner
  Ruling 1 → **Option A** (policy wins; upstream refs only in legal/provenance).
  Applying Option A requires updating the second test and regenerating
  `frontend/qubit-dist`.
- The shipped bundle currently embeds the upstream docs URL and the
  `@NousResearch` handle.

## Method (repeatable)

1. **Enumerate** every legacy token with the branding scanners (fail-closed).
2. **Classify** each hit: product surface / legal attribution / frozen record /
   internal doc.
3. **De-brand** product surfaces: rename identifiers, labels, URLs, titles,
   service/dashboard/report names, and rebuild bundles.
4. **Keep** legal attribution untouched.
5. **Re-run** the branding scans; zero unallowlisted hits on product surfaces.

## Non-negotiables

- Never delete or alter the legal attribution.
- Never edit frozen records or `docs/differential/INTERFACE.md`.
- A page, bundle, or route that merely *renders* is not evidence; proof is a
  command + observed output.
