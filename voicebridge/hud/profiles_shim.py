"""Profile addressing for the HUD (Qubit-authored; no upstream code).

Two collectors touch profiles — ``collectors_profiles`` (the registry) and
``collectors_memory`` (an island's own store). Both need the SAME two
primitives, and neither may drift from the dashboard's own profile machinery:

* :func:`profiles_registry` — the vendored profile registry Qubit binds to its
  own home. It repeats exactly what ``voicebridge/qubit_compat.py``'s
  ``_profiles_module`` does (``ensure_path()`` then ``use_qubit_home(cfg)`` then
  ``import qubit_cli.profiles``). That duplication is deliberate: the HUD must
  not import the 20k-line compat module to read a profile list, and the three
  lines are the vendored module's documented entry point, not a second reader.
  If the binding ever changes, it changes in BOTH places — grep for
  ``_profiles_module`` when touching this function.

* :func:`scoped_config` — a cfg whose reads resolve a named profile's home.
  The profile home comes from the REGISTRY (``P.get_profile_dir(name)``), never
  assembled here. It is returned as a config carrying the same ``data`` with
  ``logging.jsonl``/``logging.jsonl_path`` cleared, because that is the input
  :func:`voicebridge.qubit_runtime.runtime_home_for_cfg` reads to derive a home
  (``<cfg.state_dir()/runtime>>``) — so passing a cfg whose state dir IS the
  profile home makes every downstream reader (``QubitSessionStore``,
  ``active_memory``) resolve that island's own store. Nothing in this module
  opens a database, reads another island's files, or writes anything.

Read-only by construction: ``get_profile_dir`` is a path computation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def profiles_registry(cfg: Any) -> Any:
    """The vendored profile registry, bound to the Qubit home.

    Mirrors ``voicebridge/qubit_compat.py::_profiles_module`` (see the module
    docstring for why this is a deliberate 3-line mirror, not a fork).
    """
    from .. import qubit_runtime

    qubit_runtime.ensure_path()
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - a resolution miss must not fail a read
        pass
    from qubit_cli import profiles as P  # type: ignore

    return P


def profile_home(cfg: Any, name: str) -> Path:
    """The named profile's home directory, resolved by the registry."""
    P = profiles_registry(cfg)
    return Path(P.get_profile_dir(name))


def scoped_config(cfg: Any, name: str) -> Any:
    """A cfg that resolves *name*'s home, or raises if the island is absent.

    Returns the ORIGINAL cfg for the default profile: the live config already
    addresses the default island, and rebuilding it would only risk a
    divergence.

    ``QUBIT_HOME`` is deliberately NOT mutated: it is process-global, so
    pinning it here would leak one profile's home into every concurrent request
    in the threaded server. Instead the returned cfg carries the profile's home
    as its ``path`` and nothing that pins the current island, and
    :func:`voicebridge.qubit_runtime.runtime_home_for_cfg` derives the home from
    it — the supported path for a non-default island.
    """
    home = profile_home(cfg, name)
    is_default = False
    try:
        P = profiles_registry(cfg)
        is_default = P.normalize_profile_name(name) == "default"
    except Exception:  # noqa: BLE001 - fall back to the home comparison
        is_default = False
    if is_default:
        return cfg
    if not home.is_dir():
        raise FileNotFoundError(f"profile {name!r} has no home directory")

    base = getattr(cfg, "data", None)
    data: Dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    logging_section = data.get("logging")
    if isinstance(logging_section, dict):
        section = dict(logging_section)
        # A custom jsonl path would pin this cfg to the CURRENT island; clear
        # it so runtime_home_for_cfg derives the home from the state dir.
        section.pop("jsonl_path", None)
        section.pop("jsonl", None)
        data["logging"] = section
    cls = type(cfg)
    try:
        scoped = cls(data=data, path=home)
    except TypeError:  # pragma: no cover - a cfg shape change is reported
        raise TypeError(
            "the live config class no longer accepts (data=, path=); the HUD "
            "profile scoping needs updating in voicebridge/hud/profiles_shim.py")


__all__ = ["profiles_registry", "profile_home", "scoped_config"]
