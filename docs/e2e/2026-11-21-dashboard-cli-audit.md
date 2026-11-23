# Qubit dashboard and CLI audit — 2026-11-21

See the [2026-11-22 follow-up](2026-11-22-dashboard-cli-audit-followup.md) for expanded execution evidence and subsequent repairs.

## Executive summary

Three defects were repaired on an isolated branch. On the existing dashboard, reloading a Workbench session returned `404` and the collapsed workspace panel added 14 CSS pixels of horizontal overflow. On an isolated copy of the current source, Cron's Pause action returned a confirmation request that the browser never settled. All three repairs passed targeted isolated checks. The live service is still on its existing revision.

Qubit's own workflows and end-to-end behavior are the acceptance criteria for this audit. Upstream revisions establish provenance and provide compatibility references. The owner's ongoing dashboard improvements, the embedded xterm design, product routes, and intentional compatibility adapters are outside the three repair changes.

The audit is open. The legacy session rename, cron pause and plugin rescan browser checks timed out. A follow-up Chromium run proved the repaired Cron approval and persistence flow. The live action, provider and recovery matrices remain incomplete. The branch is local pending live verification.

## Scope and versions

| Item | Evidence |
| --- | --- |
| Repository and branch | `cgraph-dev/Qubit-AIO`, `audit/qubit-dashboard-cli-2026-11-21` |
| Source baseline | `6f4da4905728adef401d8cad0cffbfa0943a4cdf`, GitHub `main` at checkout |
| Live dashboard | Browser reached `http://127.0.0.1:8890`. `/build-info.json` reported SPA source `65f7894ee1c8d571684a5988fe912a5219882215`. The checkout had no live loopback listener. The live Python revision and service installation path were not established. |
| Isolated test environment | CPython 3.14.10, Node.js 24.22.3, npm 11.19.1, Chromium 153.0.8010.0, FastAPI 0.133.1. VoiceBridge 0.0.15, runtime package 0.21.5. WebUI tests use ephemeral loopback ports and disposable state. |
| Dashboard source provenance | `NousResearch/hermes-agent@2237be355906fbe6065ce1815711eee52b2d646e`, MIT, vendored dashboard baseline documented at `THIRD_PARTY_NOTICES.md:550`. `docs/FROZEN_TARGET.md` records a later runtime comparison pin, `088f657ecf77674c7b8e2ededf4e3576d82e042c`. |
| Workbench source provenance | `nesquena/hermes-webui@3d9488fca6bb248c77f988bfbffcca9d8cfd9561`, MIT, `docs/WORKBENCH_UI_PORT.md:5`. |
| OpenClaw source provenance | `openclaw/openclaw@7ff3b3be3713751de356800167d67ce752ad0d04`, MIT, for the console styles and layout described at `THIRD_PARTY_NOTICES.md:157`. |

The web dashboard, CLI, route contracts, and compatibility seams are in scope. Desktop, xterm redesign, destructive live tests, and edits to the owner's active checkout are excluded. The live profile was used for read-only browser observation. The isolated tests wrote to disposable stores.

## Inventory

The [machine-readable inventory](2026-11-21-dashboard-cli-inventory.json) records 60 SPA route declarations, 14 tracked overlay manifest routes, 394 registered runtime entries, 146 literal VoiceBridge HTTP dispatch conditions, 489 literal compatibility declarations, and 783 JSX control declarations. The two CLI parser trees yielded 887 VoiceBridge entries and 528 agent-runtime entries, including nested commands and aliases. The JSON records route, handler, or declaration references and available CLI flags.

- SPA registrations originate in `frontend/qubit-web/src/App.tsx:334` and `:839`. Sidebar declarations begin at `:415`. Plugin routes and overrides are resolved in `:697`, and can add to or replace shipped routes. OpenViking's local-engine entries and the Sakuga gallery have deliberate navigation visibility rules.
- The Workbench is a separate server document under two prefixes. `voicebridge/webui.py:4205` serves its static assets and bridges the Workbench APIs. The SPA hands off to this document.
- The interactive terminal on `/chat` and the native chat on `/qubit-chat` have distinct mounts in `frontend/qubit-web/src/App.tsx:823`. Their terminal and native session flows require separate evidence.
- The static JSX extraction covers known control element names and visible source attributes. Composed, runtime-injected, translated, or dynamically generated controls require browser inspection. The JSON cannot establish a complete UI-to-handler-to-write map, nor the reachability of a dynamic route.
- VoiceBridge's parser is `voicebridge/cli.py:412`. The runtime parser is `qubit_runtime/qubit_cli/main.py:3518`. Installed plugins and interactive slash commands can contribute additional commands.

## Page and action verification matrix

`PASS` applies only to the check named in that row. Live route observations recorded pathname, title and document width at a 2,514 CSS-pixel viewport. They establish initial entry only. Every unlisted action and every route listed solely in the JSON remains `NOT VERIFIED`.

| Surface | Observed evidence | Status |
| --- | --- | --- |
| Workbench session reload, live | The browser had a Workbench session URL and rendered the workspace panel. Reload at the same session path returned the plain-text “not found” body. | FAIL |
| Workbench closed panel, live | A 2,514px viewport had a 2,528px document. The collapsed pane's transform placed its border 14px beyond the viewport. | FAIL |
| Workbench session and layout, isolated repaired source | A created session returned HTTP 200 as HTML on both mount prefixes. The corresponding session API returned the same ID. Chromium checked 1,440px document width on first load and reload, with the collapsed-panel control in the viewport. | PASS |
| `/chat`, `/qubit-chat` | Initial live route entry observed. Prompt send/receive, cancellation, terminal resize and reconnect, and session persistence were not exercised. | NOT VERIFIED |
| `/sessions` | Initial live entry observed. The existing isolated test timed out locating rename. A follow-up observed the seeded row and the Rename session button after successful list reads, but did not complete an edit and read-back. | NOT VERIFIED |
| `/models`, `/config`, `/env` | Initial live entry observed. Switching, credential changes, picker state, and live persistence remain open. | NOT VERIFIED |
| `/logs`, `/files` isolated read APIs | Existing typed read test passed for both APIs. | PASS |
| `/logs`, `/files`, `/analytics` live | Initial live entry observed. Interactive file and log operations and analytics data parity remain open. | NOT VERIFIED |
| `/qubit-gateway` | The sidebar click reached the Gateway route and title. Direct entry showed a Sessions pathname on two observations. A direct-entry cause, start/stop, restart and health transitions were not established. | NOT VERIFIED |
| `/plugins` | Initial entry observed. Isolated preflight rescan API returned the overlay manifest set. The existing browser check timed out awaiting the corresponding response after trying the UI action. | NOT VERIFIED |
| `/mcp`, `/skills` | Initial live entry observed. Install, auth, invocation, mutation, retry and remove remain open. | NOT VERIFIED |
| `/cron`, `/qubit-scheduler` live | Initial live entry observed. Live schedule actions and execution remain open. | NOT VERIFIED |
| `/cron` isolated repaired source | Declining Pause left the job enabled after HTTP 409. Approving Pause and Resume each produced 409 → 200 and an independent list read returned disabled and enabled respectively. | PASS |
| `/webhooks`, `/channels` | Initial live entry observed. Creation, enablement and delivery/retry remain open. | NOT VERIFIED |
| `/agents`, `/agent-workspace`, `/qubit-boards`, `/profiles`, `/webchat` | Initial live entry observed. Data actions and recovery remain open. | NOT VERIFIED |
| `/qubit-memory`, `/open-viking` | Initial live entry observed. Real provider operations remain open. Direct entry to `/memory` showed a Sessions pathname with a Memory title in one observation. | NOT VERIFIED |
| `/qubit-models` | One direct-entry sample showed a Sessions pathname with a Qubit Models title. A stable result and provider switching were not established. | NOT VERIFIED |
| `/reports` isolated fixture | The existing Reports test passed its UI and backend read checks. | PASS |
| Runtime CLI config | A disposable config accepted `terminal.timeout=137`, and a separate process read back `137`. Both CLI entry points returned exit 0 on `--help` and exit 2 for an invalid config subcommand. | PASS |
| Remaining CLI, PTY and integration lifecycle | Parser and route registration were inventoried. Operation, signal, timeout, refusal and exit-code contracts need execution. | NOT VERIFIED |

## Defect register

| ID | Severity | Surface and reproduction | Root cause and user impact | Repair and evidence |
| --- | --- | --- | --- | --- |
| QAI-001 | P2 | Open `/qubit-workbench` at a desktop width with the workspace panel collapsed. Compare `document.documentElement.scrollWidth` with `clientWidth`. | Desktop collapsed selectors in `voicebridge/workbench_ui/static/style.css:3223` translated the zero-width pane 14px to the right. The page acquired horizontal overflow even when the pane was closed. | `aa2216c6d2e6fafd4205efad85688fbf2df1c984` removes the translation. The browser regression failed on the old CSS, then passed on the repaired branch at 1,440px, on initial navigation and after reload. Live repair verification is blocked. |
| QAI-002 | P2 | Open an existing session in Workbench and reload the session URL. Isolated session creation followed by a direct request reproduced the 404 on `/qubit-workbench/session/<id>` and `/wb/session/<id>`. | `voicebridge/workbench_ui_routes.py:112` treated the client-generated document URL as an asset directory, so refresh and deep entry failed before session restoration. | `94d4c2d681b1c452fa0e2852131fe6a93ad566e0` serves the Workbench document for the session path. Both real HTTP route regressions moved from 404 to 200, returned the correct mount base, and read back the persisted session ID through the existing API. Live repair verification is blocked. |
| QAI-003 | P2 | On an isolated VoiceBridge dashboard, click Pause for an enabled job. The normal request returned 409 with a pending confirmation and the job did not pause. | `frontend/qubit-web/src/lib/api.ts:1599` and `:1625` used bare requests, while the canonical pause/resume handler at `voicebridge/qubit_compat.py:3610` stages these mutations. The dashboard never requested the operator's choice or settled the change. | `64e42b684df505ddf8415b269464f4d23ff5908f` uses the existing approval helper. Approval and refusal unit tests passed for both actions. Isolated Chromium verified the prompt, HTTP responses and persisted job state. Live repair verification is blocked. |

[Machine-readable defects](2026-11-21-dashboard-cli-defects.json)

## Compatibility and memory

- `INTENTIONAL_ADAPTER` — Workbench serves two mount prefixes and resolves session data through the Qubit session store. The focused command-dispatch and settings suites passed 69 checks using an isolated fixture.
- `INTENTIONAL_ADAPTER` — The runtime plugin API and historical publication compatibility test selection passed 12 checks. No real external Hermes or OpenClaw artifact was installed and invoked during this audit. MCP transports, SDK globals, legacy paths, and unknown manifest fields remain open for end-to-end verification.
- `DIVERGENT` — Qubit's native chat, embedded terminal, and separate Workbench remain distinct surfaces. The source layout establishes the distinction. Cross-surface conversation behavior and session handoff were not verified.
- `UNKNOWN` — Missing capabilities relative to the pinned references were not exhaustively enumerated. The existing rebrand and product choices were preserved. The audit has no basis for declaring a missing upstream feature a Qubit defect without a failing Qubit workflow or compatibility contract.
- OpenViking — The live Studio and provider routes were reached. Fourteen isolated tests passed for the OpenViking adapter and namespace projections. A healthy live provider, real writes and recall, reindexing and offline behavior remain unverified.
- Obsidian — `voicebridge/qubit_compat.py:19828` contains the installed-plugin note adapter. A configured vault, refresh, note CRUD, UI/CLI agreement and permissions remain unverified.
- Graft — `graft map` returned command-not-found in the audit environment, and the checkout had no `graft/` graph directory. Graft health, indexing, search and shared memory authority in the deployed profile remain unverified.

## Reliability, safety and performance

The isolated Workbench tests exercised normal authenticated reads and the targeted session route. They establish neither a security assessment nor end-to-end process cleanup across all services. Cross-profile authorization, resource limits, backup and restore, dependency advisories, sensitive-data handling, network failure and restart behavior remain open. No P0 or P1 was established within the sampled evidence.

The production build completed. The initial build reported a 473.44 kB uncompressed xterm chunk and 225.73 kB uncompressed React vendor chunk. These are emitted chunk sizes, not per-route transfer, execution time or performance budgets. Bundle size, memory, p95 latency, long history, disk growth and sustained concurrency were not benchmarked.

The collapsed Workbench overflow was measured and repaired. The reported live widths are a desktop sample. Narrow screens, keyboard/focus, Escape/outside-click behavior, zoom, and a full runtime error inventory remain unverified.

## Commands and artifacts

- `git clone --depth 1 --single-branch --branch main https://github.com/cgraph-dev/Qubit-AIO.git Qubit-AIO`
- `git fetch --depth=1 origin 65f7894ee1c8d571684a5988fe912a5219882215`
- `uv sync --frozen --no-dev --extra web --python 3.14` with the interpreter and venv installed under the disposable audit state.
- `npm ci --no-audit --no-fund` in `frontend/qubit-web` and `KEEP_NODE_MODULES=1 scripts/build_dashboard.sh` in the checkout.
- `qubit --help`, `python -m voicebridge --help`, `qubit config set terminal.timeout 137`, `qubit config get terminal.timeout`, and `config audit-invalid-command` on each entry point.
- `pytest -q -m ui tests/test_dashboard_e2e_phase4.py -k 'workbench_closed or workbench_session_deep_link or files_and_logs_typed_reads' -rs` (4 passed). The same module's targeted `test_reports_reads_are_real` passed when run separately. `test_sessions_rename_persists`, `test_cron_pause_persists`, and `test_plugins_rescan_returns_manifests` timed out in separate runs. A combined run also encountered a headless browser teardown failure under the sandbox. The local runner disabled pytest plugin auto-load and supplied the installed headless shell with `--single-process --no-zygote` because the regular Chromium executable could not start in the sandbox.
- `npm test -- src/lib/api.test.ts` (58 passed, including four Cron approval regressions). A disposable Chromium run clicked Pause, declined it, then accepted Pause and Resume, and asserted the HTTP responses and independent list reads for all three outcomes.
- `pytest -q tests/test_workbench_command_dispatch.py tests/test_workbench_settings_onboarding_updates.py` (69 passed).
- `pytest -q tests/phase2_memory/test_memory_openviking.py tests/phase4_memory_projection/test_openviking_scopes.py` (14 passed).
- `pytest -q qubit_runtime/tests/qubit_cli/test_plugin_api_compat.py qubit_runtime/tests/qubit_cli/test_plugin_historic_compat.py` (12 passed).
- The linked JSON files carry inventory and defect evidence. No live screenshots, session IDs, profile data, tokens or debug logs are included in this report.

## Blockers and next checks

1. Deploy a known audited revision to the operator's `:8890` service using its rollback path after the active UI edits are ready. Verify Workbench session-link reload, session identity and layout, then Cron approval and persistence on that exact deployed revision.
2. Trace the remaining session rename and plugin rescan browser-test failures with a pinned service build and known fixture data. Establish action, request, response, and persisted state, plus stable direct entry for the affected overlay routes.
3. Finish verification of the two chat modes, gateway and PTY lifecycle, the remaining route/action matrix and CLI, and configured OpenViking, Obsidian and Graft. Exercise a real Hermes artifact and a real OpenClaw artifact through Qubit's supported adapters.
4. Rerun the bounded regressions and complete release verification before pushing the audit branch. Once pushed, the branch report and comparison will have GitHub URLs.

## Final scorecard

| Gate | Status |
| --- | --- |
| Production dashboard build | PASS |
| Isolated Workbench targeted regressions and Cron approval round trip | PASS |
| Isolated CLI config set/read-back and basic exit-code checks | PASS |
| Targeted Reports UI and Files/Logs API checks | PASS |
| Existing browser tests for session rename, cron pause and plugin rescan | FAIL |
| Live operation matrix, memory stack, CLI/PTY lifecycle, accessibility, security, performance and artifact compatibility | NOT VERIFIED |
| Live redeployment and repair verification | BLOCKED |
| Non-force push and public report link | BLOCKED |
