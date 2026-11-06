"""HUD profiles collector — Qubit-native re-point of ``backend/collectors/profiles.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: MAP (registry is shared; per-profile stats go through the authority)
----------------------------------------------------------------------------
The previous lane marked this GAP on isolation grounds: "the HUD's cross-profile
aggregate would need a second home reader that violates island isolation." The
isolation worry is right; the conclusion is not, because the authority already
takes a cfg. Evidence:

* :func:`voicebridge.profiles` (reached via the same registry
  ``GET /api/profiles`` serves) lists every profile with name/model/provider/
  has_env/skill_count/gateway_running/description — that is the cross-profile
  REGISTRY, a deliberate shared surface, not a home reader.
* :class:`voicebridge.qubit_session_store.QubitSessionStore` is constructed from
  a cfg. Handing it a profile-scoped cfg and calling ``list()`` returns THAT
  profile's totals (``total``) — one authority, one home, no second opener and
  no direct ``state.db`` read.

So: per-profile registry facts come from the registry; per-profile session
counts come from the authority, scoped one profile at a time. This module reads
NO file inside another profile's home directly. It never aggregates a profile's
messages across islands beyond its own ``total`` count.

Deliberately NOT ported
-----------------------
* ``_check_gateway_status`` — upstream ran ``systemctl --user is-active
  hermes-gateway-<name>``. Shelling a service manager per profile from a web
  read is out of scope here; the Gateway tab owns the live service read through
  :mod:`voicebridge.gateway_supervisor`.
* ``_check_server_status`` — an outbound HTTP probe of a local model server. The
  dashboard serves no provider probe (``/api/providers/validate`` is a recorded
  refusal), and a HUD tab does not invent one.
* ``_read_api_keys`` — a ``KEY``/``TOKEN``/``SECRET`` substring scan of another
  profile's ``.env``. :meth:`ProfileInfo.has_env` answers the same question
  without reading the file, so the scan is dropped.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _registry(cfg: Any) -> Tuple[Any, List[Any], str]:
    """(registry module, info rows, active name) — or raise."""
    from .profiles_shim import profiles_registry

    P = profiles_registry(cfg)
    infos = list(P.list_profiles())
    try:
        active = str(P.get_active_profile() or "default")
    except Exception:  # noqa: BLE001 - a miss is not a failed read
        active = "default"
    return P, infos, active


def _sessions_for(cfg: Any, name: str) -> Dict[str, Any]:
    """This profile's session totals, read through the ONE session authority.

    A profile cfg is derived on top of the live config so the authority resolves
    the profile's own home. Failure is reported per profile: one island's
    unreadable store must not blank the others.
    """
    out: Dict[str, Any] = {"available": False, "detail": "", "total": None}
    try:
        from ..qubit_session_store import QubitSessionStore

        scoped = _scoped_cfg(cfg, name)
        page = QubitSessionStore(scoped).list(limit=1)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    if not isinstance(page, dict):
        out["detail"] = "the session authority returned no page"
        return out
    total = page.get("total")
    if total is None:
        total = len(page.get("sessions") or [])
    out.update(available=True, total=int(total), detail="")
    return out


def _scoped_cfg(cfg: Any, name: str) -> Any:
    """A cfg whose reads resolve *name*'s home.

    Uses the voicebridge profile mechanism (``QUBIT_PROFILE`` in the runtime
    environment), which is how the rest of the bridge addresses a non-default
    island — the profile's home is resolved by the runtime, never assembled
    here.
    """
    from .profiles_shim import scoped_config

    return scoped_config(cfg, name)


def collect_profiles(home: Optional[Any] = None, cfg: Any = None, *,
                     with_sessions: bool = True) -> Dict[str, Any]:
    """Every profile island: registry facts plus its own session total."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "profiles": [],
        "total": 0,
        "active": "",
        "authority": "voicebridge.profiles registry + "
                     "voicebridge.qubit_session_store.QubitSessionStore",
    }
    try:
        _P, infos, active = _registry(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"profile registry unavailable ({type(exc).__name__}: {exc})"
        return out

    rows: List[Dict[str, Any]] = []
    for info in infos:
        name = str(getattr(info, "name", "") or "")
        if not name:
            continue
        row: Dict[str, Any] = {
            "name": name,
            "is_default": bool(getattr(info, "is_default", False)),
            "active": name == active,
            "model": getattr(info, "model", None),
            "provider": getattr(info, "provider", None),
            "has_env": bool(getattr(info, "has_env", False)),
            "skill_count": int(getattr(info, "skill_count", 0) or 0),
            "gateway_running": bool(getattr(info, "gateway_running", False)),
            "description": str(getattr(info, "description", "") or "")[:300],
            "display_name": str(getattr(info, "display_name", "") or ""),
            "distribution_name": getattr(info, "distribution_name", None),
            "distribution_version": getattr(info, "distribution_version", None),
            "sessions": None,
            "sessions_detail": "",
        }
        if with_sessions:
            sess = _sessions_for(cfg, name)
            row["sessions"] = sess.get("total") if sess.get("available") else None
            row["sessions_detail"] = str(sess.get("detail") or "")
        rows.append(row)

    out.update(
        available=True,
        detail=f"{len(rows)} profile island(s)",
        profiles=rows,
        total=len(rows),
        active=active,
        not_ported={
            "gateway_service_probe": "systemctl per profile is not run from a "
                                     "web read; see the Gateway tab",
            "local_server_probe": "no outbound probe is issued by this build",
            "api_key_scan": "ProfileInfo.has_env answers this without reading "
                            "another island's .env",
        },
    )
    return out


__all__ = ["collect_profiles"]
