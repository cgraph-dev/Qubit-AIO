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

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

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
        node: Dict[str, Any] = {"provider": provider, "model": model}
        if base_url:
            node["base_url"] = base_url
        if key_ref:
            node["key_env"] = key_ref
        data["model"] = node
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001 - profile registry is the authority
        return False


def activate_endpoint(cfg: Any, ident: str, record: Dict[str, Any], *,
                      confirm: bool = True) -> Dict[str, Any]:
    """Apply a staged custom endpoint as the effective runtime model.

    Returns a typed dict. ``confirm`` mirrors the caller's confirm gate so an
    unconfirmed call is a dry run (never a silent apply).
    """
    ident = slug_endpoint_id(ident or _text(record, "id", "name"))
    if not ident:
        return {"ok": False, "code": "MALFORMED",
                "detail": "endpoint id required"}
    profile = build_endpoint_profile(ident, record)
    gate = profile.validate(cfg)
    if gate.get("state") != "OK":
        return {"ok": False, "code": gate.get("code", "PROFILE_INVALID"),
                "detail": gate.get("detail", "endpoint is incomplete")}
    if not confirm:
        return {"ok": True, "dry_run": True, "profile_id": ident,
                "provider": profile.provider, "model": profile.model}
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
    return {"ok": True, "active": ident, "profile_id": ident,
            "provider": profile.provider, "model": profile.model,
            "kind": profile.kind, "allow_persisted": persisted,
            "home_applied": home_applied,
            "rollback": "select another profile or remove "
                        f"{ident!r} from the model-profile registry"}


def _slug_model_id(provider: str, model: str) -> str:
    return slug_endpoint_id(f"{provider}-{model}", fallback="model")


def select_model(cfg: Any, provider: str, model: str, *,
                 base_url: str = "", key_ref: str = "",
                 confirm: bool = True) -> Dict[str, Any]:
    """Find-or-create the profile for ``provider``/``model`` and activate it.

    The Qubit counterpart to the reference ``POST /api/model/set`` main-slot
    write. An already-registered profile with the same provider+model is
    reused (its endpoint/credential are preserved); otherwise one is created.
    A remote provider with no endpoint/credential is an honest typed refusal,
    never a fabricated success.
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
    for existing in R.load_profiles(cfg):
        if existing.default:
            continue
        if existing.model == model and existing.provider == provider:
            pid = existing.id
            base_url = base_url or existing.base_url
            key_ref = key_ref or existing.key_ref
            break
    if not pid:
        pid = _slug_model_id(provider, model)
    local = _is_local_url(base_url)
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
