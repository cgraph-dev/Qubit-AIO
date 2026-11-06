"""HUD live-model-capabilities collector — Qubit native; no upstream code vendored.

Upstream's Live Model tab asks a running model for its live capability set. This
tree does not run a capability-probe endpoint, so the decision is a split one:

DECISION: MAP (per-model capability read) + GAP (a probe endpoint)
-----------------------------------------------------------------
The previous lane collapsed this into ``collectors_model_info`` ("reads the real
models.dev cache"). That answers for ONE model — the configured default — so a
reader cannot compare, and the tab they asked to close is not closed.

MAP: every model Qubit is configured for gets its own capability row, read the
same way the dashboard's own ``GET /api/model/info`` reads it — through
``agent.models_dev.get_model_capabilities(provider, model)`` for the fields
``supports_tools``/``supports_vision``/``supports_reasoning``/``context_window``/
``max_output_tokens``/``model_family``, plus the config-resolved model's
context length from :func:`collectors_model_info.collect_model_info`. The model
list comes from :func:`voicebridge.chat_transport.resolve_route` /
``resolve_model`` — the SAME resolver a turn uses — so the panel can never
offer a model the runtime would not run.

GAP, stated on screen: there is no "probe this model now" endpoint. The
dashboard records ``/api/providers/validate`` as ``_NO_NETWORK_PROBE`` in the
compat table, so a tab that fired a live capability request would do exactly
what the product declines to do. ``probe`` is therefore ``None`` with a reason
and the panel says "configured, not pinged".

Also reported, because it is the honest live signal Qubit does keep:
:mod:`voicebridge.providers.dead_models`, the record of models that most
recently failed a real call. A model listed there is marked ``recently_failed``
with its reason — that is evidence from a real request, not a probe.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

#: Capability rows read per request. Bounded: each row can touch the models.dev
#: cache, and a panel must stay a viewer rather than a catalogue crawl.
MODEL_LIMIT = 40
#: Model ids contributed by each credentialed provider beyond the configured
#: default (the picker catalogue carries up to 132 per provider).
MODELS_PER_PROVIDER = 5

#: Exactly the fields ``GET /api/model/info`` projects (its
#: ``_CAPABILITY_FIELDS``), kept identical so the two surfaces agree.
CAPABILITY_FIELDS = ("supports_tools", "supports_vision", "supports_reasoning",
                     "context_window", "max_output_tokens", "model_family")


def _configured_models(cfg: Any) -> Dict[str, Any]:
    """The runtime's own resolved route + model, as a turn would use them."""
    out: Dict[str, Any] = {"available": False, "detail": "",
                           "route": "", "model": "",
                           "route_kind": "", "provider": ""}
    try:
        from .. import chat_transport as CT

        route = CT.resolve_route(cfg)
        model = CT.resolve_model(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"route resolver unavailable ({type(exc).__name__}: {exc})"
        return out
    if isinstance(model, dict) and str(model.get("state") or "").upper() not in (
            "", "FAILED", "ERROR", "UNAVAILABLE"):
        out["model"] = str(model.get("model") or model.get("id") or "")
        out["provider"] = str(model.get("provider") or "")
    out["route"] = str(route.get("route") or "") if isinstance(route, dict) \
        else str(route or "")
    out["route_kind"] = str(route.get("kind") or "") \
        if isinstance(route, dict) else ""
    out["available"] = True
    out["detail"] = "voicebridge.chat_transport.resolve_route/resolve_model"
    return out


def _picker_models() -> Dict[str, List[str]]:
    """``{provider: [model ids]}`` from the dashboard's own picker catalogue.

    ``build_model_options_payload`` is what ``GET /api/model/options`` serves.
    Called with ``refresh=False`` (a READ path: disk-cache catalogs only, no
    provider probe) and ``include_unconfigured=False``, so the ids listed here
    are exactly the ones the picker would offer — no invented model id, and no
    network probe behind a read-only tab.
    """
    out: Dict[str, List[str]] = {}
    try:
        from qubit_cli.inventory import (  # type: ignore
            build_model_options_payload, load_picker_context)

        payload = build_model_options_payload(load_picker_context(),
                                              refresh=False)
    except Exception as exc:  # noqa: BLE001 - the configured route still renders
        out["_detail"] = f"{type(exc).__name__}: {exc}"     # type: ignore
        return out
    providers = payload.get("providers") if isinstance(payload, dict) else None
    authenticated = set()
    for row in providers or []:
        if not isinstance(row, dict):
            continue
        # The picker row names its provider in `slug` (its own field name);
        # `name` is the display label and is not an identity.
        name = str(row.get("slug") or "")
        if not name or row.get("authenticated", True) is False:
            continue
        authenticated.add(name)
        models = []
        for m in row.get("models") or []:
            # The picker lists model ids as bare strings; a dict shape is
            # accepted too so a payload change cannot silently empty this read.
            ident = (m.get("id") or m.get("name") or "") \
                if isinstance(m, dict) else str(m or "")
            if ident:
                models.append(str(ident))
        if models:
            out[name] = models
    try:
        out["_authenticated"] = sorted(authenticated)     # type: ignore
    except Exception:  # noqa: BLE001 - informational only
        pass
    return out


def _pool_models(cfg: Any, per_provider: int = MODELS_PER_PROVIDER
                 ) -> List[Dict[str, str]]:
    """``[{"model", "provider"}]`` for every model in the credential pool.

    Same inventory the Providers tab reads; a provider with no entries is
    skipped because a model behind no credential cannot be run at all. The
    model id comes from the picker catalogue (see :func:`_picker_models`);
    a provider whose catalogue is not cached yet yields no id rather than a
    guessed one. Each provider contributes at most ``per_provider`` ids: this
    panel reads capabilities, it does not enumerate a 132-model catalogue.
    """
    rows: List[Dict[str, str]] = []
    catalog = _picker_models()
    picker_auth = catalog.pop("_authenticated", None)    # type: ignore
    pool_names = []
    try:
        from .. import credentials as CR

        inv = CR.inventory(cfg)
        for provider in (inv.get("providers") or []) if isinstance(inv, dict) \
                else []:
            if isinstance(provider, dict) and provider.get("entries"):
                name = str(provider.get("provider") or "")
                if name:
                    pool_names.append(name)
    except Exception:  # noqa: BLE001 - the configured route still renders
        return rows
    # Prefer the picker's own authenticated set: those rows are the ones the
    # dashboard would offer, so a model listed here is runnable, not merely
    # credentialed. Providers the picker does not authenticate yet fall back to
    # the credential pool, which is still a real (if unverified) provider.
    names = list(picker_auth or []) + [n for n in pool_names
                                       if n not in (picker_auth or [])]
    for name in names:
        for model in catalog.get(name, [])[:max(1, int(per_provider))]:
            rows.append({"provider": name, "model": model})
    return rows


def _capabilities(provider: str, model: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"found": False, "detail": "", "caps": {}}
    if not model:
        out["detail"] = "no model id to look up"
        return out
    try:
        from agent.models_dev import get_model_capabilities  # type: ignore

        mc = get_model_capabilities(provider=provider, model=model)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"models.dev client unavailable ({type(exc).__name__}: {exc})"
        return out
    if mc is None:
        out["detail"] = ("no models.dev entry for this provider/model; "
                         "capabilities are unknown, not absent")
        return out
    caps = {}
    for name in CAPABILITY_FIELDS:
        value = getattr(mc, name, None)
        caps[name] = value if isinstance(value, (bool, int, float, str)) \
            or value is None else str(value)
    out.update(found=True, caps=caps, detail="models.dev catalog")
    return out


def _dead(cfg: Any, provider: str, model: str) -> Optional[Dict[str, Any]]:
    """Recent real-call failure for this model, if one was recorded."""
    if not model:
        return None
    try:
        from ..providers import dead_models as DM

        hit = DM.is_dead(cfg, provider, model)
    except Exception:  # noqa: BLE001 - the record is optional signal
        return None
    if not hit:
        return None
    return {"reason": str((hit or {}).get("reason") or "")[:200],
            "at": (hit or {}).get("at") or (hit or {}).get("recorded_at")}


def collect_live_model_capabilities(home: Optional[Any] = None, cfg: Any = None,
                                    *, limit: int = MODEL_LIMIT) -> Dict[str, Any]:
    """Capability rows for the configured model and every credentialed provider."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "configured": {},
        "models": [],
        "count": 0,
        "found_count": 0,
        "probe": None,
        "probe_reason": ("This build serves no live model-probe endpoint: "
                         "/api/providers/validate is a recorded refusal "
                         "(network probe). A row says what Qubit's own catalog "
                         "knows, never that the model was pinged."),
        "authority": "agent.models_dev.get_model_capabilities + "
                     "voicebridge.chat_transport + voicebridge.providers."
                     "dead_models",
    }
    configured = _configured_models(cfg)
    out["configured"] = copy.deepcopy(configured)

    rows: List[Dict[str, Any]] = []
    seen = set()
    # The configured route first: it is the model a turn will actually use.
    default_model = str(configured.get("model") or "")
    default_provider = str(configured.get("provider") or "")
    if not default_model:
        try:
            from .collectors_model_info import collect_model_info

            info = collect_model_info(cfg=cfg)
            default_model = str(info.get("model") or "")
            default_provider = str(info.get("provider") or "")
            out["configured"]["model"] = default_model
            out["configured"]["provider"] = default_provider
        except Exception:  # noqa: BLE001 - the pool rows still render
            pass
    if default_model:
        seen.add((default_provider, default_model))
        caps = _capabilities(default_provider, default_model)
        rows.append({
            "model": default_model,
            "provider": default_provider,
            "role": "configured default",
            "found": caps["found"],
            "detail": caps["detail"],
            "caps": caps["caps"],
            "recently_failed": _dead(cfg, default_provider, default_model),
        })

    for entry in _pool_models(cfg):
        provider = entry["provider"]
        model = str(entry.get("model") or "")
        key = (provider, model)
        # The configured model is already row 0; a provider reached through the
        # pool without an id is skipped by _pool_models itself.
        if key in seen:
            continue
        seen.add(key)
        caps = _capabilities(provider, model)
        rows.append({
            "model": model,
            "provider": provider,
            "role": "credentialed model",
            "found": caps["found"],
            "detail": caps["detail"],
            "caps": caps["caps"],
            "recently_failed": _dead(cfg, provider, model),
        })
        if len(rows) >= max(1, int(limit)):
            break

    out.update(
        available=bool(configured.get("available") or rows),
        detail=(configured.get("detail") or "") if not rows else
               f"{sum(1 for r in rows if r['found'])} of {len(rows)} model "
               f"row(s) resolved to catalog capabilities",
        models=rows,
        count=len(rows),
        found_count=sum(1 for r in rows if r["found"]),
    )
    return out


__all__ = ["collect_live_model_capabilities", "CAPABILITY_FIELDS"]
