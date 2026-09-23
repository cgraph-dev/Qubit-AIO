"""Qubit-native credential-pool inventory + removal (A32).

The vendored credential pool (``agent.credential_pool`` / ``qubit_cli.auth``)
stores provider API keys. Qubit's policy never serves secret material to the
browser, so the upstream ``/api/credentials/pool`` route stays gated. This
module provides the real allowed counterpart the dashboard Remove control
needs:

* :func:`inventory` — a redacted provider/entry view (index, ids, labels,
  source, status). It never reads or returns a token value or fragment.
* :func:`remove` — a confirmed, e-stop-aware, audited removal delegating to the
  vendored ``CredentialPool.remove_index`` (the same persistence path the
  runtime uses), returning only non-secret metadata.

No second credential store is created.
"""
from __future__ import annotations

from typing import Any, Dict, List

SCHEMA = "qubit-credentials-pool/1"
CONFIRM_CODE = "CREDENTIAL_CONFIRM_REQUIRED"
ESTOP_CODE = "CREDENTIAL_ESTOP"
BAD_INPUT_CODE = "CREDENTIAL_BAD_INPUT"
UNAVAILABLE_CODE = "CREDENTIAL_STORE_UNAVAILABLE"
REF_REQUIRED_CODE = "CREDENTIAL_REFERENCE_REQUIRED"
REF_MISSING_CODE = "CREDENTIAL_REFERENCE_UNRESOLVED"


class CredentialError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text))
    except Exception:
        return str(text)


def _ensure_path() -> None:
    from . import qubit_runtime

    qubit_runtime.ensure_path()


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"credentials.{op}", actor=actor, **detail)
    except Exception:
        pass


def _entry_public(index: int, entry: Dict[str, Any]) -> Dict[str, Any]:
    """A redacted pool entry row. Never a token value or preview fragment."""
    return {
        "index": index,
        "id": _redact(entry.get("id", ""))[:32] or None,
        "label": _redact(entry.get("label", ""))[:64] or None,
        "auth_type": _redact(entry.get("auth_type", ""))[:32] or None,
        "source": _redact(entry.get("source", ""))[:32] or None,
        "priority": int(entry.get("priority", index - 1) or 0),
        "last_status": _redact(entry.get("last_status", ""))[:32] or None,
        "request_count": int(entry.get("request_count", 0) or 0),
        # Empty by policy: Qubit never serves a token fragment.
        "token_preview": "",
        "has_refresh": bool(entry.get("refresh_token")),
    }


def inventory(cfg: Any) -> Dict[str, Any]:
    """Redacted provider/entry inventory (references only, no token)."""
    _ensure_path()
    try:
        from qubit_cli.auth import read_credential_pool
    except Exception as exc:
        raise CredentialError(UNAVAILABLE_CODE,
                              f"credential store unavailable ({exc})")
    try:
        pool = read_credential_pool(None)
    except Exception as exc:
        raise CredentialError(UNAVAILABLE_CODE,
                              f"credential store unreadable ({exc})")
    providers: List[Dict[str, Any]] = []
    if isinstance(pool, dict):
        for provider in sorted(pool):
            rows = pool.get(provider)
            if not isinstance(rows, list):
                continue
            entries = [_entry_public(i, e) for i, e in enumerate(rows, start=1)
                       if isinstance(e, dict)]
            providers.append({"provider": str(provider), "count": len(entries),
                              "entries": entries})
    return {"state": "OK", "schema": SCHEMA, "providers": providers,
            "authority": "names/labels only; no token value or fragment is "
                         "read or returned"}


def _resolve_ref(key_ref: str) -> str:
    """Resolve an ``env:NAME`` / ``file:PATH`` reference server-side.

    The browser never sends a secret value; the operator supplies only a
    reference and the resolved value never leaves this process (it is written
    to the canonical pool and never returned or logged).
    """
    ref = str(key_ref or "").strip()
    if ref.startswith("env:"):
        import os

        return os.environ.get(ref[len("env:"):], "")
    if ref.startswith("file:"):
        import os
        from pathlib import Path

        try:
            return Path(os.path.expanduser(ref[len("file:"):])).read_text(
                encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def add_reference(cfg: Any, provider: str, key_ref: str, label: str = "",
                  *, confirmed: bool = False,
                  requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Add one credential to the canonical pool from an env:/file: reference.

    A raw key sent by the browser is refused; only a reference is accepted and
    the value is resolved server-side. Confirmed, e-stop-aware, audited, and
    never returns the token.
    """
    if _estop(cfg):
        return {"state": "ESTOP", "code": ESTOP_CODE,
                "detail": "emergency stop latched; credential add refused"}
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": "a provider id is required"}
    ref = str(key_ref or "").strip()
    if not ref.startswith(("env:", "file:")):
        return {"state": "denied", "code": REF_REQUIRED_CODE,
                "detail": "credentials are added by reference only "
                          "(env:NAME or file:PATH), never a raw key value"}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "adding a pooled credential requires explicit "
                          "confirmation",
                "provider": clean_provider, "reference": ref}
    secret = _resolve_ref(ref)
    if not secret:
        return {"state": "FAILED", "code": REF_MISSING_CODE,
                "detail": f"reference {ref!r} did not resolve to a value"}
    _ensure_path()
    try:
        from agent.credential_pool import PooledCredential, load_pool
    except Exception as exc:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": _redact(str(exc))[:200]}
    try:
        entry = PooledCredential.from_dict(clean_provider, {
            "label": _redact(label)[:64] or clean_provider,
            "auth_type": "api_key", "source": "manual",
            "access_token": secret,
        })
        pool = load_pool(clean_provider)
        added = pool.add_entry(entry)
    except Exception as exc:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": _redact(str(exc))[:200]}
    # Audit only provider/label/reference name — never the value.
    _audit("add", requested_by,
           {"provider": clean_provider, "reference": ref,
            "label": _redact(label)[:64]})
    return {"state": "OK", "schema": SCHEMA, "provider": clean_provider,
            "entry": _entry_public(0, {"id": getattr(added, "id", ""),
                                       "label": getattr(added, "label", ""),
                                       "source": getattr(added, "source", "")}),
            "reference": ref, "token_preview": "",
            "authority": "added through the canonical credential-pool "
                         "persistence path from a server-resolved reference; "
                         "no token value returned or logged"}


def remove(cfg: Any, provider: str, index: Any, *, confirmed: bool = False,
           requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Remove one pool entry by provider + 1-based index (confirmed, audited)."""
    if _estop(cfg):
        return {"state": "ESTOP", "code": ESTOP_CODE,
                "detail": "emergency stop latched; credential removal refused"}
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": "a provider id is required"}
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": "index must be a positive integer"}
    if idx < 1:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": "index must be a positive integer"}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "removing a pooled credential requires explicit "
                          "confirmation",
                "provider": clean_provider, "index": idx}
    _ensure_path()
    try:
        from agent.credential_pool import load_pool
    except Exception as exc:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": _redact(str(exc))[:200]}
    try:
        pool = load_pool(clean_provider)
        removed = pool.remove_index(idx)
    except Exception as exc:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": _redact(str(exc))[:200]}
    if removed is None:
        return {"state": "MISSING", "code": BAD_INPUT_CODE,
                "detail": f"no credential at {clean_provider!r} index {idx}"}
    _audit("remove", requested_by, {"provider": clean_provider, "index": idx})
    return {"state": "OK", "schema": SCHEMA, "provider": clean_provider,
            "removed": {"index": idx,
                        "id": _redact(getattr(removed, "id", ""))[:32] or None,
                        "source": _redact(getattr(removed, "source", ""))[:32]
                        or None},
            "authority": "removed through the canonical credential-pool "
                         "persistence path; no token value returned"}
