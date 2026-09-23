# Packet 8G — real Chromium/CDP qualification (operator runbook)

Status: **IMPLEMENTED** — hermetic qualification is safe to run now. LIVE
browser steps are **opt-in** and require `VOICE_BRIDGE_QUALIFY=1`; this packet
never enables services, timers, or live scanning on its own.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §4.1 (CDP upstream),
Packets 8A–8F, and the prompt-pack Packet 8G prompt.

## 1. What this packet qualifies

It qualifies the **existing user-launched real Chromium** through loopback CDP
(`127.0.0.1:9222`) for the strictly read-only Gmail / Instagram / Messenger
path. It covers:

| 8G requirement | Where it is proven |
|---|---|
| CDP precondition (`OK` / `BROWSER_RESTART_REQUIRED` / `UNAVAILABLE`) | `--preflight` |
| Profile-lock / browser-restart states | `--preflight` (profile lock is deliberately `not_probed` — probing would launch a competing profile) |
| Target discovery + hidden/unavailable targets | `--targets`, `qualify_targets` |
| Ambiguous multi-target refusal (never guess) | `--targets` |
| Per-platform failure isolation | `--targets`, live scans |
| Manual login / auth / 2FA / CAPTCHA states | live scans (`auth/login_2fa_captcha` state) |
| Gmail/Instagram/Messenger read-only scans | `--scan --live` |
| Clipped, bounded screenshot bytes | live scans + `--preflight` evidence |
| Reconnects | `probe_reconnect` (connect/close ×2, no mutation) |
| Selector drift | live scans / `scan_states` |
| Baseline → no-change rescan | `--fixture` pipeline |
| Operator-arranged external test item (never sent by Qubit) | runbook §6 + `--external` fixture simulation |
| No Playwright personal profile / cookie import / CDP leak / tab mutation | by construction (8B `CdpClient` allowlist + this harness) |

## 2. The harness

- `voicebridge/communications/qualify.py` — staged, bounded qualification.
- `scripts/browser_qualify.py` — CLI (`browser-qualify`).
- `tests/test_browser_qualify.py` — hermetic focused tests.
- Synthetic fixtures in `tests/fixtures/communications/` (`new_items.json`,
  `qualify_external_item.json`).

Output is machine-readable JSON with **typed** results. Live execution is
**impossible by default**: every live stage requires `--live` AND
`VOICE_BRIDGE_QUALIFY=1`, otherwise it fails closed
(`LIVE_NOT_AUTHORIZED`) and touches nothing.

## 3. Preflight (safe, no live access)

```bash
.venv/bin/python scripts/browser_qualify.py --preflight
```

Reads the loopback CDP precondition and, when CDP is reachable, prints
origin-only target metadata (no titles, no query strings). Expected typed
states:

- `OK` — loopback CDP answers; proceed to §4.
- `BROWSER_RESTART_REQUIRED` — Chromium is running without
  `--remote-debugging-port=9222`. The operator must restart Chromium with CDP;
  the harness never kills/restarts the browser.
- `UNAVAILABLE` — no Chromium/CDP; nothing can be qualified live.

## 4. Hermetic qualification (no browser, no live access)

```bash
.venv/bin/python scripts/browser_qualify.py \
  --scan gmail instagram messenger \
  --fixture tests/fixtures/communications/new_items.json \
  --external tests/fixtures/communications/qualify_external_item.json
```

Runs, per platform, in an **isolated** qualification state dir
(default `<config state>/qualification`):

1. **baseline** commit (first observation — no old-message alert);
2. rendered **8C report bundle** (JSON/Markdown/HTML + assets);
3. repeat of the same fixture → **no-change** (silent, no new report);
4. `--external` fixture (baseline rows + **one** new synthetic item) → exactly
   **one new** item committed;
5. repeat → **no-change** again (no repeat alert).

The production communications store, report root, browser, services, timers,
and user config are never touched (`test_fixture_qualification_never_touches_production_state`).

## 5. Live qualification (OPT-IN, per platform)

Prerequisites:

- Chromium already running with loopback CDP (see §3, `--preflight` = `OK`).
- Logged into the account in that Chromium (Gmail, Instagram Direct, or
  Messenger).
- Operator gate:
  ```bash
  VOICE_BRIDGE_QUALIFY=1
  ```

Start with **target discovery**:

```bash
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/browser_qualify.py \
  --targets --live
```

Then a **read-only live scan** per platform (do one platform at a time):

```bash
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/browser_qualify.py \
  --scan gmail --live
```

The scan attaches to the already-open Gmail target, runs ONLY the
repository-owned extraction JavaScript (`Runtime.evaluate` allowlist + clipped
`Page.captureScreenshot`), and persists ONLY bounded redacted evidence (clipped
`.webp`, capped at 400 KB, sha256'd, `0700`/`0600`) and typed health into the
isolated state dir. Senders, subjects, message bodies, cookies, tokens, and
secrets are never stored.

Typed live results to expect:

- `state: OK` + `health: healthy` — clean read-only scan.
- `auth/login_2fa_captcha` — logged out / 2FA / CAPTCHA / consent screen; do
  **not** automate it. Log in manually, then rescan.
- `selector_drift` — the page loaded but extraction found no rows; update the
  versioned adapter selector in a later packet, never ad-hoc.
- `overflow` — scan hit its bounds; never treats it as a clean pass.
- `unavailable` / `hidden` — no attachable target; no cursor advanced.
- `ambiguous` — multiple targets and none is the preferred route; the harness
  refuses to guess rather than scan the wrong tab.

## 6. External test item + no-change rescan (operator procedure)

Goal: prove exactly **one** genuinely new item is detected and a repeat scan is
silent. The external item must be **arranged by the operator from a separate
device/account — never sent by Qubit**.

1. Run the baseline live scan for the platform (§5) — records first observation.
2. From another device/account, send **one** new message into that inbox.
3. Rescan the same platform live:
   ```bash
   VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/browser_qualify.py \
     --scan gmail --live
   ```
   Expect `commit: new` / `new_count: 1`.
4. Rescan a third time. Expect `no-change` (silent — no repeat alert).

The deterministic core of this procedure (baseline → +1 new → no-change) is
proven hermetically by `--external` and
`test_fixture_qualification_full_pipeline`.

## 7. What is stored and where

- Default root: `<config state>/qualification` (override with
  `--state-dir`). Dirs `0700`, files `0600`.
- `qualify/<run_id>/summary.json` — redacted typed qualification record
  (platforms, states, evidence metadata). Contains no sender/subject/body/
  cookie/token/secret values and no fixture payloads.
- `qualify/<run_id>/<platform>/<asset>.webp` — bounded clipped evidence only.
- `--dry-run` runs the harness without persisting the summary/evidence.

## 8. Cleanup and rollback

- Remove the isolated qualification root to delete all qualification artifacts:
  ```bash
  rm -rf "<config state>/qualification"
  ```
- This packet changes **no** production file: no service, timer, browser tab,
  account, model, audio, or user configuration is modified. Rollback = delete
  the added files (`voicebridge/communications/qualify.py`,
  `scripts/browser_qualify.py`, `docs/OPERATIONS_8G.md`,
  `tests/test_browser_qualify.py`, the external-item fixture). No production
  behavior reverts because none was changed.

## 9. Limitations (honest)

- Live `--scan --live` requires CDP reachable AND the account logged in; a
  skipped or unavailable live stage is **never** reported as a pass.
- Profile-lock state is not directly probed (probing would launch a competing
  profile); it is reported as `not_probed`.
- Reconnect coverage is connect/close only — no page is navigated or reloaded.
- Live qualification persists no message identities and commits no cursor, so
  it intentionally does not produce a *production* report.