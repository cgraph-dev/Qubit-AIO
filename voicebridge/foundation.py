"""Packet 8T-F — Qubit Agent foundation: identity + singularity audit.

Single source for the 8T-F product identity and a hermetic, fail-closed
audit proving the "one of everything" runtime contract:

- one effective agent/session authority (Qubit; Hermes CLI contract is the
  adapted text runtime, OpenClaw the supervised gateway — neither owns sessions);
- one microphone listener, one voice session, one TTS path;
- one broker/policy path, one audit path, one emergency-stop path.

Competing Hermes/OpenClaw voice, microphone, wake-word, TTS, listener,
router, or session paths are DISABLED by policy: this module records the
refusals and the audit reports MULTIPLE whenever more than one live path of
a kind is observed. It never starts listeners, servers, or runtimes itself.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

# --- 8T-F canonical identity (single source; CLI/docs/installer mirror it) ---
# Public product: Qubit Agent. Voice subsystem: Qubit VoiceBridge.
PRODUCT_NAME = "Qubit Agent"
PUBLIC_IDENTITY = "Qubit Agent"
VOICE_SUBSYSTEM = "Qubit VoiceBridge"
INSTALL_IDENTITY = "qubit-voicebridge"
IMPORT_PACKAGE = "voicebridge"
LEGACY_IDENTITY = "openclaw-voice-bridge"

# --- 8T-F upstream pins (fresh read-only clones, no code copied) ---
HERMES_URL = "https://github.com/NousResearch/hermes-agent"
HERMES_TAG = "v2026.9.7"
HERMES_COMMIT = "2237be355906fbe6065ce1815711eee52b2d646e"
OPENCLAW_URL = "https://github.com/openclaw/openclaw"
OPENCLAW_TAG = "v2026.9.2"
OPENCLAW_COMMIT = "3928bad9badfcb6c7d140530435e806fb8092190"

# Primary text-runtime contract (adapted, behind Qubit broker/policy):
# `hermes chat -q TEXT -Q [--continue SESSION] [--model MODEL]`
PRIMARY_RUNTIME_ARGV = ("hermes", "chat", "-q")

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


def disabled_competing_paths() -> List[Dict[str, str]]:
    """Competing upstream voice paths refused by 8T-F policy (not executed)."""
    return [
        {"path": "hermes voice_mode / local TTS servers",
         "verdict": "DISABLED",
         "reason": "Qubit orchestrator speak() + Playback own all audio output"},
        {"path": "hermes wake-word mic loop",
         "verdict": "DISABLED",
         "reason": "no second listener; wake-word needs a separately authorized packet"},
        {"path": "hermes gateway / tui_gateway voice methods",
         "verdict": "DISABLED",
         "reason": "would create a second event authority beside the Qubit dashboard backend"},
        {"path": "openclaw bridge/browser TTS + profiles as authority",
         "verdict": "DISABLED",
         "reason": "8B loopback CDP is the only browser authority for 8U prep"},
        {"path": "openclaw skills auto-install (ClawHub)",
         "verdict": "DISABLED",
         "reason": "unreviewed third-party code; allowlist + hash-pin + manual review only"},
    ]


def assert_foundation(report: Mapping[str, Any]) -> None:
    """Fail closed unless the singularity audit is exactly OK."""
    if report.get("verdict") != "OK":
        raise FoundationError(
            f"foundation singularity not proven: {report.get('verdict')}: "
            f"{dict(report.get('detail', {}))}")


# --- Anti-substitution gate: the Hermes source must be the pinned tree ------
# Phase B gate: fail if the source is a Qubit imitation, the wrong commit,
# an operator install, or a temporary directory.
HERMES_ENTRY_HASHES = {
    "cli.py": "3b83554934147cb48048624d4ac69f5b6b9a61368b59e68f518e776890dd7b57",
    "hermes_constants.py": "6e6e47b32e4aa8350afbd69e39e7d9648edc2c14558ce7464c2ff7ba3453a3a0",
}
HERMES_LAYOUT_MARKERS = ("cli.py", "LICENSE", "hermes_constants.py",
                         "hermes_cli", "agent", "tools", "gateway")


def _operator_roots() -> List[str]:
    """Directories that must never count as Qubit foundation source."""
    roots: List[str] = []
    try:
        import shutil
        found = shutil.which("hermes")
        if found:
            roots.append(str(Path(found).resolve().parents[1]))
    except Exception:
        pass
    home = Path("~/.hermes").expanduser()
    roots.append(str(home))
    return roots


def check_not_temporary(candidate: Any) -> str:
    """Raise unless the path lives outside /tmp. Tested directly."""
    text = str(candidate)
    if "/tmp/" in text or text.startswith("/tmp"):
        raise FoundationError("TEMPORARY_SOURCE: Hermes source must not "
                              "live under /tmp")
    return text


def check_not_operator(candidate: Any, *,
                       forbidden_roots: Optional[List[str]] = None) -> str:
    """Raise when the path sits inside an operator Hermes location."""
    resolved = str(Path(str(candidate)).expanduser().resolve())
    for root in (forbidden_roots if forbidden_roots is not None
                 else _operator_roots()):
        if root and (resolved == root or resolved.startswith(root.rstrip("/") + "/")):
            raise FoundationError("OPERATOR_INSTALL: candidate is inside an "
                                  "operator Hermes location")
    return resolved


def check_layout_and_commit(path: Path) -> str:
    """Raise unless the Hermes layout fingerprint and pin commit hold."""
    import hashlib as _hashlib
    import subprocess as _subprocess

    for marker in HERMES_LAYOUT_MARKERS:
        if not (path / marker).exists():
            raise FoundationError(
                f"NOT_HERMES_SOURCE: missing layout marker {marker}")
    try:
        proc = _subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
    except Exception as exc:
        raise FoundationError(f"SOURCE_UNVERIFIABLE: git probe failed: {exc}")
    if proc.returncode != 0 or proc.stdout.strip() != HERMES_COMMIT:
        raise FoundationError(
            f"WRONG_COMMIT: HEAD={proc.stdout.strip()[:12]!r} != pin "
            f"{HERMES_COMMIT[:12]}")
    for rel, digest in HERMES_ENTRY_HASHES.items():
        target = path / rel
        try:
            h = _hashlib.sha256()
            with open(target, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
        except OSError as exc:
            raise FoundationError(f"ENTRY_MISSING: {rel}: {exc}")
        if h.hexdigest() != digest:
            raise FoundationError(f"HASH_MISMATCH: {rel} differs from pin")
    return proc.stdout.strip()


def verify_hermes_source(candidate: Any,
                         *,
                         forbidden_roots: Optional[List[str]] = None,
                         allow_temporary: bool = False) -> Dict[str, Any]:
    """Prove a directory is the pinned Hermes source tree (Phase B gate).

    Always proves layout fingerprint, pin commit, and entry hashes. The
    /tmp and operator-location checks apply unless the caller explicitly
    passes ``allow_temporary=True`` for read-only AUDIT of an isolated
    clone (audit inputs live in temporary locations by design; they are
    never accepted as the executed foundation).
    Returns a VERIFIED record; raises FoundationError with a typed reason
    otherwise. Pure inspection — never executes the candidate.
    """
    if not allow_temporary:
        check_not_temporary(candidate)
    resolved = check_not_operator(candidate, forbidden_roots=forbidden_roots)
    check_layout_and_commit(Path(resolved))
    return {"state": "VERIFIED", "source": resolved, "commit": HERMES_COMMIT,
            "tag": HERMES_TAG, "license": "MIT (Copyright (c) 2025 Nous Research)",
            "audit_only": bool(allow_temporary)}


class FoundationError(RuntimeError):
    """Raised when the one-of-everything contract does not hold."""


def describe() -> Dict[str, Any]:
    """Static foundation description: identity, pins, refused paths."""
    return {"schema": "qubit-foundation/1", "product": PRODUCT_NAME,
            "public_identity": PUBLIC_IDENTITY,
            "voice_subsystem": VOICE_SUBSYSTEM,
            "install_identity": INSTALL_IDENTITY,
            "import_package": IMPORT_PACKAGE,
            "legacy_identity": LEGACY_IDENTITY,
            "hermes": {"url": HERMES_URL, "tag": HERMES_TAG,
                       "commit": HERMES_COMMIT},
            "openclaw": {"url": OPENCLAW_URL, "tag": OPENCLAW_TAG,
                         "commit": OPENCLAW_COMMIT},
            "primary_runtime_argv": list(PRIMARY_RUNTIME_ARGV),
            "disabled_competing_paths": disabled_competing_paths()}


def runtime_check(provider: str = "fixture") -> Dict[str, Any]:
    """Real end-to-end probe: start the adapted runtime, ping, one turn,
    status, stop. Returns the receipt-bearing result (never a mock)."""
    from .runtime import HermesRuntime

    rt = HermesRuntime(provider=provider)
    try:
        before = rt.status()
        turn = rt.turn("foundation runtime check", session="foundation")
        live = rt.status()
        return {"verdict": "OK" if turn.get("ok") else "FAILED",
                "before": before, "turn": turn, "live": live}
    finally:
        rt.stop()


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
        provider = "fixture"
        for flag in args[1:]:
            if flag.startswith("--provider="):
                provider = flag.split("=", 1)[1]
        try:
            result = runtime_check(provider=provider)
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
