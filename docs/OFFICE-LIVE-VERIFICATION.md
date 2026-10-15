# Habblaud-in-Qubit-Office: live verification (this session)

Investigator: Qubit Agent. Date: 2026-10-15. HEAD at start: `fabc0caf3` (dirty),
checkpoint `c3b005f18`.

Everything below was **run**, and the output is the evidence. "VERIFIED" means
the exact command was executed this session.

## The headline

**The runtime works; the office test suite is RED.** These are two different
claims and both are true — the first draft of this doc measured only the runtime
and would have let a red suite pass unnoticed. Reproduced 3x:

```
.venv/bin/python -m pytest tests/test_office_game_*.py -q
-> 3 failed, 209 passed in 120.54s
```

See "The red suite" below.

## The runtime

The prior session's blocker (`/api/stream` 401 from the
iframe's `EventSource`) is already fixed, and the real Habblaud client renders
and connects inside `http://127.0.0.1:8890/qubit-office`.

## Evidence

| # | Claim | State | Evidence |
|---|-------|-------|----------|
| 1 | The vendored Habblaud client mounts in an iframe on the default `/qubit-office` route | VERIFIED | CDP probe: `frameCount: 1`, `frameRect 1305x782`, frame src `/plugin-apps/qubit-office/habblaud/`, `data-state=ready` |
| 2 | Its `EventSource` opens and the stream delivers | VERIFIED | CDP `Network.*`: `REQ GET /api/stream?token=…` → `RES 200 text/event-stream` |
| 3 | The UI reflects that stream | VERIFIED | frame `bodyText`: `"Habblaud Connected 10 agents 0 working 0 need you"` + real rooms `boardroom / desk-a / desk-b / review` + real activity rows |
| 4 | The canvas actually paints | VERIFIED | `non_background_pixels 102180`, 162 tones, `console_errors: []` |
| 5 | "Connecting…" is a cold start, not a stall | VERIFIED | time-sampled probe: `0.83s "Connecting… 0 agents"` → `1.10s "Connected 10 agents"` (~0.3 s) |
| 6 | The `?shell=1` fallback still works (the rollback path) | VERIFIED | `render_office_screenshot.py --url '...?shell=1'` → `data_state ready`, **843450** painted pixels (87.8%), `console_errors: []`, text `Qubit Office / PLACE Outside the building` |
| 7 | Mobile viewport (390x844, mobile emulation) | VERIFIED (renders, no overflow) | frame `368x742`, canvas `368x742`, `iframeOverflow 0`, `topOverflow 0`, `console_errors: []` |
| 8 | The three `entry` declaration sites agree | VERIFIED | `manifest.json`, `qubit-dist/plugins/fleet.json`, `qubit-web/public/plugins/fleet.json` all say `"entry": "qubit-office.game.js"`, and the SERVED `fleet.json` matches |
| 9 | The vendored tree is fresh against its own record | VERIFIED | `scripts/vendor_habblaud.py --check` → `ok: 77 files, 2147637 bytes, commit 4ccce2537472` (exit 0) |

## Defects found (real, both pre-existing — neither is a port regression)

### D1 — `ceo: undefined` in the mobile activity feed
On a 390x844 mobile viewport the frame text reads
`Activity ceo: undefined ceo started a turn …`.

**Not a backend defect.** The SSE payload is correct — dumping the live feed
shows `agentName` values `wren` / `lead-content` / `ceo`, never empty:

```
'wren'         | 'desk-a'   | '4' | wren used a tool
'lead-content' | 'boardroom'| '2' | lead-content claimed a task …
'ceo'          | 'boardroom'| '1' | ceo started a turn
```

So the `undefined` is introduced by the **vendored client's own mobile layout
path**, inside the minified bundle. It is an upstream client bug, not something
the port introduced. `voicebridge/office_stream.py:336` correctly falls back
`character.get("slug") or agent_id or ""`.

Not fixed this session: it is inside a generated artifact (`bundle/app-*.js`,
minified) that is covered by the vendor transform + `PROVENANCE.json` hash
record. Hand-patching it would invalidate the record. The correct repair is a
transform step in `scripts/vendor_habblaud.py` (like the existing
`openStream(` rewrite) plus a rebuild + provenance regen.

### D2 — the vendored UI ships pt-BR copy
`<html lang="pt-BR">`, and the served document carries Portuguese strings
(`"Escritório virtual em pixel art…"`, `"Conectando…"` when disconnected).
Owner ruling (docs/IDENTITY.md) is English-authoring for docs **and the
product**, new work ships English-only.

Not fixed this session: same reason as D1 — it is inside the minified bundle and
the locale chunks (`en-*.js`, `es-*.js`, `fr-*.js` are already present, so an
English locale chunk exists and the app appears to support locale selection).
The honest next step is to find the client's locale default and set it to `en`
in the injected config, rather than translating strings.

## The red suite (the real defect)

`pytest tests/test_office_game_*.py -q` → **3 failed, 209 passed**, reproduced 3x.

All three are in `tests/test_office_game_shell.py`, and all three are the **same
root cause: the tests encode the pre-rewrite contract while the code
deliberately moved ahead.** This is test drift, not a product defect — but it is
NOT benign, because a red suite hides the next real regression.

| Test | Old assertion | Why it now fails |
|------|---------------|------------------|
| `test_the_default_renderer_is_the_app_and_the_shell_is_opt_in` | `'has("app")' not in code` | `entry.ts:81` is now `params.has("shell") && !params.has("app")`. The entry's own comment (lines 55-78) documents `?app=1` as a **deliberate** compat alias for bookmarks from the first Habblaud preview; the app is still the unconditional default. The test forbids a token the code intentionally keeps. |
| `test_the_stream_is_bound_and_the_writes_are_gated_not_missing` | `perms.gated`, `msgs.gated` | `office-api.ts` was rewritten. It exports `bindingVerdict(): {bound, missing, gated, live}` and `ROUTES`, not the old per-client object. The old shape is `undefined`, so node throws `TypeError: Cannot read properties of undefined (reading 'gated')`. |
| `test_the_binding_summary_counts_the_new_truth` | `s.startswith("3 of 5 routes bound")` | Real output is now `"8 bound · 5 unavailable · 2 confirmation-gated"` — **more routes actually bound**, i.e. an improvement. The assertion pins the older, worse state. |

**This is the skill's own rule in the wild:** *"if an assertion would go RED
when the bug is FIXED, it is measuring the bug, not the code."* All three pin a
superseded state. The correct repair is to reconcile the tests to the real
contract while keeping each one's protected PROPERTY — not to delete assertions.

Delegated to a single agent with exclusive ownership of
`tests/test_office_game_shell.py`, with a falsification requirement (mutate a
scratch copy, confirm RED, restore). Result: `docs/SHELL-TEST-RECONCILIATION.md`.

## What this session did NOT verify

- Firefox rendering (Chrome-only; delegated).
- Whether the mobile `ceo: undefined` reproduces on every device profile.
- The `ceo: undefined` root cause inside the minified bundle.
- Any behavior outside `/qubit-office` (the rest of the office tab suite).
