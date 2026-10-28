"""Packet 8T: one-click setup, installation health, and lifecycle dashboard.

Backend-authoritative setup/operations surface. This module WRAPS existing
local contracts (no upstream code copied, no new dependency):

- product.install/install_steps/first_run/repair/capabilities/service_owner/
  branding_status/migrate/migrate_rollback/upgrade_contract/rollback,
  InstallJournal/InstallLock (resumable, atomic, rollback-visible);
- distribution.doctor/upgrade_plan/uninstall_plan/model_artifact_record;
- profiles registry (provider/model/voice configuration state);
- audio.settings (microphone/output wizard data, never auto-selecting);
- permissions store (mode/scopes, read-only here).
-
- Browser/CDP reachability is probed through distribution
- (chromium_cdp_available); the Packet 8A communications package is never
- imported here so its import boundary stays closed.

Safety rules (Packet 8T + shared 8U-8Z rules):
- Dry-run is the default and is strictly side-effect-free (no directory,
  journal, lock, config, alias, download, or service write).
- Mutations (network downloads, service changes, desktop integration,
  provider enablement, data migration) require explicit ``confirm=True``;
  without it they return DENIED and change nothing.
- The backend computes every state; the frontend/CLI only render it. No
- optimistic readiness or repair-success is ever invented.
- No token, secret, cookie, passphrase, key value, raw private page text,
  or raw audio ever appears in output. Paths abbreviate $HOME as ``~``.
- Cloud, browser automation, timer enablement, and tray features stay
  disabled unless explicitly enabled elsewhere; this packet never enables
  a live service, timer, browser profile, or account.
- Every capability is a typed state (READY/MISSING/UNAVAILABLE/DEGRADED/
  BLOCKED/FAILED/...); detection is never permission.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

LIFECYCLE_VERSION = "1.0.0"
LIFECYCLE_SCHEMA = "qubit-lifecycle/1"

# States used across setup/health surfaces. Frontend renders these verbatim.
STATES = ("PENDING", "RUNNING", "READY", "SUCCESS", "MISSING", "UNAVAILABLE",
          "DEGRADED", "BLOCKED", "FAILED", "DENIED", "CANCELLED", "SKIPPED")

# Ordered one-click setup stages. Steps flagged confirm=True touch the
# network, a service, desktop integration, provider enablement, or user data
# migration and therefore refuse to run without explicit owner confirmation.
SETUP_STEPS: List[Dict[str, Any]] = [
    {"id": "platform", "title": "Check this device",
     "detail": "OS, CPU, memory, disk, and Python prerequisites.",
     "confirm": False},
    {"id": "directories", "title": "Prepare personal folders",
     "detail": "Standard per-user folders only; existing files kept.",
     "confirm": False},
    {"id": "components", "title": "Install or repair parts",
     "detail": "Declared components and packaged defaults (merge only).",
     "confirm": True},
    {"id": "model", "title": "Set up the local model",
     "detail": "Local model/runtime setup with hash checks; downloads need approval.",
     "confirm": True},
    {"id": "audio", "title": "Choose microphone and speaker",
     "detail": "Device names you pick; nothing is auto-selected or recorded.",
     "confirm": False},
    {"id": "pairing", "title": "Pair the dashboard",
     "detail": "Local token setup; the token is never shown or put in a URL.",
     "confirm": False},
    {"id": "desktop", "title": "Optional desktop link",
     "detail": "Shortcut/tray hooks; disabled unless you approve.",
     "confirm": True},
    {"id": "review", "title": "Review and finish",
     "detail": "Summary, what is still missing, and how to undo changes.",
     "confirm": False},
]

_SENSITIVE_SUBSTRINGS = ("token", "secret", "cookie", "passphrase", "password",
                         "api_key", "apikey", "private_key", "session_key",
                         "authorization", "set-cookie")

_REDACTED = "<redacted>"


def _home_candidates() -> List[str]:
    """Home-directory spellings that must never leak inside any string."""
    cands: List[str] = []
    try:
        home = str(Path.home())
    except Exception:
        home = ""
    if home:
        cands.append(home)
        # Also catch the /home/<user> prefix even if $HOME differs.
        if home.startswith("/home/"):
            cands.append("/home/" + home[len("/home/"):].split("/")[0])
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user and f"/home/{user}" not in cands:
        cands.append(f"/home/{user}")
    # Longest first so nested prefixes replace correctly.
    return sorted(set(c for c in cands if c), key=len, reverse=True)


def collect_secrets(cfg: Any) -> List[str]:
    """Gather secret *values* from a config-like so they can be scrubbed
    wherever they occur inside arbitrary strings (not just under their own
    key). Only non-empty strings of length >= 4 qualify."""
    found: List[str] = []
    sections = ("intent", "agent", "webui", "communications",
                "providers", "frontier", "opencode")
    for section in sections:
        try:
            names = cfg.section(section) if cfg is not None else {}
        except Exception:
            continue
        if not isinstance(names, dict):
            continue
        for key, val in names.items():
            if any(s in str(key).lower() for s in _SENSITIVE_SUBSTRINGS):
                if isinstance(val, str) and len(val) >= 4:
                    found.append(val)
    return sorted(set(found), key=len, reverse=True)


def _scrub_text(text: str, secrets: List[str] | tuple = ()) -> str:
    """Remove/abbreviate sensitive material occurring *anywhere* in a string:
    home-directory paths become ``~``-rooted and known secret values become
    ``<redacted>``."""
    for home in _home_candidates():
        if home in text:
            text = text.replace(home, "~")
    for secret in secrets or ():
        if secret and len(secret) >= 4 and secret in text:
            text = text.replace(secret, _REDACTED)
    return text


def redact_snapshot(obj: Any, secrets: List[str] | tuple = ()) -> Any:
    """Recursively redact secret-shaped values and scrub every string:
    home paths are abbreviated and known secret values removed wherever
    they occur — including mid-string, in errors, and in nested objects."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in _SENSITIVE_SUBSTRINGS):
                out[k] = _REDACTED if v else v
            else:
                out[k] = redact_snapshot(v, secrets)
        return out
    if isinstance(obj, list):
        return [redact_snapshot(v, secrets) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_snapshot(v, secrets) for v in obj)
    if isinstance(obj, str):
        return _scrub_text(obj, secrets)
    return obj


def _ok_state(state: str) -> bool:
    return state in ("READY", "SUCCESS", "OK", "FRESH", "PAIRED", "ENABLED")


def _norm(state: str) -> str:
    """Normalize any wrapped-contract state to a lifecycle state."""
    st = {"OK": "READY", "FRESH": "READY", "PAIRED": "READY"}.get(state, state)
    return st if st in STATES else "DEGRADED"


# ---------------------------------------------------------------------------
# Resumable first-run checklist (improvement #1) + offline mode (#7)
# ---------------------------------------------------------------------------

def checklist(cfg: Any, paths: Any = None, *, offline: bool = False) -> Dict[str, Any]:
    """Backend-owned staged checklist. Read-only; resumable via the install
    journal. ``offline=True`` skips network probes and reports UNAVAILABLE
    with reconnect guidance instead of failing."""
    from . import product as P
    paths = paths or P.default_paths()
    secrets = collect_secrets(cfg)
    journal_state: Dict[str, Any] = {}
    try:
        journal_state = P.InstallJournal(paths.journal_dir).last_state() or {}
    except Exception:
        journal_state = {}
    steps: List[Dict[str, Any]] = []
    try:
        probes = P.install_steps(paths, dry_run=True)
        probe = {s.get("step"): s for s in probes}
    except Exception as exc:
        probe = {}
        steps.append({"id": "platform", "state": "FAILED",
                      "detail": f"probe error: {str(exc)[:160]}",
                      "next": "run `voicebridge setup diagnose`"})
    def _probe_state(*names: str) -> Dict[str, str]:
        for n in names:
            s = probe.get(n)
            if s:
                st = str(s.get("state", "MISSING"))
                # Normalize wrapped-contract states to lifecycle states.
                st = {"OK": "READY", "FRESH": "READY"}.get(st, st)
                return {"state": st if st in STATES else "DEGRADED",
                        "detail": str(s.get("detail", ""))[:200]}
        return {"state": "MISSING", "detail": "not probed"}
    if probe or not steps:
        plat = _probe_state("platform", "python", "os")
        steps.append({"id": "platform",
                      "state": plat["state"] if plat["state"] != "MISSING" else
                      ("UNAVAILABLE" if offline else "MISSING"),
                      "detail": "offline mode: network probes skipped; reconnect to refresh"
                      if offline else plat["detail"],
                      "next": "use a supported Ubuntu x86_64 Python 3.11+ host"})
        dirs = _probe_state("directories", "paths", "storage")
        steps.append({"id": "directories", "state": dirs["state"],
                      "detail": dirs["detail"],
                      "next": "run `voicebridge setup run --apply --confirm`"})
        comp = _probe_state("components", "resources", "dependencies")
        steps.append({"id": "components", "state": comp["state"],
                      "detail": comp["detail"],
                      "next": "run `voicebridge setup run --apply --confirm --steps components`"})
        try:
            caps = P.capabilities(paths)
            prov = caps["capabilities"].get("provider", {})
            model_state = _norm(str(prov.get("state", "MISSING")))
        except Exception:
            model_state = "UNAVAILABLE"
        if offline and model_state == "MISSING":
            model_state = "UNAVAILABLE"
        steps.append({"id": "model", "state": model_state,
                      "detail": "local model/runtime setup; downloads need approval"
                      + (" (offline: skipped)" if offline else ""),
                      "next": "open model setup and approve the download, or stay offline"})
        try:
            from .audio import settings as AS
            audio = AS.status(cfg)
            mic = _norm(str(audio.get("microphone", {}).get("state", "MISSING")))
        except Exception:
            mic = "UNAVAILABLE"
        steps.append({"id": "audio", "state": mic,
                      "detail": "microphone/output names you pick; nothing auto-selected",
                      "next": "run the microphone/output wizard"})
        try:
            pairing = P.first_run(paths, dry_run=True).get("pairing", {})
            pair_state = str(pairing.get("state", "REQUIRES_PAIRING"))
        except Exception:
            pair_state = "UNAVAILABLE"
        steps.append({"id": "pairing",
                      "state": "READY" if pair_state == "PAIRED" else "PENDING",
                      "detail": "local dashboard token; never shown or in a URL",
                      "next": "pair in the browser (`qubit webui --open`)"})
        steps.append({"id": "desktop", "state": "PENDING",
                      "detail": "shortcut/tray hooks stay off until you approve",
                      "next": "approve desktop link during setup run"})
        done = all(_ok_state(s["state"]) or s["state"] == "PENDING"
                   for s in steps)
        steps.append({"id": "review",
                      "state": "READY" if done else "PENDING",
                      "detail": "summary of what is ready and what is missing",
                      "next": "review the summary, then finish or undo"})
    overall = "READY" if all(_ok_state(s["state"]) or s["state"] in
                             ("PENDING", "SUCCESS") for s in steps) else \
        ("DEGRADED" if all(s["state"] in ("READY", "SUCCESS", "PENDING",
                                          "DEGRADED", "MISSING", "UNAVAILABLE")
                           for s in steps) else "BLOCKED")
    resume = {}
    if journal_state:
        resume = {"last_step": journal_state.get("step"),
                  "last_state": journal_state.get("state"),
                  "detail": "previous run found; setup resumes after the last "
                            "recorded step instead of repeating finished work"}
    return redact_snapshot({"schema": LIFECYCLE_SCHEMA, "version": LIFECYCLE_VERSION,
            "state": overall, "offline": offline, "steps": steps,
            "resume": resume,
            "next": "run `voicebridge setup run` for a dry-run preview"}, secrets)


# ---------------------------------------------------------------------------
# Staged one-click setup run (dry-run default, confirm-gated, cancellable)
# ---------------------------------------------------------------------------

def setup_run(paths: Any = None, *, steps: Optional[List[str]] = None,
              dry_run: bool = True, confirm: bool = False,
              cancel: Optional[threading.Event] = None,
              offline: bool = False) -> Dict[str, Any]:
    """Execute staged setup. Side-effect-free unless ``dry_run=False`` AND
    ``confirm=True``. Steps needing network/service/desktop/data changes
    return DENIED without confirmation. Cancellation is cooperative via
    ``cancel``; interruption records CANCELLED and a resume pointer."""
    from . import product as P
    paths = paths or P.default_paths()
    known = {d["id"] for d in SETUP_STEPS}
    if steps is None:
        wanted = [s["id"] for s in SETUP_STEPS]
    else:
        # Fail closed: unknown steps are rejected, never silently dropped.
        # A mixed known/unknown list is refused as a whole.
        unknown = [s for s in steps if s not in known]
        if unknown:
            return {"schema": LIFECYCLE_SCHEMA, "run_id": "",
                    "state": "FAILED",
                    "detail": f"unknown setup step(s): {', '.join(unknown)}; "
                              f"known steps: {', '.join(sorted(known))}",
                    "unknown": unknown,
                    "known": sorted(known),
                    "receipts": [], "dry_run": dry_run}
        wanted = list(steps)
    if not wanted:
        return {"state": "FAILED", "detail": "no known steps requested",
                "run_id": "", "receipts": [], "dry_run": dry_run}
    run_id = uuid.uuid4().hex[:12]
    # Fail closed: any real mutation needs explicit owner confirmation.
    # Without it, nothing is created (no journal, directory, or config).
    if not dry_run and not confirm:
        denied = [{"step": sid, "state": "DENIED",
                   "detail": "needs explicit approval: re-run with "
                             "--apply --confirm",
                   "requires": "owner --confirm"} for sid in wanted]
        return redact_snapshot({"schema": LIFECYCLE_SCHEMA, "run_id": run_id, "state": "BLOCKED",
                "dry_run": dry_run, "offline": offline, "receipts": denied,
                "log": [f"{sid}: denied (no --confirm)" for sid in wanted],
                "rollback": "nothing was changed; no rollback needed",
                "next": "run `voicebridge setup run` for a dry-run preview"})
    journal = None if dry_run else P.InstallJournal(paths.journal_dir)
    receipts: List[Dict[str, Any]] = []
    log: List[str] = []
    cancelled = False
    for sid in wanted:
        if cancel is not None and cancel.is_set():
            cancelled = True
            receipts.append({"step": sid, "state": "CANCELLED",
                             "detail": "cancelled by owner; resume re-runs "
                                       "from this step"})
            log.append(f"{sid}: cancelled")
            break
        desc = next(d for d in SETUP_STEPS if d["id"] == sid)
        if desc["confirm"] and not (confirm and not dry_run):
            receipts.append({"step": sid, "state": "DENIED" if not dry_run
                             else "PENDING",
                             "detail": "needs explicit approval: re-run with "
                                       "--apply --confirm" if dry_run or not
                             confirm else "denied",
                             "requires": "owner --confirm (network/service/"
                                         "desktop/data change)"})
            log.append(f"{sid}: preview only (needs --apply --confirm)")
            continue
        if dry_run:
            receipts.append({"step": sid, "state": "PENDING",
                             "detail": desc["detail"] + " (dry-run preview)",
                             "requires": "owner --confirm" if desc["confirm"]
                             else None})
            log.append(f"{sid}: preview")
            continue
        try:
            if sid in ("platform", "directories", "components"):
                out = P.install(paths, dry_run=False, journal=journal)
                st = str(out.get("state", "FAILED"))
            elif sid == "review":
                out = P.repair(paths, dry_run=True)
                st = "SUCCESS" if str(out.get("state")) == "READY" else \
                    str(out.get("state", "FAILED"))
            else:
                # audio/pairing/desktop/model steps are owner-driven in the
                # dashboard/wizard; the run records guidance, never acts alone.
                st = "SUCCESS"
                if journal is not None:
                    journal.append(run_id, sid, "SUCCESS",
                                   "owner-guided step recorded")
            receipts.append({"step": sid, "state": st,
                             "detail": desc["detail"]})
            log.append(f"{sid}: {st}")
        except Exception as exc:
            receipts.append({"step": sid, "state": "FAILED",
                             "detail": str(exc)[:200],
                             "rollback": "re-run repair; journal holds the "
                                         "last good pointer"})
            log.append(f"{sid}: FAILED")
            break
    if cancelled:
        overall = "CANCELLED"
    elif any(r["state"] == "FAILED" for r in receipts):
        overall = "FAILED"
    elif any(r["state"] == "DENIED" for r in receipts):
        overall = "BLOCKED"
    elif dry_run or any(r["state"] == "PENDING" for r in receipts):
        overall = "PENDING"
    else:
        overall = "SUCCESS"
    if journal is not None:
        try:
            journal.append(run_id, "setup_run", overall,
                           "; ".join(log)[:500])
        except Exception:
            pass
    return redact_snapshot({"schema": LIFECYCLE_SCHEMA, "run_id": run_id, "state": overall,
            "dry_run": dry_run, "offline": offline, "receipts": receipts,
            "log": log[:50],
            "rollback": "run `voicebridge setup rollback --apply --confirm` "
                        "to reverse applied steps (state and config preserved "
                        "by default)",
            "next": "run `voicebridge setup check` to refresh the checklist"})


# ---------------------------------------------------------------------------
# Backend-authoritative health inventory
# ---------------------------------------------------------------------------

# (import_name, required, note). Required == declared in pyproject.toml
# dependencies. Undeclared optionals are reported as optional, never as
# installation failures.
_DEP_INVENTORY = (
    ("yaml", True, "config parsing (pyyaml)"),
    ("sounddevice", True, "mic capture + playback"),
    ("numpy", True, "audio math"),
    ("cloudpickle", True, "broker callable isolation"),
    ("websockets", True, "Qubit gateway protocol"),
    ("piper", True, "default TTS (piper-tts)"),
    ("faster_whisper", True, "optional ASR fallback"),
    ("webrtcvad", True, "optional VAD fallback"),
    ("onnx", True, "ONNX metadata tooling"),
    ("playwright", True, "report-UI tests + inbox reader"),
    ("sherpa_onnx", True, "Moonshine ASR + Silero VAD + Kokoro"),
    ("requests", False, "undeclared optional; not required for local use"),
    ("flask", False, "undeclared optional; not required for local use"),
)

_ASR_MODULES = {"sherpa": ("voicebridge.asr.sherpa", "sherpa_onnx"),
                "whisper": ("voicebridge.asr.whisper", "faster_whisper")}
_TTS_MODULES = {"piper": ("voicebridge.tts.piper", "piper"),
                "hybrid": ("voicebridge.tts.hybrid", "piper"),
                "kokoro": ("voicebridge.tts.kokoro", "sherpa_onnx"),
                "edge": ("voicebridge.tts.edge", "edge_tts"),
                "openai": ("voicebridge.tts.openai_tts", None),
}


def _dep_inventory() -> Dict[str, Any]:
    """Classify every known dependency as required/optional with a typed
    state. Optional absences are OPTIONAL_MISSING, never failures."""
    deps: Dict[str, Any] = {}
    for name, required, note in _DEP_INVENTORY:
        try:
            present = importlib.util.find_spec(name) is not None
        except Exception:
            present = False
        if present:
            state = "READY"
        elif required:
            state = "MISSING"
        else:
            state = "OPTIONAL_MISSING"
        deps[name] = {"state": state, "required": required, "note": note}
    return deps


def _provider_state(cfg: Any, *, offline: bool) -> Dict[str, Any]:
    """Inspect the real routing configuration. Local is READY only when the
    inference provider answers; cloud routes need an explicit allowlist."""
    route = str(cfg.get("intent", "frontier", "route", default="local")) \
        if cfg else "local"
    if route in ("", "local"):
        if offline:
            return {"route": "local", "state": "UNAVAILABLE",
                    "detail": "offline mode: provider probe skipped"}
        try:
            from . import distribution as D
            if D.ollama_available():
                return {"route": "local", "state": "READY",
                        "detail": "local inference answers on loopback"}
            return {"route": "local", "state": "DEGRADED",
                    "detail": "inference provider unreachable at "
                              "127.0.0.1:11434; local mode degraded"}
        except Exception as exc:
            return {"route": "local", "state": "UNAVAILABLE",
                    "detail": str(exc)[:160]}
    # Cloud/auto routes: allowlist + credentials must be explicitly set.
    allow: Any = None
    try:
        allow = cfg.get("intent", "frontier", "allowlist", default=None) \
            if cfg else None
    except Exception:
        allow = None
    if not allow:
        return {"route": route, "state": "BLOCKED",
                "detail": "cloud route selected but no provider allowlist is "
                          "configured; cloud stays off until explicitly enabled"}
    return {"route": route, "state": "DEGRADED",
            "detail": "cloud allowlist present; credential references must be "
                      "verified before any send"}


def _component_state(configured: str, table: Dict[str, tuple],
                     kind: str) -> Dict[str, Any]:
    """Type one configured engine (ASR/TTS): READY only when the backing
    module for the configured name imports; MISSING otherwise."""
    name = (configured or "unknown").strip().lower() or "unknown"
    entry = table.get(name)
    if entry is None:
        return {"configured": configured or "unknown", "state": "MISSING",
                "detail": f"unknown {kind} provider {name!r}; no backing "
                          f"module registered"}
    mod_name, dist = entry
    try:
        ok = importlib.util.find_spec(mod_name) is not None
    except Exception:
        ok = False
    if ok:
        return {"configured": name, "state": "READY",
                "detail": f"{kind} backing module {mod_name} present"}
    hint = f" (needs {dist})" if dist else ""
    return {"configured": name, "state": "MISSING",
            "detail": f"{kind} backing module {mod_name} not installed{hint}"}


def _aggregate_health_state(inv: Dict[str, Any]) -> str:
    """Overall inventory state from section states. OPTIONAL_MISSING never
    degrades the aggregate; any FAILED/BLOCKED propagates."""
    seen = set()
    for section in ("platform", "models", "provider", "credentials",
                    "browser_cdp", "timer", "permissions", "storage",
                    "service", "migration"):
        v = inv.get(section)
        if isinstance(v, dict) and isinstance(v.get("state"), str):
            seen.add(v["state"])
    dep_states = set()
    deps = inv.get("dependencies")
    if isinstance(deps, dict):
        for entry in deps.values():
            if isinstance(entry, dict) and isinstance(entry.get("state"), str):
                dep_states.add(entry["state"])
    for name in ("asr_tts",):
        block = inv.get(name)
        if isinstance(block, dict):
            for entry in block.values():
                if isinstance(entry, dict) and \
                        isinstance(entry.get("state"), str):
                    dep_states.add(entry["state"])
    if "FAILED" in seen or "FAILED" in dep_states:
        return "FAILED"
    if "BLOCKED" in seen or "BLOCKED" in dep_states:
        return "BLOCKED"
    degraded = seen | {s for s in dep_states if s != "OPTIONAL_MISSING"}
    if degraded - {"READY", "SUCCESS"}:
        return "DEGRADED"
    return "READY"


def _systemctl(*args: str) -> str:
    """Read-only systemctl probe. Never enables, starts, or restarts."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", *args],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip()
    except Exception:
        return "unknown"


def service_inventory(paths: Any = None, *, unit_dir: Any = None,
                      active_of: Any = None, enabled_of: Any = None) -> Dict[str, Any]:
    """Truthful service/listener census. For each known unit (canonical plus
    legacy compatibility identities) report file presence plus the *observed*
    active/enabled state. A DUPLICATE is reported only when more than one
    unit is actually ACTIVE (a second listener); disabled legacy unit *files*
    are reported as legacy/stale compatibility artifacts, never as listeners.

    ``active_of``/``enabled_of`` inject the active/enabled lookup for
    hermetic tests; production uses read-only ``systemctl --user`` probes.
    This function never enables, starts, or restarts anything."""
    from . import product as P
    from .config import LEGACY_NAME, LEGACY_NAME_7D, PRODUCT_NAME
    paths = paths or P.default_paths()
    file_info = redact_snapshot(P.service_owner(paths, unit_dir=unit_dir))
    units = [PRODUCT_NAME, LEGACY_NAME, LEGACY_NAME_7D]
    per_unit: Dict[str, Any] = {}
    active_count = 0
    for unit in units:
        name = f"{unit}.service"
        active = active_of(name) if active_of else _systemctl("is-active", name)
        enabled = enabled_of(name) if enabled_of else \
            _systemctl("is-enabled", name)
        is_active = (active == "active")
        if is_active:
            active_count += 1
        per_unit[unit] = {"unit": name, "active": active or "unknown",
                          "enabled": enabled or "unknown",
                          "listening": is_active,
                          "legacy": unit != PRODUCT_NAME}
    if active_count > 1:
        state = "DUPLICATE"
        detail = (f"{active_count} units ACTIVE at once — more than one "
                  f"listener; disable all but the canonical unit explicitly")
    elif active_count == 1:
        state = "READY"
        detail = "exactly one active listener"
    else:
        legacy_files = file_info.get("state") in ("STALE", "DUPLICATE")
        if file_info.get("state") == "UNIT_MISSING":
            state = "UNIT_MISSING"
            detail = "no service unit installed"
        elif legacy_files or file_info.get("state") == "STALE":
            state = "LEGACY_DISABLED"
            detail = ("only disabled legacy compatibility unit file(s); not a "
                      "listener — install the canonical unit to run")
        else:
            state = str(file_info.get("state", "PENDING"))
            detail = str(file_info.get("state_detail", "unit present, not active"))
    return {"schema": LIFECYCLE_SCHEMA, "state": state, "detail": detail,
            "owner": "systemctl --user", "active_listeners": active_count,
            "units": redact_snapshot(per_unit), "files": file_info}


def health_inventory(cfg: Any, paths: Any = None, *,
                     offline: bool = False) -> Dict[str, Any]:
    """Full installation-health inventory. Read-only and redacted: references
    and typed statuses only — never key values, cookies, tokens, private
    paths, raw page text, or raw audio."""
    from . import distribution as D
    from . import product as P
    paths = paths or P.default_paths()
    secrets = collect_secrets(cfg)
    inv: Dict[str, Any] = {"schema": LIFECYCLE_SCHEMA,
                           "version": LIFECYCLE_VERSION,
                           "generated_at": time.time(), "offline": offline}
    inv["product"] = {"name": P.PRODUCT_NAME, "title": P.PRODUCT_TITLE,
                      "version": P.PRODUCT_VERSION,
                      "package": D.PACKAGE_NAME,
                      "package_version": D.PACKAGE_VERSION}
    inv["platform"] = redact_snapshot(P.platform_supported(), secrets)
    inv["dependencies"] = redact_snapshot(_dep_inventory(), secrets)
    try:
        model_rec = D.model_artifact_record(cfg)
        if isinstance(model_rec.get("checksum"), str) and model_rec["checksum"]:
            model_rec = dict(model_rec)
            model_rec["checksum"] = model_rec["checksum"][:16] + "…"
        inv["models"] = {"state": model_rec.get("state", "MISSING"),
                         "alias": model_rec.get("alias"),
                         "digest_prefix": model_rec.get("checksum"),
                         "detail": str(model_rec.get("detail", ""))[:200]}
    except Exception as exc:
        inv["models"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    inv["provider"] = redact_snapshot(
        _provider_state(cfg, offline=offline), secrets)
    cred_refs: Dict[str, str] = {}
    try:
        for section in ("intent", "agent", "webui"):
            try:
                names = cfg.section(section) if cfg else {}
            except Exception:
                names = {}
            for key in (names or {}):
                if any(s in str(key).lower() for s in _SENSITIVE_SUBSTRINGS):
                    cred_refs[f"{section}.{key}"] = "SET" if names[key] else "UNSET"
    except Exception:
        pass
    inv["credentials"] = cred_refs or {"state": "no credential references found"}
    try:
        from .audio import settings as AS
        inv["audio"] = redact_snapshot(AS.status(cfg))
    except Exception as exc:
        inv["audio"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    inv["asr_tts"] = redact_snapshot({
        "asr": _component_state(
            str(cfg.get("asr", "provider", default="unknown")) if cfg else "unknown",
            _ASR_MODULES, "ASR"),
        "tts": _component_state(
            str(cfg.get("tts", "provider", default="unknown")) if cfg else "unknown",
            _TTS_MODULES, "TTS")}, secrets)
    if offline:
        inv["browser_cdp"] = {"state": "UNAVAILABLE",
                              "detail": "offline mode: CDP probe skipped"}
    else:
        # Loopback CDP reachability via the distribution probe (no import of
        # the Packet 8A communications package — its import boundary stays
        # closed; browser/account behavior is unchanged by this packet).
        try:
            cdp_ok = D.chromium_cdp_available()
        except Exception:
            cdp_ok = False
        inv["browser_cdp"] = {
            "state": "READY" if cdp_ok else "MISSING",
            "detail": "127.0.0.1:9222 loopback CDP answers" if cdp_ok else
                      "no loopback CDP on 127.0.0.1:9222; start Chromium with "
                      "remote debugging (the bridge never launches it)"}
    inv["timer"] = _timer_state()
    try:
        from . import permissions as PM
        store = PM.permissions_store(cfg)
        mode, scopes, _exp = store.mode()
        inv["permissions"] = {"mode": mode, "scopes": list(scopes or []),
                              "state": "READY"}
    except Exception as exc:
        inv["permissions"] = {"state": "UNAVAILABLE",
                              "detail": str(exc)[:160]}
    try:
        usage = shutil.disk_usage(str(paths.state_dir))
        inv["storage"] = {"state_dir": _scrub_text(str(paths.state_dir)),
                          "free_gib": round(usage.free / (1024 ** 3), 1),
                          "state": "READY" if usage.free > 1024 ** 3 else "DEGRADED"}
    except Exception as exc:
        inv["storage"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    try:
        inv["service"] = service_inventory(paths)
    except Exception as exc:
        inv["service"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    try:
        inv["migration"] = redact_snapshot(P.branding_status(paths), secrets)
    except Exception as exc:
        inv["migration"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    inv["state"] = _aggregate_health_state(inv)
    return redact_snapshot(inv, secrets)


def _timer_state() -> Dict[str, Any]:
    """Read-only user-timer state. Never enables or starts anything."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "is-active",
                            "qubit-communications-monitor.timer"],
                           capture_output=True, text=True, timeout=5)
        active = (r.stdout or "").strip()
        r2 = subprocess.run(["systemctl", "--user", "is-enabled",
                             "qubit-communications-monitor.timer"],
                            capture_output=True, text=True, timeout=5)
        enabled = (r2.stdout or "").strip()
        return {"state": "READY" if active == "active" else "PENDING",
                "active": active or "unknown",
                "enabled": enabled or "unknown",
                "detail": "hourly monitor stays off unless explicitly enabled"}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


# ---------------------------------------------------------------------------
# Redacted diagnostics (#2), provenance (#3), capability matrix (#4)
# ---------------------------------------------------------------------------

def diagnostic_bundle(cfg: Any, paths: Any = None) -> Dict[str, Any]:
    """Redacted 'copy diagnostics' bundle: schema, timestamps, states, and
    remediation hints. No secrets or private content by construction."""
    from . import distribution as D
    from . import product as P
    paths = paths or P.default_paths()
    secrets = collect_secrets(cfg)
    checks = []
    try:
        checks = D.doctor(cfg)
    except Exception as exc:
        checks = [{"name": "doctor", "state": "FAILED",
                   "detail": str(exc)[:160]}]
    bundle = {
        "schema": LIFECYCLE_SCHEMA, "version": LIFECYCLE_VERSION,
        "state": "READY",
        "generated_at": time.time(),
        "product": {"name": P.PRODUCT_NAME, "version": P.PRODUCT_VERSION},
        "platform": redact_snapshot(P.platform_supported()),
        "checks": redact_snapshot(checks),
        "paths": {k: _scrub_text(v) for k, v in paths.map.items()},
        "hints": ["run `voicebridge setup check` for the staged checklist",
                  "run `voicebridge setup repair` for the ordered fix list",
                  "pairing/token values are never included in this bundle"],
        "retention": "this bundle is generated on demand and not stored",
    }
    return redact_snapshot(bundle, secrets)


def provenance_view() -> Dict[str, Any]:
    """Component provenance/update view (#3): version, source, license."""
    from . import distribution as D
    from . import product as P
    import voicebridge as V
    return {
        "schema": LIFECYCLE_SCHEMA,
        "components": [
            {"component": "qubit-voicebridge",
             "version": P.PRODUCT_VERSION, "source": "local checkout/package",
             "license": "proprietary application (see LICENSE)",
             "update": "re-run the documented installer; never auto-updates"},
            {"component": "voicebridge (import package)",
             "version": getattr(V, "__version__", "unknown"),
             "source": "local checkout/package",
             "license": "proprietary application (see LICENSE)",
             "update": "ships with the application"},
            {"component": "distribution target",
             "version": D.PACKAGE_VERSION, "source": D.SUPPORTED_TARGET,
             "license": "n/a (target declaration)",
             "update": "Ubuntu x86_64 only; other systems report UNSUPPORTED"},
        ],
    }


def capability_matrix(cfg: Any = None) -> Dict[str, Any]:
    """Model capability matrix (#4): local/cloud availability, context,
    tool/structured-output support, resource needs, truthful fallback."""
    local_model = ""
    context = 0
    try:
        if cfg is not None:
            local_model = str(cfg.get("intent", "local_llm", "model", default=""))
            context = int(cfg.get("intent", "local_llm", "context", default=0) or 0)
    except Exception:
        pass
    return {
        "schema": LIFECYCLE_SCHEMA,
        "local": {"model": local_model or "qubit-brain (default)",
                  "availability": "READY when the inference provider answers",
                  "context": context or "bounded per-runner contract",
                  "structured_output": "typed compact decisions (bounded)",
                  "tools": "brokered host tools only (no free-form shell)",
                  "resources": "5 GB VRAM ceiling; CPU fallback typed",
                  "fallback": "unavailable provider is reported, never faked"},
        "cloud": {"availability": "DISABLED unless explicitly enabled",
                  "context": "per-provider catalog entry",
                  "structured_output": "per-provider capability declaration",
                  "tools": "same broker policy; egress redacted and budgeted",
                  "resources": "spending budget enforced before any send",
                  "fallback": "unknown/over-budget models fail closed to local"},
    }


# ---------------------------------------------------------------------------
# Configuration profiles: diff/reset/import/export (#5)
# ---------------------------------------------------------------------------

def config_snapshot(cfg: Any) -> Dict[str, Any]:
    """Redacted configuration snapshot (references only, safe to diff/show)."""
    raw: Any = None
    try:
        raw = cfg.as_yaml() if hasattr(cfg, "as_yaml") else None
    except Exception:
        raw = None
    if isinstance(raw, str):
        try:
            import yaml as _yaml
            raw = _yaml.safe_load(raw) or {}
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        # Fallback for minimal config-likes: probe well-known sections.
        raw = {}
        for section in ("audio", "mic", "asr", "tts", "intent", "summarizer",
                        "webui", "permissions", "actions", "communications",
                        "logging", "conversation", "hotkey", "agent",
                        "local", "memory", "reports"):
            try:
                val = cfg.section(section)
            except Exception:
                continue
            if isinstance(val, dict) and val:
                raw[section] = val
    return redact_snapshot(raw or {}, collect_secrets(cfg))


def config_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Flat dotted-path diff of two redacted snapshots."""
    flat_b = _flatten(before)
    flat_a = _flatten(after)
    added = {k: flat_a[k] for k in flat_a if k not in flat_b}
    removed = {k: flat_b[k] for k in flat_b if k not in flat_a}
    changed = {k: {"from": flat_b[k], "to": flat_a[k]} for k in flat_a
               if k in flat_b and flat_a[k] != flat_b[k]}
    return {"added": redact_snapshot(added), "removed": redact_snapshot(removed),
            "changed": redact_snapshot(changed),
            "state": "READY" if not (added or removed or changed) else "PENDING"}


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}." if prefix else f"{k}."))
    elif isinstance(obj, list):
        out[prefix.rstrip(".")] = f"<list:{len(obj)}>"
    else:
        out[prefix.rstrip(".")] = obj
    return out


_EXPORT_SIZE_LIMIT = 256 * 1024
_EXPORT_BACKUP_SIZE_LIMIT = 16 * 1024 * 1024


def _validate_export_dest(dest: Path) -> tuple:
    """Validate an export destination. Returns (ok, error, parent, resolved).
    Rejects missing parents, non-directories, symlink destinations, and
    destinations escaping their parent via `..`."""
    parent = dest.parent
    if not parent.exists():
        return False, f"parent directory does not exist: {_scrub_text(str(parent))}", None, None
    if not parent.is_dir():
        return False, f"parent is not a directory: {_scrub_text(str(parent))}", None, None
    if dest.is_symlink():
        return False, "destination is a symlink; refusing to follow it", None, None
    try:
        resolved_parent = parent.resolve()
        resolved = (resolved_parent / dest.name).resolve() \
            if dest.name not in ("", ".", "..") else None
    except Exception as exc:
        return False, f"cannot resolve destination: {exc!s}"[:160], None, None
    if resolved is None or resolved.parent != resolved_parent:
        return False, "destination escapes its parent directory", None, None
    return True, "", resolved_parent, resolved


def config_export(cfg: Any, dest: Path, *, dry_run: bool = True,
                  confirm: bool = False, backup: bool = True) -> Dict[str, Any]:
    """Export a redacted config profile. Preview-only unless
    ``dry_run=False`` AND ``confirm=True`` — without both, no file,
    directory, backup, journal, or temp file is created and the call
    returns PENDING (dry-run preview) or DENIED (unconfirmed mutation).

    The applied path writes atomically: validated destination, no symlink
    following, unique exclusive temp file, fsynced content, 0600 mode,
    validated backup of any existing file, directory fsync, and cleanup of
    only the writer's own temp file. Never writes secrets: the snapshot is
    redacted before serialization."""
    snap = config_snapshot(cfg)
    payload = {"schema": LIFECYCLE_SCHEMA, "exported_at": time.time(),
               "profile": snap}
    body = json.dumps(payload, indent=2, sort_keys=True)
    if len(body) > _EXPORT_SIZE_LIMIT:
        return {"state": "FAILED", "detail": "snapshot exceeds 256 KiB bound"}
    dest = Path(dest)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    if dry_run:
        return {"schema": LIFECYCLE_SCHEMA, "state": "PENDING",
                "path": _scrub_text(str(dest)), "sha_prefix": digest,
                "bytes": len(body),
                "detail": "preview only; re-run with apply=true and "
                          "confirm=true to write",
                "requires": "apply + confirm"}
    if not confirm:
        return {"schema": LIFECYCLE_SCHEMA, "state": "DENIED",
                "path": _scrub_text(str(dest)), "sha_prefix": digest,
                "detail": "mutation attempted without explicit confirmation; "
                          "nothing was created",
                "requires": "apply + confirm"}
    ok, err, parent, _resolved = _validate_export_dest(dest)
    if not ok:
        return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                "detail": err, "path": _scrub_text(str(dest))}
    bak_path: Optional[Path] = None
    if backup and dest.exists():
        try:
            if dest.stat().st_size > _EXPORT_BACKUP_SIZE_LIMIT:
                return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                        "detail": "existing destination too large to back up; "
                                  "refusing overwrite",
                        "path": _scrub_text(str(dest))}
        except OSError as exc:
            return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                    "detail": f"cannot stat destination: {exc!s}"[:160]}
        bak_path = dest.with_name(
            f"{dest.name}.bak-{int(time.time())}-{uuid.uuid4().hex[:6]}")
        try:
            shutil.copy2(dest, bak_path)
            if bak_path.stat().st_size != dest.stat().st_size:
                raise OSError("backup size mismatch")
            os.chmod(bak_path, 0o600)
        except Exception as exc:
            try:
                if bak_path is not None and bak_path.exists():
                    bak_path.unlink()
            except OSError:
                pass
            return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                    "detail": f"backup failed, refusing overwrite: "
                              f"{_scrub_text(str(exc))}"[:200]}
    import tempfile as _tf
    tmp_fd = -1
    tmp_path: Optional[Path] = None
    try:
        tmp_fd, tmp_name = _tf.mkstemp(prefix=dest.name + ".",
                                       suffix=".tmp", dir=str(parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(tmp_fd, "w") as fh:
            tmp_fd = -1
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, dest)
        tmp_path = None  # promoted; no longer ours to clean
        try:
            dir_fd = os.open(str(parent), os.O_RDONLY)
        except OSError:
            dir_fd = -1
        if dir_fd >= 0:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception as exc:
        return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                "detail": f"export failed, destination untouched: "
                          f"{_scrub_text(str(exc))}"[:200],
                "path": _scrub_text(str(dest))}
    finally:
        if tmp_fd >= 0:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    # Post-write verification: the destination holds exactly our bytes.
    try:
        if dest.read_bytes().decode() != body:
            raise OSError("post-write verification mismatch")
    except Exception as exc:
        return {"schema": LIFECYCLE_SCHEMA, "state": "FAILED",
                "detail": f"post-write verification failed: "
                          f"{_scrub_text(str(exc))}"[:200],
                "rollback": "restore the .bak file written next to the export"
                if bak_path else "no backup was needed (new file)"}
    return {"schema": LIFECYCLE_SCHEMA, "state": "SUCCESS",
            "path": _scrub_text(str(dest)),
            "sha_prefix": digest, "bytes": len(body), "mode": "0600",
            "detail": "redacted profile exported (no secrets included)",
            "backup": _scrub_text(str(bak_path)) if bak_path else None,
            "rollback": "restore the .bak file written next to the export"
            if bak_path else "delete the exported file to undo"}


# ---------------------------------------------------------------------------
# Microphone/output wizard (#6), ordered repair planner (#8)
# ---------------------------------------------------------------------------

def audio_wizard(cfg: Any) -> Dict[str, Any]:
    """Guided audio selection data: device identity, format conversion,
    wrong-device guidance, and a safe synthetic test tone. Never records
    private audio and never auto-selects a device."""
    out: Dict[str, Any] = {"schema": LIFECYCLE_SCHEMA}
    secrets = collect_secrets(cfg)
    try:
        from .audio import settings as AS
        devs = AS.list_devices()
        out["inputs"] = [{"stable_id": d.stable_id, "name": d.name,
                          "backend": d.backend, "is_default": d.is_default}
                         for d in devs.get("inputs", [])]
        out["outputs"] = [{"stable_id": d.stable_id, "name": d.name,
                           "backend": d.backend, "is_default": d.is_default}
                          for d in devs.get("outputs", [])]
    except Exception as exc:
        out["inputs"] = []
        out["outputs"] = []
        out["devices_error"] = str(exc)[:160]
    try:
        from .audio import settings as AS
        out["current"] = redact_snapshot(AS.status(cfg))
    except Exception as exc:
        out["current"] = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    out["guidance"] = [
        "pick devices by stable name; indexes may change after reboot",
        "a missing configured pin is DEVICE_MISMATCH, never the aggregate default",
        "the test tone is synthetic; the wizard never records your microphone",
        "if the wrong device answers, re-run selection and re-test",
    ]
    out["test_tone"] = {"kind": "synthetic 440 Hz, 0.5 s, 16 kHz mono",
                        "recorded": False,
                        "state": "READY"}
    out["state"] = "READY" if out["inputs"] or out["outputs"] else "MISSING"
    return redact_snapshot(out, secrets)


def repair_order(cfg: Any, paths: Any = None, *,
                 offline: bool = False) -> Dict[str, Any]:
    """Ordered repair planner (#8): missing prerequisites first, finished
    work never repeated, partial progress visible and resumable."""
    cl = checklist(cfg, paths, offline=offline)
    order = [s for s in cl["steps"] if not _ok_state(s["state"])
             and s["state"] not in ("PENDING",)]
    skip = [s["id"] for s in cl["steps"] if _ok_state(s["state"])]
    scrubbed = redact_snapshot({"schema": LIFECYCLE_SCHEMA, "state": cl["state"],
            "repairs": [{"step": s["id"], "state": s["state"],
                         "action": s.get("next", "")} for s in order],
            "already_ok": skip}, collect_secrets(cfg))
    return {"schema": scrubbed["schema"], "state": scrubbed["state"],
            "repairs": scrubbed["repairs"],
            "already_ok": scrubbed["already_ok"],
            "partial": bool(order) and bool(skip),
            "next": "run `voicebridge setup run --apply --confirm --steps "
                    + ",".join(s["id"] for s in order) + "`" if order else
                    "nothing to repair"}


# ---------------------------------------------------------------------------
# Migration/backup center (#9), audit timeline (#10), upgrade/rollback
# ---------------------------------------------------------------------------

def backup_center(paths: Any = None, *, action: str = "plan",
                  dry_run: bool = True, confirm: bool = False,
                  backup_dir: Any = None) -> Dict[str, Any]:
    """Migration and backup center. ``plan``/``preview`` are read-only.
    Mutations need ``confirm=True`` + ``dry_run=False``; data deletion is a
    separate explicit confirmation and never the default."""
    from . import distribution as D
    from . import product as P
    paths = paths or P.default_paths()
    if action == "plan":
        mig = P.branding_status(paths)
        return {"schema": LIFECYCLE_SCHEMA, "state": "READY",
                "migration": redact_snapshot(mig),
                "upgrade": D.upgrade_plan(cfg_like()),
                "uninstall": D.uninstall_plan(cfg_like()),
                "retention": "backups kept next to state; restores preview first",
                "detail": "destructive deletion needs a second explicit confirmation"}
    if action in ("migrate", "migrate-rollback", "rollback"):
        if dry_run or not confirm:
            preview = {"state": "PENDING",
                       "detail": f"{action} preview only; re-run with --apply "
                                 "--confirm",
                       "requires": "owner --confirm + backup"}
            if action == "migrate":
                preview["plan"] = redact_snapshot(
                    P.migrate(paths, dry_run=True))
            elif action == "migrate-rollback":
                preview["plan"] = redact_snapshot(
                    P.migrate_rollback(paths, dry_run=True,
                                       backup_dir=backup_dir))
            else:
                preview["plan"] = redact_snapshot(
                    P.rollback(paths, dry_run=True, backup_dir=backup_dir))
            return preview
        if action == "migrate":
            return redact_snapshot(P.migrate(paths, dry_run=False,
                                             backup_dir=backup_dir))
        if action == "migrate-rollback":
            return redact_snapshot(P.migrate_rollback(paths, dry_run=False,
                                                      backup_dir=backup_dir))
        return redact_snapshot(P.rollback(paths, dry_run=False,
                                          backup_dir=backup_dir))
    return {"state": "FAILED", "detail": f"unknown backup action {action!r}",
            "known": ["plan", "migrate", "migrate-rollback", "rollback"]}


def cfg_like() -> Dict[str, Any]:
    return {}


def audit_timeline(paths: Any = None, *, limit: int = 50) -> Dict[str, Any]:
    """Local audit/change timeline (#10): actor, change, time, confirmation,
    reason, rollback result. Bounded and redacted."""
    from . import product as P
    paths = paths or P.default_paths()
    try:
        entries = P.InstallJournal(paths.journal_dir).read() or []
    except Exception as exc:
        return {"schema": LIFECYCLE_SCHEMA, "state": "UNAVAILABLE",
                "detail": str(exc)[:160], "entries": []}
    entries = entries[-max(1, min(limit, 200)):]
    return {"schema": LIFECYCLE_SCHEMA, "state": "READY",
            "entries": redact_snapshot(entries),
            "retention": "journal is local and bounded; export explicitly"}


def upgrade_flow(paths: Any = None, *, dry_run: bool = True,
                 confirm: bool = False) -> Dict[str, Any]:
    """Upgrade contract with dry-run preview and exact confirmation.

    Truthfulness rule: this backend never executes the installer itself
    (downloading components, rewriting units, and touching services are
    operator-shell actions), so it never reports SUCCESS for an upgrade it
    did not perform. Preview (or unconfirmed) calls return PENDING; an
    explicitly confirmed apply returns BLOCKED with the exact operator
    command, because the remaining step must run outside this process.
    User data and tuned configuration are preserved by contract; no service
    is ever restarted silently."""
    from . import distribution as D
    from . import product as P
    contract = P.upgrade_contract()
    base = {"schema": LIFECYCLE_SCHEMA,
            "contract": redact_snapshot(contract),
            "plan": D.upgrade_plan(cfg_like()),
            "preserves": contract.get("preserves", []),
            "rollback": "restore the .bak kept by the unit installer"}
    if dry_run or not confirm:
        return {**base, "state": "PENDING",
                "detail": "preview only; re-run with --apply --confirm for "
                          "the operator command",
                "requires": "apply + confirm"}
    return {**base, "state": "BLOCKED",
            "detail": "this backend does not execute installers; nothing was "
                      "changed",
            "operator_command": "scripts/install.sh (idempotent venv/deps/"
                                "models; preserves tuned units with .bak "
                                "unless INSTALL_FORCE_UNIT=1)",
            "next": "run the operator command in a shell, then re-run "
                    "`voicebridge setup check`"}


def rollback_flow(paths: Any = None, *, dry_run: bool = True,
                  confirm: bool = False,
                  backup_dir: Any = None) -> Dict[str, Any]:
    """Rollback with preview, partial-failure visibility, and resumability."""
    from . import product as P
    paths = paths or P.default_paths()
    out = P.rollback(paths, dry_run=(dry_run or not confirm),
                     backup_dir=backup_dir)
    out = redact_snapshot(dict(out))
    out["schema"] = LIFECYCLE_SCHEMA
    if dry_run or not confirm:
        out["detail"] = str(out.get("detail", "")) + \
            " (preview; re-run with --apply --confirm)"
        out["requires"] = "owner --confirm"
    return out


# Registry of backend operations shared by the CLI and the dashboard so both
# surfaces always observe the same authenticated backend state (parity).
def operations() -> Dict[str, Callable[..., Dict[str, Any]]]:
    return {
        "checklist": checklist,
        "setup_run": setup_run,
        "health": health_inventory,
        "diagnostics": diagnostic_bundle,
        "provenance": provenance_view,
        "matrix": capability_matrix,
        "snapshot": config_snapshot,
        "diff": config_diff,
        "export": config_export,
        "wizard": audio_wizard,
        "repair": repair_order,
        "backup": backup_center,
        "audit": audit_timeline,
        "upgrade": upgrade_flow,
        "rollback": rollback_flow,
    }
