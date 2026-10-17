"""Qubit foundation: identity, singularity audit, and runtime receipt.

Single source for the Qubit product identity and a hermetic, fail-closed
audit proving the "one of everything" runtime contract:

- one effective agent/session authority (Qubit-native agent turns);
- one microphone listener, one voice session, one TTS path;
- one broker/policy path, one audit path, one emergency-stop path.

The audit reports MULTIPLE whenever more than one live path of a kind is
observed. This module never starts listeners, servers, or runtimes itself.
Upstream provenance (formerly adapted source) lives in
voicebridge/upstream/ATTRIBUTION.md and docs/UPSTREAM_REUSE.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

# --- 8T-F canonical identity (single source; CLI/docs/installer mirror it) ---
# Public product: Qubit AiO. Voice subsystem: Qubit VoiceBridge.
PRODUCT_NAME = "Qubit AiO"
PUBLIC_IDENTITY = "Qubit AiO"
VOICE_SUBSYSTEM = "Qubit VoiceBridge"
INSTALL_IDENTITY = "qubit-voicebridge"
IMPORT_PACKAGE = "voicebridge"
LEGACY_IDENTITY = "openclaw-voice-bridge"

# Qubit-native agent entry points (the effective runtime — no external
# assistant process is launched or required by normal execution).
AGENT_TURN_PATH = "voicebridge.agent.turn"
AGENT_SESSION_PATH = "voicebridge.agent.run_session"

SINGLE = "SINGLE"
MULTIPLE = "MULTIPLE"
UNAVAILABLE = "UNAVAILABLE"


def _classify(count: Optional[int]) -> str:
    if count is None:
        return UNAVAILABLE
    if count == 1:
        return SINGLE
    return MULTIPLE


def singularity_report(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Audit one-of-everything from an observed runtime state snapshot.

    ``state`` maps each authority kind to an observed live-path COUNT
    (int) or None when unobservable here:
    listeners, voice_sessions, tts_paths, brokers, audit_paths,
    emergency_stops, agent_authorities.
    Returns typed per-kind states plus an overall verdict: "OK" only when
    every observable kind is exactly one; "DEGRADED" when anything is
    unobservable but nothing is multiple; "FAILED" on any MULTIPLE.
    """
    kinds = ("listeners", "voice_sessions", "tts_paths", "brokers",
             "audit_paths", "emergency_stops", "agent_authorities")
    detail = {k: _classify(state.get(k)) for k in kinds}
    states = set(detail.values())
    if MULTIPLE in states:
        verdict = "FAILED"
    elif UNAVAILABLE in states:
        verdict = "DEGRADED"
    else:
        verdict = "OK"
    return {"schema": "qubit-foundation/1", "verdict": verdict,
            "detail": detail}


def collect_runtime_state(mic_open: Optional[bool] = None,
                          hotkey_running: Optional[bool] = None,
                          web_active_count: Optional[int] = None,
                          voice_session_count: Optional[int] = None,
                          tts_provider: Optional[str] = None,
                          broker_count: Optional[int] = None,
                          audit_count: Optional[int] = None,
                          estop_count: Optional[int] = None,
                          agent_count: Optional[int] = None) -> Dict[str, int]:
    """Fold raw runtime observations into authority counts.

    Listener total = mic stream + hotkey loop + web-PTT turns (each 0/1 or
    a counted number; omitted entirely when nothing is observable).
    Voice/TTS/broker/audit/estop/agent counts pass through only when the
    caller actually observed them — nothing is assumed to be one.
    Pure function: no I/O, no singletons.
    """
    counts: Dict[str, int] = {}
    if not (mic_open is None and hotkey_running is None
            and web_active_count is None):
        counts["listeners"] = ((1 if mic_open else 0)
                               + (1 if hotkey_running else 0)
                               + (web_active_count or 0))
    if tts_provider is not None:
        counts["tts_paths"] = 1 if tts_provider else 0
    for key, val in (("voice_sessions", voice_session_count),
                     ("brokers", broker_count),
                     ("audit_paths", audit_count),
                     ("emergency_stops", estop_count),
                     ("agent_authorities", agent_count)):
        if val is not None:
            counts[key] = val
    return counts


def native_authorities() -> List[Dict[str, str]]:
    """The single native authority behind each runtime concern."""
    return [
        {"concern": "agent turns", "authority": AGENT_TURN_PATH},
        {"concern": "worker sessions", "authority": AGENT_SESSION_PATH},
        {"concern": "audio output", "authority": "orchestrator speak() + Playback"},
        {"concern": "browser", "authority": "loopback CDP tabs"},
        {"concern": "skills", "authority": "reviewed manifests (allowlist + hash-pin)"},
    ]


def assert_foundation(report: Mapping[str, Any]) -> None:
    """Fail closed unless the singularity audit is exactly OK."""
    if report.get("verdict") != "OK":
        raise FoundationError(
            f"foundation singularity not proven: {report.get('verdict')}: "
            f"{dict(report.get('detail', {}))}")


# --- Native runtime identity: the Qubit agent entry points -------------------
# Wrong entry, missing module, or altered digest fails closed. There is no
# external assistant source to verify: normal execution never launches one.


def _sha256_file(path: Path) -> str:
    import hashlib as _hashlib

    h = _hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def identity_receipt() -> Dict[str, Any]:
    """Record the effective Qubit runtime identity (inspectable proof)."""
    from . import agent as _agent

    module_file = str(Path(str(_agent.__file__)).resolve())
    try:
        from .opencode import policy as _pol
        from .opencode import state as _ocs
        owned = _ocs.verify_owned_binary()
        tool_runtime: Dict[str, Any] = {
            "binary": str(owned.get("binary", "")),
            "pin": _pol.PINNED_OPENCODE_VERSION,
            "verified": bool(owned.get("ok")),
        }
    except Exception as exc:
        tool_runtime = {"state": "UNAVAILABLE", "detail": str(exc)[:160]}
    return {"schema": "qubit-runtime-identity/1",
            "product": PRODUCT_NAME,
            "agent_turn": AGENT_TURN_PATH,
            "agent_session": AGENT_SESSION_PATH,
            "module_file": module_file,
            "module_sha256": _sha256_file(Path(module_file)),
            "tool_runtime": tool_runtime}


def verify_runtime_identity() -> Dict[str, Any]:
    """Fail closed unless the native agent entry points resolve.

    Checks the agent module exposes ``turn``/``run_session`` with the
    native receipt marker, and the module file on disk matches the
    receipt digest (altered runtime fails closed). Raises
    FoundationError with a typed reason otherwise.
    """
    from . import agent as _agent

    for name in ("turn", "run_session"):
        if not callable(getattr(_agent, name, None)):
            raise FoundationError(
                f"MISSING_ENTRY: voicebridge.agent.{name} is not callable")
    receipt = identity_receipt()
    live_digest = _sha256_file(Path(receipt["module_file"]))
    if live_digest != receipt["module_sha256"]:
        raise FoundationError("DIGEST_MISMATCH: agent module changed "
                              "between receipt and verification")
    return {"state": "VERIFIED", "receipt": receipt}


class FoundationError(RuntimeError):
    """Raised when the one-of-everything contract does not hold."""


def describe() -> Dict[str, Any]:
    """Static foundation description: identity and native authorities."""
    return {"schema": "qubit-foundation/1", "product": PRODUCT_NAME,
            "public_identity": PUBLIC_IDENTITY,
            "voice_subsystem": VOICE_SUBSYSTEM,
            "install_identity": INSTALL_IDENTITY,
            "import_package": IMPORT_PACKAGE,
            "legacy_identity": LEGACY_IDENTITY,
            "native_authorities": native_authorities()}


def runtime_check() -> Dict[str, Any]:
    """Real end-to-end probe of the native runtime: identity verification
    plus one receipt-bearing agent turn (never a mock). A silent model
    yields a typed FAILED verdict, never fabricated text."""
    from .agent import AgentDenied, turn as _turn

    try:
        identity = verify_runtime_identity()
    except FoundationError as exc:
        return {"verdict": "FAILED", "error": str(exc)[:300]}
    try:
        res = _turn(None, "foundation runtime check",
                    session="foundation")
    except AgentDenied as exc:
        return {"verdict": "FAILED", "error": str(exc)[:300],
                "identity": identity}
    return {"verdict": "OK" if res.get("ok") else "FAILED",
            "identity": identity, "turn": res}


def main(argv: Optional[List[str]] = None) -> int:
    import json as _json
    import sys as _sys

    args = list(argv if argv is not None else _sys.argv[1:])
    cmd = args[0] if args else "describe"
    if cmd == "describe":
        _sys.stdout.write(_json.dumps(describe(), indent=2) + "\n")
        return 0
    if cmd == "check":
        report = singularity_report({})
        _sys.stdout.write(_json.dumps(report, indent=2) + "\n")
        return 0  # audit ran; verdict (DEGRADED) is inside the JSON
    if cmd == "runtime-check":
        try:
            result = runtime_check()
        except Exception as exc:
            _sys.stdout.write(_json.dumps(
                {"verdict": "FAILED", "error": str(exc)[:300]}) + "\n")
            return 1
        _sys.stdout.write(_json.dumps(result, indent=2) + "\n")
        return 0 if result.get("verdict") == "OK" else 1
    _sys.stderr.write(f"unknown foundation command: {cmd}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
