# Qubit surface reachability audit (8U)

Status: **AUDITED — one cross-boundary dead route (repaired), four dead voice
slots, one unreachable production module, and 26 git-tracking gaps found and
evidenced; all other audited production surfaces are reachable.**
Agent: W1 (roles A + G). Frozen commit: `a4795b90` (+ uncommitted wave-1
repairs).
Evidence tests: `tests/test_surface_reachability8u.py` (13 passed) +
`tests/test_qbpv2_reach_deadcode.py` (7 passed) = **20 passed**.
Scope: dead/unreachable production surfaces in the Qubit-owned tree
(`voicebridge/`, `frontend/`, `scripts/`, `tests/`). The byte-identical
vendored fork (`qubit_hermes/`) carries upstream code only and is covered by
the Task 1 provenance manifest, not re-audited here.

This document is append-only evidence. Findings that require edits outside
Agent 1's ownership are recorded as **cross-boundary handoffs** with the exact
path, symbol, caller, and required change; the owning agent integrates them.

---

## Findings

### R1 — Dead voice dispatch slots (`voicebridge/voice_commands.py`)

`_BACKENDS` maps three handlers that no `VoiceCommand` in either registry
(`REGISTRY` or `PLUGIN_FLEET_REGISTRY`, merged by `_iter_entries()`) ever
dispatches. `execute()` and `confirm_pending()` look up
`_BACKENDS.get(entry.backend)`; with no entry naming these backends, the three
handlers are unreachable through voice.

| Backend key | Mapping | Handler | Handler definition | Evidence |
|---|---|---|---|---|
| `model` | `voice_commands.py:3699` | `_b_model` | `voice_commands.py:1334` | no entry backend == `model` |
| `setup_run_preview` | `voice_commands.py:3681` | `_b_setup_run_preview` | `voice_commands.py:455` | no entry backend == `setup_run_preview` |
| `vision_guidance` | `voice_commands.py:3704` | `_b_vision_guidance` | `voice_commands.py:1929` | no entry backend == `vision_guidance` |

Runtime introspection (494 entries across both registries):
`set(_BACKENDS) - {e.backend for e in _iter_entries()} ==
{model, setup_run_preview, vision_guidance}`.

- **Reachable?** No, through voice. The functions are also not called anywhere
  else in `voicebridge/` or `tests/` (verified by reference scan), so the
  handlers are dead code.
- **Resolution:** documented (evidence-backed). The dispatch table is the
  authoritative wiring; an unmapped `_BACKENDS` key is a latent
  "not wired yet" path, not a product capability.
- **Cross-boundary handoff (owner: Agent 4, `voicebridge/voice_commands.py`):**
  either (a) register the intended voice entries so the three backends are
  reachable, or (b) delete the three dead `_BACKENDS` entries and handler
  functions. Recommended (b) unless a voice command is intended:
  - delete lines `3681`, `3699`, `3704` from `_BACKENDS`;
  - delete `_b_model` (`1334`), `_b_setup_run_preview` (`455`),
    `_b_vision_guidance` (`1929`);
  - re-run `tests/test_surface_reachability8u.py` (the pinned dead-set
    assertion flips to `[]` once removed; update `DEAD_VOICE_BACKENDS`).

### R2 — Dead voice summary handler (`voicebridge/voice_commands.py`)

`_SUMMARIES["vision"] = _sum_vision` (`voice_commands.py:4183`, function at
`3799`) is never selected: no entry has `summary == "vision"`. `summarize()`
falls back to `_generic_summary`.

- **Reachable?** No.
- **Resolution:** documented.
- **Cross-boundary handoff (owner: Agent 4):** delete the `"vision"` key at
  `voice_commands.py:4183` and `_sum_vision` at `3799`, or set
  `summary="vision"` on a vision entry if richer speech was intended.

### R3 — Shadowed compat route `GET /api/sessions/empty` (`voicebridge/hermes_compat.py`)

The generic session-show route is registered early:

- `voicebridge/hermes_compat.py:3979` —
  `_r("GET", r"/api/sessions/(?!shares$)(?P<id>[^/]+)", "h_session_show")`

The intended empty-sessions read is registered **396 lines later**:

- `voicebridge/hermes_compat.py:4375` —
  `("GET", r"/api/sessions/empty.*", "sess_empty")`, handler
  `h_sess_empty` at `4505`.

Because routes are matched in order and the generic `[^/]+` pattern accepts the
literal `empty`, a bare `GET /api/sessions/empty` is captured by
`h_session_show` and returns `404 SESSION_NOT_FOUND`. Observed runtime:

```
GET /api/sessions/empty       -> 404 {"code": "SESSION_NOT_FOUND", "detail": "no session 'empty'"}
GET /api/sessions/empty/sub   -> 200 {"ok": true, "count": 0, "sessions": []}
GET /api/sessions/empty/count -> 200 {"ok": true, "count": 0, ...}   # works (extra path segment)
```

The frontend's live caller uses `GET /api/sessions/empty/count`
(`frontend/qubit-web/src/lib/api.ts:417`, `SessionsPage.tsx:844`), which still
reaches `h_sess_empty`; `deleteEmptySessions` uses `DELETE /api/sessions/empty`
(`api.ts:421`), which is correctly ordered above the generic delete route
(`hermes_compat.py:4366-4371`). So the impact today is latent: the bare
`GET /api/sessions/empty` branch of the `empty.*` pattern is dead, and any
future caller of it would get a misleading 404 instead of the real read.

- **Reachable?** Partially — only the multi-segment form.
- **Resolution:** documented + cross-boundary handoff.
- **Cross-boundary handoff (owner: Agent 2, `voicebridge/hermes_compat.py`):**
  move the `GET /api/sessions/empty.*` registration above the generic
  `GET /api/sessions/(?!shares$)(?P<id>[^/]+)` route (mirroring the explicit
  DELETE ordering comment at lines 4366-4371):

  ```diff
  @@ voicebridge/hermes_compat.py
  -_r("GET", r"/api/sessions/(?!shares$)(?P<id>[^/]+)", "h_session_show")
  +# GET /api/sessions/empty[*] is the pinned "read empty sessions" endpoint,
  +# not a session named ``empty``; it must precede the generic id pattern.
  +_r("GET", r"/api/sessions/empty.*", "sess_empty")
  +_r("GET", r"/api/sessions/(?!shares$)(?P<id>[^/]+)", "h_session_show")
  ```
  and remove the later duplicate registration at line `4375`.
  `tests/test_surface_reachability8u.py::test_compat_bare_get_sessions_empty_is_reachable`
  is an `xfail` that turns into an `xpass` when this lands.

- **RESOLVED by the orchestrator integration wave (2026-09-13):** the
  `GET /api/sessions/empty.*` registration was moved above the generic
  session-id pattern in `voicebridge/hermes_compat.py`; the duplicate late
  registration was removed. The reachability test is now a plain assertion
  (no `xfail`) and passes. In the same wave the stale companion assertion in
  `tests/test_hermes_plugins.py` was corrected so
  `PUT /api/plugins/kanban/orchestration` is checked against the shared
  `KANBAN_WRITE_BROKERED` typed gate (it previously accepted a bare 404 for a
  mutating route). `DELETE /api/sessions/empty` is now the typed
  `SESSIONS_RECEIPT_STORE` bulk-sweep gate (ledger
  `api:DELETE /api/sessions/empty` IMPLEMENTED → SAFE_EQUIVALENT).

### R4 — Unexplained byte deviation: 13 EOL-normalized fork files (Task 1)

`qubit_hermes/` is byte-identical to the pinned checkout and differs from 13
upstream **git blobs** only by line endings, because upstream `.gitattributes`
pins `*.ps1 text eol=crlf` (index stores LF, checkout writes CRLF). Recorded in
`docs/QUBIT_HERMES_VENDOR_MANIFEST.json` under `eol_normalized` with upstream
blob object hash, blob sha256, worktree sha256, local sha256, provenance, and
this test file as the regression. No Qubit production caller invokes these
Windows installer/CI scripts.

- **Resolution:** documented (not a Qubit patch; the fork matches the pinned
  checkout). `UNCHANGED=12160`, `EOL_NORMALIZED=13`,
  `MISSING=0`, `QUBIT_PATCHED=0`, `UNEXPLAINED=0`.

### R5 — Unreachable production module: `voicebridge/hermes_backend.py` (W2)

A full AST import-graph scan over `voicebridge/`, `tests/`, and `scripts/`
(absolute imports, relative imports, `import_module("voicebridge.…")` literals,
and quoted module-name literals) finds exactly one module with no inbound
reference: `voicebridge/hermes_backend.py` (34 KB, 0 importers). Its own
docstring calls it the "single programmatic entry point" for the vendored
FastAPI backend, but `voicebridge/hermes_runtime.py`,
`voicebridge/webui.py`, and the CLI never import it — only the unrelated
`webui.hermes_backend` config flag shares the name.

- **Reachable?** No production path imports it. `command -v` none; the module
  is importable but dead.
- **Resolution:** documented + cross-boundary handoff.
- **Cross-boundary handoff (owner: W2, `voicebridge/hermes_backend.py`):**
  either (a) wire `hermes_backend.app()` / `hermes_backend.serve()` into the
  real backend start path (`voicebridge/hermes_asgi.py` or the CLI
  `qubit agent serve|dashboard`), with a live proving test, or (b) remove the
  module if `hermes_runtime` already provides the same entry point.
  `tests/test_qbpv2_reach_deadcode.py::test_no_unreachable_voicebridge_modules`
  pins the module in `KNOWN_UNREFERENCED_MODULES`; once wired/removed, the
  pinned set flips to `set()`.

### R5b — 26 upstream files present on disk but absent from the git index (byte-parity)

`git ls-tree -r HEAD -- qubit_hermes` tracks 12,147 of the pinned tree's
12,173 blobs; 26 upstream-tracked files exist on disk byte-identically but are
excluded from the local index by the **vendored upstream `.gitignore`** (they
were not force-added). A fresh clone therefore lacks them even though the
working tree is byte-complete. Reconstructed exactly with `git check-ignore
-v` and recorded in `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` under
`git_index_gaps` (each row carries `upstream_blob`, `on_disk_sha256`,
`ignored_by`, reason, and this audit's regression test):

| `.gitignore` rule | files | examples |
|---|---|---|
| `examples/` | 16 | `optional-skills/creative/concept-diagrams/examples/*.md` |
| `web/public/fonts/` | 7 | `web/public/fonts/Rules*.woff2` |
| `export*` | 2 | `skills/creative/p5js/scripts/export-frames.js` |
| `apps/desktop/src/**/*.js` | 1 | `apps/desktop/src/plugins/hello-runtime/plugin.runtime.js` |
| `data/` | 1 | `website/src/data/userStories.json` |

All 26 are content-identical to their upstream blobs (0 content diffs). The
only mode difference is a consequence: `export-frames.js` is executable
upstream (`100755`) and ignored locally, so the tracked tree shows 69 vs 70
executables; the on-disk mode matches upstream. No symlink exists in either
tree.

- **Resolution:** documented + cross-boundary handoff.
- **Cross-boundary handoff (owner: orchestrator, vendor integration):**
  `git add -f` the 26 paths (or add negations in a **non-vendored** ignore
  file) before the final commit so the fork is reproducible from a fresh
  clone. Do **not** edit `qubit_hermes/.gitignore` — it is upstream source and
  must stay byte-identical. After the force-add,
  `local_git_file_count` becomes 12,173 and `git_index_gaps` becomes `[]`;
  `tests/test_surface_reachability8u.py::test_vendor_manifest_git_index_gaps_are_byte_identical`
  accepts either 0 or 26 gaps (all-or-nothing).

### R6 — Reachability categories verified clean (no fix needed)

| Category | Finding | Test |
|---|---|---|
| Fake/missing compat handlers | 0 — every non-gated route resolves to a callable `HANDLERS` entry; every gated route carries a typed `gate.code` | `test_every_compat_route_has_a_real_handler_or_typed_gate` |
| Compat patterns | all 380 patterns compile; methods are known | `test_compat_route_patterns_compile_and_methods_are_known` |
| Unused/orphaned overlay plugins | 0 — 10 disk dirs == 10 registered manifests, each entry bundle present | `test_every_overlay_plugin_dir_is_registered_and_loadable` |
| Static fleet placeholders | 0 — both fleet entries resolve to real `entry`/`css` bundles with a tab path | `test_static_fleet_plugins_resolve_to_real_bundles` |
| Dangling CLI registrations | 0 — every `_OP_CLI_INDEX`/`_INTEGRATION` key maps to a real parser command; every `_INTEGRATION` value is callable | `test_every_cli_registry_key_maps_to_a_real_parser_command` |
| Unmounted pages / stale redirect aliases | 0 (existing) | `test_every_frontend_page_component_is_lazy_imported_and_mounted`, `test_every_webui_route_alias_resolves_to_a_real_route` |
| Artifact-only tests | exactly the documented 11; new unflagged production-dead tests fail | `test_production_dead_tests_are_exactly_documented` |
| No-op buttons / placeholder data | 0 — no empty `onClick={() => {}}` / `onClick={() => null}` handlers; "placeholder" hits are legitimate input-field placeholders | manual scan (documented limit) |

---

## Positive reachability verifications (regression-guarded)

| Surface | Result | Assertion |
|---|---|---|
| Voice registry entries | 494 entries; **every** `backend` resolves to a `_BACKENDS` handler; no empty backend | `test_every_voice_entry_reaches_a_registered_backend` |
| Voice ids/aliases | no duplicate ids; no alias collisions | `test_voice_ids_and_aliases_are_unique` |
| CLI parser | all 106 top-level subcommands dispatched by `_INTEGRATION_DESTS`/`_OP_CLI_INDEX`/`_INTEGRATION`, a native `command ==` handler, or `cli_hermes_forward.route()` | `test_every_cli_parser_command_is_dispatched` |
| Dashboard pages | all 19 `pages/*.tsx` are lazy-imported and present in `BUILTIN_ROUTES_CORE`/chat host | `test_every_frontend_page_component_is_lazy_imported_and_mounted` |
| webui route aliases | all 32 `HERMES_ROUTE_ALIASES` targets resolve to a built-in route, `/chat`, or an overlay plugin tab | `test_every_webui_route_alias_resolves_to_a_real_route` |
| compat route table | no same-`(method,pattern)` duplicates (42 repeated patterns are all distinct methods) | `test_compat_has_no_same_method_duplicate_route_patterns` |
| voice modules | all `voicebridge/**` modules have an inbound import/`import_module`/literal reference except the one documented dead module (R5) | `test_no_unreachable_voicebridge_modules` |
| vendored fork modes/symlinks | 12,173 files; on-disk modes reproduce upstream (`100644` ×12103, `100755` ×70), 0 symlinks, 0 mode/symlink divergences | `test_vendor_manifest_records_modes_and_symlinks` |
| vendored fork git tracking | 26 documented byte-identical git-index gaps (R5b); `tracking_blockers` equals the gap set | `test_vendor_manifest_git_index_gaps_are_byte_identical` |

## Not exhaustively audited (stated limits)

- **Tests that never call production code:** the 11 artifact-only test files
  are now pinned by
  `tests/test_qbpv2_reach_deadcode.py::test_production_dead_tests_are_exactly_documented`
  (inspection shows they assert repository artifacts — manifests, ledgers,
  scans — not mocked production behavior, so they are not "dead tests").
  `test_qbpv2_*` wave files are exempt while the wave is in flight.
- **Routes never called by production:** every compat route is reachable
  through `dispatch()` and every non-gated route has a real handler (R6);
  however a reverse "no frontend literal/ledger row references this route"
  audit was not made a hard gate because vendored frontend URLs are assembled
  dynamically (template literals), which produced an unreliable undercount.
  This remains a stated limit, not a silent pass.
- **`qubit_hermes/**` dead code:** the fork is byte-identical upstream; its
  internal dead code is upstream's and is intentionally not modified.
- **Frontend minified plugin bundles:** not AST-audited for no-op handlers
  (`frontend/*.js`, `frontend/qubit-app/plugins/*.js`); they have backend
  callers verified elsewhere.
- **`scripts/**`:** treated as manual entry points; not flagged as unreachable.

---

## Wave-2 resolution (2026-09-13, append-only)

The reachability findings above are now resolved, not merely pinned:

- **R1 — dead voice dispatch slots (RESOLVED).** The unreachable handlers
  `_b_model`, `_b_setup_run_preview`, `_b_vision_guidance` and their
  `_BACKENDS` entries were removed from `voicebridge/voice_commands.py`.
  `tests/test_surface_reachability8u.py` now pins `DEAD_VOICE_BACKENDS = []`, so
  a new unreachable dispatch slot fails the gate.
- **R2 — dead voice summary handler (RESOLVED).** `_sum_vision` and
  `_SUMMARIES["vision"]` were removed; `DEAD_VOICE_SUMMARIES = []`.
- **R3 — shadowed `GET /api/sessions/empty` (RESOLVED in wave 1).** Route
  reordered above the generic session-id pattern.
- **R4 — 13 EOL-normalized fork files (DOCUMENTED, not a defect).** Unchanged.
- **R5 — `voicebridge/hermes_backend.py` (RESOLVED).** It is now referenced by
  production code: `voicebridge/hermes_runtime.serve()` delegates to
  `hermes_backend.serve()`, so the programmatic server entry point is reachable
  from the runtime bootstrap. `KNOWN_UNREFERENCED_MODULES` is now empty.
- **R5b — 26 upstream files absent from the git index (RESOLVED).** All 26 were
  force-added; `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` now reports
  `git_index_gaps = 0` (the manifest measures tracked state from the index, so a
  force-added file counts as tracked and would survive a fresh clone).

---

## Wave-3 independent re-verification (2026-09-14, Agent 5, append-only)

Re-measured by fresh runtime introspection (not by trusting the prior counts).
No reachability surface needed a code change; the counts moved because the
wave-2 integration added surfaces. Source-derived numbers:

- **Voice:** 495 entries (both registries merged); 0 entries with an unwired or
  empty `backend`; dead `_BACKENDS` = `[]`; dead `_SUMMARIES` = `[]`; 0
  duplicate ids; 0 alias collisions.
- **CLI:** 106 top-level subcommands; 0 with no dispatch path
  (`_INTEGRATION_DESTS` / `_OP_CLI_INDEX` / `_INTEGRATION` / native handler /
  `cli_hermes_forward.route()`).
- **Pages:** 19 `pages/*.tsx` on disk, 19 lazy-imported, 0 unmounted, 0
  dangling; `BUILTIN_ROUTES_CORE` = 19 routes; 37 `HERMES_ROUTE_ALIASES`; 0
  stale aliases; 10 overlay plugin tabs all resolvable.
- **Compat routes:** 383 registered; 0 same-`(method, pattern)` duplicates
  (57 repeated patterns are distinct methods).
- **Modules:** 105 top-level `voicebridge/` modules; only `__main__` has no
  inbound import from another module — it is the interpreter entry point, not
  dead code. The documented `hermes_backend` dead module remains resolved.
- **Tests re-run (not the monolithic suite):**
  `tests/test_surface_reachability8u.py` + `tests/test_qbpv2_reach_deadcode.py`
  = 20 passed.

