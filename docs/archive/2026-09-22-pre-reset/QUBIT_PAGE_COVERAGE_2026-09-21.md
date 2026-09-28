# Qubit Page/Control Coverage — 2026-09-21

**Artifact:** `docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` (machine-readable, schema `qubit-page-coverage/1`)
**Generator:** `scripts/page_coverage.py` (in-repo, deterministic, no LLM/browser).
`python scripts/page_coverage.py` writes the artifact; `--check` renders in
memory and exits non-zero on drift; `--out <path>` writes elsewhere. Two
consecutive runs at one revision are byte-identical (verified 2026-09-21:
sha256 `0822a665130fd1d499281c6b21a67174af9ad0366a4bd25a9c3778fac1f2079d`).
**Revision:** `f984911c81e119fe6fe1220510b8ac324c1224a4`
**Status:** source-level inventory. **No browser/E2E verification was run.**
This document does not claim completion, 100%, 1:1, or VERIFIED.

## Method

Static extraction from the current worktree at the revision above:

- `frontend/qubit-web/src/App.tsx` — built-in routes, sidebar nav, plugin-tab merge.
- `frontend/qubit-web/src/pages/*.tsx` — JSX control occurrences (tag + file:line).
- `frontend/qubit-app/plugins/*/manifest.json` and `frontend/qubit-web/public/plugins/fleet.json`.
- `voicebridge/qubit_compat.py` `ROUTES` — 411 backend API operations (337 implemented, 73 typed-refusal gates, 1 stream).
- `voicebridge/dashboard_ops.py` `catalog()` — 592 canonical registry operations.
- `voicebridge/voice_commands.py` — 535 voice intents across `REGISTRY`, `PLUGIN_FLEET_REGISTRY`, `MODEL_PROFILE_REGISTRY`.
- `voicebridge/cli.py` `build_parser()` — 777 CLI subcommands.
- `docs/archive/2026-09-21/HERMES_FRONTEND_MAP.json` and `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`.

Disposition rules: a row is `OPEN` until an end-to-end chain is observed
(input → auth → canonical op → policy/broker/e-stop → runtime → persistent
state → result → error/retry/cancel/recovery/audit). `SAFE_EQUIVALENT` is used
only for typed-refusal gates whose counterpart names an in-tree canonical
surface; the counterpart itself is not E2E-verified here. **No control is marked
`IMPLEMENTED`.**

## Counts

Regenerated 2026-09-21 by `scripts/page_coverage.py`; the previous hand-merged
artifact (3357 rows, 3288 OPEN, stale `latest_main`/`latest_release`) is
superseded. The generator reads the live OpenClaw manifest, so the OpenClaw rows
now carry the manifest's current dispositions rather than the stale 433-OPEN
snapshot.

| Disposition | Rows |
|---|---|
| OPEN | 3199 |
| SAFE_EQUIVALENT | 156 |
| IMPLEMENTED | 0 |
| **Total** | **3355** |

| Kind | Rows |
|---|---|
| page_area | 28 |
| client_route | 34 |
| sidebar_link | 30 |
| breadcrumb | 1 |
| known_gap | 4 |
| control | 509 |
| api_operation | 411 |
| openclaw_gateway_method | 434 |
| canonical_operation | 592 |
| cli_command | 777 |
| voice_intent | 535 |

Per-area OPEN counts are in `summary.per_area_open_counts` of the JSON. The
Gateway area dominates because the generator records an OpenClaw
contractual-pin gateway method row `SAFE_EQUIVALENT` only when the manifest says
SAFE_EQUIVALENT, else `OPEN` (it never propagates IMPLEMENTED), pending
real-backend E2E. The regenerated manifest now disposes 434/434 (252
IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY, 0 OPEN), so the OpenClaw rows
contribute 87 SAFE_EQUIVALENT and 347 OPEN source-only rows.

## Top gaps

1. **OpenClaw contractual-pin methods are code-side disposed 434/434** (252
   IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY, 0 OPEN) in the regenerated
   manifest; this artifact still records each OpenClaw row source-only (87
   SAFE_EQUIVALENT, 347 OPEN) pending real-backend E2E.
   (`QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`, `QUBIT_OPENCLAW_METHOD_INVENTORY.json`)
2. **`/api/portal` reads through the fork proxy**, not Qubit authority; no
   `qubit_compat.ROUTES` row. (`frontend/qubit-web/src/lib/api.ts:1885`)
3. **Documentation is an OpenAPI browser only** — not a full docs product.
   (`frontend/qubit-web/src/pages/DocsPage.tsx`)
4. **Agents is read-only** — only `GET /api/qubit/agents` is exposed.
   (`frontend/qubit-web/src/pages/AgentsPage.tsx`)
5. **Boards and Kanban are dual UIs** over one kanban store; vendored kanban
   writes are gated (`KANBAN_WRITE_BROKERED`), single-store authority unproven.
6. **No breadcrumb component exists** in the SPA.
7. **Dashboard controls** are all source-presence only; none is E2E-verified
   (the generator emits no `IMPLEMENTED` control row).
8. **Remote hub/OAuth/update paths** are typed refusals; counterparts are
   source-present but not E2E-verified (the 156 `SAFE_EQUIVALENT` rows: 69
   route-gate rows plus 87 OpenClaw rows the generator now classifies from the
   live manifest rather than the stale snapshot).

## Focused verification addendum (2026-09-21)

The chat-area `control` rows were re-extracted from the current worktree
(`frontend/qubit-web/src/pages/ChatPage.tsx`,
`frontend/qubit-web/src/pages/TerminalChatPage.tsx`) by the deterministic
extractor recorded in the session handoff; the previous rows carried stale
line numbers from an earlier revision. Two voice-surface rows were added
(Right-Ctrl push-to-talk; the voice status region). All 21 chat control rows
remain **OPEN** — focused component tests are not the real-backend E2E chain
the release gate requires.

Repairs recorded this session (evidence only; no row is closed):

- `/qubit-chat` voice status did not render the active engine, Right-Ctrl
  state, privacy/network consequence, or fallback reason (they were computed
  but unused). Now rendered in a `role="status"` region
  (`ChatPage.tsx:1141`), covered by
  `frontend/qubit-web/src/pages/ChatPage.voice9u.test.tsx`.
- The Right-Ctrl listener was mounted on every dashboard route because the
  ChatPage host is persistent; it is now gated on `isActive`
  (`ChatPage.tsx:997`).
- The `/chat` context guard's live probe treated the vendored resolver's
  generic 256K fallback as a real window, defeating
  `terminal.context_window_verification=required`. `_real_context_window`
  (`voicebridge/providers/resolver.py`) now reports that fallback as
  unverified; tests in `tests/test_chat_terminal_parity9u.py`.

Remaining OPEN after the 2026-09-21 regeneration: **3199** rows; no row is
marked IMPLEMENTED. (Earlier 3288/3247 figures came from the superseded
hand-merged artifact and the pre-Phase-2 manifest; the generator extraction is
509 controls vs the prior 511, and re-derives dispositions from the live
manifest.)

## How a verifier moves rows off OPEN

No row may be closed from source presence. A verifier must exercise the real
control against the real canonical backend and capture state + audit evidence.
Start the canonical server (it is the only Qubit authority) and drive the
built dashboard with Playwright:

```bash
# 1. build the dashboard once, then start the one canonical Qubit server
cd frontend/qubit-web && npm run build && cd -
qubit webui --port 8890          # default host 127.0.0.1; token in voice.yaml webui.token

# 2. in a second shell, run the real-WebUI Playwright walks that already exist
python -m pytest tests/test_ui_browser_8l.py \
                 tests/test_qubit_playwright8tf.py \
                 tests/test_qbpv2_frontend_chat_live.py -q
```

Those suites boot the real `voicebridge.webui.WebUI` against the built SPA and
render/navigate routes. They are a **starting point, not sufficient proof**:
each `control`/`api_operation` row still needs a targeted Playwright interaction
(click/fill/select/submit) plus a post-action assertion of persisted state, the
canonical audit/approval receipt, and the error/retry/cancel path. Record the
observed request/response, the store delta, and the audit id on the row, then
re-run the frozen final matrix. Do not mark a row `IMPLEMENTED` without that
chain and the independent read-only verifier required by
`docs/QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`.
