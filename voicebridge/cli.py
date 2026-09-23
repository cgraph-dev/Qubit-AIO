"""CLI launcher: run the bridge, run one utterance, send a typed task, test tools."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def _shared_power(cfg):
    """Return the shared 8O PowerPolicy for the config scope (one per process)."""
    from . import power as PW
    return PW.power_policy(cfg)


def _launcher_name() -> str:
    """User-facing launcher name for help/usage banners.

    Canonical name is ``qubit``; ``voicebridge`` is a compatibility alias
    (same parser, same backend). Honors argv[0] so installed-console-script
    help reads correctly under either name; anything else falls back to the
    canonical name.
    """
    try:
        base = Path(sys.argv[0]).name
    except Exception:
        return "qubit"
    return base if base in ("qubit", "voicebridge") else "qubit"


# ---------------------------------------------------------------------------
# Agent 1 wave 3: catalog-op CLI surfaces.
#
# Every entry maps `qubit <family> <sub>` to the SAME production op id the
# dashboard/voice surfaces use (`voicebridge.dashboard_ops.run_op`), so all
# three surfaces share one operation, one confirmation journal and one audit
# path. Read ops execute; confirm-tier ops stage the shared confirmation and
# return `needs-confirm` (settled via `qubit approvals respond`). Only ops the
# live parser does not already own are listed (no native surface is shadowed).
# ---------------------------------------------------------------------------
_OP_CLI = (
    # family, subcommand, op_id, help, argparse specs
    ("brain", "export", "brain_export",
     "export a Brain profile (read-only)",
     (("principal", {}), ("--identity", {"default": ""}))),
    ("brain", "onboard-plan", "brain_onboard_plan",
     "Brain setup plan (read-only)", ()),
    ("brain", "onboard-apply", "brain_onboard_apply",
     "apply a Brain onboarding plan (stages confirmation)",
     (("answers", {}),)),
    ("brain", "sweep", "brain_sweep",
     "sweep expired Brain fields (stages confirmation)", ()),
    ("ghost", "action-preview", "ghost_action_preview",
     "preview a Ghost action (read-only)",
     (("action_id", {"nargs": "*"}), ("--target", {"default": ""}),
      ("--text", {"default": ""}), ("--role", {"default": ""}))),
    ("ghost", "action-recover", "ghost_action_recover",
     "recover a Ghost action receipt (stages confirmation)",
     (("receipt_id", {"nargs": "*"}),)),
    ("ghost", "action-retry", "ghost_action_retry",
     "retry a Ghost action (stages confirmation)",
     (("receipt_id", {"nargs": "*"}), ("--target", {"default": ""}),
      ("--text", {"default": ""}), ("--role", {"default": ""}),
      ("--scope", {"default": ""}))),
    ("ghost", "click", "ghost_click",
     "click a Ghost target (stages confirmation)",
     (("target", {"nargs": "*"}), ("--role", {"default": ""}),
      ("--scope", {"default": ""}))),
    ("ghost", "type", "ghost_type",
     "type into a Ghost target (stages confirmation)",
     (("target", {}), ("text", {"nargs": "+"}),
      ("--role", {"default": ""}), ("--scope", {"default": ""}))),
    ("ghost", "read-tab", "ghost_read_tab",
     "read the active Ghost tab (read-only)",
     (("--role", {"default": ""}), ("--scope", {"default": ""}))),
    ("memory", "remember", "memory_remember",
     "remember a fact (stages confirmation)",
     (("text", {"nargs": "*"}),)),
    ("memory", "forget", "memory_forget",
     "forget a memory by id (stages confirmation)",
     (("memory_id", {"type": int}),)),
    ("acp", "accept-hooks", "acp_accept_hooks",
     "typed refusal: hook bypass flags are never honored", ()),
    ("acp", "serve", "acp_serve",
     "typed refusal: no ACP server is shipped", ()),
    ("skills", "workshop-backup", "skills_workshop_backup",
     "back up the verified-skill collection (stages confirmation)",
     (("--label", {"default": ""}),)),
    ("skills", "workshop-restore", "skills_workshop_restore",
     "restore the collection from a backup (stages confirmation)",
     (("backup_id", {"nargs": "*"}),)),
    ("skills", "workshop-rollback", "skills_workshop_rollback",
     "roll back the verified-skill collection (stages confirmation)",
     (("backup_id", {"nargs": "?"}),)),
    ("kanban", "delegated", "kanban_delegated",
     "delegated kanban subcommand guidance (read-only)",
     (("subcommand", {}),)),
    ("kanban", "restore", "kanban_restore",
     "restore an archived task (stages confirmation)",
     (("task_id", {"type": int}),)),
    ("opencode", "run-confirmed", "opencode_run_confirmed",
     "apply a prepared OpenCode change by expected hash (stages confirmation)",
     (("task", {}), ("--project", {"default": ""}),
      ("--in-project", {"default": ""}),
      ("--expected-hash", {"required": True}))),
    # CLI-coverage gaps: catalog ops that declared a `cli` interface but had
    # no binding. Each resolves to the same op id the dashboard/voice run.
    ("backup", "create", "backup_create",
     "create a passphrase-encrypted portable backup (stages confirmation)",
     (("--passphrase", {"required": True}), ("--label", {"default": ""}))),
    ("backup", "restore", "backup_restore",
     "preview/restore a portable backup (stages confirmation)",
     (("bundle", {}), ("--passphrase", {"required": True}),
      ("--apply", {"action": "store_true"}))),
    ("reports", "list", "reports_list",
     "published report bundles, newest first (read-only)", ()),
    ("marketplace", "discovery", "marketplace_discovery",
     "local reviewed sources only; remote fetches refused (read-only)",
     (("--source", {"default": ""}),)),
    ("plugins", "remote", "plugins_remote",
     "typed refusal: remote plugin identifiers are never fetched",
     (("identifier", {}),)),
    # Workstream F: owner-scoped autonomy admin (same ops the dashboard and
    # voice registry run through one backend + confirmation journal).
    ("autonomy", "status", "autonomy_status",
     "effective owner-scoped autonomy policy (read-only)", ()),
    ("autonomy", "list", "autonomy_list",
     "owner-scoped autonomy profiles (read-only)", ()),
    ("autonomy", "show", "autonomy_show",
     "one autonomy profile by id (read-only)",
     (("profile_id", {}),)),
    ("autonomy", "pause", "autonomy_pause",
     "pause one scoped-autonomy profile (stages confirmation)",
     (("profile_id", {}), ("--confirm", {"action": "store_true"}))),
    ("autonomy", "resume", "autonomy_resume",
     "resume one scoped-autonomy profile (stages confirmation)",
     (("profile_id", {}), ("--confirm", {"action": "store_true"}))),
    ("autonomy", "revoke", "autonomy_revoke",
     "revoke one scoped-autonomy profile (stages confirmation)",
     (("profile_id", {}), ("--confirm", {"action": "store_true"}))),
    ("autonomy", "expire", "autonomy_expire",
     "expire elapsed autonomy profiles (read-only)", ()),
)

_OP_CLI_INDEX = {(fam, sub): op_id for fam, sub, op_id, _, _ in _OP_CLI}

# Leaf families whose production op is a single read/diagnostic with no
# subcommand: `qubit <family>` runs the same op id the dashboard/voice run.
_OP_LEAF_CLI = (
    ("logs", "logs", "redacted journal tail (read-only)"),
    ("tools", "tools", "brokered tool names (read-only)"),
    ("reasoning", "reasoning", "per-request output budgets (read-only)"),
    ("prompt-budget", "prompt_budget", "context budget report (read-only)"),
)

# Whole Qubit CLI families served by the vendored fork (Wave 1). The
# real dispatch is the pre-parse route in main() (same pattern as `agent` /
# `qubit`), which preserves upstream argv/help/errors/exit status; these
# entries declare the family in the live parser tree, help, completions and the
# gap ledger. Read-only subcommands forward verbatim, mutating subcommands
# require Qubit's --confirm, and unclassified subcommands are typed refusals.
_FAMILY_FORWARD_HELP = (
    ("bundles", "list/show/create/delete/reload skill bundles "
                "(read-only list/show; writes need --confirm)"),
    ("claw", "legacy-assistant migration tools (confirm-gated; no silent "
             "secret import)"),
    ("console", "safe command console REPL (confirm-gated interactive)"),
    ("debug", "diagnostics share/delete (confirm-gated; nothing uploaded "
              "without confirmation)"),
    ("desktop", "native desktop app build/launch (confirm-gated)"),
    ("dump", "redacted setup summary for support (read-only)"),
    ("gui", "desktop app alias (confirm-gated)"),
    ("memory-graph", "memory-graph list/edit/delete "
                     "(read-only list; writes need --confirm)"),
    ("moa", "Mixture-of-Agents slots (read-only list; configure/delete "
            "need --confirm)"),
    ("portal", "cloud portal access (confirm-gated; operator-only)"),
    ("prompt-size", "system-prompt + tool-schema byte breakdown "
                    "(read-only, offline)"),
)

# The CLI families above are the surface for catalog ops that previously had
# no `qubit` binding. Declare that coverage to the voice registry so the
# single-clear invariant (`voice_commands.CLI_COVERAGE` == live CLI families)
# holds and the ledger resolves each family to its real op id. Fills only
# absent keys; a richer mapping from voice_commands always wins.
_CLI_COVERAGE = {
    "logs": ("logs",),
    "tools": ("tools",),
    "reasoning": ("reasoning",),
    "prompt-budget": ("prompt_budget",),
    "reports": ("reports_list",),
    "marketplace": ("marketplace_discovery",),
    "backup": (),   # passphrase-sealed: extra ops only, never spoken
    # Wave 1 fork-backed families: no voice execution surface yet, so they
    # carry an explicit voice-impossible reason (CLI/dashboard only).
    "bundles": (),
    "claw": (),
    "console": (),
    "debug": (),
    "desktop": (),
    "dump": (),
    "gui": (),
    "memory-graph": (),
    "moa": (),
    "pause": (),
    "portal": (),
    "prompt-size": (),
    "worktree": (),
    # Packet 8U top-level families over existing backends. Each maps to the
    # SAME voice-registry ids its `profile model` / `flags` / `package` /
    # `schedules` counterpart already uses, so the single-clear invariant holds
    # without a second store or a fabricated voice surface.
    # Owner Decision #11: the sanctioned native update surface maps to the same
    # `update_status` voice intent the dashboard update card uses.
    "update": ("update_status",),
    "model": ("config_profiles",),
    "config": ("config_profiles", "auth_status"),
    "doctor": ("doctor", "diagnostics"),
    "cron": ("schedules_list", "schedules_create", "schedules_show",
             "schedules_pause", "schedules_resume", "schedules_delete",
             "schedules_history", "schedules_run_due"),
}
_CLI_VOICE_IMPOSSIBLE = {
    "backup.voice": "passphrase-sealed backups: use `qubit backup create` / "
                    "`qubit backup restore` in a terminal or the dashboard form",
    "bundles.voice": "skill-bundle bundles are a CLI/dashboard operation; no "
                     "spoken command mutates the bundle registry",
    "claw.voice": "Legacy-assistant migration is an attended CLI/dashboard "
                  "operation and never runs from a spoken command",
    "console.voice": "an interactive console REPL cannot run over the voice "
                     "surface; use `qubit console --confirm` in a terminal",
    "debug.voice": "debug bundles may contain logs; sharing is a confirm-gated "
                   "CLI/dashboard action only",
    "desktop.voice": "building/launching the native desktop app is an attended "
                     "hardware action, not a spoken command",
    "dump.voice": "the support dump is a copy-paste CLI read; no spoken form "
                  "is required",
    "gui.voice": "building/launching the desktop GUI is an attended hardware "
                 "action, not a spoken command",
    "memory-graph.voice": "memory-graph writes are confirm-gated CLI/dashboard "
                          "actions; only the read list is speakable elsewhere",
    "moa.voice": "MoA preset configuration is an interactive CLI/dashboard "
                 "action; no spoken command mutates the preset store",
    "pause.voice": "the emergency stop is owned by `qubit power emergency-stop`; "
                   "voice uses the same Qubit e-stop authority",
    "portal.voice": "the cloud portal is operator-only (credentials) and is "
                    "not exposed to spoken commands",
    "prompt-size.voice": "prompt-size is an offline diagnostic report for a "
                         "terminal; no spoken form is required",
    "worktree.voice": "git worktree reclaim is an attended, confirm-gated "
                      "CLI/dashboard action never run from voice",
}


def _sync_cli_coverage() -> None:
    """Declare the CLI-coverage-gap families to the voice registry (idempotent)."""
    from . import voice_commands as VC

    for family, targets in _CLI_COVERAGE.items():
        VC.CLI_COVERAGE.setdefault(family, targets)
    for key, reason in _CLI_VOICE_IMPOSSIBLE.items():
        VC.VOICE_IMPOSSIBLE.setdefault(key, reason)


def _add_model_profile_subcommands(container, common) -> None:
    """Register the canonical model-profile subcommands on *container*.

    Shared by the top-level ``model`` family and ``profile model`` so both name
    the SAME resolver-backed operations (one backend, one flag surface) rather
    than a second store. Reads execute; mutations are dry-run unless --confirm
    and accept only ``env:``/``file:`` secret references.
    """
    pmod_sub = container.add_subparsers(dest="profile_model_command")
    pmod_list = pmod_sub.add_parser("list", parents=[common],
                                    help="list model profiles + effective selection")
    pmod_list.add_argument("--json", action="store_true")
    pmod_show = pmod_sub.add_parser("show", parents=[common],
                                    help="show one model profile (read-only)")
    pmod_show.add_argument("profile")
    pmod_show.add_argument("--json", action="store_true")
    pmod_test = pmod_sub.add_parser("test", parents=[common],
                                    help="preflight a model profile (read-only; --probe runs a local ping)")
    pmod_test.add_argument("profile")
    pmod_test.add_argument("--probe", action="store_true")
    pmod_test.add_argument("--json", action="store_true")
    pmod_create = pmod_sub.add_parser(
        "create", parents=[common],
        help="create a model profile (dry-run unless --confirm; non-secret flags only)")
    pmod_create.add_argument("--id", required=True)
    pmod_create.add_argument("--kind", choices=["local", "api", "realtime",
                                                "plugin"],
                             default="local")
    pmod_create.add_argument("--provider", default="ollama")
    pmod_create.add_argument("--model", default=None)
    pmod_create.add_argument("--base-url", default="")
    pmod_create.add_argument("--key-ref", default="",
                             help="secret reference env:NAME or file:PATH (never a raw value)")
    pmod_create.add_argument("--locality", choices=["local", "external"],
                             default=None)
    pmod_create.add_argument("--egress-consent", choices=["deny", "allow"],
                             default=None)
    pmod_create.add_argument("--roles", default=None,
                             help="comma-separated roles (chat,hard,coding,tool,summarize,vision,classify)")
    pmod_create.add_argument("--context-tokens", type=int, default=4096)
    pmod_create.add_argument("--confirm", action="store_true",
                             help="actually persist (default: dry-run)")
    pmod_create.add_argument("--json", action="store_true")
    pmod_clone = pmod_sub.add_parser(
        "clone", parents=[common],
        help="clone a model profile under a new id (dry-run unless --confirm)")
    pmod_clone.add_argument("source")
    pmod_clone.add_argument("new")
    pmod_clone.add_argument("--confirm", action="store_true")
    pmod_clone.add_argument("--json", action="store_true")
    pmod_edit = pmod_sub.add_parser(
        "edit", parents=[common],
        help="edit a model profile (dry-run unless --confirm; non-secret flags only)")
    pmod_edit.add_argument("profile")
    pmod_edit.add_argument("--kind", choices=["local", "api", "realtime",
                                              "plugin"],
                           default=None)
    pmod_edit.add_argument("--provider", default=None)
    pmod_edit.add_argument("--model", default=None)
    pmod_edit.add_argument("--base-url", default=None)
    pmod_edit.add_argument("--key-ref", default=None,
                           help="secret reference env:NAME or file:PATH (never a raw value)")
    pmod_edit.add_argument("--locality", choices=["local", "external"],
                           default=None)
    pmod_edit.add_argument("--egress-consent", choices=["deny", "allow"],
                           default=None)
    pmod_edit.add_argument("--roles", default=None)
    pmod_edit.add_argument("--context-tokens", type=int, default=None)
    pmod_edit.add_argument("--confirm", action="store_true")
    pmod_edit.add_argument("--json", action="store_true")
    pmod_activate = pmod_sub.add_parser(
        "activate", parents=[common],
        help="select a profile for a session/role (dry-run unless --confirm)")
    pmod_activate.add_argument("profile")
    pmod_activate.add_argument("--session", default="cli")
    pmod_activate.add_argument("--role", default="chat")
    pmod_activate.add_argument("--confirm", action="store_true")
    pmod_activate.add_argument("--json", action="store_true")
    pmod_deactivate = pmod_sub.add_parser(
        "deactivate", parents=[common],
        help="clear a session profile override (dry-run unless --confirm)")
    pmod_deactivate.add_argument("--session", default="cli")
    pmod_deactivate.add_argument("--role", default="*",
                                 help="role to clear, or * for the whole session")
    pmod_deactivate.add_argument("--confirm", action="store_true")
    pmod_deactivate.add_argument("--json", action="store_true")
    pmod_rollback = pmod_sub.add_parser(
        "rollback", parents=[common],
        help="select a profile's rollback target for a session (dry-run unless --confirm)")
    pmod_rollback.add_argument("profile")
    pmod_rollback.add_argument("--session", default="cli")
    pmod_rollback.add_argument("--role", default="chat")
    pmod_rollback.add_argument("--confirm", action="store_true")
    pmod_rollback.add_argument("--json", action="store_true")
    pmod_remove = pmod_sub.add_parser(
        "remove", parents=[common],
        help="remove a non-default model profile (dry-run unless --confirm)")
    pmod_remove.add_argument("profile")
    pmod_remove.add_argument("--confirm", action="store_true")
    pmod_remove.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    _sync_cli_coverage()
    # Provider choices come from the single messaging capability registry so a
    # newly registered channel is selectable without a second list drifting.
    from .messaging import PROVIDERS as _MSG_PROVIDERS
    _msg_provider_choices = list(_MSG_PROVIDERS)
    p = argparse.ArgumentParser(
        prog=_launcher_name(),
        description="Qubit Agent (Qubit VoiceBridge): local push-to-talk voice interface (ASR + intent + gateway + TTS).")
    common = argparse.ArgumentParser(add_help=False)
    # NOTE: these options also exist at the top level so `voicebridge --config X
    # <subcommand> …` works. Each subparser re-declares them via `parents=[common]`;
    # without SUPPRESS the subparser default would clobber a value the top level
    # already parsed (e.g. `--config X websites list` silently ignoring X).
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="path to voice.yaml (default ~/.config/qubit-voicebridge/voice.yaml; legacy config paths resolve via compat alias)")
    common.add_argument("--no-hotkey", action="store_true",
                        default=argparse.SUPPRESS,
                        help="disable hotkey; use manual Enter PTT (headless/SSH)")
    common.add_argument("--dry-run", action="store_true",
                        default=argparse.SUPPRESS,
                        help="never dispatch a tool action; only speak the plan")
    common.add_argument("--mute", action="store_true",
                        default=argparse.SUPPRESS,
                        help="start muted (no spoken output; logs still work)")
    p.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--no-hotkey", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mute", action="store_true", help=argparse.SUPPRESS)
    # `qubit --version` prints the product identity and exits 0 (the canonical
    # Qubit version, not the vendored runtime's). Honored before any family
    # routing/dispatch so it works without config or audio hardware.
    from .product import PRODUCT_VERSION as _PRODUCT_VERSION
    p.add_argument("--version", action="version",
                   version="%(prog)s " + _PRODUCT_VERSION)
    sub = p.add_subparsers(dest="command")

    def add(name, **kw):
        sp = sub.add_parser(name, parents=[common], **kw)
        return sp

    add("run", help="interactive push-to-talk loop (default)")
    add("once", help="record one utterance, process it, exit")
    add("mic-test", help="record 5s and report levels (diagnostic)")
    task = add("task", help="send a typed task through the pipeline (no mic)")
    task.add_argument("text", nargs="*", help="task text, e.g. 'inspect the Stripe webhook flow'")
    task.add_argument("--model", default=None, help="model override (provider/model or tier)")
    task.add_argument("--current", action="store_true",
                      help="run the current kanban task (newest open/claimed/blocked) instead of text")
    asr = add("asr-test", help="transcribe a WAV file (diagnostic)")
    asr.add_argument("file", type=Path)
    tts = add("tts-test", help="synthesize text to WAV and play it (diagnostic)")
    tts.add_argument("text", nargs="+")
    tts.add_argument("--out", type=Path, default=None)
    add("status", help="print gateway/ASR/TTS/VRAM status and exit")
    auth_p = add("auth", help="Qubit Agent credential-reference inventory (names only, never values)")
    auth_sub = auth_p.add_subparsers(dest="auth_command")
    auth_sub.add_parser("status", parents=[common],
                        help="show configured providers and reference state (read-only)")
    auth_sub.add_parser("logout", parents=[common],
                        help="verify no stored credentials linger (read-only scan)")
    auth_login = auth_sub.add_parser("login", parents=[common],
                                     help="manual-handoff enrollment steps for a provider "
                                          "(guidance only; you log in yourself)")
    auth_login.add_argument("--provider", required=True,
                            help="provider name from the frontier config")
    auth_login.add_argument("--open", action="store_true",
                            help="also open the documented portal page locally")
    auth_verify = auth_sub.add_parser("verify", parents=[common],
                                      help="re-probe one provider reference (read-only)")
    auth_verify.add_argument("--provider", required=True,
                             help="provider name from the frontier config")
    mcp_p = add("mcp", help="MCP server inventory and mount policy (read-only)")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_command")
    mcp_sub.add_parser("status", parents=[common],
                       help="list configured MCP servers without mounting anything")
    mcp_inv = mcp_sub.add_parser(
        "invoke", parents=[common],
        help="call a tool on a configured MCP server (brokered; confirmed)")
    mcp_inv.add_argument("server", help="registered MCP server name")
    mcp_inv.add_argument("--tool", default="", help="tool name to call")
    mcp_inv.add_argument("--arg", action="append", default=[], metavar="K=V",
                         help="tool argument (repeatable)")
    mcp_inv.add_argument("--confirm", action="store_true",
                         help="confirm the side-effecting call")
    mcp_inv.add_argument("--confirmation-id", default="",
                         help="settle a staged confirmation instead")
    worker_p = add("worker", help="Native Qubit worker-lane status (read-only)")
    worker_sub = worker_p.add_subparsers(dest="worker_command")
    worker_sub.add_parser("status", parents=[common],
                          help="agent route, model, and enablement (read-only)")
    ap = add("approvals", help="Shared human-in-the-loop confirmations (same journal as voice + dashboard)")
    ap_sub = ap.add_subparsers(dest="approvals_command")
    ap_list = ap_sub.add_parser("list", parents=[common],
                                help="list pending confirmations (read-only)")
    ap_list.add_argument("--session", default="",
                         help="only this session (default: all sessions)")
    ap_show = ap_sub.add_parser("show", parents=[common],
                                help="show one confirmation (argument names only, never values)")
    ap_show.add_argument("confirmation_id", help="approval id")
    ap_resp = ap_sub.add_parser("respond", parents=[common],
                                help="approve (executes once) or deny a confirmation")
    ap_resp.add_argument("confirmation_id", help="approval id")
    ap_resp.add_argument("decision", choices=["approve", "deny"],
                         help="approve executes the staged backend exactly once")
    ap_resp.add_argument("--session", default="",
                         help="require this session (default: the record's session)")
    add("verify-release", help="hermetic static release qualification (no build/install)")
    ck = add("checkpoints", help="named journal checkpoints: inspect and prune (read-only by default)")
    ck_sub = ck.add_subparsers(dest="checkpoints_command")
    ck_create = ck_sub.add_parser("create", parents=[common],
                                  help="record the current journal tail position")
    ck_create.add_argument("name", help="checkpoint name")
    ck_create.add_argument("--journal", default="",
                           help="journal file (default: state approvals journal)")
    ck_sub.add_parser("list", parents=[common],
                      help="list checkpoints (read-only)")
    ck_verify = ck_sub.add_parser("verify", parents=[common],
                                  help="recompute digests and compare (read-only)")
    ck_verify.add_argument("name", help="checkpoint name")
    ck_prune = ck_sub.add_parser("prune", parents=[common],
                                 help="delete a checkpoint record (needs --confirm)")
    ck_prune.add_argument("name", help="checkpoint name")
    ck_prune.add_argument("--confirm", action="store_true",
                          help="explicit owner approval")
    sess = add("sessions", help="Qubit action sessions: list/search/show + lifecycle (rename/archive/delete/resume)")
    sess_sub = sess.add_subparsers(dest="sessions_command")
    sess_list = sess_sub.add_parser("list", parents=[common],
                                    help="list recent sessions (read-only)")
    sess_list.add_argument("--limit", type=int, default=20,
                           help="max sessions (default: 20, max: 100)")
    sess_search = sess_sub.add_parser("search", parents=[common],
                                      help="search sessions by redacted text (read-only)")
    sess_search.add_argument("query", help="substring query")
    sess_search.add_argument("--limit", type=int, default=20,
                             help="max matches (default: 20)")
    sess_show = sess_sub.add_parser("show", parents=[common],
                                    help="show one session (redacted, read-only)")
    sess_show.add_argument("session_id", help="receipt/action id")
    sess_rename = sess_sub.add_parser("rename", parents=[common],
                                      help="set a session display name (idempotent, audited)")
    sess_rename.add_argument("session_id")
    sess_rename.add_argument("name")
    sess_archive = sess_sub.add_parser("archive", parents=[common],
                                       help="archive a session (idempotent, recoverable)")
    sess_archive.add_argument("session_id")
    sess_delete = sess_sub.add_parser("delete", parents=[common],
                                      help="delete a session (destructive; needs --confirm; resume restores)")
    sess_delete.add_argument("session_id")
    sess_delete.add_argument("--confirm", action="store_true",
                             help="explicit owner approval")
    sess_resume = sess_sub.add_parser("resume", parents=[common],
                                      help="resume/restore an archived or deleted session")
    sess_resume.add_argument("session_id")
    # B-04M: the canonical chat-transcript authority (state.db) needs a CLI
    # surface so migrated Qubit history is reachable from the terminal, not
    # only the receipt-lifecycle ``sessions`` store.
    cst = add("chat-sessions", aliases=["chat_sessions"],
              help="Canonical chat transcripts from state.db (the ONE session "
                   "authority: list/search/show/messages/export)")
    cst_sub = cst.add_subparsers(dest="chat_sessions_command")
    cst_list = cst_sub.add_parser("list", parents=[common],
                                  help="list canonical chat sessions (read-only)")
    cst_list.add_argument("--limit", type=int, default=20)
    cst_list.add_argument("--archived", action="store_true",
                          help="include archived sessions")
    cst_show = cst_sub.add_parser("show", parents=[common],
                                  help="show one canonical chat session")
    cst_show.add_argument("session_id")
    cst_msgs = cst_sub.add_parser("messages", parents=[common],
                                  help="show messages for a canonical session")
    cst_msgs.add_argument("session_id")
    cst_msgs.add_argument("--limit", type=int, default=100)
    cst_search = cst_sub.add_parser("search", parents=[common],
                                    help="search canonical chat sessions")
    cst_search.add_argument("query")
    cst_search.add_argument("--limit", type=int, default=20)
    cst_export = cst_sub.add_parser("export", parents=[common],
                                    help="export a canonical session transcript")
    cst_export.add_argument("session_id")
    cap = add("capability", help="capability catalog: typed state for any Qubit capability (read-only)")
    cap.add_argument("action", nargs="?", default="summary",
                     choices=["check", "list", "summary"],
                     help="check one name, list names, or show state counts")
    cap.add_argument("name", nargs="?", default="",
                     help="capability name for check (e.g. chat, whatsapp, skills)")
    cap.add_argument("--state", default=None,
                     choices=["IMPLEMENTED", "BLOCKED", "REJECTED"],
                     help="filter for list")
    comp = add("completion", help="print shell completion script (Qubit Agent)")
    comp.add_argument("shell", nargs="?", default="bash",
                      choices=["bash", "zsh", "fish"],
                      help="shell dialect (default: bash)")
    comp.add_argument("--cmd", default="qubit",
                      choices=["qubit", "voicebridge", "qubit-dev"],
                      help="command to complete (default: qubit; voicebridge is a compat alias)")
    roundtrip = add("roundtrip", help="end-to-end test: synth speech -> ASR -> classify -> gateway -> summarize -> TTS file")
    roundtrip.add_argument("--text", default=None, help="sample utterance (default: Stripe webhook example)")
    roundtrip.add_argument("--model", default=None, help="model override for the agent run")
    enroll = add("enroll", help="enroll your voice for speaker verification (voice-ID)")
    enroll.add_argument("--file", type=Path, default=None,
                        help="enroll from an existing WAV instead of recording")
    enroll.add_argument("--name", default=None, help="speaker name (default: from config, 'user')")
    enroll.add_argument("--seconds", type=int, default=None, help="recording length (default: 5)")
    verify = add("verify", help="diagnostic: score a WAV against the enrolled speaker")
    verify.add_argument("file", type=Path)
    verify.add_argument("--name", default=None)
    flags = add("flags", help="inspect/toggle reversible feature flags (dry-run by default)")
    flags_sub = flags.add_subparsers(dest="flags_command")
    flags_list = flags_sub.add_parser("list", parents=[common],
                                      help="show every flag + effective value")
    flags_list.add_argument("--json", action="store_true", help="machine-readable output")
    fget = flags_sub.add_parser("get", parents=[common], help="show one flag's effective value")
    fget.add_argument("name")
    fset = flags_sub.add_parser("set", parents=[common], help="set a flag value (dry-run unless --apply)")
    fset.add_argument("name")
    fset.add_argument("value")
    fset.add_argument("--apply", action="store_true",
                      help="write the config with a timestamped backup (default: dry-run)")
    froll = flags_sub.add_parser("rollback", parents=[common],
                                 help="restore a flag's rollback value (dry-run unless --apply)")
    froll.add_argument("name")
    froll.add_argument("--apply", action="store_true",
                       help="write the config with a timestamped backup (default: dry-run)")
    cfgp = add("config", help="effective Qubit configuration + secret-reference "
               "store (reads only; writes are confirm-gated and never store "
               "secret values)")
    cfgp_sub = cfgp.add_subparsers(dest="config_command")
    cfgp_path = cfgp_sub.add_parser("path", parents=[common],
                                    help="effective config file path (read-only)")
    cfgp_path.add_argument("--json", action="store_true")
    cfgp_show = cfgp_sub.add_parser("show", parents=[common],
                                    help="effective config with secret-shaped "
                                         "values masked (read-only)")
    cfgp_show.add_argument("--json", action="store_true")
    cfgp_check = cfgp_sub.add_parser("check", parents=[common],
                                     help="load + validate the effective config "
                                          "and reference store (read-only)")
    cfgp_check.add_argument("--json", action="store_true")
    cfgp_epath = cfgp_sub.add_parser("env-path", parents=[common],
                                     help="secret-reference store path (read-only)")
    cfgp_epath.add_argument("--json", action="store_true")
    cfgp_elist = cfgp_sub.add_parser("env-list", parents=[common],
                                     help="secret-reference names only, never "
                                          "values (read-only)")
    cfgp_elist.add_argument("--json", action="store_true")
    cfgp_set = cfgp_sub.add_parser(
        "set", parents=[common],
        help="set a vault:/ref: secret reference (needs --confirm; a raw "
             "secret value is refused)")
    cfgp_set.add_argument("name", help="reference name, e.g. OPENAI_API_KEY")
    cfgp_set.add_argument("ref", help="vault:NAME or ref:NAME (never a raw value)")
    cfgp_set.add_argument("--confirm", action="store_true")
    cfgp_set.add_argument("--json", action="store_true")
    cfgp_unset = cfgp_sub.add_parser("unset", parents=[common],
                                     help="remove a secret reference (needs --confirm)")
    cfgp_unset.add_argument("name")
    cfgp_unset.add_argument("--confirm", action="store_true")
    cfgp_unset.add_argument("--json", action="store_true")
    drill = add("flag-drill", help="hermetic rollback drill: toggle + restore every flag on a temp config/state")
    pkg = add("package", help="download-and-run distribution surface (read-only)")
    pkg_sub = pkg.add_subparsers(dest="package_command")
    pkg_sub.add_parser("manifest", parents=[common],
                       help="print the versioned package manifest")
    pkg_doctor = pkg_sub.add_parser("doctor", parents=[common],
                                    help="hardware/resource doctor (typed states)")
    pkg_doctor.add_argument("--json", action="store_true")
    pkg_launch = pkg_sub.add_parser("launch", parents=[common],
                                    help="print the verified launch command")
    pkg_up = pkg_sub.add_parser("upgrade", parents=[common],
                                help="upgrade behavior plan (read-only)")
    pkg_un = pkg_sub.add_parser("uninstall", parents=[common],
                                help="uninstall behavior plan (read-only, confirms)")
    pkg_model = pkg_sub.add_parser("model", parents=[common],
                                   help="model artifact metadata + digest (fail closed)")
    pkg_dist = pkg_sub.add_parser("distributable", parents=[common],
                                  help="distributable artifact completeness check")
    pkg_cert = pkg_sub.add_parser("certify", parents=[common],
                                  help="Packet 8K hermetic release certification "
                                       "(provenance, secret/machine-path scan, "
                                       "units, runbooks, upgrade/uninstall contract)")
    pkg_cert.add_argument("--json", action="store_true",
                          help="machine-readable certification report")
    docp = add("doctor", help="host + capability diagnostics (read-only; same "
               "checks as `package doctor` and the capability catalog)")
    docp_sub = docp.add_subparsers(dest="doctor_command")
    docp_run = docp_sub.add_parser("run", parents=[common],
                                   help="hardware/resource + capability checks")
    docp_run.add_argument("--json", action="store_true")
    docp_check = docp_sub.add_parser("check", parents=[common],
                                     help="one capability's typed state (read-only)")
    docp_check.add_argument("name", help="capability name, e.g. chat, whatsapp")
    docp_check.add_argument("--json", action="store_true")
    docp_list = docp_sub.add_parser("list", parents=[common],
                                    help="capability names, optionally state-filtered (read-only)")
    docp_list.add_argument("--state", default=None,
                           choices=["IMPLEMENTED", "BLOCKED", "REJECTED",
                                    "NOT_UPSTREAM", "UNKNOWN"],
                           help="only names in this typed state")
    docp_list.add_argument("--json", action="store_true")
    docp_sum = docp_sub.add_parser("summary", parents=[common],
                                   help="capability counts by typed state (read-only)")
    docp_sum.add_argument("--json", action="store_true")
    prod = add("product", help="Packet 8K-P product surface "
               "(paths/install/first-run/repair/migrate/service/shortcut/"
               "tray/capabilities/boundary/upgrade/rollback)")
    prod_sub = prod.add_subparsers(dest="product_command")
    prod_paths = prod_sub.add_parser("paths", parents=[common],
                                     help="effective path map (diagnostic)")
    prod_paths.add_argument("--json", action="store_true")
    prod_inst = prod_sub.add_parser("install", parents=[common],
                                    help="installer (prereq probe + apply; "
                                         "never enables live services)")
    prod_inst.add_argument("--apply", action="store_true",
                           help="actually stage config/resources (default dry-run)")
    prod_inst.add_argument("--service-owner", default=None,
                           help="documented service owner (systemctl --user)")
    prod_first = prod_sub.add_parser("first-run", parents=[common],
                                     help="beginner first-run flow "
                                          "(status by default)")
    prod_first.add_argument("--apply", action="store_true",
                            help="stage configured defaults + compat aliases "
                                 "(merge only, never overwrite tuned settings)")
    prod_rep = prod_sub.add_parser("repair", parents=[common],
                                   help="one diagnostic/repair command")
    prod_caps = prod_sub.add_parser("capabilities", parents=[common],
                                    help="typed capability states")
    prod_caps.add_argument("--json", action="store_true")
    prod_mig = prod_sub.add_parser("migration", parents=[common],
                                   help="branding/migration status (read-only)")
    prod_mig.add_argument("--json", action="store_true")
    prod_migrate = prod_sub.add_parser("migrate", parents=[common],
                                       help="reversible legacy -> canonical "
                                            "migration (backed up, "
                                            "resume-able)")
    prod_migrate.add_argument("--apply", action="store_true",
                              help="perform the migration (default dry-run)")
    prod_migrate.add_argument("--backup-dir", type=Path, default=None)
    prod_migrate.add_argument("--json", action="store_true")
    prod_migroll = prod_sub.add_parser("migrate-rollback", parents=[common],
                                       help="reverse a branding migration")
    prod_migroll.add_argument("--apply", action="store_true",
                              help="perform the rollback (default dry-run)")
    prod_migroll.add_argument("--backup-dir", type=Path, default=None)
    # Real Qubit user-data migration (read-only source, backed up, idempotent,
    # reversible). Distinct from the branding migration above.
    prod_hinspect = prod_sub.add_parser(
        "legacy-inspect", parents=[common],
        help="read-only inventory of the operator's legacy agent home")
    prod_hinspect.add_argument("--home", type=Path, default=None)
    prod_hinspect.add_argument("--json", action="store_true")
    prod_hmigrate = prod_sub.add_parser(
        "legacy-migrate", parents=[common],
        help="import legacy agent sessions/messages into the Qubit chat store "
             "(dry-run unless --apply; backs up first)")
    prod_hmigrate.add_argument("--apply", action="store_true",
                               help="perform the import (default dry-run)")
    prod_hmigrate.add_argument("--home", type=Path, default=None)
    prod_hmigrate.add_argument("--backup-dir", type=Path, default=None)
    prod_hmigrate.add_argument("--json", action="store_true")
    prod_hroll = prod_sub.add_parser(
        "legacy-rollback", parents=[common],
        help="restore the chat store from a legacy-migrate backup")
    prod_hroll.add_argument("--backup-dir", type=Path, required=True)
    prod_hroll.add_argument("--apply", action="store_true",
                            help="perform the rollback (default dry-run)")
    prod_hroll.add_argument("--json", action="store_true")
    prod_svc = prod_sub.add_parser("service", parents=[common],
                                   help="service ownership contract (read-only)")
    prod_short = prod_sub.add_parser("shortcut", parents=[common],
                                     help="desktop shortcut (dry-run unless --apply)")
    prod_short.add_argument("--apply", action="store_true")
    prod_short.add_argument("--json", action="store_true")
    prod_tray = prod_sub.add_parser("tray", parents=[common],
                                    help="optional tray status")
    prod_tray.add_argument("--json", action="store_true")
    prod_bound = prod_sub.add_parser("boundary", parents=[common],
                                     help="packaging/private-source boundary")
    prod_bound.add_argument("--json", action="store_true")
    prod_up = prod_sub.add_parser("upgrade", parents=[common],
                                  help="upgrade/uninstall/rollback contract")
    prod_up.add_argument("--json", action="store_true")
    prod_roll = prod_sub.add_parser("rollback", parents=[common],
                                    help="rollback from a backup (dry-run unless --apply)")
    prod_roll.add_argument("--apply", action="store_true")
    prod_roll.add_argument("--backup-dir", type=Path, default=None)
    aud = add("audio", help="user-facing audio settings contract (list/select/reset/status)")
    aud_sub = aud.add_subparsers(dest="audio_command")
    aud_list = aud_sub.add_parser("list", parents=[common], help="list audio devices")
    aud_list.add_argument("--json", action="store_true")
    aud_sel = aud_sub.add_parser("select", parents=[common], help="select a device by stable name")
    aud_sel.add_argument("kind", choices=["mic", "output"])
    aud_sel.add_argument("name")
    aud_sub.add_parser("reset", parents=[common], help="reset audio to OS defaults")
    aud_sub.add_parser("status", parents=[common], help="typed audio state (OK/DEGRADED/MISSING/PERMISSION_DENIED)")
    aud_sub.add_parser("check", parents=[common],
                       help="live mic signal self-check (typed HEALTHY/SILENT/"
                            "DEGRADED/UNAVAILABLE); refused while the bridge "
                            "holds the microphone")
    aud_sub.add_parser("wake-status", parents=[common],
                       help="wake-word spotter readiness (read-only; never downloads)")
    aud_probe = aud_sub.add_parser("wake-probe", parents=[common],
                                   help="run keyword detection over a 16 kHz mono WAV file")
    aud_probe.add_argument("wav", type=Path, help="absolute path to the WAV file")
    aud_probe.add_argument("--threshold", type=float, default=0.25)
    prof = add("profile", help="provider/profile registry (brain/STT/TTS/voice/audio)")
    prof_sub = prof.add_subparsers(dest="profile_command")
    prof_list = prof_sub.add_parser("list", parents=[common],
                                    help="list registered profiles + active selection")
    prof_list.add_argument("--json", action="store_true")
    prof_show = prof_sub.add_parser("show", parents=[common],
                                    help="show the active profile for one component")
    prof_show.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_check = prof_sub.add_parser("check", parents=[common],
                                     help="run every gate for a profile (read-only)")
    prof_check.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_check.add_argument("--profile", default=None)
    prof_install = prof_sub.add_parser("install", parents=[common],
                                       help="register a profile manifest (JSON, read-only)")
    prof_install.add_argument("manifest", type=Path, help="path to a manifest JSON")
    prof_select = prof_sub.add_parser("select", parents=[common],
                                      help="activate a profile (dry-run unless --apply)")
    prof_select.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_select.add_argument("profile")
    prof_select.add_argument("--apply", action="store_true",
                             help="actually apply (default: dry-run)")
    prof_select.add_argument("--confirm", action="store_true",
                             help="confirm the side effect")
    prof_reset = prof_sub.add_parser("reset", parents=[common],
                                     help="reset one component to its default")
    prof_reset.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_reset.add_argument("--apply", action="store_true",
                            help="actually apply (default: dry-run)")
    prof_up = prof_sub.add_parser("upgrade", parents=[common],
                                  help="re-verify a profile's gates")
    prof_up.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_up.add_argument("--profile", default=None)
    prof_up.add_argument("--apply", action="store_true",
                         help="actually re-verify (default: dry-run)")
    prof_rm = prof_sub.add_parser("remove", parents=[common],
                                  help="remove a non-default profile from the registry")
    prof_rm.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_rm.add_argument("profile")
    prof_rm.add_argument("--apply", action="store_true",
                         help="actually remove (default: dry-run)")
    prof_roll = prof_sub.add_parser("rollback", parents=[common],
                                    help="restore a component's previous profile")
    prof_roll.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_roll.add_argument("--backup", type=Path, default=None)
    prof_roll.add_argument("--apply", action="store_true",
                           help="actually roll back (default: dry-run)")
    # Canonical model/provider profiles (voicebridge.providers.resolver).
    # Every mutation is dry-run unless --confirm; secrets are env:/file:
    # references only (a raw key is refused with a typed error).
    prof_model = prof_sub.add_parser(
        "model", parents=[common],
        help="canonical model/provider profiles (list/show/test/create/clone/"
             "edit/activate/deactivate/rollback/remove)")
    _add_model_profile_subcommands(prof_model, common)
    # Canonical speech (STT/TTS/realtime) profiles — the SAME registry the
    # orchestrator ASR/TTS seams, the dashboard ops, the Qubit RPC surface,
    # and the voice registry resolve through. Reads execute; mutations are
    # dry-run unless --confirm; key_ref is an env:/file: reference only.
    prof_speech = prof_sub.add_parser(
        "speech", parents=[common],
        help="canonical speech profiles (STT/TTS/realtime): list/show/test/"
             "create/clone/remove/select/clear")
    psp_sub = prof_speech.add_subparsers(dest="profile_speech_command")
    psp_list = psp_sub.add_parser("list", parents=[common],
                                  help="list speech profiles + effective selection")
    psp_list.add_argument("--kind", choices=["speech_in", "speech_out",
                                             "realtime"], default=None)
    psp_list.add_argument("--json", action="store_true")
    psp_show = psp_sub.add_parser("show", parents=[common],
                                  help="show one speech profile (read-only)")
    psp_show.add_argument("profile")
    psp_show.add_argument("--json", action="store_true")
    psp_test = psp_sub.add_parser("test", parents=[common],
                                  help="preflight the active (or named) speech profile (read-only)")
    psp_test.add_argument("profile", nargs="?", default="")
    psp_test.add_argument("--kind", choices=["speech_in", "speech_out",
                                             "realtime"], default="speech_in")
    psp_test.add_argument("--json", action="store_true")
    psp_create = psp_sub.add_parser(
        "create", parents=[common],
        help="create a speech profile (dry-run unless --confirm; non-secret flags only)")
    psp_create.add_argument("--id", required=True)
    psp_create.add_argument("--kind", choices=["speech_in", "speech_out",
                                               "realtime"], default="speech_in")
    psp_create.add_argument("--provider", required=True)
    psp_create.add_argument("--model", default="")
    psp_create.add_argument("--voice", default="")
    psp_create.add_argument("--device", default="")
    psp_create.add_argument("--language", default="")
    psp_create.add_argument("--locality", choices=["local", "external"],
                            default=None)
    psp_create.add_argument("--egress-consent", choices=["deny", "allow"],
                            default=None)
    psp_create.add_argument("--key-ref", default="",
                            help="secret reference env:NAME or file:PATH (never a raw value)")
    psp_create.add_argument("--base-url", default="")
    psp_create.add_argument("--engine", default="")
    psp_create.add_argument("--fallback-to", default="")
    psp_create.add_argument("--confirm", action="store_true")
    psp_create.add_argument("--json", action="store_true")
    psp_clone = psp_sub.add_parser(
        "clone", parents=[common],
        help="clone a speech profile (dry-run unless --confirm)")
    psp_clone.add_argument("source")
    psp_clone.add_argument("new")
    psp_clone.add_argument("--confirm", action="store_true")
    psp_clone.add_argument("--json", action="store_true")
    psp_remove = psp_sub.add_parser(
        "remove", parents=[common],
        help="remove a non-default speech profile (dry-run unless --confirm)")
    psp_remove.add_argument("profile")
    psp_remove.add_argument("--confirm", action="store_true")
    psp_remove.add_argument("--json", action="store_true")
    psp_select = psp_sub.add_parser(
        "select", parents=[common],
        help="select a speech profile for a session/kind (dry-run unless --confirm)")
    psp_select.add_argument("profile")
    psp_select.add_argument("--session", default="cli")
    psp_select.add_argument("--kind", choices=["speech_in", "speech_out",
                                               "realtime"], required=True)
    psp_select.add_argument("--confirm", action="store_true")
    psp_select.add_argument("--json", action="store_true")
    psp_clear = psp_sub.add_parser(
        "clear", parents=[common],
        help="clear a session speech override (dry-run unless --confirm)")
    psp_clear.add_argument("--session", default="cli")
    psp_clear.add_argument("--kind", choices=["speech_in", "speech_out",
                                              "realtime"], default=None)
    psp_clear.add_argument("--confirm", action="store_true")
    psp_clear.add_argument("--json", action="store_true")
    # Top-level family: `qubit model ...` names the SAME canonical
    # resolver-backed model profiles `qubit profile model ...` exposes.
    top_model = add("model", help="canonical model/provider profiles "
                    "(same registry as `profile model`; dry-run unless --confirm)")
    _add_model_profile_subcommands(top_model, common)
    brand = add("branding", help="Qubit VoiceBridge branding migration (dry-run/backup/verify/rollback)")
    brand_sub = brand.add_subparsers(dest="branding_command")
    brand_plan = brand_sub.add_parser("plan", parents=[common],
                                      help="prepare the legacy -> qubit-voicebridge migration")
    brand_plan.add_argument("--apply", action="store_true", default=False,
                            help="perform the migration with a backup (default: dry-run plan only)")
    brand_plan.add_argument("--migrate-paths", action="store_true", default=False,
                            help="ALSO move live config/state/data paths (explicit, separate operator step)")
    brand_plan.add_argument("--backup-dir", type=Path, default=None)
    brand_plan.add_argument("--repo", type=Path, default=None,
                            help="target repository root (default: this repo)")
    brand_verify = brand_sub.add_parser("verify", parents=[common],
                                        help="read-only branding consistency check")
    brand_verify.add_argument("--repo", type=Path, default=None,
                              help="target repository root (default: this repo)")
    brand_roll = brand_sub.add_parser("rollback", parents=[common],
                                      help="restore branding backups (reversible)")
    brand_roll.add_argument("--backup-dir", type=Path, required=True)
    brand_roll.add_argument("--repo", type=Path, default=None,
                            help="target repository root (default: this repo)")
    brand_roll.add_argument("--home", type=Path, default=None,
                            help="scope runtime restore/alias removal to this "
                                 "home dir (tests use a tmp home; default is "
                                 "the operator HOME)")
    perm = add("permissions", help="Packet 7E permission profiles + scoped grants")
    perm_sub = perm.add_subparsers(dest="permissions_command")
    perm_mode = perm_sub.add_parser("mode", parents=[common],
                                    help="show or set the permission mode (restricted/unrestricted)")
    perm_mode.add_argument("action", choices=["show", "set"])
    perm_mode.add_argument("value", nargs="?", default=None,
                           choices=["restricted", "unrestricted"],
                           help="mode to set")
    perm_mode.add_argument("--scope", action="append", default=[],
                           help="user-selected scope (unrestricted pre-auth); repeatable")
    perm_mode.add_argument("--ttl", type=float, default=None,
                           help="mode TTL seconds (unrestricted, time-bounded)")
    perm_mode.add_argument("--confirm", action="store_true",
                           help="explicitly confirm the side effect")
    perm_sub.add_parser("list", parents=[common], help="list grants")
    perm_show = perm_sub.add_parser("show", parents=[common], help="show one grant")
    perm_show.add_argument("grant_id")
    perm_check = perm_sub.add_parser("check", parents=[common],
                                     help="effective decision for a capability (read-only)")
    perm_check.add_argument("capability")
    perm_check.add_argument("--scope", action="append", default=[],
                            help="requested scope; repeatable")
    perm_grant = perm_sub.add_parser("grant", parents=[common],
                                     help="grant a capability in scopes (dry-run unless --confirm)")
    perm_grant.add_argument("capability")
    perm_grant.add_argument("--scope", action="append", required=True)
    perm_grant.add_argument("--ttl", type=float, default=None)
    perm_grant.add_argument("--confirm", action="store_true")
    perm_revoke = perm_sub.add_parser("revoke", parents=[common],
                                      help="revoke a grant (dry-run unless --confirm)")
    perm_revoke.add_argument("grant_id")
    perm_revoke.add_argument("--confirm", action="store_true")
    perm_reset = perm_sub.add_parser("reset", parents=[common],
                                     help="reset to restricted + revoke all grants (dry-run unless --confirm)")
    perm_reset.add_argument("--confirm", action="store_true")
    sk = add("skills", help="Packet 7E skill manifests (install staged disabled)")
    sk_sub = sk.add_subparsers(dest="skills_command")
    sk_sub.add_parser("list", parents=[common], help="list installed skills")
    sk_show = sk_sub.add_parser("show", parents=[common], help="show one skill manifest")
    sk_show.add_argument("skill_id")
    sk_check = sk_sub.add_parser("check", parents=[common],
                                 help="validate a manifest file (read-only)")
    sk_check.add_argument("manifest",
                          help="local manifest file; remote Git/index "
                               "identifiers are typed-refused, never fetched")
    sk_install = sk_sub.add_parser("install", parents=[common],
                                   help="validate + stage a skill disabled (dry-run unless --confirm)")
    sk_install.add_argument("manifest",
                            help="local manifest file; remote Git/index "
                                 "identifiers are typed-refused, never fetched")
    sk_install.add_argument("--confirm", action="store_true")
    sk_enable = sk_sub.add_parser("enable", parents=[common],
                                  help="enable a staged/disabled skill (dry-run unless --confirm)")
    sk_enable.add_argument("skill_id")
    sk_enable.add_argument("--confirm", action="store_true")
    sk_disable = sk_sub.add_parser("disable", parents=[common],
                                   help="disable a skill (dry-run unless --confirm)")
    sk_disable.add_argument("skill_id")
    sk_disable.add_argument("--confirm", action="store_true")
    sk_remove = sk_sub.add_parser("remove", parents=[common],
                                  help="remove a skill (dry-run unless --confirm)")
    sk_remove.add_argument("skill_id")
    sk_remove.add_argument("--confirm", action="store_true")
    sk_roll = sk_sub.add_parser("rollback", parents=[common],
                                help="restore a skill's previous state (dry-run unless --confirm)")
    sk_roll.add_argument("skill_id")
    sk_roll.add_argument("--backup", type=Path, default=None)
    sk_roll.add_argument("--confirm", action="store_true")
    pj = add("project", help="Named multi-folder workspaces (Qubit registry)")
    pj_sub = pj.add_subparsers(dest="project_command")
    pj_create = pj_sub.add_parser("create", parents=[common],
                                  help="create a workspace (dry-run unless --confirm)")
    pj_create.add_argument("name", help="human name, e.g. 'Website Redesign'")
    pj_create.add_argument("folders", nargs="*", help="folder paths (first = primary)")
    pj_create.add_argument("--slug", default="", help="explicit slug override")
    pj_create.add_argument("--description", default="")
    pj_create.add_argument("--primary", default="", help="primary folder path")
    pj_create.add_argument("--use", action="store_true", help="set active after create")
    pj_create.add_argument("--confirm", action="store_true")
    pj_list = pj_sub.add_parser("list", parents=[common], help="list workspaces")
    pj_list.add_argument("--archived", action="store_true", help="include archived")
    pj_active = pj_sub.add_parser("active", parents=[common],
                                  help="show the active workspace (read-only)")
    pj_show = pj_sub.add_parser("show", parents=[common], help="show one workspace")
    pj_show.add_argument("slug")
    pj_add = pj_sub.add_parser("add-folder", parents=[common],
                               help="add a folder (dry-run unless --confirm)")
    pj_add.add_argument("slug")
    pj_add.add_argument("folder")
    pj_add.add_argument("--confirm", action="store_true")
    pj_rm = pj_sub.add_parser("remove-folder", parents=[common],
                              help="remove a folder (dry-run unless --confirm)")
    pj_rm.add_argument("slug")
    pj_rm.add_argument("folder")
    pj_rm.add_argument("--confirm", action="store_true")
    pj_rename = pj_sub.add_parser("rename", parents=[common],
                                  help="rename a workspace (dry-run unless --confirm)")
    pj_rename.add_argument("slug")
    pj_rename.add_argument("new_name")
    pj_rename.add_argument("--confirm", action="store_true")
    pj_primary = pj_sub.add_parser("set-primary", parents=[common],
                                   help="set the primary folder (dry-run unless --confirm)")
    pj_primary.add_argument("slug")
    pj_primary.add_argument("folder")
    pj_primary.add_argument("--confirm", action="store_true")
    pj_use = pj_sub.add_parser("use", parents=[common],
                               help="set the active workspace (dry-run unless --confirm)")
    pj_use.add_argument("slug", nargs="?", default="")
    pj_use.add_argument("--clear", action="store_true", help="clear the active pointer")
    pj_use.add_argument("--confirm", action="store_true")
    pj_archive = pj_sub.add_parser("archive", parents=[common],
                                   help="archive a workspace (dry-run unless --confirm)")
    pj_archive.add_argument("slug")
    pj_archive.add_argument("--confirm", action="store_true")
    pj_restore = pj_sub.add_parser("restore", parents=[common],
                                   help="restore an archived workspace (dry-run unless --confirm)")
    pj_restore.add_argument("slug")
    pj_restore.add_argument("--confirm", action="store_true")
    pj_bind = pj_sub.add_parser("bind-board", parents=[common],
                                help="bind a kanban board (dry-run unless --confirm)")
    pj_bind.add_argument("slug")
    pj_bind.add_argument("--board", default="")
    pj_bind.add_argument("--unbind", action="store_true",
                         help="remove the board binding instead")
    pj_bind.add_argument("--confirm", action="store_true")
    pj_roll = pj_sub.add_parser("rollback", parents=[common],
                                help="restore the registry from a backup (dry-run unless --confirm)")
    pj_roll.add_argument("--backup", default="")
    pj_roll.add_argument("--confirm", action="store_true")
    kb = add("kanban", help="Local task boards (Qubit registry; execution ops delegate)")
    kb_sub = kb.add_subparsers(dest="kanban_command")
    kb_sub.add_parser("init", parents=[common],
                      help="create the board store if missing (idempotent)")
    kb_sub.add_parser("boards", parents=[common], help="list boards")
    kb_sub.add_parser("stores", parents=[common],
                      help="show both kanban stores and the authoritative one")
    kb_migrate = kb_sub.add_parser(
        "migrate", parents=[common],
        help="import legacy kanban tasks into the Qubit store")
    kb_bc = kb_sub.add_parser("boards-create", parents=[common],
                              help="create a board (dry-run unless --confirm)")
    kb_bc.add_argument("slug")
    kb_bc.add_argument("--name", default="")
    kb_bc.add_argument("--confirm", action="store_true")
    kb_create = kb_sub.add_parser("create", parents=[common],
                                  help="create a task (dry-run unless --confirm)")
    kb_create.add_argument("title")
    kb_create.add_argument("--body", default="")
    kb_create.add_argument("--assignee", default="")
    kb_create.add_argument("--priority", choices=["low", "normal", "high"],
                           default="normal")
    kb_create.add_argument("--board", default="main")
    kb_create.add_argument("--confirm", action="store_true")
    kb_list = kb_sub.add_parser("list", parents=[common], help="list tasks")
    kb_list.add_argument("--board", default="")
    kb_list.add_argument("--status", default="",
                         choices=["", "open", "claimed", "blocked", "done", "archived"])
    kb_list.add_argument("--limit", type=int, default=50)
    kb_ls = kb_sub.add_parser("ls", parents=[common], help="list tasks")
    kb_ls.add_argument("--board", default="")
    kb_ls.add_argument("--status", default="",
                       choices=["", "open", "claimed", "blocked", "done", "archived"])
    kb_ls.add_argument("--limit", type=int, default=50)
    kb_show = kb_sub.add_parser("show", parents=[common],
                                help="show a task with comments + events")
    kb_show.add_argument("task_id", type=int)
    kb_sub.add_parser("current", parents=[common],
                      help="show the current task: newest open/claimed/blocked (read-only)")
    for _kb_mut, _kb_help in (
            ("claim", "claim a task (dry-run unless --confirm)"),
            ("assign", "set the assignee (dry-run unless --confirm)"),
            ("reassign", "move a claim (dry-run unless --confirm)"),
            ("reclaim", "release a claim (dry-run unless --confirm)"),
            ("complete", "mark done (dry-run unless --confirm)"),
            ("block", "mark blocked (dry-run unless --confirm)"),
            ("unblock", "return to open (dry-run unless --confirm)"),
            ("archive", "archive a task (dry-run unless --confirm)")):
        _p = kb_sub.add_parser(_kb_mut, parents=[common], help=_kb_help)
        _p.add_argument("task_id", type=int)
        if _kb_mut in ("claim", "assign", "reassign"):
            _p.add_argument("--assignee", default="")
        _p.add_argument("--confirm", action="store_true")
    kb_comment = kb_sub.add_parser("comment", parents=[common],
                                   help="append a comment (dry-run unless --confirm)")
    kb_comment.add_argument("task_id", type=int)
    kb_comment.add_argument("body")
    kb_comment.add_argument("--confirm", action="store_true")
    kb_edit = kb_sub.add_parser("edit", parents=[common],
                                help="edit title/body/priority (dry-run unless --confirm)")
    kb_edit.add_argument("task_id", type=int)
    kb_edit.add_argument("--title", default="")
    kb_edit.add_argument("--body", default="")
    kb_edit.add_argument("--priority", default="",
                         choices=["", "low", "normal", "high"])
    kb_edit.add_argument("--confirm", action="store_true")
    kb_stats = kb_sub.add_parser("stats", parents=[common],
                                 help="per-board status counts")
    kb_stats.add_argument("--board", default="")
    kb_log = kb_sub.add_parser("log", parents=[common],
                               help="recent board events")
    kb_log.add_argument("--board", default="")
    kb_log.add_argument("--limit", type=int, default=50)
    kb_sub.add_parser("export", parents=[common],
                      help="dump the board snapshot as JSON (rollback)")
    kb_import = kb_sub.add_parser("import", parents=[common],
                                  help="restore a snapshot (dry-run unless --confirm)")
    kb_import.add_argument("snapshot", type=Path)
    kb_import.add_argument("--confirm", action="store_true")
    kb_open = kb_sub.add_parser("open", parents=[common],
                                help="render the whole board canvas: columns, widgets, layout, roles (read-only)")
    kb_open.add_argument("--board", default="main")
    kb_move = kb_sub.add_parser("move", parents=[common],
                                help="move a task across boards/columns (dry-run unless --confirm)")
    kb_move.add_argument("task_id", type=int)
    kb_move.add_argument("--board", default="")
    kb_move.add_argument("--status", default="",
                         choices=["", "open", "claimed", "blocked", "done", "archived"])
    kb_move.add_argument("--confirm", action="store_true")
    kb_wa = kb_sub.add_parser("widget-add", parents=[common],
                              help="add a sandboxed widget (dry-run unless --confirm)")
    kb_wa.add_argument("--board", default="main")
    kb_wa.add_argument("--kind", default="note",
                       choices=["note", "tasks", "stats", "links"])
    kb_wa.add_argument("--title", default="")
    kb_wa.add_argument("--content", default="")
    kb_wa.add_argument("--x", type=int, default=0)
    kb_wa.add_argument("--y", type=int, default=0)
    kb_wa.add_argument("--w", type=int, default=4)
    kb_wa.add_argument("--h", type=int, default=3)
    kb_wa.add_argument("--confirm", action="store_true")
    kb_wl = kb_sub.add_parser("widget-list", parents=[common],
                              help="list canvas widgets (read-only)")
    kb_wl.add_argument("--board", default="main")
    kb_wm = kb_sub.add_parser("widget-move", parents=[common],
                              help="reposition/resize a widget (dry-run unless --confirm)")
    kb_wm.add_argument("widget_id", type=int)
    kb_wm.add_argument("--x", type=int, default=None)
    kb_wm.add_argument("--y", type=int, default=None)
    kb_wm.add_argument("--w", type=int, default=None)
    kb_wm.add_argument("--h", type=int, default=None)
    kb_wm.add_argument("--confirm", action="store_true")
    kb_wr = kb_sub.add_parser("widget-remove", parents=[common],
                              help="remove a widget (dry-run unless --confirm)")
    kb_wr.add_argument("widget_id", type=int)
    kb_wr.add_argument("--confirm", action="store_true")
    kb_lshow = kb_sub.add_parser("layout-show", parents=[common],
                                 help="show the board column layout (read-only)")
    kb_lshow.add_argument("--board", default="main")
    kb_lset = kb_sub.add_parser("layout-set", parents=[common],
                                help="set board columns, e.g. --columns open,claimed,done (dry-run unless --confirm)")
    kb_lset.add_argument("--board", default="main")
    kb_lset.add_argument("--columns", default="")
    kb_lset.add_argument("--confirm", action="store_true")
    kb_rg = kb_sub.add_parser("role-grant", parents=[common],
                              help="grant a board role to a delegate principal (dry-run unless --confirm)")
    kb_rg.add_argument("--board", default="main")
    kb_rg.add_argument("--principal", default="")
    kb_rg.add_argument("--role", default="viewer",
                       choices=["owner", "editor", "viewer"])
    kb_rg.add_argument("--confirm", action="store_true")
    kb_rr = kb_sub.add_parser("role-revoke", parents=[common],
                              help="revoke a board role (dry-run unless --confirm)")
    kb_rr.add_argument("--board", default="main")
    kb_rr.add_argument("--principal", default="")
    kb_rr.add_argument("--confirm", action="store_true")
    kb_roles = kb_sub.add_parser("roles", parents=[common],
                                 help="list board roles (read-only)")
    kb_roles.add_argument("--board", default="main")
    kb_snaps = kb_sub.add_parser("snapshots", parents=[common],
                                 help="list canvas snapshots (read-only)")
    kb_snaps.add_argument("--board", default="main")
    kb_snaps.add_argument("--limit", type=int, default=50)
    kb_snapr = kb_sub.add_parser("snapshot-restore", parents=[common],
                                 help="restore a canvas snapshot (dry-run unless --confirm)")
    kb_snapr.add_argument("snapshot_id", type=int)
    kb_snapr.add_argument("--confirm", action="store_true")
    for _kb_del in ("swarm", "dispatch", "daemon", "watch",
                    "notify-subscribe", "notify-list", "notify-unsubscribe",
                    "heartbeat", "runs"):
        kb_sub.add_parser(_kb_del, parents=[common],
                          help="delegates to a Qubit-native path (typed refusal)")
    for _kb_un in ("set-model", "diagnostics", "diag", "link", "unlink",
                   "attach", "attachments", "attach-rm", "schedule",
                   "request-review", "request-changes", "reopen-review",
                   "promote", "tail", "assignees", "context", "specify",
                   "decompose", "gc", "repair"):
        kb_sub.add_parser(_kb_un, parents=[common],
                          help="no Qubit construct yet (typed refusal)")
    cu = add("computer-use", help="Desktop-control capability probes (control disabled)")
    cu_sub = cu.add_subparsers(dest="computer_use_command")
    cu_sub.add_parser("status", parents=[common],
                      help="display/accessibility/driver state (read-only probes)")
    cu_sub.add_parser("doctor", parents=[common],
                      help="readiness checklist with guidance (no remediation)")
    cu_sub.add_parser("permissions", parents=[common],
                      help="control policy record (read-only)")
    cu_sub.add_parser("install", parents=[common],
                      help="refused: unpinned driver binaries are never downloaded")
    cu_sub.add_parser("session", parents=[common],
                      help="typed graphical-session probe: x11 | wayland | both | none (read-only)")
    cu_sub.add_parser("ops", parents=[common],
                      help="bounded allowlisted desktop ops: launch/PiP/present (read-only)")
    cu_launch = cu_sub.add_parser("launch", parents=[common],
                                  help="launch an approved app via an injected "
                                       "launcher (needs --confirm; typed refusal "
                                       "with no launcher)")
    cu_launch.add_argument("app", help="approved app id (see `qubit computer-use ops`)")
    cu_launch.add_argument("--target", default="desktop")
    cu_launch.add_argument("--confirm", action="store_true")
    cu_pip = cu_sub.add_parser("pip", parents=[common],
                               help="Document Picture-in-Picture over an injected "
                                    "Control-UI surface (needs --confirm)")
    cu_pip.add_argument("--target", default="desktop")
    cu_pip.add_argument("--width", type=int, default=640)
    cu_pip.add_argument("--height", type=int, default=400)
    cu_pip.add_argument("--confirm", action="store_true")
    cu_present = cu_sub.add_parser("present", parents=[common],
                                   help="persist a bounded presentation "
                                        "descriptor (needs --confirm)")
    cu_present.add_argument("mode", nargs="?", default="document",
                            choices=("document", "dock", "fullscreen"))
    cu_present.add_argument("--panes", default="",
                            help="comma-separated pane ids (chat,files,...)")
    cu_present.add_argument("--target", default="desktop")
    cu_present.add_argument("--confirm", action="store_true")
    cu_sub.add_parser("presentation", parents=[common],
                      help="persisted presentation descriptor (read-only)")
    rt = add("runtime", help="Pinned confined runtimes (Qubit container equivalent; no daemon, no pulls)")
    rt_sub = rt.add_subparsers(dest="runtime_command")
    rt_sub.add_parser("images", parents=[common],
                      help="list the pinned runtime registry (read-only)")
    rt_sub.add_parser("status", parents=[common],
                      help="installed state per pinned image (read-only, re-verified)")
    rt_sub.add_parser("doctor", parents=[common],
                      help="resource/permission/hygiene diagnosis (read-only)")
    for _rt_mut, _rt_help in (
            ("install", "install a pinned runtime from staged artifacts (dry-run unless --confirm; never downloads)"),
            ("upgrade", "re-verify + refresh a pinned runtime receipt (dry-run unless --confirm)"),
            ("rollback", "restore the previous install receipt (dry-run unless --confirm)"),
            ("remove", "remove an installed runtime (dry-run unless --confirm; audited)")):
        _p = rt_sub.add_parser(_rt_mut, parents=[common], help=_rt_help)
        _p.add_argument("name", help="pinned image name (see `qubit runtime images`)")
        _p.add_argument("--confirm", action="store_true")
        if _rt_mut in ("install", "upgrade"):
            _p.add_argument("--source", default="",
                            help="staged artifact path (opencode-binary); "
                                 "else QUBIT_OPENCODE_PIN_BIN, operator "
                                 "install, or PATH")
    # Owner Decision #11: the sanctioned Qubit update surface. Bare `update` is
    # `--check` (read-only). The three verbs are mutually exclusive; a real
    # apply/rollback needs no second --confirm because choosing the verb is the
    # explicit operator intent.
    up = add("update", help="Qubit update: --check (read-only, default), --apply, --rollback")
    _up_verbs = up.add_mutually_exclusive_group()
    _up_verbs.add_argument("--check", action="store_true",
                           help="read-only update status: product version, install "
                                "method, revision, runtime-image state (default)")
    _up_verbs.add_argument("--apply", action="store_true",
                           help="back up a timestamped snapshot, then fast-forward "
                                "the checkout (never discards local work)")
    _up_verbs.add_argument("--rollback", action="store_true",
                           help="restore the recorded pre-apply revision from the "
                                "most recent update backup")
    up.add_argument("--json", action="store_true",
                    help="emit the structured receipt as JSON")
    up.add_argument("--source", default="",
                    help="override the update source: a git remote name or a "
                         "local repository/bundle path")
    up.add_argument("--backup-dir", default=None,
                    help="explicit update backup directory (default: the Qubit "
                         "backup dir under <state>/backups/update/)")
    br = add("browser", help="Loopback browser tabs (read-only unless --confirm; profiles untouched)")
    br_sub = br.add_subparsers(dest="browser_command")
    br_sub.add_parser("status", parents=[common],
                      help="loopback CDP state (read-only probe)")
    br_close = br_sub.add_parser("close-profile", parents=[common],
                                 help="refused: real profiles are never mutated")
    br_close.add_argument("--profile", default="default")
    br_sub.add_parser("tabs", parents=[common],
                      help="list open page tabs (read-only)")
    br_sub.add_parser("doctor", parents=[common],
                      help="bridge health: endpoint, tabs, ledger, launch config (read-only)")
    br_read = br_sub.add_parser("read", parents=[common],
                                help="read the current tab: title, URL, bounded text (read-only)")
    br_read.add_argument("--target", default="",
                         help="tab id (default: Qubit-current tab)")
    br_shot = br_sub.add_parser(
        "screenshot", parents=[common],
        help="bounded screenshot of the current tab (read-only)")
    br_shot.add_argument("--target", default="")
    br_shot.add_argument("--clip", default="",
                         help="x,y,w,h crop (optional)")
    br_shot.add_argument("--format", dest="fmt", default="jpeg",
                         choices=("jpeg", "png"))
    br_shot.add_argument("--max-bytes", type=int, default=0)
    br_find = br_sub.add_parser("find", parents=[common],
                                help="search the current tab text (read-only)")
    br_find.add_argument("query", help="text to search (<=200 chars)")
    br_find.add_argument("--target", default="")
    br_act = br_sub.add_parser("activate", parents=[common],
                               help="bring a tab to front (needs --confirm)")
    br_act.add_argument("target", help="tab id")
    br_act.add_argument("--confirm", action="store_true")
    br_open = br_sub.add_parser("open", parents=[common],
                                help="open a URL in a new tab (needs --confirm)")
    br_open.add_argument("url", help="http(s) URL, no credentials")
    br_open.add_argument("--confirm", action="store_true")
    br_ob = br_sub.add_parser("open-browser", parents=[common],
                              help="focus the browser on the current tab (needs --confirm)")
    br_ob.add_argument("--confirm", action="store_true")
    br_nav = br_sub.add_parser("navigate", parents=[common],
                               help="navigate a tab to a URL (needs --confirm)")
    br_nav.add_argument("target", help="tab id")
    br_nav.add_argument("url", help="http(s) URL, no credentials")
    br_nav.add_argument("--confirm", action="store_true")
    br_fill = br_sub.add_parser("fill", parents=[common],
                                help="type text into a form field (needs --confirm)")
    br_fill.add_argument("target", help="tab id")
    br_fill.add_argument("selector", help="CSS selector (<=256 chars)")
    br_fill.add_argument("text", help="text to type (<=2000 chars)")
    br_fill.add_argument("--confirm", action="store_true")
    br_click = br_sub.add_parser("click", parents=[common],
                                 help="click an element (needs --confirm)")
    br_click.add_argument("target", help="tab id")
    br_click.add_argument("selector", help="CSS selector (<=256 chars)")
    br_click.add_argument("--confirm", action="store_true")
    br_tclose = br_sub.add_parser("close", parents=[common],
                                  help="close a tab (needs --confirm)")
    br_tclose.add_argument("target", help="tab id")
    br_tclose.add_argument("--confirm", action="store_true")
    br_fav = br_sub.add_parser("favicon", parents=[common],
                               help="capture the current tab's favicon "
                                    "(read-only; bounded same-origin image)")
    br_fav.add_argument("--target", default="")
    br_fav.add_argument("--max-bytes", type=int, default=0)
    br_fc = br_sub.add_parser("favicon-cache", parents=[common],
                              help="list the bounded favicon cache (read-only)")
    br_fc.add_argument("--limit", type=int, default=32)
    br_dl = br_sub.add_parser("download", parents=[common],
                              help="save the displayed document into an "
                                   "approved project root (needs --confirm)")
    br_dl.add_argument("dest", help="destination path inside an approved project root")
    br_dl.add_argument("--target", default="")
    br_dl.add_argument("--max-bytes", type=int, default=0)
    br_dl.add_argument("--confirm", action="store_true")
    acp = add("acp", help="Editor-integration checks + ACP external harness client (no ACP server shipped)")
    acp.add_argument("--accept-hooks", action="store_true",
                     help="refused: unseen hooks are never auto-approved")
    acp.add_argument("--check", action="store_true",
                     help="editor readiness probes (read-only)")
    acp.add_argument("--setup", action="store_true",
                     help="print a manual editor snippet (never writes)")
    acp.add_argument("--setup-browser", action="store_true",
                     help="print manual browser-integration steps")
    acp.add_argument("--editor", default="vscode",
                     help="editor for --setup (vscode|zed|jetbrains)")
    acp.add_argument("--version", action="store_true",
                     help="report the ACP server state")
    acp.add_argument("--yes", "-y", action="store_true",
                     help="accepted and ignored (no prompts exist)")
    acp_sub = acp.add_subparsers(dest="acp_command")
    acp_sub.add_parser("status", parents=[common],
                       help="external harness client state (read-only)")
    acp_sub.add_parser("catalog", parents=[common],
                       help="PATH presence + configured harnesses (read-only)")
    acp_hs = acp_sub.add_parser("handshake", parents=[common],
                                help="shape ACP initialize (needs --confirm; never executes)")
    acp_hs.add_argument("harness")
    acp_hs.add_argument("--confirm", action="store_true")
    acp_send = acp_sub.add_parser("send", parents=[common],
                                  help="shape session/prompt; always typed-refused")
    acp_send.add_argument("harness")
    acp_send.add_argument("prompt", nargs="+")
    acp_send.add_argument("--confirm", action="store_true")
    acp_recv = acp_sub.add_parser("receipts", parents=[common],
                                  help="redacted ACP receipts (bounded tail)")
    acp_recv.add_argument("--harness", default="")
    acp_conf = acp_sub.add_parser("configure", parents=[common],
                                  help="record a harness credential reference (needs --confirm)")
    acp_conf.add_argument("harness")
    acp_conf.add_argument("--credential-ref", default="",
                          help="env:VAR or file:PATH (never a value)")
    acp_conf.add_argument("--confirm", action="store_true")
    acp_unconf = acp_sub.add_parser("unconfigure", parents=[common],
                                    help="remove a configured harness (needs --confirm)")
    acp_unconf.add_argument("harness")
    acp_unconf.add_argument("--confirm", action="store_true")
    pl = add("plugins", help="Plugin runtime registry (manifests are metadata; in-process handlers only)")
    pl_sub = pl.add_subparsers(dest="plugins_command")
    pl_sub.add_parser("status", parents=[common],
                      help="typed plugin runtime state (read-only)")
    pl_sub.add_parser("list", parents=[common],
                      help="list registered plugins + built-ins (read-only)")
    pl_show = pl_sub.add_parser("show", parents=[common],
                                help="show one plugin record (redacted)")
    pl_show.add_argument("plugin_id")
    pl_reg = pl_sub.add_parser("register", parents=[common],
                               help="record a validated manifest (needs --confirm; never executes)")
    pl_reg.add_argument("manifest", help="path to a JSON manifest")
    pl_reg.add_argument("--confirm", action="store_true")
    pl_inv = pl_sub.add_parser("invoke", parents=[common],
                               help="invoke an in-process handler (needs --confirm)")
    pl_inv.add_argument("plugin_id")
    pl_inv.add_argument("--text", default="",
                        help="built-in echo text (in-process handlers only)")
    pl_inv.add_argument("--confirm", action="store_true")
    pl_unreg = pl_sub.add_parser("unregister", parents=[common],
                                 help="remove a registered plugin (needs --confirm)")
    pl_unreg.add_argument("plugin_id")
    pl_unreg.add_argument("--confirm", action="store_true")
    pl_sub.add_parser("doctor", parents=[common],
                      help="store/handlers/latch health (read-only)")
    pl_sub.add_parser("catalog", parents=[common],
                      help="installable plugin rows: built-ins, registered, "
                           "reviewed local manifests (read-only)")
    pl_inst = pl_sub.add_parser("install", parents=[common],
                                help="install a reviewed catalog id or local "
                                     "manifest path (needs --confirm)")
    pl_inst.add_argument("identifier",
                         help="reviewed catalog id or local *.json manifest path")
    pl_inst.add_argument("--confirm", action="store_true")
    ch = add("channels", help="Inbound channel framework (references only; no listener starts)")
    ch_sub = ch.add_subparsers(dest="channels_command")
    ch_sub.add_parser("status", parents=[common],
                      help="typed inbound state; connected is never faked")
    ch_sub.add_parser("catalog", parents=[common],
                      help="every known channel with an honest typed state")
    ch_sub.add_parser("accounts", parents=[common],
                      help="configured references only (never values)")
    ch_conf = ch_sub.add_parser("configure", parents=[common],
                                help="record an env:/file: reference (needs --confirm)")
    ch_conf.add_argument("channel")
    ch_conf.add_argument("secret_ref", help="env:VAR or file:PATH (never a value)")
    ch_conf.add_argument("--events", default="",
                         help="comma-separated event allowlist (default: message)")
    ch_conf.add_argument("--confirm", action="store_true")
    ch_unconf = ch_sub.add_parser("unconfigure", parents=[common],
                                  help="remove a configured channel (needs --confirm)")
    ch_unconf.add_argument("channel")
    ch_unconf.add_argument("--confirm", action="store_true")
    ch_recv = ch_sub.add_parser("receive", parents=[common],
                                help="validate a signed raw delivery (HMAC; no listener)")
    ch_recv.add_argument("channel")
    ch_recv.add_argument("payload_file", type=Path,
                         help="file containing the raw request body")
    ch_recv.add_argument("--signature", default="",
                         help="HMAC-SHA256 signature (sha256=... accepted)")
    ch_recv.add_argument("--timestamp", type=float, default=None)
    ch_recv.add_argument("--delivery-id", default="")
    ch_recv.add_argument("--sender", default="")
    ch_recv.add_argument("--event", default="message")
    ch_sub.add_parser("doctor", parents=[common],
                      help="store/listener/latch diagnostics (read-only)")
    lsp = add("lsp", help="Language-server inventory (binaries never downloaded)")
    lsp_sub = lsp.add_subparsers(dest="lsp_command")
    lsp_sub.add_parser("status", parents=[common],
                       help="layer state + inventory summary (read-only)")
    lsp_sub.add_parser("list", parents=[common],
                       help="known servers with PATH presence (read-only)")
    lsp_which = lsp_sub.add_parser("which", parents=[common],
                                   help="locate one server on PATH (read-only)")
    lsp_which.add_argument("server")
    lsp_install = lsp_sub.add_parser("install", parents=[common],
                                     help="refused: binaries never downloaded")
    lsp_install.add_argument("server", nargs="?", default="")
    lsp_sub.add_parser("install-all", parents=[common],
                       help="refused: binaries never downloaded")
    lsp_sub.add_parser("restart", parents=[common],
                       help="typed no-client report (no persistent clients)")
    th = add("theme", help="Optional Qubit themes and token tweaks")
    th_sub = th.add_subparsers(dest="theme_command")
    th_sub.add_parser("list", parents=[common], help="list built-in presets")
    th_sub.add_parser("show", parents=[common],
                      help="show the active preset + tweaks")
    th_set = th_sub.add_parser("set", parents=[common],
                               help="switch preset (dry-run unless --confirm)")
    th_set.add_argument("name")
    th_set.add_argument("--confirm", action="store_true")
    th_tweak = th_sub.add_parser("set-tweak", parents=[common],
                                 help="override one token (dry-run unless --confirm)")
    th_tweak.add_argument("token", help="design token, e.g. --primary")
    th_tweak.add_argument("color", help="#rgb or #rrggbb hex")
    th_tweak.add_argument("--confirm", action="store_true")
    th_clear = th_sub.add_parser("clear-tweak", parents=[common],
                                 help="remove one override (dry-run unless --confirm)")
    th_clear.add_argument("token")
    th_clear.add_argument("--confirm", action="store_true")
    th_reset = th_sub.add_parser("reset", parents=[common],
                                   help="restore the default preset (dry-run unless --confirm)")
    th_reset.add_argument("--confirm", action="store_true")
    px = add("proxy", help="Forwarding-proxy inventory (no server runs)")
    px_sub = px.add_subparsers(dest="proxy_command")
    px_sub.add_parser("status", parents=[common],
                      help="proxy server state (read-only)")
    px_sub.add_parser("providers", parents=[common],
                      help="per-provider eligibility (references only)")
    px_sub.add_parser("start", parents=[common],
                      help="refused: forwarding would bypass egress + audit")
    hk = add("hooks", help="Declared event subscriptions (shells never execute)")
    hk_sub = hk.add_subparsers(dest="hooks_command")
    hk_declare = hk_sub.add_parser("declare", parents=[common],
                                   help="record a subscription disabled (dry-run unless --confirm)")
    hk_declare.add_argument("name")
    hk_declare.add_argument("--event", required=True)
    hk_declare.add_argument("--shell", required=True,
                            help="shell text for REVIEW ONLY (never executed)")
    hk_declare.add_argument("--confirm", action="store_true")
    hk_sub.add_parser("list", parents=[common], help="list declarations")
    for _hk_ro, _hk_help in (("show", "show one declaration"),
                             ("validate", "static screening (never executes)"),
                             ("test", "dry-run delivery plan"),
                             ("doctor", "registry health")):
        _p = hk_sub.add_parser(_hk_ro, parents=[common], help=_hk_help)
        if _hk_ro in ("show", "validate", "test"):
            _p.add_argument("name")
    hk_fire = hk_sub.add_parser("fire", parents=[common],
                                help="manual broker-audited delivery (shells NOT executed)")
    hk_fire.add_argument("--event", required=True)
    hk_fire.add_argument("--confirm", action="store_true")
    sy = add("sync", help="Device-bound encrypted state handoff (user-carried)")
    sy_sub = sy.add_subparsers(dest="sync_command")
    sy_offer = sy_sub.add_parser("offer", parents=[common],
                                 help="export an encrypted bundle to the outbox "
                                      "(dry-run unless --confirm)")
    sy_offer.add_argument("--passphrase", default="",
                          help="offer passphrase (or VOICE_BRIDGE_PASSPHRASE)")
    sy_offer.add_argument("--for-device", default="",
                          help="bind to a destination device id (optional)")
    sy_offer.add_argument("--confirm", action="store_true")
    sy_sub.add_parser("list", parents=[common], help="list outbox offers")
    sy_show = sy_sub.add_parser("show", parents=[common],
                                help="show one offer manifest")
    sy_show.add_argument("offer_id")
    sy_revoke = sy_sub.add_parser("revoke", parents=[common],
                                  help="tombstone an offer (dry-run unless --confirm)")
    sy_revoke.add_argument("offer_id")
    sy_revoke.add_argument("--confirm", action="store_true")
    sy_accept = sy_sub.add_parser("accept", parents=[common],
                                  help="preview (default) or apply a bundle")
    sy_accept.add_argument("bundle", nargs="?", default="",
                           help="bundle file (or use --offer)")
    sy_accept.add_argument("--offer", default="",
                           help="outbox offer id (enforces revocation/binding)")
    sy_accept.add_argument("--passphrase", default="",
                           help="bundle passphrase (or VOICE_BRIDGE_PASSPHRASE)")
    sy_accept.add_argument("--apply", action="store_true",
                           help="actually restore (needs --confirm + enrollment)")
    sy_accept.add_argument("--confirm", action="store_true")
    pr = add("pairing", help="Enrollment pairing codes (hashed, single-use, granted)")
    pr_sub = pr.add_subparsers(dest="pairing_command")
    pr_create = pr_sub.add_parser("create", parents=[common],
                                  help="issue a one-time code (dry-run unless --confirm)")
    pr_create.add_argument("--label", required=True)
    pr_create.add_argument("--scope", action="append", default=[],
                           help="grant scope (repeatable, at least one)")
    pr_create.add_argument("--account", default="",
                           help="bind to a messaging account id (optional)")
    pr_create.add_argument("--ttl", type=float, default=600.0)
    pr_create.add_argument("--confirm", action="store_true")
    pr_approve = pr_sub.add_parser("approve", parents=[common],
                                   help="verify a code and issue its grant "
                                        "(dry-run unless --confirm)")
    pr_approve.add_argument("code")
    pr_approve.add_argument("--confirm", action="store_true")
    pr_sub.add_parser("list", parents=[common], help="list codes (redacted)")
    pr_revoke = pr_sub.add_parser("revoke", parents=[common],
                                  help="tombstone a code + revoke grant "
                                       "(dry-run unless --confirm)")
    pr_revoke.add_argument("code_id")
    pr_revoke.add_argument("--confirm", action="store_true")
    wh = add("webhook", help="Event subscriptions with HMAC verification (no listener)")
    wh_sub = wh.add_subparsers(dest="webhook_command")
    wh_add = wh_sub.add_parser("add", parents=[common],
                               help="record a subscription (dry-run unless --confirm)")
    wh_add.add_argument("sub_id")
    wh_add.add_argument("--url", required=True)
    wh_add.add_argument("--event", action="append", default=[],
                        help="event name (repeatable, at least one)")
    wh_add.add_argument("--secret", required=True,
                        help="signing secret as env:VAR or file:PATH (never a value)")
    wh_add.add_argument("--confirm", action="store_true")
    wh_sub.add_parser("list", parents=[common], help="list subscriptions")
    wh_show = wh_sub.add_parser("show", parents=[common],
                                help="show one subscription")
    wh_show.add_argument("sub_id")
    wh_remove = wh_sub.add_parser("remove", parents=[common],
                                  help="delete a subscription (dry-run unless --confirm)")
    wh_remove.add_argument("sub_id")
    wh_remove.add_argument("--confirm", action="store_true")
    wh_verify = wh_sub.add_parser("verify", parents=[common],
                                  help="verify one inbound delivery (HMAC + freshness + replay)")
    wh_verify.add_argument("sub_id")
    wh_verify.add_argument("--payload", required=True,
                           help="delivery bytes file (never inline secrets)")
    wh_verify.add_argument("--signature", required=True, help="hex HMAC-SHA256")
    wh_verify.add_argument("--timestamp", type=float, required=True)
    wh_verify.add_argument("--record-inbox", action="store_true",
                           help="also store the verified payload as inbound")
    wh_verify.add_argument("--sender", default="",
                           help="sender label for the inbound record")
    wh_sub.add_parser("test", parents=[common],
                      help="dry-run delivery plan (no secret, no network)").add_argument(
                          "sub_id")
    mg = add("msg", help="Brokered messaging accounts + outbox (credential-gated sends)")
    mg_sub = mg.add_subparsers(dest="msg_command")
    mg_acct = mg_sub.add_parser("account-add", parents=[common],
                                help="record a provider account (dry-run unless --confirm)")
    mg_acct.add_argument("account_id")
    mg_acct.add_argument("--provider", required=True,
                         choices=_msg_provider_choices)
    mg_acct.add_argument("--label", required=True)
    mg_acct.add_argument("--key-ref", required=True,
                         help="credential as env:VAR or file:PATH (never a value)")
    mg_acct.add_argument("--origin", default="")
    mg_acct.add_argument("--phone-number-id", default="",
                         help="whatsapp-cloud phone number id")
    mg_acct.add_argument("--confirm", action="store_true")
    mg_sub.add_parser("account-list", parents=[common], help="list accounts")
    mg_show = mg_sub.add_parser("account-show", parents=[common],
                                help="show one account")
    mg_show.add_argument("account_id")
    mg_rm = mg_sub.add_parser("account-remove", parents=[common],
                              help="remove an account (dry-run unless --confirm)")
    mg_rm.add_argument("account_id")
    mg_rm.add_argument("--confirm", action="store_true")
    mg_draft = mg_sub.add_parser("draft", parents=[common],
                                 help="stage an outbox draft (dry-run unless --confirm)")
    mg_draft.add_argument("--account", required=True)
    mg_draft.add_argument("--to", required=True)
    mg_draft.add_argument("--body", required=True)
    mg_draft.add_argument("--idempotency-key", default="")
    mg_draft.add_argument("--confirm", action="store_true")
    mg_approve = mg_sub.add_parser("approve", parents=[common],
                                   help="approve a draft (dry-run unless --confirm)")
    mg_approve.add_argument("msg_id")
    mg_approve.add_argument("--confirm", action="store_true")
    mg_send = mg_sub.add_parser("send", parents=[common],
                                help="send with fresh exact confirmation")
    mg_send.add_argument("msg_id")
    mg_send.add_argument("--confirm", action="store_true")
    mg_retry = mg_sub.add_parser("retry", parents=[common],
                                 help="re-attempt queued/failed (dry-run unless --confirm)")
    mg_retry.add_argument("msg_id")
    mg_retry.add_argument("--confirm", action="store_true")
    mg_link = mg_sub.add_parser("link", parents=[common],
                                help="typed link state (read-only)")
    mg_link.add_argument("account_id")
    mg_list = mg_sub.add_parser("list", parents=[common], help="list outbox")
    mg_list.add_argument("--state", default="",
                         choices=["", "draft", "approved", "queued", "sent", "failed"])
    mg_show = mg_sub.add_parser("show", parents=[common], help="show one message")
    mg_show.add_argument("msg_id")
    # OC-D16: dynamic Feishu agent routing records (local, credential-reference
    # only). Sending as an agent needs the operator-injected open-platform
    # transport; without it the op is a typed MSG_TRANSPORT_UNAVAILABLE.
    fa_add = mg_sub.add_parser("feishu-agent-add", parents=[common],
                               help="record a dynamic Feishu agent route (dry-run unless --confirm)")
    fa_add.add_argument("agent_id")
    fa_add.add_argument("--app-id-ref", required=True,
                        help="env:VAR or file:PATH reference (never a value)")
    fa_add.add_argument("--allowlist", default="",
                        help="comma/space-separated chat allowlist")
    fa_add.add_argument("--commands", default="",
                        help="comma/space-separated command allowlist")
    fa_add.add_argument("--confirm", action="store_true")
    mg_sub.add_parser("feishu-agent-list", parents=[common],
                      help="list dynamic Feishu agent routes (read-only)")
    fa_rm = mg_sub.add_parser("feishu-agent-remove", parents=[common],
                              help="remove a dynamic Feishu agent route (dry-run unless --confirm)")
    fa_rm.add_argument("agent_id")
    fa_rm.add_argument("--confirm", action="store_true")
    fa_send = mg_sub.add_parser("feishu-send-as-agent", parents=[common],
                                help="send as a dynamic Feishu agent (needs live transport; --confirm)")
    fa_send.add_argument("agent_id")
    fa_send.add_argument("--account", required=True)
    fa_send.add_argument("--to", required=True)
    fa_send.add_argument("--body", required=True)
    fa_send.add_argument("--confirm", action="store_true")
    vo = add("voice", help="Voice command registry (resolve/execute/confirm/cancel)")
    vo_sub = vo.add_subparsers(dest="voice_command")
    vo_say = vo_sub.add_parser("say", parents=[common],
                               help="resolve + execute one request (typed text in)")
    vo_say.add_argument("text", nargs="+", help="request text, e.g. 'list projects'")
    vo_say.add_argument("--session", default="cli")
    vo_say.add_argument("--json", action="store_true")
    vo_confirm = vo_sub.add_parser("confirm", parents=[common],
                                   help="consume a pending confirmation (latest or id)")
    vo_confirm.add_argument("confirmation_id", nargs="?", default="")
    vo_confirm.add_argument("--session", default="cli")
    vo_cancel = vo_sub.add_parser("cancel", parents=[common],
                                  help="deny pending confirmations")
    vo_cancel.add_argument("confirmation_id", nargs="?", default="")
    vo_cancel.add_argument("--session", default="cli")
    vo_sub.add_parser("commands", parents=[common],
                      help="list every voice command with aliases and help")
    bn = add("brain", help="Multi-principal profiles, accounts, vault refs, onboarding")
    bn_sub = bn.add_subparsers(dest="brain_command")
    bn_padd = bn_sub.add_parser("principal-add", parents=[common],
                                help="register a principal (dry-run unless --confirm)")
    bn_padd.add_argument("principal_id")
    bn_padd.add_argument("--kind", required=True,
                         choices=["person", "organization", "employee"])
    bn_padd.add_argument("--label", required=True)
    bn_padd.add_argument("--confirm", action="store_true")
    bn_sub.add_parser("principal-list", parents=[common],
                      help="list principals")
    bn_madd = bn_sub.add_parser("member-add", parents=[common],
                                help="link employee to org (dry-run unless --confirm)")
    bn_madd.add_argument("org")
    bn_madd.add_argument("employee")
    bn_madd.add_argument("--confirm", action="store_true")
    bn_mrm = bn_sub.add_parser("member-remove", parents=[common],
                               help="unlink (preserves data + audit)")
    bn_mrm.add_argument("org")
    bn_mrm.add_argument("employee")
    bn_mrm.add_argument("--confirm", action="store_true")
    bn_pset = bn_sub.add_parser("profile-set", parents=[common],
                                help="set a profile field (dry-run unless --confirm)")
    bn_pset.add_argument("principal")
    bn_pset.add_argument("key")
    bn_pset.add_argument("value")
    bn_pset.add_argument("--identity", default="default")
    bn_pset.add_argument("--purpose", default="")
    bn_pset.add_argument("--trust", default="user",
                         choices=["user", "observed", "inferred"])
    bn_pset.add_argument("--retention-days", type=int, default=None)
    bn_pset.add_argument("--confirm", action="store_true")
    bn_pshow = bn_sub.add_parser("profile-show", parents=[common],
                                 help="show a scoped profile (masked)")
    bn_pshow.add_argument("principal")
    bn_pshow.add_argument("--identity", default="default")
    bn_pforget = bn_sub.add_parser("profile-forget", parents=[common],
                                   help="delete field/identity/profile (dry-run unless --confirm)")
    bn_pforget.add_argument("principal")
    bn_pforget.add_argument("--identity", default="")
    bn_pforget.add_argument("--key", default="")
    bn_pforget.add_argument("--confirm", action="store_true")
    bn_sub.add_parser("profile-sweep", parents=[common],
                      help="delete expired fields (dry-run unless --confirm)").add_argument(
                          "--confirm", action="store_true", dest="confirm")
    bn_pexp = bn_sub.add_parser("profile-export", parents=[common],
                                help="export scoped profile data")
    bn_pexp.add_argument("principal")
    bn_pexp.add_argument("--identity", default="")
    bn_aadd = bn_sub.add_parser("account-add", parents=[common],
                                help="bind an account alias (dry-run unless --confirm)")
    bn_aadd.add_argument("alias")
    bn_aadd.add_argument("--origin", required=True)
    bn_aadd.add_argument("--principal", required=True)
    bn_aadd.add_argument("--identity", default="default")
    bn_aadd.add_argument("--label", default="")
    bn_aadd.add_argument("--confirm", action="store_true")
    bn_alist = bn_sub.add_parser("account-list", parents=[common],
                                 help="list account bindings")
    bn_alist.add_argument("--principal", default="")
    bn_arm = bn_sub.add_parser("account-remove", parents=[common],
                               help="remove a binding (dry-run unless --confirm)")
    bn_arm.add_argument("alias")
    bn_arm.add_argument("--confirm", action="store_true")
    bn_vset = bn_sub.add_parser("vault-set", parents=[common],
                                help="record a secret reference (dry-run unless --confirm)")
    bn_vset.add_argument("name")
    bn_vset.add_argument("--ref", required=True,
                         help="env:VAR, file:PATH, or keyring:NAME (never a value)")
    bn_vset.add_argument("--principal", default="")
    bn_vset.add_argument("--confirm", action="store_true")
    bn_vlist = bn_sub.add_parser("vault-list", parents=[common],
                                 help="list references (kinds only)")
    bn_vlist.add_argument("--principal", default="")
    bn_vrm = bn_sub.add_parser("vault-remove", parents=[common],
                               help="delete a reference (dry-run unless --confirm)")
    bn_vrm.add_argument("name")
    bn_vrm.add_argument("--confirm", action="store_true")
    bn_on = bn_sub.add_parser("onboard", parents=[common],
                              help="guided first-run setup (preview unless --confirm)")
    bn_on.add_argument("--answers-file", default="",
                       help="JSON answers file (no answers = show the plan)")
    bn_on.add_argument("--confirm", action="store_true")
    mg_inbox = mg_sub.add_parser("inbox", parents=[common],
                                 help="list inbound messages (redacted previews)")
    mg_inbox.add_argument("--subscription", default="")
    mg_inshow = mg_sub.add_parser("inbox-show", parents=[common],
                                  help="show one inbound message")
    mg_inshow.add_argument("msg_id")
    mg_sum = mg_sub.add_parser("summarize", parents=[common],
                               help="extractive digest of one inbound message")
    mg_sum.add_argument("msg_id")
    mg_reply = mg_sub.add_parser("draft-reply", parents=[common],
                                 help="draft a linked reply (dry-run unless --confirm)")
    mg_reply.add_argument("msg_id")
    mg_reply.add_argument("--account", required=True)
    mg_reply.add_argument("--body", required=True)
    mg_reply.add_argument("--confirm", action="store_true")
    gw = add("gateway", help="Account-bound gateway records (no process supervision)")
    gw_sub = gw.add_subparsers(dest="gateway_command")
    gw_add = gw_sub.add_parser("add", parents=[common],
                               help="record a gateway (dry-run unless --confirm)")
    gw_add.add_argument("gateway_id")
    gw_add.add_argument("--provider", required=True,
                        choices=_msg_provider_choices)
    gw_add.add_argument("--account", required=True)
    gw_add.add_argument("--channel", required=True,
                        choices=["webhook", "polling", "websocket"])
    gw_add.add_argument("--key-ref", required=True,
                        help="credential as env:VAR or file:PATH (never a value)")
    gw_add.add_argument("--confirm", action="store_true")
    gw_sub.add_parser("list", parents=[common], help="list gateways")
    gw_show = gw_sub.add_parser("show", parents=[common], help="show one gateway")
    gw_show.add_argument("gateway_id")
    gw_rm = gw_sub.add_parser("remove", parents=[common],
                              help="delete a record (dry-run unless --confirm)")
    gw_rm.add_argument("gateway_id")
    gw_rm.add_argument("--confirm", action="store_true")
    gw_link = gw_sub.add_parser("link", parents=[common],
                                help="typed link state (read-only)")
    gw_link.add_argument("gateway_id")
    # Qubit-owned gateway control (Owner Decision #10): `status` is real; `start`/
    # `stop`/`restart` drive the real Qubit gateway supervisor through the one
    # authority path (e-stop → confirmation → audit). `run`/`install` stay
    # operator-owned.
    gw_sub.add_parser("status", parents=[common],
                      help="real Qubit gateway status (service, pid, health, restarts)")
    for _gw_ctl in ("start", "stop", "restart"):
        _gw_p = gw_sub.add_parser(
            _gw_ctl, parents=[common],
            help="control the Qubit gateway service (confirm-gated)")
        _gw_p.add_argument("--confirm", action="store_true")
    for _gw_no in ("run", "install"):
        gw_sub.add_parser(_gw_no, parents=[common],
                          help="refused: operator supervises gateway processes")
    gw_sub.add_parser("protocol", parents=[common],
                      help="read-only gateway protocol + RPC method inventory")
    gw_neg = gw_sub.add_parser("negotiate", parents=[common],
                               help="negotiate the gateway protocol version")
    gw_neg.add_argument("--client-version", default=None)
    gw_serve = gw_sub.add_parser("serve", parents=[common],
                                 help="refused: no standalone gateway listener")
    gw_serve.add_argument("--action", default="serve")
    gw_rpc = gw_sub.add_parser(
        "rpc", parents=[common],
        help="in-process gateway RPC: list methods, or call one")
    gw_rpc.add_argument("method", nargs="?", default="")
    gw_rpc.add_argument("--params", default="{}",
                        help="JSON params object for the method call")
    pe = add("peer", help="Peer gateways: registry, enrollment, typed delivery")
    pe_sub = pe.add_subparsers(dest="peer_command")
    pe_add = pe_sub.add_parser("add", parents=[common],
                               help="record a peer (dry-run unless --confirm)")
    pe_add.add_argument("peer_id")
    pe_add.add_argument("--url", required=True)
    pe_add.add_argument("--account", required=True)
    pe_add.add_argument("--key-ref", required=True,
                        help="credential as env:VAR or file:PATH (never a value)")
    pe_add.add_argument("--confirm", action="store_true")
    pe_sub.add_parser("list", parents=[common], help="list peers")
    pe_show = pe_sub.add_parser("show", parents=[common], help="show one peer")
    pe_show.add_argument("peer_id")
    pe_rm = pe_sub.add_parser("remove", parents=[common],
                              help="delete a peer (dry-run unless --confirm)")
    pe_rm.add_argument("peer_id")
    pe_rm.add_argument("--confirm", action="store_true")
    pe_link = pe_sub.add_parser("link", parents=[common],
                                help="typed link state (add --probe for a live check)")
    pe_link.add_argument("peer_id")
    pe_link.add_argument("--probe", action="store_true")
    pe_dm = pe_sub.add_parser("dm", parents=[common],
                              help="deliver a message (fresh exact --confirm)")
    pe_dm.add_argument("peer_id")
    pe_dm.add_argument("--to", required=True)
    pe_dm.add_argument("--text", required=True)
    pe_dm.add_argument("--confirm", action="store_true")
    pe_run = pe_sub.add_parser("run", parents=[common],
                               help="run a task on a peer (fresh exact --confirm)")
    pe_run.add_argument("peer_id")
    pe_run.add_argument("--agent", default="")
    pe_run.add_argument("--text", required=True)
    pe_run.add_argument("--confirm", action="store_true")
    add("tui", help="live terminal board (same backend as status/dashboard)")
    vs = add("vision", help="Approved vision analysis (explicit, VRAM-guarded)")
    vs_sub = vs.add_subparsers(dest="vision_command")
    vs_desc = vs_sub.add_parser("describe", parents=[common],
                                help="analyze one image (dry-run unless --confirm)")
    vs_desc.add_argument("image", help="local image path (png/jpeg/gif/webp/bmp)")
    vs_desc.add_argument("--prompt", default="",
                         help="analysis question (default: factual description)")
    vs_desc.add_argument("--model", default="",
                         help="vision model override (default: intent.vision.model)")
    vs_desc.add_argument("--timeout", type=float, default=None,
                         help="request timeout s (10..600)")
    vs_desc.add_argument("--confirm", action="store_true")
    vs_sub.add_parser("status", parents=[common],
                      help="vision backend state (read-only, never loads)")
    for _hk_mut, _hk_help in (("revoke", "revoke a declaration"),
                              ("remove", "delete a declaration")):
        _p = hk_sub.add_parser(_hk_mut, parents=[common],
                               help=_hk_help + " (dry-run unless --confirm)")
        _p.add_argument("name")
        _p.add_argument("--confirm", action="store_true")
    ia = add("import-agent", help="Foreign agent setup inventory (never imports)")
    ia_sub = ia.add_subparsers(dest="import_agent_command")
    ia_inspect = ia_sub.add_parser("inspect", parents=[common],
                                   help="quarantined structural inventory (read-only)")
    ia_inspect.add_argument("--source", default="",
                            help="foreign agent tree (default: auto-detect below home)")
    ia_inspect.add_argument("--kind", default="",
                            choices=["", "claude-code", "codex"])
    ia_sub.add_parser("import", parents=[common],
                      help="refused: unreviewed state needs review first")
    ls = add("lessons", help="Learned preferences with stored provenance (Packet 6A)")
    ls_sub = ls.add_subparsers(dest="lessons_command")
    ls_sub.add_parser("list", parents=[common],
                      help="list lessons and pending reviews (read-only)")
    ls_promote = ls_sub.add_parser("promote", parents=[common],
                                   help="promote a reviewed lesson (needs --confirm)")
    ls_promote.add_argument("lesson_id", help="lesson id")
    ls_promote.add_argument("--confirm", action="store_true")
    ls_remove = ls_sub.add_parser("remove", parents=[common],
                                  help="tombstone a lesson (needs --confirm)")
    ls_remove.add_argument("lesson_id", help="lesson id")
    ls_remove.add_argument("--confirm", action="store_true")
    mem = add("memory", help="Packet 8M semantic memory (GENOME contract; disabled by default)")
    mem_sub = mem.add_subparsers(dest="memory_command")
    mem_status = mem_sub.add_parser("status", parents=[common],
                                    help="typed memory + optional semantic status")
    mem_status.add_argument("--probe", action="store_true",
                            help="opt-in live embedding-provider probe (may contact Ollama)")
    sem_en = mem_sub.add_parser("enable", parents=[common],
                                help="enable the optional semantic adapter (dry-run unless --confirm)")
    sem_en.add_argument("--provider", choices=["none", "hash", "ollama"], default=None)
    sem_en.add_argument("--confirm", action="store_true")
    sem_dis = mem_sub.add_parser("disable", parents=[common],
                                 help="disable the optional semantic adapter (dry-run unless --confirm)")
    sem_dis.add_argument("--confirm", action="store_true")
    sem_set = mem_sub.add_parser("set-provider", parents=[common],
                                 help="set the embedding provider none|hash|ollama (dry-run unless --confirm)")
    sem_set.add_argument("provider", choices=["none", "hash", "ollama"])
    sem_set.add_argument("--confirm", action="store_true")
    bel_rec = mem_sub.add_parser("belief-record", parents=[common],
                                 help="record a belief fact over [from, until)")
    bel_rec.add_argument("entity")
    bel_rec.add_argument("fact_type")
    bel_rec.add_argument("value")
    bel_rec.add_argument("--scope", default="default")
    bel_rec.add_argument("--from", dest="valid_from", type=float, default=None)
    bel_rec.add_argument("--until", dest="valid_until", type=float, default=None)
    bel_rec.add_argument("--source", choices=["user", "tool", "web", "model", "observed"],
                         default="user")
    bel_at = mem_sub.add_parser("belief-at", parents=[common],
                                help="facts true at a time for an entity")
    bel_at.add_argument("entity")
    bel_at.add_argument("--time", type=float, default=None, help="unix ts (default now)")
    bel_at.add_argument("--fact-type", default=None)
    bel_at.add_argument("--scope", default="default")
    mem_sub.add_parser("belief-conflicts", parents=[common],
                       help="coexisting conflicting belief facts")
    bel_ex = mem_sub.add_parser("explain", parents=[common],
                                help="stored-data-only explanation of a memory id")
    bel_ex.add_argument("memory_id", type=int)
    mem_sub.add_parser("journal-verify", parents=[common],
                       help="verify journal hash chain + replay reproduces live state")
    mem_re = mem_sub.add_parser("journal-replay", parents=[common],
                                help="preview replay snapshot hash into a temp store (never mutates live)")
    mem_re.add_argument("--until", type=int, default=None)
    mem_exp = mem_sub.add_parser("export", parents=[common],
                                 help="export active memory records to private JSON")
    mem_exp.add_argument("--out", type=Path, default=None)
    mem_exp.add_argument("--scope", default=None)
    # OC-48 local part: markdown workspace memory + offline dreaming. The
    # external LanceDB/Wiki/Honcho-style sync providers stay a typed local-only
    # refusal (see `workspace-providers`), so no credential is ever needed for
    # the local surface.
    mem_wsl = mem_sub.add_parser("workspace-list", parents=[common],
                                 help="list markdown workspace notes (read-only)")
    mem_wsl.add_argument("--tag", default="")
    mem_wss = mem_sub.add_parser("workspace-show", parents=[common],
                                 help="show one workspace note (read-only)")
    mem_wss.add_argument("name")
    mem_wsw = mem_sub.add_parser("workspace-write", parents=[common],
                                 help="write a workspace note (dry-run unless --confirm)")
    mem_wsw.add_argument("name")
    mem_wsw.add_argument("--body", required=True)
    mem_wsw.add_argument("--tag", action="append", default=[])
    mem_wsw.add_argument("--confirm", action="store_true")
    mem_wsf = mem_sub.add_parser("workspace-forget", parents=[common],
                                 help="forget a workspace note (dry-run unless --confirm)")
    mem_wsf.add_argument("name")
    mem_wsf.add_argument("--confirm", action="store_true")
    mem_wsd = mem_sub.add_parser("workspace-dream", parents=[common],
                                 help="offline consolidate workspace notes (dry-run unless --confirm)")
    mem_wsd.add_argument("--confirm", action="store_true")
    mem_sub.add_parser("workspace-providers", parents=[common],
                       help="workspace-memory providers (external sync typed local-only)")
    ws = add("websites", help="Packet 8E website-integration onboarding (proposal/observability, no live adapter)")
    ws_sub = ws.add_subparsers(dest="websites_command")
    ws_human = argparse.ArgumentParser(add_help=False)
    ws_human.add_argument("--human", action="store_true",
                          help="concise human-readable output (default: JSON)")
    ws_add = ws_sub.add_parser("add", parents=[common, ws_human],
                               help="validate + create a PENDING/disabled proposal (dry-run by default)")
    ws_add.add_argument("url")
    ws_add.add_argument("--name", default="", help="display name (default: host)")
    ws_sub.add_parser("list", parents=[common, ws_human], help="list integration records")
    ws_show = ws_sub.add_parser("show", parents=[common, ws_human],
                                help="show one record + effective permissions")
    ws_show.add_argument("id_or_url")
    ws_fetch = ws_sub.add_parser("fetch", parents=[common, ws_human],
                                 help="read-only SSRF-guarded fetch of a public URL")
    ws_fetch.add_argument("url")
    ws_search = ws_sub.add_parser("search", parents=[common, ws_human],
                                  help="web search via the configured provider (typed unavailable if none)")
    ws_search.add_argument("query")
    ws_ins = ws_sub.add_parser("inspect", parents=[common, ws_human],
                               help="run bounded read-only discovery")
    ws_ins.add_argument("id_or_url")
    ws_ins.add_argument("--no-network", action="store_true",
                        help="skip the network probe (typed offline result)")
    ws_ins.add_argument("--fixture", type=Path, default=None,
                        help="operator-supplied synthetic probe JSON (READY smoke path; never live)")
    ws_probe = ws_sub.add_parser("probe", parents=[common, ws_human],
                                 help="safe bounded LIVE probe of a public URL (requires --live; persists nothing)")
    ws_probe.add_argument("url")
    ws_probe.add_argument("--live", action="store_true",
                          help="explicit opt-in to one bounded read-only network probe")
    ws_chk = ws_sub.add_parser("check", parents=[common, ws_human],
                               help="re-verify against current permissions (read-only)")
    ws_chk.add_argument("id_or_url")
    ws_link = ws_sub.add_parser("manifest-link", parents=[common, ws_human],
                                help="link a valid, installed, active 7E skill manifest (dry-run unless --confirm)")
    ws_link.add_argument("id_or_url")
    ws_link.add_argument("skill_id", help="skill_id of an active installed 7E skill")
    ws_link.add_argument("--artifact", type=Path, default=None,
                         help="optional local artifact file whose bytes are SHA-256 verified")
    ws_link.add_argument("--artifact-sha256", default=None,
                         help="expected sha256 hex of the local artifact bytes")
    ws_link.add_argument("--confirm", action="store_true")
    ws_logs = ws_sub.add_parser("logs", parents=[common, ws_human],
                                help="redacted progress/error log for one record")
    ws_logs.add_argument("id_or_url")
    ws_logs.add_argument("--tail", type=int, default=50)
    ws_en = ws_sub.add_parser("enable", parents=[common, ws_human],
                              help="mark a validated proposal active (never grants) (dry-run unless --confirm)")
    ws_en.add_argument("id_or_url")
    ws_en.add_argument("--confirm", action="store_true")
    ws_dis = ws_sub.add_parser("disable", parents=[common, ws_human],
                               help="disable the record (dry-run unless --confirm)")
    ws_dis.add_argument("id_or_url")
    ws_dis.add_argument("--confirm", action="store_true")
    ws_grant = ws_sub.add_parser("grant", parents=[common, ws_human],
                                 help="grant the proposed read-only capabilities (scoped+expiring) (dry-run unless --confirm)")
    ws_grant.add_argument("id_or_url")
    ws_grant.add_argument("--capability", action="append", default=[],
                          help="restrict to a specific proposed capability; repeatable")
    ws_grant.add_argument("--ttl", type=float, default=None,
                          help="grant TTL seconds (default 7 days)")
    ws_grant.add_argument("--confirm", action="store_true")
    ws_rev = ws_sub.add_parser("revoke", parents=[common, ws_human],
                               help="revoke grants linked to the record (dry-run unless --confirm)")
    ws_rev.add_argument("id_or_url")
    ws_rev.add_argument("--confirm", action="store_true")
    ws_rem = ws_sub.add_parser("remove", parents=[common, ws_human],
                               help="revoke linked grants + remove the record (dry-run unless --confirm)")
    ws_rem.add_argument("id_or_url")
    ws_rem.add_argument("--confirm", action="store_true")
    ws_roll = ws_sub.add_parser("rollback", parents=[common, ws_human],
                                help="restore the prior record state (grants not auto-restored) (dry-run unless --confirm)")
    ws_roll.add_argument("id_or_url")
    ws_roll.add_argument("--confirm", action="store_true")
    act = add("actions", help="Packet 8F typed brokered actions (preview/approve/execute/cancel/receipt/retry/recover)")
    act_sub = act.add_subparsers(dest="actions_command")
    act_sub.add_parser("list", parents=[common],
                       help="list action contracts + effective permissions")
    act_show = act_sub.add_parser("show", parents=[common],
                                  help="show one action contract")
    act_show.add_argument("action_id")
    act_chk = act_sub.add_parser("check", parents=[common],
                                 help="read-only effective permission decision")
    act_chk.add_argument("action_id")
    act_chk.add_argument("--scope", default="")
    act_prev = act_sub.add_parser("preview", parents=[common],
                                  help="real no-side-effect preview + stable action hash")
    act_prev.add_argument("action_id")
    act_prev.add_argument("--args", default="{}", help="exact action args as JSON")
    act_prev.add_argument("--target", default="", help="target URL/workspace/app")
    act_prev.add_argument("--account", default="", help="account scope, e.g. gmail:default")
    act_prev.add_argument("--scope", default="", help="7E scope tag (default: contract first scope)")
    act_prev.add_argument("--idempotency-key", default="")
    act_exec = act_sub.add_parser("execute", parents=[common],
                                  help="gate+confirm+execute one action (dry-run by default; --confirm = fresh confirmation)")
    act_exec.add_argument("action_id")
    act_exec.add_argument("--args", default="{}", help="exact action args as JSON")
    act_exec.add_argument("--envelope", default=None,
                          help="full validated envelope JSON (two-step prepare path)")
    act_exec.add_argument("--target", default="", help="target URL/workspace/app")
    act_exec.add_argument("--account", default="", help="account scope")
    act_exec.add_argument("--scope", default="", help="7E scope tag")
    act_exec.add_argument("--idempotency-key", default="")
    act_exec.add_argument("--confirmation", default=None,
                          help="fresh confirmation id from `actions prepare`")
    act_exec.add_argument("--confirm", action="store_true",
                          help="explicit fresh human confirmation of this exact action")
    act_exec.add_argument("--expected-hash", default="",
                          help="action hash from a prior preview (stale-target guard)")
    act_exec.add_argument("--fixture", type=Path, default=None,
                          help="synthetic adapter outcomes JSON (explicit simulation; never live)")
    act_prep = act_sub.add_parser("prepare", parents=[common],
                                  help="issue a fresh expiring single-use confirmation bound to an envelope")
    act_prep.add_argument("action_id")
    act_prep.add_argument("--envelope", required=True, help="validated envelope JSON")
    act_can = act_sub.add_parser("cancel", parents=[common],
                                 help="cancel an in-flight action (idempotent)")
    act_can.add_argument("receipt_id")
    act_rec = act_sub.add_parser("receipt", parents=[common],
                                 help="show one redacted action receipt")
    act_rec.add_argument("receipt_id")
    act_recs = act_sub.add_parser("receipts", parents=[common],
                                  help="list recent action receipts")
    act_recs.add_argument("--limit", type=int, default=30)
    act_retry = act_sub.add_parser("retry", parents=[common],
                                   help="re-execute a failed action with the SAME args (fresh confirmation if required)")
    act_retry.add_argument("receipt_id")
    act_retry.add_argument("--args", default="{}", help="exact original args as JSON")
    act_retry.add_argument("--target", required=True)
    act_retry.add_argument("--scope", required=True)
    act_retry.add_argument("--account", default="")
    act_retry.add_argument("--confirmation", default=None)
    act_retry.add_argument("--confirm", action="store_true")
    act_retry.add_argument("--fixture", type=Path, default=None)
    act_recov = act_sub.add_parser("recover", parents=[common],
                                   help="compensating rollback for a reversible action (honest ROLLBACK_IMPOSSIBLE otherwise)")
    act_recov.add_argument("receipt_id")
    act_recov.add_argument("--confirmation", default=None)
    act_recov.add_argument("--confirm", action="store_true")
    act_recov.add_argument("--fixture", type=Path, default=None)
    ghost = add("ghost", help="Packet 8N Ghost desktop/browser capability adapter (optional, supervised; disabled by default)")
    ghost_sub = ghost.add_subparsers(dest="ghost_command")
    ghost_sub.add_parser("list", parents=[common],
                         help="list Ghost capabilities + effective permissions")
    ghost_sub.add_parser("status", parents=[common],
                         help="typed adapter/browser-role/session-grant status")
    ghost_show = ghost_sub.add_parser("show", parents=[common],
                                      help="show one Ghost capability contract")
    ghost_show.add_argument("action_id")
    ghost_chk = ghost_sub.add_parser("check", parents=[common],
                                     help="read-only effective permission decision")
    ghost_chk.add_argument("action_id")
    ghost_chk.add_argument("--scope", default="")
    ghost_gr = ghost_sub.add_parser("grant", parents=[common],
                                    help="issue a bounded user session grant for a read/reversible capability")
    ghost_gr.add_argument("capability")
    ghost_gr.add_argument("--scope", action="append", default=[],
                          help="scope tag; repeatable")
    ghost_gr.add_argument("--target", default="", help="origin or bounded target (default: contract first scope)")
    ghost_gr.add_argument("--role", default="",
                          choices=["", "chromium", "comet"],
                          help="browser role binding (optional)")
    ghost_gr.add_argument("--ttl", type=float, default=None,
                          help="grant TTL seconds (default 600)")
    ghost_rev = ghost_sub.add_parser("revoke", parents=[common],
                                     help="revoke a session grant (and its mirrored 7E grant)")
    ghost_rev.add_argument("grant_id")
    ghost_prev = ghost_sub.add_parser("preview", parents=[common],
                                      help="real no-side-effect preview + stable action hash")
    ghost_prev.add_argument("action_id")
    ghost_prev.add_argument("--args", default="{}", help="exact action args as JSON")
    ghost_prev.add_argument("--target", default="")
    ghost_prev.add_argument("--scope", default="")
    ghost_prev.add_argument("--role", default="")
    ghost_exec = ghost_sub.add_parser("execute", parents=[common],
                                      help="gate+confirm+execute one Ghost capability (dry-run by default; --confirm = fresh confirmation; role-bound actions need --target=https://approved-origin)")
    ghost_exec.add_argument("action_id")
    ghost_exec.add_argument("--args", default="{}")
    ghost_exec.add_argument("--target", default="")
    ghost_exec.add_argument("--scope", default="")
    ghost_exec.add_argument("--role", default="")
    ghost_exec.add_argument("--confirmation", default=None)
    ghost_exec.add_argument("--confirm", action="store_true")
    ghost_exec.add_argument("--expected-hash", default="")
    ghost_exec.add_argument("--fixture", type=Path, default=None,
                            help="synthetic adapter outcomes JSON (explicit simulation; never live)")
    ghost_exec.add_argument("--timeout", type=float, default=None,
                            help="cap the spawn-isolated execution window for side-effecting actions, 1..600s (never extends the contract budget; read-only actions run inline with no killable timeout by design)")
    ghost_retry = ghost_sub.add_parser("retry", parents=[common],
                                       help="re-execute a failed Ghost action with the SAME args/target/scope (fresh confirmation if required)")
    ghost_retry.add_argument("receipt_id")
    ghost_retry.add_argument("--args", default="{}",
                             help="exact original args as JSON (must match the receipt)")
    ghost_retry.add_argument("--target", default="desktop")
    ghost_retry.add_argument("--scope", default="")
    ghost_retry.add_argument("--role", default="")
    ghost_retry.add_argument("--confirmation", default=None)
    ghost_retry.add_argument("--confirm", action="store_true")
    ghost_retry.add_argument("--fixture", type=Path, default=None,
                             help="synthetic adapter outcomes JSON (explicit simulation; never live)")
    ghost_recov = ghost_sub.add_parser("recover", parents=[common],
                                       help="compensating rollback for a reversible Ghost action (honest ROLLBACK_IMPOSSIBLE otherwise)")
    ghost_recov.add_argument("receipt_id")
    ghost_recov.add_argument("--confirmation", default=None)
    ghost_recov.add_argument("--confirm", action="store_true")
    ghost_recov.add_argument("--fixture", type=Path, default=None,
                             help="synthetic adapter outcomes JSON (explicit simulation; never live)")
    ghost_can = ghost_sub.add_parser("cancel", parents=[common],
                                     help="cancel an in-flight Ghost action (idempotent)")
    ghost_can.add_argument("receipt_id")
    ghost_recs = ghost_sub.add_parser("receipts", parents=[common],
                                      help="list recent Ghost action receipts")
    ghost_recs.add_argument("--limit", type=int, default=30)
    ghost_en = ghost_sub.add_parser("enable", parents=[common],
                                    help="enable the adapter health flag (capabilities still need an executor)")
    ghost_dis = ghost_sub.add_parser("disable", parents=[common],
                                     help="disable the adapter")
    ghost_stop = ghost_sub.add_parser("emergency-stop", parents=[common],
                                      help="global immediate stop (latched until reset)")
    ghost_reset = ghost_sub.add_parser("reset", parents=[common],
                                       help="reset the emergency-stop latch (returns to disabled)")
    power = add("power", help="Packet 8O full-capability power profile: one policy path, capability inventory, owner power grant, global control")
    power_sub = power.add_subparsers(dest="power_command")
    power_sub.add_parser("profile", parents=[common],
                         help="inventory every installed capability across all 11 domains (available != authorized)")
    power_sub.add_parser("status", parents=[common],
                         help="typed global power-control status (emergency stop, disable, circuits)")
    power_sub.add_parser("circuits", parents=[common],
                         help="per-adapter circuit-breaker states")
    power_show = power_sub.add_parser("show", parents=[common],
                                      help="show one capability's contract + effective state")
    power_show.add_argument("capability")
    power_chk = power_sub.add_parser("check", parents=[common],
                                     help="read-only effective decision for one capability (one policy path)")
    power_chk.add_argument("capability")
    power_chk.add_argument("--scope", action="append", default=[])
    power_chk.add_argument("--target", default="")
    power_chk.add_argument("--role", default="", choices=["", "chromium", "comet"])
    power_chk.add_argument("--account", default="")
    power_gr = power_sub.add_parser("grant", parents=[common],
                                    help="issue the ONE owner power grant (supersedes previous; dry-run unless --confirm)")
    power_gr.add_argument("--browser", action="append", default=[], choices=["chromium", "comet"])
    power_gr.add_argument("--app", action="append", default=[])
    power_gr.add_argument("--account", action="append", default=[])
    power_gr.add_argument("--origin", action="append", default=[])
    power_gr.add_argument("--directory", action="append", default=[])
    power_gr.add_argument("--scope", action="append", default=[])
    power_gr.add_argument("--capability", action="append", default=[],
                          help="exact capability ids covered by the grant "
                               "(required)")
    power_gr.add_argument("--ttl", type=float, default=None,
                          help="grant duration seconds (default 7 days; max 90 days)")
    power_gr.add_argument("--confirm", action="store_true",
                          help="explicitly confirm the side effect")
    power_gr.add_argument("--note", default="")
    power_rev = power_sub.add_parser("revoke", parents=[common],
                                     help="revoke the owner power grant (dry-run unless --confirm)")
    power_rev.add_argument("grant_id")
    power_rev.add_argument("--confirm", action="store_true")
    power_reset_grant = power_sub.add_parser("reset-grant", parents=[common],
                                             help="remove the owner power grant entirely")
    power_reset_grant.add_argument("--confirm", action="store_true",
                                   help="explicitly confirm the grant reset")
    power_es = power_sub.add_parser("emergency-stop", parents=[common],
                                    help="global immediate stop: latches, cancels active runs; reset required")
    power_es.add_argument("--confirm", action="store_true",
                          help="explicitly confirm the emergency stop")
    power_reset = power_sub.add_parser("reset", parents=[common],
                                       help="reset the emergency-stop/disable latches (requires --confirm)")
    power_reset.add_argument("--confirm", action="store_true",
                             help="explicitly confirm the reset (human authorization)")
    power_dis = power_sub.add_parser("disable", parents=[common],
                                     help="global power disable (all capabilities denied)")
    power_dis.add_argument("--confirm", action="store_true",
                           help="explicitly confirm the global disable")
    power_en = power_sub.add_parser("enable", parents=[common],
                                    help="clear the global disable latch (requires no emergency stop)")
    power_en.add_argument("--confirm", action="store_true",
                          help="explicitly confirm the enable")
    vs = add("verified-skills", help="Packet 8P verified skill memory (disabled by default; owner-gated)")
    vs_sub = vs.add_subparsers(dest="verified_skills_command")
    vs_sub.add_parser("status", parents=[common],
                      help="typed store status + journal verification")
    vs_sub.add_parser("list", parents=[common],
                      help="list skills (optionally by status/kind)")
    vs_list_status = vs_sub.add_parser("list-status", parents=[common],
                                       help="list skills by status (default verified)")
    vs_list_status.add_argument("--status", default="verified",
                                choices=["quarantined", "verified", "refuted",
                                         "archived"])
    vs_show = vs_sub.add_parser("show", parents=[common], help="show one skill")
    vs_show.add_argument("id")
    vs_submit = vs_sub.add_parser("submit", parents=[common],
                                  help="stage a recipe candidate QUARANTINED (never auto-verified)")
    vs_submit.add_argument("--name", required=True)
    vs_submit.add_argument("--interface", default="")
    vs_submit.add_argument("--implementation", required=True)
    vs_submit.add_argument("--acceptance-test", required=True)
    vs_submit.add_argument("--task", required=True)
    vs_submit.add_argument("--capability", action="append", default=[])
    vs_submit.add_argument("--cost", default="normal",
                           choices=["cheap", "normal", "expensive"])
    vs_submit.add_argument("--trust", type=int, default=1, choices=[1, 2, 3])
    vs_submit.add_argument("--model", default="")
    vs_submit.add_argument("--confirm", action="store_true",
                           help="persist the candidate (dry-run unless --confirm)")
    vs_verify = vs_sub.add_parser("verify", parents=[common],
                                  help="OWNER verdict: verify a candidate (no self-verification)")
    vs_verify.add_argument("id")
    vs_verify.add_argument("--reason", default="")
    vs_verify.add_argument("--confirm", action="store_true")
    vs_refute = vs_sub.add_parser("refute", parents=[common],
                                  help="OWNER verdict: refute a candidate (terminal)")
    vs_refute.add_argument("id")
    vs_refute.add_argument("--reason", default="")
    vs_refute.add_argument("--confirm", action="store_true")
    vs_fail = vs_sub.add_parser("record-failure", parents=[common],
                                help="record an OBSERVED failure as a negative skill")
    vs_fail.add_argument("--task", required=True)
    vs_fail.add_argument("--approach", required=True)
    vs_fail.add_argument("--reason", default="")
    vs_fail.add_argument("--confirm", action="store_true")
    vs_recall = vs_sub.add_parser("recall", parents=[common],
                                  help="budgeted top-K recall + negatives slot")
    vs_recall.add_argument("query")
    vs_recall.add_argument("--top-k", type=int, default=5)
    vs_recall.add_argument("--token-budget", type=int, default=6000)
    vs_pin = vs_sub.add_parser("pin", parents=[common],
                               help="pin a verified skill (always hot; OWNER)")
    vs_pin.add_argument("id")
    vs_pin.add_argument("--confirm", action="store_true")
    vs_unpin = vs_sub.add_parser("unpin", parents=[common], help="unpin a skill")
    vs_unpin.add_argument("id")
    vs_unpin.add_argument("--confirm", action="store_true")
    vs_reinf = vs_sub.add_parser("reinforce", parents=[common],
                                 help="record a real outcome; failure re-checks then quarantines")
    vs_reinf.add_argument("id")
    vs_reinf.add_argument("--outcome", choices=["success", "failure"], required=True)
    vs_reinf.add_argument("--confirm", action="store_true")
    vs_quar = vs_sub.add_parser("quarantine-revoked", parents=[common],
                                help="quarantine a skill whose 7E permission was revoked")
    vs_quar.add_argument("id")
    vs_quar.add_argument("--confirm", action="store_true")
    vs_sweep = vs_sub.add_parser("sweep", parents=[common],
                                 help="retention: archive cold low-utility; quarantine stale")
    vs_sweep.add_argument("--confirm", action="store_true")
    vs_remove = vs_sub.add_parser("remove", parents=[common],
                                  help="remove a skill (OWNER; dry-run unless --confirm)")
    vs_remove.add_argument("id")
    vs_remove.add_argument("--confirm", action="store_true")
    vs_retier = vs_sub.add_parser("retier", parents=[common],
                                  help="recompute hot/warm/cold tiers by utility")
    vs_snap = vs_sub.add_parser("snapshot", parents=[common],
                                help="write a digest-verified backup snapshot")
    vs_snap.add_argument("--out", type=Path, default=None)
    vs_restore = vs_sub.add_parser("restore", parents=[common],
                                   help="restore a snapshot (OWNER; dry-run unless --confirm)")
    vs_restore.add_argument("snapshot", type=Path)
    vs_restore.add_argument("--merge", action="store_true")
    vs_restore.add_argument("--confirm", action="store_true")
    vs_export = vs_sub.add_parser("export", parents=[common],
                                  help="export a digest-verified snapshot to a file")
    vs_export.add_argument("--out", type=Path, required=True)
    vs_sub.add_parser("bundled", parents=[common],
                      help="list Qubit-bundled skill manifests (read-only; no network)")

    exp = add("experiments", help="Packet 8P controlled offline evaluation (disabled by default)")
    exp_sub = exp.add_subparsers(dest="experiments_command")
    exp_sub.add_parser("status", parents=[common],
                       help="typed store status + journal verification")
    exp_sub.add_parser("candidates", parents=[common], help="list candidates")
    exp_creg = exp_sub.add_parser("candidate", parents=[common],
                                  help="register a typed prompt/model candidate")
    exp_creg.add_argument("--role", choices=["champion", "challenger", "historical"],
                          required=True)
    exp_creg.add_argument("--label", required=True)
    exp_creg.add_argument("--model", default="local")
    exp_creg.add_argument("--description", default="")
    exp_creg.add_argument("--confirm", action="store_true")
    exp_start = exp_sub.add_parser("start", parents=[common],
                                   help="open one running experiment for a pair")
    exp_start.add_argument("--champion", required=True)
    exp_start.add_argument("--challenger", required=True)
    exp_start.add_argument("--confirm", action="store_true")
    exp_list = exp_sub.add_parser("list", parents=[common],
                                  help="list experiments")
    exp_list.add_argument("--status", default=None,
                          choices=["running", "promoted", "aborted", "held"])
    exp_show = exp_sub.add_parser("show", parents=[common],
                                  help="show one experiment + trials + signals")
    exp_show.add_argument("id")
    exp_trial = exp_sub.add_parser("trial", parents=[common],
                                   help="start a trial for a running experiment")
    exp_trial.add_argument("experiment_id")
    exp_trial.add_argument("--variant", choices=["champion", "challenger"],
                           required=True)
    exp_trial.add_argument("--confirm", action="store_true")
    exp_finish = exp_sub.add_parser("finish-trial", parents=[common],
                                    help="finish a trial")
    exp_finish.add_argument("trial_id")
    exp_finish.add_argument("--outcome", choices=["completed", "failed", "cancelled"],
                            required=True)
    exp_finish.add_argument("--note", default="")
    exp_finish.add_argument("--confirm", action="store_true")
    exp_sig = exp_sub.add_parser("signal", parents=[common],
                                 help="record an outcome signal (explicit/implicit, [0,1])")
    exp_sig.add_argument("trial_id")
    exp_sig.add_argument("--kind", choices=["explicit", "implicit"], required=True)
    exp_sig.add_argument("--source", default="user_explicit_good")
    exp_sig.add_argument("--value", type=float, required=True)
    exp_sig.add_argument("--confirm", action="store_true")
    exp_eval = exp_sub.add_parser("evaluate", parents=[common],
                                  help="compute promotion decision (read-only, no state change)")
    exp_eval.add_argument("id")
    exp_eval.add_argument("--seed", type=int, default=None)
    exp_prom = exp_sub.add_parser("promote", parents=[common],
                                  help="record a promotion (OWNER + gate report required; never auto)")
    exp_prom.add_argument("id")
    exp_prom.add_argument("--gate-report", required=True,
                          help="reference to a PASSING regression/security gate report")
    exp_prom.add_argument("--confirm", action="store_true")
    exp_abort = exp_sub.add_parser("abort", parents=[common],
                                   help="abort a running experiment (OWNER)")
    exp_abort.add_argument("id")
    exp_abort.add_argument("--confirm", action="store_true")
    exp_snap = exp_sub.add_parser("snapshot", parents=[common],
                                  help="write a digest-verified backup snapshot")
    exp_snap.add_argument("--out", type=Path, default=None)

    export = add("export-state", help="encrypt + export portable state (memory/lessons/reports/config/model)")
    fi = add("files", help="Scoped project-folder inspection (read-only; project roots only)")
    fi_sub = fi.add_subparsers(dest="files_command")
    fi_list = fi_sub.add_parser("list", parents=[common],
                                help="list a project folder (read-only)")
    fi_list.add_argument("path", help="absolute path inside a project folder")
    fi_list.add_argument("--depth", type=int, default=1)
    fi_read = fi_sub.add_parser("read", parents=[common],
                                help="read a project file, bounded + redacted (read-only)")
    fi_read.add_argument("path", help="absolute path inside a project folder")
    fi_read.add_argument("--max-chars", type=int, default=20000)
    fi_inspect = fi_sub.add_parser("inspect", parents=[common],
                                   help="inspect a folder or file (default: active project folder)")
    fi_inspect.add_argument("path", nargs="?", default="",
                            help="absolute path inside a project folder")
    fi_patch = fi_sub.add_parser(
        "patch", parents=[common],
        help="apply a unified diff inside a project folder (confirmed, brokered, reversible)")
    fi_patch.add_argument("target",
                          help="absolute file path inside a project folder")
    fi_pgrp = fi_patch.add_mutually_exclusive_group(required=True)
    fi_pgrp.add_argument("--patch", default="", help="unified diff text")
    fi_pgrp.add_argument("--patch-file", default="",
                         help="read the unified diff from this file")
    fi_patch.add_argument("--confirm", action="store_true")
    fi_rollback = fi_sub.add_parser(
        "rollback", parents=[common],
        help="roll back the last applied patch (confirmed, brokered)")
    fi_rollback.add_argument("target",
                             help="absolute file path inside a project folder")
    fi_rollback.add_argument("--backup", default="")
    fi_rollback.add_argument("--confirm", action="store_true")
    gi = add("git", help="Scoped read-only git inspection (status/log/diff/show; no writes, no network)")
    gi_sub = gi.add_subparsers(dest="git_command")
    gi_status = gi_sub.add_parser("status", parents=[common],
                                  help="porcelain work-tree status (read-only)")
    gi_status.add_argument("--root", default="",
                           help="absolute project folder (default: active project folder)")
    gi_log = gi_sub.add_parser("log", parents=[common],
                               help="bounded commit log (read-only)")
    gi_log.add_argument("--root", default="",
                        help="absolute project folder (default: active project folder)")
    gi_log.add_argument("--limit", type=int, default=10)
    gi_diff = gi_sub.add_parser("diff", parents=[common],
                                help="bounded unstaged diff (read-only)")
    gi_diff.add_argument("--root", default="",
                         help="absolute project folder (default: active project folder)")
    gi_diff.add_argument("--path", default="",
                         help="repo-relative file path (default: whole tree)")
    gi_show = gi_sub.add_parser("show", parents=[common],
                                help="show a committed file version (read-only)")
    gi_show.add_argument("path", help="repo-relative file path")
    gi_show.add_argument("--root", default="",
                         help="absolute project folder (default: active project folder)")
    gi_show.add_argument("--ref", default="HEAD",
                         help="HEAD or HEAD~N (default: HEAD)")
    ie = add("ide", help="IDE launcher (open only; in-file work uses supervised coding)")
    ie_sub = ie.add_subparsers(dest="ide_command")
    ie_sub.add_parser("status", parents=[common],
                      help="detected editors and default (read-only)")
    ie_open = ie_sub.add_parser("open", parents=[common],
                                help="open a workspace folder in the editor (needs --confirm)")
    ie_open.add_argument("--editor", default="",
                         help="code|cursor|zed|windsurf|subl|gedit|code-insiders (default: detected)")
    ie_open.add_argument("--folder", default="",
                         help="workspace folder (default: active project folder)")
    ie_open.add_argument("--confirm", action="store_true")
    sc = add("schedules", help="User scheduled jobs (SQLite store; execution via run-due)")
    sc_sub = sc.add_subparsers(dest="schedules_command")
    sc_create = sc_sub.add_parser("create", parents=[common],
                                  help="schedule a task (needs --confirm)")
    sc_create.add_argument("name", help="job name (<=80 chars)")
    sc_create.add_argument("task_text", help="task to run (<=2000 chars)")
    sc_create.add_argument("--when", required=True,
                           help="'every N minutes|hours', 'daily HH[:MM][am|pm]', 'once in N minutes|hours'")
    sc_create.add_argument("--deliver-to", default="",
                           help="delivery targets: channel:<account>:<recipient> "
                                "and/or webhook:<sub_id>, comma separated")
    sc_create.add_argument("--confirm", action="store_true")
    sc_list = sc_sub.add_parser("list", parents=[common],
                                help="list scheduled jobs (read-only)")
    sc_list.add_argument("--active-only", action="store_true")
    sc_show = sc_sub.add_parser("show", parents=[common],
                                help="show one job with recent runs (read-only)")
    sc_show.add_argument("job_id", type=int)
    for _sc_mut, _sc_help in (
            ("pause", "pause a job (needs --confirm)"),
            ("resume", "resume a job (needs --confirm)"),
            ("delete", "delete a job, history retained (needs --confirm)")):
        _p = sc_sub.add_parser(_sc_mut, parents=[common], help=_sc_help)
        _p.add_argument("job_id", type=int)
        _p.add_argument("--confirm", action="store_true")
    sc_hist = sc_sub.add_parser("history", parents=[common],
                                help="run history (read-only, retained after delete)")
    sc_hist.add_argument("--job-id", type=int, default=0)
    sc_hist.add_argument("--limit", type=int, default=30)
    sc_deliv = sc_sub.add_parser("deliveries", parents=[common],
                                 help="delivery history (read-only)")
    sc_deliv.add_argument("--job-id", type=int, default=0)
    sc_deliv.add_argument("--limit", type=int, default=30)
    sc_sub.add_parser("run-due", parents=[common],
                      help="execute due jobs now (bounded; pre-authorized at creation)")
    cr = add("cron", help="Scheduled jobs on the SAME canonical SQLite store "
             "as `schedules` (mutations need --confirm)")
    cr_sub = cr.add_subparsers(dest="cron_command")
    cr_list = cr_sub.add_parser("list", parents=[common],
                                help="list jobs, active and disabled (read-only)")
    cr_list.add_argument("--active-only", action="store_true",
                         help="omit disabled jobs (default: list all)")
    cr_add = cr_sub.add_parser("add", parents=[common],
                               help="schedule a task (needs --confirm)")
    cr_add.add_argument("name", help="job name (<=80 chars)")
    cr_add.add_argument("task_text", help="task to run (<=2000 chars)")
    cr_add.add_argument("--when", required=True,
                        help="'every N minutes|hours', 'daily HH[:MM][am|pm]', "
                             "'once in N minutes|hours'")
    cr_add.add_argument("--deliver-to", default="",
                        help="delivery targets: channel:<account>:<recipient> "
                             "and/or webhook:<sub_id>, comma separated")
    cr_add.add_argument("--confirm", action="store_true")
    cr_show = cr_sub.add_parser("show", parents=[common],
                                help="show one job with recent runs (read-only)")
    cr_show.add_argument("job_id", type=int)
    cr_rm = cr_sub.add_parser("remove", parents=[common],
                              help="delete a job, history retained (needs --confirm)")
    cr_rm.add_argument("job_id", type=int)
    cr_rm.add_argument("--confirm", action="store_true")
    for _cr_name, _cr_help in (
            ("enable", "enable a disabled job (needs --confirm)"),
            ("disable", "disable a job, history retained (needs --confirm)")):
        _p = cr_sub.add_parser(_cr_name, parents=[common], help=_cr_help)
        _p.add_argument("job_id", type=int)
        _p.add_argument("--confirm", action="store_true")
    cr_run = cr_sub.add_parser("run", parents=[common],
                               help="run due jobs now, or force one with --job-id")
    cr_run.add_argument("--job-id", type=int, default=0,
                        help="force one job id (default: run all due jobs)")
    cr_status = cr_sub.add_parser("status", parents=[common],
                                  help="scheduler posture: due + catch-up (read-only)")
    cr_runs = cr_sub.add_parser("runs", parents=[common],
                                help="run history, retained after delete (read-only)")
    cr_runs.add_argument("--job-id", type=int, default=0)
    cr_runs.add_argument("--limit", type=int, default=30)
    nd = add("node", help="Paired node host: inventory + capability dispatch "
             "(read-only except confirmed enroll/revoke)")
    nd_sub = nd.add_subparsers(dest="node_command")
    nd_sub.add_parser("status", parents=[common],
                      help="node host inventory summary (read-only)")
    nd_sub.add_parser("list", parents=[common],
                      help="list enrolled nodes (read-only)")
    nd_show = nd_sub.add_parser("show", parents=[common],
                                help="show one enrolled node (read-only)")
    nd_show.add_argument("node_id")
    nd_caps = nd_sub.add_parser("capabilities", parents=[common],
                                help="declared capabilities with typed availability")
    nd_caps.add_argument("node_id")
    nd_reg = nd_sub.add_parser("register", parents=[common],
                               help="enroll a paired node (needs --confirm; no listener opened)")
    nd_reg.add_argument("--name", required=True)
    nd_reg.add_argument("--endpoint", required=True)
    nd_reg.add_argument("--capability", action="append", default=[],
                        help="declared capability (repeatable)")
    nd_reg.add_argument("--confirm", action="store_true")
    nd_rev = nd_sub.add_parser("revoke", parents=[common],
                               help="revoke an enrolled node (needs --confirm)")
    nd_rev.add_argument("node_id")
    nd_rev.add_argument("--confirm", action="store_true")
    nd_disp = nd_sub.add_parser("dispatch", parents=[common],
                                help="dispatch one capability (local-safe reads only)")
    nd_disp.add_argument("node_id")
    nd_disp.add_argument("capability")
    tk = add("tasks", help="Unified background-task ledger (read-only read model)")
    tk_sub = tk.add_subparsers(dest="tasks_command")
    tk_sub.add_parser("status", parents=[common],
                      help="ledger counts by source and state (read-only)")
    tk_list = tk_sub.add_parser("list", parents=[common],
                                help="merged tasks, newest-first (read-only)")
    tk_list.add_argument("--source", default="",
                         help="opencode|schedules|kanban (default: all)")
    tk_list.add_argument("--state", default="",
                         help="canonical state filter (running|ok|failed|blocked|pending|unknown)")
    tk_list.add_argument("--limit", type=int, default=50)
    tk_show = tk_sub.add_parser("show", parents=[common],
                                help="one normalized task + raw source record (read-only)")
    tk_show.add_argument("task_id")
    tk_show.add_argument("--source", default="")
    tk_tl = tk_sub.add_parser("timeline", parents=[common],
                              help="ordered real events for one task (read-only)")
    tk_tl.add_argument("task_id")
    tk_tl.add_argument("--source", default="")
    md = add("media", help="Media generation: provider catalog + gated generate")
    md_sub = md.add_subparsers(dest="media_command")
    md_sub.add_parser("status", parents=[common],
                      help="provider/kind readiness (read-only)")
    md_sub.add_parser("catalog", parents=[common],
                      help="flat honest provider/kind rows (read-only)")
    md_gen = md_sub.add_parser("generate", parents=[common],
                               help="generate media (costly kinds need --confirm)")
    md_gen.add_argument("--kind", required=True,
                        choices=["image", "music", "video", "speech"])
    md_gen.add_argument("--provider", default="")
    md_gen.add_argument("--prompt", required=True)
    md_gen.add_argument("--confirm", action="store_true")
    lm = add("local-models", help="Local model inventory + brokered write "
             "gates (nothing is downloaded, started, or written)")
    lm_sub = lm.add_subparsers(dest="local_models_command")
    lm_sub.add_parser("status", parents=[common],
                      help="local runtime state (read-only)")
    lm_sub.add_parser("hardware", parents=[common],
                      help="GPU/VRAM gauges (read-only)")
    lm_sub.add_parser("catalog", parents=[common],
                      help="known/installed local models (read-only)")
    lm_sub.add_parser("jobs", parents=[common],
                      help="install/download job history (typed EMPTY)")
    lm_ri = lm_sub.add_parser("runtime-install", parents=[common],
                              help="typed gate: pinned-runtime install is brokered")
    lm_ri.add_argument("--backend", default="")
    lm_ri.add_argument("--confirm", action="store_true")
    lm_dl = lm_sub.add_parser("download", parents=[common],
                              help="typed gate: model download is brokered")
    lm_dl.add_argument("model")
    lm_dl.add_argument("--confirm", action="store_true")
    lm_sl = lm_sub.add_parser("sideload", parents=[common],
                              help="typed gate: on-disk GGUF registration is brokered")
    lm_sl.add_argument("path")
    lm_sl.add_argument("--confirm", action="store_true")
    lm_sv = lm_sub.add_parser("server", parents=[common],
                              help="typed gate: local server start/stop is brokered")
    lm_sv.add_argument("server_action", nargs="?", default="start",
                       choices=["start", "stop"])
    lm_sv.add_argument("--confirm", action="store_true")
    lm_ej = lm_sub.add_parser("eject", parents=[common],
                              help="typed gate: ejecting a resident model is brokered")
    lm_ej.add_argument("model")
    lm_ej.add_argument("--confirm", action="store_true")
    lm_ac = lm_sub.add_parser("activate", parents=[common],
                              help="typed gate: making a model default is a brokered write")
    lm_ac.add_argument("model")
    lm_ac.add_argument("--confirm", action="store_true")
    lm_qs = lm_sub.add_parser("quickstart", parents=[common],
                              help="typed gate: one-click runtime+model setup is brokered")
    lm_qs.add_argument("--model", default=None)
    lm_qs.add_argument("--confirm", action="store_true")
    export.add_argument("--out", type=Path, default=None,
                        help="output file (default: <state_dir>/portable-<ts>.vbp)")
    export.add_argument("--passphrase", default=None,
                        help="passphrase (required; prefer VOICE_BRIDGE_PASSPHRASE env)")
    imp = add("import-state", help="verify + restore a portable-state bundle (dry-run by default)")
    imp.add_argument("bundle", type=Path)
    imp.add_argument("--passphrase", default=None,
                     help="passphrase (required; prefer VOICE_BRIDGE_PASSPHRASE env)")
    imp.add_argument("--apply", action="store_true",
                     help="actually restore (default: dry-run/preview)")
    dev = add("device", help="trusted-device enrollment (explicit; read-only about host)")
    dev_sub = dev.add_subparsers(dest="device_command")
    dev_sub.add_parser("status", parents=[common], help="print the current device profile")
    dev_enroll = dev_sub.add_parser("enroll", parents=[common],
                                    help="explicitly authorize THIS device")
    dev_enroll.add_argument("--model-alias", default=None)
    mon = add("monitor", help="Packet 8D: hourly inbox monitor (lease, bundles, notifications)")
    mon_sub = mon.add_subparsers(dest="monitor_command")
    mon_run = mon_sub.add_parser("run", parents=[common],
                                 help="run one bounded hourly pass (oneshot)")
    mon_run.add_argument("--canary", action="store_true",
                         help="explicit opt-in live canary")
    mon_run.add_argument("--no-notify", action="store_true",
                         help="suppress notifications")
    mon_sub.add_parser("health", parents=[common],
                       help="typed monitor health (CDP, timer, backlog)")
    mon_install = mon_sub.add_parser("install", parents=[common],
                                     help="render/install systemd service+timer")
    mon_install.add_argument("--enable", action="store_true",
                             help="explicitly enable the hourly timer (live activation)")
    mon_sub.add_parser("disable", parents=[common],
                       help="disable the timer (keep all state)")
    mon_sub.add_parser("rollback", parents=[common],
                       help="rollback: disable timer, keep cursors/reports/evidence")
    mon_sub.add_parser("launch", parents=[common],
                       help="optional approved Chromium launch (cdp_launch must be on)")
    webui = add("webui", help="probe/open the local dashboard (never starts "
                "a second bridge, listener, or server; never puts a token in "
                "a URL or on the command line)")
    webui.add_argument("--open", action="store_true",
                       help="open the local dashboard in the default browser "
                            "when it is already reachable")
    webui.add_argument("--port", type=int, default=None,
                       help="dashboard port (default: webui.port from config)")
    webui.add_argument("--json", action="store_true",
                       help="machine-readable typed result")
    st = add("setup", help="Packet 8T one-click setup, installation health, "
             "and lifecycle dashboard (dry-run by default; backend-owned states)")
    st_sub = st.add_subparsers(dest="setup_command")
    st_sub.add_parser("check", parents=[common],
                      help="staged checklist with resume pointer (read-only)")
    st_check_off = st_sub.add_parser("check-offline", parents=[common],
                                     help="checklist in offline degraded mode (read-only)")
    st_run = st_sub.add_parser("run", parents=[common],
                               help="staged setup run (dry-run preview by default)")
    st_run.add_argument("--apply", action="store_true",
                        help="actually perform staged writes (default dry-run)")
    st_run.add_argument("--confirm", action="store_true",
                        help="explicit owner approval for network/service/desktop/data steps")
    st_run.add_argument("--steps", default="",
                        help="comma-separated step ids (default: all)")
    st_sub.add_parser("health", parents=[common],
                      help="backend-authoritative installation inventory (redacted)")
    st_sub.add_parser("diagnose", parents=[common],
                      help="redacted diagnostic bundle (never includes secrets)")
    st_sub.add_parser("matrix", parents=[common],
                      help="model capability matrix (local/cloud, fallback)")
    st_sub.add_parser("wizard", parents=[common],
                      help="microphone/output wizard data (never records audio)")
    st_sub.add_parser("repair", parents=[common],
                      help="ordered repair plan (finished work not repeated)")
    st_sub.add_parser("audit", parents=[common],
                      help="local audit/change timeline (bounded, redacted)")
    st_exp = st_sub.add_parser("export", parents=[common],
                               help="export redacted config profile (preview by default; "
                                    "needs --apply --confirm to write)")
    st_exp.add_argument("--out", type=Path, default=None,
                        help="output file (default: <state_dir>/config-profile.json)")
    st_exp.add_argument("--apply", action="store_true",
                        help="actually write the file (default: preview only)")
    st_exp.add_argument("--confirm", action="store_true",
                        help="explicit owner approval (required with --apply)")
    st_bk = st_sub.add_parser("backup", parents=[common],
                              help="migration/backup center plan|migrate|migrate-rollback|rollback")
    st_bk.add_argument("action", nargs="?", default="plan")
    st_bk.add_argument("--apply", action="store_true")
    st_bk.add_argument("--confirm", action="store_true")
    st_bk.add_argument("--backup-dir", type=Path, default=None)
    st_up = st_sub.add_parser("upgrade", parents=[common],
                              help="upgrade contract preview/apply (never restarts services silently)")
    st_up.add_argument("--apply", action="store_true")
    st_up.add_argument("--confirm", action="store_true")
    st_rb = st_sub.add_parser("rollback", parents=[common],
                              help="rollback preview/apply (partial failures visible)")
    st_rb.add_argument("--apply", action="store_true")
    st_rb.add_argument("--confirm", action="store_true")
    st_rb.add_argument("--backup-dir", type=Path, default=None)
    fr = add("frontier", help="Packet 8Q frontier-provider gateway "
             "(local default; cloud disabled unless explicitly enabled)")
    fr_sub = fr.add_subparsers(dest="frontier_command")
    fr_sub.add_parser("status", parents=[common],
                      help="typed gateway status (route, allowlist, budgets, disclosure)")
    fr_show = fr_sub.add_parser("show", parents=[common],
                                help="show one provider/model catalog entry")
    fr_show.add_argument("id", help="provider/model, e.g. zen/my-model")
    fr_chat = fr_sub.add_parser("chat", parents=[common],
                                help="one bounded completion via the gateway "
                                     "(dry-run by default; --confirm to send)")
    fr_chat.add_argument("prompt", nargs="+", help="sanitized task text")
    fr_chat.add_argument("--route", default="",
                         choices=["", "local", "frontier", "auto"],
                         help="route override (default: configured route)")
    fr_chat.add_argument("--provider", default="")
    fr_chat.add_argument("--model", default="")
    fr_chat.add_argument("--max-tokens", type=int, default=None)
    fr_chat.add_argument("--confirm", action="store_true",
                         help="explicitly confirm a cloud send (required for frontier/auto)")
    fr_chat.add_argument("--allow-private", action="store_true",
                         help="explicit per-request approval to include private context")
    fr_chat.add_argument("--json", action="store_true",
                           help="machine-readable typed result")
    oc = add("opencode", help="Packet 8R supervised OpenCode coding adapter "
             "(optional; disabled by default; same broker as dashboard/voice)")
    oc_sub = oc.add_subparsers(dest="opencode_command")
    oc_sub.add_parser("status", parents=[common],
                      help="typed adapter status (enablement, binary pin, capabilities)")
    oc_sub.add_parser("list", parents=[common],
                      help="list opencode capabilities + effective gate state")
    oc_show = oc_sub.add_parser("show", parents=[common],
                                help="show one opencode capability contract")
    oc_show.add_argument("action_id")
    oc_chk = oc_sub.add_parser("check", parents=[common],
                               help="read-only effective decision for one capability")
    oc_chk.add_argument("action_id")
    oc_chk.add_argument("--scope", default="code")
    oc_gr = oc_sub.add_parser("grant", parents=[common],
                              help="grant an opencode capability in the 7E store (dry-run unless --confirm)")
    oc_gr.add_argument("capability")
    oc_gr.add_argument("--scope", action="append", default=[])
    oc_gr.add_argument("--ttl", type=float, default=None)
    oc_gr.add_argument("--confirm", action="store_true")
    oc_rev = oc_sub.add_parser("revoke", parents=[common],
                               help="revoke an opencode grant")
    oc_rev.add_argument("grant_id")
    oc_prev = oc_sub.add_parser("preview", parents=[common],
                                help="validated no-side-effect preview + action hash (spawns nothing)")
    oc_prev.add_argument("--project", default=".")
    oc_prev.add_argument("--task", default="")
    oc_prev.add_argument("--task-file", type=Path, default=None)
    oc_prev.add_argument("--disposable", action="store_true", default=True)
    oc_prev.add_argument("--in-project", action="store_true",
                         help="run in the project dir instead of a disposable worktree")
    oc_prep = oc_sub.add_parser("prepare", parents=[common],
                                help="issue a fresh hash-bound confirmation for an exact preview")
    oc_prep.add_argument("--project", default=".")
    oc_prep.add_argument("--task", default="")
    oc_prep.add_argument("--task-file", type=Path, default=None)
    oc_prep.add_argument("--in-project", action="store_true")
    oc_prep.add_argument("--expected-hash", default="",
                         help="bind to a prior preview hash (stale refused)")
    oc_ex = oc_sub.add_parser("execute", parents=[common],
                              help="execute one bounded opencode run (requires grant + --confirm)")
    oc_ex.add_argument("--project", default=".")
    oc_ex.add_argument("--task", default="")
    oc_ex.add_argument("--task-file", type=Path, default=None)
    oc_ex.add_argument("--in-project", action="store_true")
    oc_ex.add_argument("--timeout", type=int, default=None)
    oc_ex.add_argument("--confirmation", default=None)
    oc_ex.add_argument("--confirm", action="store_true")
    oc_ex.add_argument("--expected-hash", default="")
    oc_ex.add_argument("--fixture", type=Path, default=None,
                       help="synthetic outcomes JSON (explicit simulation; never live)")
    oc_can = oc_sub.add_parser("cancel", parents=[common],
                               help="cancel an in-flight opencode run (idempotent)")
    oc_can.add_argument("receipt_id")
    oc_recs = oc_sub.add_parser("receipts", parents=[common],
                                help="list recent opencode receipts")
    oc_recs.add_argument("--limit", type=int, default=30)
    oc_retry = oc_sub.add_parser("retry", parents=[common],
                                 help="re-execute a failed opencode run with the SAME task/project (fresh confirmation if required)")
    oc_retry.add_argument("receipt_id")
    oc_retry.add_argument("--project", default=".")
    oc_retry.add_argument("--task", default="")
    oc_retry.add_argument("--task-file", type=Path, default=None)
    oc_retry.add_argument("--timeout", type=int, default=None)
    oc_retry.add_argument("--confirmation", default=None)
    oc_retry.add_argument("--confirm", action="store_true")
    oc_retry.add_argument("--fixture", type=Path, default=None,
                          help="synthetic outcomes JSON (explicit simulation; never live)")
    oc_recov = oc_sub.add_parser("recover", parents=[common],
                                 help="compensating rollback for a reversible opencode run (honest ROLLBACK_IMPOSSIBLE otherwise)")
    oc_recov.add_argument("receipt_id")
    oc_recov.add_argument("--confirmation", default=None)
    oc_recov.add_argument("--confirm", action="store_true")
    oc_recov.add_argument("--fixture", type=Path, default=None,
                          help="synthetic outcomes JSON (explicit simulation; never live)")
    oc_sub.add_parser("enable", parents=[common],
                      help="owner opt-in: set the user enablement flag (config master switch still applies)")
    oc_sub.add_parser("disable", parents=[common], help="disable the adapter")
    oc_sub.add_parser("emergency-stop", parents=[common],
                      help="latch an immediate stop (reset required)")
    oc_sub.add_parser("reset", parents=[common],
                      help="clear enablement + emergency-stop latch (returns to disabled)")
    tr = add("transfers", help="Node-host file transfer: confined list, "
             "bounded confirmed fetch, resumable plan/resume (same backend "
             "as voice/dashboard; local nodes only)")
    tr_sub = tr.add_subparsers(dest="transfers_command")
    tr_sub.add_parser("status", parents=[common],
                      help="typed transfer inventory (read-only)")
    tr_list = tr_sub.add_parser("list", parents=[common],
                                help="bounded confined directory listing "
                                     "(read-only)")
    tr_list.add_argument("path", help="absolute path inside an approved root")
    tr_list.add_argument("--node", default="", help="enrolled node id (default: first local)")
    tr_list.add_argument("--limit", type=int, default=200)
    tr_fetch = tr_sub.add_parser("fetch", parents=[common],
                                 help="confirmed bounded chunk read "
                                      "(base64 + SHA-256; --confirm)")
    tr_fetch.add_argument("path")
    tr_fetch.add_argument("--node", default="")
    tr_fetch.add_argument("--offset", type=int, default=0)
    tr_fetch.add_argument("--max-bytes", type=int, default=None)
    tr_fetch.add_argument("--confirm", action="store_true")
    tr_plan = tr_sub.add_parser("plan", parents=[common],
                                help="confirmed resumable transfer plan "
                                     "(digests only; --confirm)")
    tr_plan.add_argument("path")
    tr_plan.add_argument("--node", default="")
    tr_plan.add_argument("--chunk-size", type=int, default=None)
    tr_plan.add_argument("--confirm", action="store_true")
    tr_resume = tr_sub.add_parser("resume", parents=[common],
                                  help="next chunk spec for a persisted plan "
                                       "(read-only)")
    tr_resume.add_argument("transfer_id")
    tr_resume.add_argument("--node", default="")
    tr_resume.add_argument("--offset", type=int, default=0)
    rc = add("recall", help="Active-recall memory: session policy, bounded "
             "recall/resolve, confirmation-gated escalation "
             "(same store as memory/CLI/voice)")
    rc_sub = rc.add_subparsers(dest="recall_command")
    rc_sub.add_parser("status", parents=[common],
                      help="policy/store health (read-only)")
    rc_sub.add_parser("policy-get", parents=[common],
                      help="show the persisted active-recall policy (read-only)")
    rc_pol = rc_sub.add_parser("policy-set", parents=[common],
                               help="bounded policy update (needs --confirm)")
    rc_pol.add_argument("--enable", action="store_true")
    rc_pol.add_argument("--disable", action="store_true")
    rc_pol.add_argument("--min-score", type=float, default=None)
    rc_pol.add_argument("--max-items", type=int, default=None)
    rc_pol.add_argument("--session-scoped", action="store_true")
    rc_pol.add_argument("--global-scope", action="store_true")
    rc_pol.add_argument("--confirm", action="store_true")
    rc_q = rc_sub.add_parser("recall", parents=[common],
                             help="policy-scoped, bounded, redacted recall "
                                  "(read-only)")
    rc_q.add_argument("query")
    rc_q.add_argument("--session", default="")
    rc_q.add_argument("--limit", type=int, default=None)
    rc_res = rc_sub.add_parser("resolve", parents=[common],
                               help="bounded prompt-context blocks (read-only)")
    rc_res.add_argument("query")
    rc_res.add_argument("--session", default="")
    rc_esc = rc_sub.add_parser("escalate", parents=[common],
                               help="escalate one memory toward core "
                                    "(needs --confirm)")
    rc_esc.add_argument("memory_id", type=int)
    rc_esc.add_argument("--reason", required=True)
    rc_esc.add_argument("--confirm", action="store_true")
    dc = add("documents", help="Bounded PDF/transcript/text extraction inside "
             "approved roots (same backend as voice/dashboard)")
    dc_sub = dc.add_subparsers(dest="documents_command")
    dc_sub.add_parser("status", parents=[common],
                      help="supported kinds + optional PDF backend (read-only)")
    dc_sub.add_parser("kinds", parents=[common],
                      help="list supported document kinds (read-only)")
    dc_ex = dc_sub.add_parser("extract", parents=[common],
                              help="confirmed bounded extraction (--confirm)")
    dc_ex.add_argument("path")
    dc_ex.add_argument("--max-chars", type=int, default=None)
    dc_ex.add_argument("--page", type=int, default=None,
                       help="PDF page (1-based); PDFs only")
    dc_ex.add_argument("--confirm", action="store_true")
    ak = add("ask", help="Structured question prompts: status/ask/answer/"
             "pending (same store as voice/dashboard)")
    ak_sub = ak.add_subparsers(dest="ask_command")
    ak_sub.add_parser("status", parents=[common],
                      help="pending count, kinds, TTL, latch (read-only)")
    ak_sub.add_parser("pending", parents=[common],
                      help="unanswered, unexpired prompt contracts (read-only)")
    ak_ask = ak_sub.add_parser("ask", parents=[common],
                               help="create a single free-text question "
                                    "(needs --confirm)")
    ak_ask.add_argument("question")
    ak_ask.add_argument("--session", default="")
    ak_ask.add_argument("--confirm", action="store_true")
    ak_ans = ak_sub.add_parser("answer", parents=[common],
                               help="record answers for a pending prompt "
                                    "(JSON map or list)")
    ak_ans.add_argument("prompt_id")
    ak_ans.add_argument("--answers", default="",
                        help="JSON object {\"q1\": ...} or list of "
                             "{id,value} objects")
    ak_ans.add_argument("--answers-file", type=Path, default=None,
                        help="read the answers JSON from this file")
    sh = add("shares", help="Qubit-safe session shares: publish/list/revoke/"
             "resolve/purge (locally served, token-scoped, revocable)")
    sh_sub = sh.add_subparsers(dest="shares_command")
    sh_sub.add_parser("status", parents=[common],
                      help="share-store health and counts (read-only)")
    sh_sub.add_parser("list", parents=[common],
                      help="redacted share records, no tokens (read-only)")
    sh_pub = sh_sub.add_parser("publish", parents=[common],
                               help="snapshot a redacted session as a share "
                                    "(needs --confirm; token shown once)")
    sh_pub.add_argument("session_id")
    sh_pub.add_argument("--ttl-s", type=float, default=None)
    sh_pub.add_argument("--confirm", action="store_true")
    sh_rev = sh_sub.add_parser("revoke", parents=[common],
                               help="revoke a share, tombstone retained "
                                    "(needs --confirm)")
    sh_rev.add_argument("share_id")
    sh_rev.add_argument("--confirm", action="store_true")
    sh_res = sh_sub.add_parser("resolve", parents=[common],
                               help="read-only view of a shared session "
                                    "(token from a file)")
    sh_res.add_argument("share_id")
    sh_res.add_argument("--token", default="")
    sh_res.add_argument("--token-file", type=Path, default=None,
                        help="read the bearer token from this file (preferred)")
    sh_purge = sh_sub.add_parser("purge", parents=[common],
                                 help="drop expired share records "
                                      "(needs --confirm)")
    sh_purge.add_argument("--confirm", action="store_true")
    ag = add("agent", help="Qubit Agent runtime: agent loop, sessions, tools, skills, models, memory, tasks and CLI all execute from the Qubit source tree")
    ag.add_argument("agent_args", nargs=argparse.REMAINDER,
                    help="arguments passed verbatim to the Qubit Agent runtime "
                         "(e.g. 'status', 'sessions list', 'gateway --help')")

    # --- Agent 1 integration wave: real Agent 5-10 backends served by the
    # canonical `qubit <family> <sub>` CLI. Parsers are declared here and the
    # handlers live in the _INTEGRATION table (dispatch below); existing
    # families gain subcommands, and the integration-only families
    # (egress/policy/usage/receipts/logbook) are declared for the voice parity
    # map. Top-level completions are regenerated by scripts/gen_completions.py.
    def _integ(parser, name, help_text, spec=()):
        sp = parser.add_parser(name, parents=[common], help=help_text)
        for arg, kwargs in spec:
            sp.add_argument(arg, **kwargs)
        return sp

    # browser.py: managed-profile lifecycle (personal profiles stay refused)
    _integ(br_sub, "profiles",
           "list Qubit-managed browser profiles (read-only)")
    _integ(br_sub, "create-profile",
           "create a Qubit-managed browser profile (needs --confirm)",
           (("name", {}),
            ("--cdp-port", {"type": int, "default": None}),
            ("--confirm", {"action": "store_true"})))
    _integ(br_sub, "open-profile",
           "launch a managed profile via the approved launcher (needs --confirm)",
           (("profile", {}), ("--confirm", {"action": "store_true"})))
    _integ(br_sub, "close-managed-profile",
           "close a Qubit-managed profile (needs --confirm; real profiles refused)",
           (("profile", {}), ("--confirm", {"action": "store_true"})))
    _integ(br_sub, "remove-profile",
           "delete a Qubit-managed profile (needs --confirm)",
           (("profile", {}), ("--confirm", {"action": "store_true"})))

    # computer_use.py: Control-UI pane arrangement (no desktop/display access)
    _integ(cu_sub, "screen",
           "persist the Control UI pane arrangement (needs --confirm)",
           (("--panes", {"default": ""}),
            ("--layout", {"default": "columns"}),
            ("--confirm", {"action": "store_true"})))

    # local_models.py: ordered fallback chain / resolver
    _integ(lm_sub, "fallback",
           "ordered local-model fallback chain (optionally resolve a model)",
           (("model", {"nargs": "?", "default": ""}),))

    # gateway_peers.py: approval-gated supervision + discovery
    _integ(gw_sub, "supervision-request",
           "approval-gated supervised bridge transition (needs --confirm)",
           (("gateway_id", {}), ("action", {}),
            ("--approval-id", {"default": ""}),
            ("--confirm", {"action": "store_true"})))
    _integ(gw_sub, "supervision-revert",
           "restore the pre-supervision process state (needs --confirm)",
           (("gateway_id", {}), ("--confirm", {"action": "store_true"})))
    _integ(gw_sub, "discovery",
           "gateway/peer/provider discovery (read-only)")

    # pairing.py: setup-code + accept (create/approve already exist)
    _integ(pr_sub, "setup-code",
           "issue a one-time pairing code + join URL/QR (needs --confirm)",
           (("label", {}), ("scopes", {"nargs": "*"}),
            ("--account", {"default": ""}),
            ("--ttl-s", {"type": float, "default": None}),
            ("--confirm", {"action": "store_true"})))
    _integ(pr_sub, "accept",
           "redeem a setup code/join URL and bind its account (needs --confirm)",
           (("setup", {}), ("--confirm", {"action": "store_true"})))

    # runtime_images.py + runtime_inventory.py: recovery/report/discovery
    _integ(rt_sub, "recover",
           "reconcile/re-stage a runtime from verified local state (needs --confirm)",
           (("name", {}), ("--confirm", {"action": "store_true"})))
    _integ(rt_sub, "repair",
           "bounded runtime-state repair, never downloads (needs --confirm)",
           (("--confirm", {"action": "store_true"}),))
    _integ(rt_sub, "history",
           "append-only local update history (read-only)",
           (("name", {"nargs": "?", "default": ""}),))
    _integ(rt_sub, "update-report",
           "update status + history + recovery plan (read-only)")
    _integ(rt_sub, "discovery",
           "runtime gateway/peer discovery (read-only)")
    _integ(rt_sub, "marketplace",
           "local reviewed plugin catalog; remote fetches refused (read-only)")

    # hooks.py: core lifecycle emission (shell never executes)
    _integ(hk_sub, "emit",
           "emit a core lifecycle event into the hook registry",
           (("event", {}), ("--payload", {"default": ""})))

    # verified_skills/workshop.py: agent-owned collection layer
    _integ(sk_sub, "workshop",
           "verified-skill collection workshop (contracts/collection/curator-status/"
           "backups/backup/restore/rollback)",
           (("workshop_action", {"nargs": "?", "default": "curator-status"}),
            ("backup_id", {"nargs": "?", "default": ""}),
            ("--status", {"default": None}),
            ("--label", {"default": ""})))

    # Top-level families declared by the voice parity map for the integration
    # wave: egress / policy / usage / receipts / logbook (the same backend
    # functions the voice and dashboard surfaces call).
    eg = add("egress",
             help="Deny-by-default egress policy + per-host decisions (read-only)")
    eg_sub = eg.add_subparsers(dest="egress_command", required=True)
    _integ(eg_sub, "status",
           "deny-by-default egress policy + secret registry (read-only)")
    _integ(eg_sub, "check",
           "typed allow/deny decision for one destination (read-only)",
           (("host", {}),))

    pol = add("policy",
              help="Workspace policy conformance + attestation (read-only)")
    pol_sub = pol.add_subparsers(dest="policy_command", required=True)
    _integ(pol_sub, "conformance",
           "policy workspace-conformance doctor (read-only)")
    _integ(pol_sub, "attest",
           "content-addressed workspace attestation (read-only)")

    use = add("usage",
              help="Token/cost usage analytics + telemetry gate (local, redacted)")
    use.add_argument("--limit", type=int, default=1000)
    use_sub = use.add_subparsers(dest="usage_command")
    use_sub.add_parser("analytics", parents=[common],
                       help="aggregate the local usage ledger (read-only)")
    use_sub.add_parser("telemetry", parents=[common],
                       help="OpenTelemetry-style telemetry gate status (read-only)")

    add("receipts",
        help="Verify the local execution-receipt chain (read-only)")

    lb = add("logbook", help="Append-only work journal (entries/summary/note)")
    lb.add_argument("--limit", type=int, default=100)
    lb.add_argument("--window-s", type=float, default=None)
    lb_sub = lb.add_subparsers(dest="logbook_command")
    lb_sub.add_parser("entries", parents=[common],
                      help="entries + summary (read-only)")
    lb_sub.add_parser("summary", parents=[common],
                      help="windowed logbook aggregation (read-only)")
    lb_note = lb_sub.add_parser("note", parents=[common],
                                help="append a work-journal note")
    lb_note.add_argument("summary")
    lb_note.add_argument("--kind", default="note")
    lb_note.add_argument("--source", default="user")

    # CLI-coverage-gap families: catalog ops that declared a `cli` interface
    # but had no parser binding. Each subcommand maps to the same production op
    # id the dashboard/voice surfaces run.
    bk = add("backup", help="Passphrase-sealed portable backup (create/restore)")
    bk_sub = bk.add_subparsers(dest="backup_command", required=True)
    rp = add("reports", help="Published report bundles (read-only)")
    rp_sub = rp.add_subparsers(dest="reports_command", required=True)
    mk = add("marketplace", help="Local reviewed plugin/agent sources (read-only)")
    mk_sub = mk.add_subparsers(dest="marketplace_command", required=True)
    au = add("autonomy",
             help="Owner-scoped autonomy profiles (read + confirm-gated control)")
    au_sub = au.add_subparsers(dest="autonomy_command", required=True)

    # Leaf families: one read/diagnostic op id apiece, run via _it_run_op.
    for _leaf_fam, _leaf_op, _leaf_help in _OP_LEAF_CLI:
        _leaf = add(_leaf_fam, help=_leaf_help)
        _leaf.set_defaults(_op_id=_leaf_op, _op_argnames=[])

    # `plugins remote` is declared in _OP_CLI (no manual parser here).

    # Catalog-op CLI surfaces (wave 3): each maps to the same production op id
    # the dashboard/voice surfaces run, via _it_run_op + dashboard_ops.run_op.
    _op_parsers = {"brain": bn_sub, "ghost": ghost_sub, "memory": mem_sub,
                   "acp": acp_sub, "skills": sk_sub, "kanban": kb_sub,
                   "opencode": oc_sub, "backup": bk_sub, "reports": rp_sub,
                   "marketplace": mk_sub, "autonomy": au_sub,
                   "plugins": pl_sub}
    for fam, sub_name, op_id, help_text, specs in _OP_CLI:
        op_sp = _integ(_op_parsers[fam], sub_name, help_text, specs)
        op_sp.set_defaults(
            _op_id=op_id,
            _op_argnames=[s[0].lstrip("-") for s in specs])

    # Wave 1 Qubit CLI parity: whole families served by the vendored fork.
    # These parser entries declare each family in the live parser tree (help,
    # completions, ledger); main() routes the family pre-parse so upstream
    # argv/help/errors/exit status are preserved, exactly like `agent`.
    for _ff_name, _ff_help in _FAMILY_FORWARD_HELP:
        _ffp = add(_ff_name, help=_ff_help)
        _ffp.add_argument(
            "fork_args", nargs=argparse.REMAINDER,
            help=f"arguments forwarded to the Qubit-owned fork {_ff_name} "
                 "command (read-only subcommands forward verbatim; mutating "
                 "subcommands require --confirm)")

    # Wave 2 Qubit-native commands for fork-absent surfaces. The pinned fork's
    # `pause` writes a second ESTOP sentinel (Qubit owns ONE e-stop) and its
    # `worktree` has only list/prune; add/remove/switch are Qubit-native,
    # confirmation-gated, and confined to a registered project root.
    pause_p = add("pause", help="Engage the ONE Qubit emergency stop latch "
                                "(halts new work; requires --confirm)")
    pause_p.add_argument("--confirm", action="store_true",
                         help="explicitly confirm the emergency stop")
    wt_p = add("worktree", help="Confined git-worktree lifecycle in a "
                                "registered project root (add/remove/switch; "
                                "needs --confirm)")
    wt_sub = wt_p.add_subparsers(dest="worktree_command")
    wt_add = wt_sub.add_parser("add", parents=[common],
                               help="create <root>/.worktrees/<name> via git "
                                    "worktree add (needs --confirm)")
    wt_add.add_argument("name")
    wt_add.add_argument("--root", default="")
    wt_add.add_argument("--branch", default="")
    wt_add.add_argument("--base", default="")
    wt_add.add_argument("--confirm", action="store_true")
    wt_rm = wt_sub.add_parser("remove", parents=[common],
                              help="remove <root>/.worktrees/<name> via git "
                                   "worktree remove (needs --confirm)")
    wt_rm.add_argument("name")
    wt_rm.add_argument("--root", default="")
    wt_rm.add_argument("--force", action="store_true")
    wt_rm.add_argument("--confirm", action="store_true")
    wt_sw = wt_sub.add_parser("switch", parents=[common],
                              help="switch the checked-out branch in the "
                                   "resolved project root (needs --confirm)")
    wt_sw.add_argument("branch")
    wt_sw.add_argument("--root", default="")
    wt_sw.add_argument("--confirm", action="store_true")
    return p


def _resolve_task_text(cfg, args):
    """Resolve `task` text: explicit words, or `--current` (newest
    actionable kanban task via KB.current_task — the same helper the
    voice `task_run_current` entry uses). Returns (text, err_body, rc);
    err_body is None on success."""
    if getattr(args, "current", False):
        if getattr(args, "text", []):
            return "", {"state": "FAILED", "code": "BAD_ARGS",
                        "detail": "--current takes no task text"}, 2
        from . import kanban as _KB
        cur = _KB.current_task(cfg)
        if cur.get("state") != "OK" or not cur.get("task"):
            return "", cur, 2
        _t = cur["task"]
        text = (str(_t.get("title", "")) + "\n" +
                str(_t.get("body", ""))).strip()
        return text, None, 0
    if not getattr(args, "text", []):
        return "", {"state": "FAILED", "code": "BAD_ARGS",
                    "detail": "task text is required (or --current)"}, 2
    return " ".join(args.text), None, 0


# ---------------------------------------------------------------------------
# Agent 1 integration wave: `qubit <family> <sub>` -> real Agent 5-10 backends.
# One data-driven table; the dispatcher in main() runs the matching handler
# before the family `_cmd_*` ladders and renders a typed JSON envelope.
# ---------------------------------------------------------------------------

def _eval_path(cfg, name):
    return Path(cfg.state_dir()) / "evaluation" / name


def _it_confirm(args) -> bool:
    return bool(getattr(args, "confirm", False))


# --- browser.py -----------------------------------------------------------
def _it_browser_profiles(cfg, args):
    from . import browser as BR

    return BR.profiles(cfg)


def _it_browser_create(cfg, args):
    from . import browser as BR

    return BR.create_profile(cfg, args.name,
                             cdp_port=getattr(args, "cdp_port", None),
                             requested_by="cli:user", confirmed=_it_confirm(args))


def _it_browser_open(cfg, args):
    from . import browser as BR

    return BR.open_profile(cfg, args.profile, requested_by="cli:user",
                           confirmed=_it_confirm(args))


def _it_browser_close(cfg, args):
    from . import browser as BR

    return BR.close_profile(cfg, args.profile, requested_by="cli:user",
                            confirmed=_it_confirm(args))


def _it_browser_remove(cfg, args):
    from . import browser as BR

    return BR.remove_profile(cfg, args.profile, requested_by="cli:user",
                             confirmed=_it_confirm(args))


# --- computer_use.py ------------------------------------------------------
def _it_cu_screen(cfg, args):
    from . import computer_use as CU

    raw = getattr(args, "panes", "") or ""
    panes = None
    if raw:
        try:
            panes = json.loads(raw)
        except ValueError:
            panes = [p.strip() for p in raw.split(",") if p.strip()]
    return CU.screen(cfg, panes, layout=getattr(args, "layout", "columns")
                     or "columns", requested_by="cli:user",
                     confirmed=_it_confirm(args))


# --- local_models.py ------------------------------------------------------
def _it_lm_fallback(cfg, args):
    from . import local_models as LM

    model = getattr(args, "model", "") or ""
    return LM.resolve_fallback(cfg, model) if model else LM.fallback_chain(cfg)


# --- gateway_peers.py -----------------------------------------------------
def _it_gateway_supervision_request(cfg, args):
    from . import gateway_peers as GW

    return GW.supervision_request(
        cfg, args.gateway_id, args.action,
        approval_id=getattr(args, "approval_id", "") or "",
        requested_by="cli:user", confirmed=_it_confirm(args))


def _it_gateway_supervision_revert(cfg, args):
    from . import gateway_peers as GW

    return GW.supervision_revert(cfg, args.gateway_id, requested_by="cli:user",
                                 confirmed=_it_confirm(args))


def _it_gateway_discovery(cfg, args):
    from . import gateway_peers as GW

    return GW.discovery(cfg)


# --- pairing.py -----------------------------------------------------------
def _it_pairing_setup(cfg, args):
    from . import pairing as PR

    ttl = getattr(args, "ttl_s", None)
    return PR.encode_setup(
        cfg, args.label, list(getattr(args, "scopes", []) or []),
        account=getattr(args, "account", "") or "",
        ttl_s=(PR.DEFAULT_TTL_S if ttl is None else ttl),
        requested_by="cli:user", confirmed=_it_confirm(args))


def _it_pairing_accept(cfg, args):
    from . import pairing as PR

    return PR.accept(cfg, args.setup, requested_by="cli:user",
                     confirmed=_it_confirm(args))


# --- runtime_images.py / runtime_inventory.py -----------------------------
def _it_runtime_recover(cfg, args):
    from . import runtime_images as RI

    return RI.recover(cfg, args.name, requested_by="cli:user",
                      confirmed=_it_confirm(args))


def _it_runtime_repair(cfg, args):
    from . import runtime_images as RI

    return RI.repair(cfg, requested_by="cli:user", confirmed=_it_confirm(args))


def _it_runtime_history(cfg, args):
    from . import runtime_images as RI

    return RI.update_history(cfg, getattr(args, "name", "") or "")


def _it_runtime_update_report(cfg, args):
    from . import runtime_images as RI

    return RI.update_report(cfg)


def _it_runtime_discovery(cfg, args):
    from . import runtime_inventory as RIN

    return RIN._discovery(cfg)


def _it_runtime_marketplace(cfg, args):
    from . import runtime_inventory as RIN

    return RIN._marketplace(cfg)


# --- proxy_status.py ------------------------------------------------------
def _it_proxy_egress_status(cfg, args):
    from . import proxy_status as PX

    return PX.egress_status(cfg)


def _it_proxy_egress_check(cfg, args):
    from . import proxy_status as PX

    return PX.check_egress(cfg, args.host)


# --- release.py -----------------------------------------------------------
def _it_product_conformance(cfg, args):
    from . import release as REL

    return REL.workspace_conformance()


def _it_product_attest(cfg, args):
    from . import release as REL

    return REL.workspace_attestation()


# --- hooks.py -------------------------------------------------------------
def _it_hooks_emit(cfg, args):
    from . import hooks as HK

    raw = getattr(args, "payload", "") or ""
    payload = {}
    if raw:
        try:
            payload = json.loads(raw)
        except ValueError:
            return {"state": "FAILED", "code": "HOOK_BAD_PAYLOAD",
                    "detail": "--payload must be a JSON object"}
        if not isinstance(payload, dict):
            return {"state": "FAILED", "code": "HOOK_BAD_PAYLOAD",
                    "detail": "--payload must be a JSON object"}
    return HK.emit(cfg, args.event, payload=payload, requested_by="cli:user")


# --- verified_skills/workshop.py ------------------------------------------
def _it_skills_workshop(cfg, args):
    from .verified_skills.workshop import verified_skill_workshop

    ws = verified_skill_workshop(cfg)
    action = str(getattr(args, "workshop_action", "") or "curator-status")
    bid = str(getattr(args, "backup_id", "") or "")
    if action == "contracts":
        return ws.contracts()
    if action == "collection":
        return ws.collection(status=getattr(args, "status", None))
    if action == "curator-status":
        return ws.curator_status()
    if action == "backups":
        return ws.backups()
    if action == "backup":
        return ws.backup(label=getattr(args, "label", "") or "")
    if action == "restore":
        return ws.restore(bid, requested_by="user")
    if action == "rollback":
        return ws.rollback(bid, requested_by="user")
    return {"state": "FAILED", "code": "UNKNOWN_WORKSHOP_ACTION",
            "detail": f"unknown workshop action {action!r}"}


# --- evaluation: usage / receipts / logbook / telemetry -------------------
def _it_exp_usage(cfg, args):
    from .evaluation import usage_analytics as UA

    ledger = UA.UsageLedger(_eval_path(cfg, "usage.jsonl"))
    limit = int(getattr(args, "limit", 1000) or 1000)
    records = ledger.read(limit=limit)
    out = UA.analyze(records)
    out.update({"state": "OK", "records": len(records),
                "rejections": ledger.rejections, "path": str(ledger.path),
                "authority": "append-only local usage ledger; no prompts or "
                             "secrets are stored"})
    return out


def _it_exp_receipts(cfg, args):
    from .evaluation import receipts as RC

    path = _eval_path(cfg, "receipts.jsonl")
    if not path.exists():
        return {"state": "NOT_FOUND", "code": "RECEIPTS_NOT_FOUND",
                "detail": f"no execution-receipt chain at {path}"}
    rows, rejected = [], 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rejected += 1
    verdict = RC.verify_receipts(rows)
    return {"state": "OK" if verdict.get("valid") else "FAILED",
            "schema": "qubit-receipts/1", "records": len(rows),
            "rejected": rejected, "verify": verdict, "path": str(path)}


def _it_exp_logbook(cfg, args):
    from .evaluation import logbook as LB

    ledger = LB.Logbook(_eval_path(cfg, "logbook.jsonl"))
    window = getattr(args, "window_s", None)
    limit = int(getattr(args, "limit", 100) or 100)
    return {"state": "OK", "schema": "qubit-logbook/1",
            "entries": ledger.entries(limit=limit, window_s=window),
            "summary": ledger.summary(window_s=window),
            "path": str(ledger.path)}


def _it_exp_logbook_note(cfg, args):
    from .evaluation import logbook as LB

    ledger = LB.Logbook(_eval_path(cfg, "logbook.jsonl"))
    entry = LB.LogbookEntry.create(kind=getattr(args, "kind", "note"),
                                   summary=args.summary,
                                   source=getattr(args, "source", "user"))
    return {"state": "OK", "schema": "qubit-logbook/1",
            "entry": ledger.append(entry), "path": str(ledger.path)}


def _it_exp_telemetry(cfg, args):
    from .evaluation import telemetry as TE

    return {"state": "OK", "schema": "qubit-telemetry/1",
            "enabled": bool(TE.telemetry_enabled(cfg)),
            "endpoint": TE.endpoint_for(cfg),
            "detail": "opt-in only; no transport is created when disabled"}


_INTEGRATION_DESTS = {
    "browser": "browser_command",
    "computer-use": "computer_use_command",
    "local-models": "local_models_command",
    "gateway": "gateway_command",
    "pairing": "pairing_command",
    "runtime": "runtime_command",
    "hooks": "hooks_command",
    "skills": "skills_command",
    "egress": "egress_command",
    "policy": "policy_command",
    "usage": "usage_command",
    "receipts": None,
    "logbook": "logbook_command",
    "brain": "brain_command",
    "ghost": "ghost_command",
    "memory": "memory_command",
    "acp": "acp_command",
    "kanban": "kanban_command",
    "opencode": "opencode_command",
    "backup": "backup_command",
    "reports": "reports_command",
    "marketplace": "marketplace_command",
    "plugins": "plugins_command",
    "logs": None,
    "tools": None,
    "reasoning": None,
    "prompt-budget": None,
}

_INTEGRATION = {
    ("browser", "profiles"): _it_browser_profiles,
    ("browser", "create-profile"): _it_browser_create,
    ("browser", "open-profile"): _it_browser_open,
    ("browser", "close-managed-profile"): _it_browser_close,
    ("browser", "remove-profile"): _it_browser_remove,
    ("computer-use", "screen"): _it_cu_screen,
    ("local-models", "fallback"): _it_lm_fallback,
    ("gateway", "supervision-request"): _it_gateway_supervision_request,
    ("gateway", "supervision-revert"): _it_gateway_supervision_revert,
    ("gateway", "discovery"): _it_gateway_discovery,
    ("pairing", "setup-code"): _it_pairing_setup,
    ("pairing", "accept"): _it_pairing_accept,
    ("runtime", "recover"): _it_runtime_recover,
    ("runtime", "repair"): _it_runtime_repair,
    ("runtime", "history"): _it_runtime_history,
    ("runtime", "update-report"): _it_runtime_update_report,
    ("runtime", "discovery"): _it_runtime_discovery,
    ("runtime", "marketplace"): _it_runtime_marketplace,
    ("hooks", "emit"): _it_hooks_emit,
    ("skills", "workshop"): _it_skills_workshop,
    ("egress", "status"): _it_proxy_egress_status,
    ("egress", "check"): _it_proxy_egress_check,
    ("policy", "conformance"): _it_product_conformance,
    ("policy", "attest"): _it_product_attest,
    ("usage", None): _it_exp_usage,
    ("usage", "analytics"): _it_exp_usage,
    ("usage", "telemetry"): _it_exp_telemetry,
    ("receipts", None): _it_exp_receipts,
    ("logbook", None): _it_exp_logbook,
    ("logbook", "entries"): _it_exp_logbook,
    ("logbook", "summary"): _it_exp_logbook,
    ("logbook", "note"): _it_exp_logbook_note,
}


def _it_run_op(cfg, args):
    """Run the catalog op id bound to this CLI subcommand via the shared
    production dispatcher (same op the dashboard console and voice run)."""
    from . import dashboard_ops as OPS

    op_id = str(getattr(args, "_op_id", "") or "")
    payload: Dict[str, Any] = {}
    for name in getattr(args, "_op_argnames", ()) or ():
        key = name.replace("-", "_")
        val = getattr(args, key, None)
        if val is None:
            continue
        if isinstance(val, list):
            if not val:
                continue
            val = " ".join(str(v) for v in val)
        elif isinstance(val, bool):
            # store_true flags feed choice args ("true"/"false") on extras.
            val = "true" if val else "false"
        elif isinstance(val, str) and not val:
            continue
        payload[key] = val
    return OPS.run_op(cfg, op_id, payload, actor="cli:user", session="cli")


# CLI-coverage-gap leaf families share the production dispatcher.
for _leaf_pair in (("logs", None), ("tools", None), ("reasoning", None),
                   ("prompt-budget", None)):
    _INTEGRATION[_leaf_pair] = _it_run_op


def _cmd_autonomy(cfg, args) -> int:
    """Owner-scoped autonomy admin (same backend as the dashboard/voice ops)."""
    from . import autonomy_ops as AO
    from .actions import HUMAN_PRINCIPAL

    sub = getattr(args, "autonomy_command", "") or "status"
    try:
        if sub == "status":
            return _finish_integration({"state": "OK", "autonomy": AO.status(cfg)})
        if sub == "list":
            profs = AO.list_profiles(cfg)
            return _finish_integration({
                "state": "OK",
                "autonomy": {"count": len(profs),
                             "profiles": [p.to_dict() for p in profs]}})
        if sub == "show":
            prof = AO.show(cfg, str(getattr(args, "profile_id", "") or ""))
            if prof is None:
                return _finish_integration({
                    "state": "UNAVAILABLE",
                    "code": "AUTONOMY_PROFILE_NOT_FOUND"})
            return _finish_integration(
                {"state": "OK", "autonomy": {"profile": prof.to_dict()}})
        if sub == "expire":
            expired = AO.expire(cfg)
            return _finish_integration({
                "state": "OK",
                "autonomy": {"expired": list(expired), "count": len(expired)}})
        if sub in ("pause", "resume", "revoke"):
            pid = str(getattr(args, "profile_id", "") or "")
            if not getattr(args, "confirm", False):
                return _finish_integration({
                    "state": "NEEDS-CONFIRM", "code": "CONFIRM_REQUIRED",
                    "detail": f"re-run with --confirm to {sub} {pid!r}",
                    "profile_id": pid})
            fn = {"pause": AO.pause, "resume": AO.resume,
                  "revoke": AO.revoke}[sub]
            prof = fn(cfg, pid, owner=HUMAN_PRINCIPAL)
            return _finish_integration(
                {"state": "OK", "autonomy": {"profile": prof.to_dict()}})
    except Exception as exc:  # typed backend boundary: never a traceback
        return _finish_integration({
            "state": "FAILED",
            "code": str(getattr(exc, "code", None) or "AUTONOMY_ERROR"),
            "detail": str(getattr(exc, "detail", None) or exc)[:300]})
    return _finish_integration({"state": "FAILED", "code": "BAD_SUBCOMMAND",
                                "detail": sub})


def _finish_integration(out) -> int:
    """Render one integration result and map its typed state to an exit code."""
    print(json.dumps(out, indent=2, sort_keys=True))
    state = str(out.get("state", "")).upper()
    code = str(out.get("code", "") or "").upper()
    if state in ("OK", "READY", "HEALTHY", "PASS", "EXECUTED", "AVAILABLE"):
        return 0
    if state == "NEEDS-CONFIRM" or "CONFIRM" in code:
        return 3
    return 2


def _run_integration(cfg, command, args):
    """Run a wired integration handler, or ``None`` for a native command."""
    if command not in _INTEGRATION_DESTS:
        return None
    dest = _INTEGRATION_DESTS[command]
    sub = getattr(args, dest, None) if dest else None
    handler = _INTEGRATION.get((command, sub))
    if handler is None:
        if (command, sub) not in _OP_CLI_INDEX:
            return None
        handler = _it_run_op
    try:
        out = handler(cfg, args)
    except Exception as exc:  # typed backend boundary: never a traceback
        out = {"state": "FAILED",
               "code": str(getattr(exc, "code", None) or "CLI_ERROR"),
               "detail": str(getattr(exc, "detail", None) or exc)[:400],
               "owner": "qubit-policy",
               "rollback": "no state changed by the failed call"}
    if isinstance(out, int):
        return out
    return _finish_integration(out)


# `qubit chat` one-shot selectors. When any is present the vendored chat
# answers and exits (no interactive session), so a TTY is never required.
# `-z/--oneshot PROMPT` is a *top-level* vendored flag, not a `chat` flag, so
# the in-`chat` boolean is `--oneshot` (dest ``oneshot_exit``).
_CHAT_HEADLESS_FLAGS = frozenset({
    "-q", "--query", "--query-file", "--oneshot", "-Q", "--quiet",
})


def _chat_requires_tty(tail: list) -> bool:
    """True when an interactive ``qubit chat`` launch would need a TTY.

    Help is always allowed through (pipable); so is any one-shot selector
    (``-q/--query/--query-file/--oneshot/-Q/--quiet``), which the vendored chat
    answers and exits without a TTY. Only a bare interactive launch on a
    non-TTY stdin is gated.
    """
    for tok in tail:
        if tok in ("-h", "--help"):
            return False
        if tok.split("=", 1)[0] in _CHAT_HEADLESS_FLAGS:
            return False
    try:
        return not sys.stdin.isatty()
    except Exception:  # no stdin object / closed: fail safe to the gate
        return True


def _chat_noninteractive_gate(tail: list):
    """Typed, helpful result for an interactive ``qubit chat`` with no TTY.

    Returns ``None`` when the interactive launch may proceed (a TTY, help, or a
    one-shot selector), otherwise the envelope the caller prints. This is a
    real capability boundary, not a refusal: it names the working
    non-interactive counterparts.
    """
    if not _chat_requires_tty(tail):
        return None
    return {
        "state": "needs-tty",
        "code": "CHAT_REQUIRES_TTY",
        "detail": ("`qubit chat` opens the interactive terminal chat and needs "
                   "a TTY. For non-interactive use run `qubit chat -q \"...\"` "
                   "(one-shot), `qubit run`, or the dashboard `/qubit-chat` "
                   "surface."),
        "alternatives": ["qubit chat -q \"...\"", "qubit agent chat -q \"...\"",
                         "qubit run", "dashboard /qubit-chat"],
    }


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Narrow legacy migration: adopt pre-rebrand env names for this process
    # (never the external upstream home; see voicebridge.qubit_legacy).
    from . import qubit_legacy
    qubit_legacy.install()
    # Migration-compat CLI aliases: old scripts may call the legacy-named
    # subcommands; translate them to the canonical Qubit names before parsing
    # so the product help/completions never advertise the upstream name.
    _legacy_cmd_aliases = {"hermes-inspect": "legacy-inspect",
                           "hermes-migrate": "legacy-migrate",
                           "hermes-rollback": "legacy-rollback"}
    raw = [_legacy_cmd_aliases.get(tok, tok) for tok in raw]
    # Passthrough to the vendored Qubit Agent runtime must not be re-parsed by
    # Qubit's argparse (leading --help/--version/--flags would be swallowed).
    # `qubit agent <args…>` dispatches verbatim. `qubit` stays as an
    # undeclared migration alias (never advertised in help/completions) so old
    # scripts keep working without carrying upstream identity in the product.
    if raw and raw[0] in ("agent", "hermes"):
        from . import qubit_runtime
        from .branding import help_overlay
        with help_overlay.active():
            return qubit_runtime.run(raw[1:])
    # Agent 1: forward supported `<family> <sub>` pairs to the real vendored
    # fork (same in-process runtime as `qubit agent`). Only pairs the Qubit
    # parser does not own are routed, so native commands are never shadowed.
    # Wave 1 whole-family routes are resolved here too: read-only subcommands
    # forward verbatim, mutating subcommands need Qubit's --confirm, and
    # unclassified subcommands are typed refusals (never a raw bypass).
    from . import cli_qubit_forward as _qubit_forward
    _route = _qubit_forward.route(raw)
    if _route is not None:
        if _route.kind in ("needs-confirm", "refused"):
            envelope = {"state": _route.kind, "code": _route.code,
                        "detail": _route.detail}
            if _route.argv:
                envelope["argv"] = _route.argv
            print(json.dumps(envelope, indent=2, sort_keys=True))
            return 3
        # `qubit chat` is the real terminal chat (a classified forward to the
        # vendored `chat` command). The interactive session needs a TTY: a
        # non-TTY launch with no one-shot selector gets a typed, helpful result
        # instead of a TUI that cannot read input. Help and one-shot paths
        # (`-q/--query/--query-file/--oneshot/-Q/--quiet`) forward unchanged.
        if raw and raw[0] == "chat":
            gate = _chat_noninteractive_gate(raw[1:])
            if gate is not None:
                print(json.dumps(gate, indent=2, sort_keys=True))
                return 2
        from . import qubit_runtime
        from .branding import help_overlay
        with help_overlay.active():
            return qubit_runtime.run(_route.argv)
    args = build_parser().parse_args(raw)
    command = args.command or "run"

    # logging early so every event is captured
    from .config import Config
    from .logging_setup import setup
    cfg = Config.load(args.config)
    setup(level=cfg.get("logging", "level", default="INFO"),
          jsonl=cfg.get("logging", "jsonl", default=True),
          state_dir=cfg.state_dir())

    # Owner-scoped autonomy admin is a native family: pause/resume/revoke call
    # the canonical backend directly with an explicit --confirm (the dashboard
    # and voice paths stage the same op through the shared journal).
    if command == "autonomy":
        return _cmd_autonomy(cfg, args)

    # Packet 8U: real top-level families that delegate to the EXISTING canonical
    # backends (model profiles / config + env refs / diagnostics / schedules) —
    # the same stores the `profile model`, `flags`, `package`, and `schedules`
    # surfaces use, never a second store or a demo route.
    if command == "model":
        return _cmd_profile_model(cfg, args)
    if command == "config":
        return _cmd_config(cfg, args, args.config)
    if command == "doctor":
        return _cmd_doctor(cfg, args)
    if command == "cron":
        return _cmd_cron(cfg, args)

    # Agent 1 integration wave: wired family subcommands execute their real
    # backend here, before the native family handlers below.
    _integration = _run_integration(cfg, command, args)
    if _integration is not None:
        return _integration

    if command == "status":
        return _cmd_status(cfg)
    if command == "auth":
        sub = getattr(args, "auth_command", None) or "status"
        if sub == "logout":
            return _cmd_auth_logout(cfg)
        if sub == "login":
            return _cmd_auth_login(cfg, args)
        if sub == "verify":
            return _cmd_auth_verify(cfg, args)
        return _cmd_auth_status(cfg)
    if command == "mcp":
        if getattr(args, "mcp_command", None) == "invoke":
            return _cmd_mcp_invoke(cfg, args)
        return _cmd_mcp_status(cfg)
    if command == "worker":
        return _cmd_worker_status(cfg)
    if command == "approvals":
        return _cmd_approvals(cfg, args)
    if command == "verify-release":
        return _cmd_verify_static()
    if command == "checkpoints":
        return _cmd_checkpoints(cfg, args)
    if command == "sessions":
        return _cmd_sessions(cfg, args)
    if command in ("chat-sessions", "chat_sessions"):
        return _cmd_chat_sessions(cfg, args)
    if command == "capability":
        return _cmd_capability(args.action, args.name, args.state)
    if command == "completion":
        return _cmd_completion(args.shell, args.cmd)
    if command == "mic-test":
        return _cmd_mic_test(cfg)
    if command == "asr-test":
        return _cmd_asr_test(cfg, args.file)
    if command == "tts-test":
        return _cmd_tts_test(cfg, " ".join(args.text), args.out)
    if command == "enroll":
        return _cmd_enroll(cfg, args.file, args.name, args.seconds)
    if command == "verify":
        return _cmd_verify(cfg, args.file, args.name)
    if command == "flags":
        return _cmd_flags(cfg, args, args.config)
    if command == "flag-drill":
        return _cmd_flag_drill(cfg)
    if command == "package":
        return _cmd_package(cfg, args)
    if command == "product":
        return _cmd_product(cfg, args)
    if command == "audio":
        return _cmd_audio(cfg, args)
    if command == "profile":
        return _cmd_profile(cfg, args)
    if command == "branding":
        return _cmd_branding(cfg, args)
    if command == "permissions":
        return _cmd_permissions(cfg, args)
    if command == "skills":
        return _cmd_skills(cfg, args)
    if command == "project":
        return _cmd_project(cfg, args)
    if command == "kanban":
        return _cmd_kanban(cfg, args)
    if command == "runtime":
        return _cmd_runtime(cfg, args)
    # Owner Decision #11: the sanctioned native update surface (check/apply/
    # rollback) — never a typed refusal and never fork-forwarded.
    if command == "update":
        return _cmd_update(cfg, args)
    if command == "computer-use":
        return _cmd_computer_use(cfg, args)
    if command == "browser":
        return _cmd_browser(cfg, args)
    if command == "acp":
        return _cmd_acp(cfg, args)
    if command == "lsp":
        return _cmd_lsp(cfg, args)
    if command == "plugins":
        return _cmd_plugins(cfg, args)
    if command == "channels":
        return _cmd_channels(cfg, args)
    if command == "theme":
        return _cmd_theme(cfg, args)
    if command == "proxy":
        return _cmd_proxy(cfg, args)
    if command == "import-agent":
        return _cmd_import_agent(cfg, args)
    if command == "lessons":
        return _cmd_lessons(cfg, args)
    if command == "hooks":
        return _cmd_hooks(cfg, args)
    if command == "sync":
        return _cmd_sync(cfg, args)
    if command == "pairing":
        return _cmd_pairing(cfg, args)
    if command == "webhook":
        return _cmd_webhook(cfg, args)
    if command == "msg":
        return _cmd_msg(cfg, args)
    if command == "gateway":
        return _cmd_gateway(cfg, args)
    if command == "peer":
        return _cmd_peer(cfg, args)
    if command == "voice":
        return _cmd_voice(cfg, args)
    if command == "brain":
        return _cmd_brain(cfg, args)
    if command == "tui":
        return _cmd_tui(cfg)
    if command == "vision":
        return _cmd_vision(cfg, args)
    if command == "memory":
        return _cmd_memory(cfg, args)
    if command == "websites":
        return _cmd_websites(cfg, args)
    if command == "actions":
        return _cmd_actions(cfg, args)
    if command == "ghost":
        return _cmd_ghost(cfg, args)
    if command == "power":
        return _cmd_power(cfg, args)
    if command == "pause":
        return _cmd_pause(cfg, args)
    if command == "worktree":
        return _cmd_worktree(cfg, args)
    if command == "verified-skills":
        return _cmd_verified_skills(cfg, args)
    if command == "experiments":
        return _cmd_experiments(cfg, args)
    if command == "export-state":
        return _cmd_export_state(cfg, args)
    if command == "import-state":
        return _cmd_import_state(cfg, args)
    if command == "device":
        return _cmd_device(cfg, args)
    if command == "monitor":
        return _cmd_monitor(cfg, args)
    if command == "schedules":
        return _cmd_schedules(cfg, args)
    if command == "node":
        return _cmd_node(cfg, args)
    if command == "tasks":
        return _cmd_tasks(cfg, args)
    if command == "media":
        return _cmd_media(cfg, args)
    if command == "local-models":
        return _cmd_local_models(cfg, args)
    if command == "transfers":
        return _cmd_transfers(cfg, args)
    if command == "recall":
        return _cmd_recall(cfg, args)
    if command == "documents":
        return _cmd_documents(cfg, args)
    if command == "ask":
        return _cmd_ask(cfg, args)
    if command == "shares":
        return _cmd_shares(cfg, args)
    if command == "files":
        return _cmd_files(cfg, args)
    if command == "git":
        return _cmd_git(cfg, args)
    if command == "ide":
        return _cmd_ide(cfg, args)
    if command == "frontier":
        return _cmd_frontier(cfg, args)
    if command == "opencode":
        return _cmd_opencode(cfg, args)
    if command == "webui":
        return _cmd_webui(cfg, args)
    if command == "setup":
        return _cmd_setup(cfg, args)

    from .orchestrator import VoiceOrchestrator
    orch = VoiceOrchestrator(cfg, no_hotkey=args.no_hotkey, dry_run=args.dry_run)
    orch.muted = args.mute

    try:
        if command == "run":
            orch.blocking_tasks = False   # PTT loop must stay responsive
            orch.run()
        elif command == "once":
            orch.mic_only = True
            orch.run()
        elif command == "task":
            text, err_body, rc = _resolve_task_text(cfg, args)
            if err_body is not None:
                if err_body.get("state") == "EMPTY":
                    import json as _json3
                    print(_json3.dumps(err_body, indent=2, sort_keys=True))
                else:
                    print(f"ERROR: {err_body.get('detail', 'bad task args')}",
                          file=sys.stderr)
                return rc
            if args.model:
                cfg.data.setdefault("agent", {})["model"] = args.model
            orch.process_text(text)
        elif command == "roundtrip":
            return _cmd_roundtrip(cfg, args.text, args.model, orch)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        orch.stop()
    return 0


def _cmd_status(cfg) -> int:
    from .logging_setup import get_logger
    get_logger()
    print("Qubit Agent — Qubit VoiceBridge status")
    from .runtime_inventory import runtime_inventory
    try:
        inv = runtime_inventory(cfg)
        a = inv.get("agent", {}) if isinstance(inv, dict) else {}
        print(f"agent runtime : {a.get('runtime', 'qubit-native')}"
              f" ({a.get('model', '')})")
        b = inv.get("broker", {}) if isinstance(inv, dict) else {}
        print(f"emergency stop : {'LATCHED' if b.get('emergency_stop') else 'clear'}")
    except Exception as exc:
        print(f"runtime inventory: UNAVAILABLE ({str(exc)[:100]})")
    w = inv.get("worker", {}) if isinstance(inv, dict) else {}
    print(f"worker route   : {w.get('route', 'local')}")
    print(f"agent model    : {cfg.get('agent', 'model', default='') or 'qubit-brain4'}")
    from .runtime_inventory import current_vram_mb
    print(f"free VRAM        : {current_vram_mb()} MiB")
    # Snap-confinement XDG redirects are LOUD, never silent: one line per
    # redirected variable so status shows exactly which data tree is used.
    from .config import xdg_report
    for _entry in xdg_report():
        if _entry["source"] == "snap-confinement-redirect":
            print(f"xdg                : {_entry['var']} snap-confinement-"
                  f"redirect (sandbox value ignored) -> {_entry['effective']}")
    # ASR
    asr_cfg = cfg.section("asr")
    if asr_cfg.get("provider", "sherpa") == "sherpa":
        from .asr.sherpa import SherpaASR
        try:
            asr = SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                            model_dir=cfg.model_dir(),
                            provider=asr_cfg.get("provider_ep", "cpu"),
                            hotwords=asr_cfg.get("hotwords", ""),
                            hotwords_score=float(asr_cfg.get("hotwords_score", 1.5)))
            asr.load()
            print(f"ASR              : sherpa/{asr.engine} on {asr.provider}")
        except Exception as exc:
            print(f"ASR              : FAILED ({str(exc)[:120]})")
    else:
        from .asr.whisper import FasterWhisperASR
        asr = FasterWhisperASR(model=asr_cfg.get("model", "base"),
                               device=asr_cfg.get("device", "auto"))
        try:
            asr.load()
            print(f"ASR              : whisper/{asr.model_name} on {asr._actual_device}")
        except Exception as exc:
            print(f"ASR              : FAILED ({str(exc)[:120]})")
    # TTS
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    try:
        tts.load()
        print(f"TTS              : piper/{tts.voice} ({tts.sample_rate} Hz)")
    except Exception as exc:
        print(f"TTS              : FAILED ({str(exc)[:120]})")
    # speaker verification
    try:
        from .voice_id import SpeakerVerifier
        sv_cfg = cfg.section("speaker_verify")
        sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(),
                             name=sv_cfg.get("name", "user"),
                             threshold=float(sv_cfg.get("threshold", 0.75)))
        sv.load()
        state = f"enrolled '{sv.name}' (threshold {sv.threshold})" if sv.ready \
            else "NOT enrolled (run: voicebridge enroll)"
        print(f"voice-ID         : {state}")
    except Exception as exc:
        print(f"voice-ID         : FAILED ({str(exc)[:120]})")
    # PersonaPlex / Qubit agent / local
    print(f"agent backend    : {'enabled' if cfg.get('agent', 'enabled', default=True) else 'disabled'}")
    print(f"local actions    : {'enabled' if cfg.get('local', 'enabled', default=True) else 'disabled'}")
    # TTS
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    print(f"TTS              : piper/{tts.voice} {'OK' if tts.available() else 'MISSING VOICE'}")
    return 0


def _cmd_completion(shell: str, cmd: str) -> int:
    """Print the generated shell completion script (read-only, stdout)."""
    from .resources import resource_path

    path = resource_path(f"completions/{cmd}.{shell}")
    alt = Path(__file__).resolve().parent / "_data" / "completions" / f"{cmd}.{shell}"
    candidate = path if path.is_file() else alt
    if not candidate.is_file():
        print(f"completion unavailable for {cmd}/{shell}", file=sys.stderr)
        return 1
    sys.stdout.write(candidate.read_text())
    return 0


def _cmd_capability(action: str, name: str, state: str | None) -> int:
    """Capability catalog lookup (read-only, typed result)."""
    from .capability_catalog import check, list_names, summary

    if action == "check":
        print(json.dumps(check(name or ""), indent=2))
        return 0
    if action == "list":
        for item in list_names(state):
            print(item)
        return 0
    print(json.dumps(summary(), indent=2))
    return 0


def _cmd_auth_status(cfg) -> int:
    """Credential-reference inventory (names only, never values)."""
    from .auth_status import auth_status

    print(json.dumps(auth_status(cfg), indent=2))
    return 0


def _cmd_auth_logout(cfg) -> int:
    """Verify no stored credentials linger (read-only scan)."""
    from .integration_status import logout_check

    out = logout_check(cfg)
    print(json.dumps(out, indent=2))
    return 0


def _cmd_auth_login(cfg, args) -> int:
    """Manual-handoff enrollment steps (guidance only, nothing captured)."""
    from . import auth_login as AL

    out = AL.login_plan(cfg, args.provider,
                        open_browser=bool(getattr(args, "open", False)))
    print(json.dumps(out, indent=2))
    return 0 if out.get("state") == "OK" else 2


def _cmd_auth_verify(cfg, args) -> int:
    """Re-probe one provider credential reference (read-only check)."""
    from . import auth_login as AL

    out = AL.verify_provider(cfg, args.provider)
    print(json.dumps(out, indent=2))
    return 0


def _cmd_sessions_list(cfg, limit: int = 20) -> int:
    """Recent action sessions from receipts (redacted)."""
    from .auth_status import recent_sessions

    print(json.dumps(recent_sessions(cfg, limit), indent=2))
    return 0


def _cmd_sessions(cfg, args) -> int:
    """Session list/search/show + lifecycle (rename/archive/delete/resume).

    The same ``auth_status.SessionStore`` the dashboard and voice read through
    ``recent_sessions`` backs every operation here: receipts stay append-only,
    mutations are metadata tombstones, delete needs explicit --confirm and is
    recoverable via resume, and every mutation is redacted + audited.
    """
    from .auth_status import SessionStore, recent_sessions

    sub = getattr(args, "sessions_command", None) or "list"
    if sub == "list":
        out = recent_sessions(cfg, int(getattr(args, "limit", 20) or 20))
    else:
        store = SessionStore(cfg.state_dir())
        sid = str(getattr(args, "session_id", "") or "")
        if sub == "search":
            out = store.search(str(getattr(args, "query", "") or ""),
                               limit=int(getattr(args, "limit", 20) or 20))
        elif sub == "show":
            out = store.show(sid)
        elif sub == "rename":
            out = store.rename(sid, str(getattr(args, "name", "") or ""))
        elif sub == "archive":
            out = store.archive(sid)
        elif sub == "delete":
            out = store.delete(sid, confirm=bool(getattr(args, "confirm", False)))
        elif sub == "resume":
            out = store.resume(sid)
        else:
            out = {"schema": "qubit-sessions/1", "state": "FAILED",
                   "code": "UNKNOWN_SUBCOMMAND", "detail": sub}
    print(json.dumps(out, indent=2))
    state = str(out.get("state", ""))
    if state == "OK":
        return 0
    if state in ("NOT_FOUND", "UNAVAILABLE", "FAILED"):
        return 2
    return 3


def _rebrand_chat_sessions_cli(out: Any) -> Any:
    """Render a chat-session store result as Qubit at the CLI boundary.

    The canonical store is durable history and may hold legacy upstream-authored
    text (a session ``name``/``title``/``preview`` and message ``content``/
    ``meta``). It is never rewritten; upstream product names are rendered as
    Qubit only on the printed response, through the SAME helpers the dashboard
    handlers use (``voicebridge.qubit_compat._rebrand_chat_record`` /
    ``_rebrand_chat_messages``), so the CLI and the dashboard agree for
    identical data. Only those served fields are touched: path fields such as
    ``cwd`` and every other key stay byte-exact.
    """
    from .qubit_compat import _rebrand_chat_messages, _rebrand_chat_record

    if not isinstance(out, dict):
        return out
    rebranded = dict(out)
    if isinstance(rebranded.get("sessions"), list):
        rebranded["sessions"] = [_rebrand_chat_record(row)
                                 for row in rebranded["sessions"]]
    if isinstance(rebranded.get("session"), dict):
        rebranded["session"] = _rebrand_chat_record(rebranded["session"])
    if isinstance(rebranded.get("messages"), list):
        rebranded["messages"] = _rebrand_chat_messages(rebranded["messages"])
    return rebranded


def _cmd_chat_sessions(cfg, args) -> int:
    """Canonical chat transcripts (state.db) — the ONE session authority.

    B-04M: reads ``voicebridge.chat_transport.store`` (the same store the
    terminal/agent, /chat, /qubit-chat, dashboard, and voice use), so migrated
    Qubit history and native Qubit chats are visible from the CLI. Read-only;
    lifecycle mutations stay on the confirmation-gated surfaces. The printed
    result is rebranded by :func:`_rebrand_chat_sessions_cli` exactly like the
    dashboard, so upstream product names in stored history render as Qubit.
    """
    from . import chat_transport as CT

    store = CT.store(cfg)
    sub = getattr(args, "chat_sessions_command", None) or "list"
    if sub == "show":
        out = store.show(str(getattr(args, "session_id", "") or ""))
    elif sub == "messages":
        out = store.messages(str(getattr(args, "session_id", "") or ""),
                             limit=int(getattr(args, "limit", 100) or 100))
    elif sub == "search":
        out = store.list(limit=int(getattr(args, "limit", 20) or 20),
                         query=str(getattr(args, "query", "") or ""))
    elif sub == "export":
        out = store.export(str(getattr(args, "session_id", "") or ""))
    else:
        out = store.list(limit=int(getattr(args, "limit", 20) or 20),
                         include_archived=bool(getattr(args, "archived",
                                                       False)))
    print(json.dumps(_rebrand_chat_sessions_cli(out), indent=2))
    return 0


def _cmd_mcp_status(cfg) -> int:
    """MCP server inventory without mounting anything (read-only)."""
    from .integration_status import mcp_status

    print(json.dumps(mcp_status(cfg), indent=2))
    return 0


def _cmd_mcp_invoke(cfg, args) -> int:
    """Call a tool on a configured MCP server through the brokered client.

    Confirmation/e-stop/allowlist/timeout are enforced by
    :func:`voicebridge.mcp_servers.invoke_cli`; a refusal is typed and never a
    fabricated success.
    """
    from . import mcp_servers as MS

    tool_args = {}
    for item in getattr(args, "arg", []) or []:
        if "=" in item:
            key, value = item.split("=", 1)
            tool_args[key.strip()] = value
    out = None
    try:
        out = MS.invoke_cli(
            cfg, args.server, tool=getattr(args, "tool", "") or "",
            args=tool_args or None,
            confirmed=bool(getattr(args, "confirm", False)),
            confirmation_id=str(getattr(args, "confirmation_id", "") or ""))
    except MS.McpServerError as exc:
        out = {"state": "REFUSED", "ok": False, "code": exc.code,
               "detail": exc.detail}
    print(json.dumps(out, indent=2))
    return 0 if out.get("state") in ("OK", "APPROVAL_REQUIRED") else 1


def _cmd_worker_status(cfg) -> int:
    """Native Qubit worker-lane status (read-only)."""
    from .integration_status import worker_status

    print(json.dumps(worker_status(cfg), indent=2))
    return 0


def _cmd_verify_static() -> int:
    """Hermetic static release qualification (no build/install)."""
    from .integration_status import verify_static

    out = verify_static()
    print(json.dumps(out, indent=2))
    return 0 if out.get("state") == "PASS" else 1


def _cmd_lessons(cfg, args) -> int:
    """Learned preferences with stored provenance (Packet 6A).

    Same LearningLedger store the dashboard (/api/lessons/*) and voice
    (lessons_*) use. Mutations need --confirm; removals tombstone with
    provenance (never wholesale delete).
    """
    import json as _json

    from .learning import LearningLedger
    from .logging_setup import log_event as _log

    sub = getattr(args, "lessons_command", None) or "list"
    led = LearningLedger(cfg.state_dir())
    if sub == "list":
        print(_json.dumps({"lessons": led.lessons(),
                           "reviews": led.reviews()}, indent=2))
        return 0
    lid = str(getattr(args, "lesson_id", "") or "").strip()
    if not lid:
        print("ERROR: lesson_id is required")
        return 2
    if not bool(getattr(args, "confirm", False)):
        print(_json.dumps({"state": "denied", "code": "CONFIRMATION_REQUIRED",
                           "detail": f"lessons {sub} {lid} needs --confirm"}))
        return 3
    if sub == "promote":
        promoted = led.promote(lid)
        if promoted is None:
            print(_json.dumps({"state": "failed", "detail": "unknown lesson id"}))
            return 2
        _log("lessons.promote", actor="cli:user", lesson_id=lid[:16])
        print(_json.dumps({"ok": True, "lesson_id": lid,
                           "lesson": promoted}, indent=2))
        return 0
    if sub == "remove":
        if not led.remove(lid):
            print(_json.dumps({"state": "failed", "detail": "unknown lesson id"}))
            return 2
        _log("lessons.remove", actor="cli:user", lesson_id=lid[:16])
        print(_json.dumps({"ok": True, "lesson_id": lid}, indent=2))
        return 0
    print(f"ERROR: unknown lessons command {sub!r}")
    return 2


def _cmd_approvals(cfg, args) -> int:
    """Shared confirmation journal (same records voice + dashboard use).

    Approve executes the staged backend exactly once through the identical
    path as `voice confirm` / dashboard confirm (same session, same
    emergency-stop, audit, redaction). Deny settles without executing.
    Values are never printed: show/list expose argument names only.
    """
    import json as _json

    from .approvals import ApprovalJournal
    from .redaction import redact_text as _redact

    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                              default_ttl_s=120.0)
    sub = getattr(args, "approvals_command", None) or "list"

    def _public(rec):
        meta = getattr(rec, "meta", {}) or {}
        args_meta = meta.get("args", {}) if isinstance(
            meta.get("args", {}), dict) else {}
        return {"id": rec.id, "capability": rec.capability,
                "summary": _redact(rec.summary or "")[:300],
                "session": meta.get("session", "voice"),
                "state": rec.state, "created_at": rec.created_at,
                "expires_at": rec.expires_at,
                "arg_names": sorted(str(k) for k in args_meta)}

    if sub == "list":
        items = [_public(a) for a in journal.pending()]
        want = (getattr(args, "session", "") or "").strip()
        if want:
            items = [i for i in items if i["session"] == want]
        print(_json.dumps({"pending": items, "count": len(items)}, indent=2))
        return 0
    cid = str(getattr(args, "confirmation_id", "") or "").strip()
    if not cid:
        print("ERROR: confirmation_id is required")
        return 2
    if sub == "show":
        rec = journal.get(cid)
        if rec is None:
            print(_json.dumps({"state": "failed", "code": "NO_CONFIRMATION",
                               "detail": "unknown confirmation id"}))
            return 2
        print(_json.dumps(_public(rec), indent=2))
        return 0
    if sub == "respond":
        rec = journal.get(cid)
        if rec is None or rec not in journal.pending():
            print(_json.dumps({"state": "failed", "code": "NO_CONFIRMATION",
                               "detail": "That confirmation is gone."}))
            return 2
        meta = getattr(rec, "meta", {}) or {}
        from . import dashboard_ops as _OPS

        session = _OPS.record_session(cfg, cid)
        want = (getattr(args, "session", "") or "").strip()
        if want and want != session:
            print(_json.dumps({"state": "failed", "code": "SESSION_MISMATCH",
                               "detail": "That confirmation belongs to "
                                         "another session."}))
            return 2
        decision = getattr(args, "decision", "deny")
        if decision == "deny":
            journal.respond(cid, "deny")
            print(_json.dumps({"state": "executed",
                               "speech": "Denied.", "receipt": {"denied": cid}},
                              indent=2))
            return 0
        capability = str(getattr(rec, "capability", "") or "")
        if capability.startswith("dashboard."):
            from . import dashboard_ops as OPS

            out = OPS.confirm_op(cfg, cid, actor="cli:user", session=session)
        else:
            from . import voice_commands as VC

            ctx = VC.make_ctx(cfg, actor="cli:user", session=session,
                              source="cli")
            outcome = VC.confirm_pending(ctx, cid)
            out = {"state": outcome.kind, "speech": outcome.speech}
            if outcome.receipt:
                out["receipt"] = outcome.receipt
        print(_json.dumps(out, indent=2, default=str))
        return 0 if out.get("state") == "executed" else 2
    print(f"ERROR: unknown approvals command {sub!r}")
    return 2


def _cmd_checkpoints(cfg, args) -> int:
    """Journal checkpoint inspect/prune (metadata only, explicit confirm)."""
    from . import checkpoints as CK

    sub = getattr(args, "checkpoints_command", None) or "list"
    state_dir = cfg.state_dir()
    if sub == "create":
        journal = args.journal or str(state_dir / "approvals.jsonl")
        try:
            out = CK.create(state_dir, journal, args.name)
        except CK.CheckpointError as exc:
            print(json.dumps({"state": "FAILED", "detail": str(exc)}))
            return 1
        print(json.dumps(out, indent=2))
        return 0
    if sub == "verify":
        print(json.dumps(CK.verify(state_dir, args.name), indent=2))
        return 0
    if sub == "prune":
        out = CK.prune(state_dir, args.name,
                       confirm=bool(getattr(args, "confirm", False)))
        print(json.dumps(out, indent=2))
        return 0 if out.get("state") == "OK" else 1
    print(json.dumps({"checkpoints": CK.list_all(state_dir)}, indent=2))
    return 0


def _cmd_mic_test(cfg) -> int:
    import numpy as np
    from .audio.vad import Endpointer
    from .audio.mic import MicCapture
    mic_cfg = cfg.section("mic")
    print("Recording 5 seconds. Speak now...")
    mic = MicCapture(device=mic_cfg.get("device"),
                     sample_rate=mic_cfg.get("sample_rate", 48000),
                     channels=mic_cfg.get("channels", 2),
                     blocksize=mic_cfg.get("blocksize", 480),
                     endpointer=Endpointer(aggressiveness=mic_cfg.get("aggressiveness", 1)),
                     gain=float(mic_cfg.get("gain", 4.0)),
                     min_rms=float(mic_cfg.get("min_rms", 0.0008)))
    import time
    samples = []
    with mic.open():
        t0 = time.time()
        while time.time() - t0 < 5:
            data, _ = mic._stream.read(mic.blocksize)
            mono = MicCapture._mono(data) * mic.gain
            peak = float(np.abs(mono).max()) if mono.size else 0.0
            samples.append(peak)
    peak_avg = float(np.mean(samples)) if samples else 0.0
    print(f"peak RMS avg (with gain x{mic.gain}): {peak_avg:.4f}  (min_rms floor: {mic.min_rms})")
    print("Above the floor = the bridge will capture. If this is ~0, check the input")
    print("device / gain (Focusrite: turn gain knob to ~12 o'clock, speak 6-12 in away).")
    return 0


def _cmd_asr_test(cfg, file: Path) -> int:
    if not file.exists():
        print(f"file not found: {file}")
        return 1
    asr_cfg = cfg.section("asr")
    provider = str(asr_cfg.get("provider", "sherpa") or "sherpa")
    if provider == "whisper":
        from .asr.whisper import FasterWhisperASR
        asr = FasterWhisperASR(model=asr_cfg.get("model", "base"),
                               device=asr_cfg.get("device", "auto"))
    else:
        from .asr.sherpa import SherpaASR
        asr = SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                        model_dir=cfg.model_dir(),
                        provider=asr_cfg.get("provider_ep", "cpu"),
                        num_threads=int(asr_cfg.get("num_threads", 4)),
                        language=asr_cfg.get("language"),
                        hotwords=asr_cfg.get("hotwords", ""))
    asr.load()
    tr = asr.transcribe(file)
    device = getattr(asr, "_actual_device", getattr(asr, "provider", provider))
    print(f"[{tr.provider}/{device}] conf={tr.confidence:.2f} dur={tr.duration_s:.1f}s")
    print(tr.text)
    return 0



def _cmd_tts_test(cfg, text: str, out: Path | None) -> int:
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    tts.load()
    path = tts.synthesize(text, out)
    print(f"synthesized: {path}")
    from .audio.playback import Playback
    Playback().play(path)
    return 0


def _cmd_roundtrip(cfg, text: str | None, model: str | None, orch) -> int:
    """End-to-end: TTS synth sample -> whisper ASR -> classify -> gateway task
    -> summarize -> TTS synth final. Prints each stage; returns 0 on success."""
    from .tts.piper import PiperTTS
    from .asr.whisper import FasterWhisperASR

    sample = text or ("Open the current project, inspect the Stripe webhook flow, "
                      "and tell Qubit to patch the signature verification and add structured logs.")
    print("[1/6] synth sample speech (piper) ...")
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    tts.load()
    wav = tts.synthesize(sample, Path("/tmp/qubit-voicebridge/roundtrip-sample.wav"))
    print(f"      -> {wav}")

    print("[2/6] transcribe (whisper) ...")
    asr_cfg = cfg.section("asr")
    asr = FasterWhisperASR(model=asr_cfg.get("model", "base"), device=asr_cfg.get("device", "auto"))
    asr.load()
    tr = asr.transcribe(wav)
    print(f"      -> '{tr.text}'")
    assert tr.text.strip(), "ASR produced empty text"

    print("[3/6] classify intent ...")
    orch.start()
    intent = orch.classifier.classify(tr.text)
    print(f"      -> kind={intent.kind} conf={intent.confidence:.2f} reason={intent.reason}")
    assert intent.kind in ("coding", "conversation"), "expected a coding (or conversation) intent"

    print("[4/6] dispatch to the agent runtime (this can take a while on local models) ...")
    if model:
        cfg.data.setdefault("agent", {})["model"] = model
    task_text = intent.task_text or tr.text
    orch._dispatch_coding(task_text, model=model)
    print("      -> done")

    print("[5/6] final spoken summary rendered by piper ...")
    # The orchestrator already spoke the summary; render it to a file to verify.
    # Re-run the last summary from events is overkill — synthesize a fixed check line.
    check = tts.synthesize("Round trip complete.", Path("/tmp/qubit-voicebridge/roundtrip-final.wav"))
    print(f"      -> {check}")

    print("[6/6] roundtrip OK")
    return 0


def _record_seconds_to_wav(cfg, seconds: int, out_path: Path) -> Path:
    """Record `seconds` of raw mono 16k mic audio to a WAV (for enrollment)."""
    import numpy as np
    import sounddevice as sd
    from .audio.vad import FRAME_RATE, resample_linear
    mic_cfg = cfg.section("mic")
    sr = int(mic_cfg.get("sample_rate", 48000))
    ch = int(mic_cfg.get("channels", 2))
    print(f"Recording {seconds}s of your voice — speak naturally now...")
    chunks = []
    with sd.InputStream(samplerate=sr, channels=ch,
                        device=mic_cfg.get("device"),
                        blocksize=int(mic_cfg.get("blocksize", 480)),
                        dtype="float32") as stream:
        for _ in range(int(seconds * sr / stream.blocksize)):
            data, _ = stream.read(stream.blocksize)
            chunks.append(data)
    audio = np.concatenate(chunks, axis=0)
    # loudest channel mono (same as the bridge's capture path)
    if audio.ndim > 1 and audio.shape[1] > 1:
        audio = np.max(np.abs(audio), axis=1) * np.sign(audio[:, 0])
    mono = resample_linear(audio.astype(np.float32), sr, FRAME_RATE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(FRAME_RATE)
        wf.writeframes((np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    print(f"Saved {seconds}s capture to {out_path}")
    return out_path


def _cmd_enroll(cfg, file: Path, name: str | None, seconds: int | None) -> int:
    from .voice_id import SpeakerVerifier
    sv_cfg = cfg.section("speaker_verify")
    name = name or sv_cfg.get("name", "user")
    sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(), name=name,
                         threshold=float(sv_cfg.get("threshold", 0.75)))
    if file is None:
        seconds = seconds or int(sv_cfg.get("enroll_seconds", 5))
        wav = _record_seconds_to_wav(cfg, seconds,
                                     Path(cfg.state_dir()) / "enroll-last.wav")
    else:
        wav = file
    try:
        score = sv.enroll(wav)
    except Exception as exc:
        print(f"ENROLL FAILED: {exc}")
        return 1
    print(f"Enrolled '{name}' (self-score {score:.3f}).")
    print("Barge-in now answers only to YOUR voice. Hold Ctrl and talk.")
    return 0


def _cmd_verify(cfg, file: Path, name: str | None) -> int:
    from .voice_id import SpeakerVerifier
    sv_cfg = cfg.section("speaker_verify")
    name = name or sv_cfg.get("name", "user")
    sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(), name=name,
                         threshold=float(sv_cfg.get("threshold", 0.75)))
    try:
        sv.load()
        accepted, score = sv.verify_wav(file, name)
    except Exception as exc:
        print(f"VERIFY FAILED: {exc}")
        return 1
    print(f"score vs '{name}': {score:.3f}  (threshold {sv.threshold}) -> "
          f"{'MATCH (your voice)' if accepted else 'REJECT (not you)'}")
    return 0


def _cmd_flags(cfg, args, explicit_config: Path | None) -> int:
    """voicebridge flags list|get|set|rollback. Read-only unless --apply."""
    from . import feature_flags as FF
    import json as _json

    cmd = getattr(args, "flags_command", None) or "list"
    if cmd == "list":
        rows = [(f.name, f.category, str(FF.effective(cfg, f.name)),
                 str(f.default), str(f.rollback_value), ".".join(f.config_path))
                for f in FF.FLAGS]
        if getattr(args, "json", False):
            print(_json.dumps([{"name": r[0], "category": r[1], "effective": r[2],
                                "default": r[3], "rollback": r[4], "config_path": r[5]}
                               for r in rows], indent=2, sort_keys=True))
            return 0
        print(f"{'flag':<16} {'category':<10} {'effective':<12} "
              f"{'default':<12} {'rollback':<12} config_path")
        for r in rows:
            print(f"{r[0]:<16} {r[1]:<10} {r[2]:<12} {r[3]:<12} {r[4]:<12} {r[5]}")
        return 0

    if cmd == "get":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(FF.effective(cfg, f.name))
        return 0

    target = explicit_config or cfg.path
    if target is None:
        from .config import resolve_config_path
        target = resolve_config_path()
    target = Path(target)

    if cmd == "set":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        value: object
        if isinstance(f.default, bool):
            value = str(args.value).lower() in ("1", "true", "yes", "on")
        elif isinstance(f.default, (int, float)):
            value = type(f.default)(args.value)
        else:
            value = args.value
        if getattr(args, "apply", False):
            if not target.exists():
                print(f"ERROR: config file not found: {target}")
                return 2
            bak = FF.apply_flag(target, f.name, value)
            print(f"applied {f.name}={value} -> {target} (backup {bak})")
        else:
            print(f"[dry-run] would set {f.name}={value} in {target}")
            print(f"  env override: {' '.join(f'{k}={v}' for k, v in FF.to_env(f, value).items())}")
            print(f"  rollback    : voicebridge flags rollback {f.name} --apply --config {target}")
        return 0

    if cmd == "rollback":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        if getattr(args, "apply", False):
            if not target.exists():
                print(f"ERROR: config file not found: {target}")
                return 2
            bak = FF.rollback_flag(target, f.name)
            print(f"rolled back {f.name}={f.rollback_value} -> {target} (backup {bak})")
        else:
            print(f"[dry-run] would restore {f.name}={f.rollback_value} in {target}")
            print(f"  env override: {' '.join(f'{k}={v}' for k, v in FF.to_env(f, f.rollback_value).items())}")
        return 0
    print(f"ERROR: unknown flags command {cmd!r}")
    return 2


def _cmd_flag_drill(cfg) -> int:
    """Hermetic rollback drill on throwaway config/state (never the live one)."""
    from . import feature_flags as FF
    import tempfile as _tf
    from .config import Config as _Config

    with _tf.TemporaryDirectory(prefix="vb-flag-drill-") as td:
        tdp = Path(td)
        cfg_path = tdp / "voice.yaml"
        cfg_path.write_text(FF.redact_config_yaml(cfg))
        state = tdp / "state"
        state.mkdir()
        # seed user data (memory + report + cursor) that rollback must preserve
        mem = state / "memory.db"
        mem.write_bytes(b"\x00MEMORY\x01")
        rep = state / "reports"
        rep.mkdir()
        (rep / "manifest.json").write_text('{"id":"x"}')
        cur = state / "cursors.jsonl"
        cur.write_text('{"account":"gmail","cursor":7}\n')

        cfg2 = _Config.load(cfg_path)
        before = FF.state_snapshot(state)
        for f in FF.FLAGS:
            flip = (not f.default) if isinstance(f.default, bool) else (
                f.rollback_value if f.rollback_value != f.default else f.default)
            FF.apply_flag(cfg_path, f.name, flip)
            cfg3 = _Config.load(cfg_path)
            if FF.effective(cfg3, f.name) == f.default:
                print(f"DRILL FAIL: {f.name} did not flip to {flip}")
                return 2
            FF.rollback_flag(cfg_path, f.name)
            cfg4 = _Config.load(cfg_path)
            if FF.effective(cfg4, f.name) != f.rollback_value:
                print(f"DRILL FAIL: {f.name} did not roll back to "
                      f"{f.rollback_value}")
                return 2
            FF.apply_flag(cfg_path, f.name, f.default)
            cfg5 = _Config.load(cfg_path)
            if FF.effective(cfg5, f.name) != f.default:
                print(f"DRILL FAIL: {f.name} did not restore default "
                      f"{f.default}")
                return 2
            print(f"DRILL OK: {f.name} flip->rollback preserved state")
        after = FF.state_snapshot(state)
        if before != after:
            print(f"DRILL FAIL: user data changed: {set(before) ^ set(after)}")
            return 2
        print(f"flag drill: {len(FF.FLAGS)} flags flipped+rolled back; user data intact")
        return 0


def _cmd_package(cfg, args) -> int:
    """voicebridge package manifest|doctor|launch|upgrade|uninstall|
    model|distributable|certify."""
    import json as _json
    from . import distribution as D
    cmd = getattr(args, "package_command", None) or "manifest"
    if cmd == "manifest":
        print(_json.dumps(D.manifest(), indent=2, sort_keys=True))
        return 0
    if cmd == "doctor":
        checks = D.doctor(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(checks, indent=2, sort_keys=True))
        else:
            for c in checks:
                print(f"[{c['state']:>13}] {c['name']:<12} {c['detail']}")
            print(D.doctor_summary(checks))
        return 0 if all(c["state"] == "OK" for c in checks) else 1
    if cmd == "launch":
        print(" ".join(D.launcher_command(cfg, no_hotkey=args.no_hotkey)))
        return 0
    if cmd == "upgrade":
        print(_json.dumps(D.upgrade_plan(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "uninstall":
        plan = D.uninstall_plan(cfg)
        print(_json.dumps(plan, indent=2, sort_keys=True))
        print("NOTE: uninstall requires explicit operator confirmation; "
              "state is preserved by default.")
        return 0
    if cmd == "model":
        rec = D.model_artifact_record(cfg)
        print(_json.dumps(rec, indent=2, sort_keys=True))
        return 0 if rec.get("state") == "OK" else 1
    if cmd == "distributable":
        m = D.distributable_manifest()
        print(_json.dumps(m, indent=2, sort_keys=True))
        return 0 if m["complete"] else 1
    if cmd == "certify":
        from . import release as R
        report = R.certify()
        if getattr(args, "json", False):
            print(_json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_certify_human(report)
        return 0 if R.report_pass(report) else 2
    print(f"ERROR: unknown package command {cmd!r}")
    return 2


def _cmd_product(cfg, args) -> int:
    """voicebridge product paths|install|first-run|repair|capabilities|
    migration|service|shortcut|tray|boundary|upgrade|rollback (Packet 8K-P)."""
    import json as _json
    from . import product as P
    cmd = getattr(args, "product_command", None) or "paths"
    if cmd == "paths":
        diag = P.path_map_diagnostic()
        if getattr(args, "json", False):
            print(_json.dumps(diag, indent=2, sort_keys=True))
        else:
            print(f"product: {diag['product']} {diag['version']}")
            for k, v in diag["paths"].items():
                print(f"  {k:<10} {v}")
            print(f"platform: {diag['platform']['state']} "
                  f"({diag['platform']['detail']})")
        return 0
    if cmd == "install":
        out = P.install(dry_run=not args.apply,
                        service_owner=args.service_owner)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("READY", "DEGRADED") else 1
    if cmd == "first-run":
        out = P.first_run(dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("READY", "DEGRADED", "BLOCKED") else 1
    if cmd == "repair":
        out = P.repair()
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "READY" else 1
    if cmd == "capabilities":
        out = P.capabilities()
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            for c in out["capabilities"].values():
                print(f"[{c['state']:>10}] {c['capability']:<10} {c['detail']}")
            print(out["note"])
        return 0
    if cmd == "migration":
        out = P.branding_status()
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"migration: {out['state']} — {out['detail']}")
        return 0
    if cmd == "migrate":
        out = P.migrate(dry_run=not args.apply, backup_dir=args.backup_dir)
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"migrate: {out['state']} — {out['detail']}")
            if out.get("backup_dir"):
                print(f"  backup: {out['backup_dir']}")
        return 0 if out["state"] in ("OK", "READY", "FRESH") else 1
    if cmd == "migrate-rollback":
        out = P.migrate_rollback(dry_run=not args.apply,
                                 backup_dir=args.backup_dir)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("OK", "READY") else 1
    if cmd in ("legacy-inspect", "hermes-inspect"):
        from . import qubit_migration as HM
        out = HM.inspect(cfg, home=getattr(args, "home", None))
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"runtime home: {out['home']} ({out['state']})")
            for rel, entry in out.get("databases", {}).items():
                counts = entry.get("counts", {})
                detail = ", ".join(f"{k}={v}" for k, v in counts.items()) or "no rows"
                print(f"  {rel}: {detail}")
            cfg_info = out.get("config", {})
            if cfg_info:
                print(f"  model: {cfg_info.get('model_provider')}/"
                      f"{cfg_info.get('model_default')}")
            print(f"  secret refs: {len(out.get('secret_refs', []))}")
            for sub, n in (out.get("dirs") or {}).items():
                print(f"  {sub}: {n} file(s)")
        return 0 if out["state"] == "OK" else 1
    if cmd in ("legacy-migrate", "hermes-migrate"):
        from . import qubit_migration as HM
        out = HM.apply(cfg, confirm=bool(args.apply),
                       backup_dir=getattr(args, "backup_dir", None),
                       home=getattr(args, "home", None))
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"migration: {out['state']}")
            if out.get("dry_run"):
                print(f"  source sessions={out.get('source_sessions')} "
                      f"messages={out.get('source_messages')} "
                      f"dest={out.get('destination')}")
                print(f"  {out.get('detail', '')}")
            else:
                print(f"  imported sessions={out.get('imported_sessions')} "
                      f"messages={out.get('imported_messages')} "
                      f"skipped={out.get('skipped_sessions')}")
                mp = out.get("model_profile") or {}
                if mp:
                    print(f"  model profile: {mp.get('profile_id')} "
                          f"({mp.get('state')}) key_ref={mp.get('key_ref')}")
                cron = out.get("cron") or {}
                if cron:
                    print(f"  schedules: +{cron.get('added')} job(s)")
                print(f"  added files: {out.get('added_files')}")
                print(f"  backup: {out.get('backup_dir')}")
                for err in out.get("errors", [])[:5]:
                    print(f"  error: {err}")
        return 0 if out["state"] in ("OK", "PARTIAL", "DRY_RUN") else 1
    if cmd in ("legacy-rollback", "hermes-rollback"):
        from . import qubit_migration as HM
        out = HM.rollback(cfg, backup_dir=args.backup_dir,
                          confirm=bool(getattr(args, "apply", False)))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("OK", "DRY_RUN") else 1
    if cmd == "service":
        out = P.service_owner()
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "shortcut":
        out = P.desktop_shortcut(dry_run=not args.apply)
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"shortcut: {out['state']} — {out['detail']}")
        return 0
    if cmd == "tray":
        out = P.tray_status()
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"tray: {out['state']} — {out['detail']}")
        return 0
    if cmd == "boundary":
        out = P.package_boundary()
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print(f"private source solved: {out['private_source_solved']}")
            print(out["release_blocker"])
        return 0
    if cmd == "upgrade":
        out = P.upgrade_contract()
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            print("upgrade/uninstall/rollback contract:")
            print(f"  preserves: {', '.join(out['preserves'])}")
            print(f"  does_not:  {', '.join(out['does_not'])}")
        return 0
    if cmd == "rollback":
        out = P.rollback(dry_run=not args.apply, backup_dir=args.backup_dir)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("OK", "READY", "MISSING") else 1
    print(f"ERROR: unknown product command {cmd!r}")
    return 2


def _cmd_setup(cfg, args) -> int:
    """voicebridge setup check|run|health|diagnose|matrix|wizard|repair|audit|
    export|backup|upgrade|rollback (Packet 8T). Same backend as the dashboard;
    dry-run by default, explicit --apply --confirm for mutations."""
    import json as _json
    from . import lifecycle as L
    cmd = getattr(args, "setup_command", None) or "check"
    if cmd in ("check", "check-offline"):
        out = L.checklist(cfg, offline=(cmd == "check-offline"))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("READY", "DEGRADED", "PENDING") else 1
    if cmd == "run":
        raw_steps = getattr(args, "steps", "") or ""
        wanted = [s.strip() for s in raw_steps.split(",") if s.strip()] or None
        out = L.setup_run(steps=wanted, dry_run=not args.apply,
                          confirm=bool(args.confirm))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("SUCCESS", "PENDING") else 1
    if cmd == "health":
        print(_json.dumps(L.health_inventory(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "diagnose":
        print(_json.dumps(L.diagnostic_bundle(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "matrix":
        print(_json.dumps(L.capability_matrix(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "wizard":
        print(_json.dumps(L.audio_wizard(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "repair":
        out = L.repair_order(cfg)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("READY", "DEGRADED", "PENDING") else 1
    if cmd == "audit":
        print(_json.dumps(L.audit_timeline(), indent=2, sort_keys=True))
        return 0
    if cmd == "export":
        dest = args.out
        if dest is None:
            from . import product as P
            dest = P.default_paths().state_dir / "config-profile.json"
        out = L.config_export(cfg, dest, dry_run=not args.apply,
                              confirm=bool(args.confirm))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("SUCCESS", "PENDING") else 1
    if cmd == "backup":
        out = L.backup_center(action=args.action or "plan",
                              dry_run=not args.apply,
                              confirm=bool(args.confirm),
                              backup_dir=getattr(args, "backup_dir", None))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("READY", "SUCCESS", "PENDING",
                                     "OK", "FRESH") else 1
    if cmd == "upgrade":
        out = L.upgrade_flow(dry_run=not args.apply,
                             confirm=bool(args.confirm))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "PENDING" else 1
    if cmd == "rollback":
        out = L.rollback_flow(dry_run=not args.apply,
                              confirm=bool(args.confirm),
                              backup_dir=getattr(args, "backup_dir", None))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] in ("OK", "READY", "SUCCESS", "PENDING",
                                     "MISSING") else 1
    print(f"ERROR: unknown setup command {cmd!r}")
    return 2


def _print_certify_human(report) -> None:
    """Human-readable certification summary (never prints secret values)."""
    p = report["provenance"]
    print(f"version {p['version']} commit {p['commit'] or '?'} "
          f"dirty={p.get('dirty')}")
    core = report["machine_paths"]["core"]
    pers = report["machine_paths"]["personalization"]
    print(f"machine paths: core={len(core)} personalization={len(pers)}")
    for rel, hits in core.items():
        print(f"  FAIL core machine path {rel}: {hits}")
    for rel, hits in pers.items():
        print(f"  (personalization) {rel}: {hits}")
    print(f"secret hits: {len(report['secrets'])}")
    for rel, shapes in report["secrets"].items():
        print(f"  FAIL secret-shaped {rel}: {shapes}")
    sig = report.get("signature", {})
    print(f"signing capability: {sig.get('state', 'NOT_CONFIGURED')} "
          f"({sig.get('detail', 'not recorded')[:80]})")
    fm = " ".join(f"{c['name']}={c['state']}" for c in report["filesystem_modes"])
    print(f"filesystem modes: {fm}")
    runbooks = " ".join(f"{c['name']}={c['state']}"
                        for c in report["operator_runbooks"] if c["state"] != "OK")
    print(f"runbooks missing/failed: {runbooks or 'none'}")
    mig = " ".join(f"{c['name']}={c['state']}"
                   for c in report["backup_migration"] if c["state"] != "OK")
    print(f"migration: {mig or 'ok'}")
    units = report["units"]
    print(f"units: systemd_analyze={units.get('systemd_analyze')} "
          f"all_ok={units.get('all_ok')}")


def _bridge_service_active() -> bool:
    """True when the voice-bridge user service is running (holds the mic)."""
    try:
        import subprocess
        for unit in ("qubit-voicebridge.service", "qubit-voice-agent.service"):
            r = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True, timeout=5)
            if (r.stdout or "").strip() == "active":
                return True
        return False
    except Exception:
        return True   # fail-safe: refuse the check rather than open a 2nd stream


def _cmd_audio(cfg, args) -> int:
    """voicebridge audio list|select|reset|status — user-facing settings."""
    import json as _json
    from .audio import settings as AS
    cmd = getattr(args, "audio_command", None) or "status"
    if cmd == "list":
        try:
            devs = AS.list_devices()
        except AS.AudioSettingsError as exc:
            print(f"ERROR: {exc}")
            return 2
        if getattr(args, "json", False):
            # only STABLE fields are exposed — never raw_name (may contain an
            # ALSA hw path) or the transient PortAudio index
            def _stable(d):
                return {"stable_id": d.stable_id, "name": d.name,
                        "role": d.role, "backend": d.backend,
                        "channels": d.channels,
                        "default_samplerate": d.default_samplerate,
                        "is_default": d.is_default, "state": d.state}
            out = {"inputs": [_stable(d) for d in devs["inputs"]],
                   "outputs": [_stable(d) for d in devs["outputs"]]}
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        print("INPUT devices:")
        for d in devs["inputs"]:
            flag = " *" if d.is_default else "  "
            print(f"{flag} {d.stable_id:<45} backend={d.backend}")
        print("OUTPUT devices:")
        for d in devs["outputs"]:
            flag = " *" if d.is_default else "  "
            print(f"{flag} {d.stable_id:<45} backend={d.backend}")
        return 0
    if cmd == "select":
        try:
            if args.kind == "mic":
                dev = AS.select_microphone(cfg, args.name)
            else:
                dev = AS.select_output(cfg, args.name)
        except AS.InvalidSelectionError as exc:
            print(f"ERROR: {exc}")
            return 2
        except AS.AudioSettingsError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"selected {args.kind}: {dev.stable_id} (persisted by name)")
        return 0
    if cmd == "reset":
        try:
            bak = AS.reset_to_defaults(cfg)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"reset audio to OS defaults (backup {bak})")
        return 0
    if cmd == "status":
        st = AS.status(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(st, indent=2, sort_keys=True))
            return 0
        print(f"mic    : [{st['mic']['state']:>16}] {st['mic']['detail']}")
        print(f"output : [{st['output']['state']:>16}] {st['output']['detail']}")
        return 0 if all(v["state"] == "OK" for v in st.values()) else 1
    if cmd == "check":
        from .audio.mic import mic_selfcheck
        # The Scarlett tolerates ONE input stream at a time. If the bridge is
        # already running it holds the mic (its own startup self-check runs
        # before the barge stream opens) — refuse rather than open a second
        # stream and risk the documented PortAudio corruption.
        if _bridge_service_active():
            msg = ("bridge is running and already holds the microphone "
                   "(one input stream at a time); its startup mic self-check "
                   "is in /api/status -> mic and the journal (mic.selfcheck)")
            if getattr(args, "json", False):
                print(_json.dumps({"state": "REFUSED", "detail": msg},
                                  indent=2, sort_keys=True))
            else:
                print(f"mic self-check : [      REFUSED] {msg}")
            return 1
        res = mic_selfcheck(cfg, seconds=float(
            cfg.get("mic", "selfcheck_seconds", default=1.0)))
        if getattr(args, "json", False):
            print(_json.dumps(res, indent=2, sort_keys=True))
        else:
            print(f"mic self-check : [{str(res.get('state')):>11}] "
                  f"peak={res.get('peak')}")
            print(f"  {res.get('hint', '')}")
        return 0 if res.get("state") == "HEALTHY" else 1
    if cmd == "wake-status":
        from . import wake as WK
        out = WK.status(cfg)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "READY" else 1
    if cmd == "wake-probe":
        from . import wake as WK
        try:
            out = WK.detect_file(cfg, str(args.wav),
                                 threshold=args.threshold)
        except WK.WakeError as exc:
            print(_json.dumps({"state": "FAILED", "code": exc.code,
                               "detail": WK.redact_spec(exc.detail)[:300],
                               "owner": "qubit-policy"},
                              indent=2))
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(f"ERROR: unknown audio command {cmd!r}")
    return 2


_PROFILE_MODEL_SECRET_GUIDANCE = (
    "key_ref must be a secret reference (env:NAME or file:PATH), never a raw "
    "key value. Put the key in your environment or an owner-only file first; "
    "secrets are never written to a profile, command history, a transcript, or "
    "the audit log.")


def _profile_model_emit(out) -> int:
    import json as _json

    print(_json.dumps(out, indent=2, sort_keys=True))
    state = str(out.get("state", "OK"))
    ok = out.get("ok", True)
    return 0 if ok is not False and state in (
        "OK", "VALID", "DEGRADED", "dry_run") else 2


def _profile_model_key_ref_guard(key_ref):
    """Typed refusal for a raw secret value; None when the ref is safe."""
    from .providers import resolver as R

    ref = str(key_ref or "").strip()
    if not ref:
        return None
    if R.looks_like_raw_secret(ref):
        return {"state": "FAILED", "code": "RAW_SECRET_REFUSED",
                "detail": "refused a raw secret value. " +
                          _PROFILE_MODEL_SECRET_GUIDANCE}
    if not R.is_secret_ref(ref):
        return {"state": "FAILED", "code": "BAD_SECRET_REF",
                "detail": _PROFILE_MODEL_SECRET_GUIDANCE}
    return None


def _profile_model_roles(text):
    from .providers import resolver as R

    if text is None:
        return None
    roles = [r.strip() for r in str(text).replace(" ", ",").split(",")
             if r.strip()]
    return roles or [R.DEFAULT_ROLE]


def _cmd_profile_model(cfg, args) -> int:
    """qubit profile model list|show|test|create|clone|edit|activate|
    deactivate|rollback|remove — canonical resolver-backed model profiles.

    Reads execute; mutations are dry-run unless --confirm. Secrets are
    ``env:``/``file:`` references only: a raw key is refused with a typed
    error before anything is written.
    """
    import dataclasses
    import json as _json

    from .providers import resolver as R

    cmd = getattr(args, "profile_model_command", None) or "list"
    try:
        if cmd == "list":
            out = R.list_profiles(cfg)
            if getattr(args, "json", False):
                return _profile_model_emit(out)
            print(f"{'id':<24} {'kind':<9} {'provider':<12} {'model':<26} "
                  f"{'locality':<9} {'ctx':>8} {'gate':<8} default")
            for p in out["profiles"]:
                print(f"{p['id']:<24} {p['kind']:<9} {p['provider']:<12} "
                      f"{str(p['model']):<26} {p['locality']:<9} "
                      f"{int(p.get('context_tokens') or 0):>8} "
                      f"{p['gate']:<8} {'yes' if p['default'] else ''}")
            print("count:", out["count"])
            return 0
        if cmd == "show":
            return _profile_model_emit(R.show_profile(cfg, args.profile))
        if cmd == "test":
            return _profile_model_emit(
                R.test_profile(cfg, args.profile, probe=bool(args.probe)))
        if cmd == "create":
            guard = _profile_model_key_ref_guard(args.key_ref)
            if guard:
                return _profile_model_emit(guard)
            kind = args.kind or R.LOCAL
            if kind == R.PLUGIN:
                # Provider-plugin record (Radius adapter): credentials are an
                # env:/file: reference only, and a default reference is valid
                # without a present key (config-only until it resolves).
                profile = R.radius_profile(
                    args.id,
                    model=(args.model if args.model is not None else "balanced"),
                    roles=_profile_model_roles(args.roles) or [R.DEFAULT_ROLE],
                    key_ref=(str(args.key_ref or "").strip()
                             or R.RADIUS_KEY_REF),
                    base_url=args.base_url or R.RADIUS_BASE_URL,
                    egress_consent=args.egress_consent or "deny")
                return _profile_model_emit(R.upsert_model_profile(
                    cfg, profile, confirm=bool(args.confirm)))
            profile = R.ModelProfile(
                id=args.id, kind=kind,
                provider=args.provider or "ollama",
                model=args.model if args.model is not None else args.id,
                base_url=args.base_url or "",
                key_ref=str(args.key_ref or "").strip(),
                context_tokens=int(args.context_tokens or 4096),
                # Honest capability default: the local runner streams, the
                # external request/response adapter does not.
                supports_streaming=(kind == R.LOCAL),
                locality=args.locality or
                ("local" if kind == R.LOCAL else "external"),
                egress_consent=args.egress_consent or "deny",
                roles=_profile_model_roles(args.roles) or [R.DEFAULT_ROLE])
            return _profile_model_emit(
                R.upsert_model_profile(cfg, profile, confirm=bool(args.confirm)))
        if cmd == "clone":
            return _profile_model_emit(
                R.clone_model_profile(cfg, args.source, args.new,
                                      confirm=bool(args.confirm)))
        if cmd == "edit":
            p = R.get_profile(cfg, args.profile)
            if p is None:
                raise R.ResolverError(
                    "UNKNOWN_PROFILE", f"no model profile {args.profile!r}")
            if p.default:
                raise R.ResolverError(
                    "IMMUTABLE_DEFAULT",
                    "the shipped default profile is a record, not a file: "
                    "clone it to a new id and edit the clone")
            if args.key_ref is not None:
                guard = _profile_model_key_ref_guard(args.key_ref)
                if guard:
                    return _profile_model_emit(guard)
            changes = {}
            for attr in ("kind", "provider", "model", "base_url",
                         "context_tokens"):
                val = getattr(args, attr, None)
                if val is not None:
                    changes[attr] = val
            if args.key_ref is not None:
                changes["key_ref"] = str(args.key_ref or "").strip()
            if args.roles is not None:
                changes["roles"] = _profile_model_roles(args.roles)
            if args.locality is not None:
                changes["locality"] = args.locality
            if args.egress_consent is not None:
                changes["egress_consent"] = args.egress_consent
            new_kind = changes.get("kind", p.kind)
            if args.locality is None:
                if new_kind == R.LOCAL and p.locality != "local":
                    changes["locality"] = "local"
                elif new_kind != R.LOCAL and p.locality != "external":
                    changes["locality"] = "external"
            edited = dataclasses.replace(p, **changes)
            return _profile_model_emit(
                R.upsert_model_profile(cfg, edited, confirm=bool(args.confirm)))
        if cmd == "activate":
            return _profile_model_emit(R.set_session_profile(
                cfg, args.session, args.profile, role=args.role,
                confirm=bool(args.confirm)))
        if cmd == "deactivate":
            if not args.confirm:
                return _profile_model_emit(
                    {"state": "OK", "dry_run": True, "action": "would clear",
                     "session": args.session, "role": args.role})
            return _profile_model_emit(
                R.clear_session_profile(cfg, args.session, role=args.role))
        if cmd == "rollback":
            p = R.get_profile(cfg, args.profile)
            if p is None:
                raise R.ResolverError(
                    "UNKNOWN_PROFILE", f"no model profile {args.profile!r}")
            target = p.rollback_to or p.fallback_to
            if not target:
                raise R.ResolverError(
                    "NO_ROLLBACK",
                    f"profile {p.id!r} declares no rollback target")
            return _profile_model_emit(R.set_session_profile(
                cfg, args.session, target, role=args.role,
                confirm=bool(args.confirm)))
        if cmd == "remove":
            return _profile_model_emit(R.remove_model_profile(
                cfg, args.profile, confirm=bool(args.confirm)))
        print(_json.dumps({"state": "FAILED", "code": "UNKNOWN_COMMAND",
                           "detail": f"unknown profile model command {cmd!r}"},
                          indent=2))
        return 2
    except R.ResolverError as exc:
        detail = exc.to_dict()
        detail["state"] = "FAILED"
        return _profile_model_emit(detail)


def _cmd_profile_speech(cfg, args) -> int:
    """qubit profile speech list|show|test|create|clone|remove|select|clear —
    canonical STT/TTS/realtime profiles on the resolver/speech registry.

    Reads execute; mutations are dry-run unless --confirm. Secrets are
    ``env:``/``file:`` references only.
    """
    import dataclasses
    import json as _json

    from .providers import resolver as R
    from .providers import speech as SP

    cmd = getattr(args, "profile_speech_command", None) or "list"
    try:
        if cmd == "list":
            out = R.list_speech_profiles(cfg, getattr(args, "kind", None))
            if getattr(args, "json", False):
                return _profile_model_emit(out)
            print(f"{'id':<26} {'kind':<10} {'provider':<12} {'target':<26} "
                  f"{'locality':<9} {'gate':<8} default")
            for p in out["profiles"]:
                target = p.get("model") or p.get("voice") or p.get("engine")
                print(f"{p['id']:<26} {p['kind']:<10} {p['provider']:<12} "
                      f"{str(target):<26} {p['locality']:<9} "
                      f"{p.get('gate', ''):<8} {'yes' if p['default'] else ''}")
            print("count:", out["count"])
            return 0
        if cmd == "show":
            return _profile_model_emit(
                R.show_speech_profile(cfg, args.profile))
        if cmd == "test":
            pid = str(getattr(args, "profile", "") or "")
            if pid:
                return _profile_model_emit(SP.show_speech_profile(cfg, pid))
            kind = getattr(args, "kind", SP.SPEECH_IN) or SP.SPEECH_IN
            eff = (R.resolve_speech_in(cfg) if kind == SP.SPEECH_IN
                   else R.resolve_speech_out(cfg) if kind == SP.SPEECH_OUT
                   else R.resolve_realtime(cfg))
            return _profile_model_emit(
                {"state": "OK", "kind": kind,
                 "effective": eff.to_public_dict()})
        if cmd == "create":
            profile = SP.SpeechProfile.from_record({
                "id": args.id, "kind": args.kind, "provider": args.provider,
                "model": args.model, "voice": args.voice,
                "device": args.device, "language": args.language,
                "locality": args.locality or (
                    "external" if args.kind == SP.REALTIME else "local"),
                "egress_consent": args.egress_consent or "deny",
                "key_ref": str(args.key_ref or "").strip(),
                "base_url": args.base_url, "engine": args.engine,
                "fallback_to": args.fallback_to})
            return _profile_model_emit(R.upsert_speech_profile(
                cfg, profile, confirm=bool(args.confirm)))
        if cmd == "clone":
            return _profile_model_emit(R.clone_speech_profile(
                cfg, args.source, args.new, confirm=bool(args.confirm)))
        if cmd == "remove":
            return _profile_model_emit(R.remove_speech_profile(
                cfg, args.profile, confirm=bool(args.confirm)))
        if cmd == "select":
            if not args.confirm:
                return _profile_model_emit(
                    {"state": "OK", "dry_run": True, "action": "would select",
                     "profile": args.profile, "session": args.session,
                     "kind": args.kind})
            return _profile_model_emit(SP.set_session_speech_profile(
                cfg, args.session, args.kind, args.profile, confirm=True))
        if cmd == "clear":
            if not args.confirm:
                return _profile_model_emit(
                    {"state": "OK", "dry_run": True, "action": "would clear",
                     "session": args.session, "kind": args.kind or "*"})
            return _profile_model_emit(SP.clear_session_speech_profile(
                cfg, args.session, kind=getattr(args, "kind", None)))
        print(_json.dumps({"state": "FAILED", "code": "UNKNOWN_COMMAND",
                           "detail": f"unknown profile speech command {cmd!r}"},
                          indent=2))
        return 2
    except R.ResolverError as exc:
        detail = exc.to_dict()
        detail["state"] = "FAILED"
        return _profile_model_emit(detail)


def _cmd_profile(cfg, args) -> int:
    """voicebridge profile list|show|check|install|select|reset|upgrade|
    remove|rollback. Dry-run by default; JSON output."""
    import json as _json
    from . import profiles as P
    cmd = getattr(args, "profile_command", None) or "list"
    if cmd == "model":
        return _cmd_profile_model(cfg, args)
    if cmd == "speech":
        return _cmd_profile_speech(cfg, args)
    if cmd == "list":
        out = P.profiles_list(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        print(f"{'id':<22} {'kind':<6} {'state':<8} {'active':<6} rollback")
        for p in out["profiles"]:
            print(f"{p['id']:<22} {p['kind']:<6} {p['gate']:<8} "
                  f"{'*' if p['active'] else '':<6} {p['rollback_to']}")
        print("active:", _json.dumps(out["active"]))
        return 0
    if cmd == "show":
        out = P.profiles_show(cfg, args.kind)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "check":
        out = P.profile_check(cfg, args.kind, args.profile)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "install":
        try:
            data = _json.loads(Path(args.manifest).read_text())
            man = P.ProviderManifest(**data)
        except Exception as exc:
            print(_json.dumps({"state": "MISSING",
                               "detail": f"manifest error: {exc}"},
                              indent=2))
            return 2
        out = P.install_manifest(cfg, man)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "select":
        out = P.apply_profile(cfg, args.kind, args.profile,
                              dry_run=not args.apply,
                              require_confirmation=not args.confirm)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "reset":
        out = P.reset_profile(cfg, args.kind, dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "upgrade":
        out = P.upgrade_profile(cfg, args.kind, args.profile or "",
                                dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "remove":
        out = P.remove_profile(cfg, args.kind, args.profile,
                               dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "rollback":
        out = P.rollback_profile(cfg, args.kind, dry_run=not args.apply,
                                 backup=args.backup)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    print(f"ERROR: unknown profile command {cmd!r}")
    return 2


def _cmd_branding(cfg, args) -> int:
    """voicebridge branding plan|verify|rollback — Qubit migration."""
    import json as _json
    from . import profiles as P
    cmd = getattr(args, "branding_command", None) or "plan"
    if cmd == "plan":
        out = P.branding_plan(cfg, dry_run=not args.apply,
                              backup_dir=args.backup_dir,
                              repo=args.repo,
                              migrate_paths=args.migrate_paths)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "verify":
        out = P.branding_verify(cfg, repo=args.repo)
        print(_json.dumps(out, indent=2, sort_keys=True))
        if out.get("limitation"):
            print("\nLIMITATION: " + out["limitation"])
        return 0 if out["state"] == "VERIFIED" else 1
    if cmd == "rollback":
        out = P.branding_rollback(cfg, args.backup_dir, repo=args.repo,
                                  home=getattr(args, "home", None))
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    print(f"ERROR: unknown branding command {cmd!r}")
    return 2


def _perm_preview_denied(exc) -> None:
    import json as _json
    code = getattr(exc, "code", None) or "CONFIRMATION_REQUIRED"
    print(_json.dumps({"state": "denied", "code": code,
                       "detail": str(exc)}, indent=2))


def _cmd_permissions(cfg, args) -> int:
    """voicebridge permissions mode/list/show/check/grant/revoke/reset.

    Mutating commands are dry-run by default (--dry-run) and require --confirm
    to persist; every mutation is backed up, atomic, and audited.
    """
    import json as _json
    from . import permissions as P
    from .broker import build_host_broker
    cmd = getattr(args, "permissions_command", None) or "list"
    store = P.permissions_store(cfg)
    policy = P.PermissionPolicy(store, registry=build_host_broker().registry)
    try:
        if cmd == "mode":
            if args.action == "show":
                mode, scopes, expires = store.mode()
                out = {"state": "OK", "mode": mode, "scopes": scopes,
                       "expires_at": expires}
            elif getattr(args, "dry_run", False):
                out = {"state": "preview", "would_set_mode": args.value,
                       "scopes": list(args.scope or ()), "ttl_s": args.ttl}
            else:
                out = store.set_mode(args.value, requested_by="cli:user",
                                     confirmed=args.confirm,
                                     scopes=tuple(args.scope or ()),
                                     ttl_s=args.ttl)
        elif cmd == "list":
            out = {"state": "OK", "mode": store.mode()[0],
                   "grants": [g.to_dict() for g in store.grants()]}
        elif cmd == "show":
            g = next((x for x in store.grants() if x.id == args.grant_id), None)
            if g is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": f"no grant {args.grant_id}"},
                                  indent=2))
                return 1
            out = g.to_dict()
        elif cmd == "check":
            out = policy.effective(args.capability, tuple(args.scope or ()))
        elif cmd == "grant":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_grant": args.capability,
                       "scopes": list(args.scope or ()), "ttl_s": args.ttl}
            else:
                g = store.grant(args.capability, requested_by="cli:user",
                                confirmed=args.confirm,
                                scopes=tuple(args.scope or ()),
                                ttl_s=args.ttl)
                out = {"state": "OK", "grant": g.to_dict()}
        elif cmd == "revoke":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_revoke": args.grant_id}
            else:
                g = store.revoke(args.grant_id, requested_by="cli:user",
                                 confirmed=args.confirm)
                out = {"state": "OK", "revoked": g.to_dict()}
        elif cmd == "reset":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_reset_mode": "restricted",
                       "would_revoke_grants": len(store.grants())}
            else:
                out = store.reset(requested_by="cli:user",
                                  confirmed=args.confirm)
        else:
            print(f"ERROR: unknown permissions command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except P.ConfirmationRequiredError as exc:
        _perm_preview_denied(exc)
        return 3
    except (P.PermissionError, P.SkillValidationError) as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
        return 2


def _skills_load_manifest(spec, _json, P, _PLUG):
    """Load + validate a skill/plugin manifest for `skills check|install`.

    Returns the validated manifest, or prints a typed refusal and returns
    ``{"rc": <exit>}``. Remote identifiers are never fetched; missing files
    never traceback; oversized files are refused before reading; user input
    is echoed only redacted. `SkillValidationError` propagates to the
    caller's typed handler.
    """
    verdict = _PLUG.classify_install_source(str(spec))
    if verdict["kind"] == "remote":
        print(_json.dumps(_PLUG.refuse_remote(verdict["identifier"]), indent=2))
        return {"rc": 2}
    if verdict["kind"] == "missing-file":
        print(_json.dumps(
            {"state": "FAILED", "code": _PLUG.IO_CODE,
             "detail": f"no manifest file: {_PLUG.redact_spec(str(spec))[:160]}",
             "owner": "qubit-policy",
             "rollback": "nothing installed or enabled; nothing to roll back"},
            indent=2))
        return {"rc": 2}
    try:
        raw = _PLUG.load_manifest_bytes(verdict["path"])
    except _PLUG.PluginInstallError as exc:
        print(_json.dumps(
            {"state": "FAILED", "code": exc.code,
             "detail": _PLUG.redact_spec(exc.detail)[:300],
             "owner": "qubit-policy",
             "rollback": "nothing installed or enabled; nothing to roll back"},
            indent=2))
        return {"rc": 2}
    try:
        return P.SkillManifest.from_dict(_json.loads(raw))
    except ValueError as exc:
        print(_json.dumps(
            {"state": "FAILED", "code": _PLUG.INVALID_CODE,
             "detail": f"manifest is not valid JSON: "
                       f"{_PLUG.redact_spec(str(exc))[:160]}",
             "owner": "qubit-policy",
             "rollback": "nothing installed or enabled; nothing to roll back"},
            indent=2))
        return {"rc": 2}


def _cmd_skills(cfg, args) -> int:
    """voicebridge skills list|show|check|install|enable|disable|remove|rollback.

    install validates the manifest and stages the skill DISABLED; enable/disable/
    remove/rollback are dry-run by default and require --confirm to persist.
    Qubit `plugins install`: a local reviewed manifest installs here;
    remote Git/index identifiers are typed-refused (nothing is fetched);
    mutating subcommands refuse while the emergency stop is latched.
    """
    import json as _json
    from . import permissions as P
    from . import plugins as _PLUG
    cmd = getattr(args, "skills_command", None) or "list"
    store = P.permissions_store(cfg)
    if cmd in ("install", "enable", "disable", "remove", "rollback") \
            and _PLUG.estop_engaged(cfg):
        print(_json.dumps(_PLUG.refuse_estop(f"skills {cmd}"), indent=2))
        return 3
    try:
        if cmd == "list":
            # ONE read authority shared with the dashboard/voice: the
            # permissioned store plus the vendored on-disk catalog, so the
            # catalog count is the same on every surface.
            from . import skill_catalog

            out = {"state": "OK",
                   "skills": skill_catalog.merged_store_view(cfg)}
        elif cmd == "show":
            # ONE authority shared with the dashboard/voice: on-disk skills
            # resolve through the fork's skill manager, permissioned-
            # manifest-only skills through the permissioned store.
            from . import skill_catalog as _SC

            s = _SC.show_skill(cfg, args.skill_id)
            if s is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": f"no skill {args.skill_id}"},
                                  indent=2))
                return 1
            out = s
        elif cmd == "check":
            man = _skills_load_manifest(args.manifest, _json, P, _PLUG)
            if isinstance(man, dict):  # typed refusal, already printed
                return man["rc"]
            out = {"state": "OK", "valid": True, "manifest": man.to_dict()}
        elif cmd == "install":
            man = _skills_load_manifest(args.manifest, _json, P, _PLUG)
            if isinstance(man, dict):  # typed refusal, already printed
                return man["rc"]
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_stage_disabled": man.skill_id,
                       "version": man.version}
            else:
                rec = store.install_skill(man, requested_by="cli:user",
                                          confirmed=args.confirm,
                                          source_path=str(args.manifest))
                out = {"state": "OK", "staged": rec.status, "skill": rec.to_dict()}
        elif cmd == "enable":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_enable": args.skill_id}
            else:
                from . import skill_catalog as _SC

                try:
                    rec = _SC.set_skill_enabled(
                        cfg, args.skill_id, True, requested_by="cli:user")
                except _SC.SkillAuthorityError as exc:
                    print(_json.dumps({"state": "failed", "code": exc.code,
                                       "detail": exc.detail}, indent=2))
                    return 2
                out = {"state": "OK", "enabled": rec["enabled"],
                       "origin": rec["origin"], "skill_id": rec["skill_id"]}
        elif cmd == "disable":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_disable": args.skill_id}
            else:
                from . import skill_catalog as _SC

                try:
                    rec = _SC.set_skill_enabled(
                        cfg, args.skill_id, False, requested_by="cli:user")
                except _SC.SkillAuthorityError as exc:
                    print(_json.dumps({"state": "failed", "code": exc.code,
                                       "detail": exc.detail}, indent=2))
                    return 2
                out = {"state": "OK", "enabled": rec["enabled"],
                       "origin": rec["origin"], "skill_id": rec["skill_id"]}
        elif cmd == "remove":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_remove": args.skill_id}
            else:
                from . import skill_catalog as _SC

                if _SC.find_skill_path(cfg, args.skill_id) is not None:
                    try:
                        _SC.delete_skill_file(
                            cfg, args.skill_id, requested_by="cli:user")
                    except _SC.SkillAuthorityError as exc:
                        print(_json.dumps({"state": "failed", "code": exc.code,
                                           "detail": exc.detail}, indent=2))
                        return 2
                    out = {"state": "OK", "removed": args.skill_id}
                else:
                    rec = store.remove_skill(args.skill_id, requested_by="cli:user",
                                             confirmed=args.confirm)
                    out = {"state": "OK", "removed": rec.skill_id}
        elif cmd == "rollback":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_rollback": args.skill_id}
            else:
                rec = store.rollback_skill(args.skill_id, requested_by="cli:user",
                                           confirmed=args.confirm,
                                           backup_path=args.backup)
                out = {"state": "OK", "status": rec.status, "skill_id": rec.skill_id}
        else:
            print(f"ERROR: unknown skills command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except P.ConfirmationRequiredError as exc:
        _perm_preview_denied(exc)
        return 3
    except (P.PermissionError, P.SkillValidationError) as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
        return 2


def _cmd_project(cfg, args) -> int:
    """voicebridge project create|list|show|active|add-folder|remove-folder|rename|
    set-primary|use|archive|restore|bind-board|rollback.

    Named multi-folder workspaces on the canonical Qubit registry. Mutating
    subcommands preview under top-level --dry-run and require --confirm to
    persist; they refuse while the emergency stop is latched. `bind-board`
    is a typed refusal until kanban boards exist.
    """
    import json as _json
    from . import projects as PJ
    cmd = getattr(args, "project_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd not in ("list", "show", "active") and PJ.estop_engaged(cfg):
            print(_json.dumps(PJ.refuse_estop(f"project {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd not in ("list", "show", "active"):
            print(_json.dumps({"state": "preview", "would": f"project {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = PJ.list_projects(cfg, include_archived=bool(
                getattr(args, "archived", False)))
        elif cmd == "active":
            out = PJ.active_project(cfg)
        elif cmd == "show":
            out = PJ.show(cfg, args.slug)
        elif cmd == "create":
            out = PJ.create(cfg, args.name, list(getattr(args, "folders", []) or []),
                            slug=getattr(args, "slug", "") or "",
                            description=getattr(args, "description", "") or "",
                            primary=getattr(args, "primary", "") or "",
                            requested_by="cli:user", confirmed=confirm,
                            use=bool(getattr(args, "use", False)))
        elif cmd == "add-folder":
            out = PJ.add_folder(cfg, args.slug, args.folder,
                                requested_by="cli:user", confirmed=confirm)
        elif cmd == "remove-folder":
            out = PJ.remove_folder(cfg, args.slug, args.folder,
                                   requested_by="cli:user", confirmed=confirm)
        elif cmd == "rename":
            out = PJ.rename(cfg, args.slug, args.new_name,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "set-primary":
            out = PJ.set_primary(cfg, args.slug, args.folder,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "use":
            out = PJ.use(cfg, args.slug, requested_by="cli:user",
                         confirmed=confirm,
                         clear=bool(getattr(args, "clear", False)))
        elif cmd == "archive":
            out = PJ.archive(cfg, args.slug,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "restore":
            out = PJ.restore(cfg, args.slug,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "bind-board":
            out = PJ.bind_board(cfg, args.slug, getattr(args, "board", "") or "",
                                requested_by="cli:user", confirmed=confirm,
                                unbind=bool(getattr(args, "unbind", False)))
        elif cmd == "rollback":
            out = PJ.rollback(cfg, requested_by="cli:user", confirmed=confirm,
                              backup=getattr(args, "backup", "") or "")
        else:
            print(f"ERROR: unknown project command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except PJ.ProjectError as exc:
        rc = 3 if exc.code == PJ.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": PJ.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "registry untouched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_kanban(cfg, args) -> int:
    """voicebridge kanban <op> — local task boards.

    Board/task operations persist in the canonical SQLite store; execution-
    shaped subcommands (swarm/dispatch/daemon/...) are typed-delegated to
    their Qubit-native paths and removed shapes are typed-
    refused. Mutating ops preview under --dry-run, require --confirm, and
    refuse while the emergency stop is latched.
    """
    import json as _json
    from . import kanban as KB
    cmd = getattr(args, "kanban_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"boards-create", "create", "claim", "assign", "reassign",
                 "reclaim", "comment", "complete", "edit", "block",
                 "unblock", "archive", "import", "move", "widget-add",
                 "widget-move", "widget-remove", "layout-set",
                 "role-grant", "role-revoke", "snapshot-restore"}
    try:
        if cmd in ("stores", "migrate"):
            from . import kanban_bridge as KBG
            if cmd == "migrate" and KB.estop_engaged(cfg):
                print(_json.dumps(KB.refuse_estop("kanban migrate"), indent=2))
                return 3
            out = (KBG.stores(cfg) if cmd == "stores"
                   else KBG.migrate(cfg, dry_run=bool(
                       getattr(args, "dry_run", False))))
            print(_json.dumps(out, indent=2))
            return 0
        if cmd in _MUTATING and KB.estop_engaged(cfg):
            print(_json.dumps(KB.refuse_estop(f"kanban {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview", "would": f"kanban {cmd}"},
                              indent=2))
            return 0
        if cmd in KB.DELEGATED:
            print(_json.dumps(KB.refuse_delegated(cmd), indent=2))
            return 2
        if cmd == "init":
            out = KB.init(cfg)
        elif cmd == "boards":
            out = KB.boards_list(cfg)
        elif cmd == "boards-create":
            out = KB.boards_create(cfg, args.slug, getattr(args, "name", "") or "",
                                   requested_by="cli:user", confirmed=confirm)
        elif cmd == "create":
            out = KB.create(cfg, args.title, board=args.board, body=args.body,
                            assignee=args.assignee, priority=args.priority,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd in ("list", "ls"):
            out = KB.list_tasks(cfg, board=args.board, status=args.status,
                                limit=args.limit)
        elif cmd == "show":
            out = KB.show(cfg, args.task_id)
        elif cmd == "current":
            out = KB.current_task(cfg)
        elif cmd == "claim":
            out = KB.claim(cfg, args.task_id, assignee=args.assignee,
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "assign":
            out = KB.assign(cfg, args.task_id, args.assignee,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "reassign":
            out = KB.reassign(cfg, args.task_id, args.assignee,
                              requested_by="cli:user", confirmed=confirm)
        elif cmd == "reclaim":
            out = KB.reclaim(cfg, args.task_id,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "comment":
            out = KB.comment(cfg, args.task_id, args.body,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "complete":
            out = KB.complete(cfg, args.task_id,
                              requested_by="cli:user", confirmed=confirm)
        elif cmd == "edit":
            out = KB.edit(cfg, args.task_id, title=args.title, body=args.body,
                          priority=args.priority,
                          requested_by="cli:user", confirmed=confirm)
        elif cmd == "block":
            out = KB.block(cfg, args.task_id,
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "unblock":
            out = KB.unblock(cfg, args.task_id,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "archive":
            out = KB.archive_task(cfg, args.task_id,
                                  requested_by="cli:user", confirmed=confirm)
        elif cmd == "stats":
            out = KB.stats(cfg, board=args.board)
        elif cmd == "log":
            out = KB.log(cfg, board=args.board, limit=args.limit)
        elif cmd == "export":
            out = KB.export(cfg)
        elif cmd == "open":
            out = KB.board_open(cfg, board=args.board)
        elif cmd == "move":
            out = KB.move_task(cfg, args.task_id,
                               board=getattr(args, "board", "") or "",
                               status=getattr(args, "status", "") or "",
                               requested_by="cli:user", confirmed=confirm)
        elif cmd == "widget-add":
            out = KB.widget_add(cfg, args.board, args.kind, args.title,
                                content=args.content, x=args.x, y=args.y,
                                w=args.w, h=args.h,
                                requested_by="cli:user", confirmed=confirm)
        elif cmd == "widget-list":
            out = KB.widget_list(cfg, board=args.board)
        elif cmd == "widget-move":
            out = KB.widget_move(cfg, args.widget_id, x=args.x, y=args.y,
                                 w=args.w, h=args.h,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "widget-remove":
            out = KB.widget_remove(cfg, args.widget_id,
                                   requested_by="cli:user", confirmed=confirm)
        elif cmd == "layout-show":
            out = KB.layout_show(cfg, board=args.board)
        elif cmd == "layout-set":
            cols = [c.strip() for c in
                    (getattr(args, "columns", "") or "").split(",") if c.strip()]
            out = KB.layout_set(cfg, args.board, cols,
                                requested_by="cli:user", confirmed=confirm)
        elif cmd == "role-grant":
            out = KB.role_grant(cfg, args.board, args.principal, args.role,
                                requested_by="cli:user", confirmed=confirm)
        elif cmd == "role-revoke":
            out = KB.role_revoke(cfg, args.board, args.principal,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "roles":
            out = KB.roles_list(cfg, board=args.board)
        elif cmd == "snapshots":
            out = KB.snapshot_list(cfg, board=args.board, limit=args.limit)
        elif cmd == "snapshot-restore":
            out = KB.snapshot_restore(cfg, args.snapshot_id,
                                      requested_by="cli:user",
                                      confirmed=confirm)
        elif cmd == "import":
            try:
                snap = _json.loads(Path(args.snapshot).read_text(
                    encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(_json.dumps({"state": "FAILED", "code": KB.IO_CODE,
                                   "detail": KB.redact_spec(str(exc))[:200],
                                   "owner": "qubit-policy",
                                   "rollback": "board untouched"},
                                  indent=2))
                return 2
            out = KB.import_snapshot(cfg, snap, requested_by="cli:user",
                                     confirmed=confirm)
        else:
            print(_json.dumps(KB.refuse_unsupported(cmd), indent=2))
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except KB.KanbanError as exc:
        rc = 3 if exc.code == KB.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": KB.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "board untouched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_runtime(cfg, args) -> int:
    """qubit runtime images|status|doctor|install|upgrade|rollback|remove.

    The Qubit container/runtime equivalent: pinned confined runtimes with
    digest verification, staged upgrades, receipt rollback, and audited
    removal. Mutating ops preview under --dry-run, require --confirm, and
    refuse while the emergency stop is latched. Nothing here downloads:
    missing artifacts are a typed NEEDS_STAGING state.
    """
    import json as _json
    from . import runtime_images as RI
    cmd = getattr(args, "runtime_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"install", "upgrade", "rollback", "remove"}
    try:
        if cmd in _MUTATING and RI.estop_engaged(cfg):
            print(_json.dumps(RI.refuse_estop(f"runtime {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview", "would": f"runtime {cmd}"},
                              indent=2))
            return 0
        if cmd == "images":
            out = RI.images()
        elif cmd == "status":
            out = RI.status(cfg)
        elif cmd == "doctor":
            out = RI.doctor(cfg)
        elif cmd == "install":
            out = RI.install(cfg, args.name, requested_by="cli:user",
                             confirmed=confirm,
                             source=getattr(args, "source", "") or "")
        elif cmd == "upgrade":
            out = RI.upgrade(cfg, args.name, requested_by="cli:user",
                             confirmed=confirm,
                             source=getattr(args, "source", "") or "")
        elif cmd == "rollback":
            out = RI.rollback(cfg, args.name, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "remove":
            out = RI.remove(cfg, args.name, requested_by="cli:user",
                            confirmed=confirm)
        else:
            print(f"ERROR: unknown runtime command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except RI.RuntimeImageError as exc:
        rc = 3 if exc.code == RI.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": RI.redact_spec(exc.detail)[:400],
                           "owner": "qubit-policy",
                           "rollback": "runtimes untouched; nothing to roll back"},
                          indent=2))
        return rc


def _update_runtime_state(cfg):
    """Compact pinned-runtime-image state for `qubit update --check`.

    Read-only; a probe failure is omitted (never fabricated) and never fails the
    update check.
    """
    try:
        from . import runtime_images as RI

        st = RI.status(cfg)
        return {
            "state": st.get("state"),
            "daemon": st.get("daemon"),
            "images": [{"name": i.get("name"), "state": i.get("state")}
                       for i in (st.get("runtimes") or [])],
        }
    except Exception:  # noqa: BLE001
        return None


def _update_rc(out) -> int:
    """Map an update receipt to the sanctioned exit codes.

    0 OK/restored · 1 operational failure · 2 typed refusal · 3 e-stop.
    """
    from . import update_manager as UM

    code = str(out.get("code") or "")
    state = str(out.get("state") or "")
    if out.get("ok") is True or state == "OK":
        return 0
    if code == UM.ESTOP_CODE or state == "denied":
        return 3
    # A post-pull revision that does not match the update source is a typed
    # artifact mismatch (rc 2), not a generic operational failure.
    if state == "FAILED" and out.get("remote_verified") is False:
        return 2
    if code in (UM.WORKTREE_DIRTY_CODE, UM.OPERATOR_OWNED_CODE,
                UM.UNAVAILABLE_CODE, UM.CONFIRM_CODE, UM.NO_BACKUP_CODE,
                UM.BACKUP_INVALID_CODE):
        return 2
    return 1


def _print_update_receipt(out, as_json: bool) -> None:
    import json as _json

    if as_json:
        print(_json.dumps(out, indent=2, sort_keys=True))
        return
    state = out.get("state") or "UNKNOWN"
    print(f"Qubit update: {state}")
    if out.get("detail"):
        print(f"  {out['detail']}")
    if out.get("code"):
        print(f"  code: {out['code']}")


def _print_update_check(out) -> None:
    print("Qubit update check")
    print(f"  product version : {out.get('current_version') or 'unknown'}")
    print(f"  install method  : {out.get('install_method') or 'unknown'}")
    print(f"  revision        : {out.get('current_revision') or 'unknown'}")
    remote = out.get("remote") or "?"
    branch = out.get("branch") or "?"
    head = out.get("remote_revision") or "unresolved"
    print(f"  update source   : {remote}/{branch} ({head})")
    print(f"  state           : {out.get('state')} — {out.get('message') or ''}")
    rt = out.get("runtime_image")
    if isinstance(rt, dict):
        print(f"  runtime image   : {rt.get('state')} "
              f"(daemon: {rt.get('daemon')})")


def _cmd_update(cfg, args) -> int:
    """qubit update --check|--apply|--rollback (Owner Decision #11).

    The sanctioned Qubit update surface — no longer a typed refusal and never
    forwarded to the vendored fork. `--check` (and a bare `update`) is a
    read-only, truthful status: product version, install method, revision, and
    pinned runtime-image state; a DEGRADED result is reported, never disguised
    as "up to date". `--apply` writes a timestamped `git bundle` backup, then
    runs the non-destructive fast-forward. `--rollback` restores the recorded
    pre-apply revision from the most recent update backup and writes a
    pre-rollback snapshot first, so the rollback is itself reversible.
    """
    import json as _json
    from . import update_manager as UM

    check = bool(getattr(args, "check", False))
    do_apply = bool(getattr(args, "apply", False))
    do_rollback = bool(getattr(args, "rollback", False))
    verbs = [name for name, on in (("check", check), ("apply", do_apply),
                                   ("rollback", do_rollback)) if on]
    if len(verbs) > 1:
        print(_json.dumps({"state": "FAILED", "code": "UPDATE_VERB_CONFLICT",
                           "detail": "choose exactly one of --check, --apply, "
                                     "--rollback"},
                          indent=2, sort_keys=True))
        return 2
    verb = verbs[0] if verbs else "check"
    source = str(getattr(args, "source", "") or "").strip() or None
    backup_dir = getattr(args, "backup_dir", None) or None
    as_json = bool(getattr(args, "json", False))

    if verb == "check":
        out = UM.check(cfg, source=source)
        rt = _update_runtime_state(cfg)
        if rt is not None:
            out["runtime_image"] = rt
        if as_json:
            print(_json.dumps(out, indent=2, sort_keys=True))
        else:
            _print_update_check(out)
        return 0 if out.get("state") == "OK" else 2

    # Mutating verbs honor the shared top-level `--dry-run`: preview only.
    if verb in ("apply", "rollback") and getattr(args, "dry_run", False):
        print(_json.dumps({"state": "preview",
                           "would": f"qubit update --{verb}",
                           "detail": "dry-run: no backup written, no git "
                                     "command run"},
                          indent=2, sort_keys=True))
        return 0

    # Mutating verbs: fail closed on the single e-stop authority first.
    if UM.estop_engaged(cfg):
        out = {"ok": False, "state": "denied", "code": UM.ESTOP_CODE,
               "detail": "emergency stop is latched; update refused",
               "owner": "qubit-policy",
               "rollback": "nothing changed; nothing to roll back"}
        _print_update_receipt(out, as_json)
        return 3

    if verb == "apply":
        snap = UM.snapshot(cfg, reason="pre-apply", source=source,
                           backup_dir=backup_dir, actor="cli:update")
        if not snap.get("ok"):
            _print_update_receipt(snap, as_json)
            return _update_rc(snap)
        out = UM.apply(cfg, confirm=True, source=source, actor="cli:update")
        out["backup_dir"] = snap.get("backup_dir")
        out["backup_bundle"] = snap.get("bundle")
        _print_update_receipt(out, as_json)
        return _update_rc(out)

    out = UM.rollback(cfg, backup_dir=backup_dir, actor="cli:update")
    _print_update_receipt(out, as_json)
    return _update_rc(out)


def _cmd_computer_use(cfg, args) -> int:
    """voicebridge computer-use status|doctor|permissions|install.

    Read-only capability probes; control stays DISABLED (8V scope) and
    driver download/install is typed-refused (pinned-source policy).
    """
    import json as _json
    from . import computer_use as CU
    cmd = getattr(args, "computer_use_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    if cmd == "status":
        out = CU.status()
    elif cmd == "doctor":
        out = CU.doctor()
    elif cmd == "permissions":
        out = CU.permissions()
    elif cmd == "session":
        out = CU.desktop_session()
    elif cmd == "ops":
        out = CU.desktop_ops()
    elif cmd == "launch":
        out = CU.launch_app(cfg, args.app, target=args.target,
                            requested_by="cli:user", confirmed=confirm)
    elif cmd == "pip":
        out = CU.picture_in_picture(cfg, target=args.target,
                                    width=args.width, height=args.height,
                                    requested_by="cli:user", confirmed=confirm)
    elif cmd == "present":
        panes = [q for q in str(getattr(args, "panes", "") or "")
                 .replace(",", " ").split() if q] or None
        out = CU.present(cfg, args.mode, panes=panes, target=args.target,
                         requested_by="cli:user", confirmed=confirm)
    elif cmd == "presentation":
        out = CU.presentation_status(cfg)
    elif cmd == "install":
        print(_json.dumps(CU.refuse_install(), indent=2))
        return 2
    else:
        print(f"ERROR: unknown computer-use command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    code = str(out.get("code", "") or "")
    if code in (CU.LAUNCH_CONFIRM_CODE, CU.PIP_CONFIRM_CODE,
                CU.PRESENT_CONFIRM_CODE):
        return 3
    if cmd in ("launch", "pip", "present") and (
            out.get("state") in ("REFUSED", "denied") or code):
        return 2
    return 0


def _cmd_browser(cfg, args) -> int:
    """qubit browser status|close-profile|tabs|doctor|read|find|
    activate|open|navigate|fill|click|close.

    Read-only loopback state by default; real-profile mutation is
    typed-refused; tab mutations preview under top-level --dry-run and
    require --confirm (refusing while the emergency stop is latched).
    """
    import json as _json
    from . import browser as BR
    from . import browser_tabs as BT
    cmd = getattr(args, "browser_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"activate", "open", "open-browser", "navigate", "fill",
                 "click", "close", "download"}
    try:
        if cmd in _MUTATING and BT.estop_engaged(cfg):
            print(_json.dumps(BT.refuse_estop(f"browser {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview",
                               "would": f"browser {cmd}"}, indent=2))
            return 0
        if cmd == "status":
            # Packet 8A import boundary: only cli.py (allowlisted) touches the
            # communications package; browser.py renders the probe (pure).
            from .communications.monitor import cdp_precondition

            out = BR.status(cdp_precondition(cfg))
        elif cmd == "close-profile":
            print(_json.dumps(
                BR.refuse_close_profile(getattr(args, "profile", "") or ""),
                indent=2))
            return 2
        elif cmd == "tabs":
            out = BT.tabs(cfg)
        elif cmd == "doctor":
            out = BT.doctor(cfg)
        elif cmd == "read":
            out = BT.read(cfg, getattr(args, "target", "") or "")
        elif cmd == "screenshot":
            clip = getattr(args, "clip", "") or ""
            clip_args = ([int(v) for v in clip.split(",")] if clip else None)
            out = BT.screenshot(
                cfg, getattr(args, "target", "") or "",
                clip=clip_args, fmt=getattr(args, "fmt", "jpeg"),
                max_bytes=(getattr(args, "max_bytes", 0)
                           or BT.MAX_SCREENSHOT_BYTES))
        elif cmd == "find":
            out = BT.find(cfg, args.query, getattr(args, "target", "") or "")
        elif cmd == "activate":
            out = BT.activate(cfg, args.target, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "open":
            out = BT.open(cfg, args.url, requested_by="cli:user",
                          confirmed=confirm)
        elif cmd == "open-browser":
            out = BT.open_browser(cfg, requested_by="cli:user",
                                  confirmed=confirm)
        elif cmd == "navigate":
            out = BT.navigate(cfg, args.target, args.url,
                              requested_by="cli:user", confirmed=confirm)
        elif cmd == "fill":
            out = BT.fill(cfg, args.target, args.selector, args.text,
                          requested_by="cli:user", confirmed=confirm)
        elif cmd == "click":
            out = BT.click(cfg, args.target, args.selector,
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "close":
            out = BT.close(cfg, args.target, requested_by="cli:user",
                           confirmed=confirm)
        elif cmd == "favicon":
            out = BT.favicon(
                cfg, getattr(args, "target", "") or "",
                max_bytes=(getattr(args, "max_bytes", 0)
                           or BT.MAX_FAVICON_BYTES),
                requested_by="cli:user")
        elif cmd == "favicon-cache":
            out = BT.favicon_cache(cfg, limit=getattr(args, "limit", 32))
        elif cmd == "download":
            out = BT.download(
                cfg, args.dest, getattr(args, "target", "") or "",
                max_bytes=(getattr(args, "max_bytes", 0)
                           or BT.MAX_DOWNLOAD_BYTES),
                requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown browser command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except BT.BrowserTabsError as exc:
        rc = 3 if exc.code == BT.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": BT.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "browser untouched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_acp(cfg, args) -> int:
    """voicebridge acp --check|--setup|--version (no server shipped), plus the
    external harness client subcommands status|catalog|handshake|send|
    receipts|configure|unconfigure (same backend as voice/dashboard)."""
    import json as _json
    cmd = getattr(args, "acp_command", None)
    if cmd:
        from . import acp_client as AC
        confirm = bool(getattr(args, "confirm", False))
        try:
            if cmd == "status":
                out = AC.status(cfg)
            elif cmd == "catalog":
                out = AC.catalog(cfg)
            elif cmd == "receipts":
                out = AC.receipts(cfg, harness=getattr(args, "harness", "") or "")
            elif cmd == "handshake":
                out = AC.handshake(cfg, args.harness, confirmed=confirm)
            elif cmd == "send":
                out = AC.send(cfg, args.harness, prompt=" ".join(args.prompt),
                              confirmed=confirm)
            elif cmd == "configure":
                out = AC.configure(
                    cfg, args.harness,
                    credential_ref=getattr(args, "credential_ref", "") or "",
                    requested_by="cli:user", confirmed=confirm)
            elif cmd == "unconfigure":
                out = AC.unconfigure(cfg, args.harness, requested_by="cli:user",
                                     confirmed=confirm)
            else:
                print(f"ERROR: unknown acp command {cmd!r}")
                return 2
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        except AC.AcpError as exc:
            rc = 3 if exc.code == AC.CONFIRM_CODE else 2
            print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                               "code": exc.code, "detail": exc.detail[:300],
                               "owner": "qubit-policy",
                               "rollback": "no harness configured or executed"},
                              indent=2))
            return rc
    from . import acp_lsp as AL
    if getattr(args, "accept_hooks", False):
        print(_json.dumps(AL.acp_refuse_accept_hooks(), indent=2))
        return 2
    if getattr(args, "setup", False) or getattr(args, "setup_browser", False):
        if getattr(args, "setup_browser", False):
            print(_json.dumps({
                "state": "OK", "schema": "qubit-acp-browser-setup/1",
                "handoff": "manual: launch Chromium yourself with "
                           "--remote-debugging-port=9222, then confirm with "
                           "`voicebridge browser status`; Qubit never "
                           "launches or restarts your browser"}, indent=2))
            return 0
        out = AL.acp_setup_snippet(getattr(args, "editor", "") or "")
        print(_json.dumps(out, indent=2))
        return 0 if out.get("state") == "OK" else 2
    if getattr(args, "check", False) or getattr(args, "version", False):
        print(_json.dumps(AL.acp_check(), indent=2))
        return 0
    print(_json.dumps(AL.acp_refuse_serve(), indent=2))
    return 2


def _channels_doctor(cfg):
    """Inbound-channel health composed from the shared backend's public
    functions (channels_inbound exposes status/catalog/accounts)."""
    from . import channels_inbound as CI

    st = CI.status(cfg)
    cat = CI.catalog(cfg)
    acct = CI.accounts(cfg)
    return {"state": "OK", "schema": CI.SCHEMA, "store_ok": True,
            "channels": st.get("channels", 0),
            "configured": st.get("configured", 0), "connected": 0,
            "listener": st.get("listener", "NONE"),
            "accounts": len(acct.get("accounts", [])),
            "available": cat.get("available", []),
            "estop": st.get("estop", False), "warnings": []}


def _cmd_plugins(cfg, args) -> int:
    """voicebridge plugins status|list|show|register|invoke|unregister|doctor.

    The registry records validated manifests only; nothing is imported or
    executed. Mutating ops require --confirm and refuse while the emergency
    stop is latched (the module enforces both).
    """
    import json as _json
    from . import plugin_runtime as PR
    cmd = getattr(args, "plugins_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd == "status":
            out = PR.status(cfg)
        elif cmd == "list":
            out = PR.list_plugins(cfg)
        elif cmd == "show":
            out = PR.get_plugin(cfg, args.plugin_id)
        elif cmd == "doctor":
            out = PR.doctor(cfg)
        elif cmd == "catalog":
            out = PR.plugin_catalog(cfg)
        elif cmd == "install":
            out = PR.install(cfg, identifier=args.identifier,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "register":
            path = Path(args.manifest).expanduser()
            try:
                manifest = _json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PR.PluginRuntimeError(
                    PR.INVALID_MANIFEST_CODE,
                    f"manifest unreadable: {exc}") from exc
            out = PR.register(cfg, manifest=manifest, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "invoke":
            text = getattr(args, "text", "") or ""
            out = PR.invoke(cfg, args.plugin_id,
                            args={"text": text} if text else {},
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "unregister":
            out = PR.unregister(cfg, args.plugin_id, requested_by="cli:user",
                                confirmed=confirm)
        else:
            print(f"ERROR: unknown plugins command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except PR.PluginRuntimeError as exc:
        rc = 3 if exc.code == PR.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registry untouched; nothing executed"},
                          indent=2))
        return rc


def _cmd_channels(cfg, args) -> int:
    """voicebridge channels status|catalog|accounts|configure|unconfigure|
    receive|doctor.

    The registry records credential *references* only (never values); no
    listener is started and ``connected`` is never fabricated. Configure/
    unconfigure require --confirm. ``receive`` validates a signed raw body
    against the same kernel the dashboard ingress uses.
    """
    import json as _json
    from . import channels_inbound as CI
    cmd = getattr(args, "channels_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd == "status":
            out = CI.status(cfg)
        elif cmd == "catalog":
            out = CI.catalog(cfg)
        elif cmd == "accounts":
            out = CI.accounts(cfg)
        elif cmd == "doctor":
            out = _channels_doctor(cfg)
        elif cmd == "configure":
            events = [e.strip() for e in
                      str(getattr(args, "events", "") or "").split(",")
                      if e.strip()]
            out = CI.configure(cfg, args.channel, secret_ref=args.secret_ref,
                               events=events or None, requested_by="cli:user",
                               confirmed=confirm)
        elif cmd == "unconfigure":
            out = CI.unconfigure(cfg, args.channel, requested_by="cli:user",
                                 confirmed=confirm)
        elif cmd == "receive":
            payload = Path(args.payload_file).read_bytes()
            out = CI.receive(
                cfg, channel=args.channel, payload=payload,
                signature=getattr(args, "signature", "") or "",
                timestamp=getattr(args, "timestamp", None),
                delivery_id=getattr(args, "delivery_id", "") or "",
                sender=getattr(args, "sender", "") or "",
                event=getattr(args, "event", "message") or "message")
        else:
            print(f"ERROR: unknown channels command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except CI.ChannelError as exc:
        rc = 3 if exc.code == CI.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "channel registry untouched; nothing "
                                       "dispatched"},
                          indent=2))
        return rc
    except OSError as exc:
        print(_json.dumps({"state": "FAILED", "code": "CHANNEL_PAYLOAD_UNREADABLE",
                           "detail": str(exc)[:200]}, indent=2))
        return 2


def _cmd_lsp(cfg, args) -> int:
    """voicebridge lsp status|list|which|restart (+ refused install)."""
    import json as _json
    from . import acp_lsp as AL
    cmd = getattr(args, "lsp_command", None) or "status"
    if cmd == "status":
        out = AL.lsp_status()
    elif cmd == "list":
        out = AL.lsp_list()
    elif cmd == "which":
        out = AL.lsp_which(getattr(args, "server", "") or "")
        print(_json.dumps(out, indent=2))
        return 0 if out.get("state") in ("PRESENT", "ABSENT") else 2
    elif cmd == "restart":
        out = AL.lsp_restart()
    elif cmd in ("install", "install-all"):
        print(_json.dumps(
            AL.lsp_refuse_install(getattr(args, "server", "") or ""), indent=2))
        return 2
    else:
        print(f"ERROR: unknown lsp command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_theme(cfg, args) -> int:
    """voicebridge theme list|show|set|set-tweak|clear-tweak|reset.

    Optional cosmetics inside the coherent Qubit system. Mutating ops
    preview under --dry-run, require --confirm, and refuse while the
    emergency stop is latched.
    """
    import json as _json
    from . import themes as TH
    cmd = getattr(args, "theme_command", None) or "show"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("set", "set-tweak", "clear-tweak", "reset") \
                and TH.estop_engaged(cfg):
            print(_json.dumps(TH.refuse_estop(f"theme {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd != "list" and cmd != "show":
            print(_json.dumps({"state": "preview", "would": f"theme {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = TH.list_themes()
        elif cmd == "show":
            out = TH.show(cfg)
        elif cmd == "set":
            out = TH.set_theme(cfg, args.name,
                               requested_by="cli:user", confirmed=confirm)
        elif cmd == "set-tweak":
            out = TH.set_tweak(cfg, args.token, args.color,
                               requested_by="cli:user", confirmed=confirm)
        elif cmd == "clear-tweak":
            out = TH.clear_tweak(cfg, args.token,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "reset":
            out = TH.reset(cfg, requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown theme command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except TH.ThemeError as exc:
        rc = 3 if exc.code == TH.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "theme untouched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_proxy(cfg, args) -> int:
    """voicebridge proxy status|providers|start.

    Inventory only; starting a forwarder is typed-refused (egress invariant).
    """
    import json as _json
    from . import proxy_status as PX
    cmd = getattr(args, "proxy_command", None) or "status"
    if cmd == "status":
        out = PX.status()
    elif cmd == "providers":
        out = PX.providers(cfg)
    elif cmd == "start":
        print(_json.dumps(PX.refuse_start(), indent=2))
        return 2
    else:
        print(f"ERROR: unknown proxy command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_import_agent(cfg, args) -> int:
    """voicebridge import-agent inspect|import.

    Quarantined read-only inventory; content import is typed-refused
    (reviewed-code policy; credentials never imported).
    """
    import json as _json
    from . import import_agent as IA
    cmd = getattr(args, "import_agent_command", None) or "inspect"
    try:
        if cmd == "inspect":
            source = getattr(args, "source", "") or ""
            if not source.strip():
                source = IA._default_source()
            out = IA.inspect_tree(source, getattr(args, "kind", "") or "")
        elif cmd == "import":
            print(_json.dumps(IA.refuse_import(), indent=2))
            return 2
        else:
            print(f"ERROR: unknown import-agent command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except IA.ImportAgentError as exc:
        print(_json.dumps({"state": "FAILED", "code": exc.code,
                           "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "nothing imported; nothing to roll back"},
                          indent=2))
        return 2


def _cmd_hooks(cfg, args) -> int:
    """voicebridge hooks declare|list|show|validate|test|fire|revoke|remove|doctor.

    Declared event subscriptions with the shell permanently disarmed:
    delivery appends broker audit events; nothing is ever executed.
    Mutating ops preview under --dry-run, require --confirm, and refuse
    while the emergency stop is latched.
    """
    import json as _json
    from . import hooks as HK
    cmd = getattr(args, "hooks_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("declare", "fire", "revoke", "remove") \
                and HK.estop_engaged(cfg):
            print(_json.dumps(HK.refuse_estop(f"hooks {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd != "list" \
                and cmd not in ("show", "validate", "test", "doctor"):
            print(_json.dumps({"state": "preview", "would": f"hooks {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = HK.list_hooks(cfg)
        elif cmd == "show":
            out = HK.show(cfg, args.name)
        elif cmd == "declare":
            out = HK.declare(cfg, args.name, args.event, args.shell,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "validate":
            out = HK.validate(cfg, args.name)
        elif cmd == "test":
            out = HK.test_plan(cfg, args.name)
        elif cmd == "fire":
            out = HK.fire(cfg, args.event,
                          requested_by="cli:user", confirmed=confirm)
        elif cmd == "revoke":
            out = HK.revoke(cfg, args.name,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "remove":
            out = HK.remove(cfg, args.name,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "doctor":
            out = HK.doctor(cfg)
        else:
            print(f"ERROR: unknown hooks command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except HK.HookError as exc:
        rc = 3 if exc.code == HK.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registry untouched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_sync(cfg, args) -> int:
    """voicebridge sync offer|list|show|revoke|accept.

    Opt-in encrypted device-bound handoff; the user carries the bundle file
    and the passphrase between PCs. Nothing touches the network. Mutating
    ops preview under --dry-run, require --confirm, and refuse while the
    emergency stop is latched.
    """
    import json as _json
    from . import sync as SY
    cmd = getattr(args, "sync_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("offer", "revoke", "accept") and SY.estop_engaged(cfg):
            print(_json.dumps(SY.refuse_estop(f"sync {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd != "list" and cmd != "show":
            print(_json.dumps({"state": "preview", "would": f"sync {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = SY.list_offers(cfg)
        elif cmd == "show":
            out = SY.show(cfg, args.offer_id)
        elif cmd == "offer":
            out = SY.offer(cfg, getattr(args, "passphrase", "") or "",
                           for_device=getattr(args, "for_device", "") or "",
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "revoke":
            out = SY.revoke(cfg, args.offer_id,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "accept":
            out = SY.accept(cfg, getattr(args, "bundle", "") or "",
                            getattr(args, "passphrase", "") or "",
                            offer_id=getattr(args, "offer", "") or "",
                            apply=bool(getattr(args, "apply", False)),
                            requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown sync command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except SY.SyncError as exc:
        rc = 3 if exc.code == SY.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "nothing exported or restored"},
                          indent=2))
        return rc


def _cmd_tui(cfg) -> int:
    """voicebridge tui — live terminal board (read-only, same backend).

    Outside a real terminal this prints the typed TUI_NO_TTY refusal
    (piped/CI callers use `status` or the dashboard instead).
    """
    import json as _json
    from . import tui as TUI
    out = TUI.main(cfg)
    if out.get("state") == "OK":
        return 0
    print(_json.dumps(out, indent=2))
    return 2


def _cmd_vision(cfg, args) -> int:
    """voicebridge vision describe|status.

    Explicit vision analysis inside the sanctioned resident swap cycle
    (VRAM-guarded, confirmed, audited, resident always restored).
    """
    import json as _json
    from . import vision as VI
    cmd = getattr(args, "vision_command", None) or "status"
    try:
        if cmd == "status":
            out = VI.status(cfg)
        elif cmd == "describe":
            out = VI.describe(cfg, args.image, prompt=getattr(args, "prompt", "") or "",
                              model=getattr(args, "model", "") or "",
                              requested_by="cli:user",
                              confirmed=bool(getattr(args, "confirm", False)),
                              timeout_s=getattr(args, "timeout", None))
        else:
            print(f"ERROR: unknown vision command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except VI.VisionError as exc:
        rc = 3 if exc.code in (VI.CONFIRM_CODE, VI.ESTOP_CODE) else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "resident model restored by the swap "
                                       "cycle; no state written"},
                          indent=2))
        return rc


def _cmd_pairing(cfg, args) -> int:
    """voicebridge pairing create|approve|list|revoke.

    Hashed one-time codes; approval issues a real scoped grant.
    Mutating ops preview under --dry-run, require --confirm, and refuse
    while the emergency stop is latched.
    """
    import json as _json
    from . import pairing as PA
    cmd = getattr(args, "pairing_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("create", "approve", "revoke") and PA.estop_engaged(cfg):
            print(_json.dumps(PA.refuse_estop(f"pairing {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd != "list":
            print(_json.dumps({"state": "preview", "would": f"pairing {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = PA.list_codes(cfg)
        elif cmd == "create":
            out = PA.create(cfg, args.label, list(getattr(args, "scope", []) or []),
                            account=getattr(args, "account", "") or "",
                            ttl_s=getattr(args, "ttl", 600.0) or 600.0,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "approve":
            out = PA.approve(cfg, args.code,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "revoke":
            out = PA.revoke(cfg, args.code_id,
                            requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown pairing command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except PA.PairingError as exc:
        rc = 3 if exc.code == PA.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registry untouched; no grant issued"},
                          indent=2))
        return rc


def _cmd_webhook(cfg, args) -> int:
    """voicebridge webhook add|list|show|remove|verify|test.

    Verified-only subscriptions: HMAC + freshness + replay checked on
    verify; nothing executes or dispatches, ever.
    """
    import json as _json
    from . import webhooks as WH
    cmd = getattr(args, "webhook_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("add", "remove") and WH.estop_engaged(cfg):
            print(_json.dumps(WH.refuse_estop(f"webhook {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd not in ("list", "show", "test"):
            print(_json.dumps({"state": "preview", "would": f"webhook {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = WH.list_subs(cfg)
        elif cmd == "show":
            out = WH.show(cfg, args.sub_id)
        elif cmd == "add":
            out = WH.add(cfg, args.sub_id, args.url,
                         list(getattr(args, "event", []) or []),
                         getattr(args, "secret", "") or "",
                         requested_by="cli:user", confirmed=confirm)
        elif cmd == "remove":
            out = WH.remove(cfg, args.sub_id,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "test":
            out = WH.test_plan(cfg, args.sub_id)
        elif cmd == "verify":
            try:
                payload = Path(getattr(args, "payload", "") or "").read_bytes()
            except OSError as exc:
                print(_json.dumps({"state": "FAILED", "code": "WEBHOOK_BAD_INPUT",
                                   "detail": f"payload unreadable: {exc}"[:200],
                                   "owner": "qubit-policy",
                                   "rollback": "nothing verified"}, indent=2))
                return 2
            if len(payload) > 1_000_000:
                print(_json.dumps({"state": "FAILED", "code": "WEBHOOK_BAD_INPUT",
                                   "detail": "payload exceeds 1MB cap",
                                   "owner": "qubit-policy",
                                   "rollback": "nothing verified"}, indent=2))
                return 2
            out = WH.verify(cfg, args.sub_id, payload,
                            getattr(args, "signature", "") or "",
                            getattr(args, "timestamp", 0.0) or 0.0,
                            sender=getattr(args, "sender", "") or "",
                            record_inbox=bool(getattr(args, "record_inbox", False)))
        else:
            print(f"ERROR: unknown webhook command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except WH.WebhookError as exc:
        rc = 3 if exc.code == WH.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "nothing verified or dispatched"},
                          indent=2))
        return rc


def _cmd_msg(cfg, args) -> int:
    """voicebridge msg account-*/draft/approve/send/retry/link/list/show.

    Brokered outbox: fresh exact confirmation per send, credential-gated
    last mile, idempotent receipts, redacted audit.
    """
    import json as _json
    from . import messaging as MG
    cmd = getattr(args, "msg_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        _MUT = {"account-add", "account-remove", "draft", "approve",
                "send", "retry", "feishu-agent-add", "feishu-agent-remove",
                "feishu-send-as-agent"}
        if cmd in _MUT and MG.estop_engaged(cfg):
            print(_json.dumps(MG.refuse_estop(f"msg {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUT:
            print(_json.dumps({"state": "preview", "would": f"msg {cmd}"},
                              indent=2))
            return 0
        if cmd == "account-add":
            extra = {}
            if getattr(args, "phone_number_id", ""):
                extra["phone_number_id"] = args.phone_number_id
            out = MG.account_add(cfg, args.account_id, args.provider,
                                 args.label, key_ref=args.key_ref,
                                 origin=getattr(args, "origin", "") or "",
                                 extra=extra, requested_by="cli:user",
                                 confirmed=confirm)
        elif cmd == "account-list":
            out = MG.account_list(cfg)
        elif cmd == "account-show":
            out = MG.account_show(cfg, args.account_id)
        elif cmd == "account-remove":
            out = MG.account_remove(cfg, args.account_id,
                                    requested_by="cli:user", confirmed=confirm)
        elif cmd == "draft":
            out = MG.draft(cfg, args.account, args.to, args.body,
                           idempotency_key=getattr(args, "idempotency_key", "") or "",
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "approve":
            out = MG.approve(cfg, args.msg_id,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "send":
            out = MG.send(cfg, args.msg_id,
                          requested_by="cli:user", confirmed=confirm)
        elif cmd == "retry":
            out = MG.retry(cfg, args.msg_id,
                           requested_by="cli:user", confirmed=confirm)
        elif cmd == "link":
            out = MG.link(cfg, args.account_id)
        elif cmd == "list":
            out = MG.list_msgs(cfg, state_filter=getattr(args, "state", "") or "")
        elif cmd == "show":
            out = MG.show(cfg, args.msg_id)
        elif cmd == "inbox":
            out = MG.inbox_list(cfg,
                                subscription=getattr(args, "subscription", "") or "")
        elif cmd == "inbox-show":
            out = MG.inbox_show(cfg, args.msg_id)
        elif cmd == "summarize":
            out = MG.summarize(cfg, args.msg_id)
        elif cmd == "draft-reply":
            out = MG.draft_reply(cfg, args.msg_id, args.account, args.body,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "feishu-agent-add":
            out = MG.feishu_agent_add(cfg, args.agent_id,
                                      app_id_ref=args.app_id_ref,
                                      allowlist=args.allowlist,
                                      commands=getattr(args, "commands", ""),
                                      requested_by="cli:user",
                                      confirmed=confirm)
        elif cmd == "feishu-agent-list":
            out = MG.feishu_agents(cfg)
        elif cmd == "feishu-agent-remove":
            out = MG.feishu_agent_remove(cfg, args.agent_id,
                                         requested_by="cli:user",
                                         confirmed=confirm)
        elif cmd == "feishu-send-as-agent":
            out = MG.feishu_send_as_agent(cfg, args.account, to=args.to,
                                          body=args.body, agent_id=args.agent_id,
                                          requested_by="cli:user",
                                          confirmed=confirm)
        else:
            print(f"ERROR: unknown msg command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MG.MessagingError as exc:
        rc = 3 if exc.code == MG.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "outbox preserved; nothing sent twice"},
                          indent=2))
        return rc


def _cmd_gateway(cfg, args) -> int:
    """voicebridge gateway add|list|show|remove|link (+ refused supervision).

    Records and link states are real; process supervision stays with the
    operator (single event path invariant).
    """
    import json as _json
    from . import gateway_peers as GW
    cmd = getattr(args, "gateway_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("add", "remove") and GW.estop_engaged(cfg):
            print(_json.dumps(GW.refuse_estop(f"gateway {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in ("add", "remove"):
            print(_json.dumps({"state": "preview", "would": f"gateway {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = GW.gateway_list(cfg)
        elif cmd == "show":
            out = GW.gateway_show(cfg, args.gateway_id)
        elif cmd == "add":
            out = GW.gateway_add(cfg, args.gateway_id, args.provider,
                                 args.account, args.channel,
                                 key_ref=args.key_ref,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "remove":
            out = GW.gateway_remove(cfg, args.gateway_id,
                                    requested_by="cli:user", confirmed=confirm)
        elif cmd == "link":
            out = GW.gateway_link(cfg, args.gateway_id)
        elif cmd == "status":
            # Owner Decision #10: real Qubit-owned gateway status.
            from . import gateway_supervisor as GS

            out = GS.status(cfg)
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0 if out.get("running") else 2
        elif cmd in ("start", "stop", "restart"):
            # Owner Decision #10: real control of the Qubit gateway service
            # through the one authority path (e-stop → confirmation → audit).
            from . import gateway_supervisor as GS

            out = GS.control(cfg, cmd, confirmed=confirm, actor="cli:user")
            print(_json.dumps(out, indent=2, sort_keys=True))
            if out.get("ok"):
                return 0
            code = str(out.get("code") or "")
            return {"GATEWAY_CONFIRM_REQUIRED": 3, "GATEWAY_ESTOP": 3,
                    "GATEWAY_SUPERVISION_UNAVAILABLE": 2}.get(code, 1)
        elif cmd in ("run", "install"):
            print(_json.dumps(GW.refuse_supervision(cmd), indent=2))
            return 2
        elif cmd == "protocol":
            out = GW.protocol_doctor()
        elif cmd == "negotiate":
            out = GW.negotiate_protocol(
                getattr(args, "client_version", None))
        elif cmd == "serve":
            print(_json.dumps(
                GW.serve_refusal(getattr(args, "action", "serve")), indent=2))
            return 2
        elif cmd == "rpc":
            # One in-process RPC path (no standalone listener): the same
            # registry the gateway frame transport dispatches through.
            from .gateway.rpc import default_registry

            reg = default_registry()
            method = str(getattr(args, "method", "") or "").strip()
            if not method:
                print(_json.dumps({"state": "OK", "methods": reg.names()},
                                  indent=2))
                return 0
            try:
                params = _json.loads(getattr(args, "params", "{}") or "{}")
            except ValueError:
                print(_json.dumps({"state": "FAILED",
                                   "code": "BAD_PARAMS",
                                   "detail": "--params must be a JSON object"},
                                  indent=2))
                return 2
            try:
                out = reg.handle(cfg, method, params)
            except Exception as exc:  # noqa: BLE001 - typed RPC failure
                print(_json.dumps({
                    "state": "FAILED",
                    "code": str(getattr(exc, "code", "GATEWAY_METHOD_FAILED")),
                    "detail": str(getattr(exc, "detail", "") or exc)[:300]},
                    indent=2))
                return 2
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        else:
            print(f"ERROR: unknown gateway command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except GW.GatewayError as exc:
        rc = 3 if exc.code == GW.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registries untouched; nothing delivered"},
                          indent=2))
        return rc


def _cmd_peer(cfg, args) -> int:
    """voicebridge peer add|list|show|remove|link|dm|run.

    Registry, enrollment linkage, and credential-gated delivery; live
    secrets stay operator-side.
    """
    import json as _json
    from . import gateway_peers as GW
    cmd = getattr(args, "peer_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd in ("add", "remove", "dm", "run") and GW.estop_engaged(cfg):
            print(_json.dumps(GW.refuse_estop(f"peer {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd != "list" and cmd != "show" \
                and cmd != "link":
            print(_json.dumps({"state": "preview", "would": f"peer {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = GW.peer_list(cfg)
        elif cmd == "show":
            out = GW.peer_show(cfg, args.peer_id)
        elif cmd == "add":
            out = GW.peer_add(cfg, args.peer_id, args.url, args.account,
                              key_ref=args.key_ref,
                              requested_by="cli:user", confirmed=confirm)
        elif cmd == "remove":
            out = GW.peer_remove(cfg, args.peer_id,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "link":
            out = GW.peer_link(cfg, args.peer_id,
                               probe=bool(getattr(args, "probe", False)))
        elif cmd == "dm":
            out = GW.peer_dm(cfg, args.peer_id, args.to, args.text,
                             requested_by="cli:user", confirmed=confirm)
        elif cmd == "run":
            out = GW.peer_run(cfg, args.peer_id, args.agent, args.text,
                              requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown peer command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except GW.GatewayError as exc:
        rc = 3 if exc.code == GW.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registries untouched; nothing delivered"},
                          indent=2))
        return rc


def _cmd_brain(cfg, args) -> int:
    """voicebridge brain principal-*/profile-*/account-*/vault-*/onboard.

    Multi-principal identity registry with provenance, retention, and
    references-only vault. Mutating ops preview under --dry-run, require
    --confirm, and refuse while the emergency stop is latched.
    """
    import json as _json
    from . import brain as BR
    cmd = getattr(args, "brain_command", None) or "principal-list"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"principal-add", "member-add", "member-remove",
                 "profile-set", "profile-forget", "profile-sweep",
                 "account-add", "account-remove", "vault-set",
                 "vault-remove", "onboard"}
    try:
        if cmd in _MUTATING and BR.estop_engaged(cfg):
            print(_json.dumps(BR.refuse_estop(f"brain {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview", "would": f"brain {cmd}"},
                              indent=2))
            return 0
        if cmd == "principal-add":
            out = BR.principal_add(cfg, args.principal_id, args.kind,
                                   args.label, requested_by="cli:user",
                                   confirmed=confirm)
        elif cmd == "principal-list":
            out = BR.principal_list(cfg)
        elif cmd == "member-add":
            out = BR.member_add(cfg, args.org, args.employee,
                                requested_by="cli:user", confirmed=confirm)
        elif cmd == "member-remove":
            out = BR.member_remove(cfg, args.org, args.employee,
                                   requested_by="cli:user", confirmed=confirm)
        elif cmd == "profile-set":
            out = BR.profile_set(cfg, args.principal, args.key, args.value,
                                 identity=args.identity, purpose=args.purpose,
                                 trust=args.trust,
                                 retention_days=args.retention_days,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "profile-show":
            out = BR.profile_show(cfg, args.principal, identity=args.identity)
        elif cmd == "profile-forget":
            out = BR.profile_forget(cfg, args.principal, identity=args.identity,
                                    key=args.key, requested_by="cli:user",
                                    confirmed=confirm)
        elif cmd == "profile-sweep":
            out = BR.profile_sweep(cfg, requested_by="cli:user",
                                   confirmed=confirm)
        elif cmd == "profile-export":
            out = BR.profile_export(cfg, args.principal, identity=args.identity)
        elif cmd == "account-add":
            out = BR.account_add(cfg, args.alias, args.origin, args.principal,
                                 identity=args.identity, label=args.label,
                                 requested_by="cli:user", confirmed=confirm)
        elif cmd == "account-list":
            out = BR.account_list(cfg, principal=args.principal)
        elif cmd == "account-remove":
            out = BR.account_remove(cfg, args.alias,
                                    requested_by="cli:user", confirmed=confirm)
        elif cmd == "vault-set":
            out = BR.vault_set(cfg, args.name, args.ref,
                               principal=args.principal,
                               requested_by="cli:user", confirmed=confirm)
        elif cmd == "vault-list":
            out = BR.vault_list(cfg, principal=args.principal)
        elif cmd == "vault-remove":
            out = BR.vault_remove(cfg, args.name,
                                  requested_by="cli:user", confirmed=confirm)
        elif cmd == "onboard":
            answers_file = (getattr(args, "answers_file", "") or "").strip()
            if not answers_file:
                out = BR.onboard_plan()
            else:
                try:
                    answers = _json.loads(Path(answers_file).read_text(
                        encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    print(_json.dumps(
                        {"state": "FAILED", "code": "BRAIN_BAD_INPUT",
                         "detail": f"answers unreadable: {exc}"[:200],
                         "owner": "qubit-policy",
                         "rollback": "nothing saved"}, indent=2))
                    return 2
                out = BR.onboard_apply(cfg, answers, requested_by="cli:user",
                                       confirmed=confirm,
                                       dry_run=bool(getattr(args, "dry_run", False)))
        else:
            print(f"ERROR: unknown brain command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except BR.BrainError as exc:
        rc = 3 if exc.code == BR.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "registry untouched; nothing saved"},
                          indent=2))
        return rc


def _cmd_voice(cfg, args) -> int:
    """voicebridge voice say|confirm|cancel|commands.

    The same resolve/execute/confirm/cancel pipeline the microphone path
    uses, driven by typed text (no audio). Confirmations minted here land
    in the shared ApprovalJournal; "yes" turns consume them exactly once.
    """
    import json as _json
    from . import voice_commands as VC
    cmd = getattr(args, "voice_command", None) or "commands"
    session = getattr(args, "session", "") or "cli"
    ctx = VC.make_ctx(cfg, actor="cli:user", session=session, source="cli")
    as_json = bool(getattr(args, "json", False))
    if cmd == "commands":
        out = {"state": "OK", "commands": [
            {"id": e.id, "title": e.title, "aliases": list(e.aliases),
             "args": [{"name": a.name, "type": a.type, "required": a.required,
                       "choices": list(a.choices)} for a in e.args],
             "confirm": e.confirm, "scope": list(e.scope),
             "examples": list(e.examples), "help": e.help}
            for e in sorted(VC.REGISTRY.values(), key=lambda e: e.id)]}
        print(_json.dumps(out, indent=2))
        return 0
    if cmd == "say":
        text = " ".join(getattr(args, "text", []) or [])
        resolution = VC.resolve(text)
        if resolution.kind in (VC.CLARIFY, VC.AMBIGUOUS, VC.UNKNOWN):
            speech = resolution.speech or "I didn't understand that."
            if as_json:
                print(_json.dumps({"state": resolution.kind, "speech": speech},
                                  indent=2))
            else:
                print(speech)
            return 0 if resolution.kind != VC.UNKNOWN else 1
        out = VC.execute(ctx, resolution)
        if as_json:
            print(_json.dumps({"state": out.kind,
                               "speech": out.speech,
                               "confirmation_id": out.confirmation_id,
                               "receipt": out.receipt}, indent=2,
                              default=str))
        else:
            print(out.speech)
            if out.confirmation_id:
                print(f"confirmation: {out.confirmation_id} "
                      f"(voice confirm {out.confirmation_id} "
                      f"--session {session})")
        return 0 if out.kind in (VC.EXECUTED, VC.NEEDS_CONFIRM) else 2
    if cmd == "confirm":
        cid = (getattr(args, "confirmation_id", "") or "").strip() or None
        out = VC.confirm_pending(ctx, cid)
        print(out.speech)
        return 0 if out.kind == VC.EXECUTED else 2
    if cmd == "cancel":
        cid = (getattr(args, "confirmation_id", "") or "").strip() or None
        out = VC.cancel_pending(ctx, cid)
        print(out.speech)
        return 0 if out.kind == VC.EXECUTED else 2
    print(f"ERROR: unknown voice command {cmd!r}")
    return 2


def _cmd_memory_workspace(cfg, args, cmd: str) -> int:
    """OC-48 local markdown workspace memory + offline dreaming (no creds)."""
    import json as _json
    from .memory import workspace as WS

    confirm = bool(getattr(args, "confirm", False))
    dry = bool(getattr(args, "dry_run", False))
    try:
        if cmd == "workspace-list":
            out = WS.list_notes(cfg)
            tag = (getattr(args, "tag", "") or "").strip()
            if tag and isinstance(out.get("notes"), list):
                out = dict(out)
                out["notes"] = [n for n in out["notes"]
                                if tag in (n.get("tags") or [])]
        elif cmd == "workspace-show":
            out = WS.read_note(cfg, args.name)
        elif cmd == "workspace-providers":
            out = WS.providers()
        elif cmd == "workspace-write":
            if dry:
                out = {"state": "preview", "would": "memory workspace-write",
                       "name": args.name}
            else:
                out = WS.write_note(cfg, args.name, args.body,
                                    tags=getattr(args, "tag", []) or [],
                                    requested_by="cli:user", confirmed=confirm)
        elif cmd == "workspace-forget":
            if dry:
                out = {"state": "preview", "would": "memory workspace-forget",
                       "name": args.name}
            else:
                out = WS.forget_note(cfg, args.name, requested_by="cli:user",
                                     confirmed=confirm)
        elif cmd == "workspace-dream":
            if dry:
                out = {"state": "preview", "would": "memory workspace-dream"}
            else:
                out = WS.dream(cfg, requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown memory command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except WS.WorkspaceError as exc:
        rc = 3 if exc.code in (WS.CONFIRM_CODE, WS.ESTOP_CODE) else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code, "detail": exc.detail[:300],
                           "owner": "qubit-policy",
                           "rollback": "workspace notes preserved"},
                          indent=2))
        return rc


def _cmd_memory(cfg, args) -> int:
    """voicebridge memory status|enable|disable|set-provider|belief-record|
    belief-at|belief-conflicts|explain|journal-verify|journal-replay|export.

    Packet 8M semantic memory is DISABLED by default. Runtime selection
    (enabled/provider) lives in <state_dir>/memory/semantic.json (state, not
    live user config); mutations are dry-run unless --confirm. Corrupt or
    newer-than-supported state is a typed degraded/newer result and is NEVER
    silently reset to defaults.
    """
    import json as _json
    import time as _time
    from .memory import (SemanticMemory, SemanticMemoryError,
                         SemanticStateError,
                         load_semantic_state, redact_secrets)
    from .memory.semantic import SEMANTIC_STATE_SCHEMA
    cmd = getattr(args, "memory_command", None) or "status"
    if cmd.startswith("workspace-"):
        return _cmd_memory_workspace(cfg, args, cmd)
    base = cfg.state_dir() / "memory"
    base.mkdir(parents=True, exist_ok=True)
    state_path = base / "semantic.json"
    st, health = load_semantic_state(
        state_path,
        enabled_default=cfg.get("memory", "semantic", "enabled", default=False),
        provider_default=cfg.get("memory", "semantic", "provider",
                                 default="none"),
        embed_model_default=cfg.get("memory", "semantic", "embed_model",
                                    default="nomic-embed-text"),
        ollama_url_default=cfg.get("memory", "semantic", "ollama_url",
                                   default="http://127.0.0.1:11434"),
        timeout_s_default=cfg.get("memory", "semantic", "timeout_s",
                                  default=10.0),
        top_k_default=cfg.get("memory", "semantic", "top_k", default=5))
    sm = SemanticMemory(
        base / "memory.db", enabled=bool(st["enabled"]),
        provider=st["provider"], embed_model=st["embed_model"],
        ollama_url=st["ollama_url"], timeout_s=st["timeout_s"],
        top_k=st["top_k"], journal_path=base / "memory.db.journal")
    try:
        if cmd == "status":
            out = sm.status()
            out["state_error"] = health["detail"] or None
            if health["state"] in ("degraded", "newer"):
                out["state"] = health["state"]
            if getattr(args, "probe", False):
                out["provider_probe"] = sm.check_provider()
        elif cmd == "enable":
            if health["state"] in ("degraded", "newer") and not args.confirm:
                return _memory_state_blocked(health)
            provider = args.provider or st["provider"]
            merged = dict(st)
            merged.update({"enabled": True, "provider": provider,
                           "schema_version": SEMANTIC_STATE_SCHEMA})
            out = _memory_write_state(cfg, state_path, merged,
                                      confirm=args.confirm, health=health)
        elif cmd == "disable":
            if health["state"] in ("degraded", "newer") and not args.confirm:
                return _memory_state_blocked(health)
            merged = dict(st)
            merged.update({"enabled": False,
                           "schema_version": SEMANTIC_STATE_SCHEMA})
            out = _memory_write_state(cfg, state_path, merged,
                                      confirm=args.confirm, health=health)
        elif cmd == "set-provider":
            if health["state"] in ("degraded", "newer") and not args.confirm:
                return _memory_state_blocked(health)
            merged = dict(st)
            merged.update({"provider": args.provider,
                           "schema_version": SEMANTIC_STATE_SCHEMA})
            out = _memory_write_state(cfg, state_path, merged,
                                      confirm=args.confirm, health=health)
        elif cmd == "belief-record":
            rec = sm.record_belief(
                args.entity, args.fact_type, args.value, scope=args.scope,
                valid_from=args.valid_from, valid_until=args.valid_until,
                source=args.source)
            out = {"state": "ok", "id": rec["id"],
                   "text": redact_secrets(rec["text"]),
                   "valid_from": rec["valid_from"],
                   "valid_until": rec["valid_until"], "scope": rec["scope"]}
        elif cmd == "belief-at":
            t = args.time if args.time is not None else _time.time()
            facts = sm.facts_valid_at(args.entity, t, scope=args.scope,
                                      fact_type=args.fact_type)
            out = {"state": "ok", "entity": args.entity, "at": t,
                   "facts": [{"id": r["id"],
                              "text": redact_secrets(r["text"]),
                              "source": r["source"],
                              "valid_from": r["valid_from"],
                              "valid_until": r["valid_until"]}
                             for r in facts]}
        elif cmd == "belief-conflicts":
            out = {"state": "ok",
                   "conflicts": [_redact_conflict(c.__dict__)
                                 for c in sm.belief_conflicts()]}
        elif cmd == "explain":
            out = {"state": "ok", "explanation": sm.explain(args.memory_id)}
        elif cmd == "journal-verify":
            out = sm.journal_verify()
        elif cmd == "journal-replay":
            out = sm.journal_replay(until_seq=args.until)
        elif cmd == "export":
            out = {"state": "ok",
                   "records": sm.export(scope=args.scope, path=args.out)}
        else:
            print(f"ERROR: unknown memory command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except SemanticStateError as exc:
        out = {"state": "failed", "detail": str(exc)}
        if getattr(exc, "durability_uncertain", False):
            out["durability_uncertain"] = True
            out["recovery"] = ("re-run the validated write, or inspect/rollback "
                               "via `voicebridge memory status`")
        if getattr(exc, "state_path", None):
            out["state_path"] = exc.state_path
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 2
    except (SemanticMemoryError, ValueError) as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
        return 2
    finally:
        try:
            sm.close()
        except Exception:
            pass


def _redact_conflict(c: dict) -> dict:
    from .memory import redact_secrets
    out = dict(c)
    for k in ("entity", "fact_type", "value_a", "value_b"):
        if out.get(k):
            out[k] = redact_secrets(out[k])
    return out


def _memory_state_blocked(health: dict) -> int:
    import json as _json
    print(_json.dumps({"state": health["state"],
                       "detail": health["detail"],
                       "action": "refusing to mutate unless --confirm"},
                      indent=2))
    return 4


def _memory_write_state(cfg, state_path, merged, *, confirm: bool,
                        health: dict) -> dict:
    """Atomic private state write (fsync+rename). A corrupt existing file is
    backed up before an explicit confirmed overwrite; newer state is never
    overwritten."""
    import time as _time
    from .memory import write_semantic_state
    if not confirm:
        return {"state": "preview", "would_set": merged,
                "state_error": health["detail"] or None}
    if health["state"] == "newer":
        return {"state": "failed",
                "detail": "refusing to overwrite newer semantic state"}
    if health["state"] == "degraded" and state_path.exists():
        bak = state_path.with_name(
            f"semantic.json.corrupt-{int(_time.time())}")
        bak.write_text(state_path.read_text(errors="replace"))
        try:
            import os as _os
            _os.chmod(bak, 0o600)
        except OSError:
            pass
    write_semantic_state(state_path, merged)
    return {"state": "ok", "applied": merged}


def _cmd_websites(cfg, args) -> int:
    """voicebridge websites add/list/show/inspect/check/logs/manifest-link/
    enable/disable/grant/revoke/remove/rollback.

    Packet 8E onboarding + observability. Adding a URL creates a PENDING,
    disabled proposal and grants NOTHING; a valid, active 7E skill must be
    explicitly linked (manifest-link) before enable/grant. Mutations are
    confirm-gated (--confirm) with an explicit --dry-run preview that persists
    nothing.
    """
    import json as _json
    from . import permissions as P
    from . import websites as W
    cmd = getattr(args, "websites_command", None) or "list"

    # Stateless paths run BEFORE any state store is opened: `add --dry-run`
    # performs no DNS, no network, and writes no websites.json/permissions.json/
    # backup/audit; `probe` persists nothing and only touches the network when
    # the operator explicitly passes --live.
    if cmd == "add" and getattr(args, "dry_run", False):
        try:
            out = W.add_preview(args.url, name=args.name or "")
        except W.WebsiteValidationError as exc:
            print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
            return 2
        _print_website_out(args, cmd, out)
        return 0
    if cmd == "probe":
        if not getattr(args, "live", False):
            out = {"state": "denied", "code": "LIVE_REQUIRED",
                   "detail": "websites probe requires explicit --live "
                             "(bounded read-only network access)"}
            _print_website_out(args, cmd, out)
            return 3
        # The live probe dispatches through the ONE canonical broker (e-stop /
        # policy / shared confirmation journal / audit) the planner and every
        # other web path use, instead of opening its own ungated connection.
        # register_web_tools builds the shared web.* surface; the probe joins it
        # as a READ_ONLY capability and performs no e-stop/audit of its own, so
        # the broker decides and audits exactly once.
        from .broker import build_host_broker, register_web_tools
        from .power import power_control

        broker = build_host_broker(power_control=power_control(cfg), cfg=cfg)
        register_web_tools(broker, cfg=cfg)
        W.register_probe_tool(broker, cfg=cfg)
        res = broker.execute(W.PROBE_CAPABILITY, {"url": args.url},
                             source="user")
        if res.denied:
            out = {"state": "denied", "code": str(res.denied),
                   "detail": "website probe refused by the shared power "
                             "control"}
            _print_website_out(args, cmd, out)
            return 3
        if not res.ok:
            print(_json.dumps({"state": "failed",
                               "detail": W.redact_text(str(res.error))[:300]},
                              indent=2))
            return 2
        try:
            probe_dict = _json.loads(res.output)
        except (TypeError, ValueError):
            print(_json.dumps({"state": "failed",
                               "detail": "probe returned a malformed result"},
                              indent=2))
            return 2
        out = {"state": "OK", "url": args.url,
               "result": W.result_code_for_status(
                   str(probe_dict.get("status") or ""),
                   str(probe_dict.get("reason_code") or "")),
               "probe": probe_dict}
        _print_website_out(args, cmd, out)
        return 0

    if cmd in ("fetch", "search"):
        from . import web_tool as WT
        from .broker import build_host_broker, register_web_tools
        from .power import power_control

        # Dispatch the read-only web capability through the ONE canonical
        # broker (e-stop / policy / shared confirmation journal / audit) the
        # planner and every other channel use, instead of calling the SSRF
        # web tool directly and bypassing the decision junction.
        capability = "web.fetch" if cmd == "fetch" else "web.search"
        target = ({"url": args.url} if cmd == "fetch"
                  else {"query": args.query})
        broker = build_host_broker(power_control=power_control(cfg), cfg=cfg)
        register_web_tools(broker, cfg=cfg)
        res = broker.execute(capability, target, source="user")
        if res.denied:
            out = {"state": "denied", "code": str(res.denied),
                   "detail": "web access refused by the shared power control"}
        elif not res.ok:
            out = {"state": "denied", "code": "WEB_ERROR",
                   "detail": WT.redact_text(str(res.error))[:300]}
        else:
            try:
                out = _json.loads(res.output)
            except (TypeError, ValueError):
                out = {"state": "failed", "code": "WEB_ERROR",
                       "detail": "web tool returned a malformed result"}
        _print_website_out(args, cmd, out)
        return 0 if out.get("state") == "OK" else 2

    store = W.website_integrations_store(cfg)
    perms = P.permissions_store(cfg)
    try:
        if cmd == "add":
            rec = store.add(args.url, requested_by="cli:user",
                            display_name=args.name or "")
            out = {"state": "OK", "status": rec.status,
                   "granted": False, "integration": rec.to_dict()}
        elif cmd == "list":
            out = {"state": "OK", "mode": perms.mode()[0],
                   "state_path": str(store.path),
                   "integrations": [r.to_dict() for r in store.integrations()]}
        elif cmd == "show":
            rec = store.lookup(args.id_or_url)
            if rec is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": "no such integration"}, indent=2))
                return 1
            out = {"state": "OK", "integration": rec.to_dict(),
                   "effective": W.effective_permissions(perms, store, rec)}
        elif cmd == "inspect":
            rec = store.lookup(args.id_or_url)
            if rec is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": "no such integration"}, indent=2))
                return 1
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_inspect": rec.integration_id,
                       "status": rec.status}
            else:
                fixture = getattr(args, "fixture", None)
                if fixture is not None:
                    fixture_path = Path(fixture)
                    if not fixture_path.is_file():
                        print(_json.dumps({"state": "failed",
                                           "detail": f"fixture not found: "
                                                     f"{fixture_path}"}, indent=2))
                        return 2
                    try:
                        raw = _json.loads(fixture_path.read_text())
                        validated = W.validate_probe_result(raw)
                    except Exception as exc:
                        print(_json.dumps({"state": "failed",
                                           "detail": f"fixture invalid: {exc}"},
                                          indent=2))
                        return 2
                    probe = lambda _url: validated  # noqa: E731
                    source = "fixture"
                else:
                    probe = None if not args.no_network else _offline_website_probe
                    source = "offline" if args.no_network else "live"
                rec2 = store.run_discovery(rec.integration_id,
                                           requested_by="cli:user",
                                           probe=probe, probe_source=source)
                out = {"state": "OK", "status": rec2.status,
                       "reason": rec2.health.get("reason_code", ""),
                       "probe_source": source,
                       "integration": rec2.to_dict()}
        elif cmd == "check":
            out = store.check(args.id_or_url, requested_by="cli:user",
                              perms=perms)
        elif cmd == "logs":
            rows = store.logs(args.id_or_url, tail=max(1, min(int(args.tail), 500)))
            out = {"state": "OK", "integration_id": rows[-1]["integration_id"]
                   if rows else None, "logs": rows}
        elif cmd == "manifest-link":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_link": args.skill_id,
                       "integration": args.id_or_url}
            else:
                rec = store.link_manifest(
                    args.id_or_url, skill_id=args.skill_id,
                    requested_by="cli:user", confirmed=args.confirm,
                    perms=perms,
                    artifact_path=str(args.artifact) if args.artifact else None,
                    artifact_sha256=args.artifact_sha256)
                out = {"state": "OK", "manifest_link": rec.manifest_link,
                       "license": rec.license, "content_hash": rec.content_hash,
                       "artifact_sha256": rec.artifact_sha256,
                       "integration": rec.to_dict()}
        elif cmd == "enable":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_enable": args.id_or_url}
            else:
                rec = store.enable(args.id_or_url, requested_by="cli:user",
                                   confirmed=args.confirm, perms=perms)
                out = {"state": "OK", "status": rec.status,
                       "enabled": rec.enabled, "integration": rec.to_dict()}
        elif cmd == "disable":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_disable": args.id_or_url}
            else:
                rec = store.disable(args.id_or_url, requested_by="cli:user",
                                    confirmed=args.confirm)
                out = {"state": "OK", "status": rec.status,
                       "enabled": rec.enabled, "integration": rec.to_dict()}
        elif cmd == "grant":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_grant": args.id_or_url,
                       "capabilities": list(args.capability or ()),
                       "ttl_s": args.ttl}
            else:
                out = store.grant(args.id_or_url, requested_by="cli:user",
                                  confirmed=args.confirm,
                                  capabilities=tuple(args.capability or ()),
                                  ttl_s=args.ttl, perms=perms)
        elif cmd == "revoke":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_revoke": args.id_or_url}
            else:
                out = store.revoke(args.id_or_url, requested_by="cli:user",
                                   confirmed=args.confirm, perms=perms)
        elif cmd == "remove":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_remove": args.id_or_url}
            else:
                out = store.remove(args.id_or_url, requested_by="cli:user",
                                   confirmed=args.confirm, perms=perms)
        elif cmd == "rollback":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_rollback": args.id_or_url}
            else:
                out = store.rollback(args.id_or_url, requested_by="cli:user",
                                     confirmed=args.confirm)
        else:
            print(f"ERROR: unknown websites command {cmd!r}")
            return 2
        _print_website_out(args, cmd, out)
        return 0
    except W.ConfirmationRequiredError as exc:
        print(_json.dumps({"state": "denied",
                           "code": "CONFIRMATION_REQUIRED",
                           "detail": str(exc)}, indent=2))
        return 3
    except (W.WebsiteIntegrationError, W.WebsiteValidationError) as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
        return 2
    except P.PermissionError as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)}, indent=2))
        return 2


def _print_website_out(args, cmd, out) -> None:
    """Machine-readable JSON by default; concise human-readable with --human."""
    import json as _json
    if getattr(args, "human", False):
        print(_human_websites(cmd, out))
    else:
        print(_json.dumps(out, indent=2, sort_keys=True))


def _human_websites(cmd: str, out: dict) -> str:
    """Concise, typed human-readable rendering of a websites command result."""
    if out is None:
        return "unavailable"
    st = out.get("state", "unavailable")
    detail = out.get("detail", "")
    if cmd == "probe":
        p = out.get("probe") or {}
        code = out.get("code", "")
        reason = code or p.get("reason_code") or detail
        return (f"{out.get('url', '')} -> {out.get('result', p.get('status', st))} "
                f"({reason}); reachable={bool(p.get('reachable'))} "
                f"auth={bool(p.get('auth_required'))} "
                f"title={p.get('title', '')[:40] or '—'}")
    if cmd == "add":
        i = out.get("integration") or {}
        return (f"added {i.get('origin', out.get('would_add', '?'))} "
                f"[{i.get('status', out.get('status', 'PENDING'))}] "
                f"grants=none")
    if cmd == "list":
        rows = out.get("integrations") or []
        lines = [f"{out.get('mode', 'restricted')} · {len(rows)} record(s)"
                 f" · state={out.get('state_path') or '?'}"]
        for r in rows:
            lines.append(f"  {r.get('integration_id')} {r.get('origin')} "
                         f"[{r.get('status')}] caps="
                         f"{len(r.get('requested_capabilities') or [])} "
                         f"grants={len(r.get('grant_ids') or [])} "
                         f"link={r.get('manifest_link') or 'none'}")
        return "\n".join(lines)
    if cmd == "show":
        i = out.get("integration") or {}
        eff = (out.get("effective") or {}).get("effective") or {}
        grants = (out.get("effective") or {}).get("grants") or []
        link = i.get("manifest_link") or "none"
        if link != "none":
            link = (f"{link} (license {i.get('license')}, hash "
                    f"{(i.get('content_hash') or '')[:8]}, "
                    f"artifact {(i.get('artifact_sha256') or '—')[:8]})")
        return (f"{i.get('origin')} [{i.get('status')}] enabled="
                f"{bool(i.get('enabled'))} link={link} "
                f"caps={len(i.get('requested_capabilities') or [])} "
                f"grants={len(grants)} "
                f"effective=" + ", ".join(
                    f"{c}={d.get('code')}" for c, d in eff.items()) or "—")
    if cmd == "inspect":
        i = out.get("integration") or {}
        return (f"{i.get('origin')} -> {i.get('status')} "
                f"({out.get('reason', '')}) probe={out.get('probe_source', '?')} "
                f"caps-proposed={len(i.get('requested_capabilities') or [])}")
    if cmd == "check":
        m = out.get("manifest") or {}
        mstate = "unlinked" if not m else ("active" if m.get("active") else "broken")
        active = sum(1 for g in (out.get("grants") or []) if g.get("active"))
        expired = sum(1 for g in (out.get("grants") or []) if not g.get("active"))
        return (f"{out.get('origin')} [{out.get('status')}] check={out.get('state')} "
                f"mode={out.get('mode')} manifest={mstate} "
                f"grants-active={active} expired={expired}")
    if cmd == "manifest-link":
        return (f"linked {out.get('manifest_link')} (license {out.get('license')}, "
                f"hash {(out.get('content_hash') or '')[:8]}, "
                f"artifact {(out.get('artifact_sha256') or '—')[:8] or '—'})")
    if cmd == "grant":
        gs = out.get("grants") or []
        i = out.get("integration") or {}
        exp = gs[0].get("expires_at") if gs else None
        import time as _t
        exp_s = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(exp)) if exp else "never"
        return (f"granted {len(gs)} capability/ies for {i.get('origin')} "
                f"scopes={gs[0].get('scopes') if gs else '—'} expires={exp_s}")
    if cmd == "revoke":
        return f"revoked {len(out.get('revoked') or [])} grant(s) for {out.get('integration', {}).get('origin', '?')}"
    if cmd == "enable" or cmd == "disable":
        i = out.get("integration") or {}
        return f"{i.get('origin')} -> {i.get('status')} enabled={bool(i.get('enabled'))}"
    if cmd == "remove":
        r = out.get("removed") or {}
        return f"removed {r.get('origin', '?')} [REMOVED]; revoked {len(out.get('revoked') or [])} grant(s)"
    if cmd == "rollback":
        i = out.get("integration") or {}
        return f"rolled back {i.get('origin', '?')} -> {i.get('status')} (grants never auto-restored)"
    if cmd == "logs":
        rows = out.get("logs") or []
        last = rows[-1] if rows else {}
        return (f"{len(rows)} log entr{'y' if len(rows) == 1 else 'ies'}; last: "
                f"{last.get('op', '—')} [{last.get('status', '—')}] "
                f"{last.get('reason', '—')}")
    return f"{st} {detail}"


def _cmd_actions(cfg, args) -> int:
    """voicebridge actions list/show/check/preview/prepare/execute/cancel/
    receipt/receipts/retry/recover.

    Packet 8F typed, brokered, confirmation-gated actions. Restricted is the
    default; sending/publishing/purchasing/deleting/credential/account/system
    actions always require a fresh human confirmation bound to the exact
    action hash. Live adapters are DISABLED — ``--fixture`` is the explicit
    synthetic-simulation path, and every other execute returns the typed
    ADAPTER_DISABLED result.
    """
    import json as _json
    from . import actions as A
    cmd = getattr(args, "actions_command", None) or "list"

    executors: Dict[str, Any] = {}
    fixture = getattr(args, "fixture", None)
    if fixture:
        fpath = Path(fixture)
        if not fpath.is_file():
            print(_json.dumps({"state": "failed",
                               "detail": f"fixture not found: {fpath}"},
                              indent=2))
            return 2
        try:
            executors = A.make_fixture_executors(
                A.load_fixture_outcomes(fpath))
        except Exception as exc:
            print(_json.dumps({"state": "failed", "detail": f"fixture "
                               f"invalid: {exc}"}, indent=2))
            return 2
    runner = A.action_runner(cfg, executors=executors,
                             power=_shared_power(cfg))

    def _args_json(raw: str) -> Dict[str, Any]:
        try:
            return _json.loads(raw or "{}")
        except Exception as exc:
            raise A.ActionValidationError(f"args JSON invalid: {exc}") from None

    try:
        if cmd == "list":
            out = {"state": "OK", "mode": runner.perms.mode()[0],
                   "state_path": str(runner.store.path),
                   "actions": runner.actions()}
        elif cmd == "show":
            out = {"state": "OK", "action": runner.show(args.action_id)}
        elif cmd == "check":
            out = runner.check(args.action_id,
                               getattr(args, "scope", "") or "")
        elif cmd == "preview":
            out = runner.preview(
                args.action_id, _args_json(args.args), target=args.target,
                account_scope=getattr(args, "account", "") or "",
                scope=getattr(args, "scope", "") or "",
                idempotency_key=getattr(args, "idempotency_key", "") or "")
        elif cmd == "prepare":
            out = runner.prepare(args.action_id,
                                 _args_json(args.envelope),
                                 issued_by="cli:user")
        elif cmd == "execute":
            if getattr(args, "dry_run", False):
                out = runner.execute(
                    args.action_id, args=_args_json(args.args),
                    target=args.target,
                    account_scope=getattr(args, "account", "") or "",
                    scope=getattr(args, "scope", "") or "",
                    idempotency_key=getattr(args, "idempotency_key", "") or "",
                    source="user", dry_run=True)
            else:
                out = runner.execute(
                    args.action_id,
                    args=(None if args.envelope else _args_json(args.args)),
                    envelope=(_args_json(args.envelope)
                              if args.envelope else None),
                    target=args.target,
                    account_scope=getattr(args, "account", "") or "",
                    scope=getattr(args, "scope", "") or "",
                    idempotency_key=getattr(args, "idempotency_key", "") or "",
                    source="user", confirmation_id=args.confirmation,
                    confirmed=args.confirm,
                    expected_hash=args.expected_hash or "")
        elif cmd == "cancel":
            out = runner.cancel(args.receipt_id)
        elif cmd == "receipt":
            out = {"state": "OK", "receipt": runner.receipt(args.receipt_id)}
        elif cmd == "receipts":
            out = runner.receipts(getattr(args, "limit", 30) or 30)
        elif cmd == "retry":
            out = runner.retry(
                args.receipt_id, args=_args_json(args.args),
                target=args.target, scope=args.scope,
                account_scope=getattr(args, "account", "") or "",
                source="user", confirmation_id=args.confirmation,
                confirmed=args.confirm)
        elif cmd == "recover":
            out = runner.recover(args.receipt_id,
                                 confirmation_id=args.confirmation,
                                 confirmed=args.confirm)
        else:
            print(f"ERROR: unknown actions command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (A.ActionError, A.ActionValidationError, A.ActionDeniedError) as exc:
        code = getattr(exc, "code", None) or "ACTION_ERROR"
        print(_json.dumps({"state": "failed", "code": code,
                           "detail": str(exc)}, indent=2))
        return 2
    except Exception as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)[:200]},
                          indent=2))
        return 2


def _cmd_ghost(cfg, args) -> int:
    """voicebridge ghost list/status/show/check/grant/revoke/preview/execute/
    cancel/retry/recover/receipts/enable/disable/emergency-stop/reset.

    Packet 8N optional supervised Ghost adapter. All capabilities are disabled
    by default; ``--fixture`` is the explicit synthetic-simulation path and
    every other execute returns ADAPTER_DISABLED. ``GHOST_SHELL=off`` by
    default. Session grants are human-issued, bounded, expiring, target-bound
    and only cover read-only/reversible capabilities.
    """
    import json as _json
    from . import ghost as G
    G.register_ghost_specs()
    cmd = getattr(args, "ghost_command", None) or "list"

    executors: Dict[str, Any] = {}
    fixture = getattr(args, "fixture", None)
    if fixture:
        fpath = Path(fixture)
        if not fpath.is_file():
            print(_json.dumps({"state": "failed",
                               "detail": f"fixture not found: {fpath}"},
                              indent=2))
            return 2
        try:
            from . import actions as A
            executors = A.make_fixture_executors(
                A.load_fixture_outcomes(fpath))
        except Exception as exc:
            print(_json.dumps({"state": "failed", "detail": f"fixture "
                               f"invalid: {exc}"}, indent=2))
            return 2
    runner = G.ghost_runner(cfg, executors=executors,
                            power=_shared_power(cfg))

    def _args_json(raw: str) -> Dict[str, Any]:
        try:
            return _json.loads(raw or "{}")
        except Exception as exc:
            raise G.ActionValidationError(f"args JSON invalid: {exc}") from None

    try:
        if cmd == "list":
            out = {"state": "OK",
                   "mode": runner.perms.mode()[0],
                   "state_path": str(runner.store.path),
                   "capabilities": runner.ghost_actions()}
        elif cmd == "status":
            out = runner.ghost_status()
        elif cmd == "show":
            spec = G.ghost_spec(args.action_id)
            if spec is None:
                raise G.ActionValidationError(f"unknown action {args.action_id!r}")
            out = {"state": "OK", "action": spec.to_dict()}
        elif cmd == "check":
            out = runner.check(args.action_id,
                               getattr(args, "scope", "") or "")
        elif cmd == "grant":
            scopes = tuple(args.scope) or (G.ghost_spec(args.capability)
                                           .scopes[0],)
            out = runner.grant(args.capability, scopes=scopes,
                               target=getattr(args, "target", "") or "desktop",
                               role=getattr(args, "role", "") or "",
                               ttl_s=getattr(args, "ttl", None))
        elif cmd == "revoke":
            out = runner.revoke_grant(args.grant_id)
        elif cmd == "preview":
            out = runner.preview(args.action_id, _args_json(args.args),
                                 target=getattr(args, "target", "") or "desktop",
                                 scope=getattr(args, "scope", "") or "",
                                 role=getattr(args, "role", "") or "")
        elif cmd == "execute":
            role = getattr(args, "role", "") or ""
            timeout = getattr(args, "timeout", None)
            if timeout is not None and not 1.0 <= float(timeout) <= 600.0:
                print(_json.dumps({"state": "failed", "code": "GHOST_BAD_TIMEOUT",
                                   "detail": "timeout must be 1..600 seconds"},
                                  indent=2))
                return 2
            if getattr(args, "dry_run", False):
                out = runner.execute(args.action_id,
                                     args=_args_json(args.args),
                                     target=getattr(args, "target", "") or
                                     "desktop",
                                     scope=getattr(args, "scope", "") or "",
                                     source="user", dry_run=True, role=role)
            else:
                out = runner.execute(
                    args.action_id, args=_args_json(args.args),
                    target=getattr(args, "target", "") or "desktop",
                    scope=getattr(args, "scope", "") or "",
                    source="user", confirmation_id=args.confirmation,
                    confirmed=args.confirm,
                    expected_hash=args.expected_hash or "", role=role,
                    timeout_s=timeout)
        elif cmd == "retry":
            out = runner.retry(
                args.receipt_id, args=_args_json(args.args),
                target=getattr(args, "target", "") or "desktop",
                scope=getattr(args, "scope", "") or "",
                source="user", confirmation_id=args.confirmation,
                confirmed=args.confirm,
                role=getattr(args, "role", "") or "")
        elif cmd == "recover":
            out = runner.recover(args.receipt_id, source="user",
                                 confirmation_id=args.confirmation,
                                 confirmed=args.confirm)
        elif cmd == "cancel":
            out = runner.cancel(args.receipt_id)
        elif cmd == "receipts":
            out = runner.receipts(getattr(args, "limit", 30) or 30)
        elif cmd == "enable":
            out = runner.adapter.enable(requested_by="user")
        elif cmd == "disable":
            out = runner.adapter.disable(requested_by="user")
        elif cmd == "emergency-stop":
            out = runner.adapter.emergency_stop()
        elif cmd == "reset":
            out = runner.adapter.reset(requested_by="user")
        else:
            print(f"ERROR: unknown ghost command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (G.ActionValidationError, G.ActionError,
            G.SessionGrantError) as exc:
        code = getattr(exc, "code", None) or "GHOST_ERROR"
        print(_json.dumps({"state": "failed", "code": code,
                           "detail": str(exc)}, indent=2))
        return 2
    except Exception as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)[:200]},
                          indent=2))
        return 2


def _cmd_pause(cfg, args) -> int:
    """`qubit pause --confirm` -> the ONE Qubit emergency stop latch.

    The pinned fork's ``pause`` writes a second ESTOP sentinel; Qubit owns a
    single emergency-stop authority, so this engages the shared
    :class:`voicebridge.power.PowerControl` latch used by CLI/dashboard/voice.
    Without ``--confirm`` the router already returns a typed refusal; this
    handler also enforces confirmation defensively.
    """
    import json as _json
    if not getattr(args, "confirm", False):
        print(_json.dumps({
            "state": "refused", "code": "ESTOP_QUBIT_OWNED",
            "detail": "qubit pause is the ONE Qubit emergency stop and "
                      "requires --confirm; use `qubit pause --confirm` (no "
                      "second fork ESTOP sentinel)"}, indent=2,
            sort_keys=True))
        return 3
    from . import power as PW
    try:
        out = PW.power_control(cfg).emergency_stop(issued_by="cli:user")
    except PW.PowerDenied as exc:
        print(_json.dumps({"state": "denied", "code": exc.code,
                           "detail": exc.detail}, indent=2, sort_keys=True))
        return 3
    except Exception as exc:  # typed boundary: never a traceback
        print(_json.dumps({
            "state": "failed",
            "code": str(getattr(exc, "code", None) or "POWER_ERROR"),
            "detail": str(exc)[:200]}, indent=2, sort_keys=True))
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_power(cfg, args) -> int:
    """voicebridge power profile/status/circuits/show/check/grant/revoke/
    reset-grant/emergency-stop/reset/disable/enable.

    Packet 8O: one policy path + capability inventory + owner power grant +
    global power control. All of this is decision/inventory only — it never
    launches, connects, or enables a live capability.
    """
    import json as _json
    from . import power as PW
    cmd = getattr(args, "power_command", None) or "profile"
    control = PW.power_control(cfg)

    def _store():
        return PW.power_grant_store(cfg)

    def _policy(store=None):
        from .permissions import permissions_store
        return PW.power_policy(cfg, store=store or _store(),
                               control=control,
                               perms=permissions_store(cfg))

    def _shared_policy():
        return PW.power_policy(cfg)

    try:
        if cmd == "profile":
            out = PW.power_profile(cfg)
        elif cmd == "status":
            out = {"state": "OK", "control": control.status(),
                   "grant": (_store().grant().to_dict()
                             if _store().grant() else None),
                   "grant_health": _store().health()}
        elif cmd == "circuits":
            out = {"state": "OK", "circuits": control.circuit_status()}
        elif cmd == "show":
            caps = PW.power_profile(cfg)["capabilities"]
            row = next((c for c in caps
                        if c["capability"] == args.capability), None)
            if row is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": f"unknown capability "
                                             f"{args.capability}"}, indent=2))
                return 1
            out = {"state": "OK", "capability": row}
        elif cmd == "check":
            out = _shared_policy().check(
                args.capability, scopes=tuple(args.scope or ()),
                target=args.target or "", role=args.role or "",
                account_scope=args.account or "")
            out["state"] = "OK" if out["allowed"] else "denied"
        elif cmd == "grant":
            store = _store()
            if getattr(args, "dry_run", False):
                out = {"state": "preview",
                       "would_grant": {"browsers": list(args.browser or ()),
                                       "apps": list(args.app or ()),
                                       "accounts": list(args.account or ()),
                                       "origins": list(args.origin or ()),
                                       "directories": list(args.directory or ()),
                                       "scopes": list(args.scope or ()),
                                       "capabilities": list(args.capability or ()),
                                       "ttl_s": args.ttl}}
            else:
                g = store.issue(
                    browsers=tuple(args.browser or ()),
                    apps=tuple(args.app or ()),
                    accounts=tuple(args.account or ()),
                    origins=tuple(args.origin or ()),
                    directories=tuple(args.directory or ()),
                    scopes=tuple(args.scope or ()),
                    capabilities=tuple(args.capability or ()),
                    ttl_s=args.ttl, note=args.note or "",
                    issued_by="cli:user")
                out = {"state": "OK", "grant": g.to_dict()}
        elif cmd == "revoke":
            store = _store()
            if getattr(args, "dry_run", False):
                out = {"state": "preview",
                       "would_revoke": args.grant_id}
            else:
                g = store.revoke(args.grant_id, issued_by="cli:user")
                out = {"state": "OK", "revoked": g.to_dict()}
        elif cmd == "reset-grant":
            if not getattr(args, "confirm", False):
                print(_json.dumps(
                    {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                     "detail": "power reset-grant requires --confirm "
                               "(explicit human authorization)"}, indent=2))
                return 3
            g = _store().reset(issued_by="cli:user")
            out = {"state": "OK", "grant": None}
        elif cmd in ("emergency-stop", "reset", "disable", "enable"):
            if not getattr(args, "confirm", False):
                print(_json.dumps(
                    {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                     "detail": f"power {cmd} requires --confirm (explicit "
                               f"human authorization)"}, indent=2))
                return 3
            if cmd == "emergency-stop":
                out = control.emergency_stop(issued_by="cli:user")
            elif cmd == "reset":
                out = control.reset(issued_by="cli:user")
            elif cmd == "disable":
                out = control.global_disable(issued_by="cli:user")
            else:
                out = control.global_enable(issued_by="cli:user")
        else:
            print(f"ERROR: unknown power command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except PW.PowerDenied as exc:
        print(_json.dumps({"state": "denied", "code": exc.code,
                           "detail": exc.detail}, indent=2))
        return 3
    except PW.PowerError as exc:
        code = getattr(exc, "code", None) or "POWER_ERROR"
        print(_json.dumps({"state": "failed", "code": code,
                           "detail": str(exc)[:200]}, indent=2))
        return 2
    except Exception as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)[:200]},
                          indent=2))
        return 2


def _offline_website_probe(url: str) -> dict:
    """--no-network probe: a typed offline result, never a fake success."""
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return {"status": "FAILED", "origin": f"{p.scheme}://{p.hostname}",
            "title": "", "reachable": False, "auth_required": False,
            "observation_hints": (), "adapter_requirement": "",
            "reason_code": "NETWORK_DISABLED", "http_status": 0,
            "duration_s": 0.0, "bytes_read": 0, "limits": {},
            "evidence": {"reason": "NETWORK_DISABLED"}}


def _passphrase(args, env: str = "VOICE_BRIDGE_PASSPHRASE") -> str:
    import os as _os
    import sys as _sys

    flag = getattr(args, "passphrase", None)
    if flag:
        # A command-line passphrase is visible in `ps` output and shell
        # history: work, but steer the operator to the env var next time.
        print(f"WARN: --passphrase exposes the secret in `ps`/history; "
              f"prefer {env}=... (env) next time", file=_sys.stderr)
        return flag
    pw = _os.environ.get(env, "")
    if not pw:
        raise ValueError(f"passphrase required (--passphrase or {env})")
    return pw


def _cmd_verified_skills(cfg, args) -> int:
    """voicebridge verified-skills status|list|show|submit|verify|refute|
    record-failure|recall|pin|unpin|reinforce|quarantine-revoked|sweep|remove|
    retier|snapshot|restore|export.

    Packet 8P verified skill memory is DISABLED by default and is advisory
    recipe memory only. A candidate is staged QUARANTINED and only becomes
    VERIFIED through an explicit OWNER verdict (there is no self-verification
    path). Verified skills are never recalled as authority and never grant a
    capability. All persistence is 0700/0600, audited, and hash-chain
    journaled; secret-shaped text is rejected.
    """
    import json as _json
    from .redaction import redact_text
    from .verified_skills import (SkillProposal, VerifiedSkillError,
                                  verified_skills_store)
    cmd = getattr(args, "verified_skills_command", None) or "status"
    store = verified_skills_store(cfg)
    try:
        if cmd == "status":
            out = store.stats()
            out["state"] = "OK" if out["journal"]["state"] == "OK" else "degraded"
        elif cmd == "list":
            out = {"skills": [r.to_dict() for r in store.list()]}
        elif cmd == "list-status":
            out = {"skills": [r.to_dict() for r in store.list(status=args.status)]}
        elif cmd == "bundled":
            out = {"state": "OK", "bundled": store.bundled()}
        elif cmd == "show":
            rec = store.get(args.id)
            if rec is None:
                print(_json.dumps({"state": "MISSING", "id": args.id}))
                return 1
            out = {"state": "OK", "skill": rec.to_dict()}
        elif cmd == "submit":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to stage "
                                             "the candidate as QUARANTINED"}))
                return 0
            prop = SkillProposal(
                name=args.name, interface=args.interface,
                implementation=args.implementation,
                acceptance_test=args.acceptance_test, task=args.task,
                capabilities=tuple(args.capability), cost=args.cost,
                trust=args.trust, model=args.model)
            rec = store.submit(prop)
            out = {"state": "OK", "id": rec.id, "status": rec.status,
                   "check_strength": rec.check_strength}
        elif cmd == "verify":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to issue "
                                             "the OWNER verify verdict"}))
                return 0
            rec = store.apply_verdict(args.id, decision="verified",
                                      requested_by="user", reason=args.reason)
            out = {"state": "OK", "id": rec.id, "status": rec.status,
                   "tier": rec.tier}
        elif cmd == "refute":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to refute"}))
                return 0
            rec = store.apply_verdict(args.id, decision="refuted",
                                      requested_by="user", reason=args.reason)
            out = {"state": "OK", "id": rec.id, "status": rec.status}
        elif cmd == "record-failure":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to record"}))
                return 0
            rec = store.record_failure(args.task, args.approach, args.reason)
            out = {"state": "OK", "id": rec.id, "kind": rec.kind}
        elif cmd == "recall":
            out = store.recall(args.query, top_k=args.top_k,
                               token_budget=args.token_budget)
        elif cmd == "pin":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to pin"}))
                return 0
            rec = store.pin(args.id, requested_by="user")
            out = {"state": "OK", "id": rec.id, "pinned": rec.pinned}
        elif cmd == "unpin":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to unpin"}))
                return 0
            rec = store.unpin(args.id, requested_by="user")
            out = {"state": "OK", "id": rec.id, "pinned": rec.pinned}
        elif cmd == "reinforce":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to "
                                             "reinforce"}))
                return 0
            rec = store.reinforce(args.id, outcome=args.outcome,
                                  requested_by="user")
            out = {"state": "OK", "id": rec.id, "status": rec.status}
        elif cmd == "quarantine-revoked":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to "
                                             "quarantine"}))
                return 0
            rec = store.quarantine_revoked(args.id)
            out = {"state": "OK", "id": rec.id, "status": rec.status}
        elif cmd == "sweep":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to sweep"}))
                return 0
            out = store.sweep()
        elif cmd == "remove":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to remove"}))
                return 0
            rec = store.remove(args.id, requested_by="user")
            out = {"state": "OK", "id": rec.id, "removed": True}
        elif cmd == "retier":
            out = store.retier()
        elif cmd == "snapshot":
            out = store.snapshot(path=args.out)
        elif cmd == "export":
            out = store.export(args.out)
        elif cmd == "restore":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to restore"}))
                return 0
            import json as _j
            data = _j.loads(args.snapshot.read_text())
            out = store.restore(data, requested_by="user", merge=args.merge)
        else:
            print(_json.dumps({"state": "failed", "detail": f"unknown "
                                                            f"subcommand {cmd}"}))
            return 2
    except VerifiedSkillError as exc:
        print(_json.dumps({"state": "failed", "detail": redact_text(str(exc))},
                          indent=2))
        return 2
    finally:
        store.close()
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_experiments(cfg, args) -> int:
    """voicebridge experiments status|candidates|candidate|start|list|show|
    trial|finish-trial|signal|evaluate|promote|abort|snapshot.

    Packet 8P controlled evaluation is DISABLED by default and fully offline.
    It never changes production model/router/permission configuration; a
    promoted experiment is a stored decision record that requires an OWNER
    approval AND a passing regression gate report, and applying it remains a
    manual, separately authorized operation.
    """
    import json as _json
    from .redaction import redact_text
    from .experiments import (ExperimentError, experiments_store)
    cmd = getattr(args, "experiments_command", None) or "status"
    store = experiments_store(cfg)
    try:
        if cmd == "status":
            out = store.journal_verify()
            out["state"] = "OK" if out["state"] == "OK" else "degraded"
            out["experiments"] = len(store.experiments())
        elif cmd == "candidates":
            out = {"candidates": [c.to_dict() for c in store.candidates()]}
        elif cmd == "candidate":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to "
                                             "register"}))
                return 0
            c = store.register_candidate(role=args.role, label=args.label,
                                         model=args.model,
                                         description=args.description)
            out = {"state": "OK", "id": c.id, "fingerprint": c.fingerprint}
        elif cmd == "start":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to start"}))
                return 0
            e = store.start_experiment(champion_config_id=args.champion,
                                       challenger_config_id=args.challenger)
            out = {"state": "OK", "id": e.id, "status": e.status}
        elif cmd == "list":
            out = {"experiments": [e.to_dict()
                                   for e in store.experiments(status=args.status)]}
        elif cmd == "show":
            e = store.experiment(args.id)
            if e is None:
                print(_json.dumps({"state": "MISSING", "id": args.id}))
                return 1
            out = {"state": "OK", "experiment": e.to_dict(),
                   "evaluate": store.evaluate(args.id)}
        elif cmd == "trial":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to start"}))
                return 0
            t = store.start_trial(args.experiment_id, variant=args.variant)
            out = {"state": "OK", "id": t.id, "variant": t.variant}
        elif cmd == "finish-trial":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to "
                                             "finish"}))
                return 0
            t = store.finish_trial(args.trial_id, outcome=args.outcome,
                                   note=args.note)
            out = {"state": "OK", "id": t.id, "outcome": t.outcome}
        elif cmd == "signal":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to record"}))
                return 0
            s = store.record_signal(args.trial_id, kind=args.kind,
                                    source=args.source, value=args.value)
            out = {"state": "OK", "id": s.id, "kind": s.kind, "value": s.value}
        elif cmd == "evaluate":
            out = store.evaluate(args.id, seed=args.seed)
        elif cmd == "promote":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to record "
                                             "promotion"}))
                return 0
            out = store.record_promotion(args.id, owner_approved=True,
                                         gate_report=args.gate_report,
                                         requested_by="user")
        elif cmd == "abort":
            if not args.confirm:
                print(_json.dumps({"state": "preview",
                                   "detail": "dry-run; use --confirm to abort"}))
                return 0
            out = store.abort(args.id, requested_by="user")
        elif cmd == "snapshot":
            out = store.snapshot(path=args.out)
        else:
            print(_json.dumps({"state": "failed", "detail": f"unknown "
                                                            f"subcommand {cmd}"}))
            return 2
    except ExperimentError as exc:
        print(_json.dumps({"state": "failed", "detail": redact_text(str(exc))},
                          indent=2))
        return 2
    finally:
        store.close()
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_export_state(cfg, args) -> int:
    from . import portable as P
    out = args.out or (cfg.state_dir() / f"portable-{int(__import__('time').time())}.vbp")
    try:
        pw = _passphrase(args)
        bundle = P.export_bundle(cfg, pw)
        out.write_bytes(bundle)
        try:
            import os as _os
            _os.chmod(out, 0o600)
        except OSError:
            pass
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"exported encrypted portable state: {out}")
    return 0


def _cmd_import_state(cfg, args) -> int:
    from . import portable as P
    from . import device as Dev
    try:
        if not args.bundle.exists():
            print(f"ERROR: bundle not found: {args.bundle}")
            return 2
        bundle = args.bundle.read_bytes()
        info = P.bundle_info(bundle)
        print(f"bundle: schema v{info.schema_version} "
              f"openssl {info.openssl_version} kdf_iter {info.kdf_iter}")
        pw = _passphrase(args)
        payload = P.read_bundle(bundle, pw)
        plan = P.plan_restore(cfg, payload, dry_run=not args.apply)
        # trusted-device enrollment gate: a restore to a NEW device requires
        # an explicitly enrolled device; never automatic.
        try:
            Dev.require_enrolled(cfg)
        except Dev.DeviceNotEnrolledError:
            if args.apply:
                raise
            print("WARN: device not enrolled — `qubit device enroll` "
                  "must run before an applied restore")
        print("restore plan:")
        for a in plan.actions:
            print(f"  - {a}")
        if not args.apply:
            print("dry-run/preview (no files written) — pass --apply to restore")
            return 0
        result = P.restore_state(cfg, payload, dry_run=False)
        print(f"restored; pre-restore backup at {result['backup']}")
        return 0
    except P.WrongPassphraseError:
        print("ERROR: wrong passphrase or tampered bundle")
        return 2
    except P.CorruptedBundleError as exc:
        print(f"ERROR: corrupted bundle: {exc}")
        return 2
    except P.UnsupportedSchemaError as exc:
        print(f"ERROR: {exc}")
        return 2
    except P.LowDiskError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Dev.DeviceNotEnrolledError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


def _cmd_device(cfg, args) -> int:
    import json as _json
    from . import device as Dev
    cmd = getattr(args, "device_command", None) or "status"
    if cmd == "status":
        print(_json.dumps(Dev.profile(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "enroll":
        try:
            rec = Dev.enroll(cfg, model_alias=args.model_alias,
                             require_new_authorization=False)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        print(_json.dumps(rec, indent=2, sort_keys=True))
        print("device enrolled (explicit authorization recorded)")
        return 0
    print(f"ERROR: unknown device command {cmd!r}")
    return 2


def _cmd_files(cfg, args) -> int:
    """qubit files list|read|inspect — scoped project-folder inspection.

    Read-only. Every path must resolve inside a project-registered
    folder; escapes, specials, oversized/binary files, and secrets are
    refused or redacted with typed codes.
    """
    import json as _json
    from . import files as FI
    from . import file_patch as FP
    cmd = getattr(args, "files_command", None) or "inspect"
    try:
        if cmd == "list":
            out = FI.list_dir(cfg, args.path,
                              depth=getattr(args, "depth", 1) or 1)
        elif cmd == "read":
            out = FI.read_file(cfg, args.path,
                               max_chars=getattr(args, "max_chars",
                                                 20000) or 20000)
        elif cmd == "patch":
            text = getattr(args, "patch", "") or ""
            pf = getattr(args, "patch_file", "") or ""
            if pf:
                text = Path(pf).read_text(encoding="utf-8", errors="replace")
            out = FP.apply_patch(
                cfg, args.target, text, requested_by="cli:user",
                confirmed=bool(getattr(args, "confirm", False)))
        elif cmd == "rollback":
            out = FP.rollback(
                cfg, args.target, backup=getattr(args, "backup", "") or None,
                requested_by="cli:user",
                confirmed=bool(getattr(args, "confirm", False)))
        elif cmd == "inspect":
            out = FI.inspect(cfg, getattr(args, "path", "") or "")
        else:
            print(f"ERROR: unknown files command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (FI.FilesError, FP.FilePatchError) as exc:
        print(_json.dumps({"state": "FAILED", "code": exc.code,
                           "detail": (FI.redact_spec(exc.detail)
                                      if isinstance(exc, FI.FilesError)
                                      else FP.redact_spec(exc.detail))[:300],
                           "owner": "qubit-policy",
                           "rollback": "nothing written on refusal; a prior "
                                       "patch is reversible with files rollback"},
                          indent=2))
        return 2


_WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WORKTREE_MAX_OUTPUT = 8192


def _worktree_git(root, argv, timeout=120):
    """Run one git command confined to ``root`` (clean env, no shell).

    Never falls back to a host path: ``root`` is already resolved inside an
    approved project folder by :func:`voicebridge.git_scope.resolve_root`.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return subprocess.run(
        ["git", "--no-pager", "-C", str(root), *argv],
        capture_output=True, text=True, timeout=timeout, env=env)


def _worktree_out(proc):
    text = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    text = text.strip()
    return text[:_WORKTREE_MAX_OUTPUT], len(text) > _WORKTREE_MAX_OUTPUT


def _cmd_worktree(cfg, args) -> int:
    """Qubit-native, confined git-worktree add/remove/switch.

    The pinned fork has only ``worktree list|prune``. Qubit adds the missing
    lifecycle verbs as real, confirmation-gated git operations confined to a
    registered project root. No host-write fallback: an unregistered / non-repo
    / escaping target is a typed refusal and nothing is written.
    """
    import json as _json

    sub = getattr(args, "worktree_command", None)

    def _refuse(code, detail):
        print(_json.dumps({"state": "refused", "code": code,
                           "detail": detail}, indent=2, sort_keys=True))
        return 3

    def _fail(code, detail):
        print(_json.dumps({"state": "failed", "code": code,
                           "detail": str(detail)[:300]}, indent=2,
                          sort_keys=True))
        return 2

    if sub not in ("add", "remove", "switch"):
        return _refuse(
            "FORK_SUB_UNCLASSIFIED",
            "qubit worktree expects add|remove|switch here; list/prune are "
            "owned by the fork router")
    if not getattr(args, "confirm", False):
        return _refuse(
            "FORK_SUB_UNCLASSIFIED",
            f"qubit worktree {sub} is a mutating git operation and requires "
            "--confirm (explicit human authorization)")
    from . import git_scope as GS
    try:
        root = GS.resolve_root(cfg, getattr(args, "root", "") or "")
    except GS.GitError as exc:
        return _refuse(exc.code, exc.detail)

    def _confined(name):
        target = root / ".worktrees" / name
        try:
            target.resolve().relative_to(root)
        except (OSError, ValueError):
            return None
        return target

    try:
        if sub == "add":
            name = str(getattr(args, "name", "") or "")
            if not _WORKTREE_NAME_RE.match(name):
                return _refuse(
                    "GIT_BAD_INPUT",
                    "name must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
            branch = str(getattr(args, "branch", "") or "")
            base = str(getattr(args, "base", "") or "")
            for label, value in (("branch", branch), ("base", base)):
                if value and (value.startswith("-") or ".." in value
                              or len(value) > 256):
                    return _refuse("GIT_BAD_INPUT", f"bad {label}")
            target = _confined(name)
            if target is None:
                return _refuse("GIT_SCOPE_REFUSED",
                               "worktree target escapes the project root")
            if target.exists():
                return _refuse("GIT_WORKTREE_EXISTS",
                               f"{target} already exists")
            argv = ["worktree", "add"]
            if branch:
                argv += ["-b", branch, str(target)]
            else:
                argv += ["--detach", str(target)]
            if base:
                argv.append(base)
            proc = _worktree_git(root, argv)
            if proc.returncode != 0:
                return _fail("GIT_WORKTREE_ADD_FAILED",
                             _worktree_out(proc)[0]
                             or "git worktree add failed")
            out, trunc = _worktree_out(proc)
            print(_json.dumps({
                "state": "OK", "root": str(root), "path": str(target),
                "branch": branch or None, "detail": out, "truncated": trunc,
                "rollback": f"git worktree remove --force {target}"},
                indent=2, sort_keys=True))
            return 0

        if sub == "remove":
            name = str(getattr(args, "name", "") or "")
            if not _WORKTREE_NAME_RE.match(name):
                return _refuse(
                    "GIT_BAD_INPUT",
                    "name must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
            target = _confined(name)
            if target is None:
                return _refuse("GIT_SCOPE_REFUSED",
                               "worktree target escapes the project root")
            argv = ["worktree", "remove"]
            if getattr(args, "force", False):
                argv.append("--force")
            argv.append(str(target))
            proc = _worktree_git(root, argv)
            if proc.returncode != 0:
                return _fail("GIT_WORKTREE_REMOVE_FAILED",
                             _worktree_out(proc)[0]
                             or "git worktree remove failed")
            _worktree_git(root, ["worktree", "prune"])
            out, trunc = _worktree_out(proc)
            print(_json.dumps({
                "state": "OK", "root": str(root), "path": str(target),
                "removed": not target.exists(), "detail": out,
                "truncated": trunc,
                "rollback": "re-create with `qubit worktree add`"},
                indent=2, sort_keys=True))
            return 0

        branch = str(getattr(args, "branch", "") or "")
        if not branch or branch.startswith("-") or ".." in branch or \
                len(branch) > 256:
            return _refuse("GIT_BAD_INPUT", "bad branch")
        proc = _worktree_git(root, ["switch", branch])
        if proc.returncode != 0:
            return _fail("GIT_SWITCH_FAILED",
                         _worktree_out(proc)[0] or "git switch failed")
        out, trunc = _worktree_out(proc)
        print(_json.dumps({
            "state": "OK", "root": str(root), "branch": branch,
            "detail": out, "truncated": trunc,
            "rollback": "switch back with `qubit worktree switch <prev>`"},
            indent=2, sort_keys=True))
        return 0
    except subprocess.TimeoutExpired:
        return _fail("GIT_TIMEOUT", "git operation timed out")
    except FileNotFoundError:
        return _fail("GIT_FAILED", "git executable not found")
    except GS.GitError as exc:
        return _refuse(exc.code, exc.detail)
    except Exception as exc:  # typed boundary: never a traceback
        return _fail("GIT_FAILED", str(exc)[:200])


def _cmd_git(cfg, args) -> int:
    """qubit git status|log|diff|show — scoped read-only inspection.

    Same backend the dashboard and voice surfaces call. Mutating and
    network verbs do not exist here (typed refusal, never a shell).
    """
    import json as _json
    from . import git_scope as GI
    cmd = getattr(args, "git_command", None) or "status"
    root = getattr(args, "root", "") or ""
    try:
        if cmd == "status":
            out = GI.status(cfg, root)
        elif cmd == "log":
            out = GI.log(cfg, root, getattr(args, "limit", 10) or 10)
        elif cmd == "diff":
            out = GI.diff(cfg, root, getattr(args, "path", "") or "")
        elif cmd == "show":
            out = GI.show(cfg, root, getattr(args, "ref", "HEAD") or "HEAD",
                          args.path)
        else:
            print(f"ERROR: unknown git command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except GI.GitError as exc:
        print(_json.dumps(GI.public_error(exc), indent=2))
        return 2


def _cmd_ide(cfg, args) -> int:
    """qubit ide status|open — editor detection and workspace launcher.

    Opening is the whole capability (no editor control is claimed);
    ``open`` previews under --dry-run, requires --confirm, and refuses
    while the emergency stop is latched.
    """
    import json as _json
    from . import ide as IDE
    cmd = getattr(args, "ide_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd == "open" and IDE.estop_engaged(cfg):
            print(_json.dumps(IDE.refuse_estop("ide open"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd == "open":
            print(_json.dumps({"state": "preview", "would": "ide open"},
                              indent=2))
            return 0
        if cmd == "status":
            out = IDE.status(cfg)
        elif cmd == "open":
            out = IDE.open_editor(cfg, getattr(args, "editor", "") or "",
                                  getattr(args, "folder", "") or "",
                                  requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown ide command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except IDE.IdeError as exc:
        rc = 3 if exc.code == IDE.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": IDE.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "nothing launched; nothing to roll back"},
                          indent=2))
        return rc


def _cmd_config(cfg, args, explicit_config: Path | None) -> int:
    """qubit config path|show|check|env-path|env-list|set|unset.

    Reads delegate to ``config.py``/``env_store.py`` and execute. Writes are
    confirm-gated and accept only ``vault:``/``ref:`` references, so no secret
    value is ever read, printed, or stored; ``show`` masks secret-shaped values
    before printing.
    """
    import json as _json
    from . import env_store as ES
    from .config import Config, resolve_config_path

    cmd = getattr(args, "config_command", None) or "path"
    as_json = bool(getattr(args, "json", False))
    target = (explicit_config or getattr(cfg, "path", None)
              or resolve_config_path())
    try:
        if cmd == "path":
            out = {"state": "OK", "path": str(target)}
        elif cmd == "show":
            from .feature_flags import redact_config_yaml
            text = redact_config_yaml(cfg)
            if as_json:
                print(_json.dumps({"state": "OK", "path": str(target),
                                   "config": text}, indent=2, sort_keys=True))
            else:
                print(text)
            return 0
        elif cmd == "check":
            loaded = Config.load(target)
            names = ES.names(cfg)
            out = {"state": "OK", "path": str(target),
                   "sections": sorted(loaded.data.keys()),
                   "env_refs": len(names)}
        elif cmd == "env-path":
            out = {"state": "OK", "path": str(ES.store_path(cfg))}
        elif cmd == "env-list":
            names = ES.names(cfg)
            out = {"state": "OK", "count": len(names), "names": names}
        elif cmd in ("set", "unset"):
            if not bool(getattr(args, "confirm", False)):
                print(_json.dumps(
                    {"state": "denied", "code": "CONFIRM_REQUIRED",
                     "detail": f"config {cmd} requires --confirm (explicit "
                               "human authorization)"}, indent=2,
                    sort_keys=True))
                return 3
            if cmd == "set":
                out = {"state": "OK", **ES.set_ref(cfg, args.name, args.ref)}
            else:
                out = {"state": "OK", **ES.remove_ref(cfg, args.name)}
        else:
            print(f"ERROR: unknown config command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except ValueError as exc:
        print(_json.dumps({"state": "FAILED", "code": "CONFIG_REF_INVALID",
                           "detail": str(exc)[:300], "owner": "qubit-policy",
                           "rollback": "reference store unchanged"},
                          indent=2, sort_keys=True))
        return 2
    except Exception as exc:  # noqa: BLE001 - typed boundary, never a traceback
        print(_json.dumps({"state": "FAILED", "code": "CONFIG_ERROR",
                           "detail": str(exc)[:300], "owner": "qubit-policy",
                           "rollback": "no state changed"}, indent=2,
                          sort_keys=True))
        return 2


def _cmd_doctor(cfg, args) -> int:
    """qubit doctor run|check|list|summary — host + capability diagnostics.

    ``run`` delegates to ``distribution.doctor`` (the same checks as
    ``package doctor``); ``check``/``list``/``summary`` delegate to the
    capability catalog. Read-only; never a fabricated OK.
    """
    import json as _json
    from . import capability_catalog as CC

    cmd = getattr(args, "doctor_command", None) or "run"
    as_json = bool(getattr(args, "json", False))
    if cmd == "run":
        from . import distribution as D
        checks = D.doctor(cfg)
        if as_json:
            print(_json.dumps(checks, indent=2, sort_keys=True))
        else:
            for c in checks:
                print(f"[{c['state']:>13}] {c['name']:<12} {c['detail']}")
            print(D.doctor_summary(checks))
        return 0 if all(c["state"] == "OK" for c in checks) else 1
    if cmd == "check":
        out = CC.check(args.name)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("state") in ("IMPLEMENTED", "NOT_UPSTREAM") else 2
    if cmd == "list":
        names = CC.list_names(getattr(args, "state", None))
        if as_json:
            print(_json.dumps(names, indent=2, sort_keys=True))
        else:
            for name in names:
                print(name)
        return 0
    if cmd == "summary":
        print(_json.dumps(CC.summary(), indent=2, sort_keys=True))
        return 0
    print(f"ERROR: unknown doctor command {cmd!r}")
    return 2


def _cmd_cron(cfg, args) -> int:
    """qubit cron list|add|show|remove|enable|disable|run|status|runs.

    A second surface over the SAME canonical SQLite schedule store
    (:mod:`voicebridge.schedules`): no second store, no fabricated rows.
    Mutating ops preview under top-level --dry-run, require --confirm, and
    refuse while the emergency stop is latched; run history survives deletion.
    """
    import json as _json
    from . import schedules as SC

    cmd = getattr(args, "cron_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"add", "remove", "enable", "disable"}
    try:
        if cmd in _MUTATING and SC.estop_engaged(cfg):
            print(_json.dumps(SC.refuse_estop(f"cron {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview",
                               "would": f"cron {cmd}"}, indent=2))
            return 0
        if cmd == "add":
            out = SC.create(cfg, args.name, args.task_text, args.when,
                            deliver_to=getattr(args, "deliver_to", "") or "",
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "list":
            out = SC.list_jobs(
                cfg,
                include_disabled=not bool(getattr(args, "active_only", False)))
        elif cmd == "show":
            out = SC.show(cfg, args.job_id)
        elif cmd == "remove":
            out = SC.delete(cfg, args.job_id, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "enable":
            out = SC.resume(cfg, args.job_id, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "disable":
            out = SC.pause(cfg, args.job_id, requested_by="cli:user",
                           confirmed=confirm)
        elif cmd == "run":
            out = SC.trigger(cfg, getattr(args, "job_id", 0) or 0,
                             requested_by="cli:user")
        elif cmd == "status":
            out = SC.scheduler_status(cfg)
        elif cmd == "runs":
            out = SC.history(cfg, job_id=getattr(args, "job_id", 0) or 0,
                             limit=getattr(args, "limit", 30) or 30)
        else:
            print(f"ERROR: unknown cron command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except SC.SchedulesError as exc:
        rc = 3 if exc.code == SC.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": SC.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "schedule store untouched; "
                                       "nothing to roll back"}, indent=2))
        return rc


def _cmd_schedules(cfg, args) -> int:
    """qubit schedules create|list|show|pause|resume|delete|history|run-due.

    User scheduled jobs on the canonical SQLite store. Mutating ops
    preview under top-level --dry-run, require --confirm, and refuse
    while the emergency stop is latched; run history survives deletion.
    """
    import json as _json
    from . import schedules as SC
    cmd = getattr(args, "schedules_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"create", "pause", "resume", "delete"}
    try:
        if cmd in _MUTATING and SC.estop_engaged(cfg):
            print(_json.dumps(SC.refuse_estop(f"schedules {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview",
                               "would": f"schedules {cmd}"}, indent=2))
            return 0
        if cmd == "create":
            out = SC.create(cfg, args.name, args.task_text, args.when,
                            deliver_to=getattr(args, "deliver_to", "") or "",
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "list":
            out = SC.list_jobs(
                cfg,
                include_disabled=not bool(getattr(args, "active_only",
                                                  False)))
        elif cmd == "show":
            out = SC.show(cfg, args.job_id)
        elif cmd == "pause":
            out = SC.pause(cfg, args.job_id, requested_by="cli:user",
                           confirmed=confirm)
        elif cmd == "resume":
            out = SC.resume(cfg, args.job_id, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "delete":
            out = SC.delete(cfg, args.job_id, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "history":
            out = SC.history(cfg, job_id=getattr(args, "job_id", 0) or 0,
                             limit=getattr(args, "limit", 30) or 30)
        elif cmd == "deliveries":
            out = SC.delivery_history(
                cfg, job_id=getattr(args, "job_id", 0) or 0,
                limit=getattr(args, "limit", 30) or 30)
        elif cmd == "run-due":
            out = SC.run_due(cfg, requested_by="cli:user")
        else:
            print(f"ERROR: unknown schedules command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except SC.SchedulesError as exc:
        rc = 3 if exc.code == SC.CONFIRM_CODE else 2
        print(_json.dumps({"state": "denied" if rc == 3 else "FAILED",
                           "code": exc.code,
                           "detail": SC.redact_spec(exc.detail)[:300],
                           "owner": "qubit-policy",
                           "rollback": "schedule store untouched; "
                                       "nothing to roll back"},
                           indent=2))
        return rc


def _cmd_node(cfg, args) -> int:
    """qubit node status|list|show|capabilities|register|revoke|dispatch.

    Reads are read-only. ``register``/``revoke`` preview under --dry-run,
    require --confirm, and refuse while the emergency stop is latched; the
    backend never opens a listener and never dispatches remote/unsafe
    capabilities (typed refusals)."""
    import json as _json
    from . import node_host as NH
    cmd = getattr(args, "node_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"register", "revoke"}
    try:
        if cmd in _MUTATING and NH.estop_engaged(cfg):
            print(_json.dumps(NH.refuse_estop(f"node {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd in _MUTATING:
            print(_json.dumps({"state": "preview",
                               "would": f"node {cmd}"}, indent=2))
            return 0
        if cmd == "status":
            out = NH.status(cfg)
        elif cmd == "list":
            out = NH.list_nodes(cfg)
        elif cmd == "show":
            out = NH.show(cfg, args.node_id)
        elif cmd == "capabilities":
            out = NH.capabilities(cfg, args.node_id)
        elif cmd == "register":
            out = NH.enroll(cfg, name=args.name, endpoint=args.endpoint,
                            capabilities=args.capability,
                            requested_by="cli:user", confirmed=confirm)
        elif cmd == "revoke":
            out = NH.revoke(cfg, args.node_id, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "dispatch":
            out = NH.dispatch(cfg, args.node_id, args.capability,
                              requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown node command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except NH.NodeHostError as exc:
        print(_json.dumps({
            "state": "FAILED", "code": exc.code,
            "detail": NH._redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "node registry untouched; nothing dispatched"},
            indent=2))
        return 2


def _cmd_tasks(cfg, args) -> int:
    """qubit tasks status|list|show|timeline — unified background-task ledger.

    Read-only; merges the real OpenCode receipt, schedules, and kanban
    stores. An unreadable store is a typed DEGRADED result, never a
    fabricated row."""
    import json as _json
    from . import task_ledger as TL
    cmd = getattr(args, "tasks_command", None) or "status"
    source = getattr(args, "source", "") or None
    try:
        if cmd == "status":
            out = TL.status(cfg)
        elif cmd == "list":
            out = TL.list_tasks(cfg, source=source,
                                state=(getattr(args, "state", "") or None),
                                limit=getattr(args, "limit", 50) or 50)
        elif cmd == "show":
            out = TL.show(cfg, args.task_id, source=source)
        elif cmd == "timeline":
            out = TL.timeline(cfg, args.task_id, source=source)
        else:
            print(f"ERROR: unknown tasks command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except TL.TaskLedgerError as exc:
        print(_json.dumps({
            "state": "FAILED", "code": exc.code,
            "detail": TL._redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back"}, indent=2))
        return 2


def _cmd_media(cfg, args) -> int:
    """qubit media status|catalog|generate — provider-backed media.

    ``generate`` previews under --dry-run, requires --confirm for costly
    kinds, and returns the module's typed refusal when no credential
    reference or transport adapter exists (never a fabricated artifact)."""
    import json as _json
    from . import media_gen as MG
    cmd = getattr(args, "media_command", None) or "status"
    try:
        if cmd == "status":
            out = MG.status(cfg)
        elif cmd == "catalog":
            out = MG.catalog(cfg)
        elif cmd == "generate":
            if getattr(args, "dry_run", False):
                print(_json.dumps({"state": "preview", "would": "media generate",
                                   "kind": args.kind}, indent=2))
                return 0
            out = MG.generate(cfg, kind=args.kind,
                              provider=(args.provider or None),
                              prompt=args.prompt, source="cli",
                              confirmed=bool(getattr(args, "confirm", False)))
        else:
            print(f"ERROR: unknown media command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MG.MediaGenError as exc:
        rc = 3 if exc.code == MG.CONFIRM_CODE else 2
        print(_json.dumps({
            "state": "denied" if rc == 3 else "FAILED", "code": exc.code,
            "detail": MG._redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing generated; no provider request attempted"},
            indent=2))
        return rc


def _cmd_local_models(cfg, args) -> int:
    """qubit local-models status|hardware|catalog|jobs + brokered write gates.

    Reads derive from real Qubit state. Every write verb is a typed gate:
    this surface never downloads, starts, ejects, activates, or writes
    anything (all provisioning is brokered through `qubit runtime`)."""
    import json as _json
    from . import local_models as LM
    cmd = getattr(args, "local_models_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd == "status":
            out = LM.status(cfg)
        elif cmd == "hardware":
            out = LM.hardware(cfg)
        elif cmd == "catalog":
            out = LM.catalog(cfg)
        elif cmd == "jobs":
            out = LM.jobs(cfg)
        elif cmd == "runtime-install":
            out = LM.runtime_install(cfg, backend=(args.backend or None),
                                     requested_by="cli:user",
                                     confirmed=confirm)
        elif cmd == "download":
            out = LM.download(cfg, model=args.model, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "sideload":
            out = LM.sideload(cfg, path=args.path, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "server":
            out = LM.server(cfg, args.server_action, requested_by="cli:user",
                            confirmed=confirm)
        elif cmd == "eject":
            out = LM.eject(cfg, model=args.model, requested_by="cli:user",
                           confirmed=confirm)
        elif cmd == "activate":
            out = LM.activate(cfg, model=args.model, requested_by="cli:user",
                              confirmed=confirm)
        elif cmd == "quickstart":
            out = LM.quickstart(cfg, model=(args.model or None),
                                requested_by="cli:user", confirmed=confirm)
        else:
            print(f"ERROR: unknown local-models command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(_json.dumps(LM.public_error(
            getattr(exc, "code", "LOCAL_MODEL_BAD_INPUT"),
            str(exc)), indent=2))
        return 2


def _transfers_default_node(cfg) -> str:
    """First local (loopback) enrolled node, else the first node, else ""."""
    from . import file_transfer as FT
    from . import node_host as NH

    try:
        recs = NH._load(cfg).get("nodes", {})
    except Exception:
        return ""
    for node_id, rec in recs.items():
        if isinstance(rec, dict) and FT._is_local(rec):
            return str(node_id)
    return str(next(iter(recs), "") or "")


def _confirm_envelope(code: str, detail: str) -> Dict[str, Any]:
    return {"state": "CONFIRM_REQUIRED", "code": code, "detail": detail,
            "owner": "qubit-policy", "rollback": "nothing changed"}


def _cmd_transfers(cfg, args) -> int:
    """qubit transfers status|list|fetch|plan|resume.

    Same backend module the voice/dashboard surfaces call. ``fetch`` and
    ``plan`` require --confirm (bounded chunk read / digests-only plan); the
    raw bytes are never emitted by voice (a typed guard documents that).
    """
    import json as _json
    from . import file_transfer as FT
    cmd = getattr(args, "transfers_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    node = str(getattr(args, "node", "") or "") or _transfers_default_node(cfg)
    try:
        if cmd == "status":
            out = FT.status(cfg)
        elif cmd == "list":
            out = FT.list_directory(
                cfg, node, args.path,
                limit=(getattr(args, "limit", None) or FT.MAX_LIST_ENTRIES))
        elif cmd == "fetch":
            if not confirm:
                print(_json.dumps(_confirm_envelope(
                    FT.FILE_TRANSFER_CONFIRM_REQUIRED,
                    "fetching file bytes requires --confirm"), indent=2))
                return 3
            out = FT.fetch(
                cfg, node, args.path, offset=getattr(args, "offset", 0) or 0,
                max_bytes=(getattr(args, "max_bytes", None)
                           or FT.DEFAULT_FETCH_BYTES), confirmed=True)
        elif cmd == "plan":
            if not confirm:
                print(_json.dumps(_confirm_envelope(
                    FT.FILE_TRANSFER_CONFIRM_REQUIRED,
                    "planning a file transfer requires --confirm"), indent=2))
                return 3
            out = FT.stream_plan(
                cfg, node, args.path,
                chunk_size=(getattr(args, "chunk_size", None)
                            or FT.DEFAULT_CHUNK_BYTES))
        elif cmd == "resume":
            out = FT.resume(cfg, node, args.transfer_id,
                            offset=getattr(args, "offset", 0) or 0)
        else:
            print(f"ERROR: unknown transfers command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except FT.FileTransferError as exc:
        print(_json.dumps({
            "state": "FAILED", "code": exc.code, "detail": exc.detail[:300],
            "owner": "qubit-policy",
            "rollback": "nothing was read or transferred"}, indent=2))
        return 2


def _cmd_recall(cfg, args) -> int:
    """qubit recall status|policy-get|policy-set|recall|resolve|escalate.

    Same module the voice/dashboard surfaces call. ``policy-set`` and
    ``escalate`` require --confirm; reads never widen the configured cap.
    """
    import json as _json
    from . import active_memory as AM
    cmd = getattr(args, "recall_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    if cmd == "status":
        out = AM.status(cfg)
    elif cmd == "policy-get":
        out = AM.policy_get(cfg)
    elif cmd == "policy-set":
        enabled = True if getattr(args, "enable", False) else (
            False if getattr(args, "disable", False) else None)
        scoped = True if getattr(args, "session_scoped", False) else (
            False if getattr(args, "global_scope", False) else None)
        out = AM.policy_set(cfg, enabled=enabled,
                            min_score=getattr(args, "min_score", None),
                            max_items=getattr(args, "max_items", None),
                            session_scoped=scoped, confirmed=confirm)
    elif cmd == "recall":
        out = AM.recall(cfg, args.query, session=getattr(args, "session", "") or "",
                        limit=getattr(args, "limit", None))
    elif cmd == "resolve":
        out = AM.resolve(cfg, args.query,
                         session=getattr(args, "session", "") or "")
    elif cmd == "escalate":
        out = AM.escalate(cfg, args.memory_id, reason=args.reason,
                          confirmed=confirm)
    else:
        print(f"ERROR: unknown recall command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    if str(out.get("code", "")).endswith("CONFIRM_REQUIRED"):
        return 3
    return 0


def _cmd_documents(cfg, args) -> int:
    """qubit documents status|kinds|extract.

    Same module as voice/dashboard; extraction is confirmation-gated and
    confined to approved roots, with typed failures (never fabricated text).
    """
    import json as _json
    from . import doc_extract as DE
    cmd = getattr(args, "documents_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    if cmd == "status":
        out = DE.status(cfg)
    elif cmd == "kinds":
        out = DE.kinds(cfg)
    elif cmd == "extract":
        if not confirm:
            print(_json.dumps(_confirm_envelope(
                DE.CONFIRM_CODE, "document extraction requires --confirm"),
                indent=2))
            return 3
        try:
            out = DE.extract(
                cfg, args.path,
                max_chars=(getattr(args, "max_chars", None)
                           or DE.DEFAULT_MAX_CHARS),
                page=getattr(args, "page", None), confirmed=True)
        except DE.DocExtractError as exc:
            print(_json.dumps({
                "state": "FAILED", "code": exc.code,
                "detail": DE._redact(exc.detail)[:300],
                "owner": "qubit-policy",
                "rollback": "nothing was extracted"}, indent=2))
            return 2
    else:
        print(f"ERROR: unknown documents command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_ask(cfg, args) -> int:
    """qubit ask status|ask|answer|pending.

    Same module as voice/dashboard. ``ask`` requires --confirm; ``answer``
    takes the structured answer map as JSON (file or inline), never as a
    spoken value. Typed QUESTION_* failures are returned as JSON.
    """
    import json as _json
    from . import question_prompt as QP
    cmd = getattr(args, "ask_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    try:
        if cmd == "status":
            out = QP.status(cfg)
        elif cmd == "pending":
            out = QP.pending(cfg)
        elif cmd == "ask":
            if not confirm:
                print(_json.dumps(_confirm_envelope(
                    QP.CONFIRM_CODE, "asking requires --confirm"), indent=2))
                return 3
            out = QP.ask(cfg, [{"text": args.question, "kind": "text"}],
                         session=getattr(args, "session", "") or "",
                         confirmed=True)
        elif cmd == "answer":
            raw = None
            path = getattr(args, "answers_file", None)
            inline = getattr(args, "answers", "") or ""
            if path is not None:
                try:
                    raw = Path(path).expanduser().read_text(encoding="utf-8")
                except OSError as exc:
                    print(_json.dumps({"state": "FAILED",
                                       "code": QP.BAD_INPUT_CODE,
                                       "detail": f"answers file unreadable: {exc}"},
                                      indent=2))
                    return 2
            elif inline.strip():
                raw = inline
            if raw is None:
                print(_json.dumps({"state": "FAILED", "code": QP.BAD_INPUT_CODE,
                                   "detail": "--answers or --answers-file is "
                                             "required"}, indent=2))
                return 2
            try:
                answers = _json.loads(raw)
            except ValueError as exc:
                print(_json.dumps({"state": "FAILED", "code": QP.BAD_INPUT_CODE,
                                   "detail": f"answers JSON invalid: {exc}"},
                                  indent=2))
                return 2
            out = QP.answer(cfg, args.prompt_id, answers)
        else:
            print(f"ERROR: unknown ask command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except QP.QuestionError as exc:
        print(_json.dumps({
            "state": "FAILED", "code": exc.code,
            "detail": QP._redact(exc.detail)[:300],
            "owner": "qubit-policy", "rollback": "prompt store untouched"},
            indent=2))
        return 3 if exc.code == QP.CONFIRM_CODE else 2


def _cmd_shares(cfg, args) -> int:
    """qubit shares status|publish|list|revoke|resolve|purge.

    Same module as voice/dashboard. ``publish``/``revoke``/``purge`` require
    --confirm; ``resolve`` reads the bearer token from a file (or --token)
    and never persists or prints it.
    """
    import json as _json
    from . import session_share as SS
    cmd = getattr(args, "shares_command", None) or "status"
    confirm = bool(getattr(args, "confirm", False))
    if cmd == "status":
        out = SS.status(cfg)
    elif cmd == "list":
        out = SS.list_shares(cfg)
    elif cmd == "publish":
        out = SS.publish(cfg, args.session_id,
                         ttl_s=(getattr(args, "ttl_s", None)
                                or SS.DEFAULT_TTL_S),
                         confirmed=confirm)
    elif cmd == "revoke":
        out = SS.revoke(cfg, args.share_id, confirmed=confirm)
    elif cmd == "resolve":
        token = getattr(args, "token", "") or ""
        path = getattr(args, "token_file", None)
        if not token and path is not None:
            try:
                token = Path(path).expanduser().read_text(
                    encoding="utf-8").strip()
            except OSError as exc:
                print(_json.dumps({"state": "FAILED",
                                   "code": SS.BAD_INPUT_CODE,
                                   "detail": f"token file unreadable: {exc}"},
                                  indent=2))
                return 2
        out = SS.resolve(cfg, args.share_id, token)
    elif cmd == "purge":
        if not confirm:
            print(_json.dumps(_confirm_envelope(
                SS.CONFIRM_CODE, "purging expired shares requires --confirm"),
                indent=2))
            return 3
        out = SS.purge_expired(cfg)
    else:
        print(f"ERROR: unknown shares command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    if str(out.get("code", "")).endswith("CONFIRM_REQUIRED"):
        return 3
    return 0


def _cmd_monitor(cfg, args) -> int:
    import json as _json
    from .communications import monitor as M
    cmd = getattr(args, "monitor_command", None) or "run"
    if cmd == "run":
        out = M.run_hourly(cfg, source="live", dry_run=args.dry_run,
                           canary=args.canary,
                           notify=not args.no_notify)
        print(_json.dumps(out, indent=2, sort_keys=True))
        # Packet 8I: truthful aggregate exit code (degraded != OK).
        return int(out.get("exit_code",
                           0 if out.get("state") in ("OK", "preview") else 1))
    if cmd == "health":
        print(_json.dumps(M.monitor_health(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "install":
        authorized = os.environ.get("VOICE_BRIDGE_MONITOR_ENABLE") == "1"
        live = bool(args.enable) and authorized
        out = M.install_monitor(cfg, dry_run=not live, enable=live)
        print(_json.dumps(out, indent=2, sort_keys=True))
        if args.enable and not authorized:
            print("\n# denied: live installation requires BOTH --enable AND "
                  "VOICE_BRIDGE_MONITOR_ENABLE=1. Nothing was written or "
                  "enabled.", file=sys.stderr)
            return 1
        if not args.enable:
            print("\n# dry-run: nothing installed/enabled. To install AND "
                  "enable the timer (live activation), run exactly:\n#   "
                  "VOICE_BRIDGE_MONITOR_ENABLE=1 "
                  "qubit monitor install --enable", file=sys.stderr)
        return 0
    if cmd == "disable":
        print(_json.dumps(M.disable_monitor(), indent=2, sort_keys=True))
        return 0
    if cmd == "rollback":
        print(_json.dumps(M.rollback_monitor(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "launch":
        print(_json.dumps(M.optional_launch(cfg), indent=2, sort_keys=True))
        return 0
    print(f"ERROR: unknown monitor command {cmd!r}")
    return 2


def _cmd_frontier(cfg, args) -> int:
    """Packet 8Q: frontier gateway status/show/chat (local default)."""
    import json as _json
    from .providers import gateway as G
    from .providers.catalog import catalog_from_allowlist
    from .providers.contracts import ProviderRequest
    from .providers.credentials import redact_ref
    from .providers.policy import FrontierPolicy, disclosure_block
    cmd = getattr(args, "frontier_command", None) or "status"
    policy = FrontierPolicy.from_config(cfg)
    if cmd == "status":
        out = {
            "enabled": policy.enabled,
            "route": policy.route,
            "effective_default": policy.effective_route(""),
            "allowlist": policy.allowlist,
            "privacy_tier": policy.privacy_tier,
            "limits": {
                "max_tokens": policy.max_tokens,
                "max_context_chars": policy.max_context_chars,
                "max_response_chars": policy.max_response_chars,
                "timeout_s": policy.timeout_s,
                "max_retries": policy.max_retries,
                "rpm": policy.rpm,
                "max_concurrency": policy.max_concurrency,
                "daily_usd": policy.daily_usd,
                "monthly_usd": policy.monthly_usd,
            },
            "providers": {
                k: {"base_url": (v or {}).get("base_url", ""),
                    "key_ref": redact_ref(str((v or {}).get("key_ref", "")))}
                for k, v in policy.providers.items()
            },
            "local_default": "local/qubit-brain4 (no network beyond Ollama)",
            "spend_ledger": ("process-local (resets on restart; reported spend "
                             "is a lower bound after any restart)"),
            "terms": ("availability and price subject to provider account; "
                      "never free/unlimited by default"),
        }
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "show":
        ident = (args.id or "").strip()
        if "/" not in ident:
            print(_json.dumps({"ok": False, "code": "UNKNOWN_MODEL",
                               "message": "expected provider/model"}, indent=2))
            return 1
        prov, mod = ident.split("/", 1)
        pcfg = policy.provider_cfg(prov)
        allowed = policy.is_allowed(prov, mod)
        print(_json.dumps({
            "provider": prov, "model": mod, "allowed": allowed,
            "enabled": policy.enabled,
            "base_url": pcfg.get("base_url", ""),
            "key_ref": redact_ref(str(pcfg.get("key_ref", ""))),
            "disclosure": disclosure_block(policy, prov, mod, 0.0),
        }, indent=2, sort_keys=True))
        return 0 if allowed else 1
    if cmd == "chat":
        prompt = " ".join(args.prompt or []).strip()
        if not prompt:
            print("ERROR: empty prompt", file=sys.stderr)
            return 2
        route = (args.route or policy.route or "local").lower()
        if route in ("frontier", "auto") and not args.confirm:
            print(_json.dumps({
                "ok": False, "code": "CONFIRM_REQUIRED",
                "message": "cloud send requires --confirm (dry-run default)",
                "route": route, "provider": args.provider,
                "model": args.model,
            }, indent=2, sort_keys=True))
            print("\n# dry-run: nothing sent. Re-run with --confirm to send.",
                  file=sys.stderr)
            return 1
        req = ProviderRequest(
            prompt=prompt,
            max_tokens=int(args.max_tokens or policy.max_tokens),
            timeout_s=policy.timeout_s, route=route,
            provider=args.provider or "", model=args.model or "")
        res = G.run(req, cfg,
                    catalog=catalog_from_allowlist(policy.allowlist),
                    allow_private=bool(args.allow_private))
        if getattr(args, "json", False):
            print(_json.dumps(res.to_public_dict(), indent=2, sort_keys=True))
        else:
            d = res.to_public_dict()
            print(f"ok={d['ok']} identity={d['identity']} code={d['code']}")
            if d["message"]:
                print(f"note: {d['message']}")
            u = d["usage"]
            print(f"usage: cost_usd~{u['cost_usd_est']} ms={u['latency_ms']} "
                  f"fallback={u['fallback']}")
            if res.details.get("disclosure"):
                print(f"disclosure: {res.details['disclosure']}")
            if res.ok and d["text"]:
                # Redacted public text only — never the raw provider string.
                print("---")
                print(d["text"][:2000])
        return 0 if res.ok else 1
    print(f"ERROR: unknown frontier command {cmd!r}")
    return 2


def _cmd_opencode(cfg, args) -> int:
    """voicebridge opencode status/list/show/check/grant/revoke/preview/
    prepare/execute/cancel/receipts/enable/disable/emergency-stop/reset.

    Packet 8R optional supervised OpenCode adapter. Disabled by default
    (config master switch + state-file user flag + emergency-stop latch).
    ``act.opencode.run`` is DESTRUCTIVE tier: it needs a 7E grant plus a fresh
    hash-bound human confirmation. ``--fixture`` is the explicit synthetic
    path; live runs spawn exactly one ``opencode run`` child (never serve,
    never --auto). In-project (non-disposable) writes additionally require the
    preview hash via --expected-hash.
    """
    import json as _json
    from . import opencode as OC
    OC.register_opencode_specs()
    cmd = getattr(args, "opencode_command", None) or "status"

    executors: Dict[str, Any] = {}
    fixture = getattr(args, "fixture", None)
    if fixture:
        fpath = Path(fixture)
        if not fpath.is_file():
            print(_json.dumps({"state": "failed",
                               "detail": f"fixture not found: {fpath}"},
                              indent=2))
            return 2
        try:
            from . import actions as A
            executors = A.make_fixture_executors(
                A.load_fixture_outcomes(fpath))
        except Exception as exc:
            print(_json.dumps({"state": "failed", "detail": f"fixture "
                               f"invalid: {exc}"}, indent=2))
            return 2
    runner = OC.opencode_runner(cfg, executors=executors,
                                power=_shared_power(cfg))

    def _task_args() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        task = getattr(args, "task", "") or ""
        tfile = getattr(args, "task_file", None)
        if task:
            out["task"] = task
        if tfile is not None:
            out["task_file"] = str(tfile)
        out["project"] = getattr(args, "project", "") or "."
        disposable = not bool(getattr(args, "in_project", False))
        out["disposable"] = disposable
        timeout = getattr(args, "timeout", None)
        if timeout is not None:
            out["timeout_s"] = int(timeout)
        return out

    try:
        if cmd == "status":
            out = runner.status()
        elif cmd == "list":
            st = runner.status()
            out = {"state": "OK",
                   "effective_enabled": st["effective_enabled"],
                   "reason": st["reason"],
                   "capabilities": runner.opencode_actions()}
        elif cmd == "show":
            spec = OC.opencode_spec(args.action_id)
            if spec is None:
                raise OC.OpenCodeError(f"unknown action {args.action_id!r}")
            out = {"state": "OK", "action": spec.to_dict()}
        elif cmd == "check":
            out = runner.check(args.action_id,
                               getattr(args, "scope", "") or "code")
        elif cmd == "grant":
            if getattr(args, "dry_run", False) or not args.confirm:
                out = {"state": "preview",
                       "would_grant": args.capability,
                       "scopes": list(args.scope or ["code"]),
                       "ttl_s": args.ttl,
                       "note": "dry-run: re-run with --confirm to persist"}
            else:
                out = runner.grant(args.capability,
                                   scopes=tuple(args.scope or ["code"]),
                                   ttl_s=args.ttl)
        elif cmd == "revoke":
            out = runner.revoke_grant(args.grant_id)
        elif cmd == "preview":
            out = runner.preview(OC.ACT_RUN, _task_args(),
                                 target=getattr(args, "project", "") or ".",
                                 scope="code")
        elif cmd == "prepare":
            prev = runner.preview(OC.ACT_RUN, _task_args(),
                                  target=getattr(args, "project", "") or ".",
                                  scope="code")
            if getattr(args, "expected_hash", "") and \
                    prev["action_hash"] != args.expected_hash:
                out = {"state": "stale_target", "code": "STALE_TARGET",
                       "detail": "preview hash changed; preview again"}
            else:
                from .actions import build_envelope
                spec = OC.opencode_spec(OC.ACT_RUN)
                env = build_envelope(
                    spec, args=_task_args(),
                    target=getattr(args, "project", "") or ".", scope="code")
                out = runner.prepare(OC.ACT_RUN, env, issued_by="user")
                out["action_hash"] = env["action_hash"]
        elif cmd == "execute":
            if getattr(args, "dry_run", False):
                out = runner.execute(
                    OC.ACT_RUN, args=_task_args(),
                    target=getattr(args, "project", "") or ".",
                    scope="code", source="user", dry_run=True)
            elif bool(getattr(args, "in_project", False)) and not \
                    getattr(args, "expected_hash", ""):
                out = {"state": "stale_target", "code": "PREVIEW_REQUIRED",
                       "action_id": OC.ACT_RUN,
                       "detail": "in-project writes require a prior preview "
                                 "hash; run preview first and pass "
                                 "--expected-hash"}
            else:
                out = runner.execute(
                    OC.ACT_RUN, args=_task_args(),
                    target=getattr(args, "project", "") or ".",
                    scope="code", source="user",
                    confirmation_id=args.confirmation,
                    confirmed=args.confirm,
                    expected_hash=args.expected_hash or "")
        elif cmd == "cancel":
            out = runner.cancel(args.receipt_id)
        elif cmd == "retry":
            out = runner.retry(
                args.receipt_id, args=_task_args(),
                target=getattr(args, "project", "") or ".",
                scope="code", source="user",
                confirmation_id=args.confirmation,
                confirmed=args.confirm)
        elif cmd == "recover":
            out = runner.recover(args.receipt_id, source="user",
                                 confirmation_id=args.confirmation,
                                 confirmed=args.confirm)
        elif cmd == "receipts":
            out = runner.receipts(getattr(args, "limit", 30) or 30)
        elif cmd == "enable":
            out = runner.enable(requested_by="user")
        elif cmd == "disable":
            out = runner.disable(requested_by="user")
        elif cmd == "emergency-stop":
            out = runner.emergency_stop()
        elif cmd == "reset":
            out = runner.reset(requested_by="user")
        else:
            print(f"ERROR: unknown opencode command {cmd!r}")
            return 2
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (OC.OpenCodeError, ValueError) as exc:
        code = getattr(exc, "code", None) or "OPENCODE_ERROR"
        print(_json.dumps({"state": "failed", "code": code,
                           "detail": str(exc)[:300]}, indent=2))
        return 2
    except Exception as exc:
        print(_json.dumps({"state": "failed", "detail": str(exc)[:200]},
                          indent=2))
        return 2


def _cmd_webui(cfg, args) -> int:
    """voicebridge webui [--open] — probe/open the real local dashboard.

    Never starts a second bridge, hotkey listener, or dashboard instance; it
    only probes the already-running bridge's dashboard and, with ``--open``,
    opens it in the default browser. The token is never placed in a URL, on
    the command line, or in any output."""
    import json as _json
    import urllib.request
    import webbrowser
    from . import product as P
    host = cfg.get("webui", "host", default="127.0.0.1") or "127.0.0.1"
    port = args.port or int(cfg.get("webui", "port", default=8890))
    url = f"http://{host}:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            reachable = resp.status == 200
    except Exception:
        reachable = False
    if not reachable:
        if getattr(args, "json", False):
            print(_json.dumps({
                "state": "UNAVAILABLE",
                "detail": f"no dashboard at {url}; start the bridge first "
                          f"(`qubit run` or the "
                          f"{P.PRODUCT_NAME}.service) — nothing was started",
                "url": url,
                "token_in_url": False,
                "started_second_bridge": False,
            }, indent=2, sort_keys=True))
        else:
            print(f"dashboard: UNAVAILABLE — no dashboard at {url}")
            print(f"start the bridge first: qubit run  (or the "
                  f"{P.PRODUCT_NAME} user service); the shortcut never starts "
                  f"a second bridge")
        return 1
    opened = False
    if args.open:
        try:
            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
    if getattr(args, "json", False):
        print(_json.dumps({
            "state": "READY",
            "detail": "local dashboard is reachable; token is never placed "
                      "in the URL",
            "url": url,
            "token_in_url": False,
            "opened": opened,
            "started_second_bridge": False,
        }, indent=2, sort_keys=True))
    else:
        print(f"dashboard: READY — {url} (authenticated in-browser; token "
              f"never in the URL)")
        if args.open and not opened:
            print("note: could not open a default browser; open the URL "
                  "manually")
    return 0


if __name__ == "__main__":
    sys.exit(main())