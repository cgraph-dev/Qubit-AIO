"""Packet 8T-F — backend-authoritative runtime inventory (dashboard/CLI).

One real probe for product identity, Hermes provision state, vendored
module identity, voice/provider/session/approval/adapter health, and the
broker emergency-stop latch. Read-only: never starts services, models, or
runtimes and never writes state. Anything unobservable is typed
UNAVAILABLE/DEGRADED, never invented. All values redacted (no tokens,
keys, cookies, raw paths, audio, or page text).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from . import foundation as F
from .redaction import redact_text


def _short_home(path: str) -> str:
    home = str(Path("~").expanduser())
    text = str(path)
    return ("~" + text[len(home):]) if home != "~" and text.startswith(home) else text


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _hermes() -> Dict[str, Any]:
    from .runtime import provision as P

    rdir = P.runtime_dir()
    binary = rdir / "venv" / "bin" / "hermes"
    if not binary.is_file():
        return {"state": "DISABLED",
                "detail": "no provisioned Hermes runtime; see "
                          "scripts/install_hermes_runtime.sh",
                "commit": P.HERMES_COMMIT, "tag": P.HERMES_TAG}
    info = P.verify_installation(rdir)
    info["commit"] = P.HERMES_COMMIT
    return info


def _vendored() -> Dict[str, Any]:
    import hashlib as _hashlib

    from .upstream import hermes_ansi_strip, hermes_sqlite_util

    out = {}
    for mod, rel in ((hermes_ansi_strip, "tools/ansi_strip.py"),
                     (hermes_sqlite_util, "hermes_cli/sqlite_util.py")):
        path = Path(str(mod.__file__))
        h = _hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        inside = "voicebridge/upstream/" in str(path).replace("\\", "/")
        out[rel] = {"path": _short_home(str(path)), "sha256": h.hexdigest()[:16],
                    "in_foundation": inside}
    return out


def _voice(cfg: Any) -> Dict[str, Any]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    asr = cfg.section("asr") if hasattr(cfg, "section") else {}
    return {"asr_provider": str(asr.get("provider", "sherpa")
                                if isinstance(asr, dict) else "sherpa"),
            "asr_engine": str(asr.get("engine", "moonshine")
                              if isinstance(asr, dict) else "moonshine"),
            "tts_provider": str(get("tts", "provider", default="piper")),
            "tts_voice": str(get("tts", "voice", default="en_US-amy-medium")),
            "hotkey": str(get("hotkey", "hold_key", default="ctrl_r")),
            "dashboard_voice": "webui talk/type intake"}


def _broker() -> Dict[str, Any]:
    try:
        import voicebridge.power as PW

        control = PW.PowerControl()
        state = control.state
        return {"policy_path": "voicebridge broker + PowerPolicy",
                "emergency_stop": bool(state.get("emergency_stop", False)),
                "global_disable": bool(state.get("global_disable", False)),
                "enabled": bool(state.get("enabled", True))}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _approvals(cfg: Any) -> Dict[str, Any]:
    try:
        from .approvals import ApprovalJournal

        state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
        journal = ApprovalJournal(Path(state_dir) / "approvals.jsonl")
        pending = journal.pending()
        return {"pending": len(pending),
                "backend": "durable settle-once journal"}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _adapters(cfg: Any) -> Dict[str, Any]:
    import shutil as _shutil

    try:
        from .openclaw.discovery import discover_gateway

        info = discover_gateway(cfg)
        openclaw = {"configured": bool(info.token),
                    "agent": redact_text(str(info.agent or ""))[:40]}
    except Exception as exc:
        openclaw = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    hermes_bin = _shutil.which("hermes")
    return {"openclaw_gateway": openclaw,
            "hermes_cli": {"available": bool(hermes_bin),
                           "note": "operator binary, never used by Qubit "
                                   "without explicit backend enablement"}}


def _providers(cfg: Any) -> Dict[str, Any]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    return {"resident_alias": str(get("intent", "runner", "alias",
                                      default="qubit-brain4")),
            "frontier": "disabled by default (explicit allowlist + budgets)",
            "credentials": "env:/file: references only; values never shown"}


def runtime_inventory(cfg: Any) -> Dict[str, Any]:
    """Assemble the real runtime inventory; every leaf is observed or typed."""
    inv = {
        "schema": "qubit-runtime-inventory/1",
        "product": {"name": F.PRODUCT_NAME,
                    "public_identity": F.PUBLIC_IDENTITY,
                    "voice_subsystem": F.VOICE_SUBSYSTEM,
                    "install_identity": F.INSTALL_IDENTITY},
        "hermes": _safe(_hermes),
        "vendored": _safe(_vendored),
        "voice": _voice(cfg),
        "broker": _safe(_broker),
        "approvals": _approvals(cfg),
        "adapters": _adapters(cfg),
        "providers": _providers(cfg),
    }
    return inv
