"""``/p/<profile>/`` prefix resolution for the shared api_server listener."""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The legacy @handle Bot Mode advertised a host's default profile under (apps/desktop
# hermes-bots ``botHandle``). New profiles cannot take the name, but a pre-reservation
# profiles/hermes can exist and must keep its traffic.
DEFAULT_PROFILE_HANDLE = "hermes"
# The product's canonical spelling of the same default-profile handle.
PRODUCT_PROFILE_HANDLE = "qubit"
PROFILE_HANDLES = frozenset({DEFAULT_PROFILE_HANDLE, PRODUCT_PROFILE_HANDLE})
# 404 body for a prefix naming no profile here. Older gateways also send it for a 'hermes' profile
# they do not serve, so peer clients treat it as ambiguous and never reroute @hermes on it.
UNKNOWN_PROFILE_ERROR = "Unknown or unconfigured profile"


def _handle_is_free() -> bool:
    """The handle aliases the default profile only on a host with no profile of that name: a
    pre-reservation ``profiles/hermes`` (served, parked or standalone) keeps it, so its traffic is
    never answered by the default profile. Fail closed: unknown counts as taken."""
    try:
        from qubit_cli.profiles import profile_exists
        return not any(profile_exists(name) for name in PROFILE_HANDLES)
    except Exception:
        logger.warning("Cannot check for a '%s' profile", DEFAULT_PROFILE_HANDLE, exc_info=True)
        return False


def _prefix_profile(request: Any) -> str:
    """The prefix as a canonical profile id. Profile ids are lowercase on disk and every other
    reader (``get_profile_dir``, ``profile_exists``, the peer client) normalizes case, so
    ``/p/Qubit/`` must name the same profile ``/p/hermes/`` does, never fall to the alias."""
    raw = (request.match_info.get("profile") or "").strip()
    if not raw:
        return ""
    from qubit_cli.profiles import normalize_profile_name
    return normalize_profile_name(raw)


def _is_handle(profile: str) -> bool:
    return profile in PROFILE_HANDLES


def rejection_response(request: Any):
    """The 404 for a rejected prefix. A 'hermes' profile this gateway does not serve gets its own
    body, so a peer reads it as "exists elsewhere", never as "no such profile, use default"."""
    from aiohttp import web
    if _is_handle(_prefix_profile(request)) and not _handle_is_free():
        return web.json_response({"error": {
            "message": f"Profile '{DEFAULT_PROFILE_HANDLE}' exists on this host but this gateway does not serve it",
            "code": "profile_not_served"}}, status=404)
    return web.json_response({"error": UNKNOWN_PROFILE_ERROR}, status=404)


def resolve_request_profile(adapter: Any, request: Any):
    """Resolve + validate the /p/<profile>/ prefix: ``None`` (no prefix, or multiplexing off and the
    prefix names this gateway's own profile), the served profile name, or ``_PROFILE_REJECTED``
    (-> 404). Fail closed: a foreign prefix must never be ignored. The default profile's handle
    (``qubit`` canonically, ``hermes`` legacy) reaches the default profile, because peers address
    the mirror by the handle their roster shows (#118076), unless the host has a profile literally
    named ``hermes`` — the legacy on-disk id of that same handle — which keeps the name."""
    from gateway.platforms.api_server import _PROFILE_REJECTED, _prefix_names_served_profile
    profile = _prefix_profile(request)
    if not profile:
        return None
    cfg = getattr(adapter.gateway_runner, "config", None)
    if not getattr(cfg, "multiplex_profiles", False):
        if _prefix_names_served_profile(profile):
            return None
        if _is_handle(profile):
            if (_prefix_names_served_profile(PRODUCT_PROFILE_HANDLE)
                    or _prefix_names_served_profile(DEFAULT_PROFILE_HANDLE)):
                return None
            if _prefix_names_served_profile("default") and _handle_is_free():
                return None
        return _PROFILE_REJECTED
    try:
        from qubit_cli.profiles import profiles_to_serve
        served = {name for name, _ in profiles_to_serve(multiplex=True)}
    except Exception:
        # Fail closed: a prefix this gateway cannot prove it serves is a 404.
        logger.warning("Cannot list served profiles for /p/%s/", profile, exc_info=True)
        return _PROFILE_REJECTED
    if profile in served:
        return profile
    if _is_handle(profile):
        if DEFAULT_PROFILE_HANDLE in served:
            return DEFAULT_PROFILE_HANDLE
        if "default" in served and _handle_is_free():
            return "default"
    return _PROFILE_REJECTED
