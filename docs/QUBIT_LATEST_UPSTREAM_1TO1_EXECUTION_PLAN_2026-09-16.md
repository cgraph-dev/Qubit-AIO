# Qubit latest-upstream parity and completion plan

Status: additive execution plan. This file does not amend, replace, reorder, or reinterpret any frozen acceptance contract, checklist, execution directive, owner amendment, or evidence requirement.

The implementation lead must stop and report if a requirement conflicts with a frozen document. A plan, ledger, test, or evidence file must never be changed merely to make an incomplete feature appear complete.

## 1. Objective

Make Qubit the single user-facing product and runtime authority while preserving the complete Hermes capability surface, adding the complete OpenClaw capability surface, and exposing all supported operations through the same Qubit backend, CLI, authenticated dashboard, and VoiceBridge.

The target is behavioral and workflow parity, not a misleading claim that unrelated source trees are byte-identical:

1. Every upstream file is inventoried and compared byte-for-byte against the corresponding Qubit/vendor copy.
2. Every upstream command, API route, page, control, provider, tool, skill, plugin, workflow, configuration option, state transition, error, and recovery path has a Qubit disposition backed by implementation and tests.
3. Hermes and OpenClaw do not run as competing live authorities. Qubit owns the front door, session/memory stores, provider selection, broker, policy, audit, scheduler, e-stop, resource limits, and UI routing.
4. Qubit-Brain4 is a replaceable local model/provider profile, not a hard-coded dependency. Users can select local, API, hosted, speech, and realtime providers through one profile and secret-reference system.
5. Qubit is the product identity on user-facing routes, titles, CLI help, completions, browser-visible text, generated artifacts, and UX copy. Legal notices and provenance records may name upstream projects where required.
6. No operation is reported as working when it only returns a status tile, catalog row, mock, placeholder, untested refusal, or fabricated success.

Completion is evidence-based. Do not use a percentage estimate as a gate.

## 2. Baselines and upstream refresh

At the start of every execution run, create a dated, immutable baseline record containing repository URL, branch, commit, tag/release, fetch time, tree hash, file count, license, and source-manifest hash.

The baseline discovered on 2026-09-16 was:

| Project | Contractual baseline already used by Qubit | Latest observed baseline to audit now |
|---|---|---|
| Hermes | `2237be355906fbe6065ce1815711eee52b2d646e` | `main` `416a8177c25d87aa9929dfcf31f7964137d7fcdd`; tag `v2026.9.14` `7a963716b81be13ba513d4f127633b7da493aff2` |
| OpenClaw | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` and contractual `3928bad9badfcb6c7d140530435e806fb8092190` | `main` `1e29cb301fc4dd1ffdf3525133de8a4b618df56b`; latest observed tag `v2026.9.4` `8bec206f3c1f787e1e9c45cfd34d3de2a78c7b8e` |

Official repositories:

- Hermes: https://github.com/NousResearch/hermes-agent
- OpenClaw: https://github.com/openclaw/openclaw

These observations are not permission to change a frozen pin. The refresh agent must:

- fetch the latest default branch and latest release/tag with `git ls-remote` and a clean shallow or full clone;
- verify the expected default branch, signed/reproducible metadata where available, license, commit, tree hash, submodules, generated files, and clean status;
- compare contractual pin → latest release → latest default branch as three separate datasets;
- produce a machine-readable upstream delta manifest and human-readable report;
- obtain owner approval before changing a contractual pin or importing a newer behavior;
- never overwrite the pinned clone, the pristine vendor mirror, or the previous evidence.

## 3. Definitions that prevent false parity claims

### Byte parity

For every upstream file, compare raw bytes, mode, symlink target, executable bit, and path. Report `IDENTICAL`, `EOL_NORMALIZED`, `PATCHED_WITH_REASON`, `MISSING`, `EXTRA`, or `NOT_APPLICABLE` with exact hashes. Do not normalize bytes before the raw comparison. EOL-normalized files must also have a raw hash and an explicit reason.

Keep these trees separate:

- `upstream-baselines/<project>/<commit>/`: read-only source snapshot;
- `qubit-vendor/<project>/<commit>/`: pristine byte-preserving mirror;
- Qubit adapters, overlays, safety policy, and branding: outside the pristine mirror.

If a vendored file must be changed, preserve the original, record a patch manifest and rationale, and test both the upstream behavior and Qubit policy boundary. “No upstream source copied” and “byte-identical vendor” must not be claimed simultaneously unless the files and provenance actually prove it.

### Capability parity

An upstream capability is closed only when its real Qubit path is demonstrated end-to-end:

`selector/input → request validation → canonical operation → broker/policy → runtime/provider → persistent state → result/event/render → error/retry/recovery → audit/receipt`.

Each capability needs a CLI path, authenticated dashboard workflow, and natural voice path, or a frozen-contract-approved `SAFE_EQUIVALENT` / `OPERATOR_ONLY` disposition with a real typed implementation and proof. A refusal is not an implementation of a safe capability.

### Visual parity

Compare the actual Hermes page structure, navigation, typography, spacing tokens, dimensions, responsive breakpoints, states, forms, menus, modals, keyboard behavior, loading/error/empty states, and copy. Use reference screenshots and DOM/accessibility snapshots from the pinned source and latest audited source. “The route returns 200” is not visual or workflow parity.

## 4. Frozen-contract rules

- Read all frozen plans before editing: `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`, `docs/QUBIT_FULL_PARITY_CONTRACT.md`, `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`, terminal-status amendments, `docs/QUBIT_NATIVE_COMPLETION_CHECKLIST.md`, and the current gap ledger.
- Do not edit frozen acceptance criteria, required order, terminal statuses, scope, safety policy, or completion wording.
- Do not delete provenance, licenses, upstream notices, or source manifests to hide names or differences.
- Do not silently loosen e-stop, authorization, secret handling, SSRF, sandbox, audit, resource, or confirmation policy.
- Do not run two live gateways/listeners or maintain duplicate writable stores.
- Do not use a passing route, catalog entry, snapshot, status tile, or synthetic response as evidence of a working feature.
- Preserve pre-existing user changes byte-for-byte. No `git reset --hard`, `git clean`, broad checkout, mass formatter, or destructive cleanup.
- Work in bounded batches. Use focused static checks while implementing; run expensive full/UI/package/independent gates after code is implementation-ready and again after any repair.

## 5. Phase 0 — preservation, environment, and ownership

Lead actions:

1. Record `git status --short`, current HEAD, branch, remote, ignored files, disk/memory/swap, active services, Python/Node/package-manager versions, and existing test collection.
2. Hash every pre-existing modified/untracked path and store the list outside the repository.
3. Provision read-only pinned and latest upstream clones outside the repository. Never edit them.
4. Disable no service and remove no file without an explicit operator action. Detect and report competing Hermes/OpenClaw services and ports.
5. Create a worktree or isolated branch for the wave if possible. Every agent receives an exclusive path list.
6. Freeze the evidence directory layout: `upstream/`, `inventory/`, `capabilities/`, `ui/`, `tests/`, `packaging/`, `handoffs/`, `reports/`.

No implementation agent may edit another agent’s owned files. The integration lead alone resolves cross-boundary changes after recording the conflict.

## 6. Phase 1 — complete A–Z inventory and differential manifest

The inventory agent must inspect the source trees directly, not rely on existing ledgers or prior reports. Enumerate and map:

- every Hermes and OpenClaw source file, package, module, class, function, command, option, route, RPC, event, WebSocket message, schema, database/table, migration, config key, environment variable, provider, model, tool, skill, plugin, hook, scheduler, background job, permission, scope, error, retry, rollback, and recovery behavior;
- every frontend route/page, shared layout, component, form, control, modal, keyboard shortcut, asset, stylesheet, font, icon, loading state, empty state, error state, responsive state, and accessibility behavior;
- every Qubit implementation, adapter, overlay, registry row, CLI command, dashboard selector, voice utterance, backend operation, store, test, and evidence record;
- all dead imports, unreachable branches, no-op handlers, stale aliases, duplicate authorities, unconsumed configuration, hard-coded models, bypass paths, routes that render but do not mutate/read real state, and UI controls without production callers.

Deliverables:

- `docs/QUBIT_UPSTREAM_BASELINE_2026-09-16.json`;
- `docs/QUBIT_BYTE_MANIFEST_2026-09-16.json`;
- `docs/QUBIT_CAPABILITY_INVENTORY_2026-09-16.json`;
- `docs/QUBIT_UI_PARITY_INVENTORY_2026-09-16.json`;
- `scripts/differential_parity.py` plus fixtures/schemas, implementing the
  executable upstream-vs-Qubit comparison described below;
- an append-only evidence report with source path, symbol/route, upstream hash, Qubit path, disposition, and test references.

The inventory must reconcile all older audits and explicitly mark stale, contradictory, or superseded evidence. It must not mark a row implemented because an older matrix did.

## 7. Phase 2 — one Qubit authority and provider architecture

Resolve the known architecture findings PV-01 through PV-10 before breadth work:

1. Create one canonical model/provider/profile resolver for local Brain4, API, hosted, embedding, vision, STT, TTS, and realtime roles.
2. Make provider selection explicit and persistent per profile/session/operation; no import-time Brain4 default may override a selected provider.
3. Route every chat, planner, classifier, summarizer, vision, voice, realtime, and fallback call through the gateway. Ratchet-test for direct provider/Ollama/network bypasses.
4. Propagate the resolved model and role through request → operation → runtime → receipt → dashboard/voice result.
5. Store API keys only through secret references or the approved local secret store; provide Qubit CLI/dashboard setup and validation without printing secrets or putting them in argv/history/logs.
6. Unify the writable session, memory, board, task, receipt, audit, and scheduler stores. Adapters may read legacy data but must not create a second writer.
7. Ensure broker/policy/autonomy/e-stop applies to CLI, dashboard, voice, scheduled, gateway, plugin, browser, desktop, and messaging paths.
8. Make target, owner, session, device, resource, and scope boundaries explicit and test them at every dispatch layer.
9. Add a single operation contract used by CLI, dashboard, voice, and external adapters; remove duplicate route-specific semantics.
10. Make streaming and non-streaming behavior explicit. Do not label a buffered response as SSE/realtime.

## 8. Phase 3 — Hermes runtime, CLI, API, and workflows

Against both the contractual Hermes pin and the latest audited Hermes release/head:

- run the real Hermes agent loop/backend through Qubit’s canonical authority, with Qubit policy wrappers at the boundary;
- map every Hermes CLI family, subcommand, option, exit code, help text, state transition, session/profile behavior, provider choice, toolset, cron, ACP, gateway, migration, and recovery path;
- map every API route, method, status code, schema, stream/event, auth scope, pagination, error, and mutation;
- reproduce every workflow with real persistent state, including first-run setup, profiles, models, providers, sessions, files, projects, skills, plugins, MCP, channels, webhooks, pairing, logs, cron, analytics, memory, security, devices, docs, achievements, and system controls;
- preserve intentional Qubit safety differences as typed, tested terminal dispositions only where the frozen contract permits them;
- ensure `qubit` is canonical in CLI help/completions and migration aliases are documented rather than accidentally exposed.

For each row, demonstrate the same successful result and equivalent failure/recovery behavior as the reference. Capture request/response/event traces with secrets and personal paths scrubbed.

### Required differential runner

The parity agent must build and use `scripts/differential_parity.py` rather
than manually comparing selected examples. The runner must be deterministic,
rerunnable, and able to execute the complete generated inventory in isolated
upstream and Qubit environments. It must:

- invoke the upstream and Qubit CLI parsers with the same arguments and
  compare help, exit status, stdout/stderr structure, errors, and receipts;
- call corresponding API/WS/SSE operations with the same sanitized fixtures
  and compare status, headers that are not nondeterministic, JSON schemas,
  event ordering, stream semantics, and persisted state;
- exercise OpenClaw’s Gateway WebSocket/RPC contract against Qubit’s adapter,
  including handshake/auth/scopes/pairing, sessions, agent runs, events,
  approvals, reconnect/recovery, node/device operations, browser/desktop
  actions, channels and webhooks, boards/widgets, schedules/delivery,
  plugins/skills/providers, files/artifacts, memory, web fetch/search, media,
  ACP, and task/background-run state;
- drive the upstream and Qubit dashboard through equivalent selectors and
  controls, comparing DOM/accessibility snapshots, screenshots, navigation,
  state transitions, and visible error/recovery behavior;
- send equivalent voice utterances through both resolution paths and verify
  the same canonical operation, arguments, confirmation/cancel semantics, and
  result;
- compare provider/model/profile selection for local Brain4, OpenRouter,
  OpenCode, speech, realtime, and fallback profiles without exposing keys;
- normalize only an allowlisted set of nondeterministic fields (timestamps,
  UUIDs, temporary paths, session tokens, process IDs, and product branding),
  fail on every other difference, and print the exact JSON path or DOM
  selector where it occurred;
- emit per-row `PASS`, `FAIL`, `SAFE_EQUIVALENT`, or `OPERATOR_ONLY` evidence
  with command, fixture hash, upstream result hash, Qubit result hash, and
  test/trace references;
- include OpenClaw’s latest release and default-branch delta as separate
  inventory rows, and fail if a newly added OpenClaw method, event, scope,
  plugin hook, channel, or UI control has no Qubit disposition and proof;
- fail closed if an operation was not executed, a production caller is
  missing, a fixture was skipped, a refusal was mistaken for success, or a
  normalizer hides a non-approved difference.

The runner must have its own unit tests, representative end-to-end fixtures,
and an all-inventory coverage test. A byte manifest, route count, catalog
count, snapshot test, or successful health check cannot substitute for this
runner.

## 9. Phase 4 — Hermes dashboard parity and UX repair

The dashboard agent must use the actual Hermes frontend source as the visual and interaction reference, not recreate it from a route list.

For every page and nested control:

1. Build or preserve the same information architecture and interaction model.
2. Match layout grid, container widths, sidebar behavior, spacing scale, typography, colors, borders, radii, iconography, density, focus states, hover/pressed states, and responsive breakpoints.
3. Exercise every button, link, menu, form, modal, tab, table action, keyboard shortcut, stream, retry, cancel, save, delete, restore, and pagination control.
4. Verify loading, empty, validation, authorization, unavailable, degraded, failure, retry, recovery, and success states.
5. Check accessibility tree, labels, focus order, keyboard operation, contrast, reduced motion, and mobile overflow.
6. Assert every control reaches the same canonical backend operation as CLI and voice; no dead visual controls and no dashboard-only fake state.
7. Compare screenshots and DOM snapshots at desktop, tablet, and mobile widths against the reference. Record justified Qubit branding differences separately from accidental spacing/layout drift.

The final visible product must be Qubit. Upstream names may remain only in legal/provenance/source-identity records and explicitly hidden internal compatibility metadata. Do not rewrite pristine upstream bytes to accomplish branding.

## 10. Phase 5 — complete OpenClaw integration

Re-audit the latest OpenClaw source and implement every permitted capability through adapters, not a second competing runtime. Cover at minimum:

- Gateway WebSocket/RPC protocol, authentication, scopes, pairing, device lifecycle, sessions, events, reconnect, approvals, and recovery;
- channels and inbound/outbound messaging, webhooks, schedules and exactly-once delivery;
- browser navigation/tabs/snapshots/annotations, desktop/computer-use boundaries, node host and remote transport;
- boards/kanban/tabs/widgets, background task ledger, memory, logbook, artifacts, files, patch/recovery;
- plugins, providers, skills, hooks, ACP, model routing, web fetch/search, media generation, voice/realtime, and external integrations;
- resource limits, sandboxing, SSRF protection, secret redaction, e-stop, audit receipts, authorization, and failure behavior.

For each capability, prove Qubit CLI + authenticated dashboard + voice coverage and shared state. If a live external service, account, hardware device, or owner decision is required, complete the code path and classify it `OPERATOR_ONLY`; do not call it fully verified until the operator runbook succeeds.

## 11. Phase 6 — VoiceBridge and Brain4/provider switching

Voice is a first-class control surface, not a small list of aliases.

- Generate voice coverage from the canonical operation registry.
- Add natural utterances for every read, write, retry, cancel, approve, recover, and status operation.
- Ensure voice uses the same profile/provider/model resolver as CLI and dashboard.
- Support Brain4 local inference, API models, hosted models, cloud fallback, STT, TTS, and realtime profiles with explicit selection and per-role configuration.
- Make confirmation policy configurable by operation risk and autonomy profile; never let “autonomous” bypass hard-deny, e-stop, target, secret, resource, or audit rules.
- Test wake/record/transcribe/resolve/preview/confirm/execute/speak/error/cancel/retry with deterministic substitutes, then qualify hardware manually.
- Verify difficult commands such as opening a site, navigating/searching/reading a page, switching or closing tabs, analyzing page content, controlling an approved desktop app, playing media, sending a message, scheduling work, and running a background task. Every action must show the authorization and recovery path.

## 12. Phase 7 — rebrand and dead-code cleanup

Run the rebrand only after parity inventory and integration are stable:

- scan source, generated assets, routes, hrefs, titles, aria labels, CLI help, completions, logs, receipts, default configs, examples, service files, package metadata, and browser-visible text;
- classify every upstream-name occurrence as user-facing defect, required legal/provenance identity, compatibility alias, test fixture, or internal vendor source;
- remove accidental user-facing Hermes/OpenClaw branding and stale paths such as `/hermes/...` from the Qubit product surface;
- retain required licenses, notices, source manifests, pinned references, and migration documentation;
- remove only proven unreachable/dead Qubit code after call-graph and behavior checks; do not delete compatibility paths merely because they are not in the main navigation;
- regenerate bundles, completions, manifests, ledgers, and package metadata deterministically.

The acceptance test must fail on accidental user-facing upstream references but allow documented legal/provenance references.

## 13. Phase 8 — implementation batching and test policy

Agents may run fast local checks while editing: syntax/type checks for touched files, focused unit tests, schema checks, and deterministic manifest checks. Do not rerun the monolithic suite after every small edit.

After all implementation waves are complete, run the final gates in this order:

1. source inventory and raw byte manifest, twice for determinism;
2. dead-code, stale-config, route reachability, generated-artifact, secret, privacy, and branding scans;
3. focused tests for every repaired capability;
4. full hermetic suite with warnings as errors and no cache provider;
5. frontend typecheck, lint, unit tests, production build, screenshot/DOM/accessibility suite;
6. real dashboard Playwright sweep of every route and every control family;
7. CLI help/completion/parser parity and CLI/dashboard/voice differential tests;
8. package wheel/sdist, install in a clean isolated environment, run the installed CLI/backend/dashboard, and verify assets/routes;
9. migration, upgrade, rollback, uninstall, restart, crash recovery, disk-pressure, and e-stop rehearsals;
10. live canaries for Brain4, provider switching, voice, browser, desktop, messaging, schedules, and OpenClaw adapters where prerequisites exist;
11. a fresh independent verifier using only the repository, pinned/latest source baselines, and the evidence commands.

The final report must list exact commands, counts, skips, failures, environmental prerequisites, screenshots/traces, hashes, and every remaining operator-only item. A failed or skipped test is not green.

## 14. Agent deployment and handoff protocol

Deploy agents only with disjoint ownership and a written handoff. Suggested workstreams:

| Agent | Exclusive focus | Typical paths |
|---|---|---|
| A | upstream refresh, raw manifests, inventories | `scripts/*manifest*`, `scripts/*inventory*`, `docs/*BASELINE*`, `docs/*INVENTORY*` |
| B | Hermes runtime/API/CLI parity | `voicebridge/hermes_*.py`, `voicebridge/cli_hermes_forward.py`, assigned CLI modules |
| C | Hermes frontend parity and visual QA | `frontend/hermes-web/**`, assigned `frontend/*` pages/assets |
| D | OpenClaw adapters and protocol surfaces | assigned `voicebridge/openclaw*`, gateway/node/browser/messaging/media modules |
| E | providers, Brain4, speech, realtime, routing | `voicebridge/providers/**`, model/profile/speech modules |
| F | broker, policy, autonomy, stores, sessions, scheduler | `voicebridge/broker.py`, policy/autonomy/store modules |
| G | dashboard wiring, plugins, operation registry | dashboard/plugin/ops modules and assigned frontend overlays |
| H | branding, packaging, lifecycle, dead-code audit | `voicebridge/webui.py`, packaging scripts, metadata, assigned docs/config |
| I | tests and evidence integration | new tests and append-only evidence only; never weaken an existing test |
| J | independent verifier | read-only entire repository and upstream baselines; no implementation edits |

The integration lead must resolve ownership overlaps before agents start. Each handoff includes: files changed, source symbols, upstream references, tests run, known failures, evidence paths, rollback hashes, and unresolved questions. An agent may report a blocker; it may not rewrite the requirement to remove the blocker.

## 15. Per-capability evidence record

Every inventory row must contain:

`capability_id, upstream_project, upstream_commit, upstream_path/symbol, raw_hash, Qubit_path/symbol, disposition, CLI_command, dashboard_route/control, voice_utterance, request_schema, broker/policy decision, runtime/provider, store/state assertion, output/event assertion, error codes, retry/recovery proof, audit/receipt proof, security/resource proof, package proof, test_ids, screenshot/trace_ids, operator_prerequisite, rollback_ref`.

The verifier must sample successful and failing rows from every family and then run the generated all-row reachability test. A row without a real production caller and test remains open.

## 16. Final completion gate, commit, and push

Completion may be reported only when all of the following are true:

- the latest-upstream audit is recorded and contractual pin changes are explicitly approved;
- raw byte manifests are deterministic with every difference explained;
- zero unexplained inventory rows and zero code-side `MISSING`, `UNVERIFIED`, `PARTIAL`, or equivalent open rows remain;
- every page and control is reachable, functional, responsive, accessible, and connected to the canonical backend;
- CLI, dashboard, and voice produce equivalent results and share state, policy, audit, and e-stop;
- Brain4 and provider/API/speech/realtime switching are real, configurable, and tested;
- no duplicate live authority or writable store remains;
- Qubit branding is complete on the product surface while licenses/provenance remain accurate;
- dead-code and stale-configuration audits pass;
- full tests, UI tests, package/clean-install, lifecycle, recovery, and independent verification pass;
- remaining hardware/account/OAuth/signing items are explicitly operator-only with exact runbooks and are not mislabeled as verified.

Before committing, show the final changed-path list and compare it against the pre-existing hash list. Stage only intended paths. Create one coherent commit with the exact evidence reference. Re-run the post-commit verification. Push to the configured remote using the normal branch push; never force-push. Record commit SHA, remote SHA, tag/release notes if applicable, and rollback as `git revert <commit>` unless the owner explicitly directs another recovery method.

## 17. Required final report format

The lead must finish with:

1. exact upstream heads/tags and contractual pins;
2. exact byte-manifest totals and difference classifications;
3. capability inventory totals by project, family, and disposition;
4. page/control/CLI/voice coverage totals and failing/sampled controls;
5. provider/model/Brain4 switching proof;
6. full command output summaries, skips, environmental failures, and independent-verifier result;
7. operator-only runbook items;
8. changed files, commit SHA, push result, and rollback;
9. a plain verdict: `VERIFIED`, `VERIFIED WITH EXPLICIT LIMITATION`, or `INCOMPLETE`.

Never write `100%`, `1:1`, `complete`, or `verified` when any required evidence, real workflow, or independent gate is missing.
