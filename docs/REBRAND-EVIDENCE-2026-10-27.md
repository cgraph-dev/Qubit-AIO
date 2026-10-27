# D19 evidence rebrand — 2026-10-27

**Scope:** the root report/evidence artifacts named by D19 in
`docs/PHASE8_INVENTORY.md` (`INDEPENDENCE_VERDICT.md`, the captured chunk
`docs/e2e/evidence-api-chunk-served.js`) and the evidence artifacts beside them
in `docs/e2e/`. Owner approved rebranding D19's framing; nothing in this lane is
a capability, release, or parity claim.

**Status:** framing rebrand only. No product code, tests, `docs/archive/`, or
services were touched.

## Rule used

Rebrand **document framing only** — title, intro, section headers, summaries and
descriptive prose that names the product. **Preserve literal evidence byte for
byte** — captured output, command logs, code paths, identifiers, systemd unit
names, filesystem paths, quoted upstream text, and captured chunks. An old name
inside a preserved literal is not a brand leak: it is the thing the audit
measured, and changing it would falsify the record (`docs/BRAND.md` §2–§3).

Where framing prose used the old product name as the subject, it now says
"Qubit" and names the old tree explicitly as the **legacy** tree. Where the old
name is part of a path, identifier, unit name or captured line, it is unchanged.

Each edited Markdown document carries this banner:

> **Historical evidence — captured output retains pre-rebrand names.**

## Files inspected and changed

| File | Action | Reason |
|---|---|---|
| `INDEPENDENCE_VERDICT.md` | **Changed** (framing + banner) | The only root report named by D19. Title, verdict line, four section/table labels and descriptive prose rebranded. |
| `docs/e2e/2026-10-16-e2e-journey-walk.md` | **Changed** (title + banner) | Evidence write-up in `docs/e2e`; already Qubit-framed. All raw captures left verbatim. |
| `docs/e2e/E2-memory-wiki-sdk-global.md` | **Changed** (title + banner) | Evidence write-up in `docs/e2e`; already Qubit-framed. All raw captures left verbatim. |
| `docs/e2e/evidence-api-chunk-served.js` | **Preserved byte-identical** | Captured served bundle; its sha256 (`a2db2069…`) is the artifact identity recorded in the journey walk. Injecting a banner would change the evidence. |
| `docs/e2e/evidence-ok-walk.txt` | **Preserved byte-identical** | Raw browser walk capture; contains zero old-name occurrences, so no banner was needed. |

No other root report was edited. D19 names `INDEPENDENCE_VERDICT.md` and the
captured chunk specifically; the `REPORT-*`, `PROGRESS-*`, `HANDOFF.md` and
`PORT-METHOD.md` files contain literal upstream identifiers, staged progress
logs or provenance, not D19 evidence framing.

## Rebranded vs preserved at a glance

| Metric | Count |
|---|---|
| Files inspected | 5 |
| Files changed | 3 Markdown |
| Files preserved byte-identical | 2 (raw captures) |
| Framing sites rebranded in `INDEPENDENCE_VERDICT.md` | 12 |
| Titles rebranded in `docs/e2e` | 2 |
| Banners added | 3 |
| Old-name occurrences remaining (all preserved) | **146** |

Remaining old-name occurrences by file (after the edits):

| File | `hermes` (any case) | `.hermes` | `Hermes` | `HERMES_` | Disposition |
|---|---:|---:|---:|---:|---|
| `INDEPENDENCE_VERDICT.md` | 120 | 80 | 0 | 5 | Preserved literal evidence |
| `docs/e2e/2026-10-16-e2e-journey-walk.md` | 11 | 0 | 1 | 9 | Preserved raw captures/quotes |
| `docs/e2e/E2-memory-wiki-sdk-global.md` | 6 | 0 | 0 | 6 | Preserved raw captures/quotes |
| `docs/e2e/evidence-api-chunk-served.js` | 9 | 0 | 1 | 7 | Preserved captured bundle |
| `docs/e2e/evidence-ok-walk.txt` | 0 | 0 | 0 | 0 | No old names |

## What was rebranded

`INDEPENDENCE_VERDICT.md` — framing only:

- Title: `INDEPENDENCE VERDICT — can ~/.hermes be retired?` →
  `Qubit Independence Verdict — can the legacy ~/.hermes tree be retired?`
- Verdict line: "hermes can be deleted today" → "the legacy `~/.hermes` tree
  can be retired today".
- Section/table labels that used the old name as the product:
  "one stray hermes unit" / "hermes path at startup?" /
  "Code path … hermes references" / "STRAY HERMES UNIT FOUND" /
  "retire the stray hermes unit" → each now says **legacy** hermes.
- Descriptive sentences: "that one is hermes itself, not Qubit" → "that one is
  the upstream product itself, not Qubit"; "Retiring hermes must disable it" →
  "Retiring the legacy tree must disable it".

`docs/e2e/2026-10-16-e2e-journey-walk.md` — title only:
`E2E journey walk — 2026-10-16` → `Qubit E2E journey walk — 2026-10-16`.

`docs/e2e/E2-memory-wiki-sdk-global.md` — title only:
`E2 — the Memory Wiki plugin tab…` → `Qubit E2 — the Memory Wiki plugin tab…`.

## Remaining old-name occurrences and why each stays

1. **Filesystem paths** — `~/.hermes`, `~/.hermes.retired-*`,
   `~/.local/bin/hermes`, `…/.hermes/.env`, `.hermes_history`. These name the
   legacy tree/paths being audited; rewriting them would make the report
   describe a tree that never existed.
2. **Systemd unit names** — `hermes-dashboard.service`, `hermes-gateway`,
   `ai.hermes.gateway`. Identity strings of the upstream units; the report's
   finding is about that unit as it is installed.
3. **Environment/name contracts** — `HERMES_HOME`, `HERMES_KANBAN_DB`,
   `HERMES_CRON_AUTO_DELIVER_*`, `HERMES_SYNC_DEVICE_NAME`. Literal names
   quoted from code/prose; `docs/BRAND.md` §2 keeps these.
4. **Code paths, symbols and snippets** — e.g.
   `_external_default_home()`, `qubit_runtime/…:line`, `openclaw_to_hermes.py`,
   `hermes_*` imports. Quoted measurements from the audit; changing them would
   detach the claim from the source.
5. **Raw harness/console output** — `[PASS] … hermes absent`,
   `hermes global: None`, `X-Hermes-Session-Token`, the captured bundle. These
   are the evidence itself.
6. **Legacy-qualified prose** — after the edits, words like "legacy hermes"
   remain in the framing. They now explicitly mark the old tree, not Qubit.

`docs/e2e/evidence-api-chunk-served.js` is intentionally preserved: it is the
captured served chunk whose sha256 (`a2db2069a69f777d59092c54585e08003f2e6b7b9c2265a793e4cdb5039101dc`)
matches the identity recorded in the journey walk. Prepending a banner comment
would make the captured artifact no longer byte-identical. The required banner
is carried by the edited Markdown reports and by this document instead.

## Reproduce the scan

```bash
cd /tmp/opencode/wt-p8-d19

# Counts per D19 artifact
for f in INDEPENDENCE_VERDICT.md docs/e2e/*; do
  printf '%-55s hermes=%-4s .hermes=%-4s Hermes=%-3s HERMES_=%s\n' \
    "$f" \
    "$(grep -oi 'hermes' "$f" | wc -l)" \
    "$(grep -o '\.hermes' "$f" | wc -l)" \
    "$(grep -o 'Hermes' "$f" | wc -l)" \
    "$(grep -o 'HERMES_' "$f" | wc -l)"
done

# Show every preserved occurrence with its line number
grep -n -i 'hermes' INDEPENDENCE_VERDICT.md docs/e2e/*

# Prove the raw captures were not modified
sha256sum docs/e2e/evidence-api-chunk-served.js docs/e2e/evidence-ok-walk.txt
# expected:
# a2db2069a69f777d59092c54585e08003f2e6b7b9c2265a793e4cdb5039101dc  docs/e2e/evidence-api-chunk-served.js
# 2a241b29d1a27a74f03eb9fde9e38e2b676882e78c9f1c5eaa086be8a121388c  docs/e2e/evidence-ok-walk.txt
```

## Deviations

- **No banner inside the raw `.js` capture.** The banner was required for
  preserved old names, but injecting it into `evidence-api-chunk-served.js`
  would break the captured artifact's identity. The banner lives in the edited
  Markdown reports and here; the raw file is unchanged.
- **No edit to `evidence-ok-walk.txt`.** It is raw output and has zero old-name
  occurrences.
- **Other root reports left alone.** They are outside D19's named evidence and
  mostly contain literal upstream identifiers or dated progress logs.

## Not verified

No service was restarted and no runtime/browser behavior was exercised. This is
a documentation artifact change only; `VERIFIED` is not claimed.
