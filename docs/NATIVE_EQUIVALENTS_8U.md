# Qubit-Native Equivalents (Packet 8U)

Each row resolves a formerly BLOCKED/REJECTED upstream capability to a Qubit-native
implementation, an equivalent with a documented safety divergence, an
operator-qualification path, or an explicit limitation with a genuine
technical/licensing/safety reason. Machine-readable source:
`docs/NATIVE_EQUIVALENTS_8U.json` (tests/test_native_equivalents8u.py pins
every row's CLI/voice/test paths).

## N8U-BROWSER-TABS — IMPLEMENTED

User goal: drive the user's browser tabs: list, read, find, open, navigate, fill, click

Upstream: hermes-agent@2237be3 tools/browser_tool*.py (rewrite, loopback-only divergence)

Qubit: CLI `browser tabs`, `browser read`, `browser find`, `browser doctor`, `browser open`, `browser open-browser`, `browser navigate`, `browser activate`, `browser fill`, `browser click`, `browser close`; Tier-1 ops browser_* (same backend).

Voice: `browser_tabs`, `browser_read`, `browser_find`, `browser_doctor`, `browser_open_browser`, `browser_activate`, `browser_open`, `browser_navigate`, `browser_fill`, `browser_click`, `browser_close_tab`.

Tests: `tests/test_browser_tabs8u.py`. Owner: qubit-policy. Rollback: no migration; delete state/browser_tabs.json to reset the tab marker.

## N8U-SCHEDULES — IMPLEMENTED

User goal: run my tasks on a schedule (interval/daily/once)

Upstream: hermes-agent@2237be3 cron/*.py (rewrite, no detached workers)

Qubit: CLI `schedules create`, `schedules list`, `schedules show`, `schedules pause`, `schedules resume`, `schedules delete`, `schedules history`, `schedules run-due`; Tier-1 ops schedules_* (same backend).

Voice: `schedules_create`, `schedules_list`, `schedules_show`, `schedules_pause`, `schedules_resume`, `schedules_delete`, `schedules_history`, `schedules_run_due`.

Tests: `tests/test_schedules8u.py`. Owner: qubit-policy. Rollback: SQLite store; delete state/schedules.db to reset (history lost).

## N8U-FILES — IMPLEMENTED

User goal: inspect project folders and read project files

Upstream: hermes-agent@2237be3 tools/file_operations*.py (read half only; writes stay brokered)

Qubit: CLI `files list`, `files read`, `files inspect`; Tier-1 ops files_* (same backend).

Voice: `files_inspect`, `files_list`, `files_read`.

Tests: `tests/test_files_ide8u.py`. Owner: qubit-policy. Rollback: read-only; nothing to roll back.

## N8U-IDE — IMPLEMENTED

User goal: open my work in my editor

Upstream: openclaw edit/apply tool family (behavioral parity: open-only launcher, no control claimed)

Qubit: CLI `ide status`, `ide open`; Tier-1 ops ide_* (same backend).

Voice: `ide_status`, `ide_open`.

Tests: `tests/test_files_ide8u.py`. Owner: qubit-policy. Rollback: launcher only; nothing to roll back.

## N8U-WAKE — IMPLEMENTED

User goal: hands-free activation without a second listener

Upstream: hermes-agent@2237be3 tools/wake_word*.py (native sherpa spotter, single listener)

Qubit: CLI `audio wake-status`, `audio wake-probe`; Tier-1 op wake_status (same backend).

Voice: `wake_status`.

Manual qualification: 1) fetch a sherpa-onnx keyword-spotter bundle + keywords.txt into the model dir; 2) run `qubit audio wake-probe phrase.wav` and confirm the keyword is detected with a sane timestamp; 3) enable loop arming in config on the desktop session and confirm hands-free activation end to end with mic hardware.

Tests: `tests/test_wake8u.py`. Owner: qubit-policy. Rollback: no migration; remove model files to return to NO_MODEL.

## N8U-FRAMES — IMPLEMENTED

User goal: gateway frames cannot corrupt the session or OOM the bridge

Upstream: openclaw@3928bad gateway protocol v4 (validation only, no source copied)

Qubit: CLI `gateway`, `peer`; Tier-1 ops gateway/peer_* (same backend).

Tests: `tests/test_gateway_frames8u.py`. Owner: qubit-policy. Rollback: no migration; counters are in-memory.

## N8U-CLOUDMEM — IMPLEMENTED

User goal: my memory follows me without silent cloud sync

Upstream: H-cloudmem (cloud sync refused by policy; portable encrypted export is the sync story)

Qubit: CLI `memory`, `export-state`, `import-state`; memory pages + export/import forms (same backends).

Voice: `memory_search`, `memory_remember`, `memory_forget`.

Tests: `tests/test_packet5b.py`, `tests/test_packet8m.py`. Owner: qubit-policy. Rollback: standard memory/export rollback contracts.

## N8U-DESKTOP — IMPLEMENTED

User goal: use my desktop apps through Qubit

Upstream: H-desktop (raw actuators refused; supervised Ghost grants + open-only launchers are the safe surface)

Qubit: CLI `ghost`, `ghost retry`, `ghost recover`, `ghost execute`, `computer-use`, `computer-use session`, `ide`, `browser`; Tier-1 ghost/computer/ide/browser ops (same backends).

Voice: `ghost_status`, `ide_open`, `browser_open_browser`, `ghost_action_preview`, `ghost_click`, `ghost_type`, `ghost_read_tab`, `ghost_action_retry`, `ghost_action_recover`.

Limitation: Raw desktop control stays DISABLED (no actuators by policy). Full app control additionally needs a live desktop session with the operator present.

Tests: `tests/test_computer_use8tf.py`, `tests/test_packet8n.py`, `tests/test_desktop_parity8u.py`. Owner: qubit-policy. Rollback: adapter disabled by default; disable returns to refusal.

## N8U-CLOUDBROWSER — IMPLEMENTED

User goal: browser automation without handing my browser to a cloud service

Upstream: H-cloudbrowser (third-party browser service refused; loopback CDP is the authority)

Qubit: CLI `browser`; Tier-1 browser_* ops (same backend).

Voice: `browser_status`, `browser_tabs`, `browser_read`.

Tests: `tests/test_browser_tabs8u.py`, `tests/test_browser8tf.py`. Owner: qubit-policy. Rollback: nothing to roll back.

## N8U-INSTALLER — IMPLEMENTED

User goal: install, upgrade, and remove Qubit cleanly

Upstream: H-installer (native installer + wheel + systemd units; signing needs a clean machine)

Qubit: CLI `product`, `package`, `verify-release`; product/package pages + forms (same backends).

Voice: `product_status`, `product_probe`, `package_status`.

Manual qualification: Clean-machine install from the built wheel/sdist, hardware doctor, first launch, upgrade, uninstall; release signing on a clean machine with the release key.

Tests: `tests/test_packet8k.py`, `tests/test_release_smoke.py`. Owner: qubit-policy. Rollback: upgrade/uninstall/rollback plans in `qubit product`; backups preserved.

## N8U-CLAWHUB — IMPLEMENTED

User goal: install a reviewed skill safely

Upstream: O-clawhub (remote auto-install refused; reviewed-local-manifest workflow with hash/license/manifest validation, staging disabled, confirmation, rollback)

Qubit: CLI `skills install`, `skills enable`, `skills remove`, `skills rollback`; skills pages + forms (same backends).

Voice: `plugins_install`, `skills_enable`, `skills_remove`, `skills_rollback`.

Tests: `tests/test_plugins8tf.py`. Owner: qubit-policy. Rollback: rollback_to on every record; remove purges the record.

## N8U-TERMINAL — IMPLEMENTED

User goal: run terminal work through Qubit with approvals

Upstream: H-terminal (no shell-string path by construction; declared-argv scripts + bubblewrap isolation)

Qubit: CLI `actions`, `task`, `opencode`; Tier-1 actions ops + terminal guidance (same backends).

Voice: `console_help`, `actions_execute`.

Tests: `tests/test_terminal.py`, `tests/test_packet8f.py`. Owner: qubit-policy. Rollback: no standing change; per-action receipts.

## N8U-STREAM-STT-TTS — IMPLEMENTED

User goal: hear, transcribe, speak, and stream

Upstream: H-stream/H-stt/H-tts (Qubit-owned streaming/ASR/TTS contracts)

Qubit: CLI `status`, `mic-test`, `asr-test`, `tts-test`, `task`; status + audio pages (same backends).

Voice: `status`, `mic_test`, `task_run`.

Tests: `tests/test_packet4a.py`, `tests/test_packet7c.py`, `tests/test_barge_stream.py`. Owner: qubit-core. Rollback: standard config rollback.

## N8U-SESSIONS — IMPLEMENTED

User goal: my sessions persist and resume

Upstream: H-sessions (Qubit session receipts + journals + checkpoints)

Qubit: CLI `sessions`, `checkpoints`; sessions + reports pages (same backends).

Voice: `sessions`, `checkpoints_list`.

Tests: `tests/test_owner_session8tf.py`. Owner: qubit-policy. Rollback: journal append-only; checkpoints prune with confirm.

## N8U-GATEWAY — IMPLEMENTED

User goal: gateway-connected runs with approvals

Upstream: H-gateway + O-frames (client + frame guards + pairing/approval flow)

Qubit: CLI `gateway`, `peer`, `msg`; Tier-1 gateway/peer/msg ops (same backends).

Voice: `gateway`, `msg_send`, `pairing_approve`.

Tests: `tests/test_gateway_peers8tf.py`, `tests/test_messaging8tf.py`, `tests/test_gateway_frames8u.py`. Owner: qubit-policy. Rollback: disable returns to refusal.

## N8U-BOARD — IMPLEMENTED

User goal: operate everything from the dashboard

Upstream: O-board (board HTTP store + sandboxed widgets; capability operations already dashboard-operable)

Qubit: CLI `kanban open`, `kanban move`, `kanban widget-add`, `kanban widget-list`, `kanban widget-move`, `kanban widget-remove`, `kanban layout-show`, `kanban layout-set`, `kanban role-grant`, `kanban role-revoke`, `kanban roles`, `kanban snapshots`, `kanban snapshot-restore`, `kanban`, `project`; boards + projects pages, Tier-1 kanban_*/projects_* (same backends).

Voice: `kanban_list`, `projects_list`, `kanban_open`, `kanban_move`, `kanban_widget_add`, `kanban_widget_list`, `kanban_widget_remove`, `kanban_layout_show`.

Limitation: A sandboxed server-side widget canvas is deferred: it needs dashboard widget policy first, and webui.py overlaps the operator's in-flight dashboard redesign. Every capability is already operable from the dashboard via the Tier-1 op catalog and the capability pages.

Tests: `tests/test_kanban8tf.py`, `tests/test_dashboard_ops8tf.py`, `tests/test_board_canvas8u.py`. Owner: qubit-policy. Rollback: n/a (no canvas shipped).

## N8U-DOCKER — IMPLEMENTED

User goal: a reproducible, isolated runtime

Upstream: O-docker (digest-pinned multi-stage runtime pattern documented, no Dockerfile taken)

Qubit: CLI `runtime images`, `runtime status`, `runtime doctor`, `runtime install`, `runtime upgrade`, `runtime rollback`, `runtime remove`; product/package pages (same backends).

Voice: `runtime_images`, `runtime_status`, `runtime_doctor`, `runtime_install`, `runtime_upgrade`, `runtime_rollback`, `runtime_remove`.

Limitation: Docker is forbidden by the stack rules (no new runtime dependencies); the native runtime story is bubblewrap isolation + signed wheel/sdist + systemd units. Container deployment needs an operator-owned Dockerfile outside this repo.

Tests: `tests/test_packet3b.py`, `tests/test_packet8k.py`, `tests/test_runtime_images8u.py`. Owner: qubit-policy. Rollback: n/a.

## N8U-SQLITE-TXN — IMPLEMENTED

User goal: memory writes never corrupt the store

Upstream: H-sqlite-txn (IMMEDIATE txn + idempotent ADD COLUMN)

Qubit: CLI `memory`; memory pages (same backend).

Voice: `kanban_claim`, `kanban_move`, `kanban_open`.

Limitation: Internal implementation detail, not a user-facing gap: write_txn was attempted live and reverted with root cause (executescript implicitly commits). Reserved for non-executescript writes.

Tests: `tests/test_runtime_tiles.py`, `tests/test_resolution8tf.py`, `tests/test_board_canvas8u.py`, `tests/test_kanban8tf.py`. Owner: qubit-policy. Rollback: nothing installed.

## N8U-MSG-SEND — IMPLEMENTED

User goal: send this message with a fresh confirmation

Upstream: whatsapp/whatsapp-cloud/slack/send (draft/approve/send pipeline with credential + pairing gates)

Qubit: CLI `msg`; Tier-1 msg_* ops (same backend).

Voice: `msg_draft`, `msg_approve`, `msg_send`.

Tests: `tests/test_messaging8tf.py`. Owner: qubit-policy. Rollback: per-message receipts; retry/duplicate-suppression.

## N8U-CODING-VOICE — IMPLEMENTED

User goal: run this coding task by voice

Upstream: voice coding (opencode preview-hash stays CLI/dashboard-only by design; voice dispatches via the agent runtime)

Qubit: CLI `task`, `opencode`; Tier-1 task_run + opencode ops (same backends).

Voice: `task_run`, `task_run_current`, `opencode_preview`, `opencode_run`, `opencode_cancel`, `opencode_retry`, `opencode_recover`, `opencode_receipts`, `opencode_grant`, `opencode_revoke`.

Tests: `tests/test_voice_commands8tf.py`, `tests/test_packet8r.py`, `tests/test_opencode_voice8u.py`. Owner: qubit-policy. Rollback: per-run receipts.
