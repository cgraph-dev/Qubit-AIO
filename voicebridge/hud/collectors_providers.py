"""HUD providers collector — Qubit-native re-point of ``backend/collectors/providers.py``.

Upstream ``providers.py`` is a 13-line stub router over a collector; the HUD's
Providers tab shells ``hermes auth`` and parses the text. This module contains
no upstream code (the upstream collector behind it shells a CLI); it is
Qubit-authored against Qubit's own credential inventory.

DECISION: MAP (Qubit exposes a redacted credential inventory; upstream shelled)
-----------------------------------------------------------------------------
The previous lane marked this GAP. That is wrong in the direction that matters:
Qubit exposes the data MORE safely than upstream did.

* Upstream: ``hermes auth`` on PATH, parsed as text. Shelling a binary is
  forbidden here (hard rule 2) and would also be the least safe way to read
  credential state.
* Qubit: :func:`voicebridge.credentials.inventory` returns, per provider, the
  credential-pool entries with ``id``/``label``/``auth_type``/``source``/
  ``priority``/``last_status``/``request_count``/``has_refresh`` and an
  explicitly EMPTY ``token_preview`` — "names/labels only; no token value or
  fragment is read or returned" is its own contract.
* :func:`voicebridge.auth_status.auth_status` adds the frontier policy view:
  per configured provider, whether its ``key_ref`` is ``env``/``file``/
  ``missing`` and whether it is allowlisted.

Both are read verbatim; no value is ever touched, previewed or counted. This
module adds no network probe: ``/api/providers/validate`` is a recorded refusal
in the compat table (``_NO_NETWORK_PROBE``), and a HUD tab must not invent the
live probe the dashboard itself declines to run.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _pool(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "",
                           "providers": [], "count": 0,
                           "authority": "voicebridge.credentials.inventory"}
    try:
        from .. import credentials as CR

        inv = CR.inventory(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"credential pool unavailable ({type(exc).__name__}: {exc})"
        return out
    rows = inv.get("providers") if isinstance(inv, dict) else None
    if not isinstance(rows, list):
        out["detail"] = "the credential inventory returned no provider list"
        return out
    providers: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entries = [e for e in (row.get("entries") or []) if isinstance(e, dict)]
        providers.append({
            "provider": str(row.get("provider") or ""),
            "count": int(row.get("count") or len(entries)),
            "configured": bool(entries),
            "entries": [{
                "index": e.get("index"),
                "id": e.get("id"),
                "label": e.get("label"),
                "auth_type": e.get("auth_type"),
                "source": e.get("source"),
                "priority": e.get("priority"),
                "last_status": e.get("last_status"),
                "request_count": e.get("request_count"),
                "has_refresh": bool(e.get("has_refresh")),
                # Always empty on the wire; kept so the panel can say so.
                "token_preview": "",
            } for e in entries],
        })
    providers.sort(key=lambda p: (-p["count"], p["provider"]))
    out.update(available=True,
               detail=f"{len(providers)} provider(s) in the credential pool",
               providers=providers,
               count=len(providers),
               configured_count=sum(1 for p in providers if p["configured"]),
               secrets_rule=str(inv.get("authority") or ""))
    return out


def _policy(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "", "providers": [],
                           "frontier_enabled": None,
                           "authority": "voicebridge.auth_status.auth_status"}
    try:
        from .. import auth_status as AS

        st = AS.auth_status(cfg)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"auth status unavailable ({type(exc).__name__}: {exc})"
        return out
    out.update(
        available=True,
        detail="key reference kind only (env/file/missing); no value is read",
        frontier_enabled=bool(st.get("frontier_enabled")),
        providers=[{
            "provider": str(r.get("provider") or ""),
            "key_ref": str(r.get("key_ref") or ""),
            "configured": bool(r.get("configured")),
            "allowlisted": bool(r.get("allowlisted")),
        } for r in (st.get("providers") or []) if isinstance(r, dict)],
        env_references=list(st.get("env_references") or []),
    )
    return out


def collect_providers(home: Optional[Any] = None, cfg: Any = None) -> Dict[str, Any]:
    """Credential-pool inventory plus the frontier key-reference view."""
    pool = _pool(cfg)
    policy = _policy(cfg)
    return {
        "available": bool(pool.get("available") or policy.get("available")),
        "detail": pool.get("detail") or policy.get("detail") or "",
        "pool": pool,
        "policy": policy,
        "secrets_rule": ("No token value, fragment or preview is read or "
                         "returned by any layer behind this panel."),
        "probe": None,
        "probe_reason": ("This build serves no provider validation probe: "
                         "/api/providers/validate is a recorded refusal "
                         "(network probe), so 'authenticated' here means "
                         "'configured', never 'pinged'."),
        "authority": "voicebridge.credentials + voicebridge.auth_status",
    }


__all__ = ["collect_providers"]
