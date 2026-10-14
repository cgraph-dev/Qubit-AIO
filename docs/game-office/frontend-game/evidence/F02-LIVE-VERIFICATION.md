# F02/F03 — live verification against the running dashboard

**Verified 2026-10-14 12:11** against `http://127.0.0.1:8890/qubit-office` on a freshly
restarted `qubit-voicebridge.service` (MainPID 281483), with the repaired
`qubit-office.bundle.js`. Raw results: `f02-live-result.json`, `f02-live-click.json`,
screenshot `f02-live.png`.

## The three recorded failures, before and after

`probe.json` (the pre-repair audit) and `f02-live-result.json` (this run) were captured with the
same instrumentation — canvas `addEventListener` interception, then pixel-hash comparison of the
canvas before/after each gesture.

| Probe | Before (`probe.json`) | After (`f02-live-result.json`) |
|---|---|---|
| `canvas_event_listeners` | `[]` — **zero** | `pointerdown, pointermove, pointerup, pointercancel, wheel, click` |
| `wheel_changed_pixels` | `false` | **`true`** |
| `drag_changed_pixels` | `false` | **`true`** |
| page errors | none | none |
| failed requests | none | none |
| tower payloads captured | — | 11 |

## Fit / Reset (finding 2)

| Check | Result | Reading |
|---|---|---|
| `zoom_then_fit_changed` | `true` | Fit genuinely re-frames after Zoom — the `_auto`/`_autoZoom` mismatch is gone |
| `fit_then_reset_changed` | `false` | **Correct, not a failure.** Fit and Reset both return to the canonical framing, so they must produce identical pixels |
| `reset_returns_to_fit` | `true` | Reset lands exactly on the fitted view |
| `fit_is_idempotent` | `true` | Fit twice is a no-op the second time |

Under the old code, `fitRef.current` was overwritten with the live camera on every redraw, so Reset
could never return to the canonical view — `reset_returns_to_fit` would have compared a
user-panned camera against the fit and failed.

## Selection and inspector freshness (finding 3)

`f02-live-click.json`: clicking a figure at 25%/70% of the canvas selected **`ceo`**, and the panel
rendered its real fields —

```
ceo
ceo · dept 1
state: idle · at desk
last event: run_unpriced · 165547s ago
```

Then, after waiting for ≥2 polls (1400 ms):

```
last event: run_unpriced · 165548s ago
```

**The age advanced.** That is the proof that the inspector derives from the current snapshot rather
than the payload object it was clicked in — under the old code this number would have frozen at
`165547s` forever, which was the reported staleness.

The earlier `click_changed_panel: false` in `f02-live-result.json` was a click at the **canvas
centre**, which is empty space. Selecting and viewing nothing is correct there; the panel text was
already the "Click a character for its state." instruction.

## F03 — semantic state reaches the sprite

`opts.state` is now threaded into `drawAgentSprite`. The atlas loader's own comment
(`sprites.js:938-944`) states that without it the loader derives the state from `walking` alone and
returns exactly two reachable poses. This run does not independently re-derive the atlas pose IDs —
the assertion for that is `tests/test_office_interaction_lifecycle.py` plus the existing sprite
suites, not this probe. **Stated plainly because it is the one F03 claim not proven by pixels here.**

## Reproduce

```sh
.venv/bin/python <scratch>/f02_live_probe.py     # listeners, wheel, drag, fit/reset
.venv/bin/python <scratch>/f02_click_probe.py    # selection + freshness across a poll
```

Both are hermetic read-only browser probes: they gesture, hash pixels and read text. They issue no
mutation, start no agent run and write nothing to the store — so they also serve as a C4/C7 check on
the live path.
