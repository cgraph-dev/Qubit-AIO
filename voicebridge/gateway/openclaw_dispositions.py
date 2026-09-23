"""Authoritative, behavior-based dispositions for the Qubit gateway delta.

The contractual pin -> latest Qubit diff adds gateway methods to Qubit's
single in-process RPC registry (:mod:`voicebridge.gateway.rpc`). A method is
only ``IMPLEMENTED`` when it resolves to a real handler backed by a canonical
Qubit module; a method that returns a typed refusal instead of performing the
upstream action is ``SAFE_EQUIVALENT`` (real local counterpart) or
``OPERATOR_ONLY`` (needs an external credential/service Qubit does not run).
``OPEN`` is never used as a silent substitute.

``scripts/openclaw_upgrade_manifest.py`` reads this map instead of inferring a
disposition from a bare registered-method name, and fails closed when an
``IMPLEMENTED`` entry is not actually registered or a refusal entry declares no
typed code. This is the behavior record; the handler source is the evidence.
"""
from __future__ import annotations

from typing import Any, Dict

#: Contractual-pin methods whose refusal is an ARCHITECTURE choice, not an
#: external prerequisite: Qubit deliberately runs no corresponding upstream
#: component, or is intentionally single-operator. Per owner amendment
#: 2026-09-22 (sections 2 and 4) these are NOT terminal: they remain OPEN
#: until the owner approves each row individually. An OPERATOR_ONLY method
#: NOT in this set is treated as OPERATOR_ONLY_EXTERNAL (a genuine external
#: credential/service/device/process prerequisite; terminal once named).
OPERATOR_ONLY_ARCHITECTURE = frozenset({
    "agents.create",
    "agents.delete",
    "agents.update",
    "cron.scratch.get",
    "cron.scratch.set",
    "mentions.dismiss",
    "mentions.list",
    "skills.proposals.evaluate",
    "skills.proposals.historyScan",
    "skills.upload.begin",
    "skills.upload.chunk",
    "skills.upload.commit",
    "taskSuggestions.accept",
    "taskSuggestions.create",
    "taskSuggestions.dismiss",
    "tasks.dismiss",
    "users.setDisplayName",
    "users.setRole",
    "voicewake.routing.get",
    "voicewake.set",
})

#: method -> disposition record. ``status`` is one of IMPLEMENTED /
#: SAFE_EQUIVALENT / OPERATOR_ONLY. ``evidence`` names the production module for
#: IMPLEMENTED; refusal rows name the typed ``code`` and the local
#: ``qubit_counterpart``.
DISPOSITIONS: Dict[str, Dict[str, Any]] = {
    # ---- real Qubit backends (canonical modules the CLI/dashboard/voice use)
    "environments.prepare": {
        "status": "SAFE_EQUIVALENT",
        "code": "ENVIRONMENT_PREPARE_LOCAL_PLAN",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "GET /api/runtime + CLI `qubit runtime status`",
        "basis": "upstream provisions a CLOUD worker environment (service."
                 "prepare); Qubit runs no cloud worker service, so this returns "
                 "the real local pinned-runtime plan and emits its own code"},
    "tasks.history": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical task/journal store"},
    "update.report": {
        "status": "SAFE_EQUIVALENT",
        "code": "UPDATE_REPORT_LOCAL_READ",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "CLI `qubit product upgrade` + dashboard Setup",
        "basis": "upstream is a consent-gated FAILED-update report submit keyed "
                 "by attemptId to a hosted service; Qubit runs no such service "
                 "and returns the real local update/image state with its own "
                 "code"},
    "workspace.files": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "confined read over approved project roots"},
    "plugin.mutations": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the persisted plugin mutation journal"},
    "ui.panels": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the persisted dashboard panel layout"},
    "ui.panel_layout": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the persisted dashboard panel layout"},
    "sessions.sharing": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical session share store"},
    "session.publicShare.set": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "confirm-gated session share mutation"},
    "session.repository_artifacts": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads repository artifacts for the session"},
    "plugins.catalog.browse": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical plugin catalog"},
    "plugins.catalog.categories": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical plugin catalog"},
    "plugins.catalog.get": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical plugin catalog"},
    "plugins.skills.read": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/verified_skills/store.py",
        "basis": "reads the verified-skill store"},
    "skills.workshop.read": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/verified_skills/store.py",
        "basis": "reads the verified-skill workshop collections"},
    "transcripts.status": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/chat_transport.py",
        "basis": "reports the canonical chat-session authority/boundary"},
    "transcripts.export": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/chat_transport.py",
        "basis": "exports from the canonical chat-session store"},
    "computer.status": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/computer_use.py",
        "basis": "reads the computer-use control/driver status"},
    "computer.invoke": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/computer_use.py",
        "basis": "broker-gated desktop action"},
    "desktop.release": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "releases the controlled desktop session"},
    "sessions.storage.status": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the canonical session storage status"},
    "sessions.storage.run": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "runs the canonical session storage maintenance"},
    "plugins.credentials.inspect": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "inspects credential references (names/labels only)"},
    "sessions.activitySummary.ensure": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/session_activity.py",
        "basis": "builds the canonical session activity projection"},
    "claws.monitors": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "reads the claw monitor registry"},
    "claws.packages.remove": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "confirm-gated package removal"},
    "canvas.document.preview": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/gateway/rpc.py",
        "basis": "renders a confined canvas document preview"},
    "plugins.reload": {
        "status": "SAFE_EQUIVALENT",
        "code": "PLUGIN_RELOAD_LOCAL_REREAD",
        "evidence": "voicebridge/plugin_runtime.py",
        "qubit_counterpart": "dashboard Plugins rescan + CLI `qubit plugin list`",
        "basis": "upstream reloads managed plugin code; Qubit never imports or "
                 "re-executes entrypoints, so this performs the safe observable "
                 "part (a real on-disk re-read with a changed digest) and emits "
                 "its own code instead of claiming a code reload"},
    # ---- real local counterpart, but not the upstream OAuth/voice action
    "models.authLogin": {
        "status": "SAFE_EQUIVALENT",
        "code": "OAUTH_MANUAL_HANDOFF",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "CLI `qubit auth login --provider`",
        "basis": "no live provider OAuth dance is performed; enrollment is a "
                 "manual handoff in the operator's browser"},
    "models.authRefresh": {
        "status": "SAFE_EQUIVALENT",
        "code": "OAUTH_MANUAL_HANDOFF",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "CLI `qubit auth login --provider`",
        "basis": "token refresh is part of the operator-owned auth enrollment"},
    "models.authSetApiKey": {
        "status": "SAFE_EQUIVALENT",
        "code": "SECRET_WRITE_REFUSED",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "CLI `qubit auth login --provider`",
        "basis": "raw API keys are never accepted; only env:/file: references"},
    "mcp.authLogin": {
        "status": "SAFE_EQUIVALENT",
        "code": "OAUTH_MANUAL_HANDOFF",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "CLI `qubit mcp status` + `qubit auth login`",
        "basis": "no live MCP OAuth flow; enrollment is a manual handoff"},
    "talk.voice.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/audio/settings.py",
        "basis": "reads the real local audio-device settings the orchestrator "
                 "and voice commands use"},
    "talk.voice.set": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/audio/settings.py",
        "basis": "persists the mic/output device through the canonical audio "
                 "settings with a backup"},
    "talk.voice.complete": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_compat.py",
        "basis": "synthesizes TTS through the canonical speech-out profile; an "
                 "unavailable provider is the typed AUDIO_TTS_UNAVAILABLE "
                 "error, never a fabricated clip"},
    # ---- genuine operator-only (external service/credential)
    "diagnostics.cpuProfile": {
        "status": "OPERATOR_ONLY",
        "code": "DIAGNOSTICS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/rpc.py",
        "operator_prerequisite": "a Node inspector attached to the runtime "
                                 "process; Qubit does not attach one",
        "qubit_counterpart": "operator `node --inspect` + Chrome DevTools",
        "basis": "no CPU profile is fabricated; the typed code is returned"},
    "diagnostics.heapProfile": {
        "status": "OPERATOR_ONLY",
        "code": "DIAGNOSTICS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/rpc.py",
        "operator_prerequisite": "a Node inspector attached to the runtime "
                                 "process; Qubit does not attach one",
        "qubit_counterpart": "operator `node --inspect` + Chrome DevTools",
        "basis": "no heap snapshot is fabricated; the typed code is returned"},
    "controlUi.sessionPullRequests.checks": {
        "status": "OPERATOR_ONLY",
        "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/rpc.py",
        "operator_prerequisite": "a configured forge credential (e.g. a "
                                 "GitHub/GitLab token) and a network-reachable "
                                 "forge API; Qubit runs no forge service",
        "qubit_counterpart": "local change review: CLI `qubit git "
                             "status|log|diff|show` and the dashboard git "
                             "review surface",
        "basis": "no remote pull-request CI check is fetched; the typed code "
                 "is returned on the existing listener",
    },
    # ---- latest-main (v2026.9.5 / main 395690e8) delta additions ----
    "themes.list": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/themes.py",
        "basis": "reads the real local Qubit theme catalog (built-in presets)",
    },
    "themes.get": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/themes.py",
        "basis": "reads the active preset or a named local preset",
    },
    "themes.set": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/themes.py",
        "basis": "confirmed selection of a real local preset (audited, "
                 "reversible)",
    },
    "themes.import": {
        "status": "SAFE_EQUIVALENT",
        "code": "PETS_NO_DOWNLOADS",
        "evidence": "voicebridge/themes.py",
        "qubit_counterpart": "`qubit theme` presets + `qubit theme set-tweak`",
        "basis": "upstream imports an arbitrary third-party theme definition; "
                 "the pinned-source policy refuses unpinned third-party theme "
                 "code and offers the real local preset/tweak counterpart",
    },
    "controlUi.linkPreview": {
        "status": "SAFE_EQUIVALENT",
        "code": "LINK_PREVIEW_LOCAL_FETCH",
        "evidence": "voicebridge/gateway/rpc.py",
        "qubit_counterpart": "Qubit `web.fetch` (SSRF-guarded) + browser "
                             "favicon cache",
        "basis": "upstream fetches remote favicon/OpenGraph metadata through a "
                 "hosted-style proxy; Qubit performs the real bounded "
                 "SSRF-guarded local fetch and emits its own code instead of "
                 "claiming the favicon/OG proxy",
    },
    "controlUi.githubDetail": {
        "status": "OPERATOR_ONLY",
        "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/rpc.py",
        "operator_prerequisite": "a configured forge credential (e.g. a "
                                 "GitHub/GitLab token) and a network-reachable "
                                 "forge API; Qubit runs no forge service",
        "qubit_counterpart": "local change review: CLI `qubit git "
                             "status|log|diff|show` and the dashboard git "
                             "review surface",
        "basis": "no remote GitHub detail is fetched; the typed code is "
                 "returned on the existing listener",
    },
    "transcripts.summarize": {
        "status": "IMPLEMENTED", "evidence": "voicebridge/chat_transport.py",
        "basis": "reads the canonical chat-session store and summarizes the "
                 "real transcript through voicebridge.summarizer",
    },
    "sessions.setInvolvement": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSION_INVOLVEMENT_LOCAL_ARCHIVE",
        "evidence": "voicebridge/chat_transport.py",
        "qubit_counterpart": "dashboard session archive/restore",
        "basis": "upstream toggles a per-profile involvement flag; Qubit is "
                 "single-operator with no involvement profiles, so it performs "
                 "the real local archive/restore mutation and emits its code",
    },
    "progressCard.refresh": {
        "status": "SAFE_EQUIVALENT",
        "code": "PROGRESS_CARD_LOCAL_PROJECTION",
        "evidence": "voicebridge/session_activity.py",
        "qubit_counterpart": "sessions.activitySummary.ensure (session "
                             "activity projection)",
        "basis": "upstream dispatches an agent turn to rewrite a progress card; "
                 "Qubit builds the real local activity/git/summary projection "
                 "and emits its own code rather than starting an agent turn",
    },
    "environments.session.status": {
        "status": "SAFE_EQUIVALENT",
        "code": "ENVIRONMENT_SESSION_LOCAL_STATUS",
        "evidence": "voicebridge/cloud_workers.py",
        "qubit_counterpart": "voicebridge.cloud_workers config + `qubit "
                             "runtime cells`",
        "basis": "upstream reports an attached cloud worker environment; Qubit "
                 "runs no cloud worker service, so it reads the real persisted "
                 "cloud-worker config and reports the typed local status",
    },
    "environments.session.create": {
        "status": "OPERATOR_ONLY",
        "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/cloud_workers.py",
        "operator_prerequisite": "an injected cloud-worker provider transport "
                                 "and a reachable provider",
        "qubit_counterpart": "voicebridge.cloud_workers + local process cells "
                             "(`qubit runtime cells`)",
        "basis": "no cloud environment is provisioned; the typed code is "
                 "returned and nothing is fabricated",
    },
    "environments.session.destroy": {
        "status": "OPERATOR_ONLY",
        "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/cloud_workers.py",
        "operator_prerequisite": "an injected cloud-worker provider transport "
                                 "and a reachable provider",
        "qubit_counterpart": "voicebridge.cloud_workers + local process cells "
                             "(`qubit runtime cells`)",
        "basis": "no cloud environment is attached or attachable; nothing is "
                 "destroyed and the typed code is returned",
    },
    "environments.session.exec": {
        "status": "OPERATOR_ONLY",
        "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/cloud_workers.py",
        "operator_prerequisite": "an attached cloud-worker environment "
                                 "(requires a provider transport)",
        "qubit_counterpart": "local policy-gated terminal/PTY (`/chat`) and "
                             "`qubit runtime cells`",
        "basis": "no matching cloud environment is attached; Qubit never "
                 "executes a command through this method",
    },
}

#: Explicit terminal records for the contractual-pin method surface
#: (``gateway_method_surface.contractual_pin``). The generator derives
#: ``IMPLEMENTED`` for any pinned method that is registered in the canonical RPC
#: registry with a non-trivial handler, so this map only needs to document those
#: derivations or add a refusal row. Qubit's RPC registry registers the newer
#: OpenClaw delta methods and Qubit's own extensions plus a small, explicitly
#: bounded pinned slice (``connect`` plus real introspection methods); the rest
#: of the contractual-pin surface is honestly ``OPEN`` and reported by the
#: fail-closed coverage gate, not stamped covered.
PINNED_SURFACE_DISPOSITIONS: Dict[str, Dict[str, Any]] = {
    # The one pinned method that resolves to a real registered handler: the
    # OpenClaw-compatible `hello-ok` handshake on Qubit's single listener.
    "connect": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/rpc.py",
        "basis": ("upstream-compatible `hello-ok` handshake registered in the "
                  "canonical Qubit gateway RPC registry; advertises only "
                  "actually-registered methods and the fixed operator scopes"),
    },
    # Bounded pinned slice: introspection methods backed by real canonical Qubit
    # modules. Each handler delegates to the same production module the CLI,
    # dashboard, and voice surfaces use.
    "health": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/lifecycle.py",
        "basis": ("real installation-health inventory "
                  "(`voicebridge.lifecycle.health_inventory`, the same module "
                  "`qubit setup health` calls)"),
    },
    "status": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/runtime_inventory.py",
        "basis": ("real runtime inventory "
                  "(`voicebridge.runtime_inventory.runtime_inventory`, the same "
                  "function the CLI `qubit status` and voice use)"),
    },
    "logs.tail": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/dashboard_ops.py",
        "basis": ("bounded read of the canonical event log through the shared "
                  "`dashboard_ops` logs operation"),
    },
    "config.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/config.py",
        "basis": "live canonical Qubit config tree (`voicebridge.config.Config`)",
    },
    "config.schema": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/config.py",
        "basis": ("real field descriptors derived from the canonical config "
                  "schema defaults (`voicebridge.config.DEFAULTS`)"),
    },
    # ---- bounded contractual-pin read slice (2026-09-21) ----
    # Read-only/status/list/get families whose upstream action has a real local
    # canonical Qubit backend. Every handler delegates to the same production
    # module the CLI, dashboard, `/qubit-chat`, and voice surfaces use.
    "sessions.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("indexes the canonical chat-session store "
                  "(`QubitSessionStore.list`, the one SQLite state.db authority)"),
    },
    "sessions.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("reads one session from the canonical chat-session store "
                  "(`QubitSessionStore.show`)"),
    },
    "sessions.search": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("bounded query over the canonical chat-session store "
                  "(`QubitSessionStore.list(query=...)`)"),
    },
    "sessions.catalog.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("the canonical session catalog is the same single chat-session "
                  "store index (`QubitSessionStore.list`)"),
    },
    "sessions.catalog.read": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("reads one catalog session from the canonical chat-session "
                  "store (`QubitSessionStore.show`)"),
    },
    "tasks.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/task_ledger.py",
        "basis": ("the shared task ledger (`task_ledger.list_tasks`) the CLI, "
                  "dashboard, and voice read; no detached cloud task registry"),
    },
    "tasks.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/task_ledger.py",
        "basis": "one task record from the shared ledger (`task_ledger.show`)",
    },
    "plugins.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/plugin_runtime.py",
        "basis": "the canonical managed-plugin inventory (`list_plugins`)",
    },
    "plugins.inspect": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/plugin_runtime.py",
        "basis": "one managed plugin from the canonical store (`get_plugin`)",
    },
    "plugins.search": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/plugin_runtime.py",
        "basis": ("bounded query over the canonical plugin catalog "
                  "(`plugin_catalog`), not a remote marketplace fetch"),
    },
    "plugins.refresh": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/plugin_runtime.py",
        "qubit_counterpart": "dashboard Plugins rescan + CLI `qubit plugin list`",
        "basis": ("real on-disk plugin-store re-read "
                  "(`plugin_runtime.reload`); Qubit never imports/re-executes "
                  "entrypoints, so no gateway restart is required and the "
                  "emitted PLUGIN_RELOAD_LOCAL_REREAD tag marks the local "
                  "re-read"),
    },
    "cron.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/schedules.py",
        "basis": ("the canonical schedule store (`schedules.list_jobs`) the CLI "
                  "and dashboard use, mapped to the cron wire shape"),
    },
    "cron.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/schedules.py",
        "basis": "one schedule with recent runs (`schedules.get_job`)",
    },
    "cron.status": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/schedules.py",
        "basis": ("read-only scheduler posture/due windows "
                  "(`schedules.scheduler_status`)"),
    },
    "cron.runs": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/schedules.py",
        "basis": ("the canonical run-history store (`schedules.history`)"),
    },
    "exec.approvals.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/approvals.py",
        "basis": ("the shared confirmation journal "
                  "(`ApprovalJournal.pending`) the CLI `qubit approvals`, "
                  "voice yes/no, and dashboard settle through; metadata only"),
    },
    # Managed git worktrees: the canonical confined `qubit worktree` lifecycle
    # (`voicebridge/git_scope`) exposed under the OpenClaw method names. The
    # read verbs delegate to the real `/api/git/*` projections; the mutations
    # reuse the same confirmation-gated, project-root-confined git runner.
    "worktrees.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "basis": ("`git worktree list --porcelain` through the scoped "
                  "`voicebridge.git_scope` runner, the same rows the dashboard "
                  "`/api/git/worktrees` read serves"),
    },
    "worktrees.branches": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "basis": ("real `git for-each-ref` heads/remotes with checked-out "
                  "worktree mapping, the same projection as "
                  "`/api/git/branches`"),
    },
    "worktrees.create": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "qubit_counterpart": "CLI `qubit worktree add <name> --confirm`",
        "basis": ("confirmation-gated, project-root-confined `git worktree "
                  "add` into `<root>/.worktrees/<name>` through the SAME "
                  "`voicebridge.git_scope._run` the CLI/voice use"),
    },
    "worktrees.remove": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "qubit_counterpart": "CLI `qubit worktree remove <name> --confirm`",
        "basis": ("confirmation-gated `git worktree remove` + `git worktree "
                  "prune`, confined to the project root; nothing outside the "
                  "approved folder is touched"),
    },
    "worktrees.restore": {
        "status": "SAFE_EQUIVALENT",
        "code": "WORKTREES_RESTORE_LOCAL_READD",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "qubit_counterpart": "CLI `qubit worktree add <name> --confirm`",
        "basis": ("upstream replays a managed snapshot from its worktree "
                  "store; Qubit keeps no managed snapshot store, so this "
                  "re-materializes the confined worktree with `git worktree "
                  "add` and emits its own code"),
    },
    "worktrees.gc": {
        "status": "SAFE_EQUIVALENT",
        "code": "WORKTREES_GC_LOCAL_PRUNE",
        "evidence": "voicebridge/gateway/openclaw_handlers/worktrees.py",
        "qubit_counterpart": "`git worktree prune` (via `qubit worktree`)",
        "basis": ("upstream runs a managed-cache sweep with owner retention "
                  "limits; Qubit runs the real local stale-metadata cleanup "
                  "`git worktree prune --verbose` and emits its own code"),
    },
    "memory.search": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/active_memory.py",
        "basis": ("active recall over the canonical MemoryStore "
                  "(`active_memory.recall`), redacted and policy-scoped"),
    },
    "usage.status": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("real token/cost aggregates from the canonical state.db "
                  "(`analytics_usage`); an absent store is an honest empty, "
                  "never fabricated billing numbers"),
    },
    "usage.cost": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/qubit_session_store.py",
        "basis": ("real estimated/actual cost aggregates from the canonical "
                  "state.db (`analytics_usage`)"),
    },
    "tools.catalog": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/dashboard_ops.py",
        "basis": ("the canonical broker/toolset catalog (dashboard_ops `tools` "
                  "op + persisted `voicebridge.toolsets` state)"),
    },
    "tools.effective": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/toolsets.py",
        "basis": ("the enabled subset of the canonical toolset catalog from the "
                  "persisted state the toggle writes"),
    },
    "hooks.status": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/hooks.py",
        "basis": ("canonical hook declarations plus the bundled authoring "
                  "catalog (`hooks.list_hooks` + `hooks.bundled`)"),
    },
    "system.info": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/distribution.py",
        "basis": ("real host probe plus the canonical product version "
                  "(`voicebridge.distribution.VERSION`)"),
    },
    "gateway.identity.get": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/device.py",
        "basis": ("the canonical device identity/enrollment profile "
                  "(`device.profile` + stable `machine_id`)"),
    },
    "config.schema.lookup": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/config.py",
        "basis": ("a dotted path resolved against the live canonical config "
                  "schema defaults (`voicebridge.config.DEFAULTS`)"),
    },
    "config.openFile": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/config.py",
        "qubit_counterpart": "CLI `qubit config path` / open the file in your "
                             "editor",
        "basis": ("returns the real canonical config path "
                  "(`config.resolve_config_path`) with the "
                  "CONFIG_OPEN_LOCAL_PATH tag; Qubit does not spawn a GUI "
                  "opener from a gateway call"),
    },
    "agents.list": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/integration_status.py",
        "basis": ("read-only aggregation of the canonical worker lane "
                  "(`worker_status`) and runtime adapters "
                  "(`runtime_inventory`); no second agent registry"),
    },
    # ---- 2026-09-21 family handlers: typed local counterparts (SAFE_EQUIVALENT)
    # Every entry resolves to a registered handler in a family module that
    # emits the typed code; the generator fails closed if it does not.
    "conversations.list": {
        "status": "SAFE_EQUIVALENT", "code": "CONVERSATIONS_LOCAL_SESSIONS",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "canonical chat-session store index (`qubit sessions`)",
        "basis": "upstream lists multi-party conversations; Qubit has one session authority",
    },
    "conversations.send": {
        "status": "SAFE_EQUIVALENT", "code": "CONVERSATIONS_LOCAL_SESSION_APPEND",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "canonical session transcript append (`qubit chat`)",
        "basis": "no separate conversation store; appends to the shared session",
    },
    "conversations.turn": {
        "status": "SAFE_EQUIVALENT", "code": "CONVERSATIONS_LOCAL_SESSION_TURN",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.chat_transport.turn (`/qubit-chat`)",
        "basis": "runs the canonical agent turn with policy/e-stop gating",
    },
    "conversations.turn.cancel": {
        "status": "SAFE_EQUIVALENT",
        "code": "CONVERSATIONS_LOCAL_SESSION_CANCEL",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.chat_transport.cancel",
        "basis": "cancels the canonical in-flight turn",
    },
    "session.members.add": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_MEMBERS_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share grant (`qubit sessions share`)",
        "basis": "Qubit is a single-operator share model, not multi-member ACLs",
    },
    "session.members.list": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_MEMBERS_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share.list_shares",
        "basis": "lists the local revocable shares",
    },
    "session.members.listEvidence": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_MEMBERS_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share evidence rows",
        "basis": "local share evidence, not remote member audit",
    },
    "session.members.remove": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_MEMBERS_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share revoke",
        "basis": "revokes the local share",
    },
    "session.visibility.set": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_VISIBILITY_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share visibility",
        "basis": "local share visibility instead of multi-tenant visibility",
    },
    "sessions.compaction.list": {
        "status": "SAFE_EQUIVALENT", "code": "COMPACTION_LOCAL_CHECKPOINTS",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.checkpoints.list_all (`qubit checkpoints`)",
        "basis": "canonical checkpoint journal, no conversation-compaction store",
    },
    "session.discussion.open": {
        "status": "SAFE_EQUIVALENT", "code": "DISCUSSION_LOCAL_COMMENTS",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.chat_stores comment thread",
        "basis": "the one per-session comment thread",
    },
    "sessions.files.reveal": {
        "status": "SAFE_EQUIVALENT", "code": "FILES_REVEAL_LOCAL_PATH",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "sessions.files.get / dashboard file viewer",
        "basis": "headless host never opens a file manager",
    },
    "sessions.fork": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_FORK_LOCAL_TRANSCRIPT_COPY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "QubitSessionStore.import_session (transcript copy)",
        "basis": "real transcript copy into a new session, not a branch tree",
    },
    "sessions.observer.visibility": {
        "status": "SAFE_EQUIVALENT", "code": "SESSION_OBSERVER_LOCAL_SHARE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.session_share.list_shares",
        "basis": "single-operator local revocable share",
    },
    "sessions.steer": {
        "status": "SAFE_EQUIVALENT", "code": "SESSIONS_STEER_LOCAL_INJECT",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "canonical transcript append for the next turn",
        "basis": "no mid-turn steering channel; records for the next turn",
    },
    "sessions.title.prepare": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_TITLE_LOCAL_EXTRACTIVE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.summarizer.summarize (extractive)",
        "basis": "deterministic local title; no network/model call",
    },
    "sessions.usage.logs": {
        "status": "SAFE_EQUIVALENT", "code": "USAGE_LOGS_LOCAL_DAILY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.qubit_session_store.analytics_usage",
        "basis": "canonical daily usage series",
    },
    "terminal.upload": {
        "status": "SAFE_EQUIVALENT", "code": "TERMINAL_UPLOAD_LOCAL_DRAFT",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "qubit_counterpart": "voicebridge.chat_stores.FileDraftStore.put",
        "basis": "e-stop-gated local draft store, not a remote transport",
    },
    "exec.approval.waitDecision": {
        "status": "SAFE_EQUIVALENT", "code": "EXEC_APPROVAL_NO_BLOCKING_WAIT",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "qubit_counterpart": "exec.approval.list + exec.approval.get (`qubit approvals`)",
        "basis": "the confirmation journal is poll-based, with no blocking wait",
    },
    "plugin.approval.waitDecision": {
        "status": "SAFE_EQUIVALENT",
        "code": "PLUGIN_APPROVAL_NO_BLOCKING_WAIT",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "qubit_counterpart": "plugin.approval.list",
        "basis": "poll-based confirmation journal, no blocking wait",
    },
    "question.waitAnswer": {
        "status": "SAFE_EQUIVALENT", "code": "QUESTION_NO_BLOCKING_WAIT",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "qubit_counterpart": "question.list + question.resolve",
        "basis": "question prompts are answered in-process, no blocking wait",
    },
    "secrets.resolve": {
        "status": "SAFE_EQUIVALENT", "code": "SECRET_READ_REFUSED",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "qubit_counterpart": "`qubit auth status` (reference names only)",
        "basis": "Qubit never resolves a secret value over the gateway",
    },
    "models.authLogout": {
        "status": "SAFE_EQUIVALENT", "code": "AUTH_LOGOUT_MANUAL_HANDOFF",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "qubit_counterpart": "`qubit auth status` + remove the env:/file: reference",
        "basis": "credential refs are operator-managed, not session-logout",
    },
    # ---- typed OPERATOR_ONLY gates (external prerequisite absent) ----
    "sessions.dispatch": {
        "status": "OPERATOR_ONLY", "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "injected cloud-worker runtime transport",
    },
    "sessions.move": {
        "status": "OPERATOR_ONLY", "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "injected cloud-worker runtime transport",
    },
    "sessions.reclaim": {
        "status": "OPERATOR_ONLY", "code": "RUNTIME_CLOUD_UNAVAILABLE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "injected cloud-worker runtime transport",
    },
    "sessions.github.confirm": {
        "status": "OPERATOR_ONLY", "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured git forge credential reference + network access",
    },
    "sessions.github.options": {
        "status": "OPERATOR_ONLY", "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured git forge credential reference + network access",
    },
    "sessions.github.publish": {
        "status": "OPERATOR_ONLY", "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured git forge credential reference + network access",
    },
    "sessions.github.status": {
        "status": "OPERATOR_ONLY", "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured git forge credential reference + network access",
    },
    "send": {
        "status": "OPERATOR_ONLY",
        "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured messaging account and reachable provider transport",
    },
    "message.action": {
        "status": "OPERATOR_ONLY",
        "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured messaging account and reachable provider transport",
    },
    "poll": {
        "status": "OPERATOR_ONLY",
        "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py",
        "operator_prerequisite": "configured messaging account and reachable provider transport",
    },
    "environments.create": {
        "status": "OPERATOR_ONLY", "code": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "operator_prerequisite": "injected cloud-worker provider transport",
    },
    "environments.destroy": {
        "status": "OPERATOR_ONLY", "code": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "operator_prerequisite": "injected cloud-worker provider transport",
    },
    "gateway.restart.request": {
        "status": "OPERATOR_ONLY", "code": "GATEWAY_RESTART_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "operator_prerequisite": "operator restart of the Qubit service unit",
    },
    "update.hold": {
        "status": "OPERATOR_ONLY", "code": "UPDATE_HOLD_OPERATOR_ONLY",
        "evidence": "voicebridge/gateway/openclaw_handlers/system.py",
        "operator_prerequisite": "operator-managed update hold on the runtime image",
    },
    "channels.start": {
        "status": "OPERATOR_ONLY",
        "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "configured inbound channel transport + credential",
    },
    "channels.stop": {
        "status": "OPERATOR_ONLY",
        "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "configured inbound channel transport + credential",
    },
    "channels.logout": {
        "status": "OPERATOR_ONLY",
        "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "configured inbound channel transport + credential",
    },
    "projects.searchRemote": {
        "status": "OPERATOR_ONLY",
        "code": "PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "configured git forge credential + network access",
    },
    "push.test": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.vapidPublicKey": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.subscribe": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.unsubscribe": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.test": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.preferences.get": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "push.web.preferences.set": {
        "status": "OPERATOR_ONLY", "code": "PUSH_WEB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": ("configured web-push service (VAPID keypair + "
                                  "subscription store) + browser permission"),
    },
    "tools.github.status": {
        "status": "OPERATOR_ONLY", "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "GitHub credential reference with required scopes + network access",
    },
    "tools.github.configure": {
        "status": "OPERATOR_ONLY", "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "GitHub credential reference with required scopes + network access",
    },
    "tools.github.authorize.start": {
        "status": "OPERATOR_ONLY", "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "GitHub credential reference with required scopes + network access",
    },
    "tools.github.authorize.poll": {
        "status": "OPERATOR_ONLY", "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "GitHub credential reference with required scopes + network access",
    },
    "tools.github.authorize.cancel": {
        "status": "OPERATOR_ONLY", "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/content.py",
        "operator_prerequisite": "GitHub credential reference with required scopes + network access",
    },
    # ---- sessions subscriptions/viewers (2026-09-21) ----
    # Upstream keeps a server-side subscriber registry and fans a
    # ``session.change`` event over the socket. Qubit keeps the SAME real
    # registry (voicebridge/session_subscriptions.py) but serves it
    # request-scoped on its one listener, so delivery is a real local
    # counterpart, not upstream server-push.
    "sessions.subscribe": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_advanced.py",
        "qubit_counterpart": "voicebridge.session_subscriptions registry "
                             "(request-scoped read; shared with /qubit-chat)",
        "basis": ("real in-process subscriber registry instead of upstream "
                  "server-push fan-out; the registry is the one subscriber "
                  "authority every surface consults"),
    },
    "sessions.messages.subscribe": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_advanced.py",
        "qubit_counterpart": "voicebridge.session_subscriptions registry "
                             "(message-scoped subscription)",
        "basis": ("real in-process message-subscription registry; delivery is "
                  "request-scoped instead of upstream server-push"),
    },
    "sessions.messages.unsubscribe": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_SUBSCRIPTIONS_LOCAL_REGISTRY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_advanced.py",
        "qubit_counterpart": "voicebridge.session_subscriptions.unsubscribe",
        "basis": ("removes the real registry entry; symmetric with the local "
                  "subscribe counterpart"),
    },
    "sessions.viewers.set": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_VIEWERS_LOCAL_REGISTRY",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_advanced.py",
        "qubit_counterpart": "voicebridge.session_subscriptions.set_viewers",
        "basis": ("sets the real viewer set on the in-process registry; Qubit "
                  "is single-operator so there is no cloud viewer directory"),
    },
    # ---- sessions compaction/lineage (2026-09-21) ----
    # Upstream switches a leaf pointer and stores compaction checkpoints in a
    # file-backed store. Qubit realizes the same observable state through its
    # one transcript authority (restore-in-place over the real soft-archived
    # rows) and a Qubit-owned checkpoint registry in the SAME state.db.
    "sessions.branches.switch": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_BRANCH_SWITCH_LOCAL_RESTORE",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_compaction.py",
        "qubit_counterpart": "voicebridge.session_compaction.switch_branch "
                             "(restore target transcript in place)",
        "basis": ("upstream flips a leaf pointer; Qubit's single transcript "
                  "authority restores the target branch/checkpoint transcript "
                  "in place, recoverable via the soft-archived rows"),
    },
    "sessions.compaction.branch": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_COMPACTION_BRANCH_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_compaction.py",
        "qubit_counterpart": "voicebridge.session_compaction.branch "
                             "(DB checkpoint registry + transcript copy)",
        "basis": ("upstream uses a file-backed checkpoint store; Qubit records "
                  "the real checkpoint in the same state.db and creates a real "
                  "branch session from the preserved rows"),
    },
    "sessions.compaction.restore": {
        "status": "SAFE_EQUIVALENT",
        "code": "SESSIONS_COMPACTION_RESTORE_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/sessions_compaction.py",
        "qubit_counterpart": "voicebridge.session_compaction.restore "
                             "(replace_messages archive_dropped=True)",
        "basis": ("upstream restores from a file-backed checkpoint store; "
                  "Qubit restores the real preserved rows through the canonical "
                  "store, soft-archiving what it replaces"),
    },
    # ---- approvals / devices / attach (2026-09-21) ----
    # Qubit's command approval is the shared confirmation journal + broker
    # capability policy; there is no gateway-writable allowlist file. Node
    # actions are capability- and confirmation-gated, with no node-side exec
    # allowlist. Attach needs an MCP loopback attach runtime Qubit does not run.
    "exec.approvals.set": {
        "status": "SAFE_EQUIVALENT",
        "code": "EXEC_APPROVALS_LOCAL_JOURNAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/security.py",
        "qubit_counterpart": "`qubit approvals` + broker capability policy",
        "basis": ("upstream edits a gateway-writable exec-approvals allowlist "
                  "file; Qubit enforces approval through the shared confirmation "
                  "journal and broker policy, so this surfaces the real current "
                  "policy with its own code"),
    },
    "exec.approvals.node.get": {
        "status": "SAFE_EQUIVALENT",
        "code": "EXEC_APPROVALS_NODE_LOCAL_POLICY",
        "evidence": "voicebridge/gateway/openclaw_handlers/security.py",
        "qubit_counterpart": "voicebridge.node_host.show/capabilities + broker "
                             "confirmation policy",
        "basis": ("upstream reads a per-node exec-approvals allowlist; Qubit "
                  "nodes are capability- and confirmation-gated, so this reads "
                  "the real node policy with its own code"),
    },
    "exec.approvals.node.set": {
        "status": "OPERATOR_ONLY",
        "code": "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/security.py",
        "operator_prerequisite": ("a paired node agent that owns its own exec "
                                  "policy"),
        "qubit_counterpart": "`qubit node enroll/revoke` + broker confirmation "
                             "policy",
    },
    "attach.grant": {
        "status": "OPERATOR_ONLY",
        "code": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/security.py",
        "operator_prerequisite": ("an MCP loopback attach server + a paired "
                                  "browser device identity"),
        "qubit_counterpart": "voicebridge.session_share + device tokens",
    },
    "attach.revoke": {
        "status": "OPERATOR_ONLY",
        "code": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/security.py",
        "operator_prerequisite": ("an MCP loopback attach server + a paired "
                                  "browser device identity"),
        "qubit_counterpart": "voicebridge.session_share + device tokens",
    },
    # ---- agents / agents.workspace family (2026-09-21) ----
    # Read-only workspace browsing delegates to the canonical scoped file
    # authority (`voicebridge.files`) and is derived IMPLEMENTED mechanically
    # (agents.workspace.list/get, agents.files.list/get). The roster mutations
    # and the gateway workspace write carry typed refusals with a real local
    # counterpart because Qubit is one unified agent.
    "agents.files.set": {
        "status": "SAFE_EQUIVALENT",
        "code": "AGENTS_FILES_SET_BROKERED_WRITE",
        "evidence": "voicebridge/gateway/openclaw_handlers/agents.py",
        "qubit_counterpart": ("brokered qubit_runtime.tools.file_operations "
                              "write + the shared confirmation journal"),
        "basis": ("upstream writes allowlisted agent workspace files directly "
                  "through the gateway; Qubit exposes no gateway workspace "
                  "write and routes writes through the brokered file tool "
                  "under confirmation, so this returns its own code"),
    },
    "agent.wait": {
        "status": "SAFE_EQUIVALENT",
        "code": "AGENT_WAIT_LOCAL_TASK_STATE",
        "evidence": "voicebridge/gateway/openclaw_handlers/agents.py",
        "qubit_counterpart": ("voicebridge.task_ledger.show + CLI "
                              "`qubit tasks show`"),
        "basis": ("upstream blocks until an agent run reaches a terminal "
                  "state; Qubit reports the canonical task/run state with its "
                  "own code and never blocks-waits on a turn"),
    },
    "agents.create": {
        "status": "OPERATOR_ONLY",
        "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/agents.py",
        "operator_prerequisite": ("an upstream multi-agent runtime; Qubit is "
                                  "intentionally a single unified agent"),
        "qubit_counterpart": ("the single canonical agent `qubit-worker` "
                              "managed via config + the provider registry"),
    },
    "agents.update": {
        "status": "OPERATOR_ONLY",
        "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/agents.py",
        "operator_prerequisite": ("an upstream multi-agent runtime; Qubit is "
                                  "intentionally a single unified agent"),
        "qubit_counterpart": ("the single canonical agent `qubit-worker` "
                              "managed via config + the provider registry"),
    },
    "agents.delete": {
        "status": "OPERATOR_ONLY",
        "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/agents.py",
        "operator_prerequisite": ("an upstream multi-agent runtime; Qubit is "
                                  "intentionally a single unified agent"),
        "qubit_counterpart": ("the single canonical agent `qubit-worker` "
                              "managed via config + the provider registry"),
    },
    # ---- mcp.app family (2026-09-21) ----
    # mcp.app.callTool is derived IMPLEMENTED (registered handler delegates to
    # `plugin_runtime.invoke`, the canonical e-stop + confirmation-gated
    # in-process tool host). The resource operations have no gateway resource
    # endpoint, so they carry typed refusals naming the real local counterpart
    # (the brokered MCP tool layer) or the missing runtime.
    "mcp.app.listResources": {
        "status": "SAFE_EQUIVALENT",
        "code": "MCP_RESOURCES_LOCAL_TOOL",
        "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py",
        "qubit_counterpart": ("the MCP tool layer's `list_resources` "
                              "(qubit_runtime/tools/mcp_tool.py)"),
        "basis": ("upstream lists resources from an MCP app sandbox; Qubit's "
                  "canonical MCP host is tools-only and resources are served "
                  "through the brokered agent tool layer, so this returns its "
                  "own code plus the configured server roster"),
    },
    "mcp.app.readResource": {
        "status": "SAFE_EQUIVALENT",
        "code": "MCP_READ_RESOURCE_LOCAL_TOOL",
        "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py",
        "qubit_counterpart": ("the MCP tool layer's `read_resource` "
                              "(qubit_runtime/tools/mcp_tool.py)"),
        "basis": ("upstream reads a resource through an MCP app sandbox; Qubit "
                  "exposes resources only through the brokered agent tool "
                  "layer, so this returns its own code"),
    },
    "mcp.app.listResourceTemplates": {
        "status": "OPERATOR_ONLY",
        "code": "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py",
        "operator_prerequisite": ("an MCP server/runtime that advertises "
                                  "resource templates"),
        "qubit_counterpart": ("the MCP tool layer's `list_resources` "
                              "(qubit_runtime/tools/mcp_tool.py)"),
    },
    "mcp.app.updateModelContext": {
        "status": "OPERATOR_ONLY",
        "code": "MCP_APP_SANDBOX_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py",
        "operator_prerequisite": ("an MCP App sandbox runtime + an active app "
                                  "view"),
        "qubit_counterpart": ("voicebridge.plugin_runtime.mcp_doctor "
                              "(in-process MCP host diagnostics)"),
    },
    # ---- board family (2026-09-21) ----
    # Qubit's canonical board is the slug-scoped kanban canvas. Tab ops are
    # applied for real; the session/name-addressed widget ops and the sandbox
    # view-ticket runtime have no local analogue, so they carry typed refusals
    # naming the real counterpart.
    "board.update": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/board.py",
        "basis": ("applies tab_create/tab_update/tab_delete/tabs_reorder "
                  "through the canonical kanban tab primitives and returns the "
                  "real board snapshot; name-addressed widget ops fail closed "
                  "atomically with a typed code because Qubit's board addresses "
                  "widgets by id and runs no session-scoped widget sandbox"),
    },
    "board.widget.grant": {
        "status": "SAFE_EQUIVALENT",
        "code": "BOARD_WIDGET_GRANT_LOCAL_ROLES",
        "evidence": "voicebridge/gateway/openclaw_handlers/board.py",
        "qubit_counterpart": ("`qubit board role grant` (kanban.role_grant / "
                              "tab_role_grant)"),
        "basis": ("upstream decides a per-widget grant by name/revision/"
                  "instanceId; Qubit grants board capabilities through the "
                  "canonical kanban role authority, so this returns its own "
                  "code plus the real role roster"),
    },
    "board.event": {
        "status": "OPERATOR_ONLY",
        "code": "BOARD_EVENT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/board.py",
        "operator_prerequisite": ("a board widget sandbox runtime + signed "
                                  "view tickets"),
        "qubit_counterpart": "`qubit board log` (kanban board event log)",
    },
    "board.prompt.authorize": {
        "status": "OPERATOR_ONLY",
        "code": "BOARD_VIEW_TICKET_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/board.py",
        "operator_prerequisite": "a signed board view-ticket runtime",
        "qubit_counterpart": ("`qubit board roles` + the shared confirmation "
                              "journal"),
    },
    "board.action": {
        "status": "OPERATOR_ONLY",
        "code": "BOARD_ACTION_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/board.py",
        "operator_prerequisite": ("a board widget sandbox + capability "
                                  "authority runtime"),
        "qubit_counterpart": ("`qubit cron` (schedules) + the shared "
                              "confirmation journal"),
    },
    # ---- surface-control family (2026-09-21) ----
    "controlUi.sessionPreview": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/surface_control.py",
        "basis": ("real projection over the canonical chat-session store "
                  "(`QubitSessionStore.show`, the one SQLite state.db "
                  "authority the dashboard Sessions page also reads)"),
    },
    "controlUi.sessionPullRequests.subscribe": {
        "status": "SAFE_EQUIVALENT",
        "code": "CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY",
        "evidence": "voicebridge/gateway/openclaw_handlers/surface_control.py",
        "qubit_counterpart": ("voicebridge.session_subscriptions."
                              "SessionSubscriptionRegistry (shared with "
                              "sessions.subscribe)"),
        "basis": ("upstream fans server-push PR-change events to a live "
                  "connection; Qubit keeps the SAME real subscriber registry "
                  "but delivers request-scoped, so this returns its own code "
                  "and the live subscription rows"),
    },
    "tools.invoke": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/surface_control.py",
        "basis": ("executes through the canonical `CapabilityBroker` "
                  "(`build_host_broker().execute`) — the e-stop, policy, shared "
                  "confirmation journal, and audit path the CLI, dashboard, and "
                  "voice all use; a mutating capability without a real "
                  "confirmation is denied, never fabricated"),
    },
    "assistant.media.get": {
        "status": "SAFE_EQUIVALENT",
        "code": "MEDIA_NOT_SERVED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "CLI `qubit files read` + Qubit Chat attachment vision",
        "basis": "Qubit serves no binary media data URLs by policy; the real counterpart is the bounded redacted text file read and in-browser attachment rendering (same typed MEDIA_NOT_SERVED code as the /api/media route)",
    },
    "chat.toolTitles": {
        "status": "SAFE_EQUIVALENT",
        "code": "TOOL_TITLES_AGENT_PROVIDED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.qubit_session_store (agent-provided tool activity)",
        "basis": "utility-model tool titles are retired upstream (the pinned handler returns titles:{} disabled:true); Qubit likewise generates none and reports the canonical session's real agent-provided tool activity",
    },
    "controlUi.githubPreview": {
        "status": "OPERATOR_ONLY",
        "code": "FORGE_API_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "operator_prerequisite": "a configured forge credential (e.g. a GitHub/GitLab token) and a network-reachable forge API",
        "qubit_counterpart": "local change review: CLI `qubit git status|log|diff|show` and the dashboard git review surface",
        "basis": "no remote GitHub preview is fetched; Qubit runs no forge service and stores no forge token by default, so the typed code is returned",
    },
    "cron.scratch.get": {
        "status": "OPERATOR_ONLY",
        "code": "CRON_SCRATCH_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
        "qubit_counterpart": "voicebridge.schedules job fields + memory workspace notes (`qubit memory note`)",
        "basis": "Qubit's schedule store has no private per-job scratch column and no scratch store exists, so nothing is read",
    },
    "cron.scratch.set": {
        "status": "OPERATOR_ONLY",
        "code": "CRON_SCRATCH_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
        "qubit_counterpart": "voicebridge.schedules job fields + memory workspace notes (`qubit memory note`)",
        "basis": "Qubit's schedule store has no private per-job scratch column and no scratch store exists, so nothing is written",
    },
    "cron.update": {
        "status": "SAFE_EQUIVALENT",
        "code": "CRON_UPDATE_LOCAL_PAUSE_RESUME",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.schedules.set_paused / delete_job (`qubit cron pause|resume|rm`)",
        "basis": "upstream patches arbitrary cron job fields; Qubit's canonical schedule store updates only the enabled flag, so the real pause/resume mutation is applied for an `enabled` patch and other fields are refused with the own code, never fabricated",
    },
    "doctor.memory.resetGroundedShortTerm": {
        "status": "SAFE_EQUIVALENT",
        "code": "MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.memory.workspace.forget_note + list_notes/providers",
        "basis": "Qubit's canonical memory workspace is markdown-note based and has no grounded-short-term candidate artifact; the handler reads the real note inventory or forgets a named note through the canonical workspace",
    },
    "gateway.suspend.handoff": {
        "status": "OPERATOR_ONLY",
        "code": "SUSPEND_HANDOFF_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a service manager that owns process exit (systemd/launchd external restart owner)",
        "qubit_counterpart": "operator `systemctl --user restart <qubit unit>` + the operator runbook",
        "basis": "upstream arms a host-lifecycle handoff that exits the gateway process; Qubit never exits its own process from a gateway call",
    },
    "gateway.suspend.prepare": {
        "status": "SAFE_EQUIVALENT",
        "code": "SUSPEND_PREPARE_LOCAL_POWER",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.power.PowerControl.status (`qubit power status`)",
        "basis": "upstream drains in-flight work and pauses the scheduler for a cooperative process suspension; Qubit reports the real local power/e-stop state and never drains or suspends its process",
    },
    "gateway.suspend.resume": {
        "status": "SAFE_EQUIVALENT",
        "code": "SUSPEND_RESUME_LOCAL_POWER",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.power.PowerControl.reset / global_enable (`qubit power reset` / `qubit power enable`)",
        "basis": "upstream releases a prepared suspension; Qubit has no suspension to release, so it reports the real local power/e-stop state and fabricates no resume",
    },
    "mentions.dismiss": {
        "status": "OPERATOR_ONLY",
        "code": "MENTIONS_INBOX_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a human-mention inbox service backed by configured message channels",
        "qubit_counterpart": "voicebridge.automation outbox/orders (`qubit channels`)",
        "basis": "Qubit runs no human-mention inbox service, so no mention is dismissed",
    },
    "mentions.list": {
        "status": "OPERATOR_ONLY",
        "code": "MENTIONS_INBOX_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a human-mention inbox service backed by configured message channels",
        "qubit_counterpart": "voicebridge.automation outbox/orders (`qubit channels`)",
        "basis": "Qubit runs no human-mention inbox service, so no mention is listed",
    },
    "models.authOrderSet": {
        "status": "SAFE_EQUIVALENT",
        "code": "AUTH_ORDER_LOCAL_PROFILE_PRIORITY",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.model_profiles_ops.select_session_model + list_model_profiles",
        "basis": "Qubit has no multi-credential auth store to reorder; provider selection is by canonical model profile (select_session_model) with its role/fallback chain, so the handler reports the real local profile ordering",
    },
    "nativeHook.invoke": {
        "status": "OPERATOR_ONLY",
        "code": "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "operator_prerequisite": "a live native harness process exposing a registered hook relay (provider/relayId/generation)",
        "qubit_counterpart": "voicebridge.hooks (canonical hook declarations) + voicebridge.node_host",
        "basis": "relay invocation is a one-shot bridge into a live native harness; Qubit runs no native harness relay, so no hook is invoked",
    },
    "node.event": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.invoke.progress": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.invoke.result": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.pending.ack": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.pending.drain": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.pending.enqueue": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.pending.pull": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.pluginTools.update": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.rename": {
        "status": "SAFE_EQUIVALENT",
        "code": "NODE_RENAME_LOCAL_REENROLL",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "qubit_counterpart": "voicebridge.node_host.revoke + voicebridge.node_host.enroll",
        "basis": "upstream renames a paired node label in place; Qubit's node host records a label only at enroll time and has no in-place rename primitive, so this confirmation-gated handler revokes and re-enrolls the same endpoint/capabilities under the new label (a new node id)",
    },
    "node.runnerInventory.update": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "node.skills.update": {
        "status": "OPERATOR_ONLY",
        "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
        "qubit_counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
        "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
    },
    "plugins.sessionAction": {
        "status": "SAFE_EQUIVALENT",
        "code": "PLUGIN_SESSION_ACTION_LOCAL_INVOKE",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.plugin_runtime.invoke (in-process plugin handler host; e-stop + shared confirmation journal)",
        "basis": "upstream invokes a plugin-declared session action through a session-extension registry; Qubit invokes the real in-process plugin handler through the canonical plugin runtime, a different contract, and emits its own code",
    },
    "plugins.uiDescriptors": {
        "status": "SAFE_EQUIVALENT",
        "code": "PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.qubit_plugins.dashboard_surface + voicebridge.plugin_runtime.list_plugins",
        "basis": "upstream reads a plugin-declared Control-UI descriptor registry; Qubit exposes the canonical plugin dashboard-surface registry instead and emits its own code",
    },
    "portal.close": {
        "status": "OPERATOR_ONLY",
        "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
        "qubit_counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
        "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal proxy is closed",
    },
    "portal.list": {
        "status": "OPERATOR_ONLY",
        "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
        "qubit_counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
        "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal is listed",
    },
    "portal.open": {
        "status": "OPERATOR_ONLY",
        "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
        "qubit_counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
        "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal proxy is opened",
    },
    "progressCard.put": {
        "status": "SAFE_EQUIVALENT",
        "code": "PROGRESS_CARD_PUT_LOCAL_PROJECTION",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.session_activity.build_projection",
        "basis": "upstream writes a mutable progress card store; Qubit keeps no writable card store and builds the real read-only session activity projection instead",
    },
    "skills.bins": {
        "basis": "upstream collects skill executables from agent workspaces for node auto-allow; Qubit skills are read-only instructional/advisory material that ship no executables, so the handler returns the real bundled skill registry with an empty bins list",
        "code": "SKILLS_BINS_LOCAL_BUNDLED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills._bundled.bundled_skill_manifests (bundled skill registry)",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.curator.pin": {
        "basis": "upstream retired curator pin to a stub that always errors; Qubit keeps the real owner-gated pin on the verified-skill store",
        "code": "SKILLS_CURATOR_PIN_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.pin",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.curator.restore": {
        "basis": "upstream retired curator restore to a stub that always errors; Qubit restores the real digest-verified, human-only Skill Workshop collection snapshot",
        "code": "SKILLS_CURATOR_RESTORE_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.workshop.SkillWorkshop.restore/rollback",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.curator.unpin": {
        "basis": "upstream retired curator unpin to a stub that always errors; Qubit keeps the real owner-gated unpin on the verified-skill store",
        "code": "SKILLS_CURATOR_UNPIN_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.unpin",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.apply": {
        "basis": "upstream applies a pending proposal into the workspace; Qubit promotes the real quarantined verified-skill record through the owner-gated verdict",
        "code": "SKILLS_PROPOSALS_APPLY_LOCAL_VERDICT",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.apply_verdict(decision='verified')",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.evaluate": {
        "basis": "upstream runs an isolated model to evaluate a proposal; Qubit deliberately has no automated evaluator and verifies only by explicit human owner verdict",
        "code": "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "an isolated proposal-evaluation model runtime (Qubit has no self-evaluation path)",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.apply_verdict (human owner verdict) + voicebridge.evaluation promotion gate",
        "status": "OPERATOR_ONLY",
    },
    "skills.proposals.events.list": {
        "basis": "upstream lists per-proposal events from its proposal store; Qubit reads the real append-only verified-skill hash-chain journal",
        "code": "SKILLS_PROPOSALS_EVENTS_LOCAL_JOURNAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.state_journal.HashChainJournal (the verified-skill append-only journal)",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.historyScan": {
        "basis": "upstream scans session history to emit pending proposals; Qubit runs no session-history scanner",
        "code": "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "a session-history scan runtime (isolated model over the transcript) that emits pending proposals",
        "qubit_counterpart": "voicebridge.verified_skills.workshop.SkillWorkshop.author (authored/quarantined proposals)",
        "status": "OPERATOR_ONLY",
    },
    "skills.proposals.quarantine": {
        "basis": "upstream quarantines a proposal; Qubit performs the real verified-skill quarantine transition on the one store",
        "code": "SKILLS_PROPOSALS_QUARANTINE_LOCAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.quarantine_revoked",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.reject": {
        "basis": "upstream rejects a pending proposal; Qubit performs the real owner-gated refutation on the one verified-skill store",
        "code": "SKILLS_PROPOSALS_REJECT_LOCAL_VERDICT",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.VerifiedSkillStore.apply_verdict(decision='refuted')",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.requestRevision": {
        "basis": "upstream forwards free-text revision instructions to the owning agent chat session (chat.send followup); Qubit records them on the one canonical session authority",
        "code": "SKILLS_PROPOSALS_REQUESTREVISION_LOCAL_SESSION",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.qubit_session_store.QubitSessionStore.append (next turn consumes the instruction)",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.revise": {
        "basis": "upstream rewrites PROPOSAL.md in place; Qubit stages the real reviewed skill revision on the one durable confirmation journal",
        "code": "SKILLS_PROPOSALS_REVISE_LOCAL_STAGE",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.mutations.handle_skill_mutation (stages a reviewed revision on the shared confirmation journal)",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.proposals.update": {
        "basis": "upstream proposes an update to an existing skill; Qubit stages the real updated reviewed manifest on the one durable confirmation journal",
        "code": "SKILLS_PROPOSALS_UPDATE_LOCAL_STAGE",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.verified_skills.mutations.handle_skill_mutation (stages an updated reviewed manifest on the shared confirmation journal)",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.securityVerdicts": {
        "basis": "upstream fetches remote ClawHub skill security verdicts; Qubit runs no verdict service and fetches no remote verdicts",
        "code": "SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "a reachable skill security-verdict registry/endpoint and network access",
        "qubit_counterpart": "local bundled/verified skill validation (voicebridge.verified_skills._bundled + VerifiedSkillStore)",
        "status": "OPERATOR_ONLY",
    },
    "skills.update": {
        "basis": "upstream updates a skill config entry (enabled/key/env); Qubit mutates the real canonical permissioned skill record and refuses raw key/env values",
        "code": "SKILLS_UPDATE_LOCAL_CONFIG",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "qubit_counterpart": "voicebridge.permissions.PermissionsStore.enable_skill/disable_skill",
        "status": "SAFE_EQUIVALENT",
    },
    "skills.upload.begin": {
        "basis": "upstream begins a staged chunked skill-archive upload; Qubit runs no upload store and installs only reviewed local manifests",
        "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "a staged chunked upload store/transport for skill archives",
        "qubit_counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
        "status": "OPERATOR_ONLY",
    },
    "skills.upload.chunk": {
        "basis": "upstream stores an uploaded archive chunk; Qubit runs no chunked upload store",
        "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "a staged chunked upload store/transport for skill archives",
        "qubit_counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
        "status": "OPERATOR_ONLY",
    },
    "skills.upload.commit": {
        "basis": "upstream commits an uploaded skill archive for install; Qubit runs no upload store and downloads nothing",
        "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py",
        "operator_prerequisite": "a staged chunked upload store/transport for skill archives",
        "qubit_counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
        "status": "OPERATOR_ONLY",
    },
    "talk.client.close": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a client-owned realtime provider session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.client.create": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a client-owned realtime provider session (webrtc/provider-websocket) needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.client.steer": {
        "status": "SAFE_EQUIVALENT",
        "code": "TALK_CLIENT_STEER_LOCAL_TRANSCRIPT",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream delivers session-scoped active-run voice control into the running provider turn; Qubit has no mid-turn steering channel (the realtime lane was removed), so the spoken control is appended to the canonical chat session for the next turn",
    },
    "talk.client.toolCall": {
        "status": "SAFE_EQUIVALENT",
        "code": "TALK_CLIENT_TOOLCALL_LOCAL_BROKER",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "qubit_counterpart": "voicebridge.broker.CapabilityBroker.execute (e-stop + policy + shared confirmation journal + audit)",
        "basis": "upstream forwards a client-owned realtime provider tool call through gateway policy (first tool is the agent consult); Qubit has no client-owned provider transport, so the call runs through the canonical CapabilityBroker and a mutating capability without a real confirmation is denied, never fabricated",
    },
    "talk.client.transcript": {
        "status": "SAFE_EQUIVALENT",
        "code": "TALK_CLIENT_TRANSCRIPT_LOCAL_STORE",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "qubit_counterpart": "voicebridge.qubit_session_store.QubitSessionStore.append",
        "basis": "upstream appends a client-reported finalized {role,text} item with a per-voiceSession idempotent entryId; Qubit appends to the one canonical chat-session store and has no per-voiceSession entry-id dedupe",
    },
    "talk.mode": {
        "status": "SAFE_EQUIVALENT",
        "code": "TALK_MODE_LOCAL_SPEECH_PROFILE",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "qubit_counterpart": "voicebridge.providers.speech.resolve_speech_dict/list_speech_profiles + the /qubit-chat voice path",
        "basis": "upstream sets and broadcasts a Gateway Talk-mode state to WebChat/Control UI clients; Qubit keeps no Talk mode store and has no server-push lane, so the handler reports the real resolved local speech/voice profile and its own code",
    },
    "talk.session.acknowledgeMark": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a live realtime provider transport needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.appendAudio": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned realtime relay or transcription session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.cancelOutput": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned realtime relay session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.close": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned realtime/relay/transcription Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.create": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned realtime/relay/transcription Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.steer": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned agent-backed realtime Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "talk.session.submitToolResult": {
        "status": "OPERATOR_ONLY",
        "code": "TALK_REALTIME_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py",
        "operator_prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
        "qubit_counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
        "basis": "upstream a gateway-owned realtime relay provider bridge needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
    },
    "taskSuggestions.accept": {
        "status": "OPERATOR_ONLY",
        "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
        "qubit_counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
        "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is accepted or dispatched",
    },
    "taskSuggestions.create": {
        "status": "OPERATOR_ONLY",
        "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
        "qubit_counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
        "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is created",
    },
    "taskSuggestions.dismiss": {
        "status": "OPERATOR_ONLY",
        "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
        "qubit_counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
        "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is dismissed",
    },
    "tasks.cancel": {
        "status": "SAFE_EQUIVALENT",
        "code": "TASKS_CANCEL_LOCAL_RECEIPT",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.actions.ActionStore.cancel / voicebridge.chat_transport.cancel",
        "basis": "upstream cancels a detached task run by taskId; Qubit's task ledger is read-only and maps no taskId to a live run, so cancel delegates to the real action-receipt or in-flight chat-turn cancellation and emits its own code otherwise",
    },
    "tasks.dismiss": {
        "status": "OPERATOR_ONLY",
        "code": "TASKS_DISMISS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "operator_prerequisite": "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
        "qubit_counterpart": "the canonical read-only task ledger (`qubit tasks`)",
        "basis": "Qubit keeps no detached-task completion-delivery registry, so nothing is dismissed",
    },
    "tasks.retry": {
        "status": "SAFE_EQUIVALENT",
        "code": "TASKS_RETRY_LOCAL_RECEIPT",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.actions.ActionStore.retry / voicebridge.chat_transport.retry_last",
        "basis": "upstream retries a detached task's completion delivery; Qubit's ledger stores no retry arguments, so retry delegates to the real action-receipt retry (exact args) or last-turn retry and emits its own code otherwise",
    },
    "tts.disable": {
        "status": "SAFE_EQUIVALENT",
        "code": "TTS_DISABLE_LOCAL_PROFILE",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.providers.speech speech-out profile `enabled` + set_session_speech_profile",
        "basis": "upstream flips a global TTS auto-mode preference; Qubit has no global TTS enabled flag (the shipped default profile is immutable), so it surfaces the real per-speech-out-profile enabled state and never fabricates a global toggle",
    },
    "tts.enable": {
        "status": "SAFE_EQUIVALENT",
        "code": "TTS_ENABLE_LOCAL_PROFILE",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.providers.speech speech-out profile `enabled` + set_session_speech_profile",
        "basis": "upstream flips a global TTS auto-mode preference; Qubit has no global TTS enabled flag (the shipped default profile is immutable), so it surfaces the real per-speech-out-profile enabled state and never fabricates a global toggle",
    },
    "tts.setPersona": {
        "status": "SAFE_EQUIVALENT",
        "code": "TTS_PERSONA_LOCAL_PROFILE",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "qubit_counterpart": "voicebridge.providers.speech.set_session_speech_profile",
        "basis": "upstream selects a TTS persona bundle (provider + fallback + per-provider settings); Qubit keeps no persona-bundle store, so this selects a real persisted speech-out profile for a session and emits its own code",
    },
    "tts.setProvider": {
        "status": "IMPLEMENTED",
        "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py",
        "basis": "persists tts.provider through the canonical confirm-gated settings write (voicebridge.qubit_compat.config_save), the exact setting voicebridge/orchestrator._make_tts reads to build the TTS adapter; an unknown provider is a typed GATEWAY_BAD_PARAMS refusal",
    },
    "ui.command": {
        "status": "SAFE_EQUIVALENT",
        "code": "UI_COMMAND_LOCAL_PANELS",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.gateway.panels.control_panels + panel_layout",
        "basis": "upstream broadcasts a UI command to Control-UI clients; Qubit has no client-push lane and reads the canonical control-panel state applied through the dashboard",
    },
    "users.authConnect.answer": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a live external provider OAuth/connect service and network access",
        "qubit_counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
        "basis": "no external auth-connect session exists to answer; the typed code is returned",
    },
    "users.authConnect.cancel": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a live external provider OAuth/connect service and network access",
        "qubit_counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
        "basis": "no external auth-connect session exists to cancel; the typed code is returned",
    },
    "users.authConnect.catalog": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.providers.policy.FrontierPolicy configured providers + `qubit auth login`",
        "basis": "upstream catalogs external auth-connect providers/methods; Qubit returns its real configured provider registry and manual-handoff portals and emits its own code",
    },
    "users.authConnect.start": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a live external provider OAuth/connect service and network access",
        "qubit_counterpart": "CLI `qubit auth login --provider <name>` (manual handoff; Qubit never performs the OAuth dance)",
        "basis": "starting an external provider auth-connect session needs a live OAuth service Qubit does not run; the typed code is returned",
    },
    "users.authConnect.status": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a live external provider OAuth/connect service and network access",
        "qubit_counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
        "basis": "no external auth-connect session exists to report; the typed code is returned",
    },
    "users.github.authorize.cancel": {
        "status": "OPERATOR_ONLY",
        "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a started GitHub device-flow authorization (requires the live OAuth app)",
        "qubit_counterpart": "nothing to cancel locally; remove the credential reference with the operator's own tooling",
        "basis": "no GitHub device-flow authorization exists to cancel; the typed code is returned",
    },
    "users.github.authorize.poll": {
        "status": "OPERATOR_ONLY",
        "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a started GitHub device-flow authorization (requires the live OAuth app)",
        "qubit_counterpart": "CLI `qubit auth verify --provider github` (reference presence only)",
        "basis": "Qubit polls no external OAuth service and has no device-flow session in flight; the typed code is returned",
    },
    "users.github.authorize.start": {
        "status": "OPERATOR_ONLY",
        "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a live GitHub OAuth app/device-flow service and network access",
        "qubit_counterpart": "CLI `qubit auth login --provider github` (manual handoff in the operator's browser)",
        "basis": "the GitHub device-flow authorization needs a live github.com OAuth app Qubit does not run; no device flow is started and the typed code is returned",
    },
    "users.github.disconnect": {
        "status": "OPERATOR_ONLY",
        "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a linked GitHub account (requires the live OAuth app)",
        "qubit_counterpart": "voicebridge.credentials.remove (confirm-gated) removes the credential reference",
        "basis": "Qubit holds no personal GitHub connection to disconnect; the typed code is returned",
    },
    "users.github.status": {
        "status": "OPERATOR_ONLY",
        "code": "GITHUB_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a GitHub credential reference (env:/file:) with the required scopes and network access",
        "qubit_counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
        "basis": "Qubit stores credential references only and runs no GitHub OAuth/app service, so no personal GitHub status is fetched; the typed code is returned",
    },
    "users.linkAuthProfile": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.credentials.add_reference (confirm-gated; references only)",
        "basis": "upstream links a shared credential to a user profile; Qubit is single-operator, so it performs the real confirm-gated credential-pool add from an env:/file: reference and emits its own code",
    },
    "users.linkEmail": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_EMAIL_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "an email/identity verification service",
        "qubit_counterpart": "voicebridge.qubit_compat.h_auth_me (loopback identity; email empty)",
        "basis": "Qubit has no email identity service and stores no operator email; the typed code is returned",
    },
    "users.list": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_LIST_LOCAL_SINGLE_OPERATOR",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.qubit_compat.h_auth_me (single loopback operator)",
        "basis": "upstream lists a multi-user profile directory; Qubit is single-operator with no profile store, so it returns the one real loopback operator identity and emits its own code",
    },
    "users.listAuthLinks": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_AUTH_LINKS_LOCAL_CREDENTIALS",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.auth_status.auth_status (credential references, names only)",
        "basis": "upstream lists per-user linked auth profiles; Qubit is single-operator with no per-user links, so it returns the canonical credential-reference inventory and emits its own code",
    },
    "users.listModelAccounts": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_MODEL_ACCOUNTS_LOCAL_PROFILES",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.model_profiles_ops.list_model_profiles",
        "basis": "upstream lists per-user model accounts; Qubit returns its canonical single-operator model-profile registry and emits its own code",
    },
    "users.mentionable": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_MENTIONABLE_LOCAL_OPERATOR",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.qubit_compat.h_auth_me (the one mentionable operator)",
        "basis": "upstream exposes a multi-user mention directory; Qubit has one operator, so the mentionable set is that real identity and the handler emits its own code",
    },
    "users.prefs.get": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_PREFS_LOCAL_DASHBOARD",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.qubit_compat dashboard prefs (font/theme handlers)",
        "basis": "upstream reads arbitrary per-profile key/values; Qubit returns the real persisted dashboard preferences (font/theme) and emits its own code",
    },
    "users.prefs.set": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_PREFS_LOCAL_DASHBOARD",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.qubit_compat h_font(setter) + h_theme_set",
        "basis": "upstream writes arbitrary per-profile key/values; Qubit persists only its real supported dashboard preferences (font/theme) and emits its own code",
    },
    "users.selectModelAccount": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_MODEL_ACCOUNT_LOCAL_SELECT",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.model_profiles_ops.select_session_model (confirm-gated)",
        "basis": "upstream selects a per-user model account; Qubit performs the real confirm-gated session->profile selection in its canonical registry and emits its own code",
    },
    "users.self": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_SELF_LOCAL_OPERATOR",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.qubit_compat.h_auth_me (loopback session identity)",
        "basis": "upstream resolves the authenticated multi-user profile; Qubit returns its one real loopback operator identity instead and emits its own code",
    },
    "users.setAvatar": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_AVATAR_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "an operator-managed avatar/media store and hosting",
        "qubit_counterpart": "voicebridge.device.profile (local device identity; no avatar field)",
        "basis": "Qubit keeps no operator avatar/media store and hosts no avatar media, so no avatar is written; the typed code is returned",
    },
    "users.setDisplayName": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_PROFILE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a multi-user profile store (Qubit is single-operator)",
        "qubit_counterpart": "voicebridge.qubit_compat.h_auth_me (fixed operator display name)",
        "basis": "Qubit is single-operator with no mutable multi-user profile store, so no display name is written; the typed code is returned",
    },
    "users.setRole": {
        "status": "OPERATOR_ONLY",
        "code": "USERS_PROFILE_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "operator_prerequisite": "a multi-user profile/role store (Qubit is single-operator)",
        "qubit_counterpart": "the fixed operator scopes advertised by the gateway `connect` handshake",
        "basis": "Qubit has no multi-user role store and fixed operator scopes, so no role is assigned; the typed code is returned",
    },
    "users.unlinkAuthProfile": {
        "status": "SAFE_EQUIVALENT",
        "code": "USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL",
        "evidence": "voicebridge/gateway/openclaw_handlers/users.py",
        "qubit_counterpart": "voicebridge.credentials.remove (confirm-gated; references only)",
        "basis": "upstream unlinks a credential from a user profile; Qubit performs the real confirm-gated credential-pool removal and emits its own code",
    },
    "voicewake.routing.get": {
        "status": "OPERATOR_ONLY",
        "code": "WAKE_ROUTING_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "operator_prerequisite": "a configurable wake-trigger/routing store",
        "qubit_counterpart": "voicewake.get + voicebridge.wake.status (single shared audio listener)",
        "basis": "Qubit has no voice-wake routing configuration; wake detection routes through the one audio listener, so no routing config exists to read",
    },
    "voicewake.set": {
        "status": "OPERATOR_ONLY",
        "code": "WAKE_TRIGGERS_OPERATOR_REQUIRED",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "operator_prerequisite": "a configurable wake-trigger store (writable keywords/model bundle)",
        "qubit_counterpart": "voicewake.get + voicebridge.wake.status (single shared audio listener)",
        "basis": "Qubit has no writable wake-trigger store; wake phrases come from the operator-fetched sherpa-onnx keyword bundle, so no triggers are persisted",
    },
    "wake": {
        "status": "SAFE_EQUIVALENT",
        "code": "WAKE_LOCAL_AUTOMATION",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.automation.queue_event + heartbeat_due (automation tick)",
        "basis": "upstream wakes the agent immediately or at the next heartbeat; Qubit queues a real internal automation event for the canonical tick (confirmation-gated) instead of a separate wake channel",
    },
    "wizard.cancel": {
        "status": "SAFE_EQUIVALENT",
        "code": "WIZARD_CANCEL_LOCAL_QUESTION_STORE",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.question_prompt + voicebridge.lifecycle.audio_wizard",
        "basis": "Qubit has no step-wizard session registry to cancel; a pending prompt is settled through question.resolve and the audio wizard is a stateless readiness report, so the handler reports the real local state",
    },
    "wizard.next": {
        "status": "SAFE_EQUIVALENT",
        "code": "WIZARD_NEXT_LOCAL_QUESTION_STORE",
        "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py",
        "qubit_counterpart": "voicebridge.question_prompt.answer + question_prompt.pending",
        "basis": "Qubit has no step-wizard session registry; the real counterpart is the canonical question-prompt store, which advances by answering a pending prompt and then reports the remaining pending prompts",
    },
}
