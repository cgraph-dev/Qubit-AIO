"""Custom-endpoint activation and catalog model selection.

ONE authority: the resolver's model-profile registry
(:mod:`voicebridge.providers.resolver`, backed by ``<state_dir>/profiles.json``).
This module turns a staged custom-endpoint record *or* a provider/model pick
into a real, executable :class:`~voicebridge.providers.resolver.ModelProfile`
and makes it the active selection.

It is the Qubit translation of the reference's ``activate_custom_endpoint`` /
``set_model_assignment`` (``hermes_cli/web_routers/config_env.py:681-731`` and
``models.py:275``), which set ``cfg["model"]`` and save ``config.yaml``. Qubit
resolves the effective model through the resolver, so applying a selection
means four things, all reversible:

1. register the profile (``upsert_model_profile``);
2. allow *this* profile through the per-profile override the gateway already
   honours (``intent.frontier.profile_overrides`` — an explicit, confirm-gated
   operator action, never a global policy widening);
3. persist it as the active selection (``set_active_profile``);
4. best-effort mirror the runtime ``config.yaml`` ``model`` slot the vendored
   TUI reads, matching the reference's ``cfg["model"]`` write.

Credentials remain references (``env:``/``file:``) only — a raw key is never
accepted here. The previous hand-rolled ``_active`` JSON flag was a fabricated
state no runtime consumer read; this replaces it with the real apply.
"""
from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional

_logger = logging.getLogger("qubit.providers.endpoints")

from . import resolver as R
from .resolver import API, LOCAL, PLUGIN, ModelProfile

_ENDPOINT_ID_RE = re.compile(r"[^a-z0-9_.-]+")
_LOCAL_HOST_MARKERS = ("127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]")


def slug_endpoint_id(raw: str, fallback: str = "custom") -> str:
    """Normalize an endpoint id the same way the compat handler does."""
    text = str(raw or "").strip().lower()
    return _ENDPOINT_ID_RE.sub("-", text).strip("-")[:64] or fallback


def _text(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def endpoint_base_url(record: Dict[str, Any]) -> str:
    return _text(record, "base_url", "url", "api")


def endpoint_model(record: Dict[str, Any]) -> str:
    model = _text(record, "model", "default_model")
    if model:
        return model
    models = record.get("models")
    if isinstance(models, list):
        for item in models:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def endpoint_key_ref(record: Dict[str, Any]) -> str:
    """The credential *reference* for the endpoint (never a raw key value)."""
    return _text(record, "key_ref", "key_env", "api_key_ref")


def _is_local_url(url: str) -> bool:
    low = str(url or "").strip().lower()
    if not low:
        return False
    if "://" in low:
        host = low.split("://", 1)[1].split("/", 1)[0]
    else:
        host = low.split("/", 1)[0]
    host = host.split("@")[-1].split(":", 1)[0].strip("[]")
    return host in _LOCAL_HOST_MARKERS or host.startswith("127.")


def build_endpoint_profile(ident: str, record: Dict[str, Any]) -> ModelProfile:
    """Build a resolver profile from a staged endpoint record.

    A local endpoint is ``kind=local`` (no egress); a remote endpoint is
    ``kind=api`` when it carries a credential reference, else ``kind=plugin``
    (config-only/inert until a credential resolves) — both external, so the
    per-profile allowlist override is what makes activation executable.
    """
    provider = _text(record, "provider") or ident
    base_url = endpoint_base_url(record)
    model = endpoint_model(record) or ident
    key_ref = endpoint_key_ref(record)
    local = _is_local_url(base_url)
    if local:
        kind, locality, egress = LOCAL, "local", "deny"
    else:
        kind = API if key_ref else PLUGIN
        locality, egress = "external", "allow"
    context = record.get("context_length") or record.get("context_tokens") or 0
    try:
        context_tokens = int(context)
    except (TypeError, ValueError):
        context_tokens = 0
    return ModelProfile(
        id=ident, kind=kind, provider=provider, model=model,
        base_url=base_url, key_ref=key_ref,
        context_tokens=context_tokens if context_tokens > 0 else 4096,
        supports_tools=bool(record.get("supports_tools", False)),
        supports_streaming=local,
        locality=locality, egress_consent=egress,
        roles=["chat"], enabled=True, rollback_to=ident,
        provenance="qubit://settings/custom-endpoints",
    )


def resolve_real_context_tokens(model: str, base_url: str = "",
                                provider: str = "") -> Optional[int]:
    """Best-effort real context window for *model*; ``None`` when unknown.

    Thin wrapper over the bounded vendored probe
    (:func:`resolver._probe_real_context_window`, daemon thread, hard timeout):
    a genuine resolution (never the generic fallback — the probe filters it)
    is returned, anything else (offline, unknown model, timeout) is ``None``.
    Never raises, never writes. Lets activation record the window the runtime
    will actually use instead of the 4096 creation default.
    """
    try:
        from .. import qubit_runtime as _QR

        # The vendored probe imports ``agent.model_metadata``: without the
        # runtime on sys.path it degrades to resolver-unavailable (the same
        # binding ``_real_context_window`` applies before probing).
        _QR.ensure_path()
    except Exception:  # noqa: BLE001 - probe still runs, may just miss
        pass
    try:
        value, _source = R._probe_real_context_window(
            str(model or ""), str(base_url or ""), str(provider or ""))
    except Exception:  # noqa: BLE001 - probe is best-effort by contract
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def backfill_profile_context(profile: ModelProfile) -> bool:
    """Fill a defaulted ``context_tokens`` from the real window; ``True`` if set.

    Only fires when the profile still carries the ``4096`` creation default —
    an explicitly recorded window (larger OR smaller) is never overwritten
    here (stale explicit windows are corrected by
    :func:`correct_stale_profile_context`, never silently). A failed/unknown
    probe keeps the default (today's behavior). A successful fill stamps the
    probe (time/value/source) so a later repair can tell "stale" from
    "deliberate".
    """
    if int(getattr(profile, "context_tokens", 0) or 0) != 4096:
        return False
    real = resolve_real_context_tokens(
        profile.model, profile.base_url, profile.provider)
    if real is None:
        return False
    profile.context_tokens = real
    try:
        profile.context_probed_at = time.time()
    except Exception:  # noqa: BLE001 - stamp is audit-only, never fatal
        pass
    try:
        profile.context_probed_value = int(real)
    except Exception:  # noqa: BLE001 - stamp is audit-only, never fatal
        pass
    try:
        profile.context_probe_source = "probe:vendored-resolver"
    except Exception:  # noqa: BLE001 - stamp is audit-only, never fatal
        pass
    return True


def mark_profile_context_explicit(profile: ModelProfile, *,
                                  actor: str = "operator") -> bool:
    """Stamp the profile's window as an explicit user-set value.

    Called when a window is staged deliberately at activation (a
    user-typed ``context_length`` on a custom endpoint): a later
    :func:`correct_stale_profile_context` refuses to auto-move it (the
    explicit choice wins) unless forced. Never probes, never changes the
    window itself. ``True`` when stamped.
    """
    try:
        current = int(getattr(profile, "context_tokens", 0) or 0)
    except (TypeError, ValueError):
        return False
    if current <= 0:
        return False
    try:
        profile.context_probed_at = time.time()
        profile.context_probed_value = current
        profile.context_probe_source = (
            f"explicit:{str(actor or 'operator')[:64]}")
    except Exception:  # noqa: BLE001 - stamp is audit-only, never fatal
        return False
    return True


def correct_stale_profile_context(profile: ModelProfile, *,
                                  probed: Optional[int] = None,
                                  force: bool = False) -> bool:
    """Correct a stale declared window upward to a fresh probe; in-memory only.

    Bounded repair for the 2026-10-03 stale-window defect (declared 200k vs
    probed 1M on ``deepseek-v4-flash``): when a fresh genuine probe resolves
    ABOVE the declared window, the declaration moves up to the probed value
    and the probe is stamped (time/value/source). Otherwise untouched:

    * probe unknown/``None`` -> ``False`` (fail-open, today's behavior);
    * declared >= probe -> ``False`` (NEVER a downgrade; equal is fresh);
    * an explicit user-set window (``explicit:`` stamp, or the window changed
      since the probe stamp) wins -> ``False``, unless ``force=True``.

    Never persists (the caller owns the sanctioned writer) and never raises
    on a malformed profile (``False``). ``probed`` injects the fresh value
    for hermetic callers; ``None`` runs the live bounded probe.
    """
    try:
        declared = int(getattr(profile, "context_tokens", 0) or 0)
    except (TypeError, ValueError):
        return False
    fresh = probed
    if fresh is None:
        try:
            fresh = resolve_real_context_tokens(
                str(getattr(profile, "model", "") or ""),
                str(getattr(profile, "base_url", "") or ""),
                str(getattr(profile, "provider", "") or ""))
        except Exception:  # noqa: BLE001 - probe failure is a no-op
            return False
    if not isinstance(fresh, int) or fresh <= 0:
        return False
    if declared >= fresh:
        return False
    if not force:
        src = str(getattr(profile, "context_probe_source", "") or "")
        if src.startswith("explicit:"):
            return False
        try:
            stamped_at = float(
                getattr(profile, "context_probed_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            stamped_at = 0.0
        if stamped_at > 0:
            try:
                stamped_val = int(
                    getattr(profile, "context_probed_value", 0) or 0)
            except (TypeError, ValueError):
                stamped_val = 0
            if declared != stamped_val:
                # The window moved after the probe stamp: an operator
                # narrowed/widened it deliberately after seeing a probe.
                return False
    try:
        profile.context_tokens = int(fresh)
        profile.context_probed_at = time.time()
        profile.context_probed_value = int(fresh)
        profile.context_probe_source = "probe:vendored-resolver"
    except Exception:  # noqa: BLE001 - a half-written stamp is not a repair
        return False
    return True


def repair_stale_profile_context(cfg: Any, profile_id: str, *,
                                 actor: str = "dashboard:user",
                                 force: bool = False,
                                 probed: Optional[int] = None
                                 ) -> Dict[str, Any]:
    """Sanctioned one-time correction of a stale declared window.

    Loads ``profile_id`` from the canonical registry, runs
    :func:`correct_stale_profile_context` (upward-only, explicit-wins), and
    persists through ``upsert_model_profile(confirm=True)`` — never a raw
    registry edit — with a journal audit naming who/when/before/after/source.
    A no-change outcome is ``ok:True, repaired:False`` with an honest
    ``reason`` (``fresh`` | ``explicit-wins`` | ``probe-unresolved``), never
    a failure. Unknown profiles are a typed refusal.
    """
    pid = str(profile_id or "").strip()
    if not pid:
        return {"ok": False, "code": "MALFORMED",
                "detail": "profile id required"}
    profile = R.get_profile(cfg, pid)
    if profile is None:
        return {"ok": False, "code": "UNKNOWN_PROFILE",
                "detail": f"no model profile {pid!r}"}
    try:
        before = int(getattr(profile, "context_tokens", 0) or 0)
    except (TypeError, ValueError):
        before = 0
    fresh = probed
    if fresh is None:
        try:
            fresh = resolve_real_context_tokens(
                str(getattr(profile, "model", "") or ""),
                str(getattr(profile, "base_url", "") or ""),
                str(getattr(profile, "provider", "") or ""))
        except Exception:  # noqa: BLE001 - probe failure degrades to no-op
            fresh = None
    try:
        changed = correct_stale_profile_context(
            profile, probed=fresh, force=force)
    except Exception as exc:  # noqa: BLE001 - typed, never raised
        return {"ok": False, "code": "CORRECT_FAILED",
                "detail": f"stale-window correction failed: "
                          f"{type(exc).__name__}",
                "profile_id": pid, "before": before}
    if not changed:
        if not isinstance(fresh, int) or fresh <= 0:
            reason = "probe-unresolved"
        elif before >= fresh:
            reason = "fresh"
        else:
            reason = "explicit-wins"
        out: Dict[str, Any] = {
            "ok": True, "repaired": False, "reason": reason,
            "profile_id": pid, "before": before,
            "probe": fresh if isinstance(fresh, int) else None}
        if reason == "explicit-wins" and not force:
            out["detail"] = (
                f"profile {pid!r} declares {before} but the window was "
                f"explicitly set after the last probe; re-run with "
                f"force=True to override it")
        return out
    saved = R.upsert_model_profile(cfg, profile, confirm=True)
    if saved.get("state") != "OK":
        return {"ok": False, "code": saved.get("code", "PROFILE_WRITE_FAILED"),
                "detail": saved.get("detail", "could not persist correction"),
                "profile_id": pid, "before": before}
    after = int(getattr(profile, "context_tokens", 0) or before)
    try:
        from ..logging_setup import log_event as _log

        _log("model.context_repaired", actor=str(actor or ""),
             profile_id=pid,
             provider=str(getattr(profile, "provider", "") or ""),
             model=str(getattr(profile, "model", "") or ""),
             before=before, after=after,
             source=str(getattr(profile, "context_probe_source", "") or ""),
             forced=bool(force))
    except Exception:  # noqa: BLE001 - audit never fails a persisted repair
        pass
    return {"ok": True, "repaired": True, "profile_id": pid,
            "provider": str(getattr(profile, "provider", "") or ""),
            "model": str(getattr(profile, "model", "") or ""),
            "before": before, "after": after, "forced": bool(force),
            "probe_source": str(
                getattr(profile, "context_probe_source", "") or "")}


def _current_slot_summary(cfg: Any) -> str:
    """Human-readable current main-slot selection (never raises, never secrets).

    Used to name the still-working slot in a ``MODEL_UNAVAILABLE`` refusal so
    the operator knows what is still serving default turns.
    """
    try:
        active = R.get_active_profile(cfg)
        pid = str(active.get("active") or "")
        if pid:
            prof = R.get_profile(cfg, pid)
            if prof is not None:
                return f"{prof.provider}/{prof.model} (profile {pid!r})"
            return f"profile {pid!r}"
    except Exception:  # noqa: BLE001 - summary is best-effort
        pass
    try:
        configured = R.configured_default_model(cfg)
        if configured:
            return str(configured)
    except Exception:  # noqa: BLE001 - summary is best-effort
        pass
    return "the shipped local default"


def _refuse_unavailable(cfg: Any, provider: str, model: str,
                        reason: str) -> Dict[str, Any]:
    """Typed fail-closed refusal for a dead/unreachable remote model."""
    slot = _current_slot_summary(cfg)
    return {
        "ok": False, "code": "MODEL_UNAVAILABLE",
        "detail": (
            f"model {provider!r}/{model!r} was recently reported "
            f"unavailable ({reason}); the pick is refused so the main "
            f"slot stays on {slot}. Retry the pick when the model is "
            f"reachable again, or pick a working model."),
        "provider": provider, "model": model,
    }


def _warn_over_dead_record(provider: str, model: str,
                            reason: str) -> Dict[str, Any]:
    """Proceed-warning for an EXPLICIT pick of a recorded-unavailable model.

    Plain language only: this text can reach the picker/confirm surfaces,
    so it never carries internal record names.
    """
    return {
        "ok": True, "proceed_over_dead_record": True,
        "warning": (
            f"model {provider!r}/{model!r} was recently reported "
            f"unavailable ({reason}); proceeding on your explicit pick — "
            f"if turns fail, pick a working model."),
        "provider": provider, "model": model,
    }


def _gate_remote_liveness(*, cfg: Any, provider: str, model: str,
                           base_url: str,
                           explicit: bool = False) -> Optional[Dict[str, Any]]:
    """Fail-closed liveness gate for REMOTE model activation.

    Runs the existing bounded context probe
    (:func:`resolver._probe_real_context_window` — daemon thread, hard
    timeout; reused, never reimplemented). Explicit negative evidence fails
    closed with ``MODEL_UNAVAILABLE`` *before anything is persisted* (the
    caller returns the refusal as-is, so no profile is registered, the
    active pointer never moves, and the runtime ``config.yaml`` slot is
    byte-unchanged):

    * ``probe-timeout`` — the endpoint black-holed the probe;
    * ``probe-failed:*`` — the vendored layer raised during resolution
      (``ModelProtocolUnsupported`` / unavailable / transport errors surface
      here);
    * the probe itself raising (same fail-closed treatment).

    Recorded-unavailable models (a previous turn failed at use time while
    the static probe still resolved) split by caller intent: an EXPLICIT
    user pick (``select_model``/``activate_endpoint`` from the picker,
    CLI, or voice confirm) PROCEEDS with a plain-language warning audit
    (one path's turn failure is not model death while the owner watches
    it stream elsewhere); every SILENT path (``explicit=False``: defaults,
    follow-model, voice escalation auto-picks) keeps the
    ``MODEL_UNAVAILABLE`` refusal with the slot unchanged.

    Anything else is NOT negative evidence and proceeds as before: a
    resolved window, or an inconclusive result (``resolver-unavailable`` /
    ``unresolved`` / ``fallback`` — hermetic/offline/unknown) keeps today's
    accept behavior. Local-tier picks never reach this gate (the caller
    only gates non-``LOCAL`` profiles, which need no credentials to probe).
    """
    try:
        from . import dead_models as _DM

        rec = _DM.is_dead(cfg, provider, model)
    except Exception:
        rec = None
    if rec is not None:
        reason = str(rec.get("reason", "unavailable") or "unavailable")[:200]
        if explicit:
            try:
                from ..logging_setup import log_event as _log

                _log("model.dead_record_warning", provider=provider,
                     model=model, reason=reason)
            except Exception:  # noqa: BLE001 - warning audit never blocks
                pass
            return _warn_over_dead_record(provider, model, reason)
        return _refuse_unavailable(cfg, provider, model, reason)
    try:
        value, source = R._probe_real_context_window(
            str(model or ""), str(base_url or ""), str(provider or ""))
    except Exception as exc:  # noqa: BLE001 - probe error fails closed
        return _refuse_unavailable(
            cfg, provider, model, f"probe-error:{type(exc).__name__}")
    if isinstance(value, int) and value > 0:
        return None
    reason = str(source or "")
    if reason == "probe-timeout" or reason.startswith("probe-failed:"):
        return _refuse_unavailable(cfg, provider, model, reason)
    return None


def _voice_config_path(cfg: Any) -> Path:
    from .. import config as C

    return Path(getattr(cfg, "path", None) or C.resolve_config_path())


def _allow_profile(cfg: Any, profile_id: str) -> bool:
    """Allow *profile_id* through the per-profile override the gateway honours.

    Mutates the in-memory config (so the running process resolves it
    immediately) AND persists the override to the canonical voice config, so a
    restart keeps the explicit activation. Best-effort persistence: a write
    failure leaves the in-memory activation in force for this process.
    """
    data = getattr(cfg, "data", None)
    if isinstance(data, dict):
        frontier = data.setdefault("intent", {}).setdefault("frontier", {})
        overrides = frontier.setdefault("profile_overrides", {})
        entry = overrides.get(profile_id)
        if not isinstance(entry, dict):
            entry = {}
        entry["policy_enabled"] = True
        entry["allowlisted"] = True
        overrides[profile_id] = entry
    try:
        import yaml

        path = _voice_config_path(cfg)
        raw: Dict[str, Any] = {}
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        frontier = raw.setdefault("intent", {}).setdefault("frontier", {})
        overrides = frontier.setdefault("profile_overrides", {})
        entry = overrides.get(profile_id)
        if not isinstance(entry, dict):
            entry = {}
        entry["policy_enabled"] = True
        entry["allowlisted"] = True
        overrides[profile_id] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            bak = path.with_name(
                f"{path.name}.endpoint-activate.{int(time.time())}.bak")
            try:
                bak.write_bytes(path.read_bytes())
            except OSError:
                pass
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001 - in-memory activation still applies
        return False


def _persist_runtime_model(cfg: Any, *, provider: str, model: str,
                           base_url: str, key_ref: str) -> bool:
    """Mirror the selection into the Qubit runtime ``config.yaml`` ``model``.

    The reference writes ``cfg["model"]`` and saves ``config.yaml``; Qubit's
    vendored TUI / ``configured_default_model`` read the same slot. Only a
    credential *reference* is ever written (``key_env``), never a raw key.

    Single-key rule: the node carries ``default`` ONLY (plus ``provider`` /
    ``base_url`` / ``key_env``). Every reader family resolves ``default``
    (the resolver prefers it, the vendored picker context reads nothing
    else, the profile registry owns routing) — while a second ``model`` key
    rots: the vendored save normalizer pops ``model`` whenever ``default``
    exists, and any later single-key ``model`` write resurrects a split
    slot (``model: spark17`` + ``default: muse``) that shows two CURRENT
    providers in the picker. Rebuilt from scratch on every write, so a
    legacy ``model``/``name`` key can never survive.
    """
    try:
        from .. import qubit_runtime

        home = Path(qubit_runtime.runtime_home_for_cfg(cfg))
        home.mkdir(parents=True, exist_ok=True)
        path = home / "config.yaml"
        import yaml

        data: Dict[str, Any] = {}
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        node: Dict[str, Any] = {"provider": provider, "default": model}
        if base_url:
            node["base_url"] = base_url
        if key_ref:
            node["key_env"] = key_ref
        data["model"] = node
        # Tripwire: every slot write names its author. A repeat of the
        # split-slot corruption names the rogue writer in the journal.
        try:
            import traceback as _tb
            _frames = [f.name for f in _tb.extract_stack()[-6:-1]]
        except Exception:
            _frames = []
        _logger.info("runtime model slot <- %s/%s via %s",
                     provider, model, ">".join(_frames))
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001 - profile registry is the authority
        return False


def activate_endpoint(cfg: Any, ident: str, record: Dict[str, Any], *,
                      confirm: bool = True,
                      explicit_window: bool = False) -> Dict[str, Any]:
    """Apply a staged custom endpoint as the effective runtime model.

    Returns a typed dict. ``confirm`` mirrors the caller's confirm gate so an
    unconfirmed call is a dry run (never a silent apply). ``explicit_window``
    marks a caller-staged window as a deliberate user choice (stamped
    ``explicit:`` so a later stale-window repair never silently moves it);
    a carried preservation window (``select_model`` reuse) leaves it
    ``False`` so drift stays repairable.
    """
    ident = slug_endpoint_id(ident or _text(record, "id", "name"))
    if not ident:
        return {"ok": False, "code": "MALFORMED",
                "detail": "endpoint id required"}
    profile = build_endpoint_profile(ident, record)
    # A record without an explicit window gets the 4096 creation default;
    # replace it with the real window when the vendored metadata resolves one
    # (otherwise /chat's context floor refuses a capable model on stale data).
    if explicit_window and int(getattr(profile, "context_tokens", 0)
                               or 0) != 4096:
        try:
            mark_profile_context_explicit(profile, actor="activate-endpoint")
        except Exception:  # noqa: BLE001 - stamp never blocks activation
            pass
    else:
        backfill_profile_context(profile)
    gate = profile.validate(cfg)
    if gate.get("state") != "OK":
        return {"ok": False, "code": gate.get("code", "PROFILE_INVALID"),
                "detail": gate.get("detail", "endpoint is incomplete")}
    if profile.kind != R.LOCAL:
        # An explicit user pick proceeds over a recorded-unavailable model
        # with a warning (one path's turn failure is not model death);
        # silent paths never reach this explicit surface — they keep the
        # refusal via the gate's explicit=False default. Local-tier picks
        # skip the probe (no endpoint/credential involved).
        live = _gate_remote_liveness(
            cfg=cfg, provider=profile.provider, model=profile.model,
            base_url=profile.base_url, explicit=True)
        if live is not None and live.get("proceed_over_dead_record"):
            dead_warning = str(live.get("warning") or "")
            live = None
        else:
            dead_warning = ""
        if live is not None:
            return live
    else:
        dead_warning = ""
    if not confirm:
        out_dry: Dict[str, Any] = {
            "ok": True, "dry_run": True, "profile_id": ident,
            "provider": profile.provider, "model": profile.model}
        if dead_warning:
            out_dry["warning"] = dead_warning
        return out_dry
    out = R.upsert_model_profile(cfg, profile, confirm=True)
    if out.get("state") != "OK":
        return {"ok": False, "code": out.get("code", "PROFILE_WRITE_FAILED"),
                "detail": out.get("detail", "could not register endpoint")}
    persisted = _allow_profile(cfg, ident)
    active = R.set_active_profile(cfg, ident, confirm=True)
    if active.get("state") != "OK":
        return {"ok": False, "code": active.get("code", "ACTIVATE_FAILED"),
                "detail": active.get("detail", "could not activate endpoint")}
    home_applied = _persist_runtime_model(
        cfg, provider=profile.provider, model=profile.model,
        base_url=profile.base_url, key_ref=profile.key_ref)
    applied: Dict[str, Any] = {
        "ok": True, "active": ident, "profile_id": ident,
        "provider": profile.provider, "model": profile.model,
        "kind": profile.kind, "allow_persisted": persisted,
        "home_applied": home_applied,
        "rollback": "select another profile or remove "
                    f"{ident!r} from the model-profile registry"}
    if dead_warning:
        applied["warning"] = dead_warning
    return applied


def _slug_model_id(provider: str, model: str) -> str:
    return slug_endpoint_id(f"{provider}-{model}", fallback="model")


def _runtime_env_value(cfg: Any, name: str) -> str:
    """Read a NON-SECRET value (a base URL) from the process env or runtime .env.

    Only ``*_BASE_URL`` values are read here; secret values are never read
    (credentials stay references). Used to resolve a provider's endpoint the
    same way the vendored runtime does (``<PROVIDER>_BASE_URL``).
    """
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        from .. import config as _config
        text = Path(_config.canonical_runtime_env_path()).read_text(errors="replace")
    except Exception:  # noqa: BLE001 - missing/unreadable .env -> unresolved
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            return v.strip().strip('"').strip("'")
    return ""


def _provider_overlay(provider: str) -> Any:
    """The vendored provider deployment overlay (base-url env/override), or None."""
    try:
        from .. import qubit_runtime as _qr
        _qr.ensure_path()
        from qubit_cli import providers as _P  # type: ignore
        return _P.HERMES_OVERLAYS.get(str(provider or "").strip())
    except Exception:  # noqa: BLE001 - fork unavailable -> caller falls back
        return None


def _provider_base_url(cfg: Any, provider: str) -> str:
    """Resolve a known provider's endpoint exactly as the vendored runtime does.

    ``HERMES_OVERLAYS[provider].base_url_env_var`` (e.g. ``OPENCODE_GO_BASE_URL``)
    first, then a non-virtual ``base_url_override``, then the generic
    ``<SLUG>_BASE_URL`` convention. Empty when nothing resolves.
    """
    ov = _provider_overlay(provider)
    if ov is not None:
        env_name = str(getattr(ov, "base_url_env_var", "") or "")
        if env_name:
            v = _runtime_env_value(cfg, env_name)
            if v:
                return v
        override = str(getattr(ov, "base_url_override", "") or "")
        if override and "://" in override and not override.startswith(
                ("moa://", "acp://")):
            return override
    slug_env = str(provider or "").strip().upper().replace("-", "_") + "_BASE_URL"
    return _runtime_env_value(cfg, slug_env)


def _provider_key_ref(cfg: Any, provider: str) -> str:
    """Resolve a provider's credential reference from its env var NAME(s).

    Returns ``env:<NAME>`` when a candidate key var is set (name-only check;
    the secret VALUE is never read), else ``""``. Candidates are the overlay's
    ``extra_env_vars`` plus the ``<SLUG>_API_KEY`` convention.
    """
    ov = _provider_overlay(provider)
    extra = tuple(getattr(ov, "extra_env_vars", ()) or ()) if ov is not None else ()
    slug = str(provider or "").strip().upper().replace("-", "_")
    candidates = list(extra) + [f"{slug}_API_KEY"]
    try:
        from .. import env_store
        present = env_store.present_names(cfg)
    except Exception:  # noqa: BLE001 - no store -> no resolvable key
        return ""
    for name in candidates:
        if name and name in present:
            return f"env:{name}"
    return ""


def select_model(cfg: Any, provider: str, model: str, *,
                 base_url: str = "", key_ref: str = "",
                 confirm: bool = True) -> Dict[str, Any]:
    """Find-or-create the profile for ``provider``/``model`` and activate it.

    The Qubit counterpart to the reference ``POST /api/model/set`` main-slot
    write. An already-registered profile with the same provider+model is
    reused (its endpoint/credential are preserved); otherwise one is created.
    A remote provider with no endpoint/credential is an honest typed refusal,
    never a fabricated success. A remote pick whose bounded liveness probe
    reports the model unavailable (``MODEL_UNAVAILABLE``) is likewise refused
    with the slot unchanged; local-tier picks skip the probe. An explicit
    pick of a model with a recorded turn-time failure proceeds with a
    plain-language ``warning`` (one path's failure is not model death);
    silent paths keep the refusal via the gate default.
    """
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    if not model:
        return {"ok": False, "code": "MALFORMED",
                "detail": "model assignment needs a model name"}
    if not provider:
        return {"ok": False, "code": "MALFORMED",
                "detail": "model assignment needs a provider"}
    pid = ""
    reuse_context = 0
    reuse_profile = None
    for existing in R.load_profiles(cfg):
        if existing.default:
            # The shipped default is still a valid local pick (switching back
            # to the brain from the card must work); it just never counts as
            # a regular endpoint reuse below.
            if (existing.kind == R.LOCAL
                    and existing.model == model
                    and existing.provider == provider):
                reuse_profile = existing
                pid = existing.id
                break
            continue
        if existing.model == model and existing.provider == provider:
            pid = existing.id
            reuse_profile = existing
            base_url = base_url or existing.base_url
            key_ref = key_ref or existing.key_ref
            # A re-pick rebuilds the profile from this record: carry the
            # recorded window so re-selecting never clobbers a known-good
            # value (e.g. 200k) back to the 4096 creation default.
            try:
                reuse_context = int(existing.context_tokens or 0)
            except (TypeError, ValueError):
                reuse_context = 0
            break
    if reuse_profile is not None and reuse_profile.kind == R.LOCAL:
        # A registered local model (ollama) needs no endpoint or credential:
        # activate it directly. (The endpoint resolution below is for remote
        # API models only; requiring a base_url here refused every local
        # pick, so /qubit-chat could never switch to the Spark tier.)
        if not confirm:
            return {"ok": True, "dry_run": True, "profile_id": pid,
                    "provider": reuse_profile.provider,
                    "model": reuse_profile.model}
        try:
            active = R.set_active_profile(cfg, pid, confirm=True)
        except Exception as exc:  # noqa: BLE001 - typed failure
            return {"ok": False, "code": getattr(exc, "code", "ACTIVATE_FAILED"),
                    "detail": str(exc)[:300]}
        if active.get("state") != "OK":
            return {"ok": False, "code": active.get("code", "ACTIVATE_FAILED"),
                    "detail": active.get("detail", "could not activate profile")}
        # Mirror the runtime model slot like the remote path does, so the
        # vendored picker context and runtime readers agree on the local
        # selection (otherwise they keep reporting the previous API model).
        _persist_runtime_model(cfg, provider=reuse_profile.provider,
                               model=reuse_profile.model,
                               base_url="", key_ref="")
        return {"ok": True, "active": pid, "profile_id": pid,
                "provider": reuse_profile.provider,
                "model": reuse_profile.model, "kind": R.LOCAL,
                "rollback": f"select another profile"}
    if not pid:
        pid = _slug_model_id(provider, model)
    local = _is_local_url(base_url)
    if not local and not base_url:
        # Resolve a known provider's endpoint the way the vendored runtime does
        # (overlay base_url_env_var / "<SLUG>_BASE_URL"), so a catalog pick of a
        # built-in provider (opencode-go, opencode-zen, …) needs no hand-typed
        # endpoint — 1:1 with the reference's provider/model switch.
        base_url = _provider_base_url(cfg, provider)
    if not local and not key_ref:
        # Likewise resolve the credential REFERENCE from the provider's env var
        # NAME (e.g. OPENCODE_GO_API_KEY -> env:OPENCODE_GO_API_KEY); the value
        # is never read or stored.
        key_ref = _provider_key_ref(cfg, provider)
    if not local and not base_url:
        return {"ok": False, "code": "MODEL_ENDPOINT_REQUIRED",
                "detail": (f"provider {provider!r} has no configured base_url; "
                           "configure the endpoint under Models & Providers "
                           "before selecting it"),
                "provider": provider, "model": model}
    if not local and not key_ref:
        # A keyless remote endpoint is a valid config-only (inert-until-key)
        # record only when it was explicitly staged; a bare catalog pick has no
        # endpoint to run, so refuse rather than pretend it is active.
        return {"ok": False, "code": "MODEL_CREDENTIAL_REQUIRED",
                "detail": (f"provider {provider!r} has no credential reference; "
                           "store an env:/file: reference first"),
                "provider": provider, "model": model}
    record = {"provider": provider, "model": model, "base_url": base_url,
              "key_ref": key_ref}
    if reuse_context > 0:
        record["context_length"] = reuse_context
    return activate_endpoint(cfg, pid, record, confirm=confirm)


def _clear_runtime_model(cfg: Any, *, provider: str, model: str) -> bool:
    """Drop the runtime ``config.yaml`` ``model`` slot when it names the
    endpoint being removed, so a deleted endpoint is not left as the configured
    default (R1). Best-effort; the profile registry is the authority.
    """
    if not provider and not model:
        return False
    try:
        from .. import qubit_runtime

        path = Path(qubit_runtime.runtime_home_for_cfg(cfg)) / "config.yaml"
        if not path.is_file():
            return False
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        node = data.get("model")
        if not isinstance(node, dict):
            return False
        if (str(node.get("provider") or ""), str(node.get("model") or "")) != (
                str(provider or ""), str(model or "")):
            return False
        data.pop("model", None)
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        return False


def remove_endpoint(cfg: Any, ident: str, *, confirm: bool = True
                    ) -> Dict[str, Any]:
    """Remove a custom endpoint's registered model profile and active pointer.

    Deleting the endpoint JSON alone left its ModelProfile and the persisted
    ``active_model_profile`` pointer in place, so ``/model/info`` kept targeting
    a deleted endpoint (UI audit 2026-09-29 D4). This clears the active
    selection when it names the endpoint, then removes the profile (never the
    shipped default). The staged endpoint JSON is removed by the caller.
    """
    ident = slug_endpoint_id(ident)
    if not ident:
        return {"ok": False, "code": "MALFORMED",
                "detail": "endpoint id required"}
    if not confirm:
        return {"ok": True, "dry_run": True, "profile_id": ident}
    out: Dict[str, Any] = {"ok": True, "profile_id": ident,
                           "profile_removed": False, "active_cleared": False}
    try:
        active = R.get_active_profile(cfg)
        if str(active.get("active") or "") == ident:
            R.clear_active_profile(cfg, confirm=True)
            out["active_cleared"] = True
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        pass
    try:
        prof = R.get_profile(cfg, ident)
        if prof is not None and not getattr(prof, "default", False):
            out["runtime_model_cleared"] = _clear_runtime_model(
                cfg, provider=str(prof.provider or ""),
                model=str(prof.model or ""))
            res = R.remove_model_profile(cfg, ident, confirm=True)
            out["profile_removed"] = res.get("state") == "OK"
            if res.get("state") != "OK":
                out["profile_code"] = str(res.get("code") or "")
    except Exception as exc:  # noqa: BLE001 - report, never raise
        out["profile_code"] = type(exc).__name__
    return out
