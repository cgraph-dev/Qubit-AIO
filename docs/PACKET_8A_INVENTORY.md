# Packet 8A — Inventory: existing real-Chromium CDP and report paths

Status: **IMPLEMENTED (inventory only)** — Packet 8A, 2026-08-27
Source contract: `docs/UNIFIED_AGENT_PLAN.md` Phase 8 task 1 and Packet 8A prompt.
Scope: inventory + versioned contract design only. **No runtime behavior, live
config, service, or timer was changed by this packet.** No real message bodies
were read into fixtures or recorded here.

This document records *where* the legacy paths are so Packet 8B (adapters) and
8C (report bundles) can replace them. All facts below are redacted metadata:
no account names, senders, subjects, message text, cookies, or CDP debugger
WebSocket URLs appear in this file.

---

## 1. Existing inbox adapter paths (legacy)

| Mechanism | Repo path | Notes |
|---|---|---|
| Gmail review script | `scripts/review_gmail.py` | Himalaya CLI → CDP subprocess → fresh/stale cache → "not configured" text; exits 0 even when unconfigured/stale. Cache dir `~/.cache/openclaw-voice-bridge/inboxes`. |
| Instagram review script | `scripts/review_instagram.py` | CDP subprocess → fresh/stale cache → "not configured" text; same failure-shape as Gmail. |
| Messenger review script | `scripts/review_messenger.py` | Same pattern as Instagram. |
| TikTok review script | `scripts/review_tiktok.py` | Same pattern. |
| Dual-browser reader | `scripts/browser_inbox.py` | **Playwright persistent profiles** (`scripts/browser_profiles/`). Per plan rules 34/35 and §6.8, this must NOT be used for Gmail/Instagram/Messenger monitoring; the real Chromium loopback-CDP path replaces it. |
| External Hermes scripts | `$HOME/.hermes/skills/communication/social-inbox-check/scripts/{read_gmail,read_instagram,read_messenger}.py` plus `browser_ensure.sh` | Hardcoded `$HOME/.hermes` dependency (plan P0-Q, rule 43). Not reproducible project dependencies; must be replaced by repository-owned adapters (8B). |

All legacy adapters share the plan §2 audit findings: broad selectors,
unread-as-new conflation, five-minute/seven-day cache fallback that hides
stale data, errors swallowed into exit code 0, and no stable identity or
cursor contract (plan P0-Q).

## 2. Saved-report / API / UI paths

| Surface | Location | Notes |
|---|---|---|
| State root | `$HOME/.local/state/openclaw-voice-bridge/` (mode `0700`) | JSONL ledgers, events, memory/ subdir. |
| Legacy report directory | `<state>/reports/` — **mode `0775`** at preflight (must become `0700`; see §4) | 1028 Markdown files, zero JSON/HTML/image assets at preflight. |
| Legacy report writer | `scripts/generate_report.py` | Regex extraction; writes Markdown only; screenshot references are text lines (plan P0-P). |
| Dashboard "reports" API | `voicebridge/webui.py` `/api/reports` (L851) and `/api/reports/prune` (L1345) | Merges activity-feed + log records and labels them "reports" — NOT the canonical manifest store (plan P0-P, P0-Q). Replacement is Packet 8C. |
| Shared-memory ledger | `voicebridge/shared_memory.py` | JSONL used by `/api/reports`; remains an activity log, not a report store. |

## 3. Service / timer / CDP runtime state (recorded at preflight, 2026-08-27)

- `openclaw-voice-bridge.service` is **enabled** (user systemd). No inbox/monitor
  service or timer unit exists in the repo (`systemd/` has no communications
  monitor) and no such timer is active on the machine — `systemctl --user
  list-timers` showed only unrelated system timers (firmware-updater,
  launchpadlib-cache-clean).
- Loopback CDP endpoint `http://127.0.0.1:9222/json/version` was **not
  reachable** at preflight (browser not running with a debugging endpoint).
  `DEGRADED — CDP PRECONDITION UNAVAILABLE`, consistent with plan §2 audit.
- No live timer/service was installed, enabled, edited, or restarted by this
  packet. Packet 8D owns enablement under explicit user authorization.

## 4. Private-store permission facts (recorded, not fixed here)

- State root `0700` (correct).
- `<state>/reports/` `0775` (private communications/evidence must be `0700`).
- Legacy report `.md` files were mode `0664` per the plan §2 audit.
- Fixes to file modes and the authenticated asset/bundle store belong to
  Packets 8B/8C (8A changes no runtime behavior).

## 5. Contract design delivered by this packet

- `voicebridge/communications/contracts.py` — versioned `scan`,
  `account_health`, `communication_item`, `cursor`, `evidence_asset`,
  `report_manifest`, and `notification` schemas plus stable-identity /
  fallback-fingerprint logic.
- `voicebridge/communications/store.py` — SQLite migration (PRAGMA
  `user_version`), typed statuses, retention (dry-run + audited), lease, and
  the **atomic cursor/report commit** protocol.
- `tests/fixtures/communications/*.json` — synthetic sanitized Gmail /
  Instagram / Messenger and report fixtures (no private data).
- `tests/test_packet8a.py` — contract + store gates.

## 6. Upstream provenance

The pinned upstream references used by these contracts are already recorded in
`docs/UPSTREAM_REUSE.md`: R10 (Chrome DevTools Protocol, BSD-3-Clause), R11
(systemd timer, LGPL-2.1-or-later), R12 (Gmail history API contract), R13
(Playwright report-UI testing, Apache-2.0), R14 (Himalaya fallback, MIT). The
`scan`/`item`/`cursor`/`report`/`asset`/`notification` schemas themselves are
**local design** (no upstream source), recorded as an addendum in
`UPSTREAM_REUSE.md`.

## 7. Rollback

- This packet adds new modules, fixtures, tests, and docs only. Rollback =
  delete the new files (listed above). No live state, service, timer, config,
  or runtime path was touched, so there is no runtime state to restore.
- The `CommunicationsStore` DB is created only under test temp directories
  during this packet; the live state root was never opened by the store.