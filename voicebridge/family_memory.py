"""Qubit-native memory namespace for the family bridge.

The ``/api/qubit/memory/*`` rows are aliases over the canonical local
memory surface (``GET /api/memory`` and friends in
``voicebridge/qubit_compat.py``): ``qubit-local`` is the only provider and
cloud sync is refused by policy, so these methods delegate to the same
``active_memory`` store and the same persisted provider store — no second
source of truth.

Lineage note: the dashboard memory pages were built for the console; the
provider model here is Qubit-native (single local SQLite/FTS5 store) rather
than a multi-provider catalog.
"""

from __future__ import annotations

from typing import Any, Dict


def _gateway_error(code: str, detail: str) -> Any:
    from .gateway.gateway_ws import GatewayError

    return GatewayError(code, detail)


def _call(handler: Any, cfg: Any, body: Dict[str, Any],
          path: Dict[str, str]) -> Dict[str, Any]:
    code, out = handler(cfg, {}, dict(body), dict(path))
    if code != 200:
        raise _gateway_error(str(out.get("code", f"HTTP_{code}")),
                             str(out.get("detail", "memory backend failed")))
    if not isinstance(out, dict):
        raise _gateway_error("MEMORY_BAD_RESPONSE",
                             "memory backend returned no object")
    return out


def _gw_memory_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import qubit_compat as QC

    return _call(QC.h_memory, cfg, params, {})


def _gw_memory_provider(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import qubit_compat as QC

    return _call(QC.h_mem_provider, cfg, params, {})


def _gw_memory_provider_config(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    from . import qubit_compat as QC

    name = str(params.get("id", params.get("provider", "")) or "qubit-local")
    return _call(QC.mem_config, cfg, params, {"id": name})


def _gw_memory_provider_set(cfg: Any,
                            params: Dict[str, Any]) -> Dict[str, Any]:
    from . import qubit_compat as QC

    active = str(params.get("active", params.get("provider", "")) or "")
    if active and active != "qubit-local":
        raise _gateway_error("MEMORY_LOCAL_ONLY",
                             f"no memory provider {active!r}; qubit-local is "
                             "the only store and cloud sync is refused by "
                             "policy")
    return _call(QC.mem_provider_set, cfg, params, {})


def _gw_memory_probe(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import qubit_compat as QC

    return _call(QC.h_mem_provider, cfg, params, {})


#: Gateway method name -> handler (registered onto each bridge registry by the
#: family bridge in ``voicebridge/qubit_compat.py`` — the one wiring owner).
MEMORY_GATEWAY_METHODS: Dict[str, Any] = {
    "memory.status": _gw_memory_status,
    "memory.provider": _gw_memory_provider,
    "memory.provider_config": _gw_memory_provider_config,
    "memory.provider_set": _gw_memory_provider_set,
    "memory.probe": _gw_memory_probe,
}
