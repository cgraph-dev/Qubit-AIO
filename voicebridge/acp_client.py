"""Qubit ACP external harness client (OpenClaw audit #58).

Qubit recognizes the external agent harnesses (Claude Code, Cursor, Gemini,
Codex, Droid, opencode) and provides a real *client contract*: a registry,
credential-reference configuration, PATH presence detection, request shaping,
receipts, and audit. It never executes a harness binary and never opens a
connection:

* harness presence is probed with :func:`shutil.which` only -- a path lookup,
  never a version/agent invocation;
* ``handshake`` is confirmation-gated and estop-aware; with an absent binary
  or an unconfigured harness it resolves to the typed
  ``ACP_HARNESS_UNAVAILABLE`` gate. With both present it shapes the real ACP
  ``initialize`` JSON-RPC request and records a redacted receipt, reporting
  ``executed: false`` -- the harness is the operator's to run;
* ``send`` always resolves to the typed ``ACP_REFUSED`` gate (no live ACP
  session/transport exists and Qubit never executes a harness) and shapes the
  ``session/prompt`` request without sending it;
* the per-harness *adapter* contract (``adapters``/``shape_method``) shapes
  every documented ACP method, and ``adapter_session`` drives a real
  ``initialize`` + ``session/prompt`` exchange over an operator-injected
  in-process transport — Qubit still never spawns the harness binary, and
  without a transport the typed ``ACP_TRANSPORT_UNAVAILABLE`` gate is raised;
* prompts, paths, and receipts pass through centralized redaction, and the
  config store is 0600 and bounded.

Configuration lives in ``<state_dir>/acp-client.json``; redacted receipts in
``acp-log.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-acp-client/1"
STORE_NAME = "acp-client.json"
LOG_NAME = "acp-log.jsonl"
MAX_FILE_BYTES = 1_000_000
MAX_LOG_LINES = 2000
MAX_PROMPT_CHARS = 8000
MAX_LABEL_CHARS = 64

ACP_PROTOCOL_VERSION = 1

CONFIRM_CODE = "ACP_CONFIRM_REQUIRED"
ESTOP_CODE = "ACP_ESTOP"
UNKNOWN_HARNESS_CODE = "ACP_UNKNOWN_HARNESS"
HARNESS_UNAVAILABLE_CODE = "ACP_HARNESS_UNAVAILABLE"
NO_CREDENTIAL_CODE = "ACP_NO_CREDENTIAL"
REFUSED_CODE = "ACP_REFUSED"
BAD_INPUT_CODE = "ACP_BAD_INPUT"
EXISTS_CODE = "ACP_EXISTS"
NOT_FOUND_CODE = "ACP_NOT_FOUND"
CORRUPT_CODE = "ACP_STORE_CORRUPT"
TRANSPORT_CODE = "ACP_TRANSPORT_UNAVAILABLE"
ADAPTER_METHODS = ("initialize", "session/prompt", "session/cancel")

# Conventional harness binaries and their ambient credential kind. Presence is
# detected by PATH lookup only; the binary is never executed.
HARNESS_CAPS: Dict[str, Dict[str, Any]] = {
    "claude-code": {"label": "Claude Code", "binary": "claude",
                    "credential_kind": "oauth-or-api-key",
                    "detail": "run `claude` yourself; Qubit shapes the ACP "
                              "request but does not execute it"},
    "cursor": {"label": "Cursor", "binary": "cursor-agent",
               "credential_kind": "oauth",
               "detail": "run `cursor-agent` yourself; no ACP transport "
                         "adapter executes in Qubit"},
    "gemini": {"label": "Gemini CLI", "binary": "gemini",
               "credential_kind": "oauth-or-api-key",
               "detail": "run `gemini` yourself; Qubit does not execute it"},
    "codex": {"label": "Codex", "binary": "codex",
              "credential_kind": "api-key",
              "detail": "run `codex` yourself; Qubit does not execute it"},
    "droid": {"label": "Droid", "binary": "droid",
              "credential_kind": "api-key",
              "detail": "run `droid` yourself; Qubit does not execute it"},
    "opencode": {"label": "opencode", "binary": "opencode",
                 "credential_kind": "oauth-or-api-key",
                 "detail": "run `opencode` yourself; Qubit does not execute it"},
}

HARNESSES = tuple(HARNESS_CAPS)


class AcpError(Exception):
    """Typed ACP client failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _redact_obj(obj: Any) -> Any:
    from .redaction import redact_obj

    return redact_obj(obj)


def _now() -> float:
    return time.time()


def _short(path: str) -> str:
    home = str(Path("~").expanduser())
    return ("~" + path[len(home):]) if home != "~" and path.startswith(home) \
        else path


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _log_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / LOG_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "harnesses": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AcpError(CORRUPT_CODE, "ACP store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except AcpError:
        raise
    except (OSError, ValueError) as exc:
        raise AcpError(CORRUPT_CODE,
                       f"ACP store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("harnesses"), dict):
        raise AcpError(CORRUPT_CODE, "ACP store has an unknown schema")
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise AcpError(CORRUPT_CODE, f"ACP store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"acp.{op}", actor=actor, **detail)
    except Exception:
        pass


def _receipt(cfg: Any, harness: str, verdict: str, detail: str) -> None:
    path = _log_path(cfg)
    rec = {"ts": _now(), "harness": harness, "verdict": verdict,
           "detail": _redact(detail)[:200]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_LOG_LINES:
                path.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n",
                                encoding="utf-8")
        except OSError:
            pass
    except OSError:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before using ACP harnesses.",
            "owner": "qubit-policy",
            "rollback": "no harness configured or executed; nothing to roll "
                        "back"}


def _secret_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def _redact_secret_kind(ref: str) -> str:
    kind = _secret_kind(ref)
    return kind if kind == "missing" else f"{kind}:<reference>"


def _resolve_credential(rec: Dict[str, Any]) -> str:
    kind = rec.get("credential_ref_kind", "missing")
    if kind == "env":
        return "env:" + rec.get("_credential_name", "")
    if kind == "file":
        return rec.get("_credential_path", "")
    return ""


def _check_harness(harness: str) -> str:
    clean = (harness or "").strip().lower()
    if clean not in HARNESSES:
        raise AcpError(UNKNOWN_HARNESS_CODE,
                       f"unknown harness {harness!r}; known: "
                       f"{', '.join(HARNESSES)}")
    return clean


def _which(caps: Dict[str, Any]) -> Optional[str]:
    """PATH presence probe only; the binary is never executed."""
    try:
        return shutil.which(caps.get("binary", ""))
    except Exception:
        return None


def _handshake_request(harness: str, caps: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": ACP_PROTOCOL_VERSION,
                       "clientCapabilities": {
                           "fs": {"readTextFile": True,
                                  "writeTextFile": False}},
                       "clientInfo": {"name": "qubit", "version": "1.0.0"},
                       "harness": harness,
                       "harnessLabel": caps.get("label", harness)}}


def _prompt_request(harness: str, prompt: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"harness": harness,
                       "prompt": [{"type": "text", "text": prompt}]}}


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"harness": rec["harness"], "label": rec.get("label", ""),
            "credential": _redact_secret_kind(_resolve_credential(rec)),
            "enabled": bool(rec.get("enabled", True)),
            "configured_at": rec.get("configured_at"),
            "updated_by": rec.get("updated_by", "")}


def configure(cfg: Any, harness: str, *, credential_ref: str = "",
              label: str = "", confirmed: bool = False,
              requested_by: str = "cli:user") -> Dict[str, Any]:
    """Record a harness credential *reference* (never a value)."""
    if estop_engaged(cfg):
        raise AcpError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise AcpError(CONFIRM_CODE,
                       "ACP configure requires explicit --confirm")
    clean = _check_harness(harness)
    ref = (credential_ref or "").strip()
    if ref and _secret_kind(ref) == "missing":
        raise AcpError(BAD_INPUT_CODE,
                       "credential_ref must be an env:VAR or file:PATH "
                       "reference")
    state = _load(cfg)
    if clean in state["harnesses"]:
        raise AcpError(EXISTS_CODE, f"harness {clean!r} is configured")
    rec = {"harness": clean, "label": (label or "").strip()[:MAX_LABEL_CHARS],
           "credential_ref_kind": _secret_kind(ref), "enabled": True,
           "configured_at": _now(), "updated_by": requested_by}
    if ref.startswith("env:"):
        rec["_credential_name"] = ref[len("env:"):].strip()[:64]
    elif ref.startswith("file:"):
        rec["_credential_path"] = ref
    else:
        rec["credential_ref"] = ""
    state["harnesses"][clean] = rec
    _save(cfg, state)
    _audit("configure", requested_by, {"harness": clean})
    return {"state": "OK", "harness": _public(rec),
            "note": "reference recorded; Qubit never executes the harness"}


def unconfigure(cfg: Any, harness: str, *, confirmed: bool = False,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a configured harness (confirmed, audited)."""
    if estop_engaged(cfg):
        raise AcpError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise AcpError(
            CONFIRM_CODE, "ACP unconfigure requires explicit --confirm")
    clean = _check_harness(harness)
    state = _load(cfg)
    if clean not in state["harnesses"]:
        raise AcpError(NOT_FOUND_CODE, f"harness {clean!r} is not configured")
    del state["harnesses"][clean]
    _save(cfg, state)
    _audit("unconfigure", requested_by, {"harness": clean})
    return {"state": "OK", "removed": clean}


def catalog(cfg: Any) -> Dict[str, Any]:
    """Every harness with PATH presence + configured state (honest)."""
    from .providers.credentials import resolve_key

    configured = _load(cfg).get("harnesses", {})
    rows: List[Dict[str, Any]] = []
    for hid, caps in HARNESS_CAPS.items():
        binary = _which(caps)
        rec = configured.get(hid)
        if binary is None:
            state, code = "UNAVAILABLE", HARNESS_UNAVAILABLE_CODE
            credential = "missing"
        elif rec is None:
            state, code = "UNAVAILABLE", HARNESS_UNAVAILABLE_CODE
            credential = "missing"
        else:
            credential = _redact_secret_kind(_resolve_credential(rec))
            ref = _resolve_credential(rec)
            key, rc = resolve_key("acp", ref) if ref else ("", "")
            if ref and (rc or not key):
                state, code = "NO_CREDENTIAL", NO_CREDENTIAL_CODE
            else:
                state, code = "CONFIGURED", ""
        rows.append({"harness": hid, "label": caps.get("label", hid),
                     "binary": caps.get("binary", ""),
                     "present": binary is not None,
                     "path": _short(binary) if binary else None,
                     "credential_kind": caps.get("credential_kind", "unknown"),
                     "credential": credential,
                     "configured": rec is not None,
                     "adapter": "operator-injected",
                     "state": state, "code": code,
                     "detail": caps.get("detail", "")})
    return {"state": "OK", "schema": SCHEMA, "harnesses": rows,
            "available": [r["harness"] for r in rows
                          if r["state"] == "CONFIGURED"],
            "connected": [],
            "live_sessions": 0}


def status(cfg: Any) -> Dict[str, Any]:
    """Typed ACP client state: no live sessions, never a fake connection."""
    latched = estop_engaged(cfg)
    try:
        configured = len(_load(cfg).get("harnesses", {}))
    except AcpError:
        configured = -1
    present = sum(1 for caps in HARNESS_CAPS.values()
                  if _which(caps) is not None)
    return {"state": "ESTOP" if latched else "OK",
            "code": ESTOP_CODE if latched else "",
            "schema": SCHEMA, "harnesses": len(HARNESSES),
            "configured": configured, "present": present,
            "live_sessions": 0,
            "detail": "real registry/contract with typed "
                      "ACP_HARNESS_UNAVAILABLE until a binary and credential "
                      "are configured; Qubit never executes a harness",
            "estop": latched}


def handshake(cfg: Any, harness: str, *,
              confirmed: bool = False) -> Dict[str, Any]:
    """Shape the ACP ``initialize`` request, or gate with UNAVAILABLE.

    Absent binary or unconfigured harness -> typed
    ``ACP_HARNESS_UNAVAILABLE``. Present + configured -> the real request is
    shaped and a redacted receipt is recorded; ``executed`` stays False and
    the harness is never spawned.
    """
    from .providers.credentials import resolve_key

    if estop_engaged(cfg):
        raise AcpError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise AcpError(CONFIRM_CODE,
                       "ACP handshake requires explicit --confirm")
    clean = _check_harness(harness)
    caps = HARNESS_CAPS[clean]
    rec = _load(cfg)["harnesses"].get(clean)
    if rec is None:
        _receipt(cfg, clean, "UNAVAILABLE", "harness not configured")
        raise AcpError(HARNESS_UNAVAILABLE_CODE,
                       f"harness {clean!r} is not configured")
    binary = _which(caps)
    if binary is None:
        _receipt(cfg, clean, "UNAVAILABLE", "binary absent from PATH")
        raise AcpError(HARNESS_UNAVAILABLE_CODE,
                       f"harness binary {caps.get('binary')!r} is absent from "
                       "PATH")
    ref = _resolve_credential(rec)
    if ref:
        key, code = resolve_key("acp", ref)
        if code or not key:
            _receipt(cfg, clean, "NO_CREDENTIAL", "credential unresolvable")
            raise AcpError(NO_CREDENTIAL_CODE,
                           "harness credential reference unresolvable")
    request = _handshake_request(clean, caps)
    _receipt(cfg, clean, "HANDSHAKE_SHAPED",
             f"initialize request for {caps.get('binary')}")
    _audit("handshake", "cli:user", {"harness": clean})
    return {"state": "READY", "code": "", "schema": SCHEMA, "harness": clean,
            "binary": _short(binary), "request": request, "executed": False,
            "note": "ACP initialize request shaped; Qubit does not execute the "
                    "harness -- run it yourself"}


def send(cfg: Any, harness: str, *, prompt: str,
         confirmed: bool = False) -> Dict[str, Any]:
    """Never sends: always the typed ``ACP_REFUSED`` gate.

    The prompt is validated/bounded and the ``session/prompt`` request is
    shaped and receipted, but no live ACP session exists and Qubit never
    executes a harness, so nothing is sent.
    """
    if estop_engaged(cfg):
        raise AcpError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise AcpError(CONFIRM_CODE,
                       "ACP send requires explicit --confirm")
    clean = _check_harness(harness)
    text = prompt or ""
    if not text.strip():
        raise AcpError(BAD_INPUT_CODE, "prompt must not be empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise AcpError(BAD_INPUT_CODE,
                       f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    caps = HARNESS_CAPS[clean]
    rec = _load(cfg)["harnesses"].get(clean)
    binary = _which(caps)
    request = _prompt_request(clean, text)
    _receipt(cfg, clean, "REFUSED",
             f"session/prompt shaped for {caps.get('binary')}; not sent")
    _audit("send_refused", "cli:user",
           {"harness": clean, "prompt_sha256": hashlib.sha256(
               text.encode()).hexdigest()})
    if rec is None or binary is None:
        raise AcpError(REFUSED_CODE,
                       f"harness {clean!r} is not configured (binary or "
                       "credential absent); nothing sent")
    raise AcpError(REFUSED_CODE,
                   "no live ACP session/transport exists and Qubit never "
                   "executes a harness; request shaped but nothing sent")


def receipts(cfg: Any, *, harness: str = "") -> Dict[str, Any]:
    """Read redacted ACP receipts (bounded tail)."""
    path = _log_path(cfg)
    rows: List[Dict[str, Any]] = []
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-200:]:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        rows = []
    if (harness or "").strip():
        want = harness.strip().lower()
        rows = [r for r in rows if r.get("harness") == want]
    return {"state": "OK", "receipts": rows}


def doctor(cfg: Any) -> Dict[str, Any]:
    """Diagnostics: store health, PATH presence, config, and the latch."""
    warnings: List[str] = []
    store_ok = True
    try:
        configured = _load(cfg)["harnesses"]
    except AcpError as exc:
        store_ok = False
        configured = {}
        warnings.append(f"{exc.code}: {exc.detail[:200]}")
    present = sorted(h for h, caps in HARNESS_CAPS.items()
                     if _which(caps) is not None)
    return {"state": "OK" if store_ok else "DEGRADED", "schema": SCHEMA,
            "store_ok": store_ok, "harnesses": len(HARNESSES),
            "present": present, "configured": sorted(configured),
            "live_sessions": 0,
            "external_runtime": HARNESS_UNAVAILABLE_CODE,
            "estop": estop_engaged(cfg), "warnings": warnings}


# ===========================================================================
# ACP harness adapters (OC-58, no-credential parts)
#
# A real adapter contract per recognized harness: the method set, the
# request/response shapes, and a *transport-injected* session loop. Qubit
# never spawns the harness binary; an operator (or a test) injects an
# in-process transport callable, and the adapter drives the ACP JSON-RPC
# exchange through it. Without a transport the call is the typed
# ACP_TRANSPORT_UNAVAILABLE gate, so production stays non-executing.
# ===========================================================================
def _adapter_record(harness: str) -> Dict[str, Any]:
    caps = HARNESS_CAPS[harness]
    return {"harness": harness, "label": caps.get("label", harness),
            "binary": caps.get("binary", ""),
            "protocol": "acp-jsonrpc/1", "methods": list(ADAPTER_METHODS),
            "credential_kind": caps.get("credential_kind", "unknown"),
            "transport": "operator-injected (Qubit never spawns the harness)",
            "client_info": {"name": "qubit", "version": "1.0.0"}}


def adapters(cfg: Any = None) -> Dict[str, Any]:
    """Every harness adapter descriptor (read-only)."""
    rows = [_adapter_record(h) for h in HARNESSES]
    return {"state": "OK", "schema": "qubit-acp-adapters/1", "adapters": rows}


def adapter_descriptor(harness: str) -> Dict[str, Any]:
    """One harness adapter descriptor, or the typed unknown-harness error."""
    clean = _check_harness(harness)
    return {"state": "OK", "schema": "qubit-acp-adapters/1",
            "adapter": _adapter_record(clean)}


def shape_method(harness: str, method: str,
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Shape one ACP JSON-RPC request for a harness (pure; never sent)."""
    clean = _check_harness(harness)
    caps = HARNESS_CAPS[clean]
    name = str(method or "").strip()
    if name not in ADAPTER_METHODS:
        raise AcpError(BAD_INPUT_CODE,
                       f"method must be one of {ADAPTER_METHODS}")
    if name == "initialize":
        return {"state": "OK", "request": _handshake_request(clean, caps)}
    if name == "session/cancel":
        return {"state": "OK", "request": {
            "jsonrpc": "2.0", "id": 3, "method": "session/cancel",
            "params": {"harness": clean,
                       "sessionId": str((params or {}).get("sessionId", ""))[:128]}}}
    prompt = str((params or {}).get("prompt", ""))
    if len(prompt) > MAX_PROMPT_CHARS:
        raise AcpError(BAD_INPUT_CODE,
                       f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    return {"state": "OK", "request": _prompt_request(clean, prompt)}


def adapter_session(cfg: Any, harness: str, *, prompt: str,
                    transport: Any = None, confirmed: bool = False,
                    requested_by: str = "cli:user") -> Dict[str, Any]:
    """Drive a real ACP exchange over an operator-injected transport (OC-58).

    Qubit shapes the ``initialize`` and ``session/prompt`` requests and passes
    them, in order, to the injected ``transport(request) -> response`` callable.
    The harness binary is never spawned and no socket is opened by Qubit; with
    no transport the typed ``ACP_TRANSPORT_UNAVAILABLE`` gate is raised.
    """
    if estop_engaged(cfg):
        raise AcpError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise AcpError(CONFIRM_CODE,
                       "ACP session requires explicit --confirm")
    clean = _check_harness(harness)
    text = (prompt or "").strip()
    if not text:
        raise AcpError(BAD_INPUT_CODE, "prompt must not be empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise AcpError(BAD_INPUT_CODE,
                       f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    if not callable(transport):
        _receipt(cfg, clean, "TRANSPORT_UNAVAILABLE",
                 "no operator transport injected; Qubit spawns nothing")
        raise AcpError(
            TRANSPORT_CODE,
            "no operator-injected ACP transport: Qubit never spawns a harness "
            "binary; inject a transport to drive a real exchange")
    caps = HARNESS_CAPS[clean]
    requests = [_handshake_request(clean, caps), _prompt_request(clean, text)]
    responses: List[Any] = []
    for request in requests:
        try:
            response = transport(dict(request))
        except Exception as exc:
            _receipt(cfg, clean, "TRANSPORT_ERROR", _redact(str(exc)))
            raise AcpError(
                TRANSPORT_CODE,
                f"operator transport failed: {_redact(str(exc))[:160]}")
        if not isinstance(response, dict):
            raise AcpError(TRANSPORT_CODE,
                           "operator transport returned a non-object response")
        responses.append(_redact_obj(response))
    _receipt(cfg, clean, "ADAPTER_SESSION",
             f"{len(requests)} requests via operator transport")
    _audit("adapter_session", requested_by,
           {"harness": clean, "prompt_sha256": hashlib.sha256(
               text.encode()).hexdigest()})
    return {"state": "OK", "schema": "qubit-acp-adapters/1",
            "harness": clean, "requests": [_redact_obj(r) for r in requests],
            "responses": responses, "executed": True, "spawned": False,
            "transport": "operator-injected",
            "note": "ACP exchange driven in-process; Qubit never spawns the "
                    "harness"}
