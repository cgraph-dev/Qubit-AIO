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
    an explicitly recorded window (larger OR smaller) is never overwritten.
    A failed/unknown probe keeps the default (today's behavior).
    """
    if int(getattr(profile, "context_tokens", 0) or 0) != 4096:
        return False
    real = resolve_real_context_tokens(
        profile.model, profile.base_url, profile.provider)
    if real is None:
        return False
    profile.context_tokens = real
    return True


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

    One writer rule: the node is rebuilt from scratch on every write, with
    BOTH the ``model`` key (Qubit readers) and the ``default`` key (the
    vendored picker context reads ``default``, never ``model``). Merging
    instead once produced a Frankenstein slot (``model: spark17`` +
    ``default: muse`` + a stale ``base_url``) that made the picker show two
    CURRENT providers at once.
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
        node: Dict[str, Any] = {"provider": provider, "model": model,
                                "default": model}
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
    # A record without an explicit window gets the 4096 creation default;
    # replace it with the real window when the vendored metadata resolves one
    # (otherwise /chat's context floor refuses a capable model on stale data).
    backfill_profile_context(profile)
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
