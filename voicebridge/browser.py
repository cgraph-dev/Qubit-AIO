"""Qubit browser surface via the scoped loopback probe.

Qubit never touches personal browser profiles: the only browser authority
is the read-only loopback CDP path. This module provides the
Qubit-native surface:

* ``status`` — renders the 8B CDP precondition probe as a typed state
  (``OK`` / ``BROWSER_RESTART_REQUIRED`` / ``UNAVAILABLE``). The probe
  itself is acquired by the caller (``voicebridge/cli.py``, which owns the
  Packet 8A import boundary); this module never imports the
  communications package, spawns nothing, and mutates no session;
* ``close-profile`` — typed ``BROWSER_CLOSE_REFUSED`` (closing the user's
  real profile would mutate their session outside Qubit authority).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

CLOSE_CODE = "BROWSER_CLOSE_REFUSED"
CONFIRM_CODE = "BROWSER_CONFIRM_REQUIRED"
ESTOP_CODE = "BROWSER_ESTOP"
PROFILE_BAD_INPUT_CODE = "BROWSER_PROFILE_BAD_INPUT"
PROFILE_EXISTS_CODE = "BROWSER_PROFILE_EXISTS"
PROFILE_NOT_FOUND_CODE = "BROWSER_PROFILE_NOT_FOUND"
PROFILE_NO_LAUNCHER_CODE = "BROWSER_PROFILE_NO_LAUNCHER"
PROFILE_DIR = "browser-profiles"
REGISTRY_NAME = "browser-profiles.json"
MIN_PROFILE_PORT = 9300
MAX_PROFILE_PORT = 9499
_PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# Names that would shadow/select the user's real browser profile: these are
# never Qubit-managed and are always refused with BROWSER_CLOSE_REFUSED.
_RESERVED_PROFILE_NAMES = frozenset({
    "default", "user", "personal", "profile", "profiles",
    "chromium", "chrome", "firefox", "guest",
})


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def status(probe: Mapping[str, Any]) -> Dict[str, Any]:
    """Render a CDP precondition probe as typed browser state (pure)."""
    probe = dict(probe or {})
    state = str(probe.get("state", "UNAVAILABLE"))
    if state not in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE"):
        state = "UNAVAILABLE"
    return {"state": state,
            "schema": "qubit-browser/1",
            "cdp": bool(probe.get("cdp", False)),
            "detail": _redact(str(probe.get("detail", "") or ""))[:300],
            "authority": "loopback CDP read-only (Packet 8B); personal "
                         "profiles are never touched"}


def refuse_close_profile(profile: str = "") -> Dict[str, Any]:
    """Typed refusal for real-profile session mutation."""
    return {"state": "REFUSED", "code": CLOSE_CODE,
            "profile": _redact((profile or "default")[:80]),
            "explanation": "Qubit Agent cannot do this: closing a real "
                           "browser profile would mutate your session "
                           "outside Qubit authority; the read-only loopback "
                           "CDP path is the only browser authority.",
            "owner": "qubit-policy",
            "rollback": "no session touched; nothing to roll back"}


# --------------------------------------------------------- Qubit profile store
# Qubit owns a small set of DEDICATED browser profile directories (never the
# user's real Chromium profile). Only these Qubit-managed profiles may be
# created/launched/closed by Qubit; a request naming anything else is refused
# with the unchanged ``BROWSER_CLOSE_REFUSED`` contract. The lifecycle is a
# real, audited state machine persisted under the state dir with an injected
# launcher/stopper seam (no live Chromium is ever started by this module).


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True  # fail closed


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"browser_profile.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _denied(code: str, detail: str, *, state: str = "denied",
            extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"state": state, "code": code, "detail": _redact(detail)[:300],
           "owner": "qubit-policy",
           "rollback": "no browser profile or session was touched; nothing "
                       "to roll back"}
    if extra:
        out.update(extra)
    return out


def _profiles_root(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / PROFILE_DIR


def _registry_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / REGISTRY_NAME


def _load_registry(cfg: Any) -> Dict[str, Any]:
    try:
        raw = _registry_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(cfg: Any, data: Dict[str, Any]) -> None:
    path = _registry_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _short(path: str) -> str:
    home = str(Path("~").expanduser())
    text = str(path or "")
    if home != "~" and text.startswith(home):
        return "~" + text[len(home):]
    return text


def _all_profiles(cfg: Any) -> Dict[str, Dict[str, Any]]:
    data = _load_registry(cfg)
    profiles = data.get("profiles")
    return dict(profiles) if isinstance(profiles, dict) else {}


def _used_ports(profiles: Mapping[str, Any]) -> set:
    ports = set()
    for entry in profiles.values():
        if isinstance(entry, dict):
            try:
                ports.add(int(entry.get("cdp_port")))
            except (TypeError, ValueError):
                continue
    return ports


def _allocate_port(profiles: Mapping[str, Any]) -> int:
    used = _used_ports(profiles)
    for port in range(MIN_PROFILE_PORT, MAX_PROFILE_PORT + 1):
        if port not in used:
            return port
    raise ValueError("no free Qubit browser-profile port in range")


def _check_name(name: str) -> str:
    text = str(name or "").strip().lower()
    if not text or len(text) > 32 or not _PROFILE_NAME_RE.match(text):
        raise ValueError("profile name must match ^[a-z][a-z0-9-]{0,31}$")
    if text in _RESERVED_PROFILE_NAMES:
        raise ValueError(
            f"{text!r} names a user/real browser profile; Qubit only manages "
            "its own dedicated profiles")
    return text


def _public_profile(cfg: Any, name: str,
                    entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {"name": name, "managed": True,
            "state": str(entry.get("state", "stopped")),
            "cdp_port": entry.get("cdp_port"),
            "dir": _short(str(entry.get("dir", ""))),
            "pid": entry.get("pid"),
            "created_at": entry.get("created_at"),
            "running": str(entry.get("state", "stopped")) == "running"}


def profiles(cfg: Any) -> Dict[str, Any]:
    """List Qubit-managed browser profiles (read-only).

    Personal/user browser profiles are never enumerated or inspected: only
    Qubit-created dedicated profile directories are reported.
    """
    managed = _all_profiles(cfg)
    rows = [_public_profile(cfg, name, managed[name])
            for name in sorted(managed)]
    return {"state": "OK", "schema": "qubit-browser-profiles/1",
            "profiles": rows,
            "managed_root": _short(str(_profiles_root(cfg))),
            "authority": "Qubit-managed dedicated profiles only; personal "
                         "profiles are never listed or inspected"}


def create_profile(cfg: Any, name: str, *, cdp_port: Optional[int] = None,
                   requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Create a Qubit-dedicated browser profile directory + registry record.

    Confirmation required; the directory is confined to the Qubit state dir
    and never aliases a user profile. Creating the record does NOT launch a
    browser (see :func:`open_profile` for the launcher seam).
    """
    if _estop(cfg):
        return _denied(ESTOP_CODE, "emergency stop latched; profile creation "
                                   "refused", state="ESTOP")
    if not confirmed:
        return _denied(CONFIRM_CODE, "creating a browser profile requires "
                                     "explicit --confirm")
    try:
        clean = _check_name(name)
    except ValueError as exc:
        return _denied(PROFILE_BAD_INPUT_CODE, str(exc))
    managed = _all_profiles(cfg)
    if clean in managed:
        return _denied(PROFILE_EXISTS_CODE,
                       f"Qubit profile {clean!r} already exists",
                       state="EXISTS",
                       extra={"profile": _public_profile(
                           cfg, clean, managed[clean])})
    if cdp_port is None:
        try:
            port = _allocate_port(managed)
        except ValueError as exc:
            return _denied(PROFILE_BAD_INPUT_CODE, str(exc))
    else:
        try:
            port = int(cdp_port)
        except (TypeError, ValueError):
            return _denied(PROFILE_BAD_INPUT_CODE, "cdp_port must be an int")
        if not (MIN_PROFILE_PORT <= port <= MAX_PROFILE_PORT):
            return _denied(
                PROFILE_BAD_INPUT_CODE,
                f"cdp_port must be loopback-range {MIN_PROFILE_PORT}-"
                f"{MAX_PROFILE_PORT} (never a public/user listener)")
        if port in _used_ports(managed):
            return _denied(PROFILE_BAD_INPUT_CODE,
                           f"cdp_port {port} is already in use")
    profile_dir = _profiles_root(cfg) / clean
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(profile_dir, 0o700)
    except OSError as exc:
        return _denied(PROFILE_BAD_INPUT_CODE,
                       f"profile directory creation failed ({exc})")
    entry = {"name": clean, "dir": str(profile_dir), "cdp_port": port,
             "state": "stopped", "pid": None, "created_at": time.time()}
    managed[clean] = entry
    try:
        _save_registry(cfg, {"schema": "qubit-browser-profiles/1",
                             "profiles": managed})
    except OSError as exc:
        return _denied(PROFILE_BAD_INPUT_CODE,
                       f"profile registry write failed ({exc})")
    _audit("create", requested_by, {"profile": clean, "cdp_port": port})
    return {"state": "OK", "schema": "qubit-browser-profiles/1",
            "profile": _public_profile(cfg, clean, entry),
            "authority": "Qubit-managed dedicated profile (never a user "
                         "profile)"}


def open_profile(cfg: Any, profile: str, *,
                 launcher: Optional[Callable[[str, int], Any]] = None,
                 requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Launch a Qubit-managed profile through an injected approved launcher.

    Without an injected launcher the call is inert and returns the typed
    ``BROWSER_PROFILE_NO_LAUNCHER`` handoff (this module never spawns
    Chromium on its own). The launcher is the supervised seam used by the
    approved ``qubit monitor launch`` path and by hermetic tests.
    """
    if _estop(cfg):
        return _denied(ESTOP_CODE, "emergency stop latched; profile launch "
                                   "refused", state="ESTOP")
    if not confirmed:
        return _denied(CONFIRM_CODE, "launching a browser profile requires "
                                     "explicit --confirm")
    name = str(profile or "").strip().lower()
    managed = _all_profiles(cfg)
    if name not in managed:
        return _denied(PROFILE_NOT_FOUND_CODE,
                       f"no Qubit-managed profile {name!r}",
                       state="NOT_FOUND")
    entry = managed[name]
    if launcher is None:
        return {"state": "UNAVAILABLE", "code": PROFILE_NO_LAUNCHER_CODE,
                "detail": "no approved browser launcher is configured; Qubit "
                          "never spawns Chromium itself",
                "handoff": "qubit monitor launch (approved launch only)",
                "owner": "qubit-policy",
                "rollback": "no process started; nothing to roll back"}
    try:
        result = launcher(str(entry["dir"]), int(entry["cdp_port"]))
    except Exception as exc:
        return _denied(PROFILE_BAD_INPUT_CODE,
                       f"approved launcher failed ({exc})", state="FAILED")
    pid = None
    if isinstance(result, dict):
        pid = result.get("pid")
    elif isinstance(result, int):
        pid = result
    entry = dict(entry)
    entry["state"] = "running"
    entry["pid"] = pid
    managed[name] = entry
    _save_registry(cfg, {"schema": "qubit-browser-profiles/1",
                         "profiles": managed})
    _audit("open", requested_by, {"profile": name, "pid": pid})
    return {"state": "OK", "schema": "qubit-browser-profiles/1",
            "profile": _public_profile(cfg, name, entry)}


def close_profile(cfg: Any, profile: str = "", *,
                  stopper: Optional[Callable[[Dict[str, Any]], Any]] = None,
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Close a Qubit-managed profile lifecycle (or refuse a real profile).

    The safety contract is unchanged for user profiles: any name that is not
    a Qubit-managed profile returns the typed ``BROWSER_CLOSE_REFUSED``.
    A Qubit-managed profile transitions to ``stopped`` (optional injected
    stopper is invoked first); nothing outside the Qubit-owned directory and
    registry is touched.
    """
    name = str(profile or "").strip().lower()
    managed = _all_profiles(cfg)
    if name not in managed:
        return refuse_close_profile(profile)
    if _estop(cfg):
        return _denied(ESTOP_CODE, "emergency stop latched; profile close "
                                   "refused", state="ESTOP")
    if not confirmed:
        return _denied(CONFIRM_CODE, "closing a profile requires explicit "
                                     "--confirm")
    entry = dict(managed[name])
    if stopper is not None and entry.get("state") == "running":
        try:
            stopper(dict(entry))
        except Exception as exc:
            return _denied(PROFILE_BAD_INPUT_CODE,
                           f"approved stopper failed ({exc})", state="FAILED")
    entry["state"] = "stopped"
    entry["pid"] = None
    managed[name] = entry
    try:
        _save_registry(cfg, {"schema": "qubit-browser-profiles/1",
                             "profiles": managed})
    except OSError as exc:
        return _denied(PROFILE_BAD_INPUT_CODE,
                       f"profile registry write failed ({exc})")
    _audit("close", requested_by, {"profile": name})
    return {"state": "OK", "schema": "qubit-browser-profiles/1",
            "profile": _public_profile(cfg, name, entry),
            "closed": True}


def remove_profile(cfg: Any, profile: str, *,
                   requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Stop and delete a Qubit-managed profile (confined, confirmed)."""
    name = str(profile or "").strip().lower()
    managed = _all_profiles(cfg)
    if name not in managed:
        return _denied(PROFILE_NOT_FOUND_CODE,
                       f"no Qubit-managed profile {name!r}", state="NOT_FOUND")
    if _estop(cfg):
        return _denied(ESTOP_CODE, "emergency stop latched; profile removal "
                                   "refused", state="ESTOP")
    if not confirmed:
        return _denied(CONFIRM_CODE, "removing a profile requires explicit "
                                     "--confirm")
    entry = dict(managed.pop(name))
    profile_dir = _profiles_root(cfg) / name
    removed = False
    try:
        if profile_dir.is_dir():
            shutil.rmtree(profile_dir)
            removed = True
        _save_registry(cfg, {"schema": "qubit-browser-profiles/1",
                             "profiles": managed})
    except OSError as exc:
        return _denied(PROFILE_BAD_INPUT_CODE,
                       f"profile removal failed ({exc})", state="FAILED")
    _audit("remove", requested_by, {"profile": name, "dir_removed": removed})
    return {"state": "OK", "schema": "qubit-browser-profiles/1",
            "profile": name, "removed": True, "dir_removed": removed,
            "rollback": "Qubit-managed profile directory deleted; a user "
                        "profile was never touched"}
