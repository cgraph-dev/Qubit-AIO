# Reports (Packet 8C) — contract, retention safety, operations, rollback

Status: **IMPLEMENTED** (byte-persistence dependency resolved by Packet 8D).
Source contract: `docs/UNIFIED_AGENT_PLAN.md` Phase 8 tasks 10–14 and the
prompt-pack Packet 8C prompt. Built on the Packet 8A schemas/store and the
Packet 8B read-only scan results.

## 1. Canonical report bundles

- `voicebridge/communications/reports.py` writes each report atomically to
  `<state_dir>/reports/<report_id>/` as `manifest.json` (canonical JSON, single
  source of truth), `report.md`, `index.html` (self-contained, responsive,
  print-friendly), and `assets/<asset_id>.webp`.
- Staging: write to `.stage-*`, fsync, validate (schema, hashes, links, MIME),
  then `os.replace` into place; an interrupted write leaves no published bundle.
- Directories `0700`, files `0600`; filenames are generated IDs only.
- Every message/page-derived field is HTML/Markdown-escaped and redacted
  (`voicebridge/redaction.py`) before it reaches a bundle; source links are
  restricted to approved https hosts and secret-bearing URLs are dropped.

## 2. Retention safety (fail-closed)

`ReportStore.retention()` deletes expired bundles **only** after:

1. every candidate is copied into a fresh protected backup directory
   (`.backup-<id>/`) and verified (manifest present + parses);
2. the report index integrity check passes (every bundle manifest parses, IDs
   are strict, no leftover `.stage-*`, no symlinks in the root).

If the backup or index check fails or is unavailable, the result is typed
`BLOCKED`/`DEGRADED` and **nothing is deleted**. Each deletion validates the
report ID and realpath strictly inside the report root (no symlink escape, no
`..`); `ignore_errors` is never used. Every run records redacted report IDs,
timestamp, retention policy, backup ID, index-check result, deleted count, and
failures to `<state_dir>/reports/retention.log` (0600).

Retention never touches cursors, memory, evidence, legacy `.md` files, unrelated
reports, Chromium profiles, or user data.

## 3. Authenticated report API

`GET /api/reports` (index), `GET /api/reports/<id>` (detail),
`GET /api/reports/<id>/assets/<asset_id>` (image), `GET /api/reports/export/<id>`
(zip), `GET /api/reports/<id>/html` (print view), `POST /api/reports/<id>/ack`,
and `POST /api/reports/prune` (retention, dry-run by default) all require the
`X-Api-Token` header. Invalid IDs and traversal return `400`; missing reports
return `404`. `/api/activity` remains the separate activity/log feed — it is
never presented as a report. Legacy root Markdown is indexed separately under
`legacy` and never deleted.

## 4. Dashboard

The repository-owned static dashboard (`frontend/index.html` "reports" panel)
renders real canonical bundles: cards with status/freshness/new counts/
platforms/ack, search + platform filters, detail with items/evidence/thumbnail/
safe links, acknowledge / export / print view. All message-derived text is
rendered with `textContent` (never `innerHTML`); every control calls the
authenticated backend.

## 5. Operator restart and verification

The running bridge still serves the previous `/api/reports` until it is
restarted with the new code. The exact operator step:

```bash
systemctl --user restart openclaw-voice-bridge
curl -s http://127.0.0.1:8890/api/reports -H "X-Api-Token: <token>" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert "reports" in d and "legacy" in d, "old route"; print("canonical report route loaded")'
```

Do not claim the running service is updated until a fresh process has actually
loaded the new route (a subprocess smoke test proves the new code path).

## 6. Screenshot / evidence asset persistence (RESOLVED by Packet 8D)

Packet 8C persists canonical asset **bytes** atomically, hash/MIME/byte-count
validated, private, and linked from manifest, Markdown, HTML, API, and export —
**when those bytes are supplied**. The live scan path now supplies them: the
Packet 8D byte-persistence hook carries the actual `Page.captureScreenshot`
bytes from the adapter (`AdapterScan.asset_bytes`), persists them **durably
with the report commit** (store schema v2 `evidence_asset.data` BLOB, same
transaction as the cursor advance), and renders the report bundle from the
store so bundles produced from real scans carry real `.webp` assets.

Assets that still have no bytes keep an explicit typed missing-evidence reason
(`NOT_AVAILABLE` / `TARGET_HIDDEN` / `CLIP_FAILED`) and never erase the text
item. Screenshot retention is the existing 30-day `evidence_asset` policy; the
rendered bundle copy follows the 90-day report retention. No screenshots are
fabricated and no screenshot persistence is claimed from fixture-only evidence.

## 7. Rollback

- Per-bundle: `ReportStore.regen(store, report_id)` re-renders a bundle from the
  canonical 8A store (idempotent; previous bundle retained as `.bak-<ts>`).
- Retention: `.backup-<id>/` dirs keep a protected copy of pruned bundles;
  restore by copying the backup back into place.
- Full packet: remove `voicebridge/communications/reports.py`,
  `tests/test_report_bundles.py`, `tests/test_webui_reports.py`,
  `tests/fixtures/communications/legacy_report.md`; revert the `webui.py` report
  routes, `frontend/index.html` reports panel, the `test_packet8a.py` guard, and
  the `UPSTREAM_REUSE.md` R19 edit. No live state, service, account, browser,
  timer, model, or audio setting is changed by this packet.