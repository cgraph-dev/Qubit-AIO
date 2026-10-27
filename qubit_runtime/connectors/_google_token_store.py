"""Load the shared Google token writer that lives with the skill scripts.

``google_token.json`` is owned by the bundled ``google-workspace`` skill, and
its scripts must run standalone (outside the ``qubit_runtime`` package), so the
atomic writer lives beside them. The connectors load that exact file instead of
keeping a second implementation; there is one writer and one lock for every
refresh path.

Loading is lazy and failure-tolerant on purpose: a deployment that ships the
connectors without the bundled skill must not fail at import time. In that
case a refresh still works for the current turn; persisting it degrades to a
warning (return ``False``) and the lock becomes a no-op.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_TOKEN_STORE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "productivity"
    / "google-workspace"
    / "scripts"
    / "_token_store.py"
)

_module: Any = None


def _store() -> Any:
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location(
            "qubit_google_workspace_token_store", _TOKEN_STORE_PATH)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ImportError(f"cannot load the Google token writer at {_TOKEN_STORE_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def atomic_write_json(path: str | Path, payload: Any) -> None:
    _store().atomic_write_json(path, payload)


def persist_authorized_user_credentials(path: str | Path, credentials: Any) -> bool:
    try:
        return bool(_store().persist_authorized_user_credentials(path, credentials))
    except Exception:
        return False


@contextmanager
def token_file_lock(path: str | Path) -> Iterator[None]:
    try:
        module = _store()
    except Exception:
        yield
        return
    with module.token_file_lock(path):
        yield


__all__ = [
    "atomic_write_json",
    "persist_authorized_user_credentials",
    "token_file_lock",
]
