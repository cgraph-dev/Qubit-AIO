"""Config, provider, extension and auth-target handlers for the ported page.

WHY THIS MODULE IS SEPARATE FROM ``handlers_extra``
---------------------------------------------------------------------------
``handlers_extra`` owns the chat/session/approval call set. This module owns the
*operational settings* surface: what model is active, which providers exist,
what reasoning effort is set, and which extensions are installed. They are split
because these handlers have a sharp write-authority story that deserves to be
read on its own — every write below goes through exactly one Qubit authority and
none of them writes a file directly.

THE AUTHORITY MAP (one row per family, no second implementation anywhere)
---------------------------------------------------------------------------
``settings``      READ  — ``qubit_compat.h_config`` (the live settings record),
                        re-shaped into the keys the panel binds (``theme``,
                        ``show_thinking``, ``default_model``, …).
                  WRITE — ``atomic_config_write`` only, for the handful of keys
                        this page can own. Unknown keys are a typed refusal, not
                        a silent drop: the settings form posts the whole body and
                        a silently-ignored field reads to the user as "saved".
``reasoning``     READ/WRITE — ``agent.reasoning_effort`` and
                        ``display.show_reasoning`` in config.yaml, through
                        ``atomic_config_write``. This is the SAME keys the CLI's
                        ``/reasoning`` writes, so the chip and the CLI agree.
``providers``     READ  — the ``providers`` tree of ``/api/config``. Never a key
                        value: only ``has_key``/``key_ref`` presence.
                  WRITE — REFUSED. Credentials live in the keys store, never in
                        config.yaml (``CREDENTIALS_NOT_IN_CONFIG``).
``default-model`` POST  — ``providers.endpoints.select_model``, the canonical
                        find-or-create-and-activate resolver. Same path the SPA
                        uses, so both surfaces cannot disagree.
``model/set``     — already served by ``qubit_compat.h_model_set``; this module
                  does not re-implement it.
``extensions/*``  READ  — the vendored dashboard-plugin discovery
                        (``_discover_dashboard_plugins``), the same source
                        ``/api/plugins`` already serves.
                  WRITE — ``plugins_cmd._admit_and_save_plugin_sets``, the ONE
                        plugin admission authority. Install/uninstall are
                        REFUSED: extension packages are code that runs with the
                        agent's privileges and Qubit installs plugins only via
                        ``qubit plugins install`` (a reviewed, pinned flow).
``auth/*``        REFUSED, ALL OF IT. See the section at the bottom — this is
                  the deliberate refusal this module exists to make honest.

WHY ``auth`` IS A REFUSAL AND NOT AN IMPLEMENTATION
---------------------------------------------------------------------------
Upstream's ``/api/auth/*`` is a USER-PASSWORD + WebAuthn-PASSKEY model: a
``password_hash`` in settings, a browser session cookie, a credential table, and
``navigator.credentials.create()`` round-trips. Qubit has none of those. Its
dashboard auth is a loopback session TOKEN (``webui.token``) plus PROVIDER OAuth
for model access — ``qubit_compat`` proves this: its only auth routes are
``/api/auth/me`` (a constant loopback-operator identity) and
``/auth/{login,logout,native/*}`` (provider handshake + token revocation), and
``qubit_asgi.SENSITIVE_READ_PREFIXES`` puts ``/api/auth`` on the never-proxy list
because the fork would serve credentials Qubit does not own.

Shipping a passkey registration endpoint here would mean inventing a credential
store, an attestation check and a revocation path — a second, unreviewed auth
system on the same listener. That is strictly worse than an honest 501, so
``auth/status`` answers with a TRUE non-secret description of Qubit's real model
(fields the panel reads: ``auth_enabled``, ``password_auth_enabled``,
``passkey_feature_flag``) and every mutating ``auth/*`` path is a typed
``WB_AUTH_MODEL_UNSUPPORTED`` 501 naming what Qubit actually uses.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .handlers_extra import _body, _refuse
from ..workbench_shim import UPSTREAM_PIN

#: The config keys this page is ALLOWED to write, mapped to the section they
#: live in. Every one of them is a display/preference key with no security
#: effect, which is what keeps the write lane narrow enough to audit by reading
#: this dict. A key absent here is a typed refusal.
_WRITABLE_KEYS: Dict[str, Tuple[str, ...]] = {
    "show_thinking": ("display", "show_reasoning"),
    "show_reasoning": ("display", "show_reasoning"),
    "streaming": ("display", "streaming"),
    "theme": ("dashboard", "theme"),
}

#: Reasoning efforts the agent core accepts. The chip renders its ladder from
#: ``supported_efforts``; serving one of these the core would reject is a lie the
#: user only discovers on the next turn.
EFFORTS: Tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh",
                            "max")


# --------------------------------------------------------------------- helpers
def _cfg_path() -> Optional[Any]:
    """The user config.yaml this process writes, or ``None``.

    Resolved lazily so a tree without the vendored runtime still imports and
    still answers a typed refusal instead of an ImportError at listen time.
    """
    try:
        from qubit_cli.config import get_config_path  # type: ignore

        return get_config_path()
    except Exception:  # noqa: BLE001 - absence is a typed answer
        return None


def _read_user_config() -> Dict[str, Any]:
    """The RAW user config dict (pre-defaults), via the canonical reader.

    ``read_user_config_raw`` is the same reader every CLI writer uses, so the
    write below merges onto exactly what is on disk rather than onto a
    normalized tree that would re-add defaults on save.
    """
    from qubit_cli.config import read_user_config_raw  # type: ignore

    path = _cfg_path()
    if path is None:
        raise RuntimeError("qubit_cli.config.get_config_path is unavailable")
    data = read_user_config_raw(path)
    return data if isinstance(data, dict) else {}


def _write_user_config(data: Dict[str, Any]) -> None:
    """Persist through ``atomic_config_write`` — the ONE atomic writer.

    Deliberately NOT a direct ``open().write()``: ``atomic_config_write`` keeps
    the unreadable-file guard, ruamel comment preservation and the
    no-accidental-deletion rule (a partial mapping can never drop a section it
    did not mention). Every CLI writer uses it; this route adds no second path.
    """
    from qubit_cli.config import atomic_config_write  # type: ignore

    path = _cfg_path()
    if path is None:
        raise RuntimeError("qubit_cli.config.get_config_path is unavailable")
    atomic_config_write(path, data)


def _dig(data: Any, *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _put(data: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    node = data
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = node[key] = {}
        node = child
    node[path[-1]] = value


def _select_tree(cfg: Any) -> Dict[str, Any]:
    """The settings tree this module reads its own keys from.

    ``read_user_config_raw`` (uncached) is the authority, NOT ``h_config``:
    ``_config_live`` builds its record from ``cfg.data``/``config.DEFAULTS`` plus
    the dashboard override store, so a value just written to ``config.yaml`` was
    invisible to it — the panel wrote a setting and immediately re-read the old
    value. The raw file is exactly what ``atomic_config_write`` persists, so
    write-then-read round-trips on the same bytes.
    """
    data = _read_user_config()
    # Options: the live agent's DEFAULTS are what a control shows before the user
    # has ever touched it. Merge them UNDER the user file so an unset key still
    # reports the real effective value rather than ``null``.
    try:
        from qubit_cli.config import DEFAULT_CONFIG  # type: ignore

        merged: Dict[str, Any] = json.loads(
            json.dumps(DEFAULT_CONFIG, default=str))
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged
    except Exception:  # noqa: BLE001 - the raw file alone is a valid answer
        return data


# -------------------------------------------------------------------- settings
def settings(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
             p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET|POST /api/settings`` — the panel's settings record.

    BEFORE THIS, ``settings`` was routed to ``h_wb_settings``, which returned the
    ``/api/config`` tree verbatim. The panel binds ``settings.theme``,
    ``settings.show_thinking``, ``settings.default_model`` and a dozen more: none
    of those exist at the top of a config tree (they live under ``dashboard``,
    ``display`` and ``model``), so every control hydrated from ``undefined`` and
    the form painted its own defaults over the user's real values.

    The GET therefore FLATTENS the keys the panel actually reads, on top of the
    config tree so nothing is lost. A value Qubit does not have is ``None`` and
    the panel's own ``|| fallback`` applies — never a fabricated default.
    """
    method = str(p.get("method") or "GET").upper()
    if method in ("POST", "PUT", "PATCH"):
        return _settings_write(cfg, b)
    try:
        tree = _select_tree(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the settings record is unreadable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="settings",
                       counterpart="Qubit Dashboard /dashboard → Settings")
    out: Dict[str, Any] = dict(tree)
    model_node = tree.get("model")
    main_model = (model_node.get("default") or model_node.get("name")
                  if isinstance(model_node, dict)
                  else model_node)
    out.update({
        "ok": True,
        # --- the keys the panel binds, read from where they really live.
        "show_thinking": _dig(tree, "display", "show_reasoning"),
        "streaming": _dig(tree, "display", "streaming"),
        "theme": _dig(tree, "dashboard", "theme"),
        "default_model": str(main_model or "") or None,
        "default_model_provider": (
            str((model_node or {}).get("provider") or "") or None
            if isinstance(model_node, dict) else None),
        "reasoning_effort": _dig(tree, "agent", "reasoning_effort"),
        # --- auth facts the same panel renders. Qubit's model, truthfully:
        # loopback session-token gate, no user password, no passkeys.
        **_auth_facts(cfg),
        # --- version facts the version badges render.
        "webui_version": str(_dig(tree, "desktop", "version") or "") or None,
    })
    return 200, out


def _settings_write(cfg: Any, b: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/settings`` — write ONLY the display/preference keys above.

    Anything else in the body is a typed refusal that NAMES the field and writes
    NOTHING. The panel posts its whole form, so a silently-dropped field would
    render "Settings saved" over a value that did not persist — a lie the user
    acts on. Refusing keeps the promise literal: either every requested key
    landed, or the response says which one did not and no byte changed.
    """
    body = _body(b)
    if not body:
        return _refuse("WB_BAD_INPUT",
                       "POST /api/settings needs at least one field",
                       endpoint="settings")
    wanted: Dict[Tuple[str, ...], Any] = {}
    unsupported: List[str] = []
    for key, value in body.items():
        if key.startswith("_") or key in ("ok", "upstream", "config"):
            # ``_set_password``/``_clear_password``/``_current_password``/``_passwordless``
            # are upstream's password lane. They are NOT silently ignored: they
            # are the auth model Qubit does not have, so they are named.
            unsupported.append(key)
            continue
        section = _WRITABLE_KEYS.get(key)
        if section is None:
            unsupported.append(key)
            continue
        if key == "show_thinking":
            wanted[("display", "show_reasoning")] = bool(value)
        elif key == "show_reasoning":
            wanted[("display", "show_reasoning")] = bool(value)
        elif key == "streaming":
            wanted[("display", "streaming")] = bool(value)
        elif key == "theme":
            wanted[("dashboard", "theme")] = str(value or "").strip()
    if unsupported:
        auth_keys = [k for k in unsupported if "password" in k or k.startswith("_")]
        return _refuse(
            "WB_AUTH_MODEL_UNSUPPORTED" if auth_keys else "WB_NOT_IN_PORT_SET",
            (f"/api/settings received field(s) {unsupported!r} that this page "
             f"cannot write. Qubit's settings live in config.yaml and are "
             f"edited on its dashboard; nothing was written."
             + (" The password fields belong to upstream's user-password auth,"
                " which Qubit does not have (loopback session token + provider"
                " OAuth)." if auth_keys else "")),
            endpoint="settings",
            counterpart="Qubit Dashboard /dashboard → Settings, or `qubit config set`")
    try:
        data = _read_user_config()
        for path, value in wanted.items():
            _put(data, path, value)
        _write_user_config(data)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_ADAPTER_FAULT",
                       f"the config write raised {type(exc).__name__}: {exc}",
                       endpoint="settings")
    return 200, {"ok": True, "written": sorted(".".join(k) for k in wanted),
                 "source": "config.yaml", "upstream": UPSTREAM_PIN}


# ------------------------------------------------------------------- reasoning
def _reasoning_context(q: Dict[str, Any]) -> Dict[str, Any]:
    """Echo the model/provider the client asked about, so the chip can key its
    answer. Absent fields stay absent — the chip treats ``undefined`` as "ask the
    active model", which is exactly right."""
    out: Dict[str, Any] = {}
    for key in ("model", "provider", "profile"):
        value = str(q.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def reasoning(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET|POST /api/reasoning`` — the effort chip / ``display`` toggle.

    ``GET`` answers the CURRENT persisted values plus the effort ladder the agent
    core accepts. ``POST`` writes ``{effort}`` -> ``agent.reasoning_effort`` and
    ``{display}`` -> ``display.show_reasoning``, the same two keys the CLI's
    ``/reasoning`` command writes — so the chip and the CLI can never disagree
    about what is set. Anything else in the body is a typed refusal.

    This replaces a route that answered a ``settings`` shape: the chip read
    ``st.reasoning_effort`` from a config tree that has no such top-level key,
    so it always painted "not set" and every save looked applied but was not.
    """
    method = str(p.get("method") or "GET").upper()
    if method in ("POST", "PUT", "PATCH"):
        return _reasoning_write(cfg, b)
    try:
        tree = _select_tree(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the config record is unreadable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="reasoning")
    effort = str(_dig(tree, "agent", "reasoning_effort") or "") or None
    show = _dig(tree, "display", "show_reasoning")
    return 200, {
        "ok": True,
        "reasoning_effort": effort,
        "supported_efforts": list(EFFORTS),
        # Qubit's models accept a thinking on/off toggle independently of the
        # effort ladder, so the chip keeps that control visible.
        "supports_thinking_toggle": True,
        "show_thinking": None if show is None else bool(show),
        # Surfaced for the chip's staleness guard, never invented.
        "context": _reasoning_context(q),
        "source": "config.yaml",
    }


def _reasoning_write(cfg: Any, b: Dict[str, Any]) -> Tuple[int, Any]:
    body = _body(b)
    effort = body.get("effort", "__absent__")
    display = body.get("display", "__absent__")
    if effort == "__absent__" and display == "__absent__":
        return _refuse("WB_BAD_INPUT",
                       "POST /api/reasoning needs 'effort' or 'display'",
                       endpoint="reasoning")
    updates: Dict[Tuple[str, ...], Any] = {}
    if effort != "__absent__":
        eff = str(effort or "").strip().lower()
        # ``hide``/``show`` are display verbs the client may send in this slot;
        # anything else must be a real rung of the ladder the core accepts.
        if eff in ("hide", "off"):
            updates[("display", "show_reasoning")] = False
        elif eff in ("show", "on"):
            updates[("display", "show_reasoning")] = True
        elif eff and eff in EFFORTS:
            updates[("agent", "reasoning_effort")] = eff
        elif not eff:
            updates[("agent", "reasoning_effort")] = None
        else:
            return _refuse("WB_BAD_INPUT",
                           f"effort {eff!r} is not one of {list(EFFORTS)}",
                           endpoint="reasoning")
    if display != "__absent__":
        verb = str(display or "").strip().lower()
        if verb not in ("show", "on", "hide", "off", "none"):
            return _refuse("WB_BAD_INPUT",
                           f"display {verb!r} must be show|hide|none",
                           endpoint="reasoning")
        updates[("display", "show_reasoning")] = verb in ("show", "on")
    try:
        data = _read_user_config()
        for path, value in updates.items():
            if value is None:
                node = data
                for key in path[:-1]:
                    node = node.get(key) if isinstance(node, dict) else None
                    if node is None:
                        break
                if isinstance(node, dict):
                    node.pop(path[-1], None)
            else:
                _put(data, path, value)
        _write_user_config(data)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_ADAPTER_FAULT",
                       f"the config write raised {type(exc).__name__}: {exc}",
                       endpoint="reasoning")
    tree_effort = updates.get(("agent", "reasoning_effort"), "__keep__")
    if tree_effort == "__keep__":
        tree_effort = _dig(_read_user_config(), "agent", "reasoning_effort")
    return 200, {
        "ok": True,
        "reasoning_effort": tree_effort if tree_effort != "__keep__" else None,
        "supported_efforts": list(EFFORTS),
        "supports_thinking_toggle": True,
        "show_thinking": updates.get(("display", "show_reasoning"),
                                     _dig(_read_user_config(),
                                          "display", "show_reasoning")),
        "written": sorted(".".join(k) for k in updates),
        "source": "config.yaml",
    }


# ------------------------------------------------------------------- providers
#: Secrets NEVER cross this boundary. Every provider row reports key PRESENCE
#: (``has_key``) and the reference name (``key_ref``), never a value.
def providers(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET|POST /api/providers``, ``/api/providers/delete``.

    GET lists the configured providers in the shape the cards render
    (``id``/``display_name``/``has_key``/``key_source``/``models``/…), read from
    the same ``/api/config`` ``providers`` tree the model picker uses.

    POST is REFUSED with ``CREDENTIALS_NOT_IN_CONFIG``: upstream stores a raw API
    key in settings, Qubit stores a REFERENCE (``env:``/``file:``) and the secret
    itself in the keys store. Accepting a key here would either write a secret
    into config.yaml or silently drop the user's input while reporting success —
    both are worse than naming the real path.
    """
    method = str(p.get("method") or "GET").upper()
    delete = str(p.get("sub") or "").rstrip("/").endswith("delete")
    if method in ("POST", "PUT", "PATCH", "DELETE") or delete:
        return _refuse(
            "WB_CREDENTIALS_NOT_IN_CONFIG",
            "Qubit never stores a provider API key in config.yaml: keys live in "
            "the keys store and config carries only an env:/file: reference. "
            "This route cannot accept a raw key, so nothing was written. Use "
            "Models & Providers in the Qubit dashboard (or `qubit keys set`) "
            "to add or remove a credential.",
            endpoint="providers" + ("/delete" if delete else ""),
            counterpart="Qubit Dashboard → Models & Providers, or `qubit keys set`",
            status=403)
    try:
        tree = _select_tree(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the provider tree is unreadable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="providers")
    provs = tree.get("providers")
    if not isinstance(provs, dict):
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       "/api/config carries no providers tree",
                       endpoint="providers")
    active_provider = str(tree.get("provider") or "") or None
    rows: List[Dict[str, Any]] = []
    for name in sorted(provs):
        pcfg = provs.get(name)
        if not isinstance(pcfg, dict):
            continue
        models = pcfg.get("models")
        if isinstance(models, dict):
            model_ids = sorted(str(k) for k in models)
        elif isinstance(models, (list, tuple)):
            model_ids = [str(m) for m in models if isinstance(m, str)]
        else:
            model_ids = []
        default_model = str(pcfg.get("default_model") or "").strip()
        if default_model and default_model not in model_ids:
            model_ids.insert(0, default_model)
        # Presence facts only — never the value behind the reference.
        key_ref = str(pcfg.get("key_ref") or pcfg.get("key_env") or "")
        base_url = str(pcfg.get("base_url") or "")
        rows.append({
            "id": name,
            "display_name": str(pcfg.get("display_name") or name),
            "has_key": bool(key_ref),
            "key_source": ("config_yaml" if key_ref else "none"),
            "key_ref": key_ref or None,
            "base_url": base_url or None,
            "is_oauth": bool(pcfg.get("oauth")),
            "is_custom": bool(base_url and not pcfg.get("oauth")),
            "is_self_hosted": bool(pcfg.get("self_hosted")),
            "is_plugin_provider": False,
            "model_default": default_model or None,
            "models": model_ids,
            "models_total": len(model_ids),
            "configurable": True,
            "active": bool(name == active_provider),
        })
    return 200, {"ok": True, "providers": rows,
                 "active_provider": active_provider,
                 "source": "/api/config.providers"}


# ----------------------------------------------------------------- default-model
def default_model(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                  p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET|POST /api/default-model`` — the one place the default model is set.

    BEFORE THIS, ``/api/default-model`` was routed to the MODELS handler, so the
    client's ``POST {model, provider}`` was answered with an inventory listing and
    the user's model choice was silently discarded while the settings form
    reported "saved".

    POST delegates to ``providers.endpoints.select_model`` — the SAME canonical
    find-or-create-and-activate resolver the Qubit SPA and ``/api/model/set``
    use. No second writer, and the confirm-required envelope is passed straight
    through so an expensive pick still asks first.
    """
    method = str(p.get("method") or "GET").upper()
    if method in ("POST", "PUT", "PATCH"):
        body = _body(b)
        model = str(body.get("model") or "").strip()
        provider = str(body.get("provider") or "").strip()
        if not model:
            return _refuse("WB_BAD_INPUT",
                           "POST /api/default-model needs a model name",
                           endpoint="default-model")
        if not provider:
            # A model with no provider is exactly the ambiguous case Qubit's
            # resolver refuses. Say so here rather than letting it fail opaque.
            return _refuse(
                "WB_BAD_INPUT",
                f"POST /api/default-model got model {model!r} with no provider; "
                f"a model id alone is ambiguous across providers, so nothing "
                f"was set. Send {{model, provider}}.",
                endpoint="default-model")
        confirm = bool(body.get("confirm") or body.get("confirmed")
                       or body.get("confirm_expensive_model"))
        try:
            from voicebridge.providers import endpoints as EP

            applied = EP.select_model(
                cfg, provider, model,
                base_url=str(body.get("base_url") or "").strip(),
                key_ref=str(body.get("key_ref") or body.get("key_env") or "").strip(),
                confirm=confirm)
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_ADAPTER_FAULT",
                           f"select_model raised {type(exc).__name__}: {exc}",
                           endpoint="default-model")
        if not applied.get("ok"):
            code = str(applied.get("code") or "MODEL_SET_FAILED")
            confirm_required = bool(applied.get("confirm_required"))
            status = 409 if confirm_required else 400
            return status, {
                "ok": False,
                "code": code,
                "confirm_required": confirm_required,
                "confirm_message": str(applied.get("confirm_message") or ""),
                "detail": str(applied.get("detail") or ""),
                "provider": provider,
                "model": model,
                "qubit_counterpart": "Qubit Dashboard → Models",
                "upstream": UPSTREAM_PIN,
            }
        return 200, {"ok": True,
                     "model": str(applied.get("model") or model),
                     "provider": str(applied.get("provider") or provider),
                     "profile_id": applied.get("profile_id"),
                     "warning": str(applied.get("warning") or ""),
                     "source": "providers.endpoints.select_model"}
    # GET: the current default, in both the flat and nested shapes the client
    # reads. Not the whole inventory — that is /api/models.
    try:
        tree = _select_tree(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the config record is unreadable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="default-model")
    node = tree.get("model")
    if isinstance(node, dict):
        model = str(node.get("default") or node.get("name") or "")
        provider = str(node.get("provider") or "")
    else:
        model, provider = str(node or ""), str(tree.get("provider") or "")
    return 200, {"ok": True, "model": model or None,
                 "provider": provider or None,
                 "default_model": model or None,
                 "default_model_provider": provider or None}


# ----------------------------------------------------------------------- tools
def _mcp_server_rows(cfg: Any) -> Optional[List[Dict[str, Any]]]:
    """MCP server rows from the merged config, or ``None`` when unreadable.

    Falls back to ``load_config()`` (every layer: defaults + runtime config.yaml +
    user config) because the servers live under ``mcp_servers`` in the RUNTIME
    config, not under ``mcp`` in the user file. Used only when ``qubit_compat``
    could not answer (a listener without a ``cfg``), so the panel still gets the
    real inventory instead of a fabricated one.
    """
    try:
        from qubit_cli.config import load_config  # type: ignore

        merged = load_config()
    except Exception:  # noqa: BLE001
        try:
            merged = _select_tree(cfg)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(merged, dict):
        return None
    block = merged.get("mcp_servers")
    if isinstance(block, dict):
        return [dict(v, name=k) if isinstance(v, dict) else {"name": k}
                for k, v in block.items()]
    if isinstance(block, list):
        return [dict(s) for s in block if isinstance(s, dict)]
    return None


def tools(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/tools`` and ``GET /api/mcp/tools`` — the tool inventory.

    The ported page calls ``/api/mcp/tools`` for its MCP tool list
    (``panels.js:14541``); ``/api/tools`` is the same fact at the top level. Both
    are answered from the SAME live MCP server set ``/api/mcp/servers`` already
    serves, so the two panels cannot disagree.

    A server whose registry is not enumerable contributes ``tools: []`` with the
    reason attached — never a fabricated tool name, because a name that does not
    resolve fails at call time in the agent's transport, not here.
    """
    try:
        from voicebridge import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", query={}, body={})
    except AttributeError:
        # ``cfg`` is None on a listener that has not built one and
        # ``state_dir()`` then raises. Read the MCP block from the raw config
        # rather than answering a fabricated empty list.
        try:
            block = _select_tree(cfg).get("mcp")
            servers = block.get("servers") if isinstance(block, dict) else None
            code, out = 200, {"servers": servers if isinstance(servers, list)
                              else []}
        except Exception as exc2:  # noqa: BLE001
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           f"the MCP server list is unreadable: "
                           f"{type(exc2).__name__}: {exc2}", endpoint="tools")
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the MCP server list is unreadable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="tools")
    # ``qubit_compat.dispatch`` catches a handler fault and RETURNS a 5xx
    # (``EXECUTOR_ERROR``) rather than raising, so a cfg-less listener surfaces
    # as a non-200 here, not as an exception. Fall back to the raw config block
    # so an inventory of real servers is still served — never a fabricated one.
    if code != 200:
        servers = _mcp_server_rows(cfg)
        if servers is None:
            # No merged config and no compat answer. Decide on the RAW user file,
            # not the defaults-merged tree (which is never empty): with no user
            # config and no mergeable inventory the home genuinely has zero
            # servers, and an empty list is the truth — only a real read fault
            # (a user file exists yet nothing could be parsed) passes the error.
            try:
                raw = _read_user_config()
            except Exception:  # noqa: BLE001
                return code, out
            if raw:
                return code, out
            servers = []
    else:
        servers = out.get("servers") if isinstance(out, dict) else out
        servers = servers if isinstance(servers, list) else []
    rows: List[Dict[str, Any]] = []
    total = 0
    for srv in servers:
        if not isinstance(srv, dict):
            continue
        name = str(srv.get("name") or "")
        listed = srv.get("tools")
        names = ([str(t.get("name") if isinstance(t, dict) else t)
                  for t in listed] if isinstance(listed, list) else [])
        total += len(names)
        rows.append({"server": name, "tools": names,
                     "enumerable": bool(names),
                     "transport": str(srv.get("transport") or "stdio"),
                     "detail": ("" if names else
                                "tool list not enumerated for this server")})
    return 200, {"ok": True, "servers": rows, "tools": rows,
                 "total": total, "source": "/api/mcp/servers"}


# ------------------------------------------------------------------ extensions
def _extension_rows(cfg: Any) -> Tuple[List[Dict[str, Any]], set, set]:
    """(installed rows, enabled set, disabled set) from the plugin authority.

    Same discovery the already-working ``/api/plugins`` route uses
    (``_discover_dashboard_plugins``), so the extensions tab and the plugins tab
    show one truth. The enabled/disabled sets come from ``plugins_cmd``'s own
    accessors — the ones the enable/disable CLI reads.
    """
    from qubit_cli.config import load_config  # type: ignore
    from qubit_cli.plugins_cmd import (  # type: ignore
        _get_disabled_set, _get_enabled_set)
    from qubit_cli.web_server_dashboard import (  # type: ignore
        _discover_dashboard_plugins)

    try:
        enabled = set(_get_enabled_set())
    except Exception:  # noqa: BLE001
        enabled = set()
    try:
        disabled = set(_get_disabled_set())
    except Exception:  # noqa: BLE001
        disabled = set()
    rows: List[Dict[str, Any]] = []
    for plugin in _discover_dashboard_plugins():
        if not isinstance(plugin, dict):
            continue
        name = str(plugin.get("name") or plugin.get("key") or "")
        if not name:
            continue
        source = str(plugin.get("source") or "")
        # The load-time rule: a user plugin serves only when enabled and not
        # disabled (the same gate ``h_plugins`` applies), while bundled
        # extensions are active unless explicitly disabled.
        if source == "user":
            active = name in enabled and name not in disabled
        else:
            active = name not in disabled
        rows.append({
            "id": name,
            "key": str(plugin.get("key") or name),
            "name": name,
            "version": str(plugin.get("version") or ""),
            "description": str(plugin.get("description") or ""),
            "source": source or "bundled",
            "enabled": bool(active),
            "installed": True,
        })
    del cfg  # discovery reads the runtime home; cfg only gates availability
    return rows, enabled, disabled


def _extensions_status(cfg: Any) -> Dict[str, Any]:
    rows, _enabled, _disabled = _extension_rows(cfg)
    return {
        "ok": True,
        "extensions": rows,
        "gallery_installed": {r["id"]: r["version"] or "installed" for r in rows},
        "count": len(rows),
        # No sidecar / native-host proxy layer exists in Qubit, so the consent
        # surface has nothing to consent to. Stated, not implied.
        "sidecar_proxy_supported": False,
        "registry": "local",
        "source": "qubit_cli.web_server_dashboard._discover_dashboard_plugins",
    }


def extensions_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/extensions/status`` — installed extensions + enablement."""
    try:
        return 200, _extensions_status(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"extension discovery is unavailable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="extensions/status",
                       counterpart="Qubit Dashboard → Plugins")


def extensions_registry(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                        p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/extensions/registry`` — Qubit has no remote extension registry.

    Upstream fetches a catalog of downloadable extensions over the network and
    installs them by ``download_url`` + ``sha256``. Qubit's plugin sources are the
    ones ALREADY on disk (bundled + user-installed); there is no upstream service
    to fetch a catalog from, so ``entries`` is the installed set with
    ``installed: true`` and the panel's gallery correctly shows nothing new to
    install — instead of an invented catalog of packages that do not exist.
    """
    try:
        rows, _e, _d = _extension_rows(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"extension discovery is unavailable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="extensions/registry")
    entries = [{
        "id": r["id"],
        "name": r["name"],
        "version": r["version"],
        "description": r["description"],
        "installed": True,
        "download_url": None,
        "sha256": None,
    } for r in rows]
    return 200, {"ok": True, "entries": entries, "count": len(entries),
                 "remote_registry": False,
                 "detail": ("Qubit has no remote extension registry: this lists "
                            "the plugins already present on disk. New plugins "
                            "are added with `qubit plugins install <owner/repo>`.")}


def extensions_toggle(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/extensions/toggle`` — enable/disable through the ONE authority.

    The write goes through ``plugins_cmd._admit_and_save_plugin_sets`` — the same
    admission gate ``qubit plugins enable/disable`` passes: the candidate set is
    resolved against the live environment and the config commit is atomic, so a
    refusal leaves BOTH config.yaml and the environment untouched. An unknown id
    is a typed 404 and writes nothing.
    """
    body = _body(b)
    ident = str(body.get("id") or "").strip()
    if not ident:
        return _refuse("WB_BAD_INPUT", "POST /api/extensions/toggle needs 'id'",
                       endpoint="extensions/toggle")
    if "enabled" not in body:
        return _refuse("WB_BAD_INPUT",
                       "POST /api/extensions/toggle needs 'enabled' (bool)",
                       endpoint="extensions/toggle")
    enable = bool(body.get("enabled"))
    try:
        from qubit_cli.plugins_cmd import (  # type: ignore
            _admit_and_save_plugin_sets, _apply_activation,
            _discover_all_plugins, _get_disabled_set, _get_enabled_set,
            _plugin_aliases, _resolve_plugin_key)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the plugin authority is unavailable: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="extensions/toggle")
    key = _resolve_plugin_key(ident)
    if not key:
        return _refuse("WB_NOT_FOUND",
                       f"no installed extension named {ident!r}; nothing changed",
                       endpoint="extensions/toggle",
                       counterpart="Qubit Dashboard → Plugins")
    try:
        new_enabled = set(_get_enabled_set())
        new_disabled = set(_get_disabled_set())
        entries = _discover_all_plugins()
        _apply_activation(new_enabled, new_disabled, key,
                          _plugin_aliases(key, entries), enable=enable)
        _admit_and_save_plugin_sets(
            new_enabled, new_disabled,
            action=("Enable " if enable else "Disable ") + key,
            expected_config=None)
    except Exception as exc:  # noqa: BLE001
        # AdmissionRefused / DependencyConflict both land here. The message is
        # the authority's own (it names the missing dependency) and the state is
        # provably unchanged, which is what the toast must say.
        return 409, {
            "ok": False,
            "code": "WB_PLUGIN_ADMISSION_REFUSED",
            "endpoint": "extensions/toggle",
            "detail": f"{type(exc).__name__}: {exc}",
            "state": "UNCHANGED",
            "qubit_counterpart": "Qubit Dashboard → Plugins, or `qubit plugins enable/disable`",
            "owner": "qubit-policy",
            "upstream": UPSTREAM_PIN,
        }
    try:
        out = _extensions_status(cfg)
    except Exception:  # noqa: BLE001 - the write landed; a re-read fault is not a write fault
        out = {"ok": True, "extensions": [], "count": 0}
    out["toggled"] = {"id": ident, "key": key, "enabled": enable}
    out["restart_required"] = True
    return 200, out


def extensions_install(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                       p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/extensions/{install,uninstall}`` — REFUSED, named.

    Installing a plugin is running third-party code with the agent's privileges.
    Qubit's path is ``qubit plugins install <owner/repo>``: it pins the source,
    records provenance and admits the plugin through the dependency resolver.
    A ``download_url`` + ``sha256`` posted from a web page is not that flow, and
    accepting it here would let any page that reaches this listener install
    arbitrary code without the pin or the review.
    """
    verb = str(p.get("action") or str(p.get("sub") or "").rsplit("/", 1)[-1])
    return _refuse(
        "WB_BROKERED_INSTALL",
        f"extensions/{verb or 'install'} is a code-install action: Qubit admits "
        f"plugins only through the pinned, provenance-recording `qubit plugins "
        f"install <owner/repo>` flow (and `qubit plugins uninstall <name>` to "
        f"remove one). A download_url/sha256 pair from this page is not that "
        f"flow, so nothing was downloaded, installed or removed.",
        endpoint="extensions/" + (verb or "install"),
        counterpart="`qubit plugins install <owner/repo>` / `qubit plugins uninstall <name>`",
        status=403)


def extensions_sidecar_consent(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                               p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/extensions/sidecar-proxy-consent`` — GAP, answered honestly.

    Upstream extensions may ship a sidecar process that a proxy tunnels to, and
    this endpoint records the user's consent to that tunnel. Qubit has no
    sidecar/proxy layer, so there is nothing to consent to. Granting consent for
    a tunnel that does not exist would persist a permission with no owner; so the
    answer is a 501 that names the gap.
    """
    return _refuse(
        "WB_CAPABILITY_UNBOUND",
        "Qubit has no extension sidecar/proxy layer, so there is no tunnel to "
        "consent to and no record was written.",
        endpoint="extensions/sidecar-proxy-consent",
        counterpart="Qubit Dashboard → Plugins (no sidecar proxy exists)")


# ------------------------------------------------------------------------ auth
def _auth_facts(cfg: Any) -> Dict[str, Any]:
    """Qubit's REAL auth state, in the field names the panel reads.

    ``auth_enabled`` here means "the listener gates requests", which for Qubit is
    the loopback session-token gate (``webui.token``) — the same fact
    ``qubit_compat._status_auth_gate`` reports. ``password_auth_enabled`` and
    ``passkey_feature_flag`` are ``False`` because those mechanisms DO NOT EXIST,
    and the panel renders "unauthenticated" / hides the passkey block, which is
    the truthful outcome (upstream's own comment: hide a non-functional button).
    """
    try:
        from voicebridge import qubit_compat as HC

        gate = HC._status_auth_gate(cfg)
    except Exception:  # noqa: BLE001 - a gate read fault must not claim "open"
        return {"auth_enabled": None, "password_auth_enabled": False,
                "passkey_feature_flag": False, "auth_readable": False}
    return {
        "auth_enabled": bool(gate.get("auth_required")),
        "password_auth_enabled": False,
        "passkey_feature_flag": False,
        "auth_readable": True,
        "auth_flows": list(gate.get("auth_flows") or []),
        "auth_providers": list(gate.get("auth_providers") or []),
        # Named so a reader can tell "off" from "not this mechanism".
        "auth_model": "loopback session token + provider OAuth (no user password, no passkeys)",
    }


def auth_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/auth/status`` — Qubit's auth model, described truthfully.

    This is a READ of a real fact (whether the listener gates, and which flows it
    offers), not an implementation of upstream's user-auth system. The panel uses
    it to decide which auth controls to show; since
    ``password_auth_enabled``/``passkey_feature_flag`` are honestly false, it
    shows none of the controls Qubit cannot service.
    """
    facts = _auth_facts(cfg)
    if facts.get("auth_readable") is False:
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       "the auth gate could not be read; the panel will hide its "
                       "auth controls rather than assume they work",
                       endpoint="auth/status")
    return 200, {"ok": True, **facts}


def auth_unsupported(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/auth/{logout,passkeys,passkey/*}`` — typed 501, DEEP GAP.

    THE DELIBERATE REFUSAL. Each of these mutates a credential system Qubit does
    not have:

    * ``auth/logout`` — clears a browser session COOKIE. Qubit has no cookie; its
      session is a bearer token the client holds, discarded by the client.
    * ``auth/passkeys`` — lists rows in a WebAuthn credential table.
    * ``auth/passkey/register[/options]`` — creates a credential from an
      attestation object; a wrong implementation is an auth bypass.
    * ``auth/passkey/delete`` — removes one.

    Implementing any of them would mean inventing a credential store and a
    verified challenge/attestation path on this listener — a second auth system
    beside the loopback-token gate, unreviewed. The refusal names the gap and the
    path that actually works, and writes nothing.

    ``auth/logout`` is the ONE exception in substance: ``qubit_compat`` already
    implements ``POST /api/auth/logout`` (it revokes provider-session tokens and
    reports ``next: /login``). That is the real Qubit authority, so the gateway
    routes ``auth/logout`` there rather than refusing — see ``PORT_SET``.
    """
    sub = str(p.get("sub") or p.get("endpoint") or "").strip("/")
    return _refuse(
        "WB_AUTH_MODEL_UNSUPPORTED",
        f"/api/{sub or 'auth/*'} belongs to upstream's USER-PASSWORD + WebAuthn "
        f"passkey auth (password_hash, session cookie, credential table). Qubit "
        f"has no such authority: its dashboard auth is a loopback session token "
        f"and model access is provider OAuth. Implementing this would mean "
        f"inventing a second, unreviewed credential store — so nothing was "
        f"created, listed, registered or deleted.",
        endpoint=sub or "auth")


#: Path -> handler for the sub-paths ``workbench_ui_routes.HANDLERS`` binds.
HANDLERS: Dict[str, Any] = {
    "config_settings": settings,
    "config_reasoning": reasoning,
    "config_providers": providers,
    "config_default_model": default_model,
    "config_tools": tools,
    "extensions_status": extensions_status,
    "extensions_registry": extensions_registry,
    "extensions_toggle": extensions_toggle,
    "extensions_install": extensions_install,
    "extensions_sidecar_consent": extensions_sidecar_consent,
    "auth_status": auth_status,
    "auth_unsupported": auth_unsupported,
}