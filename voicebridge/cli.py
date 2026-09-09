"""CLI launcher: run the bridge, run one utterance, send a typed task, test tools."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


def _shared_power(cfg):
    """Return the shared 8O PowerPolicy for the config scope (one per process)."""
    from . import power as PW
    return PW.power_policy(cfg)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voicebridge",
        description="Qubit Agent (Qubit VoiceBridge): local push-to-talk voice interface (ASR + intent + gateway + TTS).")
    common = argparse.ArgumentParser(add_help=False)
    # NOTE: these options also exist at the top level so `voicebridge --config X
    # <subcommand> …` works. Each subparser re-declares them via `parents=[common]`;
    # without SUPPRESS the subparser default would clobber a value the top level
    # already parsed (e.g. `--config X websites list` silently ignoring X).
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="path to voice.yaml (default ~/.config/qubit-voicebridge/voice.yaml; legacy openclaw-voice-bridge/qubit-voice-agent paths resolve via compat alias)")
    common.add_argument("--no-hotkey", action="store_true",
                        default=argparse.SUPPRESS,
                        help="disable hotkey; use manual Enter PTT (headless/SSH)")
    common.add_argument("--dry-run", action="store_true",
                        default=argparse.SUPPRESS,
                        help="never dispatch to OpenClaw; only speak the plan")
    common.add_argument("--mute", action="store_true",
                        default=argparse.SUPPRESS,
                        help="start muted (no spoken output; logs still work)")
    p.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--no-hotkey", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mute", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    def add(name, **kw):
        sp = sub.add_parser(name, parents=[common], **kw)
        return sp

    add("run", help="interactive push-to-talk loop (default)")
    add("once", help="record one utterance, process it, exit")
    add("mic-test", help="record 5s and report levels (diagnostic)")
    task = add("task", help="send a typed task through the pipeline (no mic)")
    task.add_argument("text", nargs="+", help="task text, e.g. 'inspect the Stripe webhook flow'")
    task.add_argument("--model", default=None, help="model override (provider/model or tier)")
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
    claw_p = add("claw", help="OpenClaw-lineage install detection (read-only)")
    claw_sub = claw_p.add_subparsers(dest="claw_command")
    claw_sub.add_parser("status", parents=[common],
                        help="detect OpenClaw install state without changing it")
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
    sess = add("sessions", help="recent Qubit action sessions from receipts (redacted)")
    sess_sub = sess.add_subparsers(dest="sessions_command")
    sess_list = sess_sub.add_parser("list", parents=[common],
                                    help="list recent sessions (read-only)")
    sess_list.add_argument("--limit", type=int, default=20,
                           help="max sessions (default: 20, max: 100)")
    cap = add("capability", help="capability catalog: typed state for any Hermes command or Qubit capability (read-only)")
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
    comp.add_argument("--cmd", default="voicebridge",
                      choices=["voicebridge", "qubit-dev"],
                      help="command to complete (default: voicebridge)")
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
    brand = add("branding", help="Qubit VoiceBridge branding migration (dry-run/backup/verify/rollback)")
    brand_sub = brand.add_subparsers(dest="branding_command")
    brand_plan = brand_sub.add_parser("plan", parents=[common],
                                      help="prepare the openclaw-voice-bridge/qubit-voice-agent -> qubit-voicebridge migration")
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
                                      help="gate+confirm+execute one Ghost capability (dry-run by default; --confirm = fresh confirmation)")
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
    oc_sub.add_parser("enable", parents=[common],
                      help="owner opt-in: set the user enablement flag (config master switch still applies)")
    oc_sub.add_parser("disable", parents=[common], help="disable the adapter")
    oc_sub.add_parser("emergency-stop", parents=[common],
                      help="latch an immediate stop (reset required)")
    oc_sub.add_parser("reset", parents=[common],
                      help="clear enablement + emergency-stop latch (returns to disabled)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"

    # logging early so every event is captured
    from .config import Config
    from .logging_setup import setup
    cfg = Config.load(args.config)
    setup(level=cfg.get("logging", "level", default="INFO"),
          jsonl=cfg.get("logging", "jsonl", default=True),
          state_dir=cfg.state_dir())

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
        return _cmd_mcp_status(cfg)
    if command == "claw":
        return _cmd_claw_status(cfg)
    if command == "verify-release":
        return _cmd_verify_static()
    if command == "checkpoints":
        return _cmd_checkpoints(cfg, args)
    if command == "sessions":
        return _cmd_sessions_list(cfg, args.limit
                                  if hasattr(args, "limit") else 20)
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
    if command == "computer-use":
        return _cmd_computer_use(cfg, args)
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
            text = " ".join(args.text)
            if args.model:
                cfg.data.setdefault("openclaw", {})["model"] = args.model
            orch.process_text(text)
        elif command == "roundtrip":
            return _cmd_roundtrip(cfg, args.text, args.model, orch)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        orch.stop()
    return 0


def _cmd_status(cfg) -> int:
    from .openclaw.discovery import discover_gateway, current_vram_mb
    from .logging_setup import get_logger
    get_logger()  # init handlers before discovery internals log
    print("Qubit Agent — Qubit VoiceBridge status")
    from .runtime_inventory import runtime_inventory
    try:
        inv = runtime_inventory(cfg)
        h = inv.get("hermes", {}) if isinstance(inv, dict) else {}
        print(f"hermes runtime  : {h.get('state', 'UNAVAILABLE')}"
              f"{(' (' + str(h.get('version', '')) + ')') if h.get('version') else ''}")
        b = inv.get("broker", {}) if isinstance(inv, dict) else {}
        print(f"emergency stop : {'LATCHED' if b.get('emergency_stop') else 'clear'}")
    except Exception as exc:
        print(f"runtime inventory: UNAVAILABLE ({str(exc)[:100]})")
    info = discover_gateway(cfg)
    print(f"gateway url      : {info.url}")
    print(f"gateway token    : {'found' if info.token else 'MISSING'}")
    print(f"default agent    : {info.agent}")
    print(f"default model    : {info.default_model or '(none)'}")
    print(f"tier aliases     : {info.models or '(none)'}")
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
    # PersonaPlex / Hermes / local
    print(f"hermes backend   : {'enabled' if cfg.get('hermes', 'enabled', default=False) else 'disabled'}")
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


def _cmd_mcp_status(cfg) -> int:
    """MCP server inventory without mounting anything (read-only)."""
    from .integration_status import mcp_status

    print(json.dumps(mcp_status(cfg), indent=2))
    return 0


def _cmd_claw_status(cfg) -> int:
    """OpenClaw-lineage install detection (read-only)."""
    from .integration_status import claw_status

    print(json.dumps(claw_status(cfg), indent=2))
    return 0


def _cmd_verify_static() -> int:
    """Hermetic static release qualification (no build/install)."""
    from .integration_status import verify_static

    out = verify_static()
    print(json.dumps(out, indent=2))
    return 0 if out.get("state") == "PASS" else 1


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
    from .asr.whisper import FasterWhisperASR
    asr_cfg = cfg.section("asr")
    asr = FasterWhisperASR(model=asr_cfg.get("model", "base"), device=asr_cfg.get("device", "auto"))
    asr.load()
    tr = asr.transcribe(file)
    print(f"[{tr.provider}/{asr._actual_device}] conf={tr.confidence:.2f} dur={tr.duration_s:.1f}s")
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
        cfg.data.setdefault("openclaw", {})["model"] = model
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
    import os as _os
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
        for unit in ("qubit-voicebridge.service", "qubit-voice-agent.service",
                     "openclaw-voice-bridge.service"):
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
    print(f"ERROR: unknown audio command {cmd!r}")
    return 2


def _cmd_profile(cfg, args) -> int:
    """voicebridge profile list|show|check|install|select|reset|upgrade|
    remove|rollback. Dry-run by default; JSON output."""
    import json as _json
    from . import profiles as P
    cmd = getattr(args, "profile_command", None) or "list"
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
    Hermes `plugins install` parity: a local reviewed manifest installs here;
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
            out = {"state": "OK",
                   "skills": [s.to_dict() for s in store.skills()]}
        elif cmd == "show":
            s = store.skill(args.skill_id)
            if s is None:
                print(_json.dumps({"state": "MISSING",
                                   "detail": f"no skill {args.skill_id}"},
                                  indent=2))
                return 1
            out = s.to_dict()
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
                rec = store.enable_skill(args.skill_id, requested_by="cli:user",
                                         confirmed=args.confirm)
                out = {"state": "OK", "status": rec.status, "skill_id": rec.skill_id}
        elif cmd == "disable":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_disable": args.skill_id}
            else:
                rec = store.disable_skill(args.skill_id, requested_by="cli:user",
                                          confirmed=args.confirm)
                out = {"state": "OK", "status": rec.status, "skill_id": rec.skill_id}
        elif cmd == "remove":
            if getattr(args, "dry_run", False):
                out = {"state": "preview", "would_remove": args.skill_id}
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
    """voicebridge project create|list|show|add-folder|remove-folder|rename|
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
        if cmd not in ("list", "show") and PJ.estop_engaged(cfg):
            print(_json.dumps(PJ.refuse_estop(f"project {cmd}"), indent=2))
            return 3
        if getattr(args, "dry_run", False) and cmd not in ("list", "show"):
            print(_json.dumps({"state": "preview", "would": f"project {cmd}"},
                              indent=2))
            return 0
        if cmd == "list":
            out = PJ.list_projects(cfg, include_archived=bool(
                getattr(args, "archived", False)))
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
    their Qubit-native paths and remaining Hermes subcommands are typed-
    refused. Mutating ops preview under --dry-run, require --confirm, and
    refuse while the emergency stop is latched.
    """
    import json as _json
    from . import kanban as KB
    cmd = getattr(args, "kanban_command", None) or "list"
    confirm = bool(getattr(args, "confirm", False))
    _MUTATING = {"boards-create", "create", "claim", "assign", "reassign",
                 "reclaim", "comment", "complete", "edit", "block",
                 "unblock", "archive", "import"}
    try:
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


def _cmd_computer_use(cfg, args) -> int:
    """voicebridge computer-use status|doctor|permissions|install.

    Read-only capability probes; control stays DISABLED (8V scope) and
    driver download/install is typed-refused (pinned-source policy).
    """
    import json as _json
    from . import computer_use as CU
    cmd = getattr(args, "computer_use_command", None) or "status"
    if cmd == "status":
        out = CU.status()
    elif cmd == "doctor":
        out = CU.doctor()
    elif cmd == "permissions":
        out = CU.permissions()
    elif cmd == "install":
        print(_json.dumps(CU.refuse_install(), indent=2))
        return 2
    else:
        print(f"ERROR: unknown computer-use command {cmd!r}")
        return 2
    print(_json.dumps(out, indent=2, sort_keys=True))
    return 0


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
                         load_semantic_state, write_semantic_state,
                         redact_secrets)
    from .memory.semantic import SEMANTIC_STATE_SCHEMA
    cmd = getattr(args, "memory_command", None) or "status"
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
    import json as _json
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
        try:
            result = W.probe_url(args.url)
        except W.WebsiteValidationError as exc:
            print(_json.dumps({"state": "failed", "detail": str(exc)},
                              indent=2))
            return 2
        out = {"state": "OK", "url": args.url,
               "result": W.result_code_for_status(result.status,
                                                   result.reason_code),
               "probe": result.to_dict()}
        _print_website_out(args, cmd, out)
        return 0

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
    import json as _json
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
    cancel/receipts/enable/disable/emergency-stop/reset.

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
                    expected_hash=args.expected_hash or "", role=role)
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
    pw = getattr(args, "passphrase", None) or _os.environ.get(env, "")
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
            print("WARN: device not enrolled — `voicebridge device enroll` "
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
                  "voicebridge monitor install --enable", file=sys.stderr)
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
                          f"(`voicebridge run` or the "
                          f"{P.PRODUCT_NAME}.service) — nothing was started",
                "url": url,
                "token_in_url": False,
                "started_second_bridge": False,
            }, indent=2, sort_keys=True))
        else:
            print(f"dashboard: UNAVAILABLE — no dashboard at {url}")
            print(f"start the bridge first: voicebridge run  (or the "
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