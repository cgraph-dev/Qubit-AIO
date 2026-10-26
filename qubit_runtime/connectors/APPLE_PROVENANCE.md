# Apple connector pack — provenance

The Qubit Apple connector contracts in this directory were informed by
[open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright).

Reference sources reviewed read-only:

- `src/openjarvis/connectors/apple_calendar.py` — Apple epoch (2001-01-01 UTC),
  event field shape (summary/title, calendar, start/end, all-day, location,
  attendee presence), timezone/wall-clock notes, macOS Calendar database path.
- `src/openjarvis/connectors/apple_contacts.py` — contact field shape (name
  components, organization, job title, department, nickname, birthday, phones,
  emails), Apple-epoch timestamps and Apple label markup.
- `src/openjarvis/connectors/apple_health.py` — daily aggregate shape (steps,
  sleep, heart rate, active energy, workouts) and the HealthKit/export data
  sources.
- `src/openjarvis/connectors/apple_music.py` — library track shape (name,
  artist, album, duration, genre, play count, played date), AppleScript/Music.app
  approach and the "Music is not running" failure mode.

What was adapted: only the public field/API shape and the platform facts above.
**No OpenJarvis source, SQL, AppleScript, tests or helper functions are copied
into Qubit.**

Local divergence, by design:

- Qubit does not open the macOS databases or call `osascript` directly. Each
  connector takes an injected, read-only framework adapter; this keeps the Linux
  worktree from importing PyObjC or fabricating local data.
- On any non-`darwin` host every operation raises a typed `not_supported`
  refusal naming the host platform and the required macOS framework, before the
  adapter is touched.
- Results are bounded and error details are redacted; missing health metrics
  stay `None` instead of being invented as zero.
- There is no create/update/delete/play/send path in any connector, and the
  tool-definition module performs no global registration.

Qubit tests for these contracts are in
`qubit_runtime/tests/connectors/test_apple_common.py`,
`test_apple_calendar.py`, `test_apple_contacts.py`, `test_apple_health.py`,
`test_apple_music.py` and `test_apple_connectors_tool.py`.
