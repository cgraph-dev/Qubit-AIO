"""Best-effort, non-blocking prewarm of the voice-edit local models.

The first voice turn pays the cold local-model load (~17.5 s measured on the
reference host) while the classifier budget is 4 s and the transform budget is
8 s. Those budgets are deliberately short, so a cold turn can fail with
``MODEL_UNAVAILABLE`` even though the model is fine.

This module removes that first-turn penalty without changing any budget and
without blocking the hotkey: :func:`warm_async` starts ONE background thread
that asks the configured ``voice_edit.local_profile`` and
``voice_edit.detail_model`` through the canonical provider gateway. The turn
keeps running on its normal budget; the load simply starts as early as
possible so later turns already find a warm model.

Properties (all enforced here, not by callers):

* **single-flight** — at most one warm thread exists; concurrent calls return
  ``False`` instead of starting a second load;
* **cooldown** — at most one attempt per :data:`WARM_COOLDOWN_S` (300 s), so a
  failing backend cannot be hammered on every hotkey;
* **never raises** — a warm failure is swallowed and recorded in
  :func:`warm_state`;
* **never blocks** — the caller only starts a daemon thread; the model call
  happens on that thread;
* **no new authority** — it uses ``providers.gateway.run_role`` (the same
  route the real classifier/transform use) with a tiny generation request, so
  a local profile performs no network request.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

#: The warm request is a single token: it only exists to make the model load.
WARM_MAX_TOKENS = 1

#: Bounded background timeout. Long enough for a cold local load (~17.5 s
#: measured) but still finite, and it never runs on the hotkey thread.
WARM_TIMEOUT_S = 30.0

#: At most one warm attempt per five minutes.
WARM_COOLDOWN_S = 300.0

_WARM_SYSTEM = "Warm-up request. Reply with one word."

_STATE_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_WARMING = False
_STATUS = "idle"            # idle | warming | warm | failed
_LAST_ATTEMPT = 0.0         # time.monotonic() of the last dispatch
_LAST_FINISHED = 0.0
_PROFILES: List[str] = []
_ERRORS: List[str] = []


def _read_cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _configured_profiles(cfg: Any) -> List[str]:
    """Profiles to warm: the local tier always, the detail tier when distinct."""
    try:
        local = str(_read_cfg(cfg, "voice_edit", "local_profile",
                             default="qubit-brain") or "qubit-brain")
    except Exception:
        local = "qubit-brain"
    try:
        detail = str(_read_cfg(cfg, "voice_edit", "detail_model",
                              default="") or "")
    except Exception:
        detail = ""
    out: List[str] = []
    for profile in (local, detail):
        name = profile.strip()
        if name and name not in out:
            out.append(name)
    return out


def _configured_model(cfg: Any) -> str:
    """Best-effort truthful model identity for the trace (never raises)."""
    try:
        from ..providers import resolver
        return str(resolver.resolve(cfg, role=resolver.DEFAULT_ROLE).model)
    except Exception:
        return ""


def _run(cfg: Any, profiles: List[str]) -> None:
    """Warm each profile sequentially on the background thread."""
    global _WARMING, _STATUS, _LAST_FINISHED, _ERRORS
    errors: List[str] = []
    warmed = 0
    model = _configured_model(cfg)
    try:
        from ..providers import gateway as GW
    except Exception:
        GW = None  # type: ignore[assignment]
    for profile in profiles:
        if GW is None:
            errors.append("gateway_import_failed")
            break
        try:
            res = GW.run_role(
                "warm", cfg, role="chat", profile_id=profile,
                max_tokens=WARM_MAX_TOKENS, timeout_s=WARM_TIMEOUT_S,
                system_prompt=_WARM_SYSTEM, local_mode="chat",
                reasoning="low")
            if getattr(res, "ok", False):
                warmed += 1
            else:
                errors.append(
                    f"{profile}:{str(getattr(res, 'code', '') or 'failed')}")
        except Exception as exc:  # noqa: BLE001 - warming must never break a turn
            errors.append(f"{profile}:{type(exc).__name__}")
    try:
        from ..logging_setup import log_event
        log_event("voice_edit.warm.done", profiles=len(profiles),
                  warmed=warmed, errors=len(errors), model=model,
                  timeout_s=WARM_TIMEOUT_S)
    except Exception:
        pass
    with _STATE_LOCK:
        _ERRORS = errors
        _WARMING = False
        _STATUS = "warm" if warmed and not errors else "failed"
        _LAST_FINISHED = time.monotonic()


def warm_async(cfg: Any) -> bool:
    """Start the one background warm load when it is due.

    Returns ``True`` only when this call dispatched a warm; ``False`` when a
    warm is already running, the cooldown is active, there is nothing to warm,
    or anything went wrong. Never blocks and never raises.
    """
    global _THREAD, _WARMING, _STATUS, _LAST_ATTEMPT, _PROFILES, _ERRORS
    try:
        with _STATE_LOCK:
            if _WARMING:
                return False
            thread = _THREAD
            if thread is not None and thread.is_alive():
                return False
            now = time.monotonic()
            if _LAST_ATTEMPT and (now - _LAST_ATTEMPT) < WARM_COOLDOWN_S:
                return False
            profiles = _configured_profiles(cfg)
            if not profiles:
                return False
            _WARMING = True
            _STATUS = "warming"
            _LAST_ATTEMPT = now
            _PROFILES = profiles
            _ERRORS = []
            thread = threading.Thread(target=_run, args=(cfg, profiles),
                                      daemon=True, name="voice-edit-warm")
            _THREAD = thread
        thread.start()
        return True
    except Exception:  # noqa: BLE001 - warming must never break a turn
        try:
            with _STATE_LOCK:
                _WARMING = False
                _STATUS = "failed"
        except Exception:
            pass
        return False


def warm_state() -> Dict[str, Any]:
    """Snapshot for tests/diagnostics (copy; safe to call from any thread)."""
    with _STATE_LOCK:
        return {
            "state": _STATUS,
            "warming": bool(_WARMING),
            "profiles": list(_PROFILES),
            "errors": list(_ERRORS),
            "last_attempt": _LAST_ATTEMPT,
            "last_finished": _LAST_FINISHED,
            "cooldown_s": WARM_COOLDOWN_S,
        }


def reset_state() -> None:
    """Drop the warm state (test seam; never called by production code)."""
    global _THREAD, _WARMING, _STATUS, _LAST_ATTEMPT, _LAST_FINISHED
    global _PROFILES, _ERRORS
    with _STATE_LOCK:
        _THREAD = None
        _WARMING = False
        _STATUS = "idle"
        _LAST_ATTEMPT = 0.0
        _LAST_FINISHED = 0.0
        _PROFILES = []
        _ERRORS = []


__all__ = [
    "WARM_COOLDOWN_S",
    "WARM_TIMEOUT_S",
    "warm_async",
    "warm_state",
    "reset_state",
]
