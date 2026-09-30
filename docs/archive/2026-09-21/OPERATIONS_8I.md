# Packet 8I — real timer, notification, and quiet-hours qualification (operator runbook)

Status: **IMPLEMENTED** — hermetic qualification is safe to run now. LIVE steps
(one real desktop popup, a read-only systemd journal excerpt) are **opt-in** and
require `VOICE_BRIDGE_QUALIFY=1`; this packet never enables the timer, alters a
live unit, restarts a service, reads private message content, or changes the
production monitor state on its own.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8I, Packet 8I prompt-pack
prompt, and `docs/OPERATIONS_8D.md`.

## 1. What this packet qualifies

It qualifies the existing `openclaw-communications-monitor` user-systemd
oneshot + hourly timer and its notification path — and closes the four known
configuration risks:

| 8I requirement | Where it is proven |
|---|---|
| Exact unit names `openclaw-communications-monitor.{service,timer}` | `--units` (`exact_names`) |
| Enable / disable / rollback (stop+disable, state preserved) | `--units`, `monitor rollback`, `tests/test_timer_qualify.py` (`TestRollback`) + `tests/test_packet8d.py` |
| Overlap lease: acquire / renew / release / second-owner denial | `--lease` |
| Lease MONOTONIC, renewable, reboot-aware, exceeds 300 s timeout + 180 s margin (default 480 s) | `--lease`, `--preflight` (`lease`), config default `lease_ttl_s: 480.0` |
| Clock jump / suspend-resume lease immunity | `tests/test_timer_qualify.py` (monotonic lease) |
| Bounded runtime/resource limits (oneshot, MemoryMax, TimeoutStartSec, no restart loop) | `--units` (`oneshot`, `bounded_resources`, `no_restart_loop`) |
| No-change silence | `--notify` (`baseline_silent`) + `tests` |
| One controlled new-item notification | `--notify` (`one_new_item`) |
| Deduplication (no repeat popup) | `--notify` (`dedupe_no_repeat`) |
| Desktop popup delivery | `--notify --live` (real `notify-send`; opt-in) |
| Optional voice delivery + quiet hours | `--notify` (`quiet_hours_suppress`), `tests` |
| Acknowledgement (single transition) | `--notify` (`ack`) |
| Quiet-hours strict validation + IANA timezone | `--quiet-hours` + `tests` |
| `CONFIG_INVALID` fail-closed (suppress voice, no host-local fallback) | `--quiet-hours`, `--aggregate`, `tests` |
| Catch-up (Persistent once after downtime; no burst) | `--units` (`timer_persistent`), `tests` (`test_missed_run_catch_up_no_burst`) |
| Timezone/DST fold & gap | `--quiet-hours` (`dst_fold_gap`) |
| Journal evidence (typed event + optional read-only systemd journal) | `--journal` |
| Typed failure states (overlap, timeout, delivery, config, aggregate) | `--accelerated`, `--aggregate` |
| Truthful aggregate state/exit code | `--aggregate`; `run_hourly` `exit_code` |
| No report IDs/URLs/senders/subjects/bodies/tokens/cookies/secrets in the timer or notification surface | `--notify` (`redacted_copy`), `--journal` (`journal_redacted`) |
| No hardcoded `DISPLAY=:1`; validated X11/Wayland session resolution | `--units` (`no_hardcoded_display`), `--preflight` (`session`) |
| Retry / dead-letter bounds | `--notify` (`retry_bounds`), `tests` |
| Missed run / overlap / timeout / rollback / notification failure / malformed time config | `--accelerated` + `tests` |

## 2. The harness

- `voicebridge/communications/timer_qualify.py` — staged, bounded qualification.
- `scripts/timer_qualify.py` — CLI (`timer-qualify`).
- `tests/test_timer_qualify.py` — hermetic focused tests.
- `docs/OPERATIONS_8I.md` — this runbook.
- Temporary accelerated unit templates: `systemd/openclaw-communications-monitor-accel.{service,timer}` (never installed automatically; see §9).

Output is machine-readable JSON with **typed** results. Live execution is
**impossible by default**: every live stage requires `--live` AND
`VOICE_BRIDGE_QUALIFY=1`, otherwise it fails closed (`LIVE_NOT_AUTHORIZED`).
The harness never enables the timer and never alters an installed unit.

## 3. Preflight (safe, read-only)

```bash
.venv/bin/python scripts/timer_qualify.py --preflight
```

Reports, in one JSON record:

- `time_config` — typed quiet-hours + IANA timezone state (`ok` or
  `config_invalid`). An invalid value means voice is suppressed until repaired
  and never falls back to host-local time.
- `session` — the validated X11/Wayland notification session
  (`resolve_notification_session`). A headless/ambiguous result is a typed
  `unavailable`, never a guessed `DISPLAY`.
- `lease` — the effective lease default vs the 300 s oneshot timeout and the
  480 s floor (`lease_exceeds_timeout`).
- `units` — READ-ONLY `systemctl --user is-enabled/is-active` for both units so
  the operator sees the live timer state (this packet does not change it).

## 4. Hermetic qualification (no live units, no browser)

```bash
.venv/bin/python scripts/timer_qualify.py --all
```

Runs every hermetic stage in an **isolated** qualification state dir (default
`<config state>/qualification-timer`; override with `--state-dir`):

1. `units` — exact unit names, template render, `Type=oneshot`, bounded
   resources, **no hardcoded `DISPLAY`**, `OnCalendar=hourly` +
   `Persistent=true`, and the temporary accelerated templates present;
2. `lease` — acquire / held / second-owner denied / renewable / renew-others
   denied / monotonic deadline recorded / reboot-stale recovery / release;
3. `quiet_hours` — strict `HH:MM-HH:MM` parsing (rejects `24:00`, `07:60`, free
   text), IANA validation, `CONFIG_INVALID` suppression, and a real
   `Europe/Bucharest` DST fold/gap stability check;
4. `notification` — baseline silence, one new item -> one desktop+dashboard
   set, rescan dedupe, acknowledgement, bounded retry/dead-letter, quiet-hours
   voice suppression, and redacted plain-language copy (delivery functions are
   simulated in the hermetic stage);
5. `aggregate` — clean run is `OK`/exit 0; degraded account is `degraded`/exit
   1; unknown platform is `failed`/exit 1; malformed time config is
   `CONFIG_INVALID`/exit 1 (never a top-level `OK`);
6. `journal` — the durable typed-event journal records the redacted
   `communications.report.ready` event for a new-item pass;
7. `accelerated` — a temporary isolated accelerated failure-path drill
   (overlap, malformed config, delivery failure -> dead-letter, bounded voice
   timeout, aggregate truthfulness).

`--dry-run` runs without persisting the summary. The production state root,
installed units, timer, browser, audio, and the user config are never touched
(`test_qualification_never_touches_production_state`).

## 5. Live qualification (OPT-IN)

Prerequisites:

- A logged-in graphical session (`--preflight` `session.state == "ok"`).
- Operator gate:
  ```bash
  VOICE_BRIDGE_QUALIFY=1
  ```

```bash
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/timer_qualify.py --notify --live
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/timer_qualify.py --journal --live
```

- `--notify --live` sends ONE controlled plain-language desktop popup through
  the real `notify-send` (never private content) and reports the typed delivery.
- `--journal --live` reads a bounded, read-only `journalctl --user -u
  openclaw-communications-monitor.service -n 20` excerpt (metadata only).

Observing the real hourly schedule remains an operator action on the LIVE timer
(see `docs/OPERATIONS_8D.md` §4): enable it with
`VOICE_BRIDGE_MONITOR_ENABLE=1 ... --install --enable`, then watch two real
hourly elapses. The accelerated unit in §9 can exercise failure paths but can
**never substitute** for that observation.

## 6. Known-config risks closed

1. **Quiet hours / timezone fail-open -> fail-closed.** `_parse_quiet_hours`
   now strictly validates `HH:MM-HH:MM` with 00–23 hours and 00–59 minutes, and
   the timezone must be a valid IANA zone (stdlib `zoneinfo`). Invalid input is
   `CONFIG_INVALID`: voice is suppressed and the aggregate degrades — it never
   falls back to host-local time and never disables quiet hours.
2. **Hardcoded `DISPLAY=:1` removed.** The repository service template ships no
   `Environment=DISPLAY=` value. `notify-send` resolves the real X11/Wayland
   session at runtime (`voicebridge/display.py::resolve_notification_session`);
   a headless/ambiguous session is a typed failure. If the user-systemd
   instance does not import the graphical session env, run once:
   `systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR
   DBUS_SESSION_BUS_ADDRESS`.
3. **Lease vs runtime.** The overlap lease default is now 480 s (monotonic,
   renewable, reboot-aware via `boot_id`) against a 300 s `TimeoutStartSec` —
   a timer run can never outlive its own guard, a wall-clock jump cannot expire
   it, and a reboot never pins the monitor into `OVERLAP`.
4. **Truthful aggregate.** `run_hourly` now returns `degraded`/`failed` with a
   typed `code` and `exit_code = 1` whenever any account, required delivery, or
   the time config degrades; a clean run is `OK`/exit 0.

## 7. What is stored and where

- Default root: `<config state>/qualification-timer` (override with
  `--state-dir`). Dirs `0700`, files `0600`.
- `timer-qualify/<run_id>/summary.json` — the redacted typed qualification
  record (stage checks only). It never contains report IDs, URLs, senders,
  subjects, bodies, tokens, cookies, or secrets.
- `--dry-run` runs without persisting the summary.

## 8. Cleanup and rollback

- Remove the isolated qualification root to delete all artifacts:
  ```bash
  rm -rf "<config state>/qualification-timer"
  ```
- This packet changes **no** live service/timer/unit. Rollback = delete the
  added files (`voicebridge/communications/timer_qualify.py`,
  `scripts/timer_qualify.py`, `tests/test_timer_qualify.py`,
  `docs/OPERATIONS_8I.md`, the `*-accel.{service,timer}` templates) and revert
  the production edits (store v3 migration is additive `ALTER TABLE ... ADD
  COLUMN`; reverting the code leaves the extra columns inert).

## 9. Temporary accelerated failure-path unit (operator-gated)

The repository ships two templates that are **never installed automatically**:

```text
systemd/openclaw-communications-monitor-accel.service  (oneshot)
systemd/openclaw-communications-monitor-accel.timer    (OnCalendar=*:0/5)
```

The service runs `scripts/timer_qualify.py --accelerated` in an ISOLATED state
root (`VOICE_BRIDGE_QUALIFY_ACCEL_STATE`); the timer fires every 5 minutes so
the operator can watch the failure-path drill quickly. To run the drill:

```bash
# explicit operator action; then REMOVE it afterwards
systemctl --user enable --now openclaw-communications-monitor-accel.timer
journalctl --user -u openclaw-communications-monitor-accel.service --no-pager
systemctl --user disable --now openclaw-communications-monitor-accel.timer
systemctl --user reset-failed openclaw-communications-monitor-accel.service
```

This drill exercises overlap, malformed time config, delivery-failure
dead-letter, timeout, and aggregate truthfulness **in isolation**. It is NOT
the production hourly schedule and can never substitute for observing two real
hourly elapses of `openclaw-communications-monitor.timer`.

## 10. Limitations (honest)

- The hermetic `--notify` stage simulates delivery functions; a REAL desktop
  popup requires the opt-in `--notify --live` step (or observing the live
  hourly timer).
- The hermetic `--journal` stage qualifies the typed-event journal; the
  read-only systemd journal excerpt is the opt-in `--journal --live` step.
- Clock-jump and suspend/resume lease immunity are proven in
  `tests/test_timer_qualify.py` (controllable clocks); the real suspend/resume
  behaviour of the live timer is observed through the two-hourly live gate.
- The aggregate exit code makes a degraded hourly run exit non-zero — this is
  the intended truthful signal for the oneshot's journal, not a false failure.