"""Persisted, confirmation-gated toolset enable/disable (dashboard sweep).

The SPA toolset toggle (``PUT /api/tools/toolsets/{name}``) previously granted a
permission record but never persisted the ``enabled`` flag, and the read path
hard-coded ``enabled: True`` -- so toggling a toolset was a user-visible no-op.

This module is the canonical, persisted toolset state for the route layer:

* :func:`toolset_states` / :func:`toolset_enabled` -- the read path. Any toolset
  with no recorded state defaults to enabled (unchanged behavior); a recorded
  ``false`` stays false across reloads/restarts.
* :func:`stage_toolset_mutation` -- mint a single-use durable confirmation on
  the shared ``approvals.jsonl`` journal. Enabling grants a capability
  (``elevated``); disabling removes one (``reversible``). The envelope always
  carries a populated ``confirmation_id``.
* :func:`settle_toolset_mutation` / :func:`handle_toolset_mutation` -- consume
  the id once and persist idempotently.

State lives in ``<state_dir>/toolsets.json`` (0600, atomic), separate from the
plugin registry.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLSET_SCHEMA = "qubit-toolsets/1"
TOOLSET_STORE_NAME = "toolsets.json"
MAX_TOOLSETS = 512
MAX_STORE_BYTES = 262144

TOOLSET_CAPABILITY = "toolset.mutation"
DEFAULT_TTL_S = 300.0
MAX_TTL_S = 86400.0

# Typed codes for the route-layer contract.
CONFIRM_CODE = "CONFIRM_REQUIRED"
MALFORMED_CODE = "MALFORMED"
ESTOP_CODE = "ESTOP_LATCHED"
ACTOR_CODE = "ACTOR_REFUSED"
REPLAYED_CODE = "CONFIRMATION_SETTLED"
EXPIRED_CODE = "CONFIRMATION_EXPIRED"
UNKNOWN_CODE = "CONFIRMATION_UNKNOWN"
SESSION_CODE = "SESSION_MISMATCH"
STAGE_CODE = "CONFIRM_STAGE_ERROR"

RISK_REVERSIBLE = "reversible"
RISK_ELEVATED = "elevated"

_TS_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_HUMAN_ACTORS = ("user", "cli:user", "voice:user", "dashboard:user",
                 "tui:user", "web:user")


def _is_human(actor: str) -> bool:
    return str(actor or "") in _HUMAN_ACTORS


def _redact(text: str) -> str:
    try:
        from .redaction import redact_text

        return str(redact_text(str(text or "")))
    except Exception:
        return str(text or "")


def _now() -> float:
    return time.time()


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / TOOLSET_STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": TOOLSET_SCHEMA, "toolsets": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_STORE_BYTES:
            return _blank()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict) or raw.get("schema") != TOOLSET_SCHEMA \
            or not isinstance(raw.get("toolsets"), dict):
        return _blank()
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{TOOLSET_STORE_NAME}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"toolset.{op}", actor=actor, **detail)
    except Exception:
        pass


def _journal(cfg: Any):
    from .approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=DEFAULT_TTL_S)


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True   # fail closed


def _fail(code: str, detail: str, http: int = 400,
          state: str = "failed", **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "state": state, "code": code,
           "detail": _redact(detail)[:400], "http": int(http)}
    out.update(extra)
    return out


def _check_name(name: Any) -> str:
    clean = str(name or "").strip().lower()
    if not _TS_RE.match(clean):
        raise ValueError(
            "toolset name must match ^[a-z0-9][a-z0-9_.:-]{0,63}$")
    return clean


def toolset_states(cfg: Any) -> Dict[str, bool]:
    """Every recorded toolset ``name -> enabled`` (additive, bounded)."""
    out: Dict[str, bool] = {}
    for name, rec in _load(cfg)["toolsets"].items():
        if isinstance(rec, dict):
            out[str(name)] = bool(rec.get("enabled", True))
    return out


def toolset_enabled(cfg: Any, name: str, default: bool = True) -> bool:
    """Persisted enabled flag for one toolset (``default`` when never set)."""
    rec = _load(cfg)["toolsets"].get(str(name or ""))
    if isinstance(rec, dict):
        return bool(rec.get("enabled", default))
    return bool(default)


def platform_toolset_states(cfg: Any) -> Dict[str, bool]:
    """Platform-derived ``name -> enabled`` for the configurable toolset catalog.

    Mirrors exactly what the read path (``qubit_compat.h_toolsets``) serves so
    the mutation authority and the read authority cannot disagree. A toolset
    absent from the returned map is unknown to the catalog (the caller falls
    back to its default). An empty map means the vendored catalog could not be
    resolved -- callers keep their existing default behavior.
    """
    try:
        from . import qubit_runtime

        qubit_runtime.ensure_path()
        try:
            qubit_runtime.use_qubit_home(cfg)
        except Exception:  # noqa: BLE001 - keep the fallback on a resolution miss
            pass
        with qubit_runtime.profile_secret_scope(cfg):
            from qubit_cli.tools_config import (  # type: ignore
                _get_effective_configurable_toolsets,
                _get_platform_tools,
            )
            from qubit_cli.config import load_config as _load_qubit_config  # type: ignore

            platform_on = set(_get_platform_tools(_load_qubit_config(), "cli"))
            return {
                str(name): (str(name) in platform_on)
                for name, _label, _desc in _get_effective_configurable_toolsets()
            }
    except Exception:  # noqa: BLE001 - vendored catalog unavailable
        return {}


def effective_toolset_enabled(cfg: Any, name: str,
                              default: bool = True) -> bool:
    """The state the read path and the mutation authority agree on.

    A persisted dashboard record wins; otherwise the vendored platform-derived
    state; otherwise ``default``. This is the same precedence ``h_toolsets``
    serves (store overrides platform), so enabling a platform-disabled toolset
    observes ``current=False`` and stages/applies the change instead of
    reporting a false ``already`` success while the list still shows disabled.
    """
    rec = _load(cfg)["toolsets"].get(str(name or ""))
    if isinstance(rec, dict):
        return bool(rec.get("enabled", default))
    return platform_toolset_states(cfg).get(str(name), bool(default))


def toolset_rows(cfg: Any, names: List[str]) -> List[Dict[str, Any]]:
    """Render toolset wire rows with the persisted ``enabled`` state.

    The route layer can return these directly in place of the hard-coded
    ``enabled: True`` it served before.
    """
    states = _load(cfg)["toolsets"]
    rows: List[Dict[str, Any]] = []
    for name in names:
        rec = states.get(str(name))
        enabled = bool(rec.get("enabled", True)) if isinstance(rec, dict) \
            else True
        rows.append({
            "name": str(name), "label": str(name),
            "description": "Qubit broker capability",
            "platform": "qubit", "platform_label": "Qubit",
            "enabled": enabled, "configured": True, "tools": [str(name)],
        })
    return rows


def toolset_risk(enabled: bool) -> str:
    """Enabling a toolset grants a capability (elevated); disabling is
    reversible."""
    return RISK_ELEVATED if bool(enabled) else RISK_REVERSIBLE


def apply_toolset_mutation(cfg: Any, name: str, enabled: bool, *,
                           actor: str = "dashboard:user") -> Dict[str, Any]:
    """Persist one already-authorized toolset state (idempotent)."""
    try:
        clean = _check_name(name)
    except ValueError as exc:
        return _fail(MALFORMED_CODE, str(exc), 400)
    state = _load(cfg)
    rec = state["toolsets"].get(clean)
    # Derive ``current`` from the SAME authority the read path serves
    # (persisted record -> platform-derived default): a platform-disabled
    # toolset with no record is NOT already enabled, so enabling it persists.
    current = effective_toolset_enabled(cfg, clean)
    if current == bool(enabled):
        return {"ok": True, "state": "executed", "code": "OK", "name": clean,
                "enabled": current, "already": True, "http": 200}
    if len(state["toolsets"]) >= MAX_TOOLSETS and rec is None:
        return _fail(MALFORMED_CODE, "toolset limit reached", 400)
    state["toolsets"][clean] = {"enabled": bool(enabled),
                                "updated_at": _now(),
                                "updated_by": str(actor)[:64]}
    try:
        _save(cfg, state)
    except OSError as exc:
        return _fail(STAGE_CODE, f"toolset store write failed: {exc}", 500)
    _audit("enable" if enabled else "disable", actor,
           {"name": clean, "enabled": bool(enabled)})
    return {"ok": True, "state": "executed", "code": "OK", "name": clean,
            "enabled": bool(enabled), "http": 200,
            "qubit_counterpart": "CLI `qubit tools` + dashboard Settings"}


def stage_toolset_mutation(cfg: Any, name: str, enabled: bool, *,
                           actor: str = "dashboard:user",
                           session: str = "dashboard",
                           ttl_s: float = DEFAULT_TTL_S) -> Dict[str, Any]:
    """Validate and stage one toolset enable/disable on the shared journal."""
    if not _is_human(actor):
        return _fail(ACTOR_CODE,
                     "toolset changes require a confirmed human actor", 403)
    if _estop(cfg):
        return _fail(ESTOP_CODE,
                     "emergency stop is latched; reset it before changing "
                     "toolsets", 403)
    try:
        clean = _check_name(name)
    except ValueError as exc:
        return _fail(MALFORMED_CODE, str(exc), 400)
    # Same authority as the read path (see ``effective_toolset_enabled``): a
    # platform-disabled toolset with no record must stage a real enable.
    current = effective_toolset_enabled(cfg, clean)
    if current == bool(enabled):
        return {"ok": True, "state": "executed", "code": "OK", "name": clean,
                "enabled": current, "already": True, "http": 200}
    risk = toolset_risk(enabled)
    verb = "enable" if enabled else "disable"
    detail = (f"toolset {verb} {clean!r} is a {risk} capability change and "
              "requires a durable human confirmation")
    try:
        journal = _journal(cfg)
        approval = journal.create(
            TOOLSET_CAPABILITY, detail,
            ttl_s=max(1.0, min(float(ttl_s), MAX_TTL_S)),
            action=verb, name=clean, enabled=bool(enabled), risk=risk,
            actor=str(actor)[:64], session=str(session)[:64])
    except Exception as exc:  # noqa: BLE001 - typed at the boundary
        return _fail(STAGE_CODE,
                     "could not stage confirmation: " + _redact(str(exc))[:160],
                     500)
    return {"ok": False, "state": "needs-confirm", "code": CONFIRM_CODE,
            "confirmation_id": approval.id, "capability": TOOLSET_CAPABILITY,
            "name": clean, "enabled": bool(enabled), "risk": risk,
            "detail": detail, "http": 409, "ttl_s": float(ttl_s),
            "qubit_counterpart": "pending-approvals banner "
                                 "(POST /api/ops/confirm) + CLI `qubit tools`"}


def settle_toolset_mutation(cfg: Any, confirmation_id: str, *,
                            actor: str = "dashboard:user",
                            session: str = "dashboard") -> Dict[str, Any]:
    """Consume one staged toolset confirmation once and persist the change."""
    from .approvals import (ABORTED, COMPLETE, DENIED, EXECUTING, IN_PROGRESS)

    if not _is_human(actor):
        return _fail(ACTOR_CODE,
                     "toolset changes require a confirmed human actor", 403)
    if _estop(cfg):
        return _fail(ESTOP_CODE,
                     "emergency stop is latched; reset it before changing "
                     "toolsets", 403)
    cid = str(confirmation_id or "").strip()
    try:
        journal = _journal(cfg)
        item = journal.get(cid)
    except Exception as exc:  # noqa: BLE001
        return _fail(STAGE_CODE, "confirmation journal unavailable: "
                     + _redact(str(exc))[:120], 500)
    if item is None or item.capability != TOOLSET_CAPABILITY:
        return _fail(UNKNOWN_CODE,
                     "unknown or wrong-capability confirmation id", 409)
    meta = item.meta if isinstance(item.meta, dict) else {}
    if str(meta.get("session", "dashboard") or "dashboard") != str(session):
        return _fail(SESSION_CODE,
                     "that confirmation belongs to another session", 403)
    if item.state in (COMPLETE, ABORTED, DENIED):
        return _fail(REPLAYED_CODE,
                     "that confirmation was already settled, denied, or "
                     "expired", 409)
    if item.decision not in (None, "approve"):
        return _fail(REPLAYED_CODE, "that confirmation was denied", 409)
    if item.state == IN_PROGRESS:
        if item.expires_at <= _now():
            try:
                journal.abort(cid)
            except Exception:
                pass
            return _fail(EXPIRED_CODE, "that confirmation has expired", 409)
        item = journal.respond(cid, "approve")
    if item.state != EXECUTING:
        return _fail(UNKNOWN_CODE, "that confirmation is not executable", 409)
    journal.finish(cid)
    return apply_toolset_mutation(cfg, str(meta.get("name", "")),
                                  bool(meta.get("enabled", True)), actor=actor)


def settle_toolset_mutation_if_owned(cfg: Any, confirmation_id: str, *,
                                     actor: str = "dashboard:user",
                                     session: str = "dashboard"
                                     ) -> Optional[Dict[str, Any]]:
    """Settle an id owned by this module; ``None`` when it belongs elsewhere."""
    try:
        item = _journal(cfg).get(str(confirmation_id or "").strip())
    except Exception:
        return None
    if item is None or item.capability != TOOLSET_CAPABILITY:
        return None
    return settle_toolset_mutation(cfg, confirmation_id, actor=actor,
                                   session=session)


def cancel_toolset_mutations(cfg: Any, confirmation_id: Optional[str] = None,
                             *, actor: str = "dashboard:user",
                             session: str = "dashboard") -> Dict[str, Any]:
    """Deny staged ``toolset.mutation`` confirmations on the shared journal.

    Symmetric to :func:`settle_toolset_mutation`: the dashboard pending-
    approvals banner's Cancel must be able to deny a staged toolset toggle just
    like the dashboard/compat channels, otherwise the entry stays
    ``in_progress`` until its TTL expires. Filters on this module's capability
    and the caller's session; settles exactly once with audit.
    """
    from .approvals import ABORTED, COMPLETE, DENIED

    cid = str(confirmation_id or "").strip()
    try:
        journal = _journal(cfg)
        items = [a for a in journal.pending()
                 if a.capability == TOOLSET_CAPABILITY
                 and str((a.meta or {}).get("session", "dashboard")
                         or "dashboard") == str(session)]
    except Exception as exc:  # noqa: BLE001 - typed at the boundary
        return _fail(STAGE_CODE, "confirmation journal unavailable: "
                     + _redact(str(exc))[:120], 500)
    if cid:
        items = [a for a in items if a.id == cid]
    count = 0
    for item in items:
        if item.state in (COMPLETE, ABORTED, DENIED):
            continue
        try:
            journal.respond(item.id, "deny")
            count += 1
        except Exception:
            pass
    if count:
        _audit("cancel", actor, {"session": session, "count": count})
    if count == 0:
        if cid:
            return _fail(UNKNOWN_CODE,
                         "unknown or wrong-capability confirmation id", 409)
        return {"state": "failed", "code": "NOTHING_PENDING",
                "detail": "There's nothing pending."}
    return {"ok": True, "state": "executed", "code": "OK", "http": 200,
            "cancelled": count,
            "speech": f"Cancelled {count} pending toolset confirmation(s).",
            "receipt": {"cancelled": count}}


def cancel_toolset_mutation_if_owned(cfg: Any, confirmation_id: str, *,
                                     actor: str = "dashboard:user",
                                     session: str = "dashboard"
                                     ) -> Optional[Dict[str, Any]]:
    """Cancel an id owned by this module; ``None`` when it belongs elsewhere."""
    try:
        item = _journal(cfg).get(str(confirmation_id or "").strip())
    except Exception:
        return None
    if item is None or item.capability != TOOLSET_CAPABILITY:
        return None
    return cancel_toolset_mutations(cfg, confirmation_id, actor=actor,
                                    session=session)


def handle_toolset_mutation(cfg: Any, name: str, enabled: bool, *,
                            confirm: bool = False, confirmation_id: str = "",
                            actor: str = "dashboard:user",
                            session: str = "dashboard") -> Dict[str, Any]:
    """Route-friendly entry point: settle with an id, otherwise stage."""
    cid = str(confirmation_id or "").strip()
    if cid:
        return settle_toolset_mutation(cfg, cid, actor=actor, session=session)
    return stage_toolset_mutation(cfg, name, enabled, actor=actor,
                                  session=session)


def toolset_enable(cfg: Any, name: str, **kwargs: Any) -> Dict[str, Any]:
    """Stage enabling ``name`` (``confirmation_id`` settles instead)."""
    return handle_toolset_mutation(cfg, name, True, **kwargs)


def toolset_disable(cfg: Any, name: str, **kwargs: Any) -> Dict[str, Any]:
    """Stage disabling ``name`` (``confirmation_id`` settles instead)."""
    return handle_toolset_mutation(cfg, name, False, **kwargs)


def pending_toolset_mutations(cfg: Any) -> list:
    """Pending toolset mutations on the shared journal."""
    try:
        journal = _journal(cfg)
        return [{"id": a.id, "capability": a.capability,
                 "summary": _redact(a.summary)[:300], "state": a.state,
                 "name": (a.meta or {}).get("name"),
                 "enabled": (a.meta or {}).get("enabled"),
                 "risk": (a.meta or {}).get("risk")}
                for a in journal.pending()
                if a.capability == TOOLSET_CAPABILITY]
    except Exception:
        return []


def toolset_doctor(cfg: Any) -> Dict[str, Any]:
    """Diagnostics: store health and recorded toolset count."""
    try:
        state = _load(cfg)
    except Exception as exc:  # noqa: BLE001
        return {"state": "DEGRADED", "store_ok": False,
                "detail": _redact(str(exc))[:200]}
    return {"state": "OK", "store_ok": True, "schema": TOOLSET_SCHEMA,
            "toolsets": len(state["toolsets"]),
            "path": str(_store_path(cfg))}


__all__ = [
    "TOOLSET_SCHEMA", "TOOLSET_STORE_NAME", "TOOLSET_CAPABILITY",
    "DEFAULT_TTL_S", "CONFIRM_CODE", "MALFORMED_CODE", "ESTOP_CODE",
    "ACTOR_CODE", "REPLAYED_CODE", "EXPIRED_CODE", "UNKNOWN_CODE",
    "SESSION_CODE", "STAGE_CODE", "RISK_REVERSIBLE", "RISK_ELEVATED",
    "toolset_states", "toolset_enabled", "toolset_rows", "toolset_risk",
    "platform_toolset_states", "effective_toolset_enabled",
    "stage_toolset_mutation", "settle_toolset_mutation",
    "settle_toolset_mutation_if_owned", "apply_toolset_mutation",
    "cancel_toolset_mutations", "cancel_toolset_mutation_if_owned",
    "handle_toolset_mutation", "toolset_enable", "toolset_disable",
    "pending_toolset_mutations", "toolset_doctor",
]
