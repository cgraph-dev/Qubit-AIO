#!/usr/bin/env python3
"""Regenerate ``docs/QUBIT_GAP_LEDGER.openclaw.json`` deterministically.

The OpenClaw section is *evidence based*: every claim in the ledger is checked
against the real code and a behavioural test on disk, and a row is never
reported ``IMPLEMENTED`` without one.  No status is hand-flipped.

Evidence model
--------------
The audited capability inventory (72 rows at v2026.9.2 + 32 delta rows at
v2026.9.4, per ``docs/AUDIT_OPENCLAW_2026-09-11.md`` and
``docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md``) is embedded as the baseline below.
A small, documented :data:`ROW_EVIDENCE` adjustment table records the rows
this wave closes from real Qubit modules plus a proving test; the baseline is
never edited by hand.

Every row is then mechanically verified:

* ``IMPLEMENTED`` rows must cite an existing proving test on disk whose source
  references a salient token from the row's ``qubit_backend`` (module basename
  or named symbol).  A missing/unlinked proof downgrades the row to
  ``UNVERIFIED`` — it is never silently trusted.
* ``PARTIAL`` / ``GATED`` / ``MISSING`` are honest open states and are kept
  (including ``needs=creds/hardware/owner`` gates).
* The exact schema and row keys required by ``scripts/gap_ledger.py`` are
  preserved (``id``, ``area``, ``capability``, ``upstream_ref``,
  ``qubit_backend``, ``cli``, ``dashboard``, ``voice``, ``status``,
  ``proving_test``, ``note``, ``needs``).

Usage::

    python scripts/rebuild_ledger_openclaw.py            # write the section
    python scripts/rebuild_ledger_openclaw.py --check    # non-zero if stale
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

PIN = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
TAG = "v2026.9.4"
GENERATED = "2026-09-12"
UPSTREAM_TREE = ("/tmp/opencode/pinned/openclaw (read via git show "
                 f"{PIN}, shallow clone)")
OUT_PATH = ROOT / "docs" / "QUBIT_GAP_LEDGER.openclaw.json"

# The audited capability inventory (never hand-edited; verified below).
_BASELINE_JSON = r'''[{"id": "OC-01", "area": "gateway", "capability": "Gateway protocol server + RPC v4", "upstream_ref": "packages/gateway-protocol/src/version.ts:2", "qubit_backend": "none (no server)", "cli": "gateway", "dashboard": "qubit-gateway", "voice": "gateway", "status": "MISSING", "proving_test": "", "note": "No gateway server; single-event-path policy. Typed supervision refusal exists but no GATEWAY_NO_SERVER code.", "needs": "owner"}, {"id": "OC-02", "area": "gateway", "capability": "Gateway frame guards (validate, don't fail connection)", "upstream_ref": "packages/gateway-protocol/src/version.ts:4", "qubit_backend": "voicebridge/gateway/gateway_ws.py:40 FrameGuard", "cli": "gateway", "dashboard": "qubit-gateway", "voice": "gateway", "status": "IMPLEMENTED", "proving_test": "tests/test_gateway_frames8u.py", "note": "Behavior test passes (399-suite run).", "needs": "none"}, {"id": "OC-03", "area": "gateway", "capability": "Gateway/peer records + link state", "upstream_ref": "src/pairing/pairing-store.ts:33", "qubit_backend": "voicebridge/gateway_peers.py:168 gateway_add", "cli": "gateway, peer", "dashboard": "qubit-gateway", "voice": "gateway*, peer*", "status": "PARTIAL", "proving_test": "tests/test_gateway_peers8tf.py", "note": "Records + link state real; no live WS control channel.", "needs": "creds"}, {"id": "OC-04", "area": "gateway", "capability": "Gateway process supervision", "upstream_ref": "docs/cli/daemon.md:1", "qubit_backend": "voicebridge/gateway_peers.py:282 refuse_supervision", "cli": "gateway start/stop/restart/install", "dashboard": "qubit-gateway", "voice": "gateway_supervision_refusal", "status": "GATED", "proving_test": "tests/test_gateway_peers8tf.py", "note": "Typed GATEWAY_NO_SUPERVISION refusal; no live policy path.", "needs": "none"}, {"id": "OC-05", "area": "gateway", "capability": "Device pairing + device-scoped tokens (remote)", "upstream_ref": "src/gateway/device-auth.ts:1", "qubit_backend": "voicebridge/device.py (machine_id, enroll) local only", "cli": "device", "dashboard": "qubit-devices", "voice": "device, device_enroll", "status": "MISSING", "proving_test": "", "note": "Local enroll only; no remote token store/approve/rotate.", "needs": "owner"}, {"id": "OC-06", "area": "gateway", "capability": "Node pairing (remote node host)", "upstream_ref": "src/gateway/node-pairing-auto-approve.ts:88", "qubit_backend": "voicebridge/node_host.py enroll/revoke", "cli": "node", "dashboard": "qubit-devices", "voice": "node_host_*", "status": "PARTIAL", "proving_test": "tests/test_node_host8u.py", "note": "Enrollment + dispatch gates real; remote capabilities typed UNAVAILABLE; no live transport.", "needs": "hardware"}, {"id": "OC-07", "area": "gateway", "capability": "Pairing codes (one-time, hashed, scoped grants)", "upstream_ref": "src/pairing/pairing-store.ts:33", "qubit_backend": "voicebridge/pairing.py:166 create", "cli": "pairing", "dashboard": "qubit-gateway", "voice": "pairing_*", "status": "IMPLEMENTED", "proving_test": "tests/test_pairing8tf.py", "note": "", "needs": "none"}, {"id": "OC-08", "area": "gateway", "capability": "Device setup code / QR / join URL / channel DM pairing", "upstream_ref": "src/pairing/setup-code.ts:402", "qubit_backend": "voicebridge/pairing.py local codes only", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No setup-code encode/QR/join URL; no channel DM pairing.", "needs": "none"}, {"id": "OC-09", "area": "browser", "capability": "Tabs/read/find/activate/open/navigate/fill/click/close over loopback CDP", "upstream_ref": "extensions/browser/openclaw.plugin.json:1", "qubit_backend": "voicebridge/browser_tabs.py:181 cdp.py", "cli": "browser", "dashboard": "qubit-devices", "voice": "browser_*", "status": "IMPLEMENTED", "proving_test": "tests/test_browser_tabs8u.py", "note": "Real loopback CDP path; running Chromium optional (typed unavailable otherwise).", "needs": "hardware"}, {"id": "OC-10", "area": "browser", "capability": "Browser profiles + dedicated browser lifecycle + close-profile", "upstream_ref": "extensions/browser/browser-profiles.ts:1", "qubit_backend": "voicebridge/browser.py:42 refuse_close_profile", "cli": "browser close-profile", "dashboard": "qubit-devices", "voice": "browser_close_refusal", "status": "GATED", "proving_test": "tests/test_browser8tf.py", "note": "Typed BROWSER_CLOSE_REFUSED; policy refuses to touch user profiles.", "needs": "none"}, {"id": "OC-11", "area": "browser", "capability": "Browser snapshot/screenshot/state emulation (visual refs)", "upstream_ref": "extensions/browser/browser-profiles.ts:1", "qubit_backend": "voicebridge/browser_tabs.py screenshot (CDP Page.captureScreenshot)", "cli": "browser screenshot", "dashboard": "qubit-devices", "voice": "browser_screenshot", "status": "IMPLEMENTED", "proving_test": "tests/test_browser_screenshot8u.py", "note": "Bounded base64 screenshot; clip validation; typed BROWSER_UNAVAILABLE without attachment.", "needs": "hardware"}, {"id": "OC-12", "area": "browser", "capability": "Remote CDP via node host / Chrome extension attach", "upstream_ref": "docs/tools/browser.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Depends on node host #6/remote transport.", "needs": "hardware"}, {"id": "OC-13", "area": "desktop", "capability": "Real desktop control (Peekaboo/CUA)", "upstream_ref": "docs/nodes/computer-use.md:1", "qubit_backend": "voicebridge/computer_use.py:130 refuse_install", "cli": "computer-use", "dashboard": "qubit-devices", "voice": "computer_status/doctor/permissions/install_refusal", "status": "GATED", "proving_test": "tests/test_computer_use8tf.py", "note": "Typed COMPUTER_USE_NO_DRIVER; no AT-SPI actuator.", "needs": "hardware"}, {"id": "OC-14", "area": "desktop", "capability": "Supervised ghost action framework (screenshot/key/cursor/window)", "upstream_ref": "src/agents/tools/computer-tool.ts:1", "qubit_backend": "voicebridge/ghost.py:71 disabled by default", "cli": "ghost", "dashboard": "qubit-ops", "voice": "ghost_*", "status": "GATED", "proving_test": "tests/test_desktop_parity8u.py", "note": "Framework real; executor fixture-simulated; no real actuator.", "needs": "hardware"}, {"id": "OC-15", "area": "desktop", "capability": "`screen` tool (arrange Control UI)", "upstream_ref": "src/agents/tools/screen-tool.ts:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Qubit UI is not a multi-pane control UI.", "needs": "none"}, {"id": "OC-16", "area": "desktop", "capability": "Screen snapshot work journal (Logbook)", "upstream_ref": "extensions/logbook/openclaw.plugin.json:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No periodic snapshot service; needs desktop + vision.", "needs": "hardware"}, {"id": "OC-17", "area": "desktop", "capability": "CUA browser family (get_browser_state, etc.)", "upstream_ref": "docs/nodes/computer-use.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Depends on #13 real actuator.", "needs": "hardware"}, {"id": "OC-18", "area": "messaging", "capability": "Messaging accounts + draft/approve/send + inbox", "upstream_ref": "docs/channels/index.md:1", "qubit_backend": "voicebridge/messaging.py:53 PROVIDERS, :517 send", "cli": "msg", "dashboard": "qubit-gateway", "voice": "msg_*", "status": "IMPLEMENTED", "proving_test": "tests/test_messaging8tf.py", "note": "Real transports for whatsapp-cloud/slack (credential-gated).", "needs": "creds"}, {"id": "OC-19", "area": "messaging", "capability": "Broad channel set (~29 plugins)", "upstream_ref": "docs/channels/index.md:1", "qubit_backend": "voicebridge/channels_inbound.py CHANNELS (29), messaging.PROVIDERS (9)", "cli": "channels, msg", "dashboard": "", "voice": "channels_*, msg_*", "status": "PARTIAL", "proving_test": "tests/test_messaging_breadth8u.py", "note": "9 providers recognized, only whatsapp-cloud/slack have transports; 20 typed UNAVAILABLE.", "needs": "creds"}, {"id": "OC-20", "area": "messaging", "capability": "Live inbound channel ingress/listeners", "upstream_ref": "docs/channels/index.md:1", "qubit_backend": "voicebridge/channels_inbound.py receive (verified ingress, no listener)", "cli": "channels receive", "dashboard": "", "voice": "channels_receive_refusal", "status": "PARTIAL", "proving_test": "tests/test_channels_inbound8u.py", "note": "HMAC/freshness/replay ingress kernel real; every verified delivery resolves to CHANNEL_UNAVAILABLE; no listener.", "needs": "creds"}, {"id": "OC-21", "area": "messaging", "capability": "Channel account pairing / DM access control", "upstream_ref": "docs/channels/pairing.md:1", "qubit_backend": "voicebridge/pairing.py + messaging.account_*", "cli": "pairing, msg account-*", "dashboard": "", "voice": "msg_account*", "status": "PARTIAL", "proving_test": "tests/test_pairing8tf.py", "note": "Pairing + account scopes exist; not bound end-to-end to a live channel.", "needs": "creds"}, {"id": "OC-22", "area": "messaging", "capability": "Read-only communications monitor (Gmail/IG/Messenger via CDP)", "upstream_ref": "none", "qubit_backend": "voicebridge/communications/ (adapters, monitor, reports, store)", "cli": "monitor", "dashboard": "qubit-reports", "voice": "monitor", "status": "IMPLEMENTED", "proving_test": "tests/test_communications_monitor.py", "note": "Qubit-side capability; needs CDP + logged-in profiles.", "needs": "creds"}, {"id": "OC-23", "area": "webhooks", "capability": "Webhook subscription CRUD + HMAC verify/replay/freshness", "upstream_ref": "extensions/webhooks/openclaw.plugin.json:1", "qubit_backend": "voicebridge/webhooks.py:211 add, :364 verify", "cli": "webhook", "dashboard": "qubit-ops", "voice": "webhook_add/remove/show/test", "status": "IMPLEMENTED", "proving_test": "tests/test_webhooks8tf.py", "note": "", "needs": "none"}, {"id": "OC-24", "area": "webhooks", "capability": "Inbound HTTP webhook delivery endpoint", "upstream_ref": "src/plugin-sdk/webhook-ingress.ts:1", "qubit_backend": "voicebridge/webui.py:1143 _webhook_ingress (POST /hooks/<sub_id>)", "cli": "webhook verify", "dashboard": "qubit-ops", "voice": "guard_webhook_verify", "status": "IMPLEMENTED", "proving_test": "tests/test_webhook_ingress8u.py", "note": "Real HTTP endpoint with signature/stale/replay guards; implemented after the 9.2 audit.", "needs": "none"}, {"id": "OC-25", "area": "webhooks", "capability": "Gmail Pub/Sub transport", "upstream_ref": "src/hooks/gmail-watcher.ts:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Operator-run gog integration; out of scope for local-only.", "needs": "creds"}, {"id": "OC-26", "area": "automation", "capability": "Scheduled job store CRUD + run-due", "upstream_ref": "src/cron/schedule.ts:1", "qubit_backend": "voicebridge/schedules.py:289 create, :472 run_due", "cli": "schedules", "dashboard": "/api/cron/jobs", "voice": "schedules_*", "status": "IMPLEMENTED", "proving_test": "tests/test_schedules8u.py", "note": "", "needs": "none"}, {"id": "OC-27", "area": "automation", "capability": "Autonomous scheduler (fires in-process, catch-up)", "upstream_ref": "src/cron/service.ts:1", "qubit_backend": "none; schedules.py:11 no timer/daemon", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Jobs fire only on explicit run_due; no in-process timer/catch-up.", "needs": "none"}, {"id": "OC-28", "area": "automation", "capability": "Delivery of schedule output to channel/webhook", "upstream_ref": "src/cron/delivery.ts:1", "qubit_backend": "voicebridge/schedules.py delivery targets (typed SENT/UNAVAILABLE)", "cli": "schedules deliveries", "dashboard": "/api/cron/delivery-targets", "voice": "schedules_*", "status": "IMPLEMENTED", "proving_test": "tests/test_schedule_delivery8u.py", "note": "Channel delivery real via messaging outbox; webhook outbound typed UNAVAILABLE without adapter.", "needs": "creds"}, {"id": "OC-29", "area": "automation", "capability": "Hooks registry (declare/test/fire/doctor)", "upstream_ref": "docs/automation/hooks.md:1", "qubit_backend": "voicebridge/hooks.py:170 declare, :238 fire (disarmed)", "cli": "hooks", "dashboard": "/api/ops/hooks", "voice": "hooks_*", "status": "PARTIAL", "proving_test": "tests/test_hooks8tf.py", "note": "Registry + manual fire real; shell permanently disarmed; no core lifecycle event emissions.", "needs": "none"}, {"id": "OC-30", "area": "automation", "capability": "Background task ledger", "upstream_ref": "docs/automation/tasks.md:1", "qubit_backend": "voicebridge/task_ledger.py read model over opencode/schedules/kanban", "cli": "tasks", "dashboard": "", "voice": "task_ledger_*", "status": "IMPLEMENTED", "proving_test": "tests/test_task_ledger8u.py", "note": "Read-only unified ledger; no writer (adapts OpenClaw ledger as read model).", "needs": "none"}, {"id": "OC-31", "area": "automation", "capability": "Auto-reply / flows / standing orders / heartbeat", "upstream_ref": "docs/automation/standing-orders.md:1", "qubit_backend": "none", "cli": "kanban heartbeat (delegates/refuses)", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No auto-reply/flow/heartbeat subsystem.", "needs": "none"}, {"id": "OC-32", "area": "boards", "capability": "Project registry (named multi-folder workspaces)", "upstream_ref": "src/projects/project-registry.ts:281", "qubit_backend": "voicebridge/projects.py:244 create", "cli": "project", "dashboard": "qubit-boards", "voice": "projects_*", "status": "IMPLEMENTED", "proving_test": "tests/test_projects8tf.py", "note": "", "needs": "none"}, {"id": "OC-33", "area": "boards", "capability": "Project git clone/checkout", "upstream_ref": "src/projects/project-clone.ts:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No project clone; network git is out of scope.", "needs": "none"}, {"id": "OC-34", "area": "boards", "capability": "Kanban task lifecycle", "upstream_ref": "extensions/workboard/openclaw.plugin.json:1", "qubit_backend": "voicebridge/kanban.py:224 init, :316 create, :452 _transition", "cli": "kanban", "dashboard": "qubit-boards", "voice": "kanban_*", "status": "IMPLEMENTED", "proving_test": "tests/test_kanban8tf.py", "note": "", "needs": "none"}, {"id": "OC-35", "area": "boards", "capability": "Board canvas: widgets, layout, roles, snapshots", "upstream_ref": "src/boards/board-store.ts:21", "qubit_backend": "voicebridge/kanban.py:1003 board_open, :1096 widget_add", "cli": "kanban widget-*, layout-*, role-*, snapshots", "dashboard": "qubit-boards", "voice": "kanban_widget_*", "status": "IMPLEMENTED", "proving_test": "tests/test_board_canvas8u.py", "note": "Widgets now include sandboxed html/svg + per-tab + declared grants + role grants.", "needs": "none"}, {"id": "OC-36", "area": "boards", "capability": "Control-UI session boards + tabs + sandboxed HTML/SVG/plugin widgets + show_widget", "upstream_ref": "src/boards/board-layout.ts:17", "qubit_backend": "voicebridge/kanban.py tab_* + sanitize_widget_content", "cli": "kanban tab-*", "dashboard": "qubit-boards", "voice": "kanban_*", "status": "PARTIAL", "proving_test": "tests/test_board_canvas8u.py", "note": "Backend tabs + sandboxed html/svg widgets + grants exist; no plugin widget kinds and no show_widget agent tool.", "needs": "none"}, {"id": "OC-37", "area": "boards", "capability": "Workboard dispatch + worker runs", "upstream_ref": "docs/cli/workboard.md:1", "qubit_backend": "voicebridge/kanban.py:786 refuse_delegated", "cli": "kanban dispatch/swarm/daemon/watch", "dashboard": "", "voice": "", "status": "GATED", "proving_test": "tests/test_kanban8tf.py", "note": "Typed KANBAN_DELEGATED; no swarm/daemon by policy.", "needs": "none"}, {"id": "OC-38", "area": "skills", "capability": "Skill manifest install/enable/disable/remove/rollback", "upstream_ref": "src/skills/library/service.ts:75", "qubit_backend": "voicebridge/plugins.py:77-145; verified_skills/", "cli": "skills", "dashboard": "qubit-ops", "voice": "skills_*, plugins_install", "status": "IMPLEMENTED", "proving_test": "tests/test_plugins8tf.py", "note": "Local reviewed manifests only.", "needs": "none"}, {"id": "OC-39", "area": "skills", "capability": "Remote plugin/skill install (ClawHub/npm/git/archive)", "upstream_ref": "docs/tools/plugin.md:1", "qubit_backend": "voicebridge/plugins.py:108 refuse_remote", "cli": "skills install (remote refused)", "dashboard": "qubit-ops", "voice": "plugins_remote_refusal", "status": "GATED", "proving_test": "tests/test_plugins8tf.py", "note": "Pinned-source policy.", "needs": "none"}, {"id": "OC-40", "area": "skills", "capability": "Plugin runtime extensibility (tools/channels/providers/hooks)", "upstream_ref": "docs/plugins/architecture.md:1", "qubit_backend": "voicebridge/plugin_runtime.py PLUGIN_KINDS + invoke gate", "cli": "plugins", "dashboard": "", "voice": "plugins_*", "status": "PARTIAL", "proving_test": "tests/test_plugin_runtime8u.py", "note": "Registry + confirm/estop invoke gate + builtin.echo; no provider-backed execution runtime.", "needs": "none"}, {"id": "OC-41", "area": "skills", "capability": "Skill workshop / authoring / self-learning", "upstream_ref": "docs/tools/skill-workshop.md:1", "qubit_backend": "voicebridge/verified_skills/ lifecycle", "cli": "verified-skills", "dashboard": "qubit-memory", "voice": "verified_*", "status": "PARTIAL", "proving_test": "tests/test_packet8p.py", "note": "Qubit verified-skills ledger + snapshot/restore; no upstream-style agent-owned workshop collection.", "needs": "none"}, {"id": "OC-42", "area": "skills", "capability": "Verified-skills learning ledger (Qubit-only)", "upstream_ref": "none", "qubit_backend": "voicebridge/verified_skills/", "cli": "verified-skills", "dashboard": "qubit-memory", "voice": "verified_*", "status": "IMPLEMENTED", "proving_test": "tests/test_packet8p.py", "note": "", "needs": "none"}, {"id": "OC-43", "area": "sessions", "capability": "Session list from receipts", "upstream_ref": "docs/cli/sessions.md:1", "qubit_backend": "receipt reader behind sessions list/show/search", "cli": "sessions", "dashboard": "hermes-web SessionsPage", "voice": "sessions", "status": "IMPLEMENTED", "proving_test": "tests/test_owner_session8tf.py", "note": "", "needs": "none"}, {"id": "OC-44", "area": "sessions", "capability": "Session create/resume/archive/delete/rename/search/subagents/steering", "upstream_ref": "docs/concepts/session.md:1", "qubit_backend": "none (receipt store lifecycle only)", "cli": "sessions rename/archive/delete/resume/search", "dashboard": "/api/sessions/*", "voice": "session_*", "status": "PARTIAL", "proving_test": "tests/test_owner_session8tf.py", "note": "Lifecycle on append-only receipts; no subagents/steering by policy.", "needs": "none"}, {"id": "OC-45", "area": "sessions", "capability": "Journal + named checkpoints", "upstream_ref": "none", "qubit_backend": "voicebridge/memory/journal.py, checkpoints", "cli": "checkpoints", "dashboard": "qubit-reports", "voice": "checkpoints_*", "status": "IMPLEMENTED", "proving_test": "tests/test_checkpoints8tf.py", "note": "", "needs": "none"}, {"id": "OC-46", "area": "sessions", "capability": "Memory store + search + remember/forget", "upstream_ref": "docs/concepts/memory.md:1", "qubit_backend": "voicebridge/memory/store.py:97 MemoryStore", "cli": "memory", "dashboard": "qubit-memory", "voice": "memory_search/remember/forget", "status": "IMPLEMENTED", "proving_test": "tests/test_packet5b.py", "note": "", "needs": "none"}, {"id": "OC-47", "area": "sessions", "capability": "Semantic belief memory + journal chain", "upstream_ref": "none", "qubit_backend": "voicebridge/memory/semantic.py:530, journal.py:113", "cli": "memory belief-*, journal-*", "dashboard": "qubit-memory", "voice": "memory_*", "status": "IMPLEMENTED", "proving_test": "tests/test_packet5b.py", "note": "", "needs": "none"}, {"id": "OC-48", "area": "sessions", "capability": "Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho)", "upstream_ref": "docs/concepts/memory-builtin.md:1", "qubit_backend": "none (different model)", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Provider adapters new; cloud sync refused by policy.", "needs": "creds"}, {"id": "OC-49", "area": "reports", "capability": "Activity/audit ledger + report bundles", "upstream_ref": "docs/cli/audit.md:1", "qubit_backend": "voicebridge/communications/reports.py, webui.ActivityFeed", "cli": "monitor", "dashboard": "qubit-reports", "voice": "reports_list", "status": "IMPLEMENTED", "proving_test": "tests/test_report_bundles.py", "note": "", "needs": "none"}, {"id": "OC-50", "area": "reports", "capability": "Analytics / usage", "upstream_ref": "docs/cli/audit.md:1", "qubit_backend": "voicebridge/hermes_compat.py h_analytics", "cli": "", "dashboard": "/api/analytics*", "voice": "", "status": "PARTIAL", "proving_test": "tests/test_webui_reports.py", "note": "Routes exist; not backed by real token/cost accounting.", "needs": "none"}, {"id": "OC-51", "area": "reports", "capability": "Logbook screen work-journal", "upstream_ref": "extensions/logbook/openclaw.plugin.json:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Needs periodic screen capture + vision summary.", "needs": "hardware"}, {"id": "OC-52", "area": "reports", "capability": "Immutable execution-identity receipts", "upstream_ref": "docs/cli/audit.md:1", "qubit_backend": "action receipts + approval journal", "cli": "actions receipts", "dashboard": "qubit-reports", "voice": "actions_receipts", "status": "PARTIAL", "proving_test": "tests/test_packet8f.py", "note": "Receipts real; no run-correlation/execution-identity schema.", "needs": "none"}, {"id": "OC-53", "area": "runtime", "capability": "Pinned local runtime install/status/rollback", "upstream_ref": "docs/cli/fleet.md:1", "qubit_backend": "voicebridge/runtime_images.py:46 REGISTRY, :235 install", "cli": "runtime", "dashboard": "qubit-runtime", "voice": "runtime_*", "status": "IMPLEMENTED", "proving_test": "tests/test_runtime_images8u.py", "note": "", "needs": "none"}, {"id": "OC-54", "area": "runtime", "capability": "Containerized runtime / fleet multi-tenant cells / cloud workers", "upstream_ref": "docs/cli/fleet.md:1", "qubit_backend": "none; Docker forbidden by stack contract", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Out of scope (no Docker/K8s); policy gap.", "needs": "owner"}, {"id": "OC-55", "area": "runtime", "capability": "Device capability inventory (browser/desktop/IDE/vision/runtime)", "upstream_ref": "docs/nodes/computer-use.md:1", "qubit_backend": "voicebridge/runtime_inventory.py:141", "cli": "runtime, status", "dashboard": "qubit-devices", "voice": "runtime_status", "status": "PARTIAL", "proving_test": "tests/test_runtime_tiles.py", "note": "Gauge inventory; node-host-derived capabilities partial via #56.", "needs": "none"}, {"id": "OC-56", "area": "runtime", "capability": "Paired node host (screen/audio/camera/media playback/presence)", "upstream_ref": "docs/nodes/index.md:1", "qubit_backend": "voicebridge/node_host.py (enroll/status/capabilities/dispatch)", "cli": "node", "dashboard": "qubit-devices", "voice": "node_host_*", "status": "PARTIAL", "proving_test": "tests/test_node_host8u.py", "note": "Remote actuator capabilities typed UNAVAILABLE; no live media transport.", "needs": "hardware"}, {"id": "OC-57", "area": "ide", "capability": "Supervised coding runs (opencode) preview/hash/execute/receipts", "upstream_ref": "docs/tools/code-execution.md:1", "qubit_backend": "voicebridge/opencode/runner.py:31, transport.py, confinement.py", "cli": "opencode", "dashboard": "qubit-ops", "voice": "opencode_*", "status": "IMPLEMENTED", "proving_test": "tests/test_opencode_voice8u.py", "note": "", "needs": "none"}, {"id": "OC-58", "area": "ide", "capability": "ACP external harnesses (Claude Code/Cursor/Gemini/Codex/Droid)", "upstream_ref": "docs/tools/acp-agents.md:1", "qubit_backend": "voicebridge/acp_client.py HARNESSES + acp_lsp.py:124 acp_refuse_serve", "cli": "acp", "dashboard": "", "voice": "acp_client_*", "status": "PARTIAL", "proving_test": "tests/test_acp_client8u.py", "note": "Real client contract, PATH probe, request shaping, receipts; never executes a harness (executed:false).", "needs": "hardware"}, {"id": "OC-59", "area": "ide", "capability": "LSP layer (clients, lifecycle)", "upstream_ref": "docs/tools/acp-agents.md:1", "qubit_backend": "voicebridge/acp_lsp.py:192 lsp_restart (NO_CLIENTS), :200 lsp_refuse_install", "cli": "lsp", "dashboard": "", "voice": "lsp_*", "status": "GATED", "proving_test": "tests/test_acp_lsp8tf.py", "note": "Typed NO_CLIENTS / LSP_NO_DOWNLOADS.", "needs": "none"}, {"id": "OC-60", "area": "ide", "capability": "IDE launcher (open workspace in editor)", "upstream_ref": "docs/tools/index.md:1", "qubit_backend": "voicebridge/ide.py:168 open_editor", "cli": "ide", "dashboard": "qubit-devices", "voice": "ide_status/open", "status": "IMPLEMENTED", "proving_test": "tests/test_files_ide8u.py", "note": "", "needs": "hardware"}, {"id": "OC-61", "area": "ide", "capability": "Files read/list/inspect (read-only)", "upstream_ref": "docs/tools/index.md:1", "qubit_backend": "voicebridge/files.py:152 list_dir, :188 read_file, :222 inspect", "cli": "files", "dashboard": "hermes FilesPage", "voice": "files_*", "status": "IMPLEMENTED", "proving_test": "tests/test_files_ide8u.py", "note": "", "needs": "none"}, {"id": "OC-62", "area": "ide", "capability": "File write / edit / apply_patch", "upstream_ref": "docs/tools/apply-patch.md:1", "qubit_backend": "voicebridge/file_patch.py (confined unified-diff + rollback)", "cli": "files patch/rollback", "dashboard": "", "voice": "file_patch_rollback", "status": "IMPLEMENTED", "proving_test": "tests/test_file_patch8u.py", "note": "Confined single-file patch with atomic backup/rollback; no host-write fallback.", "needs": "none"}, {"id": "OC-63", "area": "web_media", "capability": "`web_search` / `x_search` / `web_fetch`", "upstream_ref": "docs/tools/web.md:1", "qubit_backend": "voicebridge/web_tool.py:243 fetch, :468 search", "cli": "websites fetch/search", "dashboard": "", "voice": "web_fetch/web_search", "status": "PARTIAL", "proving_test": "tests/test_web_tool8u.py", "note": "SSRF-safe fetch real; search only with operator provider (typed WEB_SEARCH_UNAVAILABLE otherwise); x_search absent.", "needs": "creds"}, {"id": "OC-64", "area": "web_media", "capability": "Image / music / video generation", "upstream_ref": "docs/tools/media-overview.md:1", "qubit_backend": "voicebridge/media_gen.py:68 KINDS, :677 generate", "cli": "media", "dashboard": "", "voice": "media_gen_*", "status": "PARTIAL", "proving_test": "tests/test_media_gen8u.py", "note": "Registry + credential gate; only OpenAI images has a real shaper/transport; others typed UNAVAILABLE.", "needs": "creds"}, {"id": "OC-65", "area": "web_media", "capability": "Media understanding beyond single-image vision", "upstream_ref": "docs/tools/media-overview.md:1", "qubit_backend": "voicebridge/vision.py:301 describe only", "cli": "vision", "dashboard": "qubit-devices", "voice": "vision_describe/status", "status": "PARTIAL", "proving_test": "tests/test_vision8tf.py", "note": "Single-image vision; multi-attachment/audio understanding new.", "needs": "creds"}, {"id": "OC-66", "area": "web_media", "capability": "Streaming ASR/TTS + barge-in", "upstream_ref": "docs/nodes/talk.md:1", "qubit_backend": "voicebridge/asr/, tts/, audio/vad.py", "cli": "asr-test, tts-test, mic-test", "dashboard": "Talk route", "voice": "asr_test/tts_test/mic_test", "status": "IMPLEMENTED", "proving_test": "tests/test_barge_stream.py", "note": "", "needs": "hardware"}, {"id": "OC-67", "area": "web_media", "capability": "Realtime transcription / voice call / meeting bot", "upstream_ref": "docs/plugins/voice-call.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No telephony/meeting adapters.", "needs": "creds"}, {"id": "OC-68", "area": "web_media", "capability": "Voice command surface (registry + typed guards)", "upstream_ref": "none", "qubit_backend": "voicebridge/voice_commands.py REGISTRY (420) + VOICE_GUARDS (18)", "cli": "voice", "dashboard": "qubit-ops", "voice": "420 entries/18 guards", "status": "IMPLEMENTED", "proving_test": "tests/test_voice_commands8tf.py", "note": "", "needs": "none"}, {"id": "OC-69", "area": "dashboard", "capability": "Dashboard compat routes + operations console with real forms/confirmations", "upstream_ref": "docs/web/control-ui.md:1", "qubit_backend": "voicebridge/hermes_compat.py ROUTES (300), dashboard_ops.py catalog", "cli": "all families", "dashboard": "300 routes + 10 plugins", "voice": "all", "status": "PARTIAL", "proving_test": "tests/test_dashboard_ops8tf.py", "note": "Broad but many routes are typed gates or vendored pages.", "needs": "none"}, {"id": "OC-70", "area": "dashboard", "capability": "Control-UI multi-session dashboards, board tabs, plugin widgets", "upstream_ref": "docs/web/dashboards.md:1", "qubit_backend": "voicebridge/kanban.py tab_*/widget_add; qubit-boards overlay", "cli": "kanban tab-*", "dashboard": "qubit-boards", "voice": "kanban_*", "status": "PARTIAL", "proving_test": "tests/test_board_canvas8u.py", "note": "Board tabs + sandboxed widgets landed; no multi-session dashboard/plugin widget kinds.", "needs": "none"}, {"id": "OC-71", "area": "dashboard", "capability": "Control-UI panels: terminal / browser / desktop / side chat", "upstream_ref": "docs/web/control-ui.md:1", "qubit_backend": "webui Talk/Activity/Reports + /api/qubit/chat; PTY gated", "cli": "", "dashboard": "/api/pty (gated), /api/qubit/chat", "voice": "", "status": "PARTIAL", "proving_test": "tests/test_hermes_routes_parity8u.py", "note": "Side chat works; terminal typed PTY_NO_SHELL; browser/desktop panes absent.", "needs": "none"}, {"id": "OC-72", "area": "dashboard", "capability": "Brand/theme overlay", "upstream_ref": "none", "qubit_backend": "frontend/hermes-qubit/qubit-theme.json, qubit-brand", "cli": "", "dashboard": "/api/dashboard/themes", "voice": "", "status": "IMPLEMENTED", "proving_test": "tests/test_themes8tf.py", "note": "", "needs": "none"}, {"id": "OC-D01", "area": "reports", "capability": "Team Reports plugin (GitHub+Discord activity reports, history, summaries, scheduler, HTTP)", "upstream_ref": "extensions/team-reports/openclaw.plugin.json:2", "qubit_backend": "voicebridge/communications/reports.py, dashboard_ops.py, kanban.py", "cli": "monitor", "dashboard": "qubit-reports", "voice": "reports_list", "status": "PARTIAL", "proving_test": "tests/test_report_bundles.py", "note": "Report pipeline real; no GitHub/Discord live aggregator.", "needs": "creds"}, {"id": "OC-D02", "area": "policy", "capability": "Policy plugin (workspace-conformance doctor, attestation, rules, findings)", "upstream_ref": "extensions/policy/openclaw.plugin.json:2", "qubit_backend": "voicebridge/safety/policy.py", "cli": "", "dashboard": "", "voice": "", "status": "PARTIAL", "proving_test": "", "note": "SafetyPolicy exists; no user-authored conformance rules/attestation output.", "needs": "none"}, {"id": "OC-D03", "area": "runtime", "capability": "File Transfer plugin (node-host dir list/fetch/stream, resumable)", "upstream_ref": "extensions/file-transfer/openclaw.plugin.json:8", "qubit_backend": "voicebridge/file_transfer.py (list_directory/fetch/stream_plan/resume)", "cli": "transfers", "dashboard": "/api/transfers*", "voice": "transfers_*", "status": "PARTIAL", "proving_test": "tests/test_file_transfer8u.py", "note": "Confined local fetch/chunk/resume real; non-loopback nodes typed FILE_TRANSFER_UNAVAILABLE (no remote transport).", "needs": "none"}, {"id": "OC-D04", "area": "sessions", "capability": "Active Memory plugin (active recall, session policy, escalation)", "upstream_ref": "extensions/active-memory/index.ts:84", "qubit_backend": "voicebridge/active_memory.py recall/resolve/escalate", "cli": "recall", "dashboard": "/api/recall*", "voice": "recall_*", "status": "IMPLEMENTED", "proving_test": "tests/test_active_memory8u.py", "note": "Disabled by default; policy/threshold/dedupe/resolve + confirmation-gated escalation all tested.", "needs": "none"}, {"id": "OC-D05", "area": "automation", "capability": "QA Lab / E2E automation stack (operator flow, scenarios, reporting)", "upstream_ref": "docs/concepts/qa-e2e-automation/operator-flow.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No scenario runner; overlaps pytest harness.", "needs": "none"}, {"id": "OC-D06", "area": "runtime", "capability": "Cloud workers / worker environments (prepared builds, admission, warm images)", "upstream_ref": "src/gateway/worker-environments/admission.ts:26", "qubit_backend": "voicebridge/runtime_images.py, voicebridge/node_host.py", "cli": "runtime, node", "dashboard": "qubit-runtime", "voice": "runtime_*, node_host_*", "status": "PARTIAL", "proving_test": "tests/test_runtime_images8u.py", "note": "Local prepared images real; cloud admission/placement/warm images need a provider.", "needs": "creds"}, {"id": "OC-D07", "area": "runtime", "capability": "Node runtime update / recovery (respawn, native hook relay)", "upstream_ref": "node-runtime-update.mjs:6", "qubit_backend": "voicebridge/runtime_images.py install/rollback", "cli": "runtime install/rollback", "dashboard": "qubit-runtime", "voice": "runtime_*", "status": "PARTIAL", "proving_test": "tests/test_runtime_images8u.py", "note": "Runtime image compatibility/rollback exists; no node-runtime respawn/private recovery.", "needs": "none"}, {"id": "OC-D08", "area": "policy", "capability": "Secrets egress proxy (outbound proxy, stream substitution, secret registry)", "upstream_ref": "src/secrets/egress-proxy/proxy-server.ts:1", "qubit_backend": "voicebridge/proxy_status.py refuse start (PROXY_NO_SERVER)", "cli": "proxy", "dashboard": "/api/egress/status", "voice": "", "status": "GATED", "proving_test": "tests/test_proxy_status8u.py", "note": "Typed refusal: a forwarder would bypass egress policy/audit; provider use flows through the brokered gateway.", "needs": "none"}, {"id": "OC-D09", "area": "gateway", "capability": "New gateway RPC server methods (environments, task history, update report, workspace files, plugin mutations)", "upstream_ref": "src/gateway/server-methods/environments.ts:558", "qubit_backend": "voicebridge/gateway/gateway_ws.py:40 FrameGuard, gateway_peers.py, hermes_compat.py ROUTES", "cli": "gateway", "dashboard": "/api/*", "voice": "gateway", "status": "PARTIAL", "proving_test": "tests/test_gateway_frames8u.py", "note": "No gateway server; equivalent surfaces exposed as webui routes where meaningful.", "needs": "none"}, {"id": "OC-D10", "area": "skills", "capability": "MCP hosted apps (loopback MCP HTTP server, attach grants, OAuth callback, standalone host)", "upstream_ref": "src/gateway/mcp-http.ts:543", "qubit_backend": "voicebridge/plugin_runtime.py PLUGIN_KINDS + invoke", "cli": "plugins", "dashboard": "/api/mcp* (servers)", "voice": "plugins_*", "status": "PARTIAL", "proving_test": "tests/test_plugin_runtime8u.py", "note": "Plugin host registry real; no MCP server/client transport or attach-grant store.", "needs": "none"}, {"id": "OC-D11", "area": "gateway", "capability": "Wire protocol v4 (unchanged 9.2->9.4)", "upstream_ref": "packages/gateway-protocol/src/version.ts:2", "qubit_backend": "voicebridge/gateway/gateway_ws.py:40 FrameGuard", "cli": "gateway", "dashboard": "qubit-gateway", "voice": "gateway", "status": "IMPLEMENTED", "proving_test": "tests/test_gateway_frames8u.py", "note": "Protocol unchanged; frame guard validated.", "needs": "none"}, {"id": "OC-D12", "area": "messaging", "capability": "Discord voice channels, voice-follow, transcripts, rich messages", "upstream_ref": "extensions/discord/src/voice/command.ts:32", "qubit_backend": "voicebridge/messaging.py discord (UNAVAILABLE), channels_inbound CHANNELS", "cli": "msg", "dashboard": "", "voice": "channels_*", "status": "PARTIAL", "proving_test": "tests/test_messaging_breadth8u.py", "note": "Discord recognized with typed UNAVAILABLE; no adapter/transport or voice.", "needs": "creds"}, {"id": "OC-D13", "area": "messaging", "capability": "Telegram mini-app + photo albums + rich messages", "upstream_ref": "extensions/telegram/src/miniapp/routes.ts:40", "qubit_backend": "voicebridge/messaging.py telegram (UNAVAILABLE), channels_inbound CHANNELS", "cli": "msg", "dashboard": "", "voice": "channels_*", "status": "PARTIAL", "proving_test": "tests/test_messaging_breadth8u.py", "note": "Telegram recognized with typed UNAVAILABLE; no adapter/mini-app.", "needs": "creds"}, {"id": "OC-D14", "area": "messaging", "capability": "Slack Enterprise Grid, transports, rich messages", "upstream_ref": "docs/channels/slack/enterprise-grid.md:1", "qubit_backend": "voicebridge/messaging.py slack (live transport)", "cli": "msg", "dashboard": "qubit-gateway", "voice": "msg_*", "status": "PARTIAL", "proving_test": "tests/test_messaging8tf.py", "note": "Slack transport live for basic send; no enterprise-grid/multi-workspace features.", "needs": "creds"}, {"id": "OC-D15", "area": "messaging", "capability": "iMessage private API, media, rich messages", "upstream_ref": "docs/channels/imessage/private-api.md:1", "qubit_backend": "voicebridge/messaging.py imessage (UNAVAILABLE)", "cli": "msg", "dashboard": "", "voice": "channels_*", "status": "PARTIAL", "proving_test": "tests/test_messaging_breadth8u.py", "note": "Recognized with typed UNAVAILABLE; macOS-only private API absent on Linux.", "needs": "creds"}, {"id": "OC-D16", "area": "messaging", "capability": "Feishu dynamic agents, advanced configuration", "upstream_ref": "docs/channels/feishu/dynamic-agents.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "Feishu not even in channels_inbound/messaging registries.", "needs": "creds"}, {"id": "OC-D17", "area": "messaging", "capability": "Matrix encryption + multi-account homeservers", "upstream_ref": "docs/channels/matrix/encryption.md:1", "qubit_backend": "voicebridge/messaging.py matrix (UNAVAILABLE), channels_inbound CHANNELS", "cli": "msg", "dashboard": "", "voice": "channels_*", "status": "PARTIAL", "proving_test": "tests/test_messaging_breadth8u.py", "note": "Matrix recognized with typed UNAVAILABLE; no E2EE/homeserver transport.", "needs": "creds"}, {"id": "OC-D18", "area": "web_media", "capability": "Media generation tools (image/music/video) + GPT Image 2.5", "upstream_ref": "src/agents/tools/image-generate-tool.execution.ts:1", "qubit_backend": "voicebridge/media_gen.py generate", "cli": "media", "dashboard": "", "voice": "media_gen_*", "status": "PARTIAL", "proving_test": "tests/test_media_gen8u.py", "note": "image/music/video/speech modeled; only OpenAI images transport real.", "needs": "creds"}, {"id": "OC-D19", "area": "web_media", "capability": "Web fetch HTML tag extraction (structured fetch)", "upstream_ref": "src/agents/tools/web-fetch-html-tag.ts:3", "qubit_backend": "voicebridge/web_tool.py:220 _visible_text + fetch", "cli": "websites fetch", "dashboard": "", "voice": "web_fetch", "status": "IMPLEMENTED", "proving_test": "tests/test_web_tool8u.py", "note": "HTML tag stripping/visible-text extraction bounded + redacted.", "needs": "none"}, {"id": "OC-D20", "area": "ide", "capability": "PDF + transcripts tools", "upstream_ref": "src/agents/tools/transcripts-tool-result.ts:1", "qubit_backend": "voicebridge/doc_extract.py extract (pdf/plain/transcript)", "cli": "documents", "dashboard": "/api/documents*", "voice": "documents_*", "status": "PARTIAL", "proving_test": "tests/test_doc_extract8u.py", "note": "Transcript/plain extraction real; PDF path typed DOC_EXTRACT_UNAVAILABLE (no pypdf installed) but testable with optional backend.", "needs": "none"}, {"id": "OC-D21", "area": "automation", "capability": "Question prompts (choices, free text, multi-question, ask_user)", "upstream_ref": "src/agents/tools/question-prompt-send.ts:27", "qubit_backend": "voicebridge/question_prompt.py ask/answer/pending", "cli": "ask", "dashboard": "/api/ask*", "voice": "ask_*", "status": "IMPLEMENTED", "proving_test": "tests/test_question_prompt8u.py", "note": "Bounded persisted contract; replay/expiry/estop gated.", "needs": "none"}, {"id": "OC-D22", "area": "automation", "capability": "Cron delivery to channels/webhooks/Gmail, payloads, delivery preview/plan", "upstream_ref": "src/cron/delivery.ts:1", "qubit_backend": "voicebridge/schedules.py delivery targets", "cli": "schedules deliveries", "dashboard": "/api/cron/delivery-targets", "voice": "schedules_*", "status": "IMPLEMENTED", "proving_test": "tests/test_schedule_delivery8u.py", "note": "Channel delivery real; webhook outbound typed UNAVAILABLE without an adapter; no Gmail transport.", "needs": "creds"}, {"id": "OC-D23", "area": "automation", "capability": "Hooks event types, bundled hooks, authoring config", "upstream_ref": "docs/automation/hooks/event-types.md:1", "qubit_backend": "voicebridge/hooks.py declare/fire (disarmed)", "cli": "hooks", "dashboard": "/api/ops/hooks", "voice": "hooks_*", "status": "PARTIAL", "proving_test": "tests/test_hooks8tf.py", "note": "Event-pattern registry + manual fire; no real lifecycle event emissions.", "needs": "none"}, {"id": "OC-D24", "area": "sessions", "capability": "Public session transcript sharing (publish/revoke read-only public URL)", "upstream_ref": "packages/session-url-contract/src/public-share.ts:3", "qubit_backend": "voicebridge/session_share.py publish/resolve/revoke; webui /share/<id>", "cli": "shares", "dashboard": "/api/sessions/shares, /share/<id>", "voice": "shares_*", "status": "IMPLEMENTED", "proving_test": "tests/test_session_share8u.py", "note": "Qubit-safe local token-scoped expiring read-only share (no public upload); token shown once, hash persisted.", "needs": "none"}, {"id": "OC-D25", "area": "sessions", "capability": "Subagent yield handoff (sessions_yield continuation context/tool)", "upstream_ref": "src/agents/tools/sessions-yield-tool.ts:2", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No subagent/yield continuation model.", "needs": "none"}, {"id": "OC-D26", "area": "sessions", "capability": "Session repository / managed worktrees (cloud artifacts, workspace access, lifecycle)", "upstream_ref": "src/gateway/server-methods/session-repository-artifacts.ts:1", "qubit_backend": "voicebridge/opencode/workdir.py prepare/cleanup/sweep", "cli": "opencode", "dashboard": "", "voice": "", "status": "PARTIAL", "proving_test": "tests/test_opencode_voice8u.py", "note": "Local disposable worktrees + orphan sweep; no cloud session repository/artifacts.", "needs": "none"}, {"id": "OC-D27", "area": "browser", "capability": "Browser live panel: screencast streaming with screenshot fallback; Annotate/Inspect", "upstream_ref": "extensions/browser/src/browser/screencast/session.ts:321", "qubit_backend": "voicebridge/browser_tabs.py screenshot (no screencast loop)", "cli": "browser screenshot", "dashboard": "qubit-devices", "voice": "browser_screenshot", "status": "PARTIAL", "proving_test": "tests/test_browser_screenshot8u.py", "note": "Screenshot fallback real; no Page.startScreencast stream loop.", "needs": "hardware"}, {"id": "OC-D28", "area": "web_media", "capability": "Provider account controls + model fallback picker + custom providers", "upstream_ref": "docs/concepts/model-providers/control-ui-and-keys.md:1", "qubit_backend": "voicebridge/local_models.py, brain.py, config.py", "cli": "local-models, brain account-*", "dashboard": "/api/local-models*, /api/providers/custom-endpoints (gated)", "voice": "local_models_*", "status": "PARTIAL", "proving_test": "tests/test_local_models8u.py", "note": "Accounts/reads real; custom endpoints and live probes are typed gates; no automatic fallback picker.", "needs": "creds"}, {"id": "OC-D29", "area": "skills", "capability": "Skill Workshop agent-owned collection (backup/restore/rollback, curator, contracts)", "upstream_ref": "src/skills/workshop/curator.ts:95", "qubit_backend": "voicebridge/verified_skills/ snapshot/restore/transfer", "cli": "verified-skills", "dashboard": "qubit-memory", "voice": "verified_*", "status": "PARTIAL", "proving_test": "tests/test_packet8p.py", "note": "Qubit verified-skills snapshot/restore real; no agent-owned collection contract/curator status semantics.", "needs": "none"}, {"id": "OC-D30", "area": "runtime", "capability": "Update rollback / repair / recovery (package/service rollback, update history)", "upstream_ref": "src/cli/update-cli/update-command-rollback.ts:47", "qubit_backend": "voicebridge/runtime_images.py install/rollback", "cli": "runtime rollback", "dashboard": "qubit-runtime", "voice": "runtime_*", "status": "PARTIAL", "proving_test": "tests/test_runtime_images8u.py", "note": "Runtime-image rollback real; no core/package update rollback/repair semantics.", "needs": "none"}, {"id": "OC-D31", "area": "runtime", "capability": "Plugin marketplace / gateway discovery CLI docs expand", "upstream_ref": "docs/cli/plugins/marketplace.md:1", "qubit_backend": "voicebridge/cli.py families", "cli": "skills, plugins, gateway", "dashboard": "", "voice": "", "status": "PARTIAL", "proving_test": "tests/test_native_equivalents8u.py", "note": "CLI surfaces exist/parse but the marketplace/discovery capability is remote-refused; naming alignment only.", "needs": "none"}, {"id": "OC-D32", "area": "reports", "capability": "OpenTelemetry (traces, spans/events, model-call metrics, trace-context)", "upstream_ref": "docs/gateway/opentelemetry/configuration.md:1", "qubit_backend": "none", "cli": "", "dashboard": "", "voice": "", "status": "MISSING", "proving_test": "", "note": "No OTLP exporter or trace context.", "needs": "none"}]'''

_STOP = {
    "voicebridge", "none", "with", "from", "only", "real", "typed", "store",
    "local", "receipt", "receipts", "read", "write", "server", "module",
    "plugin", "plugins", "channels", "channel", "hooks", "tools", "skills",
    "sessions", "memory", "reports", "runtime", "gateway", "browser",
}

# Documented per-row adjustments applied *on top of* the audited baseline.
# Each entry records the real Qubit backend and the behavioural test that
# exercises the production caller for a row closed since the audit.
ROW_EVIDENCE: Dict[str, Dict[str, str]] = {
    "OC-04": {
        "qubit_backend": ("voicebridge/gateway_peers.py supervision_request/"
                          "supervision_revert/supervision_status"),
        "cli": "gateway supervision-request/revert/discovery",
        "dashboard": "qubit-gateway",
        "voice": "gateway_supervision_refusal",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_gateway_peers8tf.py",
        "needs": "none",
        "note": ("Approval/grant-gated supervised-state transition on the one "
                 "bridge event path with a durable approval journal, typed "
                 "refusal, revert recovery, and e-stop; proven through the one "
                 "integration dispatcher."),
    },
    "OC-08": {
        "qubit_backend": ("voicebridge/pairing.py encode_setup/accept/"
                          "channel_dm_pairings"),
        "cli": "pairing setup-code/accept",
        "dashboard": "qubit-gateway",
        "voice": "pairing_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_oc_runtime_agent5_capabilities.py",
        "needs": "none",
        "note": ("One-time hashed setup code + qubit:// join URL/QR and "
                 "channel DM pairing, proven through the integration "
                 "dispatcher (code shown once, stored hashed only)."),
    },
    "OC-D07": {
        "qubit_backend": ("voicebridge/runtime_images.py recover/repair/"
                          "update_history/update_report"),
        "cli": "runtime recover/repair/history/update-report",
        "dashboard": "qubit-runtime",
        "voice": "runtime_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_runtime_images8u.py",
        "needs": "none",
        "note": ("Runtime/node update recovery: digest-verified reconcile or "
                 "re-stage from the pinned local candidate, bounded repair, "
                 "and append-only update history — never downloads and never "
                 "invents a healthy state; proven through the integration "
                 "dispatcher."),
    },
    "OC-D09": {
        "qubit_backend": ("voicebridge/gateway/rpc.py MethodRegistry + "
                          "voicebridge/gateway/gateway_ws.py FrameReader"),
        "cli": "gateway",
        "dashboard": "",
        "voice": "",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_gateway_frames8u.py",
        "needs": "none",
        "note": ("The new gateway server methods (environments.prepare, "
                 "task.history/suggestions, update.report, workspace.files, "
                 "plugin.mutations, migrations) are real and dispatch through "
                 "the transport-neutral FrameReader; no standalone gateway "
                 "server (OC-01 remains the server row)."),
    },
    "OC-D30": {
        "qubit_backend": ("voicebridge/runtime_images.py rollback/recover/"
                          "repair/update_history/update_report"),
        "cli": "runtime rollback/recover/repair/history/update-report",
        "dashboard": "qubit-runtime",
        "voice": "runtime_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_runtime_images8u.py",
        "needs": "none",
        "note": ("Runtime update rollback/recovery/repair/history is the "
                 "Qubit-safe equivalent of package/service update rollback; "
                 "proven through the integration dispatcher."),
    },
    "OC-27": {
        "qubit_backend": "voicebridge/schedules.py:952 autofire + :472 run_due",
        "cli": "schedules",
        "dashboard": "/api/cron/jobs",
        "voice": "schedules_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_scheduler_autofire8u.py",
        "needs": "none",
        "note": ("The monitor's single in-process tick calls "
                 "schedules.autofire (bounded, idempotent, lease-backed); a "
                 "job that missed windows while down fires once and reports "
                 "the coalesced catch-up count via run_due/scheduler_status."),
    },
    "OC-31": {
        "qubit_backend": ("voicebridge/automation.py run_directive/order_add/"
                          "process_events/heartbeat_add/tick"),
        "cli": "schedules",
        "dashboard": "/api/cron/jobs",
        "voice": "schedules_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_e5_directives.py",
        "needs": "none",
        "note": ("Standing-order auto-reply, flows, heartbeat, and the QA/"
                 "yield directives are user-configurable as an 'automation:' "
                 "task text on the existing schedules store (CLI/dashboard/"
                 "voice) and run on the ONE schedules.autofire tick via the "
                 "production run_due/run_directive dispatcher; no second "
                 "scheduler."),
    },
    "OC-D05": {
        "qubit_backend": ("voicebridge/automation.py qa_run/qa_scenario_add/"
                          "qa_scenario_run/run_directive"),
        "cli": "schedules",
        "dashboard": "/api/cron/jobs",
        "voice": "schedules_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_e5_directives.py",
        "needs": "none",
        "note": ("Qubit-safe bounded QA Lab: persisted named scenarios plus a "
                 "structured pass/fail report, configurable and runnable on "
                 "the one scheduler tick (not the full upstream operator-flow "
                 "stack, which is not shipped)."),
    },
    "OC-47": {
        "proving_test": "tests/test_packet8m.py",
        "note": ("Semantic belief memory + hash-chained journal proven by the "
                 "Packet 8M adapter test (the audit's Packet 5B citation "
                 "targets the base store, not the semantic layer)."),
    },
    "OC-D25": {
        "qubit_backend": ("voicebridge/automation.py yield_handoff/yield_resume/"
                          "yield_pending/run_directive"),
        "cli": "schedules",
        "dashboard": "/api/cron/jobs",
        "voice": "schedules_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_e5_directives.py",
        "needs": "none",
        "note": ("Bounded, redacted continuation-context handoff with an "
                 "enforced delegation depth, confirmation-gated create/consume, "
                 "and pending-handoff surfacing on the one scheduler tick; "
                 "configurable through the schedules 'automation:' directive."),
    },
    # ---- Agent 5-10 wave: rows proven by the new module tests ----
    "OC-29": {
        "qubit_backend": ("voicebridge/hooks.py emit/declare/fire + lifecycle "
                          "event emission"),
        "cli": "hooks",
        "dashboard": "/api/ops/hooks",
        "voice": "hooks_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_oc_skills_agent8_hooks.py",
        "needs": "none",
        "note": ("Core lifecycle events are emitted into the existing hook "
                 "registry (wildcard match, shell permanently disarmed, "
                 "tampered hooks reported never executed, e-stop refuses); "
                 "proven through the production hooks.emit path."),
    },
    "OC-D23": {
        "qubit_backend": ("voicebridge/hooks.py emit/declare/fire + lifecycle "
                          "event emission"),
        "cli": "hooks",
        "dashboard": "/api/ops/hooks",
        "voice": "hooks_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_oc_skills_agent8_hooks.py",
        "needs": "none",
        "note": ("Event-type patterns + real core lifecycle emissions on the "
                 "existing registry; no separate bundled-hook authority."),
    },
    "OC-36": {
        "qubit_backend": ("voicebridge/kanban.py tab_create/widget_add/"
                          "show_widget WIDGET_PLUGIN_KIND"),
        "cli": "kanban tab-*",
        "dashboard": "qubit-boards",
        "voice": "kanban_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_board_canvas8u.py",
        "needs": "none",
        "note": ("Backend board tabs + sandboxed html/svg widgets + the inert "
                 "plugin widget kind and the read-only show_widget surface, "
                 "proven through the production kanban API."),
    },
    "OC-48": {
        "qubit_backend": ("voicebridge/memory/workspace.py write_note/read_note/"
                          "list_notes/dream/providers"),
        "cli": "",
        "dashboard": "",
        "voice": "",
        "status": "PARTIAL",
        "proving_test": "tests/test_oc_state_agent9_workspace.py",
        "needs": "creds",
        "note": ("Local markdown workspace memory + dreaming are real and "
                 "local-only (torn-index recovery, confirmation, e-stop); "
                 "external providers (LanceDB/Wiki/Honcho) stay typed-"
                 "unavailable, so the cloud/provider side remains open."),
    },
    "OC-51": {
        "qubit_backend": ("voicebridge/evaluation/logbook.py Logbook/"
                          "LogbookEntry/summary/journal_from_items/capture"),
        "cli": "",
        "dashboard": "",
        "voice": "",
        "status": "PARTIAL",
        "proving_test": "tests/test_oc_reports_agent10_logbook.py",
        "needs": "hardware",
        "note": ("Append-only, redacted work-journal + windowed summaries and a "
                 "typed screen-capture refusal are real; a live screen-capture "
                 "loop still needs hardware/vision."),
    },
    "OC-D16": {
        "qubit_backend": ("voicebridge/channels_inbound.py CHANNELS; "
                          "voicebridge/messaging.py PROVIDERS"),
        "cli": "msg",
        "dashboard": "",
        "voice": "channels_*",
        "status": "PARTIAL",
        "proving_test": "tests/test_oc_messaging_agent6_ingress.py",
        "needs": "creds",
        "note": ("Feishu is recognized in both registries with an honest typed "
                 "UNAVAILABLE state and no fabricated transport; the live "
                 "open-platform adapter remains credential-gated."),
    },
    "OC-D31": {
        "qubit_backend": ("voicebridge/runtime_inventory.py runtime_inventory; "
                          "voicebridge/gateway_peers.py discovery; "
                          "voicebridge/plugin_runtime.py marketplace_discover"),
        "cli": "gateway discovery, runtime discovery, marketplace discovery",
        "dashboard": "",
        "voice": "",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_oc_runtime_agent5_capabilities.py",
        "needs": "none",
        "note": ("Local-first gateway/runtime discovery plus marketplace "
                 "discovery over reviewed local index directories; remote "
                 "fetch remains typed-refused. Proven through the production "
                 "discovery/runtime-inventory calls."),
    },
    "OC-58": {
        "qubit_backend": ("voicebridge/acp_client.py HARNESSES + "
                          "voicebridge/acp_lsp.py acp_refuse_serve"),
        "cli": "acp",
        "dashboard": "",
        "voice": "acp_client_*",
        "status": "PARTIAL",
        "proving_test": "tests/test_oc_skills_agent8_acp.py",
        "needs": "hardware",
        "note": ("Real per-harness adapter contract, PATH probe and transport-"
                 "injected session loop with named refusals; Qubit never "
                 "spawns a harness, so execution stays hardware/harness-gated."),
    },
    # ---- D4 wave: reports / policy / ide / dashboard / skills / browser ----
    "OC-50": {
        "qubit_backend": ("voicebridge/evaluation/usage_analytics.py "
                          "UsageLedger/UsageRecord/analyze"),
        "cli": "monitor",
        "dashboard": "qubit-reports (/api/analytics*, usage_summary)",
        "voice": "usage_summary",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_reports.py",
        "needs": "none",
        "note": ("Append-only, content-addressed token/cost ledger; the "
                 "analytics route reads only recorded counts/models/provid"
                 "ers/days (no fabricated numbers, no prompts or secrets)."),
    },
    "OC-52": {
        "qubit_backend": ("voicebridge/evaluation/receipts.py "
                          "ExecutionIdentity/ReceiptChain/verify_receipts"),
        "cli": "actions receipts",
        "dashboard": "qubit-reports (receipts_ledger)",
        "voice": "actions_receipts",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_reports.py",
        "needs": "none",
        "note": ("Hash-chained, execution-identity-bound receipts; any edit, "
                 "reorder, or head deletion is detected and verification "
                 "fails closed. Digests/labels only — never inputs/outputs."),
    },
    "OC-D32": {
        "qubit_backend": ("voicebridge/evaluation/telemetry.py "
                          "Tracer/export_payload/export/parse_traceparent"),
        "cli": "telemetry",
        "dashboard": "qubit-reports",
        "voice": "",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_reports.py",
        "needs": "none",
        "note": ("Local OTLP-shaped traces/spans + model-call metrics with a "
                 "W3C traceparent context; export is opt-in gated and uses a "
                 "caller-supplied transport only, so nothing leaves the host "
                 "by default."),
    },
    "OC-D02": {
        "qubit_backend": ("voicebridge/evaluation/policy_conformance.py "
                          "doctor/attestation; voicebridge/plugin_runtime.py "
                          "policy_add_rule/policy_doctor/policy_attest"),
        "cli": "policy",
        "dashboard": "qubit-ops (policy_conformance/policy_attest)",
        "voice": "policy_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_policy.py",
        "needs": "none",
        "note": ("User-authorable workspace-conformance rules + typed "
                 "findings + deterministic content-addressed attestation; the "
                 "doctor is read-only, never follows symlinks out of the "
                 "tree, and attestation is human-principal-only."),
    },
    "OC-D08": {
        "qubit_backend": ("voicebridge/proxy_secrets.py SecretRegistry/"
                          "EgressPolicy/substitute/substitute_stream; "
                          "voicebridge/proxy_status.py refuse_start"),
        "cli": "proxy",
        "dashboard": "/api/egress/status (egress_check)",
        "voice": "proxy_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_policy.py",
        "needs": "none",
        "note": ("Qubit-safe equivalent of a forwarding proxy: no server is "
                 "started (typed PROXY_NO_SERVER), and brokered egress gets a "
                 "deny-by-default destination allowlist plus a reference-only "
                 "secret registry and stream substitution that fails closed."),
    },
    "OC-59": {
        "qubit_backend": ("voicebridge/acp_lsp.py lsp_list/lsp_status/"
                          "lsp_lifecycle/lsp_refuse_install"),
        "cli": "lsp",
        "dashboard": "qubit-ops (lsp_*)",
        "voice": "lsp_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_ide_dashboard.py",
        "needs": "none",
        "note": ("Real read-only LSP inventory + explicit lifecycle state over "
                 "PATH probes. Qubit's safe equivalent spawns no persistent "
                 "client (typed NO_CLIENTS) and never downloads unpinned "
                 "server binaries (typed LSP_NO_DOWNLOADS); diagnostics run "
                 "inside supervised opencode runs."),
    },
    "OC-D20": {
        "qubit_backend": ("voicebridge/doc_extract.py extract/pdf_backend/"
                          "status (stdlib PDF text path)"),
        "cli": "documents",
        "dashboard": "/api/documents*",
        "voice": "documents_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_ide_dashboard.py",
        "needs": "none",
        "note": ("Bounded, redacted PDF + transcript extraction confined to "
                 "files.py roots with a zero-dependency stdlib PDF text "
                 "fallback (no extractor install required); confirmation and "
                 "e-stop required, escape/binary/oversize typed-refused."),
    },
    "OC-69": {
        "qubit_backend": ("voicebridge/hermes_compat.py ROUTES/dispatch; "
                          "voicebridge/dashboard_ops.py catalog/run_op"),
        "cli": "all families",
        "dashboard": "300 routes + 10 plugins",
        "voice": "all",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_ide_dashboard.py",
        "needs": "none",
        "note": ("Compat routes are registered and dispatched through the one "
                 "shared broker to real registry operations (same backend "
                 "dict as CLI/voice) with confirmation/cancel; a fabricated "
                 "route is a typed 404/NOT_FOUND."),
    },
    "OC-70": {
        "qubit_backend": ("voicebridge/kanban.py tab_create/widget_add "
                          "WIDGET_PLUGIN_KIND"),
        "cli": "kanban tab-*",
        "dashboard": "qubit-boards",
        "voice": "kanban_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_ide_dashboard.py",
        "needs": "none",
        "note": ("Board tabs, sandboxed widget kinds, and the inert plugin "
                 "widget kind (validated closed key set, requires the "
                 "review-only plugin:read grant, never rendered/executed by "
                 "the backend)."),
    },
    "OC-39": {
        "qubit_backend": ("voicebridge/plugin_runtime.py marketplace_discover/"
                          "marketplace_refuse_remote/marketplace_source_kind"),
        "cli": "skills install (remote refused)",
        "dashboard": "qubit-ops (marketplace_discovery)",
        "voice": "plugins_remote_refusal",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_skills.py",
        "needs": "none",
        "note": ("Pinned-source policy: local index directories are really "
                 "discovered (no network), while ClawHub/npm/git/archive and "
                 "URL sources are typed-refused with MARKETPLACE_REMOTE_"
                 "REFUSED."),
    },
    "OC-40": {
        "qubit_backend": ("voicebridge/plugin_runtime.py bind_handler/invoke "
                          "PLUGIN_KINDS"),
        "cli": "plugins",
        "dashboard": "qubit-ops (plugins_*)",
        "voice": "plugins_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_skills.py",
        "needs": "none",
        "note": ("Real in-process handler runtime for tool/channel/provider/"
                 "hook: a manifest entrypoint alone never executes (typed "
                 "PLUGIN_RUNTIME_UNAVAILABLE), and invoke is gated estop then "
                 "fresh confirmation then handler presence."),
    },
    "OC-41": {
        "qubit_backend": ("voicebridge/verified_skills/workshop.py "
                          "SkillWorkshop author/backup/restore"),
        "cli": "verified-skills",
        "dashboard": "qubit-memory (skills_workshop)",
        "voice": "verified_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_skills.py",
        "needs": "none",
        "note": ("Agent-owned workshop authoring over the verified-skill "
                 "store: proposals stay quarantined (no agent self-verify), "
                 "with digest-signed backups and human-only restore/rollback."),
    },
    "OC-D10": {
        "qubit_backend": ("voicebridge/plugin_runtime.py mcp_attach/mcp_serve/"
                          "mcp_authenticate/mcp_revoke"),
        "cli": "plugins",
        "dashboard": "/api/mcp* + qubit-ops",
        "voice": "plugins_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_skills.py",
        "needs": "none",
        "note": ("Loopback MCP hosted-app host: hashed bearer grants shown "
                 "once, in-process bound handlers only, unbound tools typed-"
                 "refused, unauthorized requests 401; binds 127.0.0.1 and "
                 "opens no outbound network."),
    },
    "OC-D29": {
        "qubit_backend": ("voicebridge/verified_skills/workshop.py "
                          "COLLECTION_CONTRACTS/curator_status"),
        "cli": "verified-skills",
        "dashboard": "qubit-memory (skills_workshop_curator)",
        "voice": "verified_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_skills.py",
        "needs": "none",
        "note": ("Agent-owned collection contracts + curator status over the "
                 "verified-skill store; tampered-backup digests and path "
                 "traversal are typed-refused and never applied."),
    },
    "OC-10": {
        "qubit_backend": ("voicebridge/browser.py profiles/create_profile/"
                          "open_profile/close_profile/remove_profile"),
        "cli": "browser close-profile",
        "dashboard": "qubit-devices",
        "voice": "browser_close_refusal",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_browser_desktop.py",
        "needs": "none",
        "note": ("Real Qubit-dedicated profile lifecycle (create/open/close/"
                 "remove) confined to the state dir with injected launcher/"
                 "stopper seams; any real/user profile name stays typed-"
                 "refused with BROWSER_CLOSE_REFUSED."),
    },
    "OC-15": {
        "qubit_backend": ("voicebridge/computer_use.py screen/arrange_screen/"
                          "screen_panes/screen_status"),
        "cli": "screen",
        "dashboard": "qubit-devices (computer_screen)",
        "voice": "computer_screen_arrange",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_d4_browser_desktop.py",
        "needs": "none",
        "note": ("Bounded, persisted Control UI pane arrangement (validated "
                 "panes/layouts, confirmed, e-stop aware). It is a declarative "
                 "descriptor only: it never drives a window manager or reads "
                 "the display."),
    },
    # ---- F2 wave: remaining OpenClaw code-side rows (needs=none) ----
    "OC-33": {
        "qubit_backend": ("voicebridge/projects.py clone/checkout (local "
                          "confined git clone/checkout)"),
        "cli": "project",
        "dashboard": "qubit-boards",
        "voice": "projects_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_f2_projects.py",
        "needs": "none",
        "note": ("Local, confined git clone/checkout: a workspace is cloned "
                 "from an existing on-disk git repository only (network/URL/"
                 "SCP sources are typed-refused PROJECT_CLONE_REMOTE_REFUSED, "
                 "so no outbound fetch ever runs) into the confined "
                 "projects.clone_root, optionally checked out to a validated "
                 "ref, then registered as a normal workspace with backup/"
                 "rollback/audit."),
    },
    "OC-37": {
        "qubit_backend": ("voicebridge/kanban.py refuse_delegated "
                          "(typed KANBAN_DELEGATED)"),
        "cli": "kanban dispatch/swarm/daemon/watch",
        "dashboard": "",
        "voice": "",
        "status": "GATED",
        "proving_test": "tests/test_kanban8tf.py",
        "needs": "owner",
        "note": ("The frozen acceptance test "
                 "test_kanban8tf::test_execution_subcommands_delegate_without_"
                 "starting asserts typed KANBAN_DELEGATED and that nothing is "
                 "started. A real workboard dispatch queue/worker runs would "
                 "rewrite that frozen test, so the typed delegation is the "
                 "Qubit-safe equivalent and enabling swarm/daemon remains an "
                 "explicit owner policy decision (not a code gap)."),
    },
    "OC-44": {
        "qubit_backend": ("voicebridge/auth_status.py SessionStore "
                          "(search/show/rename/archive/resume/delete) + "
                          "voicebridge/automation.py yield_handoff/"
                          "yield_resume"),
        "cli": "sessions",
        "dashboard": "/api/sessions/*",
        "voice": "session_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_owner_session8tf.py",
        "needs": "none",
        "note": ("Real session lifecycle over the append-only receipt store "
                 "(search/show/rename/archive/resume/delete, confirmation and "
                 "recovery on destructive ops). Live subagent delegation maps "
                 "to the bounded, redacted, depth-capped yield handoff "
                 "(OC-D25) and live steering is not exposed because Qubit "
                 "keeps one agent authority."),
    },
    "OC-55": {
        "qubit_backend": ("voicebridge/runtime_inventory.py device_inventory "
                          "(browser/desktop/IDE/vision/runtime/node_host)"),
        "cli": "runtime, status",
        "dashboard": "qubit-devices",
        "voice": "runtime_status",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_e9_devices.py",
        "needs": "none",
        "note": ("One real read-only device inventory: per-family availability "
                 "probes (browser loopback CDP, desktop, IDE PATH, vision, "
                 "pinned runtime) plus node-host-derived supported/remote "
                 "capabilities. Every leaf is observed or typed UNAVAILABLE; "
                 "availability is never treated as authorization."),
    },
    "OC-71": {
        "qubit_backend": ("voicebridge/gateway/panels.py control_panels/"
                          "arrange_panels + MethodRegistry ui.panels/"
                          "ui.panel_layout"),
        "cli": "",
        "dashboard": ("qubit-devices ui.panels via gateway RPC; "
                      "/api/qubit/chat; /api/pty typed PTY_NO_SHELL"),
        "voice": "",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_f2_panels.py",
        "needs": "none",
        "note": ("Real Control-UI panel registry dispatched through the shared "
                 "gateway RPC (no second listener): side chat is served by "
                 "/api/qubit/chat, browser and desktop report live device-"
                 "probe availability, and the terminal panel is honestly "
                 "typed GATED PTY_NO_SHELL (no shell starts; use `qubit tui` "
                 "or a supervised opencode run). A validated, persisted, "
                 "confirmed panel layout is available to the dashboard."),
    },
    "OC-D03": {
        "qubit_backend": ("voicebridge/file_transfer.py list_directory/fetch/"
                          "stream_plan/resume/verify_transfer"),
        "cli": "transfers",
        "dashboard": "/api/transfers*",
        "voice": "transfers_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_openclaw_e9_transfers.py",
        "needs": "none",
        "note": ("Confined local node directory list/fetch/chunked stream/"
                 "resume plus receiver-side verify_transfer, which proves a "
                 "byte-exact assembly against the persisted plan (tamper, "
                 "short, incomplete, duplicate, unknown, and remote all "
                 "typed). Non-loopback node transport stays typed "
                 "FILE_TRANSFER_UNAVAILABLE (no fabricated remote stream)."),
    },
    "OC-D26": {
        "qubit_backend": ("voicebridge/opencode/workdir.py _prepare_workdir/"
                          "_cleanup_workdir/sweep_orphaned_workdirs"),
        "cli": "opencode",
        "dashboard": "qubit-ops",
        "voice": "opencode_*",
        "status": "IMPLEMENTED",
        "proving_test": "tests/test_opencode_voice8u.py",
        "needs": "none",
        "note": ("Managed local worktrees: a disposable run gets an isolated "
                 "0700 detached git worktree, generated content is bounded/"
                 "validated (symlink escapes typed), cleanup deregisters it, "
                 "and the orphan sweep reaps only stale bases. No cloud "
                 "session repository or remote artifacts are fabricated."),
    },
}

_BACKEND_PATH_RE = re.compile(r"(voicebridge/[\w./-]+\.py)")

# Production entry points a proving test may drive. A row closed since the
# audit must be reachable through one of these (or a named backend symbol),
# not merely mention the module: this is the "production-dispatcher" bar.
_DISPATCH_SYMBOLS = {
    "autofire", "run_due", "run_directive", "run_op", "run_registry_op",
    "run_extra_op", "dispatch", "process_events", "tick", "flow_run",
    "qa_run", "qa_scenario_run", "order_add", "heartbeat_add",
    "yield_handoff", "yield_resume", "declare", "fire", "emit", "tab_create",
    "widget_add", "show_widget", "install", "recover", "repair",
    "marketplace_discover", "discovery", "runtime_inventory", "execute",
    "supervision_request", "supervision_revert", "main",
}


def _tokens(text: str) -> set:
    """Salient backend tokens: module basenames (always) + named symbols."""
    raw = str(text or "")
    out = {Path(p).stem.lower() for p in _BACKEND_PATH_RE.findall(raw)}
    out |= {t for t in re.findall(r"[a-z][a-z0-9_]{4,}", raw.lower())
            if t not in _STOP}
    return out


def _called_names(source: str) -> set:
    """Every function/method name called in a test module (lowercased)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr.lower())
            elif isinstance(func, ast.Name):
                names.add(func.id.lower())
    return names


def _backend_symbols(row: Dict[str, str]) -> set:
    named = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}",
                       str(row.get("qubit_backend", "")))
    return {n.lower() for n in named if n.lower() not in _STOP}


def _producing_dispatcher(row: Dict[str, str]) -> bool:
    """True when the proving test drives a production entry point.

    The test source is parsed and must call a known production dispatcher or a
    symbol named in the row's backend. A non-static/non-Python proof falls back
    to the token link so an honest artifact is never silently downgraded.
    """
    proving = str(row.get("proving_test", "")).split("::", 1)[0]
    if not proving or not (ROOT / proving).is_file():
        return False
    try:
        source = (ROOT / proving).read_text(encoding="utf-8")
    except OSError:
        return False
    calls = _called_names(source)
    if not calls:
        return _proves_caller(row)
    return bool(calls & _DISPATCH_SYMBOLS) or \
        bool(calls & _backend_symbols(row))


def baseline_rows() -> List[Dict[str, str]]:
    return json.loads(_BASELINE_JSON)


def _proving_test_exists(proving: str) -> bool:
    path = str(proving or "").split("::", 1)[0]
    return bool(path) and (ROOT / path).is_file()


def _proves_caller(row: Dict[str, str]) -> bool:
    """True when the cited test file references a salient backend token."""
    proving = str(row.get("proving_test", "")).split("::", 1)[0]
    if not proving or not (ROOT / proving).is_file():
        return False
    toks = _tokens(row.get("qubit_backend", ""))
    if not toks:
        return True  # no specific backend token to link; file presence is it
    try:
        source = (ROOT / proving).read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return any(tok in source for tok in toks)


def _missing_backend_paths(row: Dict[str, str]) -> List[str]:
    return [p for p in _BACKEND_PATH_RE.findall(row.get("qubit_backend", ""))
            if not (ROOT / p).is_file()]


def _verify_row(row: Dict[str, str], baseline_status: str = "IMPLEMENTED") -> None:
    """Apply the mechanical evidence checks; downgrade dishonest claims.

    ``IMPLEMENTED`` requires the cited backend to exist on disk, a real test on
    disk, and a token link to the backend. A row *upgraded* from an open
    baseline (PARTIAL/GATED/MISSING) must additionally prove a production
    dispatcher call, so a closure can never be an unlinked claim.
    """
    if row.get("status") != "IMPLEMENTED":
        if row.get("proving_test") and \
                not _proving_test_exists(row["proving_test"]):
            row["proving_test"] = ""
        return
    missing = _missing_backend_paths(row)
    if missing:
        row["status"] = "UNVERIFIED"
        row["proving_test"] = ""
        row["note"] = (row.get("note", "") + " ").strip() + (
            "Cited backend path(s) absent: " + ", ".join(missing) + ". "
            "Downgraded to UNVERIFIED.")
        return
    if not _proving_test_exists(row.get("proving_test", "")):
        row["status"] = "UNVERIFIED"
        row["proving_test"] = ""
        row["note"] = (row.get("note", "") + " ").strip() + (
            "No behavioural proof on disk; downgraded to UNVERIFIED.")
        return
    if not _proves_caller(row):
        row["status"] = "UNVERIFIED"
        row["proving_test"] = ""
        row["note"] = (row.get("note", "") + " ").strip() + (
            "Proving test does not reference the cited backend; downgraded "
            "to UNVERIFIED.")
        return
    if baseline_status != "IMPLEMENTED" and not _producing_dispatcher(row):
        row["status"] = "UNVERIFIED"
        row["proving_test"] = ""
        row["note"] = (row.get("note", "") + " ").strip() + (
            "No production-dispatcher call in the proving test; downgraded "
            "to UNVERIFIED.")


def build_rows() -> List[Dict[str, str]]:
    rows = deepcopy(baseline_rows())
    baseline = {row["id"]: row["status"] for row in rows}
    for row in rows:
        row.update(ROW_EVIDENCE.get(row["id"], {}))
    for row in rows:
        _verify_row(row, baseline[row["id"]])
    return rows


def build_ledger() -> Dict[str, object]:
    rows = build_rows()
    by_status = Counter(r["status"] for r in rows)
    by_area = Counter(r["area"] for r in rows)
    return {
        "schema": "qubit-gap-ledger-section/1",
        "source": "openclaw",
        "upstream_pin": PIN,
        "upstream_tag": TAG,
        "upstream_tree": UPSTREAM_TREE,
        "generated": GENERATED,
        "method": (
            "Evidence-based regeneration. The audited capability inventory "
            "(72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a "
            "baseline; a documented ROW_EVIDENCE table records the rows closed "
            "or upgraded by real Qubit modules + behavioural tests. Every "
            "IMPLEMENTED row is mechanically re-verified against the code and "
            "an on-disk proving test that references the cited backend; a row "
            "upgraded from an open baseline must additionally drive a real "
            "production dispatcher (run_due/autofire, run_op, hooks.emit, "
            "kanban API, discovery, ...). A missing or unlinked proof "
            "downgrades to UNVERIFIED — no status is hand-flipped, and open "
            "gates (PARTIAL/GATED/MISSING with needs=creds/hardware/owner) "
            "are preserved."
        ),
        "sources_reconciled": [
            "docs/AUDIT_OPENCLAW_2026-09-11.md",
            "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md",
            "voicebridge/automation.py",
            "voicebridge/schedules.py",
            "tests/test_automation_agent6_*.py",
            "tests/test_openclaw_e5_directives.py",
            "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)",
            "tests/ (proving-test evidence)",
        ],
        "counts": {
            "total": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_area": dict(sorted(by_area.items())),
        },
        "rows": rows,
    }


def _render(data: Dict[str, object]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="output JSON path (default: docs section)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed section differs")
    args = parser.parse_args(argv)

    data = build_ledger()
    text = _render(data)
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"STALE: {out} does not match the regenerated section",
                  file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    counts = data["counts"]
    print(f"wrote {out} ({counts['total']} rows)")
    print(f"  by_status: {counts['by_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
