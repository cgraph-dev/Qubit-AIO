# Communications Monitor (Packet 8D) — operations, notifications, rollback

Status: **IMPLEMENTED** (live timer enablement requires explicit user
authorization; this packet never enables it on its own).
Source contract: `docs/UNIFIED_AGENT_PLAN.md` Phase 8 tasks 15–19 and the
prompt-pack Packet 8D prompt. Built on Packet 8A schemas/store, Packet 8B
read-only CDP adapters, and Packet 8C report bundles.

## 1. What the hourly monitor does

`scripts/communications_monitor.py --hourly` (also `voicebridge monitor run`)
runs one bounded pass:

1. acquires ONE global overlap lease (`monitor:hourly`, default TTL 480 s —
   greater than the oneshot's 300 s `TimeoutStartSec` plus a safety margin,
   MONOTONIC, renewable, and reboot-aware; see `docs/OPERATIONS_8I.md`);
2. for each configured platform/account, runs the read-only CDP scan
   (attaches to the user's already-running Chromium on loopback `127.0.0.1`);
3. persists **real screenshot bytes** with the report commit (store schema v2
   `evidence_asset.data`) and renders the report bundle from the store, so a
   live scan produces a bundle with actual `.webp` assets — never metadata
   only;
4. records typed notifications (deduplicated desktop + dashboard, opt-in
   quiet-hours voice) and emits a durable `communications.report.ready` event
   to the typed journal the dashboard consumes;
5. runs fail-closed retention (store rows + report bundles) dry-run by default.

Output is redacted metadata only. No send/reply/mark-read, no focus/tab
mutation, no competing browser, no deletion of cursors/reports/memory.

## 2. Byte-persistence hook (the resolved 8C dependency)

Before 8D, live scans committed asset **metadata** (byte_count, sha256) only;
bundles rendered from real scans carried a typed missing-evidence reason even
for successfully captured screenshots. The 8D hook carries the actual
`Page.captureScreenshot` bytes through `AdapterScan.asset_bytes`, stores them
in the same SQLite transaction that advances the cursor (`evidence_asset.data`
BLOB), and `ReportStore.regen`/`render_from_store` load them so published
bundles contain real `.webp` files. Proof: `tests/test_packet8d.py` runs a
live-style scan with screenshot bytes and asserts the stored bytes equal the
served bytes and the bundle hash matches.

## 3. Notifications

- `notify-send` desktop notification on new items with plain-language,
  user-facing copy. Return status is truthful: `OK` only when `notify-send`
  exits 0, `failed` on a non-zero exit or timeout, `unavailable` when the
  binary is missing.
- Notifications fire ONLY for genuinely committed new-item reports. Initial
  baseline scans, no-change scans, and zero-new-item runs are silent.
- Copy is per-platform and friendly:

  | scenario | title | body |
  |---|---|---|
  | 1 new Gmail message | `Qubit: 1 new Gmail message` | `Open Qubit to review it.` |
  | N new Gmail messages | `Qubit: N new Gmail messages` | `Open Qubit to review them.` |
  | 1 new Instagram message | `Qubit: 1 new Instagram message` | `Open Qubit to review it.` |
  | 1 new Messenger message | `Qubit: 1 new Messenger message` | `Open Qubit to review it.` |
  | multiple platforms | `Qubit: New messages found` | `N new messages were found. Open Qubit to review them.` |

- Degraded/auth-required accounts get one deduplicated plain-language alert:
  title `Qubit needs attention`, body `Qubit could not check <Platform>. Open
  Qubit for details.` (single platform) or `Qubit could not check some
  accounts. Open Qubit for details.` (multiple). Never exposes raw exceptions,
  URLs, account IDs, report IDs, senders, subjects, message bodies, cookies,
  or secrets.
- Dashboard: the report bundle appears in the reports panel; `/api/notifications`
  returns recent redacted records and a pending count badge. Report IDs and
  technical details live only in the authenticated dashboard, structured audit
  records, and diagnostic logs — never in user-facing copy.
- Opt-in voice summary (`communications.monitor.voice_notify: true`): a short
  count/platform phrase outside quiet hours (default 22:00–07:00 Europe/Bucharest);
  suppressed inside quiet hours (`SUPPRESSED_QUIET_HOURS` status). **Fail-closed
  (Packet 8I):** malformed quiet-hours syntax/range or an invalid IANA timezone
  is `CONFIG_INVALID`, suppresses voice, and degrades the aggregate — it never
  falls back to host-local time and never disables quiet hours. The summary
  is synthesized through the configured LOCAL Piper voice (CPU, 0 VRAM — never
  a second resident generative model) and played through the repository
  playback contract; `OK` is returned only when synthesis AND playback both
  succeed, with bounded timeout and temporary-file cleanup.
- No-change scans are silent. Auth-required/degraded scans produce a typed
  degraded result and are deduplicated via the notification store.

## 4. systemd service + timer

Templates live in `systemd/`:

- `openclaw-communications-monitor.service` — `Type=oneshot`, bounded
  resources (`MemoryHigh=300M`, `MemoryMax=400M`, `TimeoutStartSec=300`), no
  restart loop, same user config as the bridge; the repository template ships
  **no hardcoded `DISPLAY`** (Packet 8I). The notify-send path resolves the
  real X11/Wayland user session at runtime (`voicebridge/display.py`), so a
  missing/stale session value is a typed failure, never a silent PENDING;
- `openclaw-communications-monitor.timer` — `OnCalendar=hourly`,
  `Persistent=true` (catches up once after downtime), `AccuracySec=1min`.
  `OnCalendar` is wall-clock local time and follows the system timezone/DST
  transitions; the quiet-hours window is evaluated in the configured IANA
  timezone independently.

Commands (`scripts/communications_monitor.py` or `voicebridge monitor`):

| command | behavior |
|---|---|
| `--install` | dry-run: renders both templates + prints the diff, writes NOTHING, enables NOTHING |
| `--install --enable` + `VOICE_BRIDGE_MONITOR_ENABLE=1` | live activation: atomically installs with backup, daemon-reloads, enables ONLY the timer |
| `--enable` (without `--install`) | rejected |
| `--install --enable` (without `VOICE_BRIDGE_MONITOR_ENABLE=1`) | denied; nothing written or enabled |
| `--rollback` | disables the timer; keeps units/cursors/reports/evidence |
| `--health` | typed CDP precondition, timer/service state, last scan, notification backlog, bundle counts |

The timer is NEVER enabled implicitly. The documented safe live-installation
command (installs the units AND enables the hourly timer — a real live
activation):

```bash
VOICE_BRIDGE_MONITOR_ENABLE=1 \
.venv/bin/python scripts/communications_monitor.py --install --enable
```

The equivalent `voicebridge monitor install --enable` requires the same
`VOICE_BRIDGE_MONITOR_ENABLE=1` environment variable. `--dry-run` is reserved
for scan/hourly preview behavior only and never enables anything.

## 5. Chromium-CDP precondition and optional launch

`cdp_precondition()` returns `OK` / `BROWSER_RESTART_REQUIRED` / `UNAVAILABLE`.
When Chromium is already running without loopback CDP, the monitor reports
`BROWSER_RESTART_REQUIRED` and never kills or restarts the browser. The optional
`--launch` path (`communications.monitor.cdp_launch: true`) reuses the existing
snap Chromium executable/profile on loopback only, refuses to start a competing
instance when CDP is absent but Chromium is running, and is off by default.

## 6. Retention

- Store rows: scan/health 90 d, item 90 d, report manifest 90 d, evidence
  asset (incl. bytes) 30 d, notifications 90 d (`apply_retention`).
- Report bundles: 90 d with backup + index-integrity checks before any delete
  (`ReportStore.retention`).
- Retention runs after a successful pass, dry-run by default; nothing is
  deleted implicitly.

## 7. Rollback

1. `voicebridge monitor rollback` (or `scripts/communications_monitor.py
   --rollback`) STOPS and DISABLES the timer — cursors, reports, memory, and
   evidence are preserved. The stop is essential: `systemctl disable` alone
   leaves an already-running timer active, so it would keep firing; a true
   rollback halts the hourly schedule immediately.
2. Installed unit backups are `<unit>.bak.<timestamp>`; restoring them is the
   documented unit rollback.
3. Store schema v2 is additive (`ALTER TABLE evidence_asset ADD COLUMN data
   BLOB`); the existing 8A store migrates in place and older rows keep
   `NULL` bytes (typed missing evidence). Removing the hook reverts to
   metadata-only bundles; no data is lost.