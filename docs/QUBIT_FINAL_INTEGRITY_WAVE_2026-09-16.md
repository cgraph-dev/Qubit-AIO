# Qubit final integrity wave — evidence report (2026-09-16)

Status: **append-only evidence**. This report records the code-side repairs made
in the final integrity/verification wave and the exact evidence for each. It
does not rewrite any frozen requirement, ledger row, or prior report, and it
does not reclassify any `OPERATOR_ONLY` row.

Authority: `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`,
`docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`,
`docs/QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`,
`docs/QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md`,
`docs/QUBIT_PLAN_INTEGRITY_AUDIT_2026-09-15.md`.

## Defects confirmed and repaired

| # | Defect (confirmed by independent audit) | Repair | Evidence |
| --- | --- | --- | --- |
| 1 | The non-streaming chat turn ignored the resolver-selected local model and still recorded it in the session meta/receipt (`chat_transport.py` `_generate` → `agent.turn` used `agent.model`). | `agent.turn` accepts an explicit `model` alias and executes/reports it; `chat_transport._generate` forwards `resolved_model`. | `voicebridge/agent.py:57-83,104-106`; `voicebridge/chat_transport.py:657-661,689-690`; `tests/test_final_integrity_fixes.py::test_agent_turn_uses_the_passed_model`, `::test_chat_transport_forwards_resolved_model`. |
| 2 | Capability flags falsely declared streaming for frontier/API models that use the request/response transport (`resolver.ModelProfile`, `catalog.ModelEntry`, `model_profiles_ops`, CLI create). | Streaming defaults are honest: local streams, external adapters do not unless explicitly declared; `require_streaming` now fails closed on the allowlist catalog. | `voicebridge/providers/resolver.py:110-115`; `voicebridge/providers/catalog.py:25-28,99-109`; `voicebridge/model_profiles_ops.py:171-173`; `voicebridge/cli.py:4707-4712`; `tests/test_final_integrity_fixes.py::test_streaming_capability_defaults_are_honest`, `::test_frontier_require_streaming_fails_closed`. |
| 3 | The tracked `Modelfile.qubit` baked private contact/account/location data (known `PRIV-02`, plan rule 29). | Personal facts removed; the file now points at the runtime context compiler. | `Modelfile.qubit`; `tests/test_final_integrity_fixes.py::test_tracked_modelfile_has_no_baked_pii`. History remediation is out of scope (no destructive rewrite). |
| 4 | Clean install could not build `qubit-brain4` from a fresh clone because `scripts/install.sh`/`bake_brain.py` depended on the gitignored `Modelfile.qubit4`. | Both fall back to the tracked sanitized fixture `tests/fixtures/modelfiles/qubit4.sanitized` when the local bake artifact is absent. | `scripts/install.sh:271-286`; `scripts/bake_brain.py:22-27`; `tests/test_final_integrity_fixes.py::test_installer_falls_back_to_tracked_sanitized_modelfile`, `::test_bake_brain_falls_back_to_tracked_fixture`. |
| 5 | The installer pulled a broken/retired model and mislabelled them. | The retired 3B alias is no longer pulled; the optional phi4 smart mode is opt-in (`QUBIT_OPTIONAL_PHI4=1`). | `scripts/install.sh:250-262`. |
| 6 | The OpenAI TTS adapter made an external network call with no emergency-stop/egress gate or audit. | `OpenAITTS`/`HybridTTS` take a canonical `external_speech_guard`; it blocks on latched emergency stop/global disable and on explicit consent deny, and audits each external call. | `voicebridge/tts/openai_tts.py:56-63,75-82`; `voicebridge/providers/speech.py:617-650`; `voicebridge/orchestrator.py:495-515`; `voicebridge/hermes_compat.py:2938-2956`; `tests/test_final_integrity_fixes.py::test_tts_egress_guard_blocks_before_network`, `::test_external_speech_guard_emergency_stop_and_consent`. |
| 7 | Voice tasks and scheduled jobs called `agent.turn` directly, bypassing the model/profile resolver and gateway. | They run through `chat_transport.turn`, which resolves the selected profile and dispatches local or gateway with a truthful receipt. | `voicebridge/voice_commands.py:2249-2275`; `voicebridge/schedules.py:879-919,945-950,1114-1118`; `tests/test_final_integrity_fixes.py::test_schedule_executor_uses_canonical_chat_transport`. |
| 8 | The dashboard “Open console” control opened a dead WebSocket (browser-logged 426) and the `/api/console` refusal text misdescribed the endpoint. | The modal preflights over HTTP and renders the honest typed reason + safe alternative without opening a dead socket; `/api/console` has an accurate Qubit-brokered refusal/counterpart. | `frontend/hermes-web/src/components/HermesConsoleModal.tsx:400-431`; `voicebridge/hermes_compat.py:4213-4227,4754`; rebuilt `frontend/hermes-dist`. |
| 9 | Verified unused imports in production modules. | Removed (`redact_secret_ref`, `log_event`, `resolve_input_device`, `uuid`, `os`, `Callable`, `StringIO`, `Optional`). | `voicebridge/providers/gateway.py`, `providers/policy.py`, `orchestrator.py`, `voice_commands.py`, `kanban.py`, `profiles.py`, `integration_status.py`, `feature_flags.py`. |
| 10 | `config/personal-knowledge.md` and `config/task-templates.yaml` were git-tracked with real operator PII (PRIV-01, plan rule 29). | Untracked + `.gitignore`d (local files preserved byte-for-byte); `install.sh` seeds the portable starters. | `git ls-files`; `.gitignore`; `scripts/install.sh:304-334`; `tests/test_final_integrity_fixes.py::test_personal_knowledge_is_not_tracked`, `::test_task_templates_is_not_tracked`. |
| 11 | The production local prompt and identity/greeting replies baked the operator name/handle; `open_project` patterns baked personal project names. | `SYSTEM_PROMPT` is generic (facts arrive as request-time context); identity resolves from the operator's local `- Name:` line via `_operator_name`; `open_project` is generic and only matches configured `project_roots`. | `voicebridge/local_llm.py:21-30`; `voicebridge/orchestrator.py:322-345,2170-2176,2344-2352`; `voicebridge/local/actions.py:559-566,156-161,667-673`; `tests/test_final_integrity_fixes.py::test_local_prompt_has_no_baked_personal_handle`, `::test_open_project_requires_a_configured_root`, `::test_production_modules_bake_no_operator_identity`. |
| 12 | `EdgeTTS` (an external network TTS adapter) bypassed the emergency-stop/egress gate. | `EdgeTTS` takes/honors `egress_guard` before any I/O; wired in both constructors like OpenAI TTS. | `voicebridge/tts/edge.py:21-26,34-40`; `voicebridge/orchestrator.py:484-491`; `voicebridge/hermes_compat.py:2927-2934`; `tests/test_final_integrity_fixes.py::test_edge_tts_egress_guard_blocks_before_network`. |
| 13 | `release.py::import_scan_installed` scanned the vendored runtime data tree and mislabelled a local vendored module, so the `release_qualify --install` dependency gate could never pass. | The `_data` package-data tree is skipped (separate pinned contract) and `toolsets` is recognized as a vendored local module. | `voicebridge/release.py:510-528`; `release_qualify --build --install` → 17/17 smoke checks (build OK). |

## Honest streaming statement (provider plan P3/P6)

Frontier/API text runs on a request/response transport (`frontier.py` sets
`"stream": False`); it does not stream. Qubit therefore advertises
`streaming: false` for external profiles by default and refuses a
`require_streaming` request against a non-streaming catalog entry with
`UNSUPPORTED_CAPABILITY` before any I/O. Local generation streams for real via
`local_llm.chat_local_stream`, and the dashboard/CLI/voice streaming envelope
labels the frontier turn `streaming: false` truthfully.

## Intentional compatibility boundaries (not dead code)

- `computer_use.act/snapshot/cua_browser_state/control_*` and
  `evaluation.logbook.capture_to/capture_series` are the backend for the
  hardware-gated desktop capabilities (`OC-13`, `OC-51`). Qubit deliberately
  keeps desktop control **DISABLED** and exposes only read-only probes until an
  operator provisions a pinned actuator, so these functions have no product
  control caller by design. They are exercised hermetically and are reachable
  once the operator supplies the actuator boundary. This is a documented
  boundary under plan correction #10, not an uncontrolled path.
- OpenClaw/Hermes names in `qubit claw` / fork-forward CLI help and in typed
  compat refusals are migration/developer records, already allowlisted by
  `tests/test_branding_parity_wave3.py:47-72` and
  `docs/QUBIT_REBRAND_ALLOWLIST.md`.

## Byte / provenance result

`scripts/hermes_vendor_manifest.py` (pinned Hermes `2237be35…`, OpenClaw
`3928bad9…`) regenerates deterministically: 12173/12173 vendored files
classified, `unexplained=0`, `missing=0`, `omitted=0`,
`git_index_gaps=0`, `mode_divergences=0`, `symlink_divergences=0`. The
regenerated per-file hashes include the Qubit overlay change to
`frontend/hermes-web/src/components/HermesConsoleModal.tsx`.

## Second wave (same date) — code-ready completion pass

A ten-agent read-only audit found further code-side defects; these were repaired
with focused tests. Summary (file:line evidence in the code and tests):

- **Autonomy is now a real tri-surface.** `voicebridge/autonomy_ops` admin is
  exposed via `qubit autonomy status|list|show|pause|resume|revoke|expire`
  (`voicebridge/cli.py` `_cmd_autonomy`), the voice registry
  (`voicebridge/voice_commands.py` `autonomy_*` entries) and the dashboard
  (Tier-1 `dashboard_ops.run_op`/`confirm_op` through the shared journal).
  Regression: `tests/test_autonomy_surface.py`.
- **Broker context forwarding.** `ActionRunner.execute`/`recover` now forward
  `target`/`owner`/`session`/`device` to the broker
  (`voicebridge/actions.py`), so the scoped-autonomy containment check is
  reachable from the live CLI/dashboard/voice paths.
- **Dashboard status backend fixed.** `h_status` returned a 200 with an empty
  payload (nonexistent `RI.inventory`/`voicebridge.status`); it now returns the
  canonical `runtime_inventory.runtime_inventory` (`voicebridge/hermes_compat.py`).
- **Sessions rename/delete contract aligned.** `api.ts` now sends `{name}` on
  PATCH and `{confirm:true}` on DELETE (was 400/409 for every user).
- **Provider profile endpoint/credential honored.** `EffectiveProfile` carries
  `base_url`/`key_ref`; `gateway.run_role` forwards them so a selected API
  profile executes against its own endpoint and credential reference
  (`voicebridge/providers/resolver.py`, `providers/gateway.py`).
- **Capability requirements fail closed without a catalog** (`require_streaming`
  /`require_tools`/structured) in `gateway.run`.
- **Failure receipts are truthful** (use the executed alias, not a hard-coded
  `qubit-brain4`) in all three local adapters.
- **Bare CLI families no longer silently succeed**: `egress`, `policy`,
  `backup`, `reports`, `marketplace` require a subcommand (`required=True`).
  Dead `_cmd_hermes` branch/function removed.
- **Packaging/entry-point truth**: `_data`-scoped dependency scan (prior
  commit) plus the plan-required `docs/QUBIT_PROVIDER_VOICE_AUDIT_2026-09-15.{md,json}`
  generated deterministically by `scripts/provider_voice_audit.py`.
- **Branding/config**: systemd unit comments and `docs/STEAL_LIST.md` corrected;
  `/qubit-models` added to the served SPA route set.
- **Dead imports**: 97 unused imports removed across `voicebridge/`
  (`ruff F401`); `product.LEGACY_NAMES` deliberately preserved.

### Known remaining limitations (honestly scoped, not silently passed)

- **Dashboard gated-control rendering.** Many gated controls surface a raw
  `403/409 {json}` toast instead of the typed policy-refusal renderer
  (`frontend/hermes-web/src/lib/policy-refusal.ts` is used on only a few
  pages). The controls are honest (gated, not fake success) but the copy is
  not yet uniform.
- **`/api/actions/{name}/status` wire shape** differs from the client type
  (`api.ts` expects `{lines,running,…}`; `h_actions_read` returns
  `{ok,actions,state}`), so the action-log viewers show no rows.
- **Autonomy policy ordering (D4/D5).** The outer `PowerPolicy` gate runs
  before the broker autonomy seam, so an in-scope high-impact op can still be
  denied by the gate; and `autonomy_ops`' hard-deny classifier is narrower
  than `power.is_high_impact` for project-code writes. Non-high-impact
  autonomy works end-to-end (tested); the high-impact reconciliation is open.
- **Desktop/browser hardware surfaces** (`OC-05` device tokens, `OC-12` remote
  attach, `OC-16/17` snapshot/CUA state, `OC-51` capture, `OC-D12/D13/D17`
  Discord/Telegram rich, `OC-D27` inspect/screencast) remain backend+test only
  with no product control caller; `OC-13/14` actuators require the operator's
  pinned actuator, which no caller can yet inject.
- **Voice-ID barge gate is inert**: `orchestrator._speaker` is never assigned,
  so `_barge_allowed` always returns True despite `config/voice.yaml:47`.
- **`/docs` embeds the upstream documentation site** (external egress); it is
  attribution-adjacent but not Qubit-hosted.
- **OpenClaw capability target** still needs the owner amendment described in
  the previous report before the 9.2/9.4 split is owner-approved.
- 23 defined-once helper functions have no caller (some may be intended public
  API); not removed to avoid breaking an undocumented consumer.

## Operator-only rows

The 11 `OPERATOR_ONLY` rows and their live prerequisites are unchanged and
remain unqualified (hardware: graphical session/paired node/display; creds:
Feishu open-platform secret, iMessage macOS bridge, channel tokens, external
memory provider). `docs/OPERATOR_QUALIFICATION.md` and
`docs/QUBIT_OPERATOR_QUALIFICATION_WAVE2.md` record the exact commands and
expected typed results. `OC-D16` and `OC-48`, previously flagged as code-side
gaps, now have real `qubit msg feishu-agent-*` and `qubit memory workspace-*`
CLI callers (`tests/test_feishu_agent_cli.py`,
`tests/test_workspace_memory_cli.py`).
