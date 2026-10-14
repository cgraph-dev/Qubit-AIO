# Completion contract, verification and handoff

## Verdicts are per capability

Use `LIVE-PASS`, `PREVIEW-PASS`, `FAILED`, `BLOCKED-BACKEND`, `NOT-RUN`. A recording-driven scene can earn PREVIEW-PASS, never LIVE-PASS. A present module, zero console errors, an HTTP 200 or green static check alone never proves a game journey.

Full game **frontend integration** requires every mandatory journey below and the owner-approved contract amendment. Public release/parity/VERIFIED claims retain the repo's independent release gates; this set does not replace them.

## Mandatory behavioral journeys

| Gate | Observation that proves it | Evidence |
|---|---|---|
| G01 | Initial loading→ready binds drag, wheel, click; recovery/re-entry rebinds once | Listener count + input response + failure recovery trace |
| G02 | Zoom/pan→Fit/Reset returns actual canonical frame | Camera values/screenshots, not button text |
| G03 | Outside→enter building→lobby→elevator/stairs→floor→room→exit reaches correct places | Navigation IDs, input trace, arrival/exit screenshots |
| G04 | Two real buildings/floor sets navigate without entity mixing | Backend building IDs + A/B journey trace; fixture run labelled preview |
| G05 | Reload/deep link/Back and deleted target restore valid location | Route/state snapshots |
| G06 | Character click/roster/Go to selects stable ID; live inspector updates with revision | Changed character field readback and panel screenshot |
| G07 | Semantic work/meeting/unknown/idle states select correct sprites; no fake work | Event/source IDs + pose selection; idle/unknown screenshots |
| G08 | Real character-targeted conversation streams/finalizes once; reconnect restores canonical messages | Canonical session/message IDs; no duplicate or cross-recipient frames |
| G09 | Close dialog doesn't cancel work; explicit interrupt names/receipts actual effect | Request/run state readback |
| G10 | Settings scopes are visible; Apply/Cancel/Reset work; local graphics don't change org/profile | Before/after local state and unchanged protected backend state |
| G11 | Boardroom request shows evidence/options/recommendation; exact confirmed response persists | Request/action receipt + readback, e-stop/duplicate-click controls |
| G12 | Editor draft is not committed truth; refused/stale/no-confirmation edit changes no org data | Revision/state comparisons; accepted stage/readback/undo trace |
| G13 | Money is exact backend fold; absent measurement is unknown | Ledger-linked response plus UI formatting assertion |
| G14 | 390px/desktop/DPR2/text200%/keyboard/reduced-motion remain usable | Screenshot regions, focus/gesture/navigation results |
| G15 | Stale/network/asset/recipient/portal failures don't blank scene or invent success | Failure injection with explicit banners/reasons |
| G16 | Repeated scene mounts destroy listeners/renderer/subscriptions/textures | Resource count/cleanup evidence |
| G17 | Reported frame/load/memory/request budgets are measured against named scenario | Raw performance metrics + baseline comparison |
| G18 | Actual server serves selected entry and decoded assets; one route/component/React | Response URLs, hashes/content types, engine/register count |

## Existing baseline commands (can run now, read-only)

```sh
.venv/bin/python scripts/build_office_bundle.py --check
.venv/bin/python docs/game-office/frontend-game/evidence/probe.py
.venv/bin/python docs/game-office/frontend-game/evidence/controls.py
```

The probes capture the CURRENT baseline; they are diagnostic scripts, not pass gates for the unbuilt game. Output is under the runtime scratch `office-audit-live/`; existing checked-in evidence snapshots are historic evidence and are not overwritten by a probe.

```sh
curl -sS -o /dev/null -w '%{http_code}
' http://127.0.0.1:8890/qubit-office
curl -sS -o /dev/null -w '%{http_code}
' http://127.0.0.1:8890/dashboard-plugins/qubit-office/qubit-office.bundle.js
```

Page/JS 200 are reachability checks only. Authenticated API verification must use the existing SDK/client; never print the token or secrets.

## Planned commands after implementing the named files

Do NOT report these as run/passed now. New test and harness files must be authored and included in the owning configs first. Existing Vitest includes `src/**/*.test.{ts,tsx}`, so test files live under that tree rather than an unconfigured plugin test folder.

```sh
npm --prefix frontend/qubit-web run test -- src/lib/office-game/navigation.test.ts src/lib/office-game/adapter.test.ts
npm --prefix frontend/qubit-web run test -- src/lib/office-game/input.test.ts src/lib/office-game/camera.test.ts src/lib/office-game/selection.test.ts
npm --prefix frontend/qubit-web run test -- src/lib/office-game/conversation.test.ts src/lib/office-game/requests.test.ts src/lib/office-game/editor.test.ts
npm --prefix frontend/qubit-web run typecheck
npm --prefix frontend/qubit-web run build
.venv/bin/python scripts/office_game_journey.py --base-url http://127.0.0.1:8890 --mode preview
.venv/bin/python scripts/office_game_journey.py --base-url http://127.0.0.1:8890 --mode live
```

`office_game_journey.py` and its flags are a **proposed harness contract**, not a shipped command. Its preview fixtures are clearly labelled; live mode must refuse to use fixture responses. Extend exact build/lint invocations only after packaging spike defines the compiler path. Broad matrix remains deferred until the implementation freeze/permission gate.

## Acceptance manifest schema (proposed documentation schema)

Each result records gate ID, verdict, input mode, timestamp, repository revision/worktree-diff fingerprint, active profile/recipient (redacted), browser/viewport/DPR, relevant domain IDs/revision, raw metrics, screenshot/artifact paths and reason for blocked/failed/not-run. Avoid self-referential hash totals; don't count a report as its own source evidence.

Measures: engine and listener counts, outstanding request count, bytes/requests per second, cold readiness, floor transition readiness, frame-time distribution, memory/texture trend and error/asset failures. Suggested 60Hz/high and 30Hz/low tiers are targets until measured; do not certify all hardware.

## Findings to packets

| Findings | Owning packet |
|---|---|
| A01–A03, A10–A11, A16 | F02 input/camera/selection; F06 context routing |
| A04, A21 | F03 semantic sprites and asset failure |
| A05, A09, A17–A18, A23 | F08 shell/settings/resize, F14 accessibility |
| A06–A08, A24 | F05 adapter and F15 profiling |
| A12–A14 | Owner geometry/route/event handoff + F11; don't rewrite backend here |
| A15, A19–A20 | F07/F09/F11/F12 complete navigation/character/operational surfaces |
| A22 | F10 art classification + F13 capability-aware editor |
| D01–D16 | F00 amendment/doc reconciliation; F16 final manifest truth |

## Highest-risk boundaries

1. Renderer change can preserve appearance while breaking hit tests/feet/depth. Test screen-space input and authoritative coordinates together.
2. “Game feel” can become fake work. Use separate clocks and labels; no client-inferred task success, dollar or grant.
3. Multi-building ownership is absent today. Backend contract readiness must remain visible, not papered over with invented live IDs.
4. A portrait conversation could become a second session store. Only canonical message/session/action IDs are permitted.
5. Capability furniture and decorative furniture look similar. Catalog, inspector and confirmation must explain the difference.
6. Shared host/fleet changes can affect other tabs. Keep modifications scoped; verify one owner for `/qubit-office` and no duplicate React.
7. An engine/art batch can outgrow the frontend before measurement. Build the smallest complete journey first, then expand.
8. Local source/license/provenance claims disagree. No new external art/source copied or publication-rights assertion made by this planning task.

## Builder may / may not

**May:** implement approved frontend decisions, improve visual hierarchy, add local view/preferences/navigation, adapt verified math behind tests, bind existing SDK services and owner-provided contracts, propose changes with evidence.

**May not:** rewrite backend stores/agent loops, add duplicate auth/chat/history, silently turn on workers, make paid API/asset calls, change other profiles/sessions, change frozen contracts, manufacture live data, remove tests to hide a failure, commit/push without request, claim independent verification from a self-review.

## Handoff checkpoint after each packet

Record changed paths, decisions used, commands actually run with real output, deferred gates, acceptance evidence and owner-owned blockers. Never replace this with “implemented”. Local fixture proof and live service proof stay separate.

This plan was authored/reviewed sequentially by one worker, as requested. Its mechanical coherence and baseline probes are verified below; it has not received independent implementation review. Before execution, owner approves amendment/engine/defaults. At final freeze, independent review may be performed serially or by a human; no automatic agent fan-out is authorized.
