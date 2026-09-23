# Packet 8L — Operator Live-Test Checklist (preparation only; do not run without authorization)

This checklist prepares the operator-authorized live verification required
before Packet 8L's readiness gate. It is **not** run during implementation or
hermetic verification. The operator runs each item only after explicit
authorization, and every live item is harmless, read-only, or controlled and
restores the original state afterward (with proof).

Read first: `docs/AUDIO_SETUP.md` (the working audio config — do not break it),
`docs/OPERATIONS_8KP.md`, `docs/VOICE_BRIDGE_SYSTEM.md`, and this checklist.

## General rules for every live test

- Show the exact commands and the affected resources before running.
- Never silently choose a different audio device, browser profile, display,
  account, model, or provider. If a pinned device is missing, report
  `DEVICE_MISMATCH` — never reroute.
- Never mutate accounts, send/publish messages, solve CAPTCHA/2FA, or change
  credentials. Browser checks use the already-logged-in Chromium read-only.
- Restore the original service/timer state afterward and prove the restore
  (journal + `systemctl --user is-active`).
- Record results as typed states (OK / DEGRADED / DEVICE_MISMATCH / FAILED /
  BLOCKED) with timestamps.

## 1. Real audio (mic + output)

Requires the operator's physical microphone speech and confirmation that
playback is audible.

- `voicebridge audio status` — confirm the pin is `Scarlett 2i2 4th Gen: USB
  Audio` (name pin, never an index). If the pin resolves to nothing, report
  `DEVICE_MISMATCH` and stop; do not fall back to a different device.
- `voicebridge mic-test` — speak into the AT2020; confirm a healthy peak (not
  clipping), 48V phantom on, gain knob up.
- Hold **Right Ctrl** and speak one short command; confirm the PTT beep,
  ASR transcript, agent reply, and audible Piper playback on the default sink
  (Scarlett iec958). Confirm `webui.speak_web_locally: false` so nothing
  double-speaks.
- Interruption/barge: speak mid-TTS and confirm only the enrolled voice
  interrupts (voice-ID), then confirm no cross-turn answer or duplicate
  listener.
- Latency: measure capture → ASR → route → reply → playback end-to-end and
  record the numbers.
- Hot-plug/recovery: with authorization, unplug/replug the USB interface and
  confirm the bridge re-pins by name and recovers without a restart storm.
- After each test, confirm `voicebridge audio status` still reports the same
  configured pin; restore any changed setting and prove it.

## 2. Dashboard (web UI) and pairing

- Start the bridge (`voicebridge run` or the user service), then
  `voicebridge webui --open` — confirm it opens `http://127.0.0.1:8890/` with
  no token in the URL.
- Confirm pairing is truthful: `voicebridge product first-run` reports
  `REQUIRES_PAIRING` until a real `webui.token` exists, then `PAIRED`; the
  token is never printed or placed in a URL.
- Exercise the chat page, activity feed, settings, reports, approvals,
  websites, actions, and token-setup pages on desktop and narrow mobile
  viewports; confirm every authenticated API call uses the token header
  (never `?token=`), empty/loading/error states, and no raw dynamic HTML.
- Accessibility spot checks: keyboard-only flow, focus order, visible focus,
  screen-reader labels, reduced motion, contrast, 44×44 px touch targets,
  320 px reflow, 200% zoom.

## 3. Browser (read-only CDP)

- Confirm the existing user-launched Chromium on loopback CDP
  (`http://127.0.0.1:9222/json/version`). Run one bounded, read-only inbox
  scan (Gmail/Instagram/Messenger) on an operator-controlled item; confirm no
  tab is focused, navigated, closed, or mutated; no message is sent/marked;
  no cookie/token is exported; screenshots are clipped and bounded.
- Confirm a missing/unlocked target and a profile-lock state produce a typed
  failure with guidance, never a silent fallback to another profile.

## 4. Local model / provider

- `voicebridge status` — confirm the provider (Ollama) and the pinned model
  (`qubit-brain4`) digest are present and verified; never silently switch the
  model or provider.
- Confirm offline mode reports a typed `MISSING`/degraded state instead of a
  misleading "installed" claim when the provider is absent.

## 5. Timer / notifications (quiet hours)

- Only with explicit authorization, exercise the user-systemd oneshot/timer
  on an accelerated operator-controlled schedule: confirm no-change silence,
  one genuinely new notification, deduplication, desktop delivery, quiet
  hours (valid IANA timezone), catch-up, and that no report/token/URL is
  exposed in notifications.
- Disable the timer afterward and prove it is disabled
  (`systemctl --user is-active <timer>` / `list-timers`).

## 6. Service / restart / rollback

- `voicebridge product service` — confirm the typed state is `PRESENT` (or
  `UNIT_MISSING`/`STALE`/`DUPLICATE` with the exact next action) and that
  `active_state` requires an operator `systemctl --user is-active` check.
- With authorization, restart the service once and confirm exactly one bridge
  process and one hotkey listener (no duplicate listeners), then restore the
  original state and prove it.
- Rehearse `voicebridge product rollback --apply` and
  `voicebridge product migrate-rollback --apply` against a disposable backup
  in a scratch HOME; never against live state.

## 7. What is never done during live checks

- No real account mutation, sending, publishing, purchasing, or CAPTCHA/2FA
  solving.
- No silent rerouting of mic/output/display/browser/model/provider.
- No signing-key generation; no claim that an artifact is signed.
- No changes to credentials, browser profiles, or user state without
  authorization and a restore-and-prove step.

## Result log

| Check | Command | Typed state | Evidence (redacted) | Restore proof |
|---|---|---|---|---|
| … | … | … | … | … |