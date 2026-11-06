"""Qubit HUD route table (Qubit addition).

Seven read-only collectors behind seven dashboard routes, one per HUD panel:

    GET /api/hud/health      -> voicebridge.hud.health
    GET /api/hud/sessions    -> voicebridge.hud.sessions
    GET /api/hud/cron        -> voicebridge.hud.cron
    GET /api/hud/models      -> voicebridge.hud.models
    GET /api/hud/skills      -> voicebridge.hud.skills
    GET /api/hud/plugins     -> voicebridge.hud.plugins
    GET /api/hud/costs       -> voicebridge.hud.costs

WHY THIS FILE IS SEPARATE FROM THE COMPAT ROUTE TABLE
---------------------------------------------------------------------------
``voicebridge/qubit_compat.py`` is one large vendored-compat module the
dashboard dispatches through. The HUD is a self-owned surface, so its rows are
declared here, in one place that can be read end to end, and registered into
the compat table by ONE line:

    from .hud_routes import hud_routes as _hud_routes
    for _m, _p, _h in _hud_routes():
        _r(_m, _p, _h)

THE IMPORT RULE (load-bearing)
---------------------------------------------------------------------------
The collectors live in ``voicebridge/hud/``, owned by a different lane and NOT
guaranteed to be present in every tree that boots this dashboard. Nothing in
this module imports them at import time: each collector is imported INSIDE its
handler, in a ``try``/``except``. A tree without ``voicebridge/hud`` therefore
imports this file cleanly and boots the dashboard normally — the panel answers
a typed 503 naming the missing collector instead of the whole listener failing
with an ImportError at boot. A missing capability must be a refusal, never a
crash.

READ-ONLY (C4). Every handler here is a GET that asks a collector for a
snapshot and returns it. There is no mutation path in this file, so the shared
staged-op confirmation journal is not bypassed: it is not reached at all.

The handler signature is the compat table's: ``handler(cfg, q, b, p)``
returning ``(status_code, payload)``, with ``cfg`` the resolved config, ``q``
the query strings, ``b`` the JSON body and ``p`` the regex path params.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Tuple

# One row per panel: (route suffix, collector function name). The collector is
# resolved lazily so this table can be read (and tested for shape) with the
# collector package absent.
_PANELS: Tuple[Tuple[str, str, str], ...] = (
    ("health", "collectors_health", "collect_health"),
    ("sessions", "collectors_sessions", "collect_sessions"),
    ("cron", "collectors_cron", "collect_cron"),
    ("models", "collectors_model_info", "collect_model_info"),
    ("skills", "collectors_skills", "collect_skills"),
    ("plugins", "collectors_plugins", "collect_plugins"),
    ("costs", "collectors_model_analytics", "collect_model_analytics"),
)


def _collector(panel: str) -> Callable[..., Any]:
    """Resolve one collector from ``voicebridge.hud``.

    ``panel`` is the route suffix (``health``); the module AND function that own
    it are looked up in ``_PANELS`` because the panel name is not always the
    function name (``models`` -> ``collect_model_info``). Raises — never returns
    None — so the caller has exactly one failure path. The caller turns that
    into the 503; this function does not invent a payload, because an empty
    snapshot and an absent collector must not look the same to the UI.
    """
    hud_pkg = importlib.import_module(".hud", __package__)

    row = {p: (m, f) for p, m, f in _PANELS}.get(panel)
    if row is None:
        raise AttributeError(f"no HUD collector is mapped to {panel!r}")
    module_name, fn_name = row
    module = importlib.import_module("." + module_name, hud_pkg.__name__)
    fn = getattr(module, fn_name, None)
    if not callable(fn):
        raise AttributeError(
            f"voicebridge.hud.{module_name} exposes no {fn_name}")
    return fn


def _serve(panel: str, cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Any]:
    """Shared body of the seven handlers: resolve, call, type the failure.

    The collector owns its payload shape. This wrapper only guarantees that
    (a) a missing/!importable collector is a 503 that says which one is
    missing, and (b) a collector that raises is reported as a 502 naming the
    panel and the error class — never as an empty body dressed up as data.
    """
    try:
        fn = _collector(panel)
    except Exception as exc:  # ModuleNotFoundError, AttributeError, anything
        return 503, {
            "ok": False,
            "panel": panel,
            "code": "HUD_COLLECTOR_UNAVAILABLE",
            "detail": (f"the HUD {panel} collector is not available: "
                       f"{type(exc).__name__}: {exc}. The "
                       f"voicebridge.hud package is not installed in this "
                       f"tree, so /api/hud/{panel} has nothing to serve."),
        }
    try:
        out = fn(cfg=cfg)
    except Exception as exc:
        return 502, {
            "ok": False,
            "panel": panel,
            "code": "HUD_COLLECTOR_FAILED",
            "detail": (f"the HUD {panel} collector raised: "
                       f"{type(exc).__name__}: {exc}"),
        }
    # A collector may return either the payload itself or a full
    # ``(status, payload)`` pair; both are accepted so a collector that wants
    # to answer 404 itself can, without this wrapper unwrapping it wrongly.
    if (isinstance(out, tuple) and len(out) == 2
            and isinstance(out[0], int)):
        return out[0], out[1]
    return 200, out


def h_hud_health(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/health -> the Health panel's snapshot."""
    return _serve("health", cfg, q, b, p)


def h_hud_sessions(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/sessions -> the Sessions panel's snapshot."""
    return _serve("sessions", cfg, q, b, p)


def h_hud_cron(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/cron -> the Cron panel's snapshot."""
    return _serve("cron", cfg, q, b, p)


def h_hud_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/models -> the Models panel's snapshot."""
    return _serve("models", cfg, q, b, p)


def h_hud_skills(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/skills -> the Skills panel's snapshot."""
    return _serve("skills", cfg, q, b, p)


def h_hud_plugins(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/plugins -> the Plugins panel's snapshot."""
    return _serve("plugins", cfg, q, b, p)


def h_hud_costs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/hud/costs -> the Costs panel's snapshot."""
    return _serve("costs", cfg, q, b, p)


_HANDLERS: Dict[str, Callable[..., Tuple[int, Any]]] = {
    f"h_hud_{panel}": fn for panel, fn in (
        ("health", h_hud_health),
        ("sessions", h_hud_sessions),
        ("cron", h_hud_cron),
        ("models", h_hud_models),
        ("skills", h_hud_skills),
        ("plugins", h_hud_plugins),
        ("costs", h_hud_costs),
    )
}


def hud_routes() -> List[Tuple[str, str, str]]:
    """The HUD's rows for the dashboard compat route table.

    Returns ``(method, pattern, handler_name)`` — the same triple ``_r()``
    takes in ``voicebridge/qubit_compat.py``, so registration is:

        from .hud_routes import hud_routes as _hud_routes
        for _m, _p, _h in _hud_routes():
            _r(_m, _p, _h)

    The handler NAMES are returned rather than the functions because the compat
    dispatcher resolves handlers through its own ``HANDLERS`` mapping; the
    registration also needs ``HANDLERS.update(HUD_HANDLERS)`` so those names
    resolve. Importing this module is dependency-free, which is what keeps a
    tree without ``voicebridge/hud`` bootable.
    """
    return [("GET", f"/api/hud/{suffix}", f"h_hud_{suffix}")
            for suffix, _module, _fn in _PANELS]


def hud_handlers() -> Dict[str, Callable[..., Tuple[int, Any]]]:
    """The ``HANDLERS`` entries the names in :func:`hud_routes` resolve to."""
    return dict(_HANDLERS)
