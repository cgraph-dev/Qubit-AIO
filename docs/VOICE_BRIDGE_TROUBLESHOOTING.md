# Qubit Voice Agent: Troubleshooting and Recovery

This is the practical recovery guide for the current local voice agent. The
systemd unit is installed as `qubit-voice-agent.service`. Existing machines
migrated from the historical OpenClaw Voice Bridge name may keep a legacy
`openclaw-voice-bridge.service` unit until you run the documented migration
(`voicebridge product migrate --apply`); do not run both units at the same
time.

## First: identify which part failed

There are three separate parts:

1. **Voice bridge service** — microphone, Right-Ctrl PTT, speech recognition,
   model requests, and spoken replies.
2. **Chromium/CDP** — the already logged-in browser used for read-only Gmail,
   Instagram, and Messenger scans.
3. **Communications timer** — the hourly read-only scan and notification job.

Restarting the communications timer does not repair a voice-bridge audio
failure. Restarting the voice bridge does not start Chromium or log in to a
website.

## Normal health checks

Run these from the repository:

```bash
cd ~/qubit-voice-agent

systemctl --user status qubit-voice-agent.service --no-pager -l
.venv/bin/python scripts/communications_monitor.py --health
```

Healthy voice-bridge signs:

- `Active: active (running)`;
- an `xinput test-xi2 --root` child is present when X11 hotkeys are enabled;
- no repeated `ALSA`, `PortAudio`, `playback.failed`, or
  `mic.capture.error` messages.

Healthy monitor signs:

- CDP state is `OK`;
- the communications timer is `active` and `enabled`;
- the worker service is usually `inactive` between hourly runs because it is a
  oneshot service;
- `no_change` means no new item was found and is intentionally silent.

## Restarting the voice bridge

Use this after changing an audio device, after an audio backend failure, or
when the bridge is genuinely stuck:

```bash
systemctl --user restart qubit-voice-agent.service
systemctl --user status qubit-voice-agent.service --no-pager -l
```

To inspect the last messages:

```bash
journalctl --user -u qubit-voice-agent.service -n 100 --no-pager -o short-precise
```

The unit uses `Restart=always` and `RestartSec=2s`. This means systemd
restarts it when the main Python process exits. It does **not** restart a
process that is alive while its microphone, playback stream, or hotkey child
is internally broken. That is why a manual restart may be needed after an
ALSA/PortAudio failure.

If systemd has temporarily rate-limited starts:

```bash
systemctl --user reset-failed qubit-voice-agent.service
systemctl --user start qubit-voice-agent.service
```

Do not use `kill -9`, delete the state directory, or remove databases as a
first response.

## Right-Ctrl does not work

First check whether the key event is arriving:

```bash
xinput list --short
ps -ef | grep '[x]input test-xi2'
```

The service resolves the REAL X11 session at runtime (`voicebridge/display`) and
only reports hotkeys active when that display is genuinely reachable. It no
longer ships a hardcoded `DISPLAY`/`XAUTHORITY`. If your session environment is
not inherited by the user systemd instance, import it once:

```bash
systemctl --user import-environment DISPLAY XAUTHORITY
systemctl --user restart qubit-voice-agent.service
```

Check recent bridge events:

```bash
rg -n 'hotkey|feedback\.beep|mic\.capture|playback\.' \
  $HOME/.local/state/qubit-voice-agent/events.jsonl | tail -50
```

Interpretation:

- `hotkey` with `ptt_start` and `ptt_end` means Right-Ctrl is working; the
  failure is later in audio capture or playback.
- `hotkey.xinput.dead` or no hotkey events means restart the bridge and check
  the display resolution (`journalctl --user -u qubit-voice-agent | grep
  hotkey`) and that your session env is inherited
  (`systemctl --user import-environment DISPLAY XAUTHORITY`).
- `feedback.beep` with `played=false` means the key was received but no output
  device opened.
- If there is no X session, hotkeys are unavailable by design; use the manual
  Enter-to-record mode or start the service inside the graphical user session.

After correcting the cause, restart the bridge once. Do not repeatedly restart
it while the audio device is unavailable; that only creates a restart loop.

## Microphone and headphones problems

The current setup uses the Focusrite Scarlett 2i2 4th Gen / AT2020 as the
input and the selected desktop sink for output. Device numbers can change;
use stable names from PipeWire/PulseAudio instead of guessing numeric ALSA
indices.

Inspect the current devices:

```bash
pactl info | grep -E '^(Default Sink|Default Source):'
pactl list sources short
pactl list sinks short
```

If the default sink is an unavailable HDMI/TV device and the HyperX headset is
the intended headphones, set the available HyperX sink shown by the previous
command:

```bash
pactl set-default-sink <the-hyperx-sink-name-from-the-command-above>
```

For the current machine, that name has been:

```text
alsa_output.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.iec958-stereo
```

If the Scarlett input is not the default source, select the exact
`alsa_input...Focusrite_Scarlett...` source printed by `pactl list sources
short`:

```bash
pactl set-default-source <the-focusrite-source-name-from-the-command-above>
```

Then restart the bridge:

```bash
systemctl --user restart qubit-voice-agent.service
```

Check for recovery:

```bash
journalctl --user -u qubit-voice-agent.service -n 80 --no-pager \
  | grep -E 'ALSA|PortAudio|playback\.failed|mic\.capture\.error|hotkey'
```

The commands above change the current user audio session. They may need to be
repeated after a reboot, headset unplug/replug, or an audio-server reset. If
the default sink is already correct, do not run `set-default-sink` again;
restart only when the bridge itself needs reinitialization.

Common meanings:

- `ALSA error -71` / `Protocol error`: the selected PipeWire/ALSA stream could
  not be opened; inspect the default sink/source and then restart the bridge.
- `no output device could be opened`: playback cannot reach the current sink.
- `mic.capture.error`: the input stream cannot be opened; verify the Scarlett
  source and that another application is not holding the device exclusively.
- `played=false`: the PTT event arrived, but the confirmation beep did not
  play.

## Chromium and CDP

Qubit never kills or silently replaces the user's browser. Chromium must be
started by the user with the existing logged-in profile, then the monitor
attaches through loopback CDP.

First check CDP:

```bash
curl -fsS http://127.0.0.1:9222/json/version
```

If Chromium is already running without CDP:

1. Close Chromium normally so the profile lock is released.
2. Start it in the graphical session with the documented profile:

```bash
DISPLAY=:1 /snap/bin/chromium \
  --user-data-dir=$HOME/snap/chromium/common/chromium \
  --profile-directory=Default \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --no-first-run \
  --restore-last-session
```

3. Log in manually in that window if required.
4. Re-run the CDP check and then run a dry-run scan.

Do not run a second Chromium process against the same profile. A
`SingletonLock: Permission denied` or profile-lock error usually means the
profile is still open or the directory is not owned by the current user.

## Manual monitor scans

A fixture dry-run does not access real accounts or commit state:

```bash
.venv/bin/python scripts/communications_monitor.py \
  --once --dry-run --fixture <fixture.json> --platform gmail
```

An explicitly authorized live read-only canary uses the logged-in Chromium
profile and does not send, click, type, mark-read, or modify tabs:

```bash
VOICE_BRIDGE_CANARY=1 .venv/bin/python scripts/communications_monitor.py \
  --once --live --canary --platform gmail
```

Replace `gmail` with `instagram` or `messenger` when testing those platforms.
The result is typed JSON. Important states include:

- `healthy`: scan completed;
- `no_change`: no new item since the cursor;
- `unavailable`: Chromium/CDP or the target page is unavailable;
- `auth_required`: manual login is required;
- `degraded`: selectors or evidence were incomplete;
- `failed`: the scan failed and the cursor must not advance.

Never treat `unavailable`, `degraded`, or `failed` as a successful scan.

## Hourly timer and notifications

Inspect the timer and its recent executions:

```bash
systemctl --user status openclaw-communications-monitor.timer --no-pager -l
journalctl --user -u openclaw-communications-monitor.service \
  --since '3 hours ago' --no-pager
```

The timer is intentionally quiet for an initial baseline, no-change scan, or
zero new items. A notification should be produced only after a genuinely new
item is committed. Re-scanning the same item must not create a duplicate.

The hourly timer is an explicit opt-in operation:

```bash
VOICE_BRIDGE_MONITOR_ENABLE=1 .venv/bin/python \
  scripts/communications_monitor.py --install --enable
```

Do not run that command merely to test a fixture. Use `--once --dry-run`
instead.

## Notifications and reports

Desktop notification text is intentionally plain and redacted. It should say
what needs attention, for example “Qubit: 1 new Gmail message — Open Qubit to
review it.” Report IDs and technical details belong in the authenticated
dashboard, audit records, and logs—not the desktop popup.

If a popup says “0 new items” or repeats the same report:

1. Check the monitor health output.
2. Check whether the scan was a baseline or `no_change` run.
3. Check pending notification records and the monitor journal.
4. Do not delete cursors or reports to force another popup.

## Safe rollback

Rollback disables the hourly monitor while preserving cursors, reports,
evidence, memory, and notification records:

```bash
.venv/bin/python scripts/communications_monitor.py --rollback
```

Installed unit backups are kept with timestamped `.bak` names. Do not delete
the state directory as a rollback method.

## When to report a bug

Collect status and redacted logs, but do not include tokens, cookies, message
bodies, email addresses, or browser profile files:

```bash
systemctl --user status qubit-voice-agent.service --no-pager -l
.venv/bin/python scripts/communications_monitor.py --health
journalctl --user -u qubit-voice-agent.service -n 100 --no-pager \
  | tail -100
```

Report separately whether the failure is:

- Right-Ctrl event missing;
- microphone capture failing;
- beep/TTS playback failing;
- model response failing;
- Chromium/CDP unavailable;
- a timer/notification/report problem.

This separation prevents an audio failure from being misdiagnosed as a model,
browser, or timer failure.
