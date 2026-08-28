# Chromium Operations

This document explains how to run Chromium for Qubit's read-only browser
checks, test Gmail/Instagram/Messenger safely, inspect health and reports, and
register future skills.

## Important safety rules

- Qubit connects to Chromium through loopback CDP at `127.0.0.1:9222`.
- Qubit does not send messages, reply, click, type, mark messages read, focus
  tabs, close tabs, or launch a competing browser.
- A dry-run never changes cursors, reports, notifications, or account state.
- The first real scan creates a baseline and normally produces no notification.
- A notification is produced only after a later scan commits genuinely new
  items.
- Technical report IDs belong in the authenticated dashboard, not desktop
  notification text.

The current repository still uses the legacy service and executable names
`openclaw-voice-bridge` and `voicebridge`. Do not rename those manually unless
the branding migration procedure is being followed.

## 1. Start Chromium with CDP

Chromium is the browser. CDP is the local debugging endpoint that lets Qubit
read approved page data. It is not a separate internet-facing server.

### Check whether CDP is already running

```bash
curl -fsS http://127.0.0.1:9222/json/version
```

If this succeeds, do not start another Chromium instance.

### Start Chromium using the saved Snap profile

First close all Chromium windows normally from Chromium's menu. This is
necessary to release the profile lock and avoid corrupting the saved login
profile. Then run:

```bash
DISPLAY=:0 /snap/bin/chromium \
  --user-data-dir=/home/trick/snap/chromium/common/chromium \
  --profile-directory=Default \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --no-first-run \
  --restore-last-session
```

If Chromium is already running without CDP, do not start a second instance.
Close it normally, then start it with the command above. If the old
`/home/trick/.config/qubit-cdp-chromium-snap` directory reports `Permission
denied`, do not use that directory; use the existing Snap profile above or
repair ownership only after inspecting it.

### Confirm the endpoint and logged-in tabs

```bash
curl -fsS http://127.0.0.1:9222/json \
  | python3 -c 'import json,sys; print("\n".join(x.get("url", "") for x in json.load(sys.stdin)))'
```

Open these pages manually in that Chromium window and log in if necessary:

```text
https://mail.google.com/
https://www.instagram.com/direct/inbox/
https://www.messenger.com/
```

Verify a target tab without exposing page contents:

```bash
curl -fsS http://127.0.0.1:9222/json \
  | grep -E 'mail.google.com|instagram.com|messenger.com'
```

## 2. Run safe fixture dry-runs

These commands use synthetic repository fixtures. They do not touch Chromium,
accounts, cursors, reports, or notifications.

```bash
cd /home/trick/Projects/Repos/openclaw-voice-bridge

.venv/bin/python scripts/communications_monitor.py \
  --once --dry-run \
  --fixture tests/fixtures/communications/gmail_items.json \
  --platform gmail --no-notify
```

```bash
.venv/bin/python scripts/communications_monitor.py \
  --once --dry-run \
  --fixture tests/fixtures/communications/instagram_items.json \
  --platform instagram --no-notify
```

```bash
.venv/bin/python scripts/communications_monitor.py \
  --once --dry-run \
  --fixture tests/fixtures/communications/messenger_items.json \
  --platform messenger --no-notify
```

Expected dry-run fields include:

```text
commit: preview
cursor_advanced: false
would_commit: baseline, new, or no-change
```

## 3. Run a real read-only canary

Run this only after the CDP endpoint responds and the relevant account is
already logged in:

```bash
VOICE_BRIDGE_CANARY=1 \
.venv/bin/python scripts/communications_monitor.py \
  --once --live --canary --platform gmail
```

Use the same command with `instagram` or `messenger` as the platform when
needed:

```bash
VOICE_BRIDGE_CANARY=1 \
.venv/bin/python scripts/communications_monitor.py \
  --once --live --canary --platform instagram
```

```bash
VOICE_BRIDGE_CANARY=1 \
.venv/bin/python scripts/communications_monitor.py \
  --once --live --canary --platform messenger
```

To test the scan without sending a desktop notification, add
`--no-notify`. The live canary is bounded and read-only. If there is no target
tab, the result is typed `unavailable` rather than silently falling back to
another browser or account.

## 4. Health, hourly preview, and timer

Inspect CDP, timer/service state, recent scans, notifications, and report
counts:

```bash
.venv/bin/python scripts/communications_monitor.py --health
```

Run one preview using the platforms configured in `voice.yaml`:

```bash
.venv/bin/python scripts/communications_monitor.py \
  --hourly --dry-run --no-notify
```

Preview the systemd service and timer. This writes nothing:

```bash
.venv/bin/python scripts/communications_monitor.py --install
```

Enable hourly monitoring only when you explicitly want it:

```bash
VOICE_BRIDGE_MONITOR_ENABLE=1 \
.venv/bin/python scripts/communications_monitor.py \
  --install --enable
```

Inspect the timer:

```bash
systemctl --user status openclaw-communications-monitor.timer --no-pager -l
systemctl --user list-timers --all | grep communications
```

Disable the timer while preserving cursors, reports, evidence, memory, and
notifications:

```bash
.venv/bin/python scripts/communications_monitor.py --rollback
```

The voice bridge itself can be inspected or restarted after code changes:

```bash
systemctl --user status openclaw-voice-bridge.service --no-pager -l
systemctl --user restart openclaw-voice-bridge.service
```

## 5. Reports and dashboard

Open the local dashboard:

```text
http://127.0.0.1:8890/
```

If the API has a configured token, provide it through the `X-Api-Token`
header. Do not put the real token directly in a command saved to shell
history:

```bash
read -rsp 'Qubit API token: ' QUBIT_API_TOKEN
echo
curl -fsS http://127.0.0.1:8890/api/reports \
  -H "X-Api-Token: ${QUBIT_API_TOKEN}"
unset QUBIT_API_TOKEN
```

An HTTP `401` means the header is missing or does not exactly match the
configured API token. Query-string tokens are rejected.

The dashboard contains the detailed report, evidence status, timestamps,
platform, freshness, and safe links. Desktop notifications intentionally say
only things such as `Qubit: 1 new Gmail message` and `Open Qubit to review it.`

## 6. Permissions

Show the current permission mode and grants:

```bash
.venv/bin/python -m voicebridge.cli permissions mode show
.venv/bin/python -m voicebridge.cli permissions list
```

Restricted mode is the default and denies capabilities unless explicitly
granted:

```bash
.venv/bin/python -m voicebridge.cli permissions mode set restricted --confirm
```

Unrestricted mode is still scoped and time-limited. It does not bypass hard
denials or confirmation for sending, publishing, purchasing, deletion,
credentials, account/security changes, or system-wide destructive actions:

```bash
.venv/bin/python -m voicebridge.cli permissions mode set unrestricted \
  --scope site:example.com --ttl 3600 --confirm
```

Grant and check a specific capability:

```bash
.venv/bin/python -m voicebridge.cli permissions grant browser.snapshot \
  --scope site:example.com --ttl 3600 --confirm
```

```bash
.venv/bin/python -m voicebridge.cli permissions check browser.snapshot \
  --scope site:example.com
```

Return to the safe baseline and revoke grants:

```bash
.venv/bin/python -m voicebridge.cli permissions reset --confirm
```

Mutating permission commands preview or require explicit confirmation before
persisting. Model output, page text, tool results, and skill instructions
cannot widen permissions.

## 7. Adding a future website, app, or editor

There is currently no generic command that accepts an arbitrary URL and
automatically learns how to operate it. The current monitor officially
supports only Gmail, Instagram, and Messenger.

For a future site, the implementation must first provide a reviewed typed
adapter or a clearly limited launcher. A manifest alone does not make a site
work. The workflow is:

1. implement the smallest adapter or launcher;
2. create a versioned manifest with source, revision, license, hash,
   capabilities, scopes, sensitivity, approval rules, limits, audit events,
   compatibility, and rollback behavior;
3. validate the manifest;
4. stage it disabled;
5. grant the smallest required permission and scope;
6. run synthetic, negative, security, persistence, and resource tests;
7. perform an explicitly authorized canary;
8. document and verify rollback.

Validate a manifest:

```bash
.venv/bin/python -m voicebridge.cli skills check \
  /path/to/site-manifest.json
```

Preview installation. Installation is non-persistent without confirmation:

```bash
.venv/bin/python -m voicebridge.cli skills install \
  /path/to/site-manifest.json
```

Stage it in the durable skill store, still disabled until enabled:

```bash
.venv/bin/python -m voicebridge.cli skills install \
  /path/to/site-manifest.json --confirm
```

Enable, inspect, disable, or remove it explicitly:

```bash
.venv/bin/python -m voicebridge.cli skills list
.venv/bin/python -m voicebridge.cli skills show site.skill.id
.venv/bin/python -m voicebridge.cli skills enable site.skill.id --confirm
.venv/bin/python -m voicebridge.cli skills disable site.skill.id --confirm
.venv/bin/python -m voicebridge.cli skills remove site.skill.id --confirm
```

Browser skills must use the approved existing-Chromium/CDP contract. Editor
skills require workspace scopes. Terminal skills require declared arguments
or isolated execution. A launcher that only opens an application must not
claim that it controls the application.

## 8. What each result means

| Result | Meaning | Correct response |
|---|---|---|
| `OK` | The requested bounded operation succeeded. | Review the returned summary. |
| `preview` | Dry-run calculated what would happen. | Remove dry-run only when ready to commit. |
| `baseline` | First committed scan for that account/scope. | No notification is expected. |
| `no-change` | No new items since the last committed cursor. | Nothing is required. |
| `unavailable` | CDP, target tab, or required source is unavailable. | Start Chromium/CDP or open the correct tab. |
| `auth_required` | The target is present but requires login. | Log in manually; never provide credentials to Qubit. |
| `degraded` | A bounded scan or optional evidence path was incomplete. | Read the health reason and retry after fixing it. |
| `failed` | The operation failed closed. | Inspect logs; do not treat it as success. |

Never treat a green fixture test as proof that a real account scan, real timer,
or real notification has been tested. Those are separate live checks.
